"""Circuit-breaker health tracking per provider.

healthy -> after 2+ consecutive failures -> degraded
         -> after 5+ consecutive failures -> unavailable (cooldown)
unavailable auto-recovers after cooldown_minutes; any success resets to healthy.
"""

from __future__ import annotations

import time
from typing import Any

from ..models.schemas import HealthState

_DEGRADED_AFTER = 1
_DOWN_AFTER = 4
_COOLDOWN_S = 300.0  # 5 minutes


class HealthTracker:
    def __init__(self, cooldown_s: float = _COOLDOWN_S) -> None:
        self.cooldown_s = cooldown_s
        self._state: dict[str, dict[str, Any]] = {}

    def _entry(self, provider: str) -> dict[str, Any]:
        return self._state.setdefault(
            provider,
            {"consecutive_failures": 0, "last_success": None,
             "last_error": None, "last_failure_ts": None},
        )

    def record_success(self, provider: str) -> None:
        e = self._entry(provider)
        e["consecutive_failures"] = 0
        e["last_success"] = time.time()

    def record_failure(self, provider: str, error: str | None = None) -> None:
        e = self._entry(provider)
        e["consecutive_failures"] += 1
        e["last_error"] = (error or "unknown")[:200]
        e["last_failure_ts"] = time.time()

    def state(self, provider: str) -> HealthState:
        e = self._entry(provider)
        f = e["consecutive_failures"]
        if f == 0:
            return HealthState.HEALTHY
        if f >= _DOWN_AFTER:
            # Auto-recover after cooldown.
            if e["last_failure_ts"] and time.time() - e["last_failure_ts"] > self.cooldown_s:
                e["consecutive_failures"] = _DEGRADED_AFTER  # give it another chance
                return HealthState.DEGRADED
            return HealthState.UNAVAILABLE
        if f >= _DEGRADED_AFTER:
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    def is_usable(self, provider: str) -> bool:
        return self.state(provider) != HealthState.UNAVAILABLE

    def last_error(self, provider: str) -> str | None:
        return self._entry(provider).get("last_error")

    def summary(self, provider: str) -> dict[str, Any]:
        e = self._entry(provider)
        return {
            "state": self.state(provider).value,
            "consecutive_failures": e["consecutive_failures"],
            "last_error": e["last_error"],
        }
