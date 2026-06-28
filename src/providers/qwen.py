"""Qwen International (chat.qwen.ai) web Provider (`src/providers/qwen.py`).

Contract 1 via `_browser.run_web`. Selectors / response URL best-effort; verify
against the live site.
"""

from __future__ import annotations

from src.providers._browser import run_web
from src.providers._extract import extract_text


def _parse(body: str) -> str:
    return extract_text(body)


SITE = {
    "url": "https://chat.qwen.ai/",
    "response_match": ["/api/chat", "/v1/chat/completions", "/api/v1/chat"],
    "input_selector": "textarea, div[contenteditable='true']",
    "send_selector": 'button[type="submit"], button:has-text("Send"), button[class*="send"]',
    "done_selector": 'button[type="submit"]:not([disabled])',
    "done_state": "visible",
    "login_url_match": "/login",
    "login_selectors": ['input[type="password"]', 'button:has-text("Sign in")', 'button:has-text("Log in")'],
    "assistant_selector": 'div[class*="markdown"], div[class*="prose"], div[class*="assistant"]',
    "parse": _parse,
    "type_delay_ms": 8,
}


def run(profile: str, prompt: str, *, timeout_ms: int = 120000, **options) -> str:
    return run_web(SITE, profile, prompt, timeout_ms)


__all__ = ["run", "SITE"]
