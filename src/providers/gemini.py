"""Gemini (gemini.google.com) web Provider (`src/providers/gemini.py`).

Contract 1 via `_browser.run_web`. Selectors / response URL best-effort; verify
against the live site.
"""

from __future__ import annotations

from src.providers._browser import run_web
from src.providers._extract import extract_text


def _parse(body: str) -> str:
    return extract_text(body)


SITE = {
    "url": "https://gemini.google.com/",
    "response_match": ["/bard", "/_/", "/api/chat", "/batchexecute"],
    "input_selector": "div[contenteditable='true'], rich-textarea, textarea",
    "send_selector": 'button[aria-label*="Send"], button[class*="send-button"]',
    "done_selector": 'button[aria-label*="Send"]:not([disabled])',
    "done_state": "visible",
    "login_url_match": "/accounts.google.com",
    "login_selectors": ['input[type="email"]', 'input[type="password"]'],
    "assistant_selector": 'message-content, div[class*="markdown"], div[class*="message"]',
    "parse": _parse,
    "type_delay_ms": 8,
}


def run(profile: str, prompt: str, *, timeout_ms: int = 120000, **options) -> str:
    return run_web(SITE, profile, prompt, timeout_ms)


__all__ = ["run", "SITE"]
