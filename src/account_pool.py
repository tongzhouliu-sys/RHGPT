"""Account Pool Manager (`src/account_pool.py`).

Tracks health, busy status, and queuing for web provider accounts.
Supports round-robin selection, thread-safe queuing, and automatic session recovery.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from src.logging_conf import get_logger, log_event

_log = get_logger("rhcloud.account_pool")


class AccountStatus(str, enum.Enum):
    IDLE = "idle"
    BUSY = "busy"
    EXPIRED = "expired"
    COOLDOWN = "cooldown"


class AccountSlot:
    def __init__(self, provider_name: str, site: str, profile: str, config: dict):
        self.provider_name = provider_name
        self.site = site
        self.profile = profile
        self.config = config
        self.status = AccountStatus.IDLE
        self.last_used = 0.0
        self.fail_count = 0

    def to_dict(self) -> dict:
        return {
            "provider_name": self.provider_name,
            "site": self.site,
            "profile": self.profile,
            "status": self.status.value,
            "fail_count": self.fail_count,
        }


class AccountPoolManager:
    _instance: Optional[AccountPoolManager] = None
    _instance_lock = threading.Lock()

    def __init__(self, providers_cfg: Optional[dict] = None):
        self._lock = threading.Condition(threading.Lock())
        self._slots: Dict[str, AccountSlot] = {}
        self._site_rr_index: Dict[str, int] = {}
        self._queues: Dict[str, int] = {}  # site -> number of waiting tasks

        if providers_cfg:
            self.load_providers(providers_cfg)

    @classmethod
    def get_instance(cls, providers_cfg: Optional[dict] = None) -> AccountPoolManager:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(providers_cfg)
            elif providers_cfg is not None:
                cls._instance.load_providers(providers_cfg)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def load_providers(self, providers_cfg: dict) -> None:
        with self._lock:
            providers = providers_cfg.get("providers") or {}
            for name, conf in providers.items():
                if not isinstance(conf, dict):
                    continue
                site = conf.get("site", "")
                profile = conf.get("profile", "")
                if name not in self._slots:
                    self._slots[name] = AccountSlot(name, site, profile, conf)
                else:
                    # Update config
                    self._slots[name].config = conf
                    self._slots[name].site = site
                    self._slots[name].profile = profile

    def mark_expired(self, provider_name: str) -> None:
        """Mark an account as EXPIRED so it won't be picked for subsequent tasks."""
        with self._lock:
            slot = self._slots.get(provider_name)
            if slot:
                slot.status = AccountStatus.EXPIRED
                slot.fail_count += 1
                log_event(_log, "account_marked_expired", provider=provider_name, site=slot.site)
                self._lock.notify_all()

    def mark_cooldown(self, provider_name: str, cooldown_seconds: float = 60.0) -> None:
        """Mark an account as in COOLDOWN."""
        with self._lock:
            slot = self._slots.get(provider_name)
            if slot:
                slot.status = AccountStatus.COOLDOWN
                slot.fail_count += 1
                log_event(_log, "account_marked_cooldown", provider=provider_name, site=slot.site)
                self._lock.notify_all()

    def release_account(self, provider_name: str) -> None:
        """Release an account back to IDLE state if it is not expired."""
        with self._lock:
            slot = self._slots.get(provider_name)
            if slot and slot.status == AccountStatus.BUSY:
                slot.status = AccountStatus.IDLE
                slot.last_used = time.time()
                self._lock.notify_all()

    def acquire_account(
        self,
        target: str,
        *,
        timeout_ms: int = 120000,
        on_queue: Optional[Callable[[int], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[Optional[AccountSlot], Optional[str]]:
        """Acquire an idle, healthy account for a given provider_name or site.

        If target matches a provider_name directly, attempts to acquire that specific slot
        (or an alternative candidate under the same site if it's expired/busy).
        If target is a site name or candidate list, round-robins among available idle slots under that site.

        Returns (slot, None) on success, or (None, error_reason) on failure/timeout.
        """
        start_time = time.time()
        deadline = start_time + (timeout_ms / 1000.0)

        with self._lock:
            # Determine site and candidates
            if target in self._slots:
                primary_slot = self._slots[target]
                site = primary_slot.site
            else:
                site = target

            self._queues[site] = self._queues.get(site, 0)
            queued_notified = False

            while True:
                if is_cancelled and is_cancelled():
                    return None, "job was cancelled by user"

                # Find candidate slots matching this site or primary target
                candidates = [s for s in self._slots.values() if s.site == site or s.provider_name == target]
                
                # Filter idle & healthy candidates
                idle_candidates = [s for s in candidates if s.status == AccountStatus.IDLE]

                if idle_candidates:
                    # Sort by round-robin / last_used
                    idle_candidates.sort(key=lambda s: s.last_used)
                    chosen = idle_candidates[0]
                    chosen.status = AccountStatus.BUSY
                    if queued_notified:
                        self._queues[site] = max(0, self._queues.get(site, 1) - 1)
                    return chosen, None

                # Check if all candidate slots are EXPIRED
                active_candidates = [s for s in candidates if s.status != AccountStatus.EXPIRED]
                if not active_candidates and candidates:
                    return None, f"all accounts for site '{site}' are expired and require re-seeding"

                # All active candidates are BUSY; enter queue
                now = time.time()
                if now >= deadline:
                    if queued_notified:
                        self._queues[site] = max(0, self._queues.get(site, 1) - 1)
                    return None, f"timeout waiting for available account under site '{site}'"

                if not queued_notified:
                    self._queues[site] = self._queues.get(site, 0) + 1
                    queued_notified = True

                pos = self._queues.get(site, 1)
                if on_queue:
                    # Inform caller of queue position
                    on_queue(pos)

                wait_seconds = min(1.0, deadline - now)
                self._lock.wait(timeout=wait_seconds)

    def get_status_summary() -> List[dict]:
        with self._lock:
            return [s.to_dict() for s in self._slots.values()]


__all__ = ["AccountPoolManager", "AccountStatus", "AccountSlot"]
