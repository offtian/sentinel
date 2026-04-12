"""
Unit tests for the replay CLI scaffold (sentinel.replay).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime

import pytest

from sentinel import replay as replay_mod
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.pipeline import types as pipeline_types


class _FakeDB:
    """Minimal async-context no-op stand-in for databases.Database."""

    def __init__(self, url: str) -> None:
        self._url = url

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass


class TestMain:
    def test_happy_path_prints_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given a ReplayBundle returned by the query layer
        fake_run_id = uuid.uuid4()
        fake_bundle = pipeline_types.ReplayBundle(
            run_id=fake_run_id,
            pipeline_type="sre_investigation",
            started_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
            completed_at=None,
            prompt_version="abc123:alert",
            prompt_sha256="deadbeef",
            prompt_text="You are a helpful SRE.",
            input_hash="cafebabe",
            model_ids=("openai/gpt-4.1",),
            mcp_endpoints=(),
            skill_activations=(),
            final_reply=None,
            input_payload=None,
        )

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
        assert parsed["pipeline_type"] == "sre_investigation"
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
