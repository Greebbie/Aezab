"""Circuit breaker for external service calls (LLM providers, tool endpoints).

Prevents cascade failures by fast-failing when a service is known to be down.
States: CLOSED (normal) -> OPEN (failing, reject fast) -> HALF_OPEN (probe).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Service is down, reject requests fast
    HALF_OPEN = "half_open" # Probing with single request


@dataclass
class CircuitStats:
    """Failure tracking stats for a single circuit."""
    failures: int = 0
    successes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state: CircuitState = field(default=CircuitState.CLOSED)
    state_changed_at: float = field(default_factory=time.time)


class CircuitBreaker:
    """Per-service circuit breaker with configurable thresholds."""

    def __init__(
        self,
        failure_threshold: int = 5,
        failure_window_seconds: float = 60.0,
        recovery_timeout_seconds: float = 30.0,
    ):
        self._failure_threshold = failure_threshold
        self._failure_window = failure_window_seconds
        self._recovery_timeout = recovery_timeout_seconds
        self._circuits: dict[str, CircuitStats] = {}
        self._lock = threading.RLock()

    def can_execute(self, service_name: str) -> bool:
        """Check if a request to this service should be allowed."""
        with self._lock:
            stats = self._circuits.get(service_name)
            if stats is None:
                return True

            if stats.state == CircuitState.CLOSED:
                return True

            if stats.state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if time.time() - stats.state_changed_at >= self._recovery_timeout:
                    stats.state = CircuitState.HALF_OPEN
                    stats.state_changed_at = time.time()
                    logger.info("Circuit '%s': OPEN -> HALF_OPEN (probing)", service_name)
                    return True  # Allow one probe request
                return False

            if stats.state == CircuitState.HALF_OPEN:
                return True  # Allow probe requests

            return True

    def record_success(self, service_name: str) -> None:
        """Record a successful request."""
        with self._lock:
            stats = self._get_or_create(service_name)
            stats.successes += 1
            stats.last_success_time = time.time()

            if stats.state == CircuitState.HALF_OPEN:
                stats.state = CircuitState.CLOSED
                stats.failures = 0
                stats.state_changed_at = time.time()
                logger.info("Circuit '%s': HALF_OPEN -> CLOSED (recovered)", service_name)

    def record_failure(self, service_name: str) -> None:
        """Record a failed request."""
        with self._lock:
            stats = self._get_or_create(service_name)
            now = time.time()

            # Reset failure count if outside the window
            if now - stats.last_failure_time > self._failure_window:
                stats.failures = 0

            stats.failures += 1
            stats.last_failure_time = now

            if stats.state == CircuitState.HALF_OPEN:
                # Probe failed — back to open
                stats.state = CircuitState.OPEN
                stats.state_changed_at = now
                logger.warning("Circuit '%s': HALF_OPEN -> OPEN (probe failed)", service_name)
            elif stats.state == CircuitState.CLOSED and stats.failures >= self._failure_threshold:
                stats.state = CircuitState.OPEN
                stats.state_changed_at = now
                logger.warning(
                    "Circuit '%s': CLOSED -> OPEN (%d failures in %.0fs)",
                    service_name, stats.failures, self._failure_window,
                )

    def get_status(self, service_name: str) -> dict[str, Any]:
        """Get current circuit status for monitoring."""
        with self._lock:
            stats = self._circuits.get(service_name)
            if stats is None:
                return {"service": service_name, "state": "closed", "failures": 0}
            return {
                "service": service_name,
                "state": stats.state.value,
                "failures": stats.failures,
                "successes": stats.successes,
                "last_failure": stats.last_failure_time,
                "last_success": stats.last_success_time,
            }

    def get_all_status(self) -> list[dict[str, Any]]:
        """Get status of all tracked circuits."""
        with self._lock:
            return [self.get_status(name) for name in self._circuits]

    def _get_or_create(self, service_name: str) -> CircuitStats:
        if service_name not in self._circuits:
            self._circuits[service_name] = CircuitStats()
        return self._circuits[service_name]


# Module-level singleton
circuit_breaker = CircuitBreaker()
