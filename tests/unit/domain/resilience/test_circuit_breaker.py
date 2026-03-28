from __future__ import annotations

import time

import pytest

from sentinel.domain.resilience import circuit_breaker


class TestCircuitBreaker:
    def test_starts_closed(self):
        # Given a new circuit breaker
        cb = circuit_breaker.CircuitBreaker(name="test")

        # Then the state is CLOSED
        assert cb.state == circuit_breaker.CircuitState.CLOSED

    def test_stays_closed_on_success(self):
        # Given a circuit breaker
        cb = circuit_breaker.CircuitBreaker(name="test")

        # When a success is recorded
        cb.record_success()

        # Then the state remains CLOSED
        assert cb.state == circuit_breaker.CircuitState.CLOSED

    def test_opens_after_threshold_failures(self):
        # Given a circuit breaker with threshold of 3
        cb = circuit_breaker.CircuitBreaker(name="test", failure_threshold=3)

        # When 3 failures are recorded
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()

        # Then the circuit is OPEN
        assert cb.state == circuit_breaker.CircuitState.OPEN

    def test_remains_closed_below_threshold(self):
        # Given a circuit breaker with threshold of 3
        cb = circuit_breaker.CircuitBreaker(name="test", failure_threshold=3)

        # When only 2 failures are recorded
        cb.record_failure()
        cb.record_failure()

        # Then the circuit is still CLOSED
        assert cb.state == circuit_breaker.CircuitState.CLOSED

    def test_success_resets_failure_count(self):
        # Given a circuit breaker with 2 failures (threshold=3)
        cb = circuit_breaker.CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()

        # When a success is recorded
        cb.record_success()

        # Then 2 more failures do not open the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == circuit_breaker.CircuitState.CLOSED

    def test_transitions_to_half_open_after_recovery_timeout(self):
        # Given an open circuit breaker with very short recovery
        cb = circuit_breaker.CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout_seconds=0.01,
        )
        cb.record_failure()
        assert cb.state == circuit_breaker.CircuitState.OPEN

        # When the recovery timeout elapses
        time.sleep(0.02)

        # Then the state transitions to HALF_OPEN
        assert cb.state == circuit_breaker.CircuitState.HALF_OPEN

    def test_success_in_half_open_closes_circuit(self):
        # Given a half-open circuit breaker
        cb = circuit_breaker.CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout_seconds=0.01,
        )
        cb.record_failure()
        time.sleep(0.02)
        assert cb.state == circuit_breaker.CircuitState.HALF_OPEN

        # When a success is recorded
        cb.record_success()

        # Then the circuit closes
        assert cb.state == circuit_breaker.CircuitState.CLOSED


class TestCircuitBreakerCall:
    @pytest.mark.asyncio
    async def test_call_succeeds_when_closed(self):
        # Given a closed circuit breaker
        cb = circuit_breaker.CircuitBreaker(name="test")

        # When calling a successful function
        async def _succeed() -> str:
            return "ok"

        result = await cb.call(_succeed)

        # Then the result is returned
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_call_raises_circuit_open_error_when_open(self):
        # Given an open circuit breaker
        cb = circuit_breaker.CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout_seconds=60,
        )
        cb.record_failure()

        # When calling through the circuit
        async def _fn() -> str:
            return "should not reach"

        # Then CircuitOpenError is raised
        with pytest.raises(circuit_breaker.CircuitOpenError):
            await cb.call(_fn)

    @pytest.mark.asyncio
    async def test_call_records_failure_on_exception(self):
        # Given a circuit breaker with threshold=2
        cb = circuit_breaker.CircuitBreaker(name="test", failure_threshold=2)

        async def _fail() -> str:
            raise RuntimeError("boom")

        # When two calls fail
        with pytest.raises(RuntimeError):
            await cb.call(_fail)
        with pytest.raises(RuntimeError):
            await cb.call(_fail)

        # Then the circuit opens
        assert cb.state == circuit_breaker.CircuitState.OPEN
