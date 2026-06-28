"""Execution engine (`src/runtime.py`) — A6 + A7; [修正-1][修正-4][修正-8].

Still a "pure relay for-loop with no business judgement", hardened with:
  * structured StepResult + classified retry ([修正-4], §5.4)
  * Profile-level mutex lock ([修正-8], §5.8) — same profile runs serially
  * event push (seq-numbered) via injected `emit` (契约 2, §3.2)
  * persistence ([修正/§7]): A writes NN_{key}_prompt.md / _response.md /
    _error.json and context.json. events.jsonl is written by C inside its emit.

Runs in a background Worker thread (no asyncio loop), so synchronous Playwright
inside providers is safe ([修正-1], §5.1). Pure function — HTTP-decoupled.

Public contract (frozen, §3.2):
    run_pipeline(pipeline_path, user_question, session_dir, emit) -> dict
Optional keyword args (builder/manager/validate) have defaults and do NOT
change the call site C already uses; they exist for testing and startup wiring.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from src.builder import PromptBuilder
from src.logging_conf import (
    M_SESSION_EXPIRED_TOTAL,
    M_STEP_DURATION_SECONDS,
    M_STEP_RETRIES_TOTAL,
    get_logger,
    log_event,
    metrics,
)
from src.manager import ProviderManager
from src.providers._errors import SessionExpiredError
from src.validation import validate_pipeline

_log = get_logger("rhcloud.runtime")

# ----- Profile lock ([修正-8]) -------------------------------------------------
_profile_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(profile: str) -> threading.Lock:
    """Return the process-wide lock for a profile (created on first use).

    API providers use profile == "" and therefore share a single lock keyed by
    the empty string; that is harmless (API calls are independently safe) and
    keeps the contract uniform.
    """
    with _locks_guard:
        if profile not in _profile_locks:
            _profile_locks[profile] = threading.Lock()
        return _profile_locks[profile]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ms_between(start_iso: str, end_iso: str) -> int:
    s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    return int((e - s).total_seconds() * 1000)


# ----- Step execution with classified retry ([修正-4]) -------------------------
def _fail(etype: str, msg: str, attempt: int, started: str) -> dict:
    return {
        "status": "failed",
        "content": None,
        "attempt": attempt,
        "started_at": started,
        "finished_at": _now_iso(),
        "error": {"type": etype, "message": msg},
    }


def _run_step_with_retry(
    manager: ProviderManager,
    provider_name: str,
    prompt: str,
    conf: dict,
    key: str,
    *,
    job_id: Optional[str] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Run one step. Classify failures:

      SessionExpiredError -> fatal: no retry, error.type 'session_expired'.
      any other Exception -> transient: retry up to conf['retries'] with
                             exponential backoff, then error.type 'transient'.
    """
    attempt = 0
    started = _now_iso()
    site = conf.get("site")
    while True:
        if is_cancelled and is_cancelled():
            return _fail("cancelled", "job was cancelled by user", attempt, started)
        attempt += 1
        try:
            lock = _lock_for(conf["profile"])  # [修正-8] serialize per profile
            with lock:
                content = manager.run(provider_name, prompt, on_chunk=on_chunk)
            if content is None or content == "":
                # Contract: providers must return non-empty text. Treat empty as
                # transient so it benefits from retry rather than silently passing.
                raise RuntimeError("provider returned empty content")
            return {
                "status": "succeeded",
                "content": content,
                "attempt": attempt,
                "started_at": started,
                "finished_at": _now_iso(),
                "error": None,
            }
        except SessionExpiredError as e:
            metrics.inc(M_SESSION_EXPIRED_TOTAL, provider=provider_name)
            log_event(
                _log,
                "step_session_expired",
                level=logging.WARNING,
                job_id=job_id,
                step_key=key,
                provider=provider_name,
                site=site,
                attempt=attempt,
                error_type="session_expired",
            )
            return _fail("session_expired", str(e), attempt, started)
        except Exception as e:  # transient class (incl. GenerationTimeout)
            if attempt > conf["retries"]:
                log_event(
                    _log,
                    "step_failed",
                    level=logging.ERROR,
                    job_id=job_id,
                    step_key=key,
                    provider=provider_name,
                    site=site,
                    attempt=attempt,
                    error_type="transient",
                )
                return _fail("transient", str(e), attempt, started)
            metrics.inc(M_STEP_RETRIES_TOTAL, provider=provider_name)
            log_event(
                _log,
                "step_retry",
                level=logging.WARNING,
                job_id=job_id,
                step_key=key,
                provider=provider_name,
                site=site,
                attempt=attempt,
                error_type="transient",
            )
            backoff_s = conf["retry_backoff_ms"] / 1000 * (2 ** (attempt - 1))
            sleep(backoff_s)


# ----- Pipeline orchestration --------------------------------------------------
def run_pipeline(
    pipeline_path: str,
    user_question: str,
    session_dir: str,
    emit: Callable[[dict], None],
    *,
    builder: Optional[PromptBuilder] = None,
    manager: Optional[ProviderManager] = None,
    validate: bool = True,
    job_id: Optional[str] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict:
    """Execute a pipeline sequentially.

    For each produced event, assigns a monotonically increasing `seq` and calls
    emit(event). Returns the final context. The caller (C) injects emit, which
    persists each event to events.jsonl and forwards it to SSE.
    """
    builder = builder or PromptBuilder()
    manager = manager or ProviderManager()

    # A5: defense-in-depth validation using the SAME providers config / prompts
    # dir that this manager/builder are bound to. C is expected to validate at
    # submit (returning 400); this guards direct/startup invocation too.
    if validate:
        validate_pipeline(pipeline_path, manager.config, builder.prompts_dir)

    import yaml

    with open(pipeline_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    context = {"user_question": user_question, "outputs": {}}
    os.makedirs(session_dir, exist_ok=True)

    seq = 0

    def push(ev: dict) -> None:
        nonlocal seq
        seq += 1
        ev["seq"] = seq
        emit(ev)

    log_event(_log, "pipeline_started", job_id=job_id)

    for index, step in enumerate(config["steps"]):
        if is_cancelled and is_cancelled():
            push({"type": "fatal", "error": {"type": "cancelled", "message": "job was cancelled by user"}})
            break
        key = step["key"]
        prompt_name = step["prompt"]
        prompt_text = builder.build(prompt_name, context)
        prefix = f"{index + 1:02d}_{key}"
        _write(os.path.join(session_dir, f"{prefix}_prompt.md"), prompt_text)

        provider_list = step.get("providers")
        if not provider_list:
            provider_list = [step["provider"]]

        if len(provider_list) == 1:
            provider_name = provider_list[0]
            conf = manager.resolve(provider_name)
            log_event(
                _log,
                "step_started",
                job_id=job_id,
                step_key=key,
                provider=provider_name,
                site=conf.get("site"),
            )
            push({"type": "step_started", "key": key, "provider": provider_name})

            def handle_chunk(delta: str) -> None:
                push({"type": "step_chunk", "key": key, "provider": provider_name, "delta": delta})

            result = _run_step_with_retry(
                manager,
                provider_name,
                prompt_text,
                conf,
                key,
                job_id=job_id,
                on_chunk=handle_chunk,
                is_cancelled=is_cancelled,
            )
        else:
            # Multi-provider race-to-first mode
            race_state = {"winner": None, "lock": threading.Lock()}

            def run_candidate(candidate_name: str) -> dict:
                c_conf = manager.resolve(candidate_name)

                def candidate_chunk(delta: str) -> None:
                    with race_state["lock"]:
                        if race_state["winner"] is None:
                            race_state["winner"] = candidate_name
                            log_event(_log, "step_started", job_id=job_id, step_key=key, provider=candidate_name, site=c_conf.get("site"))
                            push({"type": "step_started", "key": key, "provider": candidate_name})
                        if race_state["winner"] == candidate_name:
                            push({"type": "step_chunk", "key": key, "provider": candidate_name, "delta": delta})

                res = _run_step_with_retry(
                    manager, candidate_name, prompt_text, c_conf, key, job_id=job_id, on_chunk=candidate_chunk, is_cancelled=is_cancelled
                )
                if res["status"] == "succeeded":
                    with race_state["lock"]:
                        if race_state["winner"] is None:
                            race_state["winner"] = candidate_name
                            log_event(_log, "step_started", job_id=job_id, step_key=key, provider=candidate_name, site=c_conf.get("site"))
                            push({"type": "step_started", "key": key, "provider": candidate_name})
                return {"provider": candidate_name, "result": res, "conf": c_conf}

            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=len(provider_list)) as race_executor:
                futures = [race_executor.submit(run_candidate, p) for p in provider_list]
                successful_res = None
                first_failed = None
                for future in as_completed(futures):
                    try:
                        cand = future.result()
                        if cand["result"]["status"] == "succeeded":
                            with race_state["lock"]:
                                if successful_res is None or cand["provider"] == race_state["winner"]:
                                    successful_res = cand
                        elif first_failed is None:
                            first_failed = cand
                    except Exception:
                        pass

            if successful_res:
                provider_name = successful_res["provider"]
                result = successful_res["result"]
                conf = successful_res["conf"]
            elif first_failed:
                provider_name = first_failed["provider"]
                result = first_failed["result"]
                conf = first_failed["conf"]
            else:
                provider_name = provider_list[0]
                conf = manager.resolve(provider_name)
                result = _fail("transient", "all race candidates failed", 1, _now_iso())

        duration_ms = _ms_between(result["started_at"], result["finished_at"])
        metrics.observe(
            M_STEP_DURATION_SECONDS,
            duration_ms / 1000,
            provider=provider_name,
            site=conf.get("site"),
        )

        if result["status"] == "succeeded":
            context["outputs"][key] = result["content"]
            _write(
                os.path.join(session_dir, f"{prefix}_response.md"), result["content"]
            )
            log_event(
                _log,
                "step_succeeded",
                job_id=job_id,
                step_key=key,
                provider=provider_name,
                site=conf.get("site"),
                attempt=result["attempt"],
                duration_ms=duration_ms,
            )
            push(
                {
                    "type": "step_succeeded",
                    "key": key,
                    "provider": provider_name,
                    "content": result["content"],
                }
            )
        else:
            _write_json(
                os.path.join(session_dir, f"{prefix}_error.json"), result["error"]
            )
            push(
                {
                    "type": "step_failed",
                    "key": key,
                    "provider": provider_name,
                    "error": result["error"],
                }
            )
            # Out-of-error -> interrupt the whole pipeline ("出错即中断").
            break

    _write_json(os.path.join(session_dir, "context.json"), context)
    push({"type": "pipeline_finished"})
    log_event(_log, "pipeline_finished", job_id=job_id)
    return context


# ----- Persistence helpers (A writes these; events.jsonl is C's) ---------------
def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


__all__ = ["run_pipeline", "_run_step_with_retry", "_lock_for"]
