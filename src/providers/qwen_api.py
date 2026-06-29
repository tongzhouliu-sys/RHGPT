"""Qwen official-API Provider (`src/providers/qwen_api.py`) — B6 (P1).

Contract 1 via DashScope's OpenAI-compatible endpoint. API key from env (§9.3);
`profile` unused. `requests` imported lazily; contract tests stub `requests.post`.
"""

from __future__ import annotations

import os

from src.providers._errors import GenerationTimeout, ProviderError

_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_DEFAULT_MODEL = "qwen-plus"


def _extract(data: dict) -> str:
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def run(profile: str, prompt: str, *, timeout_ms: int = 120000, **options) -> str:
    api_key_env = options.get("api_key_env") or "DASHSCOPE_API_KEY"
    key = os.environ.get(api_key_env) or os.environ.get("QWEN_API_KEY")
    if not key:
        raise ProviderError(profile, f"{api_key_env} / QWEN_API_KEY not set")
    model = options.get("model") or os.environ.get("QWEN_MODEL", _DEFAULT_MODEL)

    import requests

    resp = requests.post(
        _ENDPOINT,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout_ms / 1000,
    )
    resp.raise_for_status()
    text = _extract(resp.json())
    if not text:
        raise GenerationTimeout(profile, "empty Qwen response")
    return text


__all__ = ["run"]
