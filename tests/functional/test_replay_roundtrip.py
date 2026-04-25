"""
Golden round-trip test: verify replay snapshot data flows correctly
from pipeline execution through to ReplayBundle reconstruction.

Tests the three-layer chain: ExecutionTracer -> operations -> ReplayBundle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from sentinel.domain import prompts
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import operations as pipeline_ops
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.pipeline import tracer as pipeline_tracer


class TestReplayRoundTrip:
    """Verify snapshot data survives the persist -> fetch -> ReplayBundle cycle."""

    @pytest.fixture
    def captured_persist_args(self) -> list[dict[str, Any]]:
        """Accumulator for args passed to persist_pipeline_run."""
        return []

    @pytest.fixture
    def captured_complete_args(self) -> list[dict[str, Any]]:
        """Accumulator for args passed to complete_pipeline_run."""
        return []

    @pytest.fixture
    def mock_db(self) -> mock.AsyncMock:
        """A mock database that no-ops."""
        return mock.AsyncMock()

    @pytest.fixture
    def tracer(self, mock_db: mock.AsyncMock) -> pipeline_tracer.ExecutionTracer:
        """Build an ExecutionTracer backed by a mock database."""
        return pipeline_tracer.ExecutionTracer(db=mock_db)

    async def test_start_pipeline_persists_all_snapshot_fields(
        self,
        tracer: pipeline_tracer.ExecutionTracer,
        captured_persist_args: list[dict[str, Any]],
    ) -> None:
        """Verify start_pipeline passes every snapshot field to persist_pipeline_run."""
        # Given a loaded prompt template and a spy on persist_pipeline_run
        prompt_tpl = prompts.load_template("alert_classifier")

        async def _spy_persist(**kwargs: Any) -> uuid.UUID:
            captured_persist_args.append(kwargs)
            return uuid.uuid4()

        # When start_pipeline is called with all snapshot metadata
        with mock.patch.object(pipeline_ops, "persist_pipeline_run", side_effect=_spy_persist):
            await tracer.start_pipeline(
                pipeline_type="investigation",
                input_data={"alert_id": "P123"},
                input_hash="abc123",
                model_ids_json=["openai/gpt-4.1-mini", "openai/gpt-4.1"],
                mcp_endpoints_json=[],
                skill_activations_json=[],
                prompt_version=prompt_tpl.version,
                prompt_sha256=prompt_tpl.sha256,
                prompt_text=prompt_tpl.system_text,
            )

        # Then all snapshot fields are forwarded to the persistence layer
        assert len(captured_persist_args) == 1
        persisted = captured_persist_args[0]
        assert persisted["pipeline_type"] == "investigation"
        assert persisted["input_json"] == {"alert_id": "P123"}
        assert persisted["input_hash"] == "abc123"
        assert persisted["model_ids_json"] == ["openai/gpt-4.1-mini", "openai/gpt-4.1"]
        assert persisted["mcp_endpoints_json"] == []
        assert persisted["skill_activations_json"] == []
        assert persisted["prompt_version"] == prompt_tpl.version
        assert persisted["prompt_sha256"] == prompt_tpl.sha256
        assert persisted["prompt_text"] == prompt_tpl.system_text

    async def test_complete_pipeline_persists_final_reply(
        self,
        tracer: pipeline_tracer.ExecutionTracer,
        captured_complete_args: list[dict[str, Any]],
    ) -> None:
        """Verify complete_pipeline forwards final_reply to the persistence layer."""
        # Given a pipeline that has already started
        with mock.patch.object(pipeline_ops, "persist_pipeline_run", return_value=uuid.uuid4()):
            await tracer.start_pipeline(pipeline_type="investigation")

        final_reply = {"alert_id": "P123", "root_cause": "OOM"}

        async def _spy_complete(**kwargs: Any) -> None:
            captured_complete_args.append(kwargs)

        # When complete_pipeline is called with a final_reply
        with mock.patch.object(pipeline_ops, "complete_pipeline_run", side_effect=_spy_complete):
            await tracer.complete_pipeline(
                status="completed",
                output_data=final_reply,
                final_reply=final_reply,
            )

        # Then the final_reply is forwarded to complete_pipeline_run
        assert len(captured_complete_args) == 1
        assert captured_complete_args[0]["final_reply"] == final_reply
        assert captured_complete_args[0]["status"] == "completed"

    async def test_replay_bundle_reconstructs_from_db_row(self) -> None:
        """Verify fetch_replay_bundle maps all DB columns into a ReplayBundle."""
        # Given a pipeline_runs row with all snapshot fields populated
        run_id = uuid.uuid4()
        prompt_tpl = prompts.load_template("alert_classifier")
        started = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        completed = datetime(2026, 4, 12, 12, 0, 30, tzinfo=UTC)
        model_ids = ["openai/gpt-4.1-mini", "openai/gpt-4.1"]
        input_payload = {"id": "P123", "title": "High CPU"}
        final_reply = {"alert_id": "P123", "root_cause": "OOM"}

        db_row_mapping = {
            "id": run_id,
            "pipeline_type": "investigation",
            "started_at": started,
            "completed_at": completed,
            "prompt_version": prompt_tpl.version,
            "prompt_sha256": prompt_tpl.sha256,
            "prompt_text": prompt_tpl.system_text,
            "input_hash": "cafebabe" * 8,
            "model_ids_json": model_ids,
            "mcp_endpoints_json": [],
            "skill_activations_json": [],
            "final_reply": final_reply,
            "input_json": input_payload,
        }

        mock_row = mock.MagicMock()
        mock_row._mapping = db_row_mapping
        mock_db = mock.AsyncMock()
        mock_db.fetch_one = mock.AsyncMock(return_value=mock_row)

        # When fetch_replay_bundle is called
        bundle = await pipeline_queries.fetch_replay_bundle(db=mock_db, run_id=run_id)

        # Then the ReplayBundle contains all snapshot values
        assert bundle.run_id == run_id
        assert bundle.pipeline_type == "investigation"
        assert bundle.started_at == started
        assert bundle.completed_at == completed
        assert bundle.prompt_version == prompt_tpl.version
        assert bundle.prompt_sha256 == prompt_tpl.sha256
        assert bundle.prompt_text == prompt_tpl.system_text
        assert bundle.input_hash == "cafebabe" * 8
        assert bundle.model_ids == ("openai/gpt-4.1-mini", "openai/gpt-4.1")
        assert bundle.mcp_endpoints == ()
        assert bundle.skill_activations == ()
        assert bundle.final_reply == final_reply
        assert bundle.input_payload == input_payload

    async def test_replay_bundle_not_found_raises(self) -> None:
        """Verify fetch_replay_bundle raises when the DB returns no row."""
        # Given a mock database that returns None for fetch_one
        missing_id = uuid.uuid4()
        mock_db = mock.AsyncMock()
        mock_db.fetch_one = mock.AsyncMock(return_value=None)

        # When fetch_replay_bundle is called with a nonexistent run_id
        # Then ReplayBundleNotFoundError is raised

        with pytest.raises(pipeline_errors.ReplayBundleNotFoundError):
            await pipeline_queries.fetch_replay_bundle(db=mock_db, run_id=missing_id)

    async def test_prompt_sha256_is_stable_across_loads(self) -> None:
        """Verify load_template returns cached identity with stable SHA-256."""
        # Given the same template loaded twice
        first = prompts.load_template("alert_classifier")
        second = prompts.load_template("alert_classifier")

        # When comparing SHA-256 digests
        # Then they are identical (content-addressable and cached)
        assert first.sha256 == second.sha256
        assert first is second

    async def test_canonical_input_hash_excludes_volatile_fields(self) -> None:
        """Verify two payloads differing only in volatile fields hash identically."""
        # Given two payloads that differ only in the timestamp field
        payload_with_early_ts = {
            "id": "P123",
            "title": "High CPU",
            "timestamp": "2026-04-12T12:00:00Z",
        }
        payload_with_late_ts = {
            "id": "P123",
            "title": "High CPU",
            "timestamp": "2026-04-12T13:00:00Z",
        }

        # When computing canonical input hashes
        hash_early = pipeline_queries.canonical_input_hash(payload=payload_with_early_ts)
        hash_late = pipeline_queries.canonical_input_hash(payload=payload_with_late_ts)

        # Then both produce the same hash
        assert hash_early == hash_late

    async def test_canonical_input_hash_differs_for_distinct_payloads(self) -> None:
        """Verify payloads with different non-volatile fields produce different hashes."""
        # Given two payloads with different alert titles
        cpu_alert = {"id": "P123", "title": "High CPU"}
        mem_alert = {"id": "P123", "title": "High Memory"}

        # When computing canonical input hashes
        hash_cpu = pipeline_queries.canonical_input_hash(payload=cpu_alert)
        hash_mem = pipeline_queries.canonical_input_hash(payload=mem_alert)

        # Then the hashes differ
        assert hash_cpu != hash_mem

    async def test_tracer_noop_when_db_is_none(self) -> None:
        """Verify the tracer generates IDs but skips persistence when db is None."""
        # Given a tracer with no database
        tracer = pipeline_tracer.ExecutionTracer(db=None)

        # When starting and completing a pipeline
        await tracer.start_pipeline(
            pipeline_type="investigation",
            input_data={"alert_id": "P123"},
        )
        await tracer.complete_pipeline(
            status="completed",
            output_data={"root_cause": "OOM"},
        )

        # Then a pipeline_run_id is still generated for correlation
        assert tracer.pipeline_run_id is not None
        assert tracer.trace_id is not None
