"""Shared browser automation utilities (`src/providers/_browser.py`) — B1.

NOT an abstract base class — these are utility functions reused by each web
Provider (约定大于配置, §6.4 note). Provides:

  * a persistent-context pool keyed by profile (reuse across steps, no per-step
    cold start — [修正-6]);
  * anti-detection (stealth init script + real UA / locale / timezone /
    viewport — [修正-5]); `playwright_stealth` is used if installed, otherwise a
    built-in init script is injected;
  * `run_web(site, profile, prompt, timeout_ms)` — the engine every web Provider
    delegates to: network-interception-first text capture with DOM fallback,
    sleep-free generation-complete detection, and session-expiry detection.

Playwright is imported lazily inside the functions that need a live browser, so
this module (and the Provider modules that import it) can be imported — and
unit/contract-tested with a fake context — without launching Chromium.

Profile serialization is the caller's responsibility (Runtime holds a per-profile
lock, [修正-8]); the pool additionally guards context creation so concurrent
first-use of one profile cannot double-launch.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

from src.providers._errors import GenerationTimeout, SessionExpiredError

# Reused across the process for the lifetime of the pool.
_PW = None
_PW_LOCK = threading.Lock()
_CONTEXTS: dict[str, object] = {}
_CTX_LOCKS: dict[str, threading.Lock] = {}
_POOL_LOCK = threading.Lock()

# A current, real desktop Chrome UA. Verify/rotate periodically.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Minimal stealth: mask the most common automation tells. Used when
# playwright_stealth is unavailable. Not a substitute for a real fingerprint
# review, but removes the obvious `navigator.webdriver` / empty-plugins signals.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
const _q = navigator.permissions && navigator.permissions.query;
if (_q) {
  navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _q(p);
}
"""


def _headless() -> bool:
    # Containers run headed under Xvfb (§12.2); allow override for local dev.
    return os.environ.get("RHCLOUD_HEADLESS", "1") == "1"


_THREAD_LOCAL = threading.local()


def _ensure_playwright():
    pw = getattr(_THREAD_LOCAL, "pw", None)
    if pw is None:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        _THREAD_LOCAL.pw = pw
    return pw


def _profile_lock(profile: str) -> threading.Lock:
    with _POOL_LOCK:
        lock = _CTX_LOCKS.get(profile)
        if lock is None:
            lock = threading.Lock()
            _CTX_LOCKS[profile] = lock
        return lock


def get_context(profile: str):
    """Return a BrowserContext for `profile`, managing thread-local Playwright instances."""
    contexts = getattr(_THREAD_LOCAL, "contexts", None)
    if contexts is None:
        contexts = {}
        _THREAD_LOCAL.contexts = contexts

    ctx = contexts.get(profile)
    if ctx is not None:
        return ctx

    with _profile_lock(profile):
        ctx = contexts.get(profile)
        if ctx is not None:
            return ctx

        pw = _ensure_playwright()
        user_data_dir = profile or os.path.join("data", "profiles", "_default")
        os.makedirs(user_data_dir, exist_ok=True)
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir,
                headless=_headless(),
                user_agent=DEFAULT_UA,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
        except Exception:
            # Fallback if profile directory is locked by another process/thread
            browser = pw.chromium.launch(
                headless=_headless(),
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=DEFAULT_UA,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1280, "height": 800},
            )
        ctx.add_init_script(_STEALTH_JS)
        contexts[profile] = ctx
        with _POOL_LOCK:
            _CONTEXTS[f"{threading.get_ident()}_{profile}"] = ctx
        return ctx


def shutdown() -> None:
    """Close all pooled contexts and stop Playwright. For cleanup / tests."""
    global _PW
    with _POOL_LOCK:
        contexts = list(_CONTEXTS.values())
        _CONTEXTS.clear()
        _CTX_LOCKS.clear()
    for ctx in contexts:
        try:
            ctx.close()
        except Exception:
            pass
    pw = getattr(_THREAD_LOCAL, "pw", None)
    if pw is not None:
        try:
            pw.stop()
        except Exception:
            pass
        _THREAD_LOCAL.pw = None


# ----- the shared web Provider engine ----------------------------------------
def run_web(
    site: dict,
    profile: str,
    prompt: str,
    timeout_ms: int = 120000,
    on_chunk: Optional[Callable[[str], None]] = None,
    **options,
) -> str:
    """Drive one web model end-to-end and return its answer text.

    `site` describes the site (see any Provider module for the shape):
        url, response_match, input_selector, send_selector (optional),
        done_selector, login_selectors, assistant_selector, parse, type_delay_ms.
    """
    ctx = get_context(profile)
    page = ctx.new_page()
    captured: dict[str, Optional[str]] = {"text": None}
    parse: Callable[[str], str] = site["parse"]
    matches = site["response_match"]
    if isinstance(matches, str):
        matches = [matches]

    def on_response(resp):
        # Network interception is the PRIMARY extraction path ([修正-5]).
        try:
            url = resp.url
            if any(m in url for m in matches):
                body = resp.text()
                text = parse(body)
                if text and text.strip():
                    prev_text = captured["text"]
                    if on_chunk:
                        if prev_text is None:
                            on_chunk(text)
                        elif text.startswith(prev_text):
                            delta = text[len(prev_text):]
                            if delta:
                                on_chunk(delta)
                    captured["text"] = text
        except Exception:
            # A single un-parseable frame must never crash the run; DOM fallback
            # or a later frame can still provide the text.
            pass

    page.on("response", on_response)
    try:
        if on_chunk:
            on_chunk(f"🌐 正在连接并加载目标页面 ({site['url']})...\n")
        page.goto(site["url"], wait_until="domcontentloaded", timeout=min(30000, timeout_ms))

        if _is_login_page(page, site):
            raise SessionExpiredError(profile)

        if on_chunk:
            on_chunk("✍️ 正在输入 Prompt 并提交请求...\n")
        _submit_prompt(page, site, prompt)

        if on_chunk:
            on_chunk("⏳ 正在等待大模型思考与生成回复...\n\n")
        _wait_generation_done(page, site, timeout_ms, on_chunk=on_chunk, captured=captured)

        text = captured["text"] or _extract_dom(page, site)
        if not text or not text.strip():
            raise GenerationTimeout(profile, "no text captured (interception + DOM both empty)")
        return text
    finally:
        # Close the page but keep the context warm in the pool ([修正-6]).
        try:
            page.close()
        except Exception:
            pass


def _is_login_page(page, site) -> bool:
    # Redirected to a login URL, or a login control is present.
    login_url = site.get("login_url_match")
    if login_url:
        try:
            if login_url in page.url:
                return True
        except Exception:
            pass
    for sel in site.get("login_selectors", []) or []:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


def _submit_prompt(page, site, prompt: str) -> None:
    editor = page.locator(site["input_selector"])
    try:
        editor.click(timeout=6000)
    except Exception as e:
        if _is_login_page(page, site):
            raise SessionExpiredError(getattr(page, "_profile", "")) from e
        raise GenerationTimeout(getattr(page, "_profile", ""), f"input_selector '{site['input_selector']}' not accessible within 6s: {e}") from e

    delay = site.get("type_delay_ms", 10)
    page.keyboard.type(prompt, delay=delay)  # human-like input
    send_selector = site.get("send_selector")
    if send_selector:
        try:
            page.locator(send_selector).click(timeout=5000)
        except Exception:
            page.keyboard.press("Enter")
    else:
        page.keyboard.press("Enter")


def _wait_generation_done(
    page,
    site: dict,
    timeout_ms: int,
    on_chunk: Optional[Callable[[str], None]] = None,
    captured: Optional[dict] = None,
) -> None:
    done_selector = site["done_selector"]
    state = site.get("done_state", "visible")

    if not on_chunk:
        try:
            page.wait_for_selector(done_selector, timeout=timeout_ms, state=state)
            return
        except Exception as e:
            raise GenerationTimeout(
                getattr(page, "_profile", ""),
                f"generation-done signal timed out: {e}",
            )

    start_time = time.time()
    last_text = (captured.get("text") if captured else None) or ""

    while True:
        try:
            page.wait_for_selector(done_selector, timeout=1000, state=state)
            break
        except Exception as e:
            if (time.time() - start_time) * 1000 >= timeout_ms:
                raise GenerationTimeout(
                    getattr(page, "_profile", ""),
                    f"generation-done signal timed out after {timeout_ms}ms: {e}",
                )
            try:
                current_dom = _extract_dom(page, site)
                if current_dom and current_dom.strip():
                    if not last_text:
                        on_chunk(current_dom)
                        last_text = current_dom
                    elif current_dom.startswith(last_text):
                        delta = current_dom[len(last_text):]
                        if delta:
                            on_chunk(delta)
                            last_text = current_dom
                    elif current_dom != last_text:
                        delta = current_dom.replace(last_text, "")
                        if delta:
                            on_chunk(delta)
                        last_text = current_dom
                    if captured is not None:
                        captured["text"] = current_dom
            except Exception:
                pass


def _extract_dom(page, site) -> str:
    # DOM fallback only ([修正-5]: interception preferred).
    selector = site.get("assistant_selector")
    if not selector:
        return ""
    try:
        loc = page.locator(selector).last
        return loc.inner_text()
    except Exception:
        return ""


__all__ = ["get_context", "run_web", "shutdown", "DEFAULT_UA"]
