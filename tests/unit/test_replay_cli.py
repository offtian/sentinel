"""
Unit tests for the replay CLI (sentinel.replay).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from sentinel import replay as replay_mod
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.pipeline import tracer as pipeline_tracer
from sentinel.domain.pipeline import types as pipeline_types


class _FakeDB:
    """Minimal async-context no-op stand-in for databases.Database."""

    def __init__(self, url: str) -> None:
        self._url = url

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def execute(self, query: object) -> None:
        pass


def _make_bundle(
    *,
    run_id: uuid.UUID | None = None,
    pipeline_type: str = "investigation",
    final_reply: dict[str, Any] | None = None,
    input_payload: dict[str, Any] | None = None,
) -> pipeline_types.ReplayBundle:
    """Build a ReplayBundle with sensible defaults for testing."""
    return pipeline_types.ReplayBundle(
        run_id=run_id or uuid.uuid4(),
        pipeline_type=pipeline_type,
        started_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        completed_at=None,
        prompt_version="abc123:alert",
        prompt_sha256="deadbeef",
        prompt_text="You are a helpful SRE.",
        input_hash="cafebabe",
        model_ids=("openai/gpt-4.1",),
        mcp_endpoints=(),
        skill_activations=(),
        final_reply=final_reply,
        input_payload=input_payload,
    )


class TestMain:
    def test_happy_path_prints_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given a ReplayBundle returned by the query layer
        fake_run_id = uuid.uuid4()
        fake_bundle = _make_bundle(run_id=fake_run_id)

        async def _fake_fetch_replay_bundle(
            *, db: object, run_id: uuid.UUID
        ) -> pipeline_types.ReplayBundle:
            return fake_bundle

        monkeypatch.setattr(pipeline_queries, "fetch_replay_bundle", _fake_fetch_replay_bundle)
        monkeypatch.setattr("sentinel.replay.databases.Database", _FakeDB)
        monkeypatch.setattr(sys, "argv", ["replay", str(fake_run_id)])

        # When main() is called with a valid run_id
        replay_mod.main()

        # Then the bundle is printed as valid JSON with the expected pipeline_type
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["pipeline_type"] == "investigation"
        assert parsed["run_id"] == str(fake_run_id)

    def test_not_found_exits_with_code_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given fetch_replay_bundle raises ReplayBundleNotFoundError
        missing_run_id = uuid.uuid4()

        async def _raise_not_found(
            *, db: object, run_id: uuid.UUID
        ) -> pipeline_types.ReplayBundle:
            raise pipeline_errors.ReplayBundleNotFoundError(run_id)

        monkeypatch.setattr(pipeline_queries, "fetch_replay_bundle", _raise_not_found)
        monkeypatch.setattr("sentinel.replay.databases.Database", _FakeDB)
        monkeypatch.setattr(sys, "argv", ["replay", str(missing_run_id)])

        # When main() is called for a non-existent run_id
        with pytest.raises(SystemExit) as exc_info:
            replay_mod.main()

        # Then the process exits with code 1
        assert exc_info.value.code == 1


class TestReplayFlag:
    def test_replay_flag_calls_replay_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the --replay flag and a monkeypatched _replay_pipeline
        fake_run_id = uuid.uuid4()
        captured_calls: list[dict[str, Any]] = []

        async def _fake_replay(*, run_id: uuid.UUID, show_diff: bool) -> None:
            captured_calls.append({"run_id": run_id, "show_diff": show_diff})

        monkeypatch.setattr(replay_mod, "_replay_pipeline", _fake_replay)
        monkeypatch.setattr(sys, "argv", ["replay", str(fake_run_id), "--replay"])

        # When main() is called with the --replay flag
        replay_mod.main()

        # Then _replay_pipeline is invoked with the correct run_id and show_diff=False
        assert len(captured_calls) == 1
        assert captured_calls[0]["run_id"] == fake_run_id
        assert captured_calls[0]["show_diff"] is False

    def test_diff_flag_calls_replay_pipeline_with_show_diff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the --diff flag and a monkeypatched _replay_pipeline
        fake_run_id = uuid.uuid4()
        captured_calls: list[dict[str, Any]] = []

        async def _fake_replay(*, run_id: uuid.UUID, show_diff: bool) -> None:
            captured_calls.append({"run_id": run_id, "show_diff": show_diff})

        monkeypatch.setattr(replay_mod, "_replay_pipeline", _fake_replay)
        monkeypatch.setattr(sys, "argv", ["replay", str(fake_run_id), "--diff"])

        # When main() is called with the --diff flag
        replay_mod.main()

        # Then _replay_pipeline is invoked with show_diff=True
        assert len(captured_calls) == 1
        assert captured_calls[0]["run_id"] == fake_run_id
        assert captured_calls[0]["show_diff"] is True

    def test_not_found_with_replay_flag_exits_with_code_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given _replay_pipeline raises ReplayBundleNotFoundError
        missing_run_id = uuid.uuid4()

        async def _raise_not_found(*, run_id: uuid.UUID, show_diff: bool) -> None:
            raise pipeline_errors.ReplayBundleNotFoundError(run_id)

        monkeypatch.setattr(replay_mod, "_replay_pipeline", _raise_not_found)
        monkeypatch.setattr(sys, "argv", ["replay", str(missing_run_id), "--replay"])

        # When main() is called with --replay for a non-existent run_id
        with pytest.raises(SystemExit) as exc_info:
            replay_mod.main()

        # Then the process exits with code 1
        assert exc_info.value.code == 1


class TestPrintDiff:
    def test_exits_3_when_outputs_differ(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Given an original and replayed output that differ
        original = {"alert_id": "a1", "root_cause": "old cause"}
        replayed = {"alert_id": "a1", "root_cause": "new cause"}

        # When _print_diff is called with differing outputs
        with pytest.raises(SystemExit) as exc_info:
            replay_mod._print_diff(original=original, replayed=replayed)

        # Then exit code 3 signals drift and the diff is printed
        assert exc_info.value.code == 3
        captured = capsys.readouterr()
        assert "--- original" in captured.out
        assert "+++ replayed" in captured.out

    def test_exits_0_when_outputs_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Given identical original and replayed outputs
        identical = {"alert_id": "a1", "root_cause": "same cause"}

        # When _print_diff is called with matching outputs
        replay_mod._print_diff(original=identical, replayed=identical)

        # Then no exit is raised and a success message is printed
        captured = capsys.readouterr()
        assert "No differences found" in captured.out

    def test_treats_none_original_as_empty_dict(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Given a None original and a non-empty replayed output
        replayed = {"alert_id": "a1"}

        # When _print_diff is called with None original
        with pytest.raises(SystemExit) as exc_info:
            replay_mod._print_diff(original=None, replayed=replayed)

        # Then exit code 3 signals drift (empty dict vs non-empty)
        assert exc_info.value.code == 3


class TestReplaySre:
    @pytest.mark.asyncio
    async def test_replays_sre_investigation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given a bundle with SRE input payload and mocked pipeline
        fake_reply = pipeline_types.InvestigationReply(
            alert_id="alert-123",
            root_cause="disk full",
            findings_summary="Root cause identified",
        )

        fake_bundle = _make_bundle(
            pipeline_type="investigation",
            input_payload={
                "id": "alert-123",
                "source": "pagerduty",
                "title": "High CPU",
                "description": "CPU at 99%",
                "severity": "critical",
                "service": "api-server",
                "triggered_at": "2024-06-01T12:00:00Z",
            },
        )

        async def _fake_investigate(
            alert: object, **kwargs: Any
        ) -> pipeline_types.InvestigationReply:
            return fake_reply

        monkeypatch.setattr(
            "sentinel.replay.investigation.investigate_alert",
            _fake_investigate,
        )

        # Use db=None so ExecutionTracer stays in no-op mode
        _real_tracer = pipeline_tracer.ExecutionTracer

        def _noop_tracer(*, db: object) -> pipeline_tracer.ExecutionTracer:
            return _real_tracer(db=None)

        monkeypatch.setattr(pipeline_tracer, "ExecutionTracer", _noop_tracer)

        fake_cfg = mock.MagicMock()
        fake_db = _FakeDB("sqlite:///test")

        # When _replay_sre is called
        result = await replay_mod._replay_sre(
            bundle=fake_bundle,
            cfg=fake_cfg,
            db=fake_db,
        )

        # Then the result matches the fake pipeline reply
        assert result.alert_id == "alert-123"
        assert result.root_cause == "disk full"


class TestReplaySupport:
    @pytest.mark.asyncio
    async def test_replays_support_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given a bundle with support input payload and mocked pipeline
        fake_reply = pipeline_types.SupportReply(
            ticket_id="t-1",
            ticket_key="SUPPORT-1",
            suggested_response="Please try restarting.",
        )

        fake_bundle = _make_bundle(
            pipeline_type="support_review",
            input_payload={
                "id": "t-1",
                "key": "SUPPORT-1",
                "summary": "Cannot log in",
                "description": "Login fails with 403",
                "reporter": "user@example.com",
                "priority": "high",
                "created_at": "2024-06-01T12:00:00Z",
                "status": "open",
            },
        )

        async def _fake_review(ticket: object, **kwargs: Any) -> pipeline_types.SupportReply:
            return fake_reply

        monkeypatch.setattr(
            "sentinel.replay.support_review.review_ticket",
            _fake_review,
        )

        # Use db=None so ExecutionTracer stays in no-op mode
        _real_tracer = pipeline_tracer.ExecutionTracer

        def _noop_tracer(*, db: object) -> pipeline_tracer.ExecutionTracer:
            return _real_tracer(db=None)

        monkeypatch.setattr(pipeline_tracer, "ExecutionTracer", _noop_tracer)

        fake_cfg = mock.MagicMock()
        fake_db = _FakeDB("sqlite:///test")

        # When _replay_support is called
        result = await replay_mod._replay_support(
            bundle=fake_bundle,
            cfg=fake_cfg,
            db=fake_db,
        )

        # Then the result matches the fake pipeline reply
        assert result.ticket_id == "t-1"
        assert result.suggested_response == "Please try restarting."
