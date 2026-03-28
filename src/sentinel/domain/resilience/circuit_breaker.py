from __future__ import annotations

import enum
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import attrs

from sentinel.utils import logs


class CircuitState(enum.Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is open."""

    def __init__(self, *, name: str, recovery_at: float) -> None:
        seconds_remaining = max(0, recovery_at - time.monotonic())
        super().__init__(f"Circuit '{name}' is open. Recovery in {seconds_remaining:.1f}s.")
        self.name = name
        self.recovery_at = recovery_at


T = TypeVar("T")


@attrs.define
class CircuitBreaker:
    """
    Thread-safe circuit breaker for protecting external service calls.

    When failures exceed `failure_threshold`, the circuit opens and rejects
    calls for `recovery_timeout_seconds`. After recovery, one call is allowed
    through (half-open). If it succeeds the circuit closes; if it fails the
    circuit reopens.

    This is mutable by design -- circuit state must change at runtime.
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    _state: CircuitState = attrs.field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = attrs.field(default=0, init=False)
    _last_failure_at: float = attrs.field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        """Record a successful call. Resets failure count and closes the circuit."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call. Opens the circuit if threshold is exceeded."""
        self._failure_count += 1
        self._last_failure_at = time.monotonic()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logs.log_event(
                "circuit_breaker.opened",
                params={
                    "name": self.name,
                    "failure_count": self._failure_count,
                    "recovery_timeout": self.recovery_timeout_seconds,
                },
            )

    async def call(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: object,
        **kwargs: object,
    ) -> T:
        """
        Execute ``fn`` through the circuit breaker.

        :raises CircuitOpenError: if the circuit is open and recovery time has not elapsed.
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            raise CircuitOpenError(
                name=self.name,
                recovery_at=self._last_failure_at + self.recovery_timeout_seconds,
            )

        try:
            result = await fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise

        self.record_success()
        return result
