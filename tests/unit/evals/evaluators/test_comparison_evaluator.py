from __future__ import annotations

from unittest import mock

import pytest

from sentinel.evals import types
from sentinel.evals.evaluators import comparison_evaluator


def _make_ctx(case_payload: dict) -> mock.MagicMock:
    ctx = mock.MagicMock()
    ctx.inputs = types.InputData(agent_name="k8s_investigation", case_payload=case_payload)
    ctx.output = ""
    return ctx


class TestFindingsKeywordCoverage:
    @pytest.mark.asyncio
    async def test_passes_when_all_keywords_found(self) -> None:
        # Given an evaluator checking for OOM-related keywords
        ev = comparison_evaluator.FindingsKeywordCoverage(
            field_path="alert.description",
            keywords=("OOMKilled", "memory"),
            threshold=0.8,
        )
        ctx = _make_ctx({"alert": {"description": "Pod OOMKilled due to memory pressure"}})

        # When evaluated
        result = await ev.evaluate(ctx)

        # Then the assertion passes
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is True

    @pytest.mark.asyncio
    async def test_fails_when_keywords_missing(self) -> None:
        # Given keywords not in the text
        ev = comparison_evaluator.FindingsKeywordCoverage(
            field_path="alert.description",
            keywords=("OOMKilled", "memory", "restart"),
            threshold=0.8,
        )
        ctx = _make_ctx({"alert": {"description": "Pod is running normally"}})

        # When evaluated
        result = await ev.evaluate(ctx)

        # Then the assertion fails
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is False


class TestMinimumSourceCount:
    @pytest.mark.asyncio
    async def test_passes_when_enough_sources(self) -> None:
        # Given enough sources
        ev = comparison_evaluator.MinimumSourceCount(
            field_path="min_findings_count",
            actual_count_field="actual_findings_count",
        )
        ctx = _make_ctx({"min_findings_count": 1, "actual_findings_count": 3})

        # When evaluated
        result = await ev.evaluate(ctx)

        # Then the assertion passes
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is True


class TestLatencyThreshold:
    @pytest.mark.asyncio
    async def test_passes_when_under_threshold(self) -> None:
        # Given latency under threshold
        ev = comparison_evaluator.LatencyThreshold(
            threshold_field="max_latency_ms",
            actual_field="actual_latency_ms",
        )
        ctx = _make_ctx({"max_latency_ms": 5000, "actual_latency_ms": 3200})

        # When evaluated
        result = await ev.evaluate(ctx)

        # Then the assertion passes
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is True

    @pytest.mark.asyncio
    async def test_fails_when_over_threshold(self) -> None:
        # Given latency exceeds threshold
        ev = comparison_evaluator.LatencyThreshold(
            threshold_field="max_latency_ms",
            actual_field="actual_latency_ms",
        )
        ctx = _make_ctx({"max_latency_ms": 5000, "actual_latency_ms": 7500})

        # When evaluated
        result = await ev.evaluate(ctx)

        # Then the assertion fails
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is False
