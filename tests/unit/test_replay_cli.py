"""
Unit tests for the replay CLI (sentinel.replay).

Tests use the F4.7+ :class:`sentinel.utils.replay_bundle.ReplayBundle`
shape (envelope + alert_payload + final_outputs + tool_io + llm_io)
rather than the legacy ``sentinel.domain.pipeline.types.ReplayBundle``
shape that the CLI consumed before the F4 refactor.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any
from unittest import mock

import pytest

from sentinel import replay as replay_mod
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.plugins.toolsets import recorded as recorded_toolset_mod
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


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
    alert_payload: dict[str, Any] | None = None,
    final_outputs: dict[str, Any] | None = None,
    runbook_id: str | None = None,
    runbook_version_sha: str | None = None,
) -> bundle_mod.ReplayBundle:
    """Build an F4.7 ReplayBundle with sensible defaults for testing."""
    return bundle_mod.ReplayBundle(
        envelope=factories.make_envelope(),
        alert_payload=alert_payload or {},
        runbook_id=runbook_id,
        runbook_version_sha=runbook_version_sha,
        tool_io=(),
        llm_io=(),
        final_outputs=final_outputs or {},
    )


def _make_recorded_toolset() -> recorded_toolset_mod.RecordedToolset:
    """Build an empty RecordedToolset; replay tests mock the pipeline call site."""
    return recorded_toolset_mod.RecordedToolset(())


class TestMain:
    def test_happy_path_prints_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given a ReplayBundle returned by the recorded-bundle query layer
        fake_run_id = uuid.uuid4()
        fake_bundle = _make_bundle(
            alert_payload={"id": "alert-123", "service": "api"},
            runbook_id="k8s-crashloop",
        )

        async def _fake_fetch(*, db: object, run_id: uuid.UUID) -> bundle_mod.ReplayBundle:
            return fake_bundle

        monkeypatch.setattr(pipeline_queries, "fetch_recorded_replay_bundle", _fake_fetch)
        monkeypatch.setattr("sentinel.replay.databases.Database", _FakeDB)
        monkeypatch.setattr(sys, "argv", ["replay", str(fake_run_id)])

        # When main() is called with a valid run_id
        replay_mod.main()

        # Then stdout carries the canonical JSON of the bundle, and stderr
        # carries the bundle_sha — _print_bundle's contract per F4.7
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["alert_payload"] == {"id": "alert-123", "service": "api"}
        assert parsed["runbook_id"] == "k8s-crashloop"
        assert "bundle_sha" in captured.err

    def test_not_found_exits_with_code_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given fetch_recorded_replay_bundle raises ReplayBundleNotFoundError
        missing_run_id = uuid.uuid4()

        async def _raise_not_found(*, db: object, run_id: uuid.UUID) -> bundle_mod.ReplayBundle:
            raise pipeline_errors.ReplayBundleNotFoundError(run_id)

        monkeypatch.setattr(pipeline_queries, "fetch_recorded_replay_bundle", _raise_not_found)
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

    def test_exits_3_when_original_is_none(self, capsys: pytest.CaptureFixture[str]) -> None:
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
        # Given a bundle with SRE alert payload and a mocked investigate_alert
        fake_reply = mock.MagicMock()
        fake_reply.model_dump_json.return_value = json.dumps(
            {"alert_id": "alert-123", "root_cause": "disk full"}
        )

        fake_bundle = _make_bundle(
            alert_payload={
                "id": "alert-123",
                "source": "pagerduty",
                "title": "High CPU",
                "description": "CPU at 99%",
                "severity": "critical",
                "service": "api-server",
                "triggered_at": "2024-06-01T12:00:00Z",
            },
        )

        async def _fake_investigate(**kwargs: Any) -> object:
            return fake_reply

        monkeypatch.setattr(
            "sentinel.replay.investigation.investigate_alert",
            _fake_investigate,
        )

        fake_cfg = mock.MagicMock()
        fake_cfg.build_holmes_adapter.return_value = mock.MagicMock()
        fake_cfg.build_k8s_investigation_adapter.return_value = mock.MagicMock()
        fake_cfg.build_challenger_adapter.return_value = mock.MagicMock()

        # When _replay_sre is called with the F4 contract
        result = await replay_mod._replay_sre(
            bundle=fake_bundle,
            cfg=fake_cfg,
            recorded_toolset=_make_recorded_toolset(),
        )

        # Then the result reflects the mocked pipeline reply (canonical JSON dict)
        assert result == {"alert_id": "alert-123", "root_cause": "disk full"}


class TestReplaySupport:
    @pytest.mark.asyncio
    async def test_replays_support_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given a bundle with support ticket payload and a mocked review_ticket
        fake_reply = mock.MagicMock()
        fake_reply.model_dump_json.return_value = json.dumps(
            {"ticket_id": "t-1", "ticket_key": "SUPPORT-1"}
        )

        fake_bundle = _make_bundle(
            alert_payload={
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

        async def _fake_review(**kwargs: Any) -> object:
            return fake_reply

        monkeypatch.setattr(
            "sentinel.replay.support_review.review_ticket",
            _fake_review,
        )

        fake_cfg = mock.MagicMock()
        fake_cfg.build_document_searcher.return_value = mock.MagicMock()
        fake_cfg.build_ticket_searcher.return_value = mock.MagicMock()

        # When _replay_support is called with the F4 contract
        result = await replay_mod._replay_support(
            bundle=fake_bundle,
            cfg=fake_cfg,
            recorded_toolset=_make_recorded_toolset(),
        )

        # Then the result reflects the mocked pipeline reply (canonical JSON dict)
        assert result == {"ticket_id": "t-1", "ticket_key": "SUPPORT-1"}
