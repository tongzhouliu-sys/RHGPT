"""Integration tests for src/runtime.py (A6 + A7; [修正-4][修正-8]).

Drives the real run_pipeline() over the fixture provider package using A's stub
emit helpers (standing in for C). Verifies: event seq/types, persisted files,
prompt chaining, fatal vs transient classification, retry, the per-profile
serialization lock, and the metrics signals.
"""

import json
import logging
import os
import tempfile
import threading
import unittest

from src import runtime
from src.builder import PromptBuilder
from src.logging_conf import metrics
from src.manager import ProviderManager
from src.runtime import run_pipeline
from src.stubs import make_file_emit, make_recording_emit
from tests.fixtures.providers import expire, flaky, ok, slow

HERE = os.path.dirname(__file__)
FIX = os.path.normpath(os.path.join(HERE, "..", "fixtures"))
FIX_CFG = os.path.join(FIX, "providers.yaml")
FIX_PROMPTS = os.path.join(FIX, "prompts")
FIX_PIPELINES = os.path.join(FIX, "pipelines")
PKG = "tests.fixtures.providers"


def PL(name: str) -> str:
    return os.path.join(FIX_PIPELINES, f"{name}.yaml")


class RuntimeITBase(unittest.TestCase):
    def setUp(self):
        # Silence the runtime's structured WARNING/ERROR logs (expire/flaky
        # tests deliberately trigger them); we assert on metrics/events, not logs.
        logging.getLogger().setLevel(logging.CRITICAL)
        from src.account_pool import AccountPoolManager
        AccountPoolManager.reset_instance()
        self.builder = PromptBuilder(FIX_PROMPTS)
        self.manager = ProviderManager(config_path=FIX_CFG, provider_package=PKG)

    def run_pl(self, name, question, session_dir, emit):
        return run_pipeline(
            PL(name),
            question,
            session_dir,
            emit,
            builder=self.builder,
            manager=self.manager,
        )


class TestHappyPath(RuntimeITBase):
    def test_two_step_success(self):
        ok.reset()
        with tempfile.TemporaryDirectory() as d:
            # Use the file emit (simulates C) so we can also confirm events.jsonl
            # is written by the emit, never by the runtime itself.
            emit, events = make_file_emit(d)
            ctx = self.run_pl("ok2", "What is 2+2?", d, emit)

            self.assertIn("s1", ctx["outputs"])
            self.assertIn("s2", ctx["outputs"])

            for fn in (
                "01_s1_prompt.md",
                "01_s1_response.md",
                "02_s2_prompt.md",
                "02_s2_response.md",
                "context.json",
                "events.jsonl",  # written by the (stub) emit, i.e. C's job
            ):
                self.assertTrue(os.path.isfile(os.path.join(d, fn)), fn)

            # s2's prompt must contain s1's output (prompt chaining via context).
            s1_out = ctx["outputs"]["s1"]
            with open(os.path.join(d, "02_s2_prompt.md"), encoding="utf-8") as f:
                s2_prompt = f.read()
            self.assertIn(s1_out, s2_prompt)

            # Event sequence: monotonic seq starting at 1, correct types/order.
            seq_types = [(e["seq"], e["type"]) for e in events]
            self.assertEqual(
                seq_types,
                [
                    (1, "step_started"),
                    (2, "step_succeeded"),
                    (3, "step_transitioning"),
                    (4, "step_started"),
                    (5, "step_succeeded"),
                    (6, "pipeline_finished"),
                ],
            )
            # succeeded events carry content; started events carry provider+key.
            succeeded = [e for e in events if e["type"] == "step_succeeded"]
            self.assertTrue(all(e.get("content") for e in succeeded))
            started = [e for e in events if e["type"] == "step_started"]
            self.assertEqual(started[0]["key"], "s1")
            self.assertEqual(started[0]["provider"], "ok_1")

            # Provider invoked exactly twice (once per step, no retries).
            self.assertEqual(len(ok.calls), 2)

            # on-disk events.jsonl matches the in-memory event list length.
            with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
                lines = [json.loads(x) for x in f if x.strip()]
            self.assertEqual(len(lines), len(events))


class TestFatalNoRetry(RuntimeITBase):
    def test_session_expired_is_fatal(self):
        expire.reset()
        with tempfile.TemporaryDirectory() as d:
            emit, events = make_recording_emit()
            ctx = self.run_pl("expire1", "Q", d, emit)

            self.assertEqual(ctx["outputs"], {})

            err_path = os.path.join(d, "01_s1_error.json")
            self.assertTrue(os.path.isfile(err_path))
            with open(err_path, encoding="utf-8") as f:
                err = json.load(f)
            self.assertEqual(err["type"], "session_expired")
            # No response file for a failed step.
            self.assertFalse(os.path.isfile(os.path.join(d, "01_s1_response.md")))

            seq_types = [(e["seq"], e["type"]) for e in events]
            self.assertEqual(
                seq_types,
                [
                    (1, "step_started"),
                    (2, "step_failed"),
                    (3, "pipeline_finished"),
                ],
            )
            failed = [e for e in events if e["type"] == "step_failed"][0]
            self.assertEqual(failed["error"]["type"], "session_expired")

            # Fatal class: NOT retried.
            self.assertEqual(len(expire.calls), 1)

    def test_session_expired_metric_incremented(self):
        expire.reset()
        metrics.reset()
        with tempfile.TemporaryDirectory() as d:
            emit, _ = make_recording_emit()
            self.run_pl("expire1", "Q", d, emit)
        counters = metrics.snapshot()["counters"]
        self.assertGreaterEqual(
            counters.get("session_expired_total{provider=expire_1}", 0), 1
        )


class TestTransientRetry(RuntimeITBase):
    def test_flaky_retries_then_succeeds(self):
        flaky.reset()
        metrics.reset()
        with tempfile.TemporaryDirectory() as d:
            emit, events = make_recording_emit()
            ctx = self.run_pl("flaky1", "Q", d, emit)

            # Eventually succeeds -> output present, no error.json written.
            self.assertIn("s1", ctx["outputs"])
            self.assertFalse(os.path.isfile(os.path.join(d, "01_s1_error.json")))
            self.assertTrue(os.path.isfile(os.path.join(d, "01_s1_response.md")))

            # profile "flaky:1" -> fails once, succeeds on 2nd attempt.
            self.assertEqual(len(flaky.calls), 2)

            self.assertEqual(
                [e["type"] for e in events],
                ["step_started", "step_succeeded", "pipeline_finished"],
            )

            counters = metrics.snapshot()["counters"]
            self.assertGreaterEqual(
                counters.get("step_retries_total{provider=flaky_1}", 0), 1
            )


class TestProfileLock(RuntimeITBase):
    def test_same_profile_runs_serially(self):
        # slow_a and slow_b both use provider slow_1 (profile p_slow); the
        # per-profile lock must prevent their run() calls from overlapping.
        slow.reset()
        barrier_errors: list[BaseException] = []

        def worker(name):
            try:
                with tempfile.TemporaryDirectory() as d:
                    emit, _ = make_recording_emit()
                    self.run_pl(name, "Q", d, emit)
            except BaseException as e:  # surface thread errors to the test
                barrier_errors.append(e)

        t1 = threading.Thread(target=worker, args=("slow_a",))
        t2 = threading.Thread(target=worker, args=("slow_b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(barrier_errors, [])
        # The decisive assertion: never more than one concurrent slow.run().
        self.assertEqual(slow.max_concurrency(), 1)
        self.assertEqual(len(slow.calls), 2)

    def test_lock_for_identity(self):
        self.assertIs(runtime._lock_for("X"), runtime._lock_for("X"))
        self.assertIsNot(runtime._lock_for("X"), runtime._lock_for("Y"))


if __name__ == "__main__":
    unittest.main()
