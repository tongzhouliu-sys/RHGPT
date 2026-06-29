"""API gateway (`src/main.py`) — C1/C2/C3/C4/C5/C9; [修正-1][修正-2][修正-7].

Job-ized API + background Worker + reconnectable SSE + HMAC auth + CORS + rate
limiting + export + health. Built against A's frozen contract 2
(`run_pipeline(pipeline_path, user_question, session_dir, emit) -> dict`) and the
REST/SSE contract 3 in docs/contracts.md.

Hardening over the §6.6 reference skeleton (mirroring how A hardened its kernel):

  * Auth is API Key + HMAC (17.1 decision / contract 3), not a static bearer.
  * SSE replay is driven by the append-only persisted event list with a
    per-client cursor and strict `seq` de-dup, so reconnection with
    `Last-Event-ID` never drops OR duplicates an event, and multiple concurrent
    readers each get the full stream (the reference's single consume-once Queue
    cannot guarantee either). [修正-2]
  * Worker-level `fatal` events are seq-assigned by the emit closure (runtime
    only numbers its own events) and carry the schema's `{type,message}` error
    object, not a bare string.
  * Explicit max-concurrency (429) + per-key rate limit (429), not just a
    silently-queued thread pool. [修正-7]
  * `create_app(...)` is a factory so API tests inject a stub runtime, fixture
    providers/prompts dirs, and a fixed keystore without touching globals.
"""

from __future__ import annotations

import errno
import inspect
import json
import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from src import export as export_mod
from src.alerts import AlertTracker, threshold_from_env
from src.auth import AuthError, load_keystore_from_env, verify_request
from src.builder import PromptBuilder
from src.logging_conf import (
    M_ACTIVE_JOBS,
    M_JOBS_FAILED_TOTAL,
    M_JOBS_TOTAL,
    configure_logging,
    get_logger,
    log_event,
    metrics,
)
from src.manager import ProviderManager
from src.runtime import run_pipeline as _real_run_pipeline
from src.validation import ValidationError, validate_pipeline_file

_log = get_logger("rhcloud.api")

_TERMINAL = {"succeeded", "failed"}
SSE_POLL_INTERVAL = 0.25  # seconds; in-memory list poll cadence for the live tail
SSE_KEEPALIVE_SECONDS = 15


# ----- fixed-window rate limiter (per api key) --------------------------------
class RateLimiter:
    """Per-identity fixed-window limiter: at most `limit` hits per `window` s."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.time):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, int]] = {}  # key -> (window_start, count)

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            start, count = self._state.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            count += 1
            self._state[key] = (start, count)
            return count <= self.limit


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _free_mb(path: str) -> Optional[float]:
    """Free megabytes on the filesystem holding `path` (nearest existing parent).

    Returns None if it cannot be determined (never blocks a request on that).
    """
    p = path
    for _ in range(40):
        if os.path.isdir(p):
            break
        parent = os.path.dirname(p)
        if not parent or parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p or ".").free / (1024 * 1024)
    except OSError:
        return None


def _is_enospc(exc: BaseException) -> bool:
    """True if the exception chain contains a 'No space left on device' error."""
    seen = exc
    while seen is not None:
        if isinstance(seen, OSError) and seen.errno == errno.ENOSPC:
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _accepts_runtime_kwargs(fn: Callable) -> bool:
    """True if `fn` takes A's optional builder/manager/job_id kwargs (or **kw).

    Lets us call the real runtime with injected builder/manager while still
    supporting minimal 4-arg stub runtimes — decided ONCE up front so a
    TypeError raised deep inside the runtime can never trigger a re-run.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True  # unknown signature -> assume the real (kwarg-accepting) runtime
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return True
    return "builder" in sig.parameters


def create_app(
    *,
    keystore: Optional[dict[str, str]] = None,
    run_pipeline_fn: Callable[..., dict] = _real_run_pipeline,
    providers_path: Optional[str] = None,
    prompts_dir: Optional[str] = None,
    sessions_root: Optional[str] = None,
    frontend_origin: Optional[str] = None,
    max_concurrent: Optional[int] = None,
    rate_limit_per_min: Optional[int] = None,
    job_timeout: Optional[int] = None,
    max_skew: int = 300,
    alert_threshold: Optional[int] = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))

    if keystore is None:
        try:
            keystore = load_keystore_from_env()
        except ValueError as e:
            # Fail closed: no creds -> empty keystore -> every signed route 401s
            # until the environment is configured. Keeps `uvicorn src.main:app`
            # importable in unconfigured environments instead of crashing.
            log_event(_log, "auth_keystore_unconfigured", level=logging.WARNING)
            _log.warning("auth disabled until configured: %s", e)
            keystore = {}

    providers_path = providers_path or os.environ.get(
        "PROVIDERS_PATH", "config/providers.yaml"
    )
    prompts_dir = prompts_dir or os.environ.get("PROMPTS_DIR", "prompts")
    sessions_root = sessions_root or os.environ.get("SESSIONS_ROOT", "data/sessions")
    frontend_origin = frontend_origin or os.environ.get(
        "FRONTEND_ORIGIN", "https://your-frontend.pages.dev"
    )
    max_concurrent = max_concurrent or _env_int("MAX_CONCURRENT_JOBS", 5)
    rate_limit_per_min = rate_limit_per_min or _env_int("RATE_LIMIT_PER_MIN", 30)
    job_timeout = job_timeout or _env_int("JOB_TIMEOUT_SECONDS", 900)
    alert_threshold = alert_threshold or threshold_from_env()
    # Refuse new jobs (507) before we run out of disk, so the failure is an
    # explicit, CORS-friendly error instead of a bare 500 from os.makedirs.
    min_free_disk_mb = _env_int("MIN_FREE_DISK_MB", 50)

    free = _free_mb(sessions_root)
    if free is not None and free < min_free_disk_mb:
        _log.warning(
            "disk_low_at_startup: %.1f MB free (< %d MB min)", free, min_free_disk_mb
        )

    app = FastAPI(title="RHCLOUD V1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],  # [修正-7] never "*"
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Api-Key",
            "X-Timestamp",
            "X-Signature",
            "Last-Event-ID",
        ],
    )

    def _cors_headers(request: Request) -> dict[str, str]:
        """ACAO headers to mirror CORSMiddleware on responses it can't reach."""
        origin = request.headers.get("origin")
        if origin and origin == frontend_origin:
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return {}

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        """Convert unhandled errors into CORS-tagged JSON.

        An uncaught exception is otherwise emitted by Starlette's outermost
        ServerErrorMiddleware — *above* CORSMiddleware — so the 500 carries no
        Access-Control-Allow-Origin and the browser reports the misleading
        "Failed to fetch" instead of the real error. Adding the header here keeps
        the real status/message visible to the client (and maps a full disk to a
        precise 507). [Failed-to-fetch root-cause fix]
        """
        if _is_enospc(exc):
            status, detail = 507, "insufficient storage: server disk is full, retry later"
            _log.error("request_failed_enospc on %s", request.url.path)
        else:
            status, detail = 500, "internal server error"
            _log.exception(
                "request_unhandled_exception on %s (%s)",
                request.url.path,
                type(exc).__name__,
            )
        return JSONResponse(status_code=status, content={"detail": detail}, headers=_cors_headers(request))

    executor = ThreadPoolExecutor(max_workers=max(1, max_concurrent))
    rate_limiter = RateLimiter(rate_limit_per_min, 60.0, clock)
    alerter = AlertTracker(alert_threshold)
    pass_runtime_kwargs = _accepts_runtime_kwargs(run_pipeline_fn)

    jobs: dict[str, dict] = {}
    jobs_guard = threading.Lock()
    active = {"n": 0}

    # ---- auth adapter --------------------------------------------------------
    async def authenticate(
        request: Request,
        x_api_key: Optional[str],
        x_timestamp: Optional[str],
        x_signature: Optional[str],
    ) -> str:
        body = await request.body()
        try:
            return verify_request(
                keystore,
                request.method,
                request.url.path,
                api_key=x_api_key,
                timestamp=x_timestamp,
                signature=x_signature,
                body=body,
                now=clock(),
                max_skew=max_skew,
            )
        except AuthError as e:
            raise HTTPException(status_code=e.status, detail=e.message)

    # ---- emit factory --------------------------------------------------------
    def make_emit(job: dict) -> Callable[[dict], None]:
        events_path = os.path.join(job["session_dir"], "events.jsonl")
        seq_state = {"max": 0}
        lock = threading.Lock()

        def emit(ev: dict) -> None:
            with lock:
                seq = ev.get("seq")
                if seq is None:
                    # Worker-level event (e.g. fatal) that did not pass through
                    # runtime's seq assignment: continue the numbering.
                    seq = seq_state["max"] + 1
                    ev["seq"] = seq
                seq_state["max"] = max(seq_state["max"], seq)
                # A's runtime owns *_prompt/_response/_error/context; events.jsonl
                # is C's (contract 2). Single Worker appends -> no write race.
                with open(events_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                job["events"].append(ev)
            alerter.observe(ev)

        return emit

    # ---- worker --------------------------------------------------------------
    def run_worker(job_id: str, pipeline: str, user_question: str) -> None:
        job = jobs[job_id]
        emit = make_emit(job)
        job["emit"] = emit  # store for reuse by cancel endpoint
        os.makedirs(job["session_dir"], exist_ok=True)

        def is_cancelled() -> bool:
            return job.get("cancelled", False)

        # ---- Watchdog: force-release slot after job_timeout seconds -----------
        def _watchdog() -> None:
            with jobs_guard:
                if job.get("_released"):
                    return
                job["_released"] = True
                active["n"] -= 1
                metrics.set_gauge(M_ACTIVE_JOBS, active["n"])
            job["cancelled"] = True
            if job["status"] == "running":
                job["status"] = "failed"
            emit(
                {
                    "type": "fatal",
                    "error": {
                        "type": "timeout",
                        "message": f"job exceeded {job_timeout}s global timeout (watchdog)",
                    },
                }
            )
            log_event(
                _log,
                "job_watchdog_timeout",
                level=logging.ERROR,
                job_id=job_id,
            )

        watchdog_timer = threading.Timer(job_timeout, _watchdog)
        watchdog_timer.daemon = True
        watchdog_timer.start()

        try:
            if pass_runtime_kwargs:
                run_pipeline_fn(
                    pipeline,
                    user_question,
                    job["session_dir"],
                    emit,
                    builder=PromptBuilder(prompts_dir),
                    manager=ProviderManager(providers_path),
                    job_id=job_id,
                    is_cancelled=is_cancelled,
                )
            else:
                run_pipeline_fn(pipeline, user_question, job["session_dir"], emit)
            if is_cancelled():
                job["status"] = "failed"
            else:
                job["status"] = "succeeded"
        except Exception as e:  # noqa: BLE001  ([修正-1] worker thread, sync-safe)
            # Worker-level uncaught failure -> fatal event (seq assigned by emit)
            # with the schema's {type,message} error object.
            emit({"type": "fatal", "error": {"type": "internal", "message": str(e)}})
            job["status"] = "failed"
            metrics.inc(M_JOBS_FAILED_TOTAL)
        finally:
            watchdog_timer.cancel()
            with jobs_guard:
                if not job.get("_released"):
                    job["_released"] = True
                    active["n"] -= 1
                    metrics.set_gauge(M_ACTIVE_JOBS, active["n"])

    # ---- routes --------------------------------------------------------------
    @app.post("/jobs")
    async def create_job(
        request: Request,
        x_api_key: str = Header(None),
        x_timestamp: str = Header(None),
        x_signature: str = Header(None),
    ):
        api_key = await authenticate(request, x_api_key, x_timestamp, x_signature)

        if not rate_limiter.allow(api_key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

        try:
            body = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="body must be valid JSON")
        user_question = body.get("user_question")
        if not isinstance(user_question, str) or not user_question.strip():
            raise HTTPException(status_code=400, detail="missing 'user_question'")
        
        selected = body.get("selected_providers")
        job_id = str(uuid.uuid4())
        session_dir = os.path.join(sessions_root, job_id)

        # Disk guard: a full Volume makes os.makedirs raise OSError(ENOSPC),
        # which without this would surface as a CORS-less 500 → "Failed to fetch"
        # in the browser. Fail fast and explicitly with 507 instead.
        free = _free_mb(sessions_root)
        if free is not None and free < min_free_disk_mb:
            _log.error(
                "job_rejected_low_disk: %.1f MB free (< %d MB min)", free, min_free_disk_mb
            )
            raise HTTPException(
                status_code=507,
                detail="insufficient storage: server disk is full, retry later",
            )
        try:
            os.makedirs(session_dir, exist_ok=True)
        except OSError as e:
            if _is_enospc(e):
                log_event(_log, "session_makedirs_enospc", level=logging.ERROR)
                raise HTTPException(
                    status_code=507,
                    detail="insufficient storage: server disk is full, retry later",
                )
            raise

        if isinstance(selected, list) and len(selected) > 0:
            import yaml
            dyn_path = os.path.join(session_dir, "pipeline.yaml")
            steps_list = []
            for key in ["generate", "review", "deep_analyze", "improve", "summary"]:
                step_obj = {"key": key, "prompt": key}
                if len(selected) > 1:
                    step_obj["providers"] = selected
                else:
                    step_obj["provider"] = selected[0]
                steps_list.append(step_obj)
            dyn_cfg = {
                "name": "Custom Selected Relay",
                "steps": steps_list,
            }
            with open(dyn_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(dyn_cfg, f)
            pipeline = dyn_path
        else:
            pipeline = body.get("pipeline", "pipelines/round1.yaml")

        # A5 defense: validate the pipeline at submit; 400 on any failure.
        try:
            validate_pipeline_file(pipeline, providers_path, prompts_dir)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail={"errors": e.errors})

        with jobs_guard:
            if active["n"] >= max_concurrent:
                raise HTTPException(status_code=429, detail="max concurrent jobs reached")
            active["n"] += 1
            metrics.set_gauge(M_ACTIVE_JOBS, active["n"])
            jobs[job_id] = {
                "status": "running",
                "cancelled": False,
                "events": [],
                "session_dir": session_dir,
            }

        metrics.inc(M_JOBS_TOTAL)
        log_event(_log, "job_created", job_id=job_id)
        executor.submit(run_worker, job_id, pipeline, user_question)
        return {"job_id": job_id}

    @app.get("/jobs/{job_id}")
    async def get_job(
        job_id: str,
        request: Request,
        x_api_key: str = Header(None),
        x_timestamp: str = Header(None),
        x_signature: str = Header(None),
    ):
        await authenticate(request, x_api_key, x_timestamp, x_signature)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job_id": job_id, "status": job["status"], "events": list(job["events"])}

    @app.post("/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        request: Request,
        x_api_key: str = Header(None),
        x_timestamp: str = Header(None),
        x_signature: str = Header(None),
    ):
        await authenticate(request, x_api_key, x_timestamp, x_signature)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job["status"] == "running":
            job["cancelled"] = True
            job["status"] = "failed"
            emit_fn = job.get("emit") or make_emit(job)
            emit_fn({"type": "fatal", "error": {"type": "cancelled", "message": "job cancelled by user"}})
        return {"job_id": job_id, "status": job["status"]}

    @app.get("/jobs/{job_id}/events")
    async def stream_events(
        job_id: str,
        request: Request,
        last_event_id: str = Header(None),
        x_api_key: str = Header(None),
        x_timestamp: str = Header(None),
        x_signature: str = Header(None),
    ):
        await authenticate(request, x_api_key, x_timestamp, x_signature)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")

        try:
            start = int(last_event_id) if last_event_id else 0
        except ValueError:
            start = 0

        async def gen():
            import asyncio

            last_sent = start
            last_emit = time.monotonic()
            while True:
                if await request.is_disconnected():
                    return
                events = job["events"]
                n = len(events)
                progressed = False
                for i in range(n):
                    ev = events[i]
                    seq = ev.get("seq", 0)
                    if seq > last_sent:
                        yield _sse(ev)
                        last_sent = seq
                        last_emit = time.monotonic()
                        progressed = True
                if job["status"] in _TERMINAL and len(job["events"]) == n:
                    # Everything flushed and job finished -> close the stream.
                    yield "event: done\ndata: {}\n\n"
                    return
                if not progressed and (time.monotonic() - last_emit) >= SSE_KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_emit = time.monotonic()
                await asyncio.sleep(SSE_POLL_INTERVAL)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/jobs/{job_id}/export")
    async def export_job(
        job_id: str,
        request: Request,
        mode: str = "merged",
        x_api_key: str = Header(None),
        x_timestamp: str = Header(None),
        x_signature: str = Header(None),
    ):
        await authenticate(request, x_api_key, x_timestamp, x_signature)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if mode not in export_mod.MODES:
            raise HTTPException(
                status_code=400, detail=f"mode must be one of {export_mod.MODES}"
            )
        session_dir = job["session_dir"]
        try:
            if mode == "merged":
                md = export_mod.build_merged_markdown(session_dir)
                return Response(
                    content=md,
                    media_type="text/markdown; charset=utf-8",
                    headers=_attach(f"{job_id}_merged.md"),
                )
            if mode == "steps":
                data = export_mod.build_steps_zip(session_dir)
                return Response(
                    content=data,
                    media_type="application/zip",
                    headers=_attach(f"{job_id}_steps.zip"),
                )
            # mode == "json"
            ctx = export_mod.read_context(session_dir)
            return JSONResponse(content=ctx, headers=_attach(f"{job_id}_context.json"))
        except export_mod.ExportError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/health")
    async def health():
        with jobs_guard:
            running = sum(1 for j in jobs.values() if j["status"] == "running")
        return {"status": "ok", "active_jobs": running, "metrics": metrics.snapshot()}

    @app.get("/providers")
    async def list_providers():
        """Return all registered providers with metadata and live account pool status for frontend."""
        mgr = ProviderManager(providers_path)
        pool_status = {s["provider_name"]: s for s in mgr.get_pool().get_status_summary()}
        result = []
        for name, conf in mgr.providers.items():
            slot_info = pool_status.get(name, {})
            result.append({
                "id": name,
                "site": conf.get("site", ""),
                "label": conf.get("label", name),
                "model": conf.get("model"),
                "api": conf.get("profile", "") == "",
                "status": slot_info.get("status", "idle"),
                "fail_count": slot_info.get("fail_count", 0),
            })
        return result

    @app.post("/admin/cleanup")
    async def trigger_disk_cleanup(
        request: Request,
        x_api_key: str = Header(None),
        x_timestamp: str = Header(None),
        x_signature: str = Header(None),
    ):
        """Trigger an immediate disk cleanup (sessions + profile caches).

        Signed with the same HMAC scheme as all other endpoints.
        Returns counts of deleted sessions and cache dirs.
        Used by the frontend 'skip queue / 插队' button when the disk is full.
        """
        await authenticate(request, x_api_key, x_timestamp, x_signature)

        from src.cleanup import cleanup_sessions, cleanup_profile_caches

        profiles_root = os.environ.get("PROFILES_ROOT", "data/profiles")

        deleted_sessions: list[str] = []
        try:
            deleted_sessions = cleanup_sessions(sessions_root, dry_run=False)
        except Exception as exc:  # noqa: BLE001
            log_event(_log, "cleanup_sessions_error", level=logging.WARNING, error=str(exc))

        cache_result: dict = {"caches_removed": 0, "orphans_removed": 0}
        try:
            cache_result = cleanup_profile_caches(
                profiles_root,
                providers_path,
                include_service_worker=True,
                delete_orphans=False,
                dry_run=False,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(_log, "cleanup_profiles_error", level=logging.WARNING, error=str(exc))

        log_event(
            _log,
            "admin_disk_cleanup",
            sessions_deleted=len(deleted_sessions),
            caches_removed=cache_result.get("caches_removed", 0),
        )
        return {
            "status": "ok",
            "sessions_deleted": len(deleted_sessions),
            "caches_removed": cache_result.get("caches_removed", 0),
            "orphans_removed": cache_result.get("orphans_removed", 0),
        }

    return app


def _sse(ev: dict) -> str:
    return f"id: {ev.get('seq', '')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _attach(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


# Module-level ASGI app for `uvicorn src.main:app` (Dockerfile CMD).
app = create_app()


__all__ = ["create_app", "app", "RateLimiter"]
