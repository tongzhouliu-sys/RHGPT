"""Unit tests for src/validation.py (A5).

Covers the four validation checks (provider exists, prompt file exists, unique
keys, forward-reference / unknown-variable) plus providers.yaml schema checks,
and proves that spaced/dotted braces in a template are NOT treated as variables
(the [修正-3] boundary — only bare {{identifier}} is a variable).
"""

import os
import tempfile
import unittest

import yaml

from src.validation import (
    ValidationError,
    validate_pipeline,
    validate_pipeline_file,
    validate_providers_config,
)

HERE = os.path.dirname(__file__)
FIX = os.path.normpath(os.path.join(HERE, "..", "fixtures"))
FIX_CFG = os.path.join(FIX, "providers.yaml")
FIX_PROMPTS = os.path.join(FIX, "prompts")
FIX_PIPELINES = os.path.join(FIX, "pipelines")


def _providers_cfg() -> dict:
    with open(FIX_CFG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def PL(name: str) -> str:
    return os.path.join(FIX_PIPELINES, f"{name}.yaml")


class TestPipelineValidation(unittest.TestCase):
    def setUp(self):
        self.cfg = _providers_cfg()

    def test_ok2_is_valid_and_returns_parsed(self):
        parsed = validate_pipeline(PL("ok2"), self.cfg, FIX_PROMPTS)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(len(parsed["steps"]), 2)

    def test_missing_provider_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_pipeline(PL("bad_provider"), self.cfg, FIX_PROMPTS)
        self.assertTrue(any("does_not_exist" in m for m in cm.exception.errors))

    def test_missing_prompt_file_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_pipeline(PL("bad_prompt"), self.cfg, FIX_PROMPTS)
        self.assertTrue(any("no_such_prompt" in m for m in cm.exception.errors))

    def test_duplicate_key_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_pipeline(PL("dup_key"), self.cfg, FIX_PROMPTS)
        self.assertTrue(any("duplicate" in m for m in cm.exception.errors))

    def test_forward_reference_raises(self):
        # s1 references {{s2}}, produced by a later step.
        with self.assertRaises(ValidationError) as cm:
            validate_pipeline(PL("bad_forward"), self.cfg, FIX_PROMPTS)
        self.assertTrue(any("forward reference" in m for m in cm.exception.errors))

    def test_unknown_variable_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_pipeline(PL("unknown_var"), self.cfg, FIX_PROMPTS)
        self.assertTrue(any("unknown variable" in m for m in cm.exception.errors))

    def test_spaced_and_dotted_braces_not_flagged(self):
        # pcode.md carries {{ vue_var }} and {{another.expr}} (NOT variables)
        # plus a valid prior-step {{s1}}. A 2-step pipeline that produces s1
        # before using pcode must validate WITHOUT error — proving the
        # validator only treats bare {{identifier}} as a variable reference.
        steps = {
            "name": "pcode-after-s1",
            "steps": [
                {"key": "s1", "provider": "ok_1", "prompt": "p1"},
                {"key": "s2", "provider": "ok_1", "prompt": "pcode"},
            ],
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            yaml.safe_dump(steps, f)
            path = f.name
        try:
            parsed = validate_pipeline(path, self.cfg, FIX_PROMPTS)
            self.assertEqual(len(parsed["steps"]), 2)
        finally:
            os.unlink(path)

    def test_validate_pipeline_file_submit_path(self):
        # C's submit path: loads providers from disk, returns parsed on success.
        parsed = validate_pipeline_file(PL("ok2"), FIX_CFG, FIX_PROMPTS)
        self.assertEqual(len(parsed["steps"]), 2)
        with self.assertRaises(ValidationError):
            validate_pipeline_file(PL("bad_provider"), FIX_CFG, FIX_PROMPTS)

    def test_validate_race_round1_pipeline(self):
        parsed = validate_pipeline_file("pipelines/race_round1.yaml", "config/providers.yaml", "prompts")
        self.assertEqual(len(parsed["steps"]), 5)


class TestProvidersConfigValidation(unittest.TestCase):
    def test_good_fixture_passes(self):
        validate_providers_config(_providers_cfg())  # must not raise

    def test_missing_site_raises(self):
        cfg = {"providers": {"x": {"profile": "p"}}}
        with self.assertRaises(ValidationError) as cm:
            validate_providers_config(cfg)
        self.assertTrue(any("missing non-empty 'site'" in m for m in cm.exception.errors))

    def test_empty_providers_raises(self):
        with self.assertRaises(ValidationError):
            validate_providers_config({"providers": {}})

    def test_non_int_option_raises(self):
        cfg = {"providers": {"x": {"site": "ok", "profile": "p", "retries": "two"}}}
        with self.assertRaises(ValidationError) as cm:
            validate_providers_config(cfg)
        self.assertTrue(any("'retries' must be an integer" in m for m in cm.exception.errors))


if __name__ == "__main__":
    unittest.main()
