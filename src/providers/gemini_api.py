"""Gemini official-API Provider (`src/providers/gemini_api.py`) — B3 (P0 baseline).

Contract 1: `run(profile, prompt, *, timeout_ms, **options) -> str`. The stable,
no-browser baseline. API key comes from the environment (never YAML, §9.3).
`profile` is unused ("" for API providers). `requests` is imported lazily so the
module imports without it; contract tests stub `requests.post`.
"""

from __future__ import annotations

import os

from src.providers._errors import GenerationTimeout, ProviderError

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-2.5-flash"
_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"]


def _extract(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


def run(profile: str, prompt: str, *, timeout_ms: int = 120000, **options) -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ProviderError(profile, "GEMINI_API_KEY / GOOGLE_API_KEY not set")
    model = options.get("model") or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)

    import requests

    models_to_try = [model] + [m for m in _FALLBACK_MODELS if m != model]
    last_err = None

    for m in models_to_try:
        url = _ENDPOINT.format(model=m)
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    params={"key": key},
                    json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
                    timeout=timeout_ms / 1000,
                )
                resp.raise_for_status()
                text = _extract(resp.json())
                if not text:
                    raise GenerationTimeout(profile, "empty Gemini response")
                return text
            except requests.HTTPError as e:
                last_err = e
                if e.response is not None:
                    status = e.response.status_code
                    if status == 404:
                        break  # model not found, switch model
                    if status == 429:
                        if attempt == 0:
                            import time
                            time.sleep(2.0)
                            continue  # retry once
                        else:
                            break  # quota exceeded for this model, switch model
                raise e
    if last_err:
        raise last_err
    raise ProviderError(profile, "Gemini call failed")


__all__ = ["run"]
