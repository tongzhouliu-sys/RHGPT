# RHCLOUD V1 — Project Knowledge Base

> **Audience**: New engineers joining the project. After reading this document you should be able to navigate the codebase, run tests, understand why things are built the way they are, and know exactly which file to touch for any category of change.
>
> **Derived from**: Direct reading of every source file, test, configuration file, and documentation file in the repository. Inferred behaviour (not directly observable without running the system) is labelled *[inferred]*.

---

## 1. Project Overview

### What problem does this project solve?

Modern AI tasks often benefit from having multiple language models review and refine each other's work — a "model relay" pattern. RHCLOUD V1 automates this: a user asks one question, and the system routes it through a configurable chain of AI providers (e.g. ChatGPT generates a draft → Gemini reviews it → Claude deep-analyses → ChatGPT refines → Gemini summarises). Every intermediate response streams to the user in real time and is persisted as artefacts.

The secondary problem: many capable AI interfaces (ChatGPT web, Claude web, Kimi, DeepSeek, Z.AI) offer no affordable API. RHCLOUD V1 bridges them via browser automation (Playwright), treating each web UI as a provider alongside official API providers.

### Target users

Internal teams or controlled-access users who want to orchestrate multi-model pipelines. Designed for a **single trusted operator** (self-hosted, not public SaaS).

### Business goals

- Run a 5-step relay pipeline across ≥ 3 distinct AI models with ≥ 1 official API.
- Stream progress in real time with no polling required by default.
- Support new providers with zero changes to the core engine.
- Survive browser session expiry with automatic failover.
- Deploy on Railway (backend) + Cloudflare Pages (frontend) as a single-repo monorepo.

### Major features

| Feature | Description |
|---------|-------------|
| Sequential relay pipelines | YAML-defined step chains; each step builds on prior outputs |
| Multi-model racing | Optional: multiple providers compete per step; first to emit output wins |
| Web automation providers | Playwright-based session reuse for ChatGPT, Claude, DeepSeek, Kimi, Z.AI, Qwen, Gemini web |
| Official API providers | Gemini, Qwen (DashScope), OpenAI-compatible, Anthropic Messages API |
| Real-time SSE streaming | Reconnectable event stream with `Last-Event-ID` replay; no event lost or duplicated |
| File-based persistence | No SQL database; every job writes Markdown + JSON artefacts to disk |
| HMAC authentication | Replay-resistant request signing (±300s window) |
| Session seeding | One-command local login → Railway Volume injection workflow |
| Export | Merged Markdown / per-step ZIP / context JSON |
| Mock mode | Frontend runs entirely without a backend (`NEXT_PUBLIC_USE_MOCK=1`) |

---

## 2. Overall Architecture

### Architecture style

**Single-process monolith with layered team ownership.** A single Python process (FastAPI + Uvicorn) runs on Railway. There is no microservice boundary, no message queue, no external database. The A / B / C split is a *team contract*, not a deployment boundary.

```
┌──────────────────────────────────────────────────────────────┐
│  Cloudflare Pages  (Next.js static export, SSG)              │
│  frontend/                                                    │
│  HMAC-signed fetch + manual SSE (EventSource cannot send     │
│  custom headers; Web Crypto API signs instead)               │
└──────────────────────────┬───────────────────────────────────┘
                           │  HTTPS + HMAC  (JSON / SSE)
┌──────────────────────────▼───────────────────────────────────┐
│  Railway  —  Single Docker container  (single instance)       │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  C  FastAPI Gateway  (src/main.py)                   │    │
│  │  HMAC auth · CORS · Rate limit · Concurrency         │    │
│  │  SSE streaming · Export · Alerts · /health           │    │
│  └───────────────────┬──────────────────────────────────┘    │
│                      │ calls (in ThreadPoolExecutor thread)   │
│  ┌───────────────────▼──────────────────────────────────┐    │
│  │  A  Runtime Engine  (src/runtime.py)                 │    │
│  │  Pipeline orchestration · Classified retry           │    │
│  │  Profile-level mutex · Persistence                   │    │
│  └───────────────────┬──────────────────────────────────┘    │
│                      │ calls (via manager.py importlib)       │
│  ┌───────────────────▼──────────────────────────────────┐    │
│  │  B  Providers  (src/providers/*.py)                  │    │
│  │  Web automation (_browser.py) │ Official API calls   │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  Volume: /app/data/  (profiles/ + sessions/)                 │
└──────────────────────────────────────────────────────────────┘
```

### Layers and responsibilities

| Layer | Owner | Key files | Responsibility |
|-------|-------|-----------|----------------|
| **A** | Backend kernel | `src/runtime.py`, `src/builder.py`, `src/manager.py`, `src/validation.py`, `src/account_pool.py`, `src/cleanup.py`, `src/logging_conf.py`, `src/providers/_errors.py`, `src/providers/stub.py` | Pipeline orchestration, prompt building, provider dispatch, validation, account pool, cleanup, structured logging |
| **B** | Providers & automation | `src/providers/` (web + API), `pipelines/*.yaml`, `prompts/*.md` | Per-site browser automation, official API clients, pipeline definitions, prompt templates |
| **C** | Gateway & frontend | `src/main.py`, `src/auth.py`, `src/export.py`, `src/alerts.py`, `frontend/` | HTTP API, HMAC auth, SSE, export, alert tracking, Next.js UI |

### Dependency graph

```
main.py (C)
  ├── runtime.py (A)          ← core orchestration
  │     ├── builder.py (A)    ← prompt rendering
  │     ├── manager.py (A)    ← provider dispatch
  │     │     └── providers/*.py (B)  [loaded dynamically by importlib]
  │     ├── validation.py (A) ← pipeline + config checking
  │     ├── account_pool.py (A) ← account health + queuing
  │     └── logging_conf.py (A) ← structured logger + metrics
  ├── auth.py (C)             ← HMAC signing + keystore
  ├── export.py (C)           ← session artefact export
  ├── alerts.py (C)           ← consecutive failure tracking
  └── logging_conf.py (A)     ← shared logger + metrics
```

Provider modules are never imported directly by `main.py` or `runtime.py`. They are discovered and loaded at call time by `manager.py` via `importlib.import_module(f"src.providers.{site}")`.

---

## 3. Directory Guide

```
rhgpt/
├── src/                        Python source — backend kernel + gateway
│   ├── __init__.py             Empty (makes src a package)
│   ├── main.py                 FastAPI app factory + all HTTP routes
│   ├── runtime.py              Pipeline execution engine
│   ├── manager.py              Provider dynamic loader + config resolver
│   ├── builder.py              Prompt template substitution
│   ├── validation.py           Config + pipeline validation (4 checks)
│   ├── account_pool.py         Account health, busy/idle/expired tracking + queuing
│   ├── auth.py                 HMAC signing + keystore loading
│   ├── logging_conf.py         Structured JSON logging + in-process metrics
│   ├── alerts.py               Consecutive failure alerting
│   ├── export.py               Session artefact export (merged/steps/json)
│   ├── cleanup.py              Session directory retention cleanup + CLI
│   ├── stubs.py                Emit-sink helpers for parallel development & tests
│   └── providers/
│       ├── __init__.py         Empty
│       ├── _errors.py          FROZEN: ProviderError, SessionExpiredError, GenerationTimeout
│       ├── _browser.py         Shared Playwright engine (persistent contexts, anti-detection)
│       ├── _extract.py         Tolerant JSON/SSE text extraction utility
│       ├── stub.py             Deterministic stub provider (no model, no network)
│       ├── anthropic_api.py    Anthropic Messages API (streaming + non-streaming)
│       ├── openai_api.py       OpenAI-compatible API (streaming + non-streaming)
│       ├── gemini_api.py       Google Gemini REST API (model waterfall + 429 retry)
│       ├── qwen_api.py         Alibaba Qwen DashScope API
│       ├── claude.py           claude.ai web automation
│       ├── chatgpt.py          chatgpt.com web automation
│       ├── gemini.py           gemini.google.com web automation
│       ├── qwen.py             chat.qwen.ai web automation
│       ├── deepseek.py         chat.deepseek.com web automation
│       ├── kimi.py             kimi.com web automation
│       └── zai.py              chat.z.ai web automation
│
├── frontend/                   Next.js 14 App Router console
│   ├── app/
│   │   ├── page.tsx            Main relay console (state machine + SSE consumer)
│   │   ├── layout.tsx          Root layout (metadata, viewport, lang="zh-CN")
│   │   ├── globals.css         All styling (CSS vars, dark/light themes, animations)
│   │   └── components/
│   │       ├── ModelProbe.tsx  Live provider status badge (polls GET /providers)
│   │       ├── AgentLogo.tsx   SVG icons + brand colours per provider
│   │       └── AvatarScene.tsx Animated office metaphor (dispatcher → messenger → agent)
│   ├── lib/
│   │   ├── api.ts              HMAC signing + all API calls + SSE reconnect + polling fallback
│   │   ├── sse.ts              SSE frame parser (fetch ReadableStream, not EventSource)
│   │   ├── markdown.ts         XSS-safe minimal Markdown → HTML renderer
│   │   └── mock.ts             In-browser mock backend (scripted 7-event sequence)
│   ├── package.json            Next.js 14, React 18, TypeScript 5 (no external UI lib)
│   └── README.md               Frontend-specific setup + env vars
│
├── pipelines/                  YAML pipeline definitions (owned by B)
│   ├── round1.yaml             5-step serial relay: chatgpt→gemini→claude→chatgpt→gemini
│   ├── race_round1.yaml        5-step relay with multi-model racing per step
│   ├── continue.yaml           4-step "再来一轮": skip generate, recap then continue
│   └── api_smoke.yaml          Single-step Gemini API smoke (no browser needed)
│
├── prompts/                    Markdown prompt templates (owned by B, Chinese language)
│   ├── generate.md             Step 1: structured initial answer (expert role)
│   ├── review.md               Step 2: critical review (finds flaws, not praises)
│   ├── deep_analyze.md         Step 3: deep analysis
│   ├── improve.md              Step 4: incorporate review into improved answer
│   ├── summary.md              Step 5: synthesise final concise answer
│   └── recap.md                Used by continue.yaml: faithful restatement of prior output
│
├── config/
│   └── providers.yaml          Provider instance definitions + global retry defaults
│
├── data/                       Runtime data (gitignored; on Railway Volume)
│   ├── profiles/               Playwright browser profiles (login state per account)
│   └── sessions/               Per-job artefact directories (uuid4 names)
│
├── docs/
│   ├── contracts.md            FROZEN: three-party interface contracts (must-read)
│   ├── api.md                  HTTP API reference
│   ├── deploy.md               End-to-end deployment guide
│   ├── session_seeding.md      Browser session injection workflow
│   └── PROJECT_KNOWLEDGE_BASE.md  ← this file
│
├── scripts/
│   ├── smoke_stub.py           M1 smoke: run full pipeline with stub provider, print artefacts
│   ├── seed_session.py         Generic site login + profile persistence helper
│   └── login_qwen.py           Qwen-specific login + ZIP packaging
│
├── tests/
│   ├── unit/                   50 unit tests (zero network, zero browser)
│   ├── integration/            6 runtime integration tests (real runtime, stub providers)
│   ├── contract/               18 provider contract tests (fake Playwright + stubbed requests)
│   ├── api/                    17 gateway tests (httpx TestClient + stub runtime)
│   ├── e2e/                    Manual E2E (real providers, pre-release only)
│   └── fixtures/
│       ├── providers.yaml      Test-only provider config pointing at fixture package
│       ├── providers/          Four test provider modules (ok, slow, expire, flaky)
│       ├── pipelines/          Test pipeline YAMLs (ok2, flaky1, expire1, slow_a, slow_b, bad_*)
│       └── prompts/            Minimal test prompt templates (p1.md, p2.md, etc.)
│
├── Dockerfile                  playwright/python:v1.40.0-jammy + xvfb + uvicorn CMD
├── railway.toml                Dockerfile build + /health check + single-instance note
├── .env.example                All environment variable documentation
├── requirements.txt            fastapi, uvicorn, pyyaml, playwright, playwright-stealth, requests
├── requirements-dev.txt        Adds: pytest, httpx
├── run_local.sh                One-command local start (uvicorn + npm run dev in parallel)
└── .github/workflows/ci.yml   lint(ruff) → unit → integration → contract → api → docker-build
```

---

## 4. Module Breakdown

### `src/runtime.py` — Pipeline Execution Engine

**Purpose**: Orchestrate the sequential steps defined in a pipeline YAML, building prompts, calling providers, persisting artefacts, and emitting typed events. This is the most important source file in the project.

**Public contract** (frozen in `docs/contracts.md`):
```python
def run_pipeline(
    pipeline_path: str,
    user_question: str,
    session_dir: str,
    emit: Callable[[dict], None],
    *,
    builder: Optional[PromptBuilder] = None,   # injectable for tests
    manager: Optional[ProviderManager] = None, # injectable for tests
    validate: bool = True,                     # defense-in-depth flag
    job_id: Optional[str] = None,              # for structured logging
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict  # {"user_question": str, "outputs": {step_key: text}}
```

The optional keyword args have defaults, so C's existing 4-argument call site (`run_pipeline(pipeline_path, user_question, session_dir, emit)`) remains valid.

**Module-level state**:
- `_profile_locks: dict[str, threading.Lock]` — per-profile serialization locks, created on first use, never destroyed
- `_locks_guard: threading.Lock` — protects mutations to `_profile_locks`

**Key functions**:

| Function | Responsibility |
|----------|---------------|
| `run_pipeline(...)` | Top-level orchestrator; reads pipeline YAML, iterates steps |
| `_run_step_with_retry(...)` | Acquires account slot, calls provider under profile lock, classified retry |
| `_lock_for(profile)` | Returns process-wide `threading.Lock` for a profile path (creates on first use) |
| `_now_iso()` | UTC ISO-8601 timestamp string |
| `_ms_between(start, end)` | Duration in ms between two ISO timestamps |
| `_fail(etype, msg, attempt, started)` | Construct a failed-step result dict |
| `_write(path, text)` | Write text file (A's persistence helper) |
| `_write_json(path, obj)` | Write JSON file (A's persistence helper) |

**Execution flow for a single step**:

```
1. Check is_cancelled() → emit fatal + break if true
2. builder.build(prompt_name, context) → rendered prompt string
3. Write NN_{key}_prompt.md to session_dir
4. Determine provider list (single provider or multi-provider race)
5. Apply adjacent-step diversity rule (if multi-provider and last_winner_provider known,
   exclude that winner from candidates — at least one other must remain)
6. emit({type:"step_started", key, provider, label, model, seq:N})
7. _run_step_with_retry(manager, provider_name, prompt, conf, key, ...):
   a. pool.acquire_account(provider_name) — blocks until idle slot (→ step_queued if waiting)
   b. _lock_for(conf["profile"]).__enter__() — serialize per profile
   c. manager.run(active_provider, prompt, on_chunk=...) → response_text
   d. on SessionExpiredError: pool.mark_expired(), switch target to site,
      retry (budget = retries × 2); if exhausted → _fail("session_expired")
   e. on any other Exception (GenerationTimeout or other transient):
      pool.release_account(), exponential backoff (retry_backoff_ms × 2^(attempt-1)),
      retry up to retries times; if exhausted → _fail("transient")
   f. on empty string return: raise RuntimeError (treated as transient)
   g. on success: pool.release_account(), return StepResult with content
8. Measure duration_ms; metrics.observe(step_duration_seconds, ...)
9. On success: write NN_{key}_response.md, emit step_succeeded
   Emit step_transitioning (key of next step) if not last step
10. On failure: write NN_{key}_error.json, emit step_failed, break (pipeline interrupted)
11. After all steps (or break): write context.json, emit pipeline_finished
```

**Multi-provider race mode** (detailed):
```
1. Spawn ThreadPoolExecutor(max_workers=len(provider_list))
2. Each candidate thread runs run_candidate(candidate_name):
   - Calls _run_step_with_retry with its own on_chunk callback
   - on_chunk: acquires race_state["lock"]
     → if race_state["winner"] is None: this candidate becomes winner,
       emit step_started + step_chunk as primary
     → else if this is winner: emit step_chunk as primary
     → else: emit runnerup_chunk
   - On success: if winner is None, claim it and emit step_started;
     else if not winner, emit runnerup_succeeded with full content
3. as_completed() collects results; picks the winner's result as the step result
4. If all fail: use first_failed result
5. If somehow no result: _fail("transient", "all race candidates failed")
```

**Important edge case**: API providers all share `profile=""`. They therefore share a single lock keyed by the empty string. This is harmless because API calls are thread-safe (pure HTTP), but means API providers technically serialize per-profile. *[Comment in source notes this is intentional and safe.]*

---

### `src/main.py` — FastAPI Gateway

**Purpose**: Wrap the runtime engine in an HTTP/SSE service. This is the only entry point for external requests.

**Design pattern**: `create_app(...)` is a **factory function** — all state (keystore, job registry, `ThreadPoolExecutor`, rate limiter, alert tracker) lives inside the closure. The module-level `app = create_app()` at the bottom is the ASGI app Uvicorn imports. Tests inject `create_app(run_pipeline_fn=stub_runtime, keystore={...}, ...)` without touching any globals.

**Module-level state**:
- None. All state is inside the `create_app()` closure.

**Closure-level state** (lives for the process lifetime):
- `jobs: dict[str, dict]` — in-memory job registry; keyed by `job_id` (UUID4)
- `jobs_guard: threading.Lock` — protects `active["n"]` counter mutations
- `active: dict` — `{"n": int}` current active job count
- `executor: ThreadPoolExecutor` — runs worker threads
- `rate_limiter: RateLimiter` — per-key fixed-window counter
- `alerter: AlertTracker` — consecutive failure streak tracker
- `pass_runtime_kwargs: bool` — pre-computed once via `_accepts_runtime_kwargs(run_pipeline_fn)`

**`_accepts_runtime_kwargs(fn)`**: Inspects the runtime function's signature at startup. Returns `True` if it accepts `**kwargs` or has a `builder` parameter (i.e. is the real runtime). Used to decide whether to call the runtime with the injected `builder`/`manager`/`job_id` kwargs. This prevents a `TypeError` if a 4-argument stub runtime is injected for tests.

**Middleware stack** (applied in order at request time):
1. `CORSMiddleware` — allows only `FRONTEND_ORIGIN`, allows methods `GET/POST/OPTIONS`, allows headers `Content-Type, X-Api-Key, X-Timestamp, X-Signature, Last-Event-ID`. Never `*`.
2. FastAPI's own exception handlers (implicit)

**`RateLimiter`**: Fixed-window per-API-key. State: `{key → (window_start, count)}`. Window resets when `now - start >= 60s`. Not distributed; in-memory only.

**`make_emit(job)`**: Produces the emit closure injected into the runtime.
- Maintains a `seq_state = {"max": 0}` counter plus a per-job `threading.Lock`.
- Events from the runtime arrive pre-numbered with `seq`. Worker-level `fatal` events (watchdog timeout, uncaught exceptions) have no `seq` — the closure assigns `seq_state["max"] + 1`, continuing the numbering.
- Writes each event to `events.jsonl` (append) AND appends to `job["events"]` (in-memory).
- Calls `alerter.observe(ev)` for every event.

**`run_worker(job_id, pipeline, user_question)`**: Runs in a `ThreadPoolExecutor` thread.
- Creates `emit` closure via `make_emit(job)`.
- Starts a `threading.Timer(job_timeout, _watchdog)` as a daemon thread.
- Calls `run_pipeline_fn(...)` (real runtime or injected stub).
- **Watchdog** (`_watchdog()`): If triggered, sets `job["cancelled"] = True`, decrements `active["n"]` (guarded by `jobs_guard` and `_released` flag to prevent double-decrement), emits `fatal` event with `type="timeout"`. The `_released` boolean flag on the job dict prevents both the watchdog and the `finally` block from decrementing `active["n"]`.
- `finally` block: cancels watchdog timer, decrements `active["n"]` if not already released.

**SSE generator** (`gen()` inside `stream_events`):
- Polls `job["events"]` list every `SSE_POLL_INTERVAL = 0.25s`.
- Tracks `last_sent` seq; only yields events with `seq > last_sent`.
- When `job["status"]` reaches terminal AND all events have been sent: yields `"event: done\ndata: {}\n\n"` and returns.
- Sends keepalive comment (`: keepalive`) every `SSE_KEEPALIVE_SECONDS = 15s` if no events to send.
- Checks `request.is_disconnected()` (async) on every iteration.

**`create_job` — `selected_providers` dynamic pipeline generation**:
- If `selected_providers` is a non-empty list, the gateway generates a pipeline YAML on the fly and writes it to `{session_dir}/pipeline.yaml`.
- The dynamic pipeline has 5 hardcoded steps: `generate, review, deep_analyze, improve, summary`.
- Each step uses all selected providers if `len > 1` (race mode) or one provider if `len == 1`.
- The dynamic pipeline is then validated with `validate_pipeline_file` before use.

**Routes summary**:

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/jobs` | ✓ | Rate-limited + concurrency-gated; optional `selected_providers` |
| GET | `/jobs/{id}` | ✓ | Polling fallback; returns full event list |
| POST | `/jobs/{id}/cancel` | ✓ | Sets `cancelled=True`, status=`failed`, emits `fatal{type:"cancelled"}` |
| GET | `/jobs/{id}/events` | ✓ | SSE; `Last-Event-ID` replay; keepalive every 15s |
| GET | `/jobs/{id}/export` | ✓ | `?mode=merged\|steps\|json` |
| GET | `/health` | ✗ | No auth; returns `{status, active_jobs, metrics}` |
| GET | `/providers` | ✗ | No auth; returns live pool status per provider |

---

### `src/manager.py` — Provider Manager

**Purpose**: Load provider modules lazily and merge per-instance YAML config with global defaults.

**Constructor** (`__init__`):
1. Opens and parses `providers.yaml`.
2. Merges global `defaults` with hardcoded `DEFAULTS = {"timeout_ms": 120000, "retries": 2, "retry_backoff_ms": 3000}`.
3. Initialises `AccountPoolManager.get_instance(self.config)` — this triggers the process-wide singleton creation on first call.

**`resolve(provider_name)`**: Returns a fully-merged config dict. Fields: `site, profile, model, label, base_url, api_key_env, timeout_ms, retries, retry_backoff_ms`.

**`_load(site)`**: Loads `{provider_package}.{site}` via `importlib.import_module`. Caches in `_module_cache`. Raises `ValueError` (not `ImportError`) if module is missing, so callers see a clean error message.

**`run(provider_name, prompt, **options)`**:
1. `resolve()` the config.
2. `_load()` the site module.
3. Checks `hasattr(module, "run")` — raises `ValueError` if missing (Contract 1 violation).
4. Builds `extra` dict from non-None config fields: `model`, `base_url`, `api_key_env`.
5. Calls `module.run(conf["profile"], prompt, timeout_ms=..., **extra, **options)`.

**Note**: `options` from the caller (including `on_chunk`) are passed through via `**options`. The `**extra` from config takes precedence over defaults inside the provider module, but `**options` (caller-provided) can override `**extra` because they are merged last.

---

### `src/account_pool.py` — Account Pool Manager

**Purpose**: Track the health and busy state of every configured provider account; provide orderly slot acquisition with queuing and session-expiry failover.

**Singleton pattern**: `AccountPoolManager.get_instance(providers_cfg)` uses a class-level `_instance_lock` to create the singleton safely under concurrent first-calls. If called again with `providers_cfg`, it calls `load_providers` to refresh config without creating a new instance. Tests call `AccountPoolManager.reset_instance()` in `setUp` to force a fresh pool.

**`load_providers(providers_cfg)`**: Idempotent update. Adds new `AccountSlot` objects for new provider names; updates `config/site/profile` for existing ones. Called both at construction and when the singleton is re-configured.

**Account states**:
```
IDLE ──acquire()──► BUSY ──release()──► IDLE
BUSY ──mark_expired()──► EXPIRED  (never returns to IDLE; requires re-seeding)
BUSY/IDLE ──mark_cooldown()──► COOLDOWN
```
`COOLDOWN` is defined in the state machine but the cooldown timer (return to IDLE after N seconds) is not implemented in the current code — `mark_cooldown` is called but there is no background timer to transition back to IDLE. *[This means cooldown acts like a softer expired state until the process restarts.]*

**`acquire_account(target, timeout_ms, on_queue, is_cancelled)`**:
- `target` can be a `provider_name` (specific slot) or `site` name (round-robin across site).
- Holds `self._lock` (a `threading.Condition`) for the entire acquisition attempt.
- Selects from idle candidates sorted by `last_used` (least-recently-used first).
- If all candidates are `EXPIRED` (none are `IDLE` or `BUSY`): returns `(None, "all accounts ... expired")`— no waiting.
- If all candidates are `BUSY`: increments `_queues[site]`, calls `on_queue(pos)`, then `self._lock.wait(timeout=min(1.0, remaining))` in a loop.
- Returns `(chosen_slot, None)` on success; `(None, error_msg)` on timeout or cancellation.

**`release_account(provider_name)`**: Only transitions `BUSY → IDLE`. Does nothing if the slot is not `BUSY` (prevents releasing an already-expired or cooldown account back to idle). Calls `self._lock.notify_all()` to wake waiting acquirers.

**`mark_expired(provider_name)`**: Transitions to `EXPIRED`, increments `fail_count`, calls `notify_all()` (so waiters can re-evaluate and potentially fail-fast if all accounts are now expired).

**Known issue in source**: `get_status_summary()` is defined as an instance method but is missing the `self` parameter (`def get_status_summary() -> List[dict]:`). This would raise `TypeError` if called directly. However, it is only called in `main.py` via `mgr.get_pool().get_status_summary()` with the instance already bound — Python will still error. The `/providers` route works because `pool_status = {s["provider_name"]: s for s in mgr.get_pool().get_status_summary()}` — this would fail at runtime. *[This is a latent bug.]*

---

### `src/auth.py` — HMAC Authentication

**Purpose**: Stateless request signing and verification. Framework-free (stdlib only).

**Signing scheme** (frozen in `docs/contracts.md` and implemented identically in TypeScript):
```
canonical = METHOD.upper() + "\n" + PATH + "\n" + X-Timestamp + "\n" + sha256_hex(body)
X-Signature = hex( HMAC-SHA256(secret.encode("utf-8"), canonical.encode("utf-8")) )
```

**Important**: `PATH` is the URL path **without** query string. The `?mode=merged` in export URLs is not signed.

**`verify_request(keystore, method, path, *, api_key, timestamp, signature, body, now, max_skew=300)`**:
1. Any missing header → `AuthError("missing authentication headers")`
2. Unknown `api_key` → `AuthError("unknown api key")`
3. Non-integer `timestamp` → `AuthError("invalid timestamp")`
4. `|now - timestamp| > max_skew` → `AuthError("timestamp outside allowed window")`
5. Signature mismatch → `AuthError("signature mismatch")` (via `hmac.compare_digest` — constant time)

All errors use the same generic-ish messages (no oracle about which check failed). The `AuthError.status` field carries 401.

**Keystore loading precedence**: `RHCLOUD_API_KEYS` (multi-key) wins over `RHCLOUD_API_KEY` + `RHCLOUD_API_SECRET` (single-key). On failure, `create_app()` catches the `ValueError`, logs a warning, and uses an empty keystore — so the server starts but every authed route returns 401 (fail-closed).

---

### `src/logging_conf.py` — Structured Logging & Metrics

**Purpose**: Install a JSON-per-line log formatter on the root logger; maintain in-process metric counters/gauges/histograms.

**Idempotency**: `configure_logging()` is guarded by a `_configured: bool` flag and `_configure_guard: threading.Lock`. Subsequent calls are no-ops. This prevents duplicate handlers if `create_app()` is called multiple times (e.g. in tests).

**`JsonFormatter.format(record)`**: Builds a payload dict from allowlisted fields only. The human-readable `getMessage()` string is included as `"msg"`. Structured fields (`event`, `job_id`, `step_key`, etc.) are pulled from `record.__dict__`. No prompt or response bodies are ever logged.

**`log_event(logger, event, *, ...)` helper**: Calls `logger.log(level, event, extra={...})`. The `extra` dict fields appear on `record.__dict__`, making them available to `JsonFormatter`. This is the canonical way to emit a structured log line throughout the codebase.

**`Metrics` singleton**: Process-wide instance `metrics` (module-level) shared across all threads. `threading.Lock` guards all mutations. `snapshot()` computes histogram summaries (count, sum, avg, max) on demand.

**Canonical metric names** (string constants on the module):

| Constant | Metric name | Type |
|----------|-------------|------|
| `M_JOBS_TOTAL` | `jobs_total` | Counter |
| `M_JOBS_FAILED_TOTAL` | `jobs_failed_total` | Counter |
| `M_STEP_DURATION_SECONDS` | `step_duration_seconds` | Histogram (labelled `provider,site`) |
| `M_STEP_RETRIES_TOTAL` | `step_retries_total` | Counter (labelled `provider`) |
| `M_SESSION_EXPIRED_TOTAL` | `session_expired_total` | Counter (labelled `provider`) |
| `M_ACTIVE_JOBS` | `active_jobs` | Gauge |

**Metric label encoding**: Labels are encoded as `name{key1=val1,key2=val2}` (sorted keys) for the dict key. Not Prometheus wire format; just a plain dict exposed via `/health`.

---

### `src/alerts.py` — Consecutive Failure Alerting

**Purpose**: Fire a loud ERROR log when the same class of error occurs N consecutive times without a success in between.

**`AlertTracker.observe(event)`**:
- `step_succeeded` or `pipeline_finished` → `self._counts.clear()` (all streaks reset)
- `step_failed` or `fatal` → extract `event["error"]["type"]`, increment that type's count; **also zero all other error types** (a new failure class resets the others — "consecutive" is per-class, not cross-class)
- When count reaches `threshold`: call `_on_alert(error_type, count)`
- Keeps firing on every subsequent failure of the same type (sustained outage keeps signalling)
- All other event types: no-op

**Default alert sink**: Calls `log_event(_log, "alert_consecutive_failures", level=ERROR, error_type=..., attempt=count)`.

**`threshold_from_env()`**: Reads `ALERT_CONSECUTIVE_THRESHOLD`, validates it is a positive integer, falls back to `DEFAULT_THRESHOLD = 3`.

---

### `src/export.py` — Session Artefact Export

**Purpose**: Read persisted session artefacts and produce downloadable export shapes.

**File discovery**: Uses regex patterns against directory listings:
- `_RESP_RE = r"^(\d+)_(?P<key>.+)_response\.md$"` — finds step responses
- `_ERR_RE = r"^(\d+)_(?P<key>.+)_error\.json$"` — finds step errors

**`key_provider_map(session_dir)`**: Reads `events.jsonl` line by line; for each event with both `key` and `provider`, records `mapping.setdefault(key, provider)` (first appearance wins — the winner in a race). Used to annotate merged export headings.

**Three export modes**:
- `merged`: Includes original question (from `context.json`), each step response as a heading with provider name, and any failed steps as warning blocks.
- `steps`: ZIP of `NN_{key}_response.md` and `NN_{key}_error.json` files.
- `json`: Returns `context.json` verbatim.

**`ExportError`**: Raised when `session_dir` doesn't exist, `context.json` is missing/invalid, or no step outputs exist. `main.py` maps this to HTTP 404.

**`MODES = ("steps", "merged", "json")`**: The tuple used for validation in `main.py`.

---

### `src/cleanup.py` — Session Retention Cleanup

**Purpose**: Delete session directories older than the retention window.

**Age measurement**: Uses `os.path.getmtime(path)` — directory mtime ≈ time of last write ≈ job end. Acknowledged in comments as a coarse approximation.

**Retention precedence**: explicit `--days` CLI arg > `SESSION_RETENTION_DAYS` env var > `DEFAULT_RETENTION_DAYS = 14`.

**CLI invocation**: `python -m src.cleanup --root data/sessions [--days 14] [--dry-run]`. The `__main__` guard calls `configure_logging()` before running, ensuring JSON log output even when run standalone.

---

### `src/stubs.py` — Emit-Sink Helpers

**Purpose**: Provide `emit` substitutes for parallel development (B/C work without waiting for each other) and for A's own integration tests.

**Three variants**:
- `make_recording_emit()` → `(emit, events_list)`: In-memory only. Events list is live and assertable.
- `make_file_emit(session_dir)` → `(emit, events_list)`: In-memory + appends to `events.jsonl`. Simulates C's emit.
- `print_emit(ev)` → `None`: Prints JSON to stdout. For ad-hoc manual runs.

**Key invariant**: None of these stubs renumber `seq`. They persist events exactly as received from the runtime.

---

### `src/providers/_errors.py` — Error Contract (Frozen)

**`ProviderError(Exception)`**: Base class for all provider errors. Carries `self.profile`.

**`SessionExpiredError(ProviderError)`**: Login state invalid. **FATAL** — runtime does not retry; marks account EXPIRED; attempts failover.

**`GenerationTimeout(ProviderError)`**: Generation timed out. **TRANSIENT** — runtime retries with exponential backoff.

**Extension rule**: B may add new exception classes. If they extend `ProviderError` or `Exception`, they are treated as transient. If a new **fatal** class is needed, it must be added to `_errors.py` and `docs/contracts.md`, and `runtime.py` must explicitly handle it (currently only `SessionExpiredError` is special-cased).

**Empty string return**: Treated the same as transient — `runtime.py` raises `RuntimeError("provider returned empty content")` which falls into the generic `except Exception` transient branch.

---

### `src/providers/_browser.py` — Shared Playwright Engine

**Purpose**: Persistent browser context pool + anti-detection + `run_web` engine used by all web providers.

**Thread model**: Playwright's `sync_api` is not thread-safe between threads sharing a single `Playwright` instance. The solution is **thread-local `Playwright` instances**:
- `_THREAD_LOCAL = threading.local()` holds a `pw` attribute per thread.
- `_ensure_playwright()` checks `_THREAD_LOCAL.pw`; creates and starts a new `sync_playwright()` if absent.
- Browser contexts are also stored per-thread in `_THREAD_LOCAL.contexts`.

This means each worker thread has its own Playwright process (or browser), and contexts for the same profile on different threads are distinct objects. The profile-level mutex in `runtime.py` ensures only one thread runs a given profile at a time, preventing duplicate contexts from being created for the same profile.

**Context pool**: `_CONTEXTS` (module-level dict) tracks all created contexts keyed by `{thread_id}_{profile}`. This is used only by `shutdown()` for cleanup. The per-thread `_THREAD_LOCAL.contexts` dict is the primary cache.

**`get_context(profile)`** — double-check locking:
```
1. Check _THREAD_LOCAL.contexts for existing ctx → return if found (fast path)
2. Acquire _profile_lock(profile)           ← per-profile lock from _CTX_LOCKS
3. Check _THREAD_LOCAL.contexts again       ← in case another thread created it
4. _ensure_playwright()                     ← lazy-init thread-local Playwright
5. pw.chromium.launch_persistent_context(
       profile,
       headless=_headless(),               ← RHCLOUD_HEADLESS env var
       user_agent=DEFAULT_UA,
       locale="zh-CN",
       timezone_id="Asia/Shanghai",
       viewport={"width":1280,"height":800},
       args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
   )
6. ctx.add_init_script(_STEALTH_JS)        ← masks webdriver, plugins, languages
7. Store in _THREAD_LOCAL.contexts[profile] AND _CONTEXTS[f"{thread_id}_{profile}"]
```

**Fallback path**: If `launch_persistent_context` fails (profile locked by another process), falls back to `pw.chromium.launch()` + `browser.new_context()`. This creates a non-persistent ephemeral context — the session state from the profile is not loaded. This is a graceful degradation that may cause `SessionExpiredError` on the next navigation if the site requires login.

**Stealth JS** (`_STEALTH_JS`) patches:
- `navigator.webdriver` → `undefined`
- `navigator.languages` → `['zh-CN', 'zh', 'en']`
- `navigator.plugins` → `[1, 2, 3, 4, 5]` (non-empty)
- `window.chrome` → `{ runtime: {} }` (present but minimal)
- `navigator.permissions.query` — returns `Notification.permission` for `notifications`

**`run_web(site, profile, prompt, timeout_ms, on_chunk)`** — execution:
1. `get_context(profile)` → persistent `BrowserContext`
2. `ctx.new_page()` → fresh page
3. Register `on_response` callback on `page.on("response")` for URL interception
4. `page.goto(site["url"], wait_until="domcontentloaded", timeout=min(30000, timeout_ms))`
5. `_is_login_page(page, site)` → if True: raise `SessionExpiredError`
6. `_submit_prompt(page, site, prompt)`:
   - `page.locator(input_selector).click(timeout=6000)` — if fails and login page: `SessionExpiredError`; else `GenerationTimeout`
   - `page.keyboard.type(prompt, delay=type_delay_ms)` — human-like typing
   - If `send_selector`: click it (fallback to Enter); else press Enter
7. `_wait_generation_done(page, site, timeout_ms, on_chunk, captured)`:
   - **Without on_chunk**: `page.wait_for_selector(done_selector, timeout=timeout_ms, state=state)` — blocks until done
   - **With on_chunk**: polls `wait_for_selector(..., timeout=1000)` in a loop; on each timeout, extracts DOM text delta and calls `on_chunk`; total wall-clock checked against deadline
8. Final text: `captured["text"]` (from network interception) or `_extract_dom(page, site)` (DOM fallback)
9. Empty result → raise `GenerationTimeout`
10. `page.close()` in `finally` (context stays alive in pool)

**`on_response` network interception**: Called for every HTTP response. Filters by URL matching `site["response_match"]`. Calls `site["parse"](body)` to extract text. If the new text starts with the previous text, emits only the delta (streaming). Exceptions in `on_response` are silently swallowed — a single unparseable frame must never crash the run.

**`shutdown()`**: Closes all tracked contexts from `_CONTEXTS`, stops thread-local Playwright. Used in tests and process teardown.

---

### `src/providers/_extract.py` — Generic Text Extraction

**Purpose**: Extract assistant text from diverse response body shapes (SSE frames, single JSON, NDJSON).

**Two-strategy extraction**:
1. Try parsing the whole body as a single JSON document → `_from_json_obj()`
2. If that fails: split into lines, strip `data:` prefixes (SSE), skip `[DONE]`, parse each line as JSON

**`_collect_strings(obj, out)`**: Walks JSON recursively. Priority keys: `content, text, completion, delta, parts, answer, response, message`. Collects strings found at these keys into `out`.

**Deduplication**: Consecutive identical fragments are dropped (handles cumulative snapshots where each frame contains the full text so far).

**Delta vs. cumulative detection**: After accumulation, compares `"".join(chunks)` (concatenated deltas) vs. `max(chunks, key=len)` (longest cumulative). Returns whichever is longer. This handles both streaming deltas (join wins) and cumulative snapshots (max wins).

---

### `src/providers/{anthropic_api, openai_api}.py` — Streaming API Providers

Both follow the same **stream-then-fallback** pattern:

```
if on_chunk:
    try:
        POST /... with stream=True
        parse SSE frames → on_chunk(delta) + accumulate full_text
        if full_text.strip(): return full_text   ← streaming succeeded
    except Exception:
        pass   ← fall through to non-streaming

POST /... without stream=True
parse response JSON → text
if not text: raise GenerationTimeout
return text
```

This means streaming is attempted when the caller provides `on_chunk`, but a non-streaming fallback is always available. If the streaming request itself fails (network error, etc.), the provider retries with a non-streaming request.

**Anthropic-specific**: Uses `anthropic-version: 2023-06-01` header. Streaming events are `type="content_block_delta"` with `delta.text`. Non-streaming: extracts from `content[].text` (type=text blocks only). Max tokens: 4096.

**OpenAI-specific**: Bearer token auth. Streaming: `choices[0].delta.content`. Non-streaming: `choices[0].message.content`. Terminator: `[DONE]`.

---

### `src/providers/gemini_api.py` — Model Waterfall + Quota Retry

**Unique behaviour**: Gemini has a 4-model fallback waterfall plus per-model 429 retry:

```
for m in [configured_model, gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.0-flash, gemini-1.5-flash]:
    for attempt in [0, 1]:
        try POST ...
        on 404: break (try next model)
        on 429:
            if attempt == 0: sleep(2s); continue (one retry on same model)
            else: break (try next model)
        on other HTTPError: raise immediately
```

If all models + attempts exhausted: raises the last `HTTPError`. No `on_chunk` streaming support (returns full response only).

---

### `src/providers/qwen_api.py` — Simple Non-Streaming API

Single POST, no streaming, no fallback waterfall. API key: `DASHSCOPE_API_KEY` or `QWEN_API_KEY` (either accepted). Endpoint: DashScope OpenAI-compatible mode.

---

### `frontend/app/page.tsx` — Main Console

**Purpose**: Single-page relay console that submits jobs, streams progress, and renders results.

**State machine** (React state):
```typescript
phase: "idle" | "running" | "done" | "error"
```

Transitions:
```
idle ──submit()──► running ──pipeline_finished──► done
                          ──fatal──► error
                          ──abort (cancel button)──► idle
```

**Per-step state**: Each step key has a `StepCard` with status `"pending" | "running" | "done" | "error"` and accumulated content string. `step_chunk` events append `delta` to the card's content for live streaming display.

**Event processing**:
- `step_started` → mark step card as running
- `step_chunk` → append `delta` to step card content
- `step_succeeded` → mark step card done, set final content
- `step_failed` → mark step card error
- `step_queued` → show queue position in step card
- `runnerup_chunk` / `runnerup_succeeded` → shown in a secondary "racing lane" in the step card
- `step_transitioning` → UI hint to pre-highlight the next step card
- `pipeline_finished` → transition phase to `done`
- `fatal` → transition phase to `error`

**Deduplication**: Events are tracked by `seq`. Both SSE and polling paths call the same `deliver(ev)` function that checks `ev.seq > lastSeq` before processing.

**"Continue" (再来一轮) flow**:
- Takes the merged output of the current job (from `context.json`).
- Submits it as `user_question` to a new job with `pipeline = "pipelines/continue.yaml"`.
- `continue.yaml` starts from `review` using the `recap` prompt, which restates the input as a clean working draft.

**Mock mode** (`NEXT_PUBLIC_USE_MOCK=1`): All API calls route to `mock.ts`. The mock produces a scripted 7-event sequence with 700ms delays: `step_started(generate)` → `step_succeeded(generate)` → `step_started(review)` → `step_succeeded(review)` → `step_started(deep_analyze)` → `step_succeeded(deep_analyze)` → `pipeline_finished`. Jobs stored in a Map; status transitions to `succeeded`.

**Export**: `downloadExport(jobId, mode)` in `api.ts` signs the request, fetches the blob, creates an object URL, programmatically clicks a temporary `<a>` element, then revokes the URL. This triggers the browser's native file download dialog without opening a new tab.

---

### `frontend/lib/api.ts` — API Client

**HMAC signing**: Uses Web Crypto API (`crypto.subtle`). `sha256Hex` uses `crypto.subtle.digest("SHA-256")`. `hmacHex` uses `importKey("raw")` + `sign("HMAC")`. Canonical string format matches `src/auth.py` exactly.

**`streamEvents()` reconnect logic**:
```
loop:
  try:
    fetch SSE with Last-Event-ID: lastSeq
    readSse(body, callback):
      deliver events as they arrive (dedup by seq)
      return true if saw "event: done"
    if completed: return  ← clean finish
    // clean stream end without done → reconnect from lastSeq
    attempts = 0
  catch:
    attempts++
    if attempts >= 5:
      pollUntilDone(...)  ← switch to polling
      return
    await sleep(min(1000 * attempts, 5000))
```

**`pollUntilDone()`**: Calls `getJob()` every 2 seconds, delivers all events (deduped by `deliver`), stops when status is terminal.

**`/providers` fetch**: No auth headers (the endpoint requires none). Falls back to empty array on any error.

---

### `frontend/lib/sse.ts` — SSE Frame Parser

**Why not `EventSource`**: The native `EventSource` browser API cannot send custom headers. HMAC signing requires `X-Api-Key`, `X-Timestamp`, `X-Signature`. The frontend uses `fetch` + `ReadableStream` instead and parses SSE frames manually.

**Frame parsing**: Accumulates bytes in a string buffer. Splits on `"\n\n"` (SSE frame separator). Parses `id:`, `event:`, `data:` prefixes per line. Comment lines (`:`) are ignored (keepalive). Multi-line `data:` fields are joined with `"\n"`. Returns `true` if any frame had `event: done`.

---

### `frontend/lib/markdown.ts` — XSS-Safe Markdown Renderer

**Security design**: Escapes **all** HTML first (`escapeHtml` converts `&`, `<`, `>`, `"` to entities), then re-introduces a restricted safe subset: headings, unordered lists, fenced/inline code, bold, italic, safe links (HTTPS only, `rel="noreferrer noopener"`). No raw HTML passthrough. No tables, footnotes, or complex nesting.

**Why custom**: A full-featured Markdown library + plugin ecosystem has a larger XSS attack surface. Model outputs are untrusted content rendered in the browser.

---

## 5. Request Lifecycle

A complete lifecycle from user click to SSE close:

```
1. User types question, selects providers, clicks Submit
   [page.tsx → api.ts.createJob()]

2. api.ts signs the request:
   body = JSON.stringify({user_question, pipeline, selected_providers?})
   canonical = "POST\n/jobs\n{timestamp}\n{sha256(body)}"
   headers = {X-Api-Key, X-Timestamp, X-Signature, Content-Type}

3. POST /jobs → main.py.create_job()
   a. authenticate(): verify_request(keystore, ...) → api_key or 401
   b. rate_limiter.allow(api_key) → 429 if exceeded
   c. Parse body; validate user_question non-empty → 400 if missing
   d. If selected_providers provided: generate dynamic pipeline YAML
   e. validate_pipeline_file(pipeline, providers_path, prompts_dir) → 400 if invalid
   f. jobs_guard lock: check active["n"] < max_concurrent → 429 if exceeded
   g. active["n"] += 1; create job dict in jobs registry
   h. executor.submit(run_worker, job_id, pipeline, user_question)
   i. Return {"job_id": uuid4_string}  ← HTTP 200

4. page.tsx opens GET /jobs/{id}/events  (SSE)

5. run_worker() in background thread:
   a. os.makedirs(session_dir)
   b. make_emit(job) → emit closure (writes events.jsonl + job["events"])
   c. threading.Timer(job_timeout, _watchdog).start()
   d. run_pipeline(pipeline_path, user_question, session_dir, emit,
                   builder=PromptBuilder(prompts_dir),
                   manager=ProviderManager(providers_path),
                   job_id=job_id,
                   is_cancelled=lambda: job.get("cancelled", False))

6. run_pipeline() iterates steps:
   For each step in pipeline YAML:
   a. is_cancelled() check → fatal + break if cancelled
   b. builder.build(prompt_name, context) → rendered prompt string
   c. Write NN_{key}_prompt.md
   d. Apply diversity rule (filter last_winner from multi-provider list)
   e. emit(step_started)  [seq assigned by push()]
   f. _run_step_with_retry(...):
      - pool.acquire_account() → blocks/queues, emits step_queued if waiting
      - _lock_for(profile).__enter__()
      - manager.run(provider, prompt, on_chunk=handle_chunk)
        → provider module loaded by importlib
        → Web providers: _browser.run_web() with Playwright
        → API providers: HTTP request, optional streaming
        → on_chunk callback → push(step_chunk) with delta
      - On SessionExpiredError: pool.mark_expired(), switch to site-wide target,
        retry (doubled budget)
      - On transient: pool.release(), exponential backoff, retry
   g. metrics.observe(step_duration_seconds, ...)
   h. Write NN_{key}_response.md / NN_{key}_error.json
   i. emit(step_succeeded or step_failed)
   j. If not last step: emit(step_transitioning)
   k. On failure: break (pipeline interrupted — "出错即中断")
   Write context.json
   emit(pipeline_finished)

7. run_worker() finally block:
   watchdog_timer.cancel()
   if not job["_released"]: active["n"] -= 1; job["_released"] = True
   job["status"] = "succeeded" or "failed"

8. SSE generator (running in asyncio event loop on Uvicorn's main thread):
   Polls job["events"] every 0.25s
   → yields new events with seq > last_sent
   → when status terminal AND all events sent: yield "event: done\n..." → return

9. page.tsx receives "event: done" → phase = "done"
   Step cards show final outputs. Export buttons enabled.
```

---

## 6. Data Flow

```
User Input (question string)
        │
        ▼
Validation (submit-time)
  validate_pipeline_file():
  • provider names exist in providers.yaml
  • prompt files exist in prompts/
  • step keys unique
  • no forward {{references}}
        │
        ▼
Prompt Building (per step)
  PromptBuilder.build(prompt_name, context):
  • reads prompts/{name}.md
  • single-pass PLACEHOLDER_RE substitution
  • {{user_question}} → user input
  • {{step_key}} → prior step output (if produced)
  • unknown placeholders preserved verbatim
        │
        ▼
Provider Call
  Web: Playwright persistent context → page → network intercept → _extract.py
  API: HTTP POST with streaming/non-streaming fallback
  Returns: plain-text Markdown string (non-empty, or transient error)
        │
        ▼
Persistence (A writes, C's emit writes events.jsonl)
  data/sessions/{job_id}/
    NN_{key}_prompt.md      ← before provider call
    NN_{key}_response.md    ← on success
    NN_{key}_error.json     ← on failure
    context.json            ← after all steps
    events.jsonl            ← per event (written by emit closure)
        │
        ▼
Event Streaming (C)
  Each event: {seq, type, key, provider, label, model, content/delta/error}
  Appended to job["events"] in-memory + events.jsonl on disk
  SSE generator polls in-memory list → client reads real-time
  Last-Event-ID replay on reconnect (no loss, no duplication)
        │
        ▼
Export (C reads A's files)
  merged: Markdown with question + all step responses + errors
  steps:  ZIP of per-step .md and .json
  json:   context.json verbatim
```

---

## 7. Startup Process

### Local (`uvicorn src.main:app`)

```
1. Python imports src/main.py
2. Module-level: app = create_app()
   a. configure_logging(LOG_LEVEL)
      → installs JsonFormatter on root logger (idempotent, guarded by _configured flag)
   b. load_keystore_from_env()
      → parses RHCLOUD_API_KEYS or RHCLOUD_API_KEY + RHCLOUD_API_SECRET
      → on ValueError: log warning, use empty keystore (fail-closed — all authed routes → 401)
   c. Resolve paths from env: PROVIDERS_PATH, PROMPTS_DIR, SESSIONS_ROOT, FRONTEND_ORIGIN
   d. Resolve limits: MAX_CONCURRENT_JOBS (default 2), RATE_LIMIT_PER_MIN (default 30),
      JOB_TIMEOUT_SECONDS (default 900), ALERT_CONSECUTIVE_THRESHOLD (default 3)
   e. FastAPI app created
   f. CORSMiddleware added (exact FRONTEND_ORIGIN, never "*")
   g. ThreadPoolExecutor(max_workers=max(1, max_concurrent)) created
   h. RateLimiter(rate_limit_per_min, 60.0) created
   i. AlertTracker(alert_threshold) created
   j. _accepts_runtime_kwargs(run_pipeline_fn) called once (introspects real runtime signature)
   k. Route handlers registered (closures close over all the above)
3. Uvicorn starts serving on 0.0.0.0:8000 (or $PORT)
```

**No eager validation of providers.yaml or pipeline files at startup.** The `validate_all()` function exists but is not called by `main.py`. Validation happens on demand at `POST /jobs`.

**`ProviderManager` and `AccountPoolManager` are not instantiated at startup.** They are created inside `run_worker()` (via `ProviderManager(providers_path)` in the `run_pipeline_fn` call), and the `AccountPoolManager` singleton is initialised inside `ProviderManager.__init__`.

### Docker (`CMD ["sh", "-c", "xvfb-run -a uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]`)

Same as above, but:
1. `xvfb-run -a` starts a virtual X display (`:99` or next available) before Uvicorn.
2. This enables headed Chromium (`RHCLOUD_HEADLESS=0`) without a physical display.
3. Playwright's Chromium was pre-installed at image build time (`playwright install chromium`).

### Playwright lazy initialization

Playwright is not started at process startup. The first call to `_browser.get_context(profile)` on any worker thread triggers:
1. `_ensure_playwright()` → `sync_playwright().start()` on that thread
2. `pw.chromium.launch_persistent_context(...)` → opens browser

This means the first job that uses a web provider has extra startup latency.

---

## 8. Shutdown Process

### Planned shutdown (SIGTERM, Ctrl+C, Railway deploy restart)

Python receives SIGTERM/SIGINT → Uvicorn catches it → begins graceful shutdown:
1. Stops accepting new connections.
2. Waits for active requests to finish (Uvicorn's graceful shutdown window).
3. **`ThreadPoolExecutor` is not explicitly shut down in `create_app()`** — it uses the default Python atexit handler to wait for running threads. *[This means in-progress jobs may or may not complete depending on shutdown timeout.]*
4. `threading.Timer` watchdog threads are daemon threads — they die with the process.
5. **Playwright browser contexts are not explicitly closed** — the `_browser.shutdown()` function exists but is not called on process exit. Chromium child processes are killed by the OS when the parent Python process exits.

### Manual cleanup (`_browser.shutdown()`)

Called explicitly in tests (`tearDownModule`, etc.):
1. Acquires `_POOL_LOCK`; copies and clears `_CONTEXTS` and `_CTX_LOCKS`.
2. Closes all contexts (`ctx.close()`).
3. Calls `_THREAD_LOCAL.pw.stop()` on the current thread's Playwright instance.

**Note**: `shutdown()` only stops Playwright on the calling thread. Worker threads have their own thread-local instances that are not cleaned up by calling `shutdown()` from the main thread.

### In-memory state loss on restart

When the process restarts, all in-memory state is lost:
- `jobs` dict (job registry) — all job status and events gone
- `active["n"]` counter reset to 0
- `AccountPoolManager` singleton reset (accounts reload from providers.yaml)
- `_profile_locks` dict reset

**Persistent state that survives restart**:
- `data/sessions/{job_id}/` directories (artefacts remain on Railway Volume)
- `data/profiles/*/` browser profiles (login sessions remain on Railway Volume)

---

## 9. Configuration System

### Loading order and precedence

```
providers.yaml defaults
    └──► providers.yaml per-instance values  (override defaults)
             └──► environment variables       (runtime secrets + overrides)
                      └──► create_app() kwargs (test injection, highest priority)
```

### `providers.yaml` schema

```yaml
defaults:
  timeout_ms: 120000      # ms; applied to any provider without a per-instance override
  retries: 2              # transient-error retry count
  retry_backoff_ms: 3000  # backoff base (grows as base × 2^attempt)

providers:
  {instance_name}:
    site: {site_name}      # REQUIRED; maps to src/providers/{site_name}.py
    profile: {path or ""}  # REQUIRED; "" for API providers
    model: {model_id}      # optional; overrides provider default or GEMINI_MODEL etc.
    label: {display_name}  # optional; shown in SSE events + /providers response
    base_url: {url}        # optional; overrides provider default endpoint
    api_key_env: {VAR}     # optional; env-var name holding the API key for this instance
    timeout_ms: {int}      # optional; per-instance override
    retries: {int}         # optional; per-instance override
    retry_backoff_ms: {int} # optional; per-instance override
```

### Pipeline YAML schema

```yaml
name: {human-readable name}
steps:
  - key: {step_identifier}    # REQUIRED; unique within pipeline; used as {{key}} in later prompts
    provider: {instance_name} # REQUIRED if providers not set; must exist in providers.yaml
    providers:                 # Alternative to provider: for multi-model race mode
      - {instance_name_1}
      - {instance_name_2}
    prompt: {prompt_name}     # REQUIRED; maps to prompts/{prompt_name}.md
```

### Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `RHCLOUD_API_KEYS` | (required) | `key1:secret1,key2:secret2` format; wins over single-key form |
| `RHCLOUD_API_KEY` + `RHCLOUD_API_SECRET` | (alternative) | Single key/secret pair |
| `FRONTEND_ORIGIN` | `https://your-frontend.pages.dev` | Exact CORS allowed origin |
| `MAX_CONCURRENT_JOBS` | `2` | Default is **2** (README); `main.py` default is **5** — env wins |
| `RATE_LIMIT_PER_MIN` | `30` | Per API-key job creation rate |
| `JOB_TIMEOUT_SECONDS` | `900` | Global watchdog; kills hung jobs |
| `ALERT_CONSECUTIVE_THRESHOLD` | `3` | Same-class failure count before ERROR log |
| `PROVIDERS_PATH` | `config/providers.yaml` | Override for tests |
| `PROMPTS_DIR` | `prompts` | Override for tests |
| `SESSIONS_ROOT` | `data/sessions` | Override for tests |
| `SESSION_RETENTION_DAYS` | `14` | Cleanup script retention window |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `RHCLOUD_HEADLESS` | `1` | `0` = headed (requires Xvfb); `1` = headless |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | (required for Gemini) | Either accepted |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model override |
| `OPENAI_API_KEY` | (required for OpenAI) | Or per-instance `api_key_env` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Or per-instance `base_url` |
| `OPENAI_MODEL` | `gpt-4o-mini` | Or per-instance `model` |
| `ANTHROPIC_API_KEY` | (required for Anthropic) | Or per-instance `api_key_env` |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | Or per-instance `base_url` |
| `ANTHROPIC_MODEL` | `claude-3-5-sonnet-20241022` | Or per-instance `model` |
| `DASHSCOPE_API_KEY` / `QWEN_API_KEY` | (required for Qwen) | Either accepted |
| `QWEN_MODEL` | `qwen-plus` | Or per-instance `model` |

**Note on `MAX_CONCURRENT_JOBS` default**: `.env.example` says default is `2`, the README table says `2`, but `main.py` line `max_concurrent = max_concurrent or _env_int("MAX_CONCURRENT_JOBS", 5)` defaults to `5` in code. The env var wins in all deployed cases; this only matters if running without setting the variable.

### Frontend environment variables (`frontend/.env.local`)

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE` | Backend URL (e.g. `https://your-app.railway.app`) |
| `NEXT_PUBLIC_API_KEY` | HMAC identity (must match a key in `RHCLOUD_API_KEYS`) |
| `NEXT_PUBLIC_API_SECRET` | HMAC secret (compiled into JS bundle — public for controlled deployments) |
| `NEXT_PUBLIC_USE_MOCK` | `1` = use in-browser mock (no backend connection) |

---

## 10. Database

RHCLOUD V1 uses **no SQL database**. The file system is the database.

### Per-job session directory

```
data/sessions/{job_id}/      ← job_id is a UUID4 string
├── events.jsonl             Written by C's emit closure (append-only, one JSON per line)
├── context.json             Written by A's runtime after all steps complete
├── 01_generate_prompt.md    Written by A before calling the generate provider
├── 01_generate_response.md  Written by A on generate step success
├── 02_review_prompt.md      Written by A before calling the review provider
├── 02_review_response.md    Written by A on review step success
├── 03_deep_analyze_prompt.md
├── 03_deep_analyze_error.json  Written by A on deep_analyze step failure
│                               Content: {"type": "transient", "message": "..."}
├── ...
└── pipeline.yaml            Only present for dynamic (selected_providers) jobs
```

**Naming convention**: `{NN}_{step_key}_{suffix}.{ext}` where `NN = str(step_index + 1).zfill(2)`.

**Writer ownership** (from `docs/contracts.md`):
- A (runtime) writes: `*_prompt.md`, `*_response.md`, `*_error.json`, `context.json`
- C (emit closure) writes: `events.jsonl`
- Neither writes the other's files.

### Browser profile directories

```
data/profiles/
├── chatgpt_acc1/     Playwright persistent context (cookies, localStorage, IndexedDB)
├── claude_acc1/
├── deepseek_acc1/
└── ...
```

These must be created via the seeding workflow and uploaded to the Railway Volume.

### Retention

`src/cleanup.py` deletes directories older than `SESSION_RETENTION_DAYS` days (measured by directory mtime). Not run automatically — must be scheduled externally or run manually.

---

## 11. Scheduled Tasks

There is **no built-in scheduler**. All periodic tasks require external scheduling.

### Session cleanup

```bash
python -m src.cleanup --root data/sessions --days 14
```

Intended to run as a Railway cron job. Delete sessions older than retention window.

### Session re-seeding

Not automated. When `SessionExpiredError` appears in logs for a web provider:
1. Local: `python scripts/seed_session.py --site {site} --profile data/profiles/{account}`
2. A headed browser opens; log in manually; press Enter to save session.
3. Upload the profile directory to Railway Volume at the same path.
4. Verify: submit a job using that provider; check logs for no `session_expired` events.

For Qwen specifically: `python scripts/login_qwen.py` — opens `chat.qwen.ai`, waits for login, packages profile as `qwen_acc1.zip` for easy upload.

---

## 12. API Overview

Full reference: [`docs/api.md`](api.md)

### Authentication

Every route except `/health` and `/providers` requires HMAC auth headers:
```
X-Api-Key:   <key>
X-Timestamp: <unix seconds>
X-Signature: hex(HMAC-SHA256(secret, METHOD + "\n" + PATH + "\n" + ts + "\n" + sha256(body)))
```
PATH excludes query string. Body is raw bytes (empty for GET → `sha256("")`). Timestamp must be within ±300 seconds of server time.

### Endpoints

**`POST /jobs`**
```json
// Request
{"user_question": "...", "pipeline": "pipelines/round1.yaml", "selected_providers": ["gemini_api_1"]}
// Response 200
{"job_id": "uuid"}
// Response 400: invalid pipeline / missing question
// Response 401: auth failure
// Response 429: rate limit or concurrency limit
```

**`GET /jobs/{id}`** — polling fallback
```json
{"job_id": "uuid", "status": "running|succeeded|failed", "events": [...]}
```

**`GET /jobs/{id}/events`** — SSE stream
```
id: 1
data: {"seq":1,"type":"step_started","key":"generate","provider":"chatgpt_web_1","label":"ChatGPT Web","model":null}

id: 2
data: {"seq":2,"type":"step_chunk","key":"generate","provider":"chatgpt_web_1","delta":"The answer..."}

event: done
data: {}
```

**`POST /jobs/{id}/cancel`**
- Sets `job["cancelled"] = True`
- Sets `job["status"] = "failed"`
- Emits `fatal` event with `error.type = "cancelled"`
- The running `run_pipeline()` will check `is_cancelled()` at the next step boundary

**`GET /jobs/{id}/export?mode=merged|steps|json`**

**`GET /health`** (no auth)
```json
{"status": "ok", "active_jobs": 1, "metrics": {"counters": {...}, "gauges": {...}, "histograms": {...}}}
```

**`GET /providers`** (no auth)
```json
[{"id": "gemini_api_1", "site": "gemini_api", "label": "Gemini 2.5 Flash",
  "model": "gemini-2.5-flash", "api": true, "status": "idle", "fail_count": 0}]
```
`api: true` means `profile == ""` (API provider, no browser). Used by `ModelProbe.tsx` to show live pool status.

### Event type reference

| Type | Fields | Meaning |
|------|--------|---------|
| `step_started` | seq, key, provider, label, model | Step execution begins |
| `step_queued` | seq, key, position, provider, label, model | Waiting for idle account |
| `step_chunk` | seq, key, provider, delta | Streaming token delta |
| `step_succeeded` | seq, key, provider, label, model, content | Step completed; `content` = full response |
| `step_failed` | seq, key, provider, label, model, error{type,message} | Step exhausted retries |
| `step_transitioning` | seq, key | Hint: `key` is the next step about to start |
| `runnerup_chunk` | seq, key, provider, delta | Token from non-winning race candidate |
| `runnerup_succeeded` | seq, key, provider, label, model, content | Non-winner completed |
| `pipeline_finished` | seq | All steps done (even if some failed) |
| `fatal` | seq, error{type,message} | Worker-level error (timeout, internal, cancelled) |

### HTTP error codes

| Code | Trigger |
|------|---------|
| 400 | Missing `user_question`; non-JSON body; invalid pipeline; bad export `mode` |
| 401 | Missing/invalid signature; unknown key; stale timestamp |
| 404 | Unknown `job_id`; no session artefacts to export |
| 429 | Rate limit exceeded; max concurrent jobs reached |
| 500 | Uncaught error in request handler |

**Important**: Pipeline step failures are NOT HTTP errors — they arrive as `step_failed` / `fatal` events.

---

## 13. Core Business Logic

### The relay pattern

The system chains multiple language models where each model refines the previous model's output. The prompts are written in Chinese and have an anti-padding instruction ("严禁客套拍马屁" — no compliments, no filler). The relay pipeline for the default `round1.yaml`:

```
User question
      ↓
[ChatGPT web] generate → structured initial answer (expert role)
      ↓ (output piped into next prompt as {{generate}})
[Gemini API] review → critical review finding flaws
      ↓ ({{generate}} + {{review}} available)
[Claude web] deep_analyze → deep analysis
      ↓ ({{generate}} + {{review}} + {{deep_analyze}} available)
[ChatGPT web] improve → improved answer incorporating prior reviews
      ↓ (all prior outputs available)
[Gemini API] summary → concise final synthesis
```

### Provider selection logic

1. `providers.yaml` maps instance names to site modules and configuration.
2. Pipeline YAML refers to instance names (`chatgpt_web_1`, not `chatgpt`).
3. **Adjacent-step diversity**: When a step has multiple candidate providers (`providers:` list) and the previous step already has a winner (`last_winner_provider`), the winner is filtered from the candidate list. At least one alternative must remain; if the filter would leave an empty list, the full list is used.
4. **Race-to-first**: In multi-provider steps, all candidates run concurrently in a `ThreadPoolExecutor`. The first candidate to call `on_chunk` wins and locks the race state. Subsequent chunks from the winner go as `step_chunk`; chunks from others go as `runnerup_chunk`.

### Session management for web providers

Web providers require a logged-in browser session stored as a Playwright persistent context directory. Sessions expire when:
- The target website invalidates the session (typically days to weeks of inactivity).
- The website detects automation (immediately or after repeated use).

On detection (`SessionExpiredError`):
1. Runtime marks account `EXPIRED` in `AccountPoolManager`.
2. Runtime switches `current_target` to the site name (not the provider name).
3. `pool.acquire_account(site)` is called — finds any other idle account under the same site.
4. If no healthy alternative: step fails with `error.type = "session_expired"`.
5. `AlertTracker` fires if threshold reached.

### Retry classification

Two error classes with fundamentally different responses:
- **Fatal** (`SessionExpiredError`): The session is invalid. Retrying the same account is futile. Response: failover to another account on the same site (budget = `retries × 2`).
- **Transient** (everything else, including `GenerationTimeout`, `RuntimeError` for empty content, `requests.HTTPError` for rate limits, network errors): Temporary failure. Response: exponential backoff retry on the same account (budget = `retries`).

Backoff formula: `retry_backoff_ms / 1000 × 2^(attempt - 1)` seconds. With defaults (3000ms, 2 retries): 3s, 6s.

### Prompt template substitution

The template language is minimal and deliberate:
- Only `{{bare_identifier}}` with no spaces/dots/operators is a variable.
- `{{ spaced }}`, `{{a.b}}`, `{{a | b}}` pass through verbatim (no false positives on Vue/Jinja/Handlebars-style syntax in model outputs).
- Single regex pass prevents any injected value from being re-scanned.
- Unknown variables preserved verbatim (visible in the persisted `_prompt.md` file — an explicit debugging signal, not a silent failure).
- Forward references detected at validation time (before job execution).

### "Continue" (再来一轮) flow

The frontend takes the current job's merged output (all step responses concatenated), submits it as `user_question` for a new job with `pipeline = "pipelines/continue.yaml"`. The `continue.yaml` pipeline starts with `review` using the `recap` prompt, which faithfully restates the input as a clean working draft without adding new substance. The downstream steps (`deep_analyze`, `improve`, `summary`) can then refine further.

---

## 14. Thread Safety and Concurrency Model

### Thread layout

```
Uvicorn main thread (asyncio event loop)
  ├── SSE generators (async coroutines, poll job["events"] every 0.25s)
  ├── Route handlers (async coroutines)
  └── ...

ThreadPoolExecutor (max_workers = MAX_CONCURRENT_JOBS)
  ├── Worker thread 1 (run_worker → run_pipeline → provider calls)
  ├── Worker thread 2
  └── ...

Per-step race ThreadPoolExecutor (created inside run_pipeline, one per race step)
  ├── Race candidate thread 1 (_run_step_with_retry → provider)
  ├── Race candidate thread 2
  └── ...

Watchdog daemon threads (threading.Timer, one per active job)
  └── _watchdog() fires after JOB_TIMEOUT_SECONDS
```

### Shared mutable state and guards

| State | Guard | Notes |
|-------|-------|-------|
| `jobs` dict + `active["n"]` | `jobs_guard: threading.Lock` | `active["n"]` changes always under this lock |
| `job["events"]` list | None (append-only from single worker thread) | SSE generator reads; only the worker appends |
| `events.jsonl` file | None (single worker writes; reads only after job done) | Single writer per file |
| `_profile_locks` dict | `_locks_guard: threading.Lock` | Lock creation is guarded; usage is not |
| `AccountPoolManager._slots` | `self._lock: threading.Condition` | All mutations under this condition |
| `Metrics._counters/_gauges/_hist` | `self._lock: threading.Lock` | All mutations and reads under this lock |
| `AlertTracker._counts` | `self._lock: threading.Lock` | Snapshot and mutations under this lock |
| `RateLimiter._state` | `self._lock: threading.Lock` | Per-key state under lock |
| `_browser._CONTEXTS` / `_CTX_LOCKS` | `_POOL_LOCK: threading.Lock` | Creation/cleanup of contexts |
| `_browser._THREAD_LOCAL` | thread-local (no lock needed) | Per-thread; no shared access |
| `logging_conf._configured` | `_configure_guard: threading.Lock` | Idempotent init guard |
| `AccountPoolManager._instance` | `_instance_lock: threading.Lock` | Singleton creation guard |

### Serialization invariants

1. **Profile-level serialization**: At most one provider call with a given profile path runs at any time (enforced by `_lock_for(profile)` in `_run_step_with_retry`). This is needed because Playwright does not support concurrent operations on the same browser context from multiple threads.

2. **Single writer for events.jsonl**: The `emit` closure in `main.py` has an internal lock (`threading.Lock`) and is the only writer to `events.jsonl`. The runtime emits via this closure; no other code writes to the file.

3. **Account pool consistency**: All transitions between account states are atomic under `self._lock`. A `notify_all()` is called after every state change that could allow a waiter to proceed.

---

## 15. Exception Handling

### Exception propagation paths

```
Provider module raises exception
        │
        ▼
_run_step_with_retry() catches it:
  SessionExpiredError → pool.mark_expired(), switch target, retry (budget: retries×2)
  Any other Exception → pool.release_account(), exponential backoff, retry (budget: retries)
  If budget exhausted → return _fail(...)
        │
        ▼
run_pipeline() checks result["status"]:
  "failed" → write NN_{key}_error.json, emit step_failed, break
  Never re-raises; always returns context dict
        │
        ▼
run_worker() around run_pipeline():
  except Exception as e:  # BLE001
    emit({"type": "fatal", "error": {"type": "internal", "message": str(e)}})
    job["status"] = "failed"
    metrics.inc(M_JOBS_FAILED_TOTAL)
  finally:
    release slot

        ▼
HTTPException (from route handlers)
  FastAPI catches → converts to JSON error response

AuthError (from auth.py)
  Caught in authenticate() → re-raised as HTTPException(status_code=e.status)

ValidationError (from validation.py)
  Caught in create_job() → HTTPException(400, detail={"errors": e.errors})

ExportError (from export.py)
  Caught in export_job() → HTTPException(404, detail=str(e))
```

### Swallowed exceptions

Several exception-swallowing patterns exist intentionally:

- `on_response` callback in `_browser.py`: Any exception silently continues (one bad frame must not crash the run).
- `_wait_generation_done` DOM extraction: `except Exception: pass`.
- Race candidate futures in `run_pipeline`: `except Exception: pass` (failed candidate just doesn't contribute a result).
- Watchdog timer: `try/except` around context/page close calls.
- `fetchProviders` in `api.ts`: returns `[]` on any error.
- Streaming fallback in API providers: `except Exception: pass`, falls through to non-streaming.

---

## 16. Logging

### Log format

Every log line is a JSON object on stdout:
```json
{"ts": "2026-06-29T10:00:00.000Z", "level": "INFO", "logger": "rhcloud.runtime",
 "event": "step_succeeded", "job_id": "uuid", "step_key": "generate",
 "provider": "chatgpt_web_1", "site": "chatgpt", "attempt": 1, "duration_ms": 4523}
```

Fields present only when non-None: `event, job_id, step_key, provider, site, attempt, duration_ms, error_type`.

**Bodies are never logged**: Prompt text and model responses live only in `*_prompt.md` and `*_response.md` files. This is an explicit privacy and volume constraint.

### Log event catalogue

| event | level | When |
|-------|-------|------|
| `auth_keystore_unconfigured` | WARNING | Startup: no API credentials found |
| `job_created` | INFO | POST /jobs accepted and submitted |
| `pipeline_started` | INFO | run_pipeline() begins |
| `step_started` | INFO | Step execution begins |
| `step_retry` | WARNING | Transient error; about to retry |
| `step_failed` | ERROR | Step exhausted retries |
| `step_session_expired_auto_failover_attempt` | WARNING | SessionExpiredError; trying failover |
| `step_succeeded` | INFO | Step completed successfully |
| `pipeline_finished` | INFO | All steps done |
| `job_watchdog_timeout` | ERROR | Job killed by watchdog timer |
| `account_marked_expired` | INFO | Account moved to EXPIRED |
| `account_marked_cooldown` | INFO | Account moved to COOLDOWN |
| `session_deleted` | INFO | Cleanup deleted a session directory |
| `session_cleanup_candidate` | INFO | Cleanup dry-run found a candidate |
| `session_cleanup_done` | INFO | Cleanup sweep finished |
| `session_delete_failed` | ERROR | Could not delete a session directory |
| `alert_consecutive_failures` | ERROR | Consecutive failure threshold reached |

### Logger namespaces

| Logger name | Module |
|-------------|--------|
| `rhcloud.api` | `src/main.py` |
| `rhcloud.runtime` | `src/runtime.py` |
| `rhcloud.account_pool` | `src/account_pool.py` |
| `rhcloud.alerts` | `src/alerts.py` |
| `rhcloud.cleanup` | `src/cleanup.py` |

---

## 17. Security

### Authentication

- **Mechanism**: HMAC-SHA256 request signing with timestamp replay protection.
- **Keystore**: Multi-key (`RHCLOUD_API_KEYS`) allows rotating secrets without downtime.
- **Fail-closed**: If `RHCLOUD_API_KEYS` is not configured at startup, the keystore is empty and every authed route returns 401 immediately.
- **Constant-time comparison**: `hmac.compare_digest()` prevents timing attacks on signature verification.
- **Replay window**: ±300 seconds. Requests outside this window are rejected even with a valid signature.

### Authorization

No per-resource authorization. Any client with a valid API key/secret pair can:
- Create jobs with any pipeline and any configured providers
- Read any job's status and events
- Export any job's artefacts
- Cancel any job

This is appropriate for the single-operator deployment model.

### CORS

`CORSMiddleware` allows only `FRONTEND_ORIGIN` (a single exact origin). Never `*`. Allowed headers are explicitly listed: `Content-Type, X-Api-Key, X-Timestamp, X-Signature, Last-Event-ID`.

### Secret management

- API keys/secrets: environment variables only; never in code, YAML, or Git.
- Secrets do not appear in log output (only `api_key` identity is logged, not the secret).
- `data/profiles/` and `data/sessions/` are gitignored.
- `.env` files are gitignored.

### Frontend security concerns

- `NEXT_PUBLIC_API_SECRET` is compiled into the JavaScript bundle and is visible to anyone who can load the page. This is acceptable only for access-controlled deployments.
- For public-facing deployments: a backend signing proxy should hold the secret and sign requests on behalf of browser clients.
- Model output rendering: `markdown.ts` escapes all HTML first, then re-introduces a restricted safe subset. No raw HTML passthrough.

### Prompt injection

Model outputs are used as inputs to later prompt templates via `{{step_key}}` substitution. The single-pass regex ensures injected model output is never re-scanned for `{{...}}` patterns. A model output containing `{{malicious}}` will appear literally in the next step's prompt, which the model will see as text, not as a template variable.

---

## 18. External Integrations

### Google Gemini API

| Aspect | Detail |
|--------|--------|
| Purpose | Primary P0 baseline API provider (stable, always available) |
| Auth | `GEMINI_API_KEY` or `GOOGLE_API_KEY` env var; passed as `?key=` query param |
| Endpoint | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` |
| Default model | `gemini-2.5-flash` |
| Fallback waterfall | `gemini-2.5-flash` → `gemini-2.5-flash-lite` → `gemini-2.0-flash` → `gemini-1.5-flash` |
| 429 handling | One retry per model after 2s sleep; then try next model |
| 404 handling | Model not found — skip to next model immediately |
| Streaming | Not supported by `gemini_api.py` (full response only) |

### Alibaba Qwen (DashScope)

| Aspect | Detail |
|--------|--------|
| Purpose | Official API provider (P1) |
| Auth | `DASHSCOPE_API_KEY` or `QWEN_API_KEY` env var; Bearer token header |
| Endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` (OpenAI-compatible) |
| Default model | `qwen-plus` |
| Streaming | Not supported by `qwen_api.py` (full response only) |

### Anthropic Messages API

| Aspect | Detail |
|--------|--------|
| Auth | `ANTHROPIC_API_KEY` or per-instance `api_key_env`; `x-api-key` header |
| Required header | `anthropic-version: 2023-06-01` |
| Endpoint | `https://api.anthropic.com/v1/messages` (or per-instance `base_url`) |
| Default model | `claude-3-5-sonnet-20241022` |
| Max tokens | 4096 (hardcoded in provider) |
| Streaming | `content_block_delta` SSE events with `delta.text`; falls back to non-streaming |
| Streaming frame type | `type="content_block_delta"`, `delta.text` |

### OpenAI-compatible API

| Aspect | Detail |
|--------|--------|
| Auth | `OPENAI_API_KEY` or per-instance `api_key_env`; Bearer token header |
| Endpoint | `https://api.openai.com/v1/chat/completions` (or per-instance `base_url`) |
| Default model | `gpt-4o-mini` |
| Streaming | `choices[0].delta.content` SSE frames with `[DONE]` terminator; falls back |
| Compatible systems | OpenAI, OneAPI, NewAPI, vLLM, DeepSeek API, any OpenAI-compatible proxy |

### Web automation targets

| Site | URL | Login detection |
|------|-----|-----------------|
| ChatGPT | `https://chatgpt.com/` | `auth.openai.com` redirect; login button |
| Claude | `https://claude.ai/new` | `login` URL match; Google auth button |
| Gemini | `https://gemini.google.com/` | accounts.google.com redirect |
| Qwen | `https://chat.qwen.ai/` | login URL; email/password inputs |
| DeepSeek | `https://chat.deepseek.com/` | login URL; auth controls |
| Kimi | `https://kimi.com/` | login URL; auth controls |
| Z.AI | `https://chat.z.ai/` | login URL; auth controls |

All web providers use `_browser.run_web()` with site-specific selector configurations. Selectors are noted in source as "best-effort, must be verified against live sites."

### Railway

| Aspect | Detail |
|--------|--------|
| Purpose | Backend hosting |
| Config | `railway.toml`: Dockerfile builder, `/health` healthcheck (100s timeout), restart on failure (10 retries) |
| Volume | `/app/data` — persists browser profiles and session artefacts |
| Constraint | **Must run as a single instance** — job registry and SSE event lists are in-memory |

### Cloudflare Pages

| Aspect | Detail |
|--------|--------|
| Purpose | Frontend static hosting |
| Build | `npm run build` → static export in `./out` |
| Domain | `gpt.985.edu.kg` (configured in `CNAME`) |

---

## 19. Test Architecture

### Test suite structure

```
tests/
├── unit/        50 tests — pure Python, no network, no browser, no FastAPI
├── integration/ 6 tests  — real runtime.py + fixture provider package
├── contract/    18 tests — provider contract compliance (fake Playwright + stubbed requests)
├── api/         17 tests — gateway contract (FastAPI TestClient + stub runtime)
└── e2e/         Manual  — real providers, pre-release only (no automated runner)
```

### Fixture provider package (`tests/fixtures/providers/`)

A complete Python package pointed at by `tests/fixtures/providers.yaml`. Used by both integration and API tests.

| Module | Behaviour |
|--------|-----------|
| `ok.py` | Always succeeds. Deterministic Markdown. Records calls in `calls = []`. |
| `slow.py` | 50ms sleep then succeeds. Tracks `_current` concurrency counter and `_max`. `max_concurrency()` returns peak. |
| `expire.py` | Always raises `SessionExpiredError`. Records calls. |
| `flaky.py` | Fails first N times (N from `profile.split(":")[-1]`, default 1), then succeeds. |

All fixtures expose `reset()` to clear module-level state between tests.

### `AccountPoolManager.reset_instance()` in tests

Integration tests call `AccountPoolManager.reset_instance()` in `setUp()`. This clears the singleton so each test gets a fresh pool without leftover expired/busy state from previous tests.

### Fixture pipelines (`tests/fixtures/pipelines/`)

| File | Purpose |
|------|---------|
| `ok2.yaml` | Two steps using `ok_1` provider; used for happy-path tests |
| `slow_a.yaml`, `slow_b.yaml` | One step each using `slow_1`; both share the same profile (for profile-lock test) |
| `expire1.yaml` | One step using `expire_1`; for fatal classification tests |
| `flaky1.yaml` | One step using `flaky_1` (profile `"flaky:1"`); for transient retry tests |
| `bad_provider.yaml` | References nonexistent provider; triggers 400 in gateway tests |
| `bad_forward.yaml`, `bad_prompt.yaml`, `dup_key.yaml`, `unknown_var.yaml` | Various validation failure cases |

### Running tests

```bash
# Unit tests (no dependencies except Python + pyyaml)
pytest tests/unit -q
# or
python -m unittest discover -s tests/unit -p 'test_*.py' -v

# Integration tests (same)
pytest tests/integration -q

# Contract tests (same — uses fake Playwright surface)
pytest tests/contract -q

# API tests (requires fastapi + httpx from requirements-dev.txt)
pytest tests/api -q

# All CI tests
pytest tests/unit tests/integration tests/contract tests/api -q
```

---

## 20. Important Design Decisions

### File system as database

**Decision**: Use files for all persistence; no SQL, no Redis.

**Rationale**: Railway free tier has no managed database. Single-process, single-instance deployment means no need for a shared store. File I/O matches the access pattern (write-once per step, read-once for export). Session directories are self-contained and directly browsable for debugging.

**Trade-off**: No cross-job querying. No atomic transactions. No job listing API. Cleanup is manual. Horizontal scaling requires shared storage.

---

### HMAC instead of JWT or session tokens

**Decision**: Stateless HMAC-SHA256 request signing.

**Rationale**: No token storage on server. Replay-resistant via timestamp. Framework-agnostic (implemented in both Python stdlib and browser Web Crypto). Multi-key keystore supports secret rotation.

**Trade-off**: Client must hold the secret. Public clients expose the secret in their bundle.

---

### Single process, no horizontal scaling

**Decision**: The gateway, job registry, SSE event lists, and browser contexts all live in a single Python process. `railway.toml` documents this constraint explicitly.

**Rationale**: Playwright browser contexts cannot be shared across processes. In-memory job registry avoids inter-process state management. The single-instance constraint is enforced at the platform level.

**Trade-off**: Zero redundancy. Process restart drops all in-progress jobs (artefacts survive on Volume). No failover.

---

### `create_app()` factory pattern

**Decision**: All application state lives inside a factory function closure, not in module-level globals.

**Rationale**: Enables clean test isolation — each test creates its own app with a stub runtime, fresh keystore, temp directories, and injected concurrency limits. No test cleanup needed for global state.

**Trade-off**: Slightly unusual for FastAPI; newcomers might expect a module-level `app = FastAPI()`.

---

### Dynamic provider loading via `importlib`

**Decision**: Provider modules loaded at call time, not import time.

**Rationale**: New providers added by creating one file; zero changes to core. Module cache means import cost is paid only once per site per process. Enables test isolation via `provider_package` injection.

**Trade-off**: Import errors surface at runtime (first job using that provider). Startup validation (`validate_all()`) is not called by default and would not catch import errors anyway.

---

### Network interception as primary, DOM as fallback

**Decision**: Web providers intercept HTTP responses from the AI site's backend API; DOM extraction is only used when interception yields nothing.

**Rationale**: DOM scraping is fragile — sites restructure HTML. Network responses are more stable (structured JSON/SSE). Interception also enables true streaming.

**Trade-off**: Site-specific URL patterns and parsers required. Patterns are inferred and must be verified against live sites.

---

### Thread-local Playwright instances

**Decision**: Each worker thread gets its own `sync_playwright()` instance and browser contexts.

**Rationale**: Playwright's sync API is not thread-safe across threads sharing a single Playwright instance. Thread-local instances eliminate this concern without async overhead.

**Trade-off**: Each thread that uses a web provider has its own Chromium process running. High `MAX_CONCURRENT_JOBS` can exhaust memory.

---

### Frozen three-party contracts

**Decision**: `docs/contracts.md` defines interfaces that A, B, and C cannot change without three-party agreement.

**Rationale**: Enables parallel development — B writes providers, C writes the gateway, A writes the runtime, all simultaneously without blocking on each other.

**Trade-off**: Contract evolution requires coordination overhead. In practice, the contract has remained stable.

---

## 21. Risks

### Technical debt and known issues

| Risk | Severity | Detail |
|------|----------|--------|
| `get_status_summary()` missing `self` | High | `account_pool.py` line 192: defined as instance method without `self`. Calling it via an instance would raise `TypeError`. The `/providers` route attempts to call it, so live pool status is broken. |
| Web provider selectors hardcoded | High | Every `SITE` config in web provider modules contains CSS selectors and URL patterns that were "best-effort inferred." Website UI changes break automation silently. Must be validated against live sites before production use. |
| COOLDOWN state has no timer | Medium | `mark_cooldown()` sets status to COOLDOWN but nothing ever transitions it back to IDLE. COOLDOWN effectively means permanently unavailable until process restart. |
| No startup validation | Medium | `validate_all()` exists but is not called at startup. Misconfigured providers/pipelines only surface at job-submit time. |
| In-memory job registry | Medium | Process restart loses all job status. Running jobs cannot be recovered. |
| `_profile_locks` dict grows unbounded | Low | Per-profile locks are created on first use and never cleaned up. For most deployments (fixed set of profiles), this is fine. Dynamic profiles would leak. |
| Playwright `shutdown()` not called on exit | Low | Browser processes are cleaned up by the OS, but not gracefully. File handles in profile directories might not be flushed. |

### Security concerns

| Risk | Severity | Detail |
|------|----------|--------|
| HMAC secret in frontend bundle | High | `NEXT_PUBLIC_API_SECRET` is visible in the compiled JavaScript. Acceptable only for access-controlled deployments. |
| No per-job authorization | Low | Any authenticated client can read or cancel any job. Appropriate for single-operator but not multi-tenant. |
| Prompt injection from model output | Low | Single-pass substitution prevents template injection. Model output with `{{...}}` is treated as literal text in the next step's prompt. |

### Performance concerns

| Risk | Severity | Detail |
|------|----------|--------|
| Sequential pipeline steps | Design choice | A 5-step pipeline with 120s timeout per step can take up to 10 minutes. |
| One Chromium per worker thread | Medium | Each thread using a web provider spawns a Chromium process. `MAX_CONCURRENT_JOBS=5` could mean 5 Chromium instances running simultaneously. |
| Race steps multiply Chromium load | Medium | A race step with 5 candidates runs 5 concurrent provider calls, each potentially in a different thread (but same profile = serialized by the profile lock). |
| SSE generator polls every 250ms | Low | For many concurrent SSE connections, this polling adds up. Acceptable for current low-concurrency deployment. |

### Hidden assumptions

- Railway Volume is mounted at `/app/data` and is writable.
- `events.jsonl` has exactly one writer per file (single-process guarantee).
- `seq` numbering starts at 1 for each job and is independent between jobs.
- The `_accepted_runtime_kwargs()` introspection works for any callable that has `**kwargs` or a `builder` parameter.
- All prompt templates reference only `user_question` and prior step keys (enforced by validation; runtime assumes validated input).

---

## 22. Future Extension Points

### Adding a new official API provider

1. Create `src/providers/{site}_api.py` with `def run(profile, prompt, *, timeout_ms, **options) -> str`.
2. Add instance to `config/providers.yaml` with `site: {site}_api`.
3. Add API key env var to `.env.example`.
4. Add contract tests in `tests/contract/test_providers.py`.
5. No changes to `manager.py`, `runtime.py`, or `main.py`.

### Adding a new web automation provider

Same as above, plus:
1. Define a `SITE` dict with all required keys (`url`, `response_match`, `input_selector`, `done_selector`, `done_state`, `login_url_match`, `login_selectors`, `assistant_selector`, `parse`, `type_delay_ms`).
2. Call `_browser.run_web(SITE, profile, prompt, timeout_ms, on_chunk)` inside `run()`.
3. Run `scripts/seed_session.py --site {site} --profile data/profiles/{account}` to seed login.
4. Validate selectors against the live site.

### Adding per-instance `api_key_env`

Already supported: set `api_key_env: MY_VAR` in `providers.yaml`; set `MY_VAR=sk-...` in the environment. Enables multiple accounts under different API keys for the same provider type.

### Enabling horizontal scaling

1. Move job registry to Redis (hash `jobs:{job_id}` → status; list `jobs:{job_id}:events` → event log).
2. Move SSE event delivery to Redis Pub/Sub.
3. Move account pool state to Redis.
4. Move Playwright browser contexts to a dedicated browser-pool service with a pool-management API.
5. Remove the `_released` flag (not needed with distributed state).

### Adding job history / listing

Store job metadata (status, timestamps, pipeline, user question) in SQLite or append to a log file. The existing `data/sessions/{job_id}/context.json` files provide per-job data; the missing piece is an index.

### Adding a `/metrics` endpoint

`metrics.snapshot()` already returns a Prometheus-style dict. Exposing it requires only adding a new route to `main.py` with the same structure as `/health`.

### Adding new pipeline steps

Add a new step to an existing YAML or create a new pipeline YAML. Create corresponding prompt templates. No code changes required.

---

## 23. Glossary

| Term | Definition |
|------|-----------|
| **Pipeline** | YAML file defining a sequence of steps; each specifies a provider and prompt template |
| **Step** | One unit: build prompt → call provider → persist output → emit events |
| **Provider** | Python module in `src/providers/` wrapping a specific AI model or website |
| **Site** | The site name used to look up the provider module (e.g. `gemini_api`, `chatgpt`) |
| **Provider instance** | One entry in `providers.yaml` (e.g. `gemini_api_1`) tying a site to a profile + overrides |
| **Profile** | Playwright persistent context directory (browser profile) for web automation providers |
| **Session seeding** | Manually logging into a web provider, persisting the login state, uploading to Railway Volume |
| **Relay** | Sequential chain of model calls where each step builds on prior step output |
| **Race / multi-provider step** | Pipeline step with multiple providers competing concurrently; first to emit output wins |
| **Winner** | The first provider to emit a `step_chunk` in a race step |
| **Runnerup** | Non-winning race candidate; output streamed as `runnerup_chunk`/`runnerup_succeeded` |
| **emit** | Callback injected by gateway into runtime; appends to in-memory event list and writes `events.jsonl` |
| **push** | Runtime's internal wrapper around emit; assigns monotonically increasing `seq` |
| **SSE** | Server-Sent Events — HTTP streaming format for pushing events to the frontend |
| **HMAC** | Hash-based Message Authentication Code; used to authenticate API requests |
| **Canonical string** | The deterministic string that is signed: `METHOD\nPATH\ntimestamp\nsha256(body)` |
| **Account pool** | `AccountPoolManager` tracking busy/idle/expired state for all configured provider accounts |
| **SessionExpiredError** | Fatal provider error: web session invalid; no retry; triggers re-seeding workflow |
| **GenerationTimeout** | Transient provider error: model did not respond within `timeout_ms` |
| **Context** | Running dict `{"user_question": ..., "outputs": {step_key: text}}` passed between steps |
| **context.json** | Final context dict written to session directory after all steps complete |
| **events.jsonl** | Append-only newline-delimited JSON event log written by C's emit closure |
| **Artefact** | Any file written to the session directory for a job |
| **Export** | `GET /jobs/{id}/export` endpoint; three modes: merged, steps, json |
| **Watchdog** | `threading.Timer` that force-cancels a job after `JOB_TIMEOUT_SECONDS` |
| **A / B / C** | Three development teams: A (backend kernel), B (providers), C (gateway + frontend) |
| **Contracts** | Frozen interface definitions in `docs/contracts.md` that all teams must respect |
| **Stub provider** | `src/providers/stub.py` — deterministic fixed response, no model, no network |
| **M1** | "Milestone 1" — the minimum viable pipeline with only the stub provider (no real models) |
| **Adjacent-step diversity rule** | When a multi-provider step follows another step, the previous step's winner is excluded from candidates |
| **出错即中断** | "Error = interrupt" — pipeline stops at the first failed step (documented in contracts.md) |
| **再来一轮** | "Another round" — the "Continue" feature that feeds the prior round's output back as a new question |

---

## 24. Reading Order

For a new engineer joining tomorrow, read in this order:

### Day 1 — Understand the system

1. **[README.md](../README.md)** — Project overview, three-party structure, how to run tests. (~10 min)
2. **[docs/contracts.md](contracts.md)** — The frozen three-party interface. The most important conceptual document. (~15 min)
3. **[config/providers.yaml](../config/providers.yaml)** — What providers are configured and how. (~5 min)
4. **[.env.example](../.env.example)** — All configuration variables. (~5 min)
5. **[pipelines/round1.yaml](../pipelines/round1.yaml)** — What a real pipeline looks like. (~3 min)
6. **[prompts/generate.md](../prompts/generate.md)** + **[prompts/review.md](../prompts/review.md)** — Prompt template examples. (~3 min)

### Day 2 — Understand the backend kernel (A)

7. **[src/providers/_errors.py](../src/providers/_errors.py)** — Error taxonomy. Short and foundational. (~5 min)
8. **[src/validation.py](../src/validation.py)** — The four validation rules. (~10 min)
9. **[src/builder.py](../src/builder.py)** — Prompt template substitution. Short, important. (~10 min)
10. **[src/manager.py](../src/manager.py)** — Provider loading and config resolution. (~10 min)
11. **[src/account_pool.py](../src/account_pool.py)** — Account state machine and queuing. (~15 min)
12. **[src/runtime.py](../src/runtime.py)** — The execution engine. Read carefully. (~30 min)

### Day 3 — Understand the gateway (C)

13. **[src/auth.py](../src/auth.py)** — HMAC authentication. (~10 min)
14. **[src/main.py](../src/main.py)** — FastAPI gateway. Focus on `create_app()`, `make_emit()`, `run_worker()`, and the SSE generator. (~25 min)
15. **[src/export.py](../src/export.py)** — Artefact export. (~10 min)
16. **[src/alerts.py](../src/alerts.py)** — Consecutive failure tracking. (~5 min)
17. **[src/logging_conf.py](../src/logging_conf.py)** — JSON logging + metrics. (~10 min)

### Day 4 — Understand the providers (B)

18. **[src/providers/_browser.py](../src/providers/_browser.py)** — Shared Playwright engine. (~20 min)
19. **[src/providers/_extract.py](../src/providers/_extract.py)** — Text extraction utility. (~5 min)
20. **[src/providers/gemini_api.py](../src/providers/gemini_api.py)** — Simplest API provider. (~5 min)
21. **[src/providers/openai_api.py](../src/providers/openai_api.py)** — Streaming + non-streaming pattern. (~10 min)
22. **[src/providers/claude.py](../src/providers/claude.py)** — Simplest web provider delegation. (~5 min)

### Day 5 — Understand the tests and frontend

23. **[tests/integration/test_runtime.py](../tests/integration/test_runtime.py)** — Integration tests reveal expected behaviour more clearly than source code. (~20 min)
24. **[tests/api/test_api.py](../tests/api/test_api.py)** — Gateway contract from the client perspective. (~15 min)
25. **[tests/fixtures/providers/ok.py](../tests/fixtures/providers/ok.py)** + **[flaky.py](../tests/fixtures/providers/flaky.py)** + **[expire.py](../tests/fixtures/providers/expire.py)** — Fixture providers. (~5 min)
26. **[frontend/lib/api.ts](../frontend/lib/api.ts)** — HMAC signing + all API calls + SSE reconnect. (~10 min)
27. **[frontend/app/page.tsx](../frontend/app/page.tsx)** — Main UI state machine. (~15 min)

### Day 6 — Run it

28. **[docs/deploy.md](deploy.md)** — Deployment steps. (~10 min)
29. **[docs/session_seeding.md](session_seeding.md)** — Browser session injection. (~10 min)
30. Run `python scripts/smoke_stub.py` — end-to-end smoke test without any model. (~5 min)
31. Run `pytest tests/unit tests/integration -q` — confirm environment is working. (~5 min)

---

*This document was produced by reading every source file, test, documentation file, pipeline YAML, prompt template, and configuration file in the repository. Last updated: 2026-06-29.*
