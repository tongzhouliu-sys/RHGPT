"""Unit tests for src/account_pool.py."""

import unittest
import time
from src.account_pool import AccountPoolManager, AccountStatus


class TestAccountPoolManager(unittest.TestCase):
    def setUp(self):
        AccountPoolManager.reset_instance()
        self.cfg = {
            "providers": {
                "chatgpt_web_1": {"site": "chatgpt", "profile": "p1"},
                "chatgpt_web_2": {"site": "chatgpt", "profile": "p2"},
                "claude_web_1": {"site": "claude", "profile": "p3"},
            }
        }
        self.pool = AccountPoolManager.get_instance(self.cfg)

    def test_round_robin_and_acquire(self):
        slot1, err1 = self.pool.acquire_account("chatgpt")
        self.assertIsNotNone(slot1)
        self.assertIn(slot1.provider_name, ["chatgpt_web_1", "chatgpt_web_2"])
        self.assertEqual(slot1.status, AccountStatus.BUSY)

        slot2, err2 = self.pool.acquire_account("chatgpt")
        self.assertIsNotNone(slot2)
        self.assertNotEqual(slot1.provider_name, slot2.provider_name)

        self.pool.release_account(slot1.provider_name)
        self.assertEqual(slot1.status, AccountStatus.IDLE)

    def test_session_expired_and_failover(self):
        slot1, _ = self.pool.acquire_account("chatgpt_web_1")
        self.assertEqual(slot1.provider_name, "chatgpt_web_1")

        # Mark expired
        self.pool.mark_expired("chatgpt_web_1")
        self.assertEqual(slot1.status, AccountStatus.EXPIRED)

        # Acquiring for site chatgpt should now return chatgpt_web_2
        slot2, err = self.pool.acquire_account("chatgpt")
        self.assertIsNotNone(slot2)
        self.assertEqual(slot2.provider_name, "chatgpt_web_2")


if __name__ == "__main__":
    unittest.main()
