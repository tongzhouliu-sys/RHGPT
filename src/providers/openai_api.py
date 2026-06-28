"""OpenAI-compatible official & custom API Provider (`src/providers/openai_api.py`).

Contract 1: `run(profile, prompt, *, timeout_ms, **options) -> str`.
Supports official OpenAI endpoints and custom OpenAI-compatible proxies (OneAPI, NewAPI, vLLM, DeepSeek API, etc.).
"""

from __future__ import annotations

import json
import os

from src.providers._errors import GenerationTimeout, ProviderError

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def run(profile: str, prompt: str, *, timeout_ms: int = 120000, **options) -> str:
    key = options.get("api_key") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ProviderError(profile, "OPENAI_API_KEY not set")

    base_url = (options.get("base_url") or os.environ.get("OPENAI_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    model = options.get("model") or os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)
    on_chunk = options.get("on_chunk")

    import requests

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    if on_chunk:
        payload = {
            "model": model,
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
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        delta = chunk_json.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            on_chunk(delta)
                            full_text += delta
                    except Exception:
                        pass
            if full_text.strip():
                return full_text
        except Exception:
            # Fallback to non-streaming if stream mode fails
            pass

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_ms / 1000)
    resp.raise_for_status()
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = ""

    if not text or not text.strip():
        raise GenerationTimeout(profile, "empty OpenAI API response")
    return text.strip()


__all__ = ["run"]
