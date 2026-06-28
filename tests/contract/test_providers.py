"""Contract tests for B's Providers (B9). No real sites, no Playwright, no
network: web Providers are driven through a fake BrowserContext that simulates
network interception / login / empty output; API Providers are driven through a
stubbed `requests.post`. Verifies every site module honors contract 1
(`run(...) -> str`, non-empty) and the error semantics (SessionExpiredError /
GenerationTimeout)."""

import importlib
import os
import unittest
from unittest import mock

from src.providers import chatgpt, claude, deepseek, gemini_api, kimi, qwen_api, zai
from src.providers._errors import GenerationTimeout, ProviderError, SessionExpiredError
from src.providers._extract import extract_text

WEB_MODULES = [chatgpt, claude, deepseek, zai, kimi]
API_MODULES = [gemini_api, qwen_api]
ALL_MODULES = WEB_MODULES + API_MODULES


# ---- fake Playwright surface ------------------------------------------------
class FakeKeyboard:
    def type(self, text, delay=0):
        pass

    def press(self, key):
        pass


class FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    def count(self):
        return 1 if self._selector in self._page.cfg.get("present", set()) else 0

    def click(self, *args, **kwargs):
        pass

    @property
    def last(self):
        return self

    def inner_text(self):
        return self._page.cfg.get("dom_text", "")


class FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    def text(self):
        return self._body


class FakePage:
    def __init__(self, cfg):
        self.cfg = cfg
        self.keyboard = FakeKeyboard()
        self._handlers = []

    @property
    def url(self):
        return self.cfg.get("nav_url", "https://example.com/")

    def on(self, event, handler):
        if event == "response":
            self._handlers.append(handler)

    def goto(self, url, **kw):
        pass

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_selector(self, selector, **kw):
        # By the time the completion signal appears, the intercepted response has
        # arrived: fire it into the handlers now.
        if "resp_url" in self.cfg:
            resp = FakeResponse(self.cfg["resp_url"], self.cfg.get("resp_body", ""))
            for h in self._handlers:
                h(resp)
        return object()

    def close(self):
        pass


class FakeContext:
    def __init__(self, cfg):
        self.cfg = cfg

    def new_page(self):
        return FakePage(self.cfg)


def _patch_ctx(cfg):
    return mock.patch("src.providers._browser.get_context", return_value=FakeContext(cfg))


CHATGPT_SSE = 'data: {"message":{"content":{"parts":["Hello from ChatGPT"]}}}\ndata: [DONE]\n'


# ---- web engine behaviour (representative: chatgpt) -------------------------
class TestWebEngine(unittest.TestCase):
    def test_success_via_interception(self):
        cfg = {
            "nav_url": "https://chatgpt.com/",
            "resp_url": "https://chatgpt.com/backend-api/conversation",
            "resp_body": CHATGPT_SSE,
            "dom_text": "",
            "present": set(),
        }
        with _patch_ctx(cfg):
            out = chatgpt.run("data/profiles/acc1", "hi")
        self.assertEqual(out, "Hello from ChatGPT")

    def test_session_expired_on_login_page(self):
        cfg = {"nav_url": "https://auth.openai.com/login", "present": set()}
        with _patch_ctx(cfg):
            with self.assertRaises(SessionExpiredError):
                chatgpt.run("data/profiles/acc1", "hi")

    def test_empty_output_is_transient(self):
        cfg = {
            "nav_url": "https://chatgpt.com/",
            "resp_url": "https://chatgpt.com/backend-api/conversation",
            "resp_body": "data: [DONE]\n",  # parses to nothing
            "dom_text": "",
            "present": set(),
        }
        with _patch_ctx(cfg):
            with self.assertRaises(GenerationTimeout):
                chatgpt.run("data/profiles/acc1", "hi")

    def test_dom_fallback_when_no_interception(self):
        cfg = {
            "nav_url": "https://chatgpt.com/",
            "resp_url": "https://other/irrelevant",  # won't match -> no capture
            "resp_body": "data: {}",
            "dom_text": "DOM fallback answer",
            "present": set(),
        }
        with _patch_ctx(cfg):
            out = chatgpt.run("data/profiles/acc1", "hi")
        self.assertEqual(out, "DOM fallback answer")


# ---- per-site parsers + extractor -------------------------------------------
class TestExtractors(unittest.TestCase):
    def test_extract_openai_json(self):
        self.assertEqual(
            extract_text('{"choices":[{"message":{"content":"Hello A"}}]}'), "Hello A"
        )

    def test_extract_sse_deltas(self):
        body = 'data: {"choices":[{"delta":{"content":"Hel"}}]}\ndata: {"choices":[{"delta":{"content":"lo"}}]}\n'
        self.assertEqual(extract_text(body), "Hello")

    def test_extract_junk_returns_empty(self):
        self.assertEqual(extract_text("<html>nope</html>"), "")

    def test_each_web_parse_handles_sample_and_junk(self):
        for mod in WEB_MODULES:
            self.assertTrue(mod.SITE["parse"](CHATGPT_SSE).strip(), mod.__name__)
            self.assertEqual(mod.SITE["parse"]("not json"), "", mod.__name__)


# ---- structural contract: every module exposes run(...) ---------------------
class TestStructuralContract(unittest.TestCase):
    REQUIRED_SITE_KEYS = {
        "url",
        "response_match",
        "input_selector",
        "done_selector",
        "parse",
        "assistant_selector",
    }

    def test_all_modules_expose_run(self):
        for mod in ALL_MODULES:
            self.assertTrue(callable(getattr(mod, "run", None)), mod.__name__)

    def test_web_site_configs_have_required_keys(self):
        for mod in WEB_MODULES:
            missing = self.REQUIRED_SITE_KEYS - set(mod.SITE)
            self.assertFalse(missing, f"{mod.__name__} missing {missing}")
            self.assertTrue(callable(mod.SITE["parse"]), mod.__name__)

    def test_modules_importable_by_site_name(self):
        # Mirrors ProviderManager's importlib loading for every configured site.
        for site in ("chatgpt", "claude", "deepseek", "zai", "kimi", "gemini_api", "qwen_api"):
            m = importlib.import_module(f"src.providers.{site}")
            self.assertTrue(hasattr(m, "run"), site)


# ---- API providers via stubbed requests -------------------------------------
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestApiProviders(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY"
        )}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_gemini_success(self):
        os.environ["GEMINI_API_KEY"] = "k"
        payload = {"candidates": [{"content": {"parts": [{"text": "G-out"}]}}]}
        with mock.patch("requests.post", return_value=_FakeResp(payload)):
            self.assertEqual(gemini_api.run("", "hi"), "G-out")

    def test_gemini_empty_is_transient(self):
        os.environ["GEMINI_API_KEY"] = "k"
        with mock.patch("requests.post", return_value=_FakeResp({})):
            with self.assertRaises(GenerationTimeout):
                gemini_api.run("", "hi")

    def test_gemini_missing_key(self):
        with self.assertRaises(ProviderError):
            gemini_api.run("", "hi")

    def test_qwen_success(self):
        os.environ["DASHSCOPE_API_KEY"] = "k"
        payload = {"choices": [{"message": {"content": "Q-out"}}]}
        with mock.patch("requests.post", return_value=_FakeResp(payload)):
            self.assertEqual(qwen_api.run("", "hi"), "Q-out")

    def test_qwen_empty_is_transient(self):
        os.environ["DASHSCOPE_API_KEY"] = "k"
        with mock.patch("requests.post", return_value=_FakeResp({"choices": []})):
            with self.assertRaises(GenerationTimeout):
                qwen_api.run("", "hi")

    def test_qwen_missing_key(self):
        with self.assertRaises(ProviderError):
            qwen_api.run("", "hi")


# ---- end-to-end: real round1.yaml + real prompts + real provider modules ----
class TestRound1Relay(unittest.TestCase):
    """Drives A's run_pipeline over the production round1.yaml using the real
    builder/manager/providers; only the browser and `requests` seams are faked.
    Proves the 5-step web+API relay, prompt chaining, and persistence (B7)."""

    def test_five_step_relay(self):
        import json
        import tempfile

        from src.builder import PromptBuilder
        from src.manager import ProviderManager
        from src.runtime import run_pipeline

        # One fake response URL containing every web site's match substring, and a
        # body the extractor turns into non-empty text — works for any web step.
        web_cfg = {
            "nav_url": "https://site/",
            "resp_url": "https://site/backend-api/conversation/completion/chat_conversations/api/chat",
            "resp_body": CHATGPT_SSE,
            "dom_text": "",
            "present": set(),
        }
        gemini_payload = {"candidates": [{"content": {"parts": [{"text": "API-REVIEW"}]}}]}

        events = []
        with tempfile.TemporaryDirectory() as session_dir:
            with _patch_ctx(web_cfg), mock.patch(
                "requests.post", return_value=_FakeResp(gemini_payload)
            ):
                os.environ["GEMINI_API_KEY"] = "k"
                try:
                    ctx = run_pipeline(
                        "pipelines/round1.yaml",
                        "设计一个高并发短链服务",
                        session_dir,
                        events.append,
                        builder=PromptBuilder("prompts"),
                        manager=ProviderManager("config/providers.yaml"),
                    )
                finally:
                    os.environ.pop("GEMINI_API_KEY", None)

            # all five steps produced non-empty output
            outputs = ctx["outputs"]
            self.assertEqual(
                set(outputs), {"generate", "review", "deep_analyze", "improve", "summary"}
            )
            for k, v in outputs.items():
                self.assertTrue(v.strip(), k)
            self.assertEqual(outputs["review"], "API-REVIEW")  # gemini API step

            # event stream: 5 step_succeeded + pipeline_finished, monotonic seq
            types = [e["type"] for e in events]
            self.assertEqual(types.count("step_succeeded"), 5)
            self.assertEqual(types[-1], "pipeline_finished")
            self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))

            # prompt chaining through the real builder: review's prompt embeds the
            # generate output (review.md references {{generate}}).
            with open(os.path.join(session_dir, "02_review_prompt.md"), encoding="utf-8") as f:
                review_prompt = f.read()
            self.assertIn("Hello from ChatGPT", review_prompt)

            # persistence: context.json + per-step response files exist
            self.assertTrue(os.path.isfile(os.path.join(session_dir, "context.json")))
            with open(os.path.join(session_dir, "context.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["outputs"]["review"], "API-REVIEW")
            self.assertTrue(os.path.isfile(os.path.join(session_dir, "01_generate_response.md")))


if __name__ == "__main__":
    unittest.main()