"""Tests for the RFC §3.8 ReplayBundle frozen-attrs contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import attrs
import pytest

from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


def _make_tool_entry(
    *,
    tool_name: str = "kubectl_logs",
    inputs: dict | None = None,
    outputs: object = "log line",
    evidence_object_id: str | None = None,
    at: datetime | None = None,
) -> bundle_mod.ToolIOEntry:
    return bundle_mod.ToolIOEntry(
        tool_name=tool_name,
        inputs=inputs or {"namespace": "ns-a"},
        outputs=outputs,
        evidence_object_id=evidence_object_id,
        at=at or datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    )


def _make_llm_entry(
    *,
    agent_name: str = "alert_classifier",
    model_id: str = "openai/gpt-4.1-mini",
    inputs: dict | None = None,
    outputs: object = "classified",
    token_usage: dict | None = None,
    at: datetime | None = None,
) -> bundle_mod.LLMIOEntry:
    return bundle_mod.LLMIOEntry(
        agent_name=agent_name,
        model_id=model_id,
        inputs=inputs or {"prompt": "Classify"},
        outputs=outputs,
        token_usage=token_usage,
        at=at or datetime(2026, 4, 25, 12, 0, 5, tzinfo=UTC),
    )


def _make_bundle(
    *,
    tool_io: tuple[bundle_mod.ToolIOEntry, ...] = (),
    llm_io: tuple[bundle_mod.LLMIOEntry, ...] = (),
    final_outputs: dict | None = None,
    runbook_id: str | None = "k8s-crashloop",
    runbook_version_sha: str | None = "abc123def456",
    alert_payload: dict | None = None,
) -> bundle_mod.ReplayBundle:
    return bundle_mod.ReplayBundle(
        envelope=factories.make_envelope(),
        alert_payload=alert_payload or {"alert_id": "P123", "title": "CrashLoop"},
        runbook_id=runbook_id,
        runbook_version_sha=runbook_version_sha,
        tool_io=tool_io,
        llm_io=llm_io,
        final_outputs=final_outputs or {"root_cause": "OOM"},
    )


class TestToolIOEntry:
    def test_is_immutable_and_kw_only(self):
        # Given a ToolIOEntry built via kwargs
        entry = _make_tool_entry()

        # When trying to mutate a field
        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            entry.tool_name = "other"  # type: ignore[misc]

    def test_evidence_object_id_defaults_to_none(self):
        # Given a ToolIOEntry constructed without an evidence id
        entry = _make_tool_entry(evidence_object_id=None)

        # Then evidence_object_id is None
        assert entry.evidence_object_id is None


class TestLLMIOEntry:
    def test_token_usage_defaults_to_none(self):
        # Given an LLMIOEntry without token usage
        entry = _make_llm_entry(token_usage=None)

        # Then token_usage is None
        assert entry.token_usage is None


class TestReplayBundleSchema:
    def test_collects_envelope_alert_runbook_and_outputs(self):
        # Given a bundle assembled from envelope + alert + runbook + outputs
        bundle = _make_bundle()

        # Then each field is preserved
        assert bundle.envelope.tenant_id == "pm-default"
        assert bundle.alert_payload["alert_id"] == "P123"
        assert bundle.runbook_id == "k8s-crashloop"
        assert bundle.runbook_version_sha == "abc123def456"
        assert bundle.final_outputs == {"root_cause": "OOM"}
        assert bundle.tool_io == ()
        assert bundle.llm_io == ()

    def test_tool_and_llm_io_are_tuples(self):
        # Given a bundle with one tool call and one LLM call recorded
        bundle = _make_bundle(
            tool_io=(_make_tool_entry(),),
            llm_io=(_make_llm_entry(),),
        )

        # Then tool_io and llm_io are tuples (immutable)
        assert isinstance(bundle.tool_io, tuple)
        assert isinstance(bundle.llm_io, tuple)
        assert len(bundle.tool_io) == 1
        assert len(bundle.llm_io) == 1


class TestBundleSha:
    def test_is_deterministic_for_identical_inputs(self):
        # Given two bundles built from identical inputs
        first = _make_bundle(
            tool_io=(_make_tool_entry(),),
            llm_io=(_make_llm_entry(),),
        )
        second = _make_bundle(
            tool_io=(_make_tool_entry(),),
            llm_io=(_make_llm_entry(),),
        )

        # When their bundle_sha values are compared
        # Then they match
        assert first.bundle_sha == second.bundle_sha

    def test_changes_when_final_outputs_change(self):
        # Given a baseline bundle and one with different final outputs
        baseline = _make_bundle(final_outputs={"root_cause": "OOM"})
        drifted = _make_bundle(final_outputs={"root_cause": "OOM (revised)"})

        # When their bundle_sha values are compared
        # Then they differ
        assert baseline.bundle_sha != drifted.bundle_sha

    def test_changes_when_tool_io_changes(self):
        # Given a bundle with one recorded tool call and another with a
        # different tool call
        baseline = _make_bundle(tool_io=(_make_tool_entry(tool_name="foo"),))
        drifted = _make_bundle(tool_io=(_make_tool_entry(tool_name="bar"),))

        # When their bundle_sha values are compared
        # Then they differ
        assert baseline.bundle_sha != drifted.bundle_sha

    def test_changes_when_llm_io_changes(self):
        # Given a bundle with one LLM call and another with a different model
        baseline = _make_bundle(llm_io=(_make_llm_entry(model_id="m-1"),))
        drifted = _make_bundle(llm_io=(_make_llm_entry(model_id="m-2"),))

        # When their bundle_sha values are compared
        # Then they differ
        assert baseline.bundle_sha != drifted.bundle_sha

    def test_changes_when_runbook_version_changes(self):
        # Given two bundles differing only by runbook_version_sha
        baseline = _make_bundle(runbook_version_sha="v1")
        drifted = _make_bundle(runbook_version_sha="v2")

        # When their bundle_sha values are compared
        # Then they differ
        assert baseline.bundle_sha != drifted.bundle_sha

    def test_returns_64_char_hex_sha256(self):
        # Given a bundle
        bundle = _make_bundle()

        # When bundle_sha is computed
        sha = bundle.bundle_sha

        # Then it is a 64-character lowercase hex string (sha256)
        assert isinstance(sha, str)
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)

    def test_is_insensitive_to_dict_key_order(self):
        # Given two bundles whose alert_payload has the same content but
        # different insertion order
        first = _make_bundle(alert_payload={"a": 1, "b": 2})
        second = _make_bundle(alert_payload={"b": 2, "a": 1})

        # When their bundle_sha values are compared
        # Then they match (canonical JSON sorts keys)
        assert first.bundle_sha == second.bundle_sha


class TestReplayBundleBuilder:
    def test_records_tool_calls_in_order(self):
        # Given a builder with three tool entries appended
        builder = bundle_mod.ReplayBundleBuilder()
        first = _make_tool_entry(tool_name="first")
        second = _make_tool_entry(tool_name="second")
        third = _make_tool_entry(tool_name="third")
        builder.record_tool_io(first)
        builder.record_tool_io(second)
        builder.record_tool_io(third)

        # When build() snapshots the accumulated tool I/O
        bundle = builder.build(
            envelope=factories.make_envelope(),
            alert_payload={"alert_id": "P1"},
            runbook_id=None,
            runbook_version_sha=None,
            final_outputs={},
        )

        # Then ordering is preserved
        assert tuple(e.tool_name for e in bundle.tool_io) == ("first", "second", "third")

    def test_records_llm_calls_in_order(self):
        # Given a builder with two LLM entries
        builder = bundle_mod.ReplayBundleBuilder()
        builder.record_llm_io(_make_llm_entry(agent_name="classifier"))
        builder.record_llm_io(_make_llm_entry(agent_name="analyser"))

        # When build() snapshots the accumulated LLM I/O
        bundle = builder.build(
            envelope=factories.make_envelope(),
            alert_payload={},
            runbook_id=None,
            runbook_version_sha=None,
            final_outputs={},
        )

        # Then ordering is preserved
        assert tuple(e.agent_name for e in bundle.llm_io) == ("classifier", "analyser")

    def test_build_returns_immutable_bundle(self):
        # Given a builder snapshot
        builder = bundle_mod.ReplayBundleBuilder()
        bundle = builder.build(
            envelope=factories.make_envelope(),
            alert_payload={},
            runbook_id=None,
            runbook_version_sha=None,
            final_outputs={},
        )

        # Then bundle.tool_io is a tuple (immutable snapshot)
        assert bundle.tool_io == ()
        assert bundle.llm_io == ()
        assert isinstance(bundle.tool_io, tuple)


class TestSerialize:
    def test_to_canonical_json_sorts_keys_and_serialises_datetimes(self):
        # Given a bundle with a datetime-bearing tool entry
        bundle = _make_bundle(tool_io=(_make_tool_entry(),))

        # When the bundle is serialised to canonical JSON
        canonical = bundle_mod.to_canonical_json(bundle)

        # Then it is a string parsable as JSON containing the alert payload
        parsed = json.loads(canonical)
        assert parsed["alert_payload"]["alert_id"] == "P123"
        # And keys are sorted (deterministic field order)
        assert list(parsed.keys()) == sorted(parsed.keys())
