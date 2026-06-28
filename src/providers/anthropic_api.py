"""Anthropic official & custom API Provider (`src/providers/anthropic_api.py`).

Contract 1: `run(profile, prompt, *, timeout_ms, **options) -> str`.
Supports official Anthropic Messages API and custom compatible proxies.
"""

from __future__ import annotations

import json
import os

from src.providers._errors import GenerationTimeout, ProviderError

_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
_DEFAULT_BASE_URL = "https://api.anthropic.com"


def run(profile: str, prompt: str, *, timeout_ms: int = 120000, **options) -> str:
    key = options.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ProviderError(profile, "ANTHROPIC_API_KEY not set")

    base_url = (options.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    model = options.get("model") or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    on_chunk = options.get("on_chunk")

    import requests

    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    if on_chunk:
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_ms / 1000, stream=True)
            resp.raise_for_status()
            full_text = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_str.startswith("data: "):
                    data_str = line_str[6:].strip()
                    try:
                        chunk_json = json.loads(data_str)
                        if chunk_json.get("type") == "content_block_delta":
                            delta = chunk_json.get("delta", {}).get("text", "")
                            if delta:
                                on_chunk(delta)
                                full_text += delta
                    except Exception:
                        pass
            if full_text.strip():
                return full_text
        except Exception:
            pass

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_ms / 1000)
    resp.raise_for_status()
    data = resp.json()
    try:
        content_list = data.get("content", [])
        text = "".join(item.get("text", "") for item in content_list if item.get("type") == "text").strip()
    except Exception:
        text = ""

    if not text:
        raise GenerationTimeout(profile, "empty Anthropic API response")
    return text


__all__ = ["run"]
