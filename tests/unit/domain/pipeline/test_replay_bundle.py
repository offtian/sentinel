"""
Unit tests for ReplayBundle and fetch_replay_bundle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import attrs
import pytest

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries
from sentinel.domain.pipeline import types as pipeline_types


class TestReplayBundleIsFrozen:
    def test_raises_on_attribute_assignment(self) -> None:
        # Given a fully-populated ReplayBundle
        bundle = pipeline_types.ReplayBundle(
            run_id=uuid.uuid4(),
            pipeline_type="investigation",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            prompt_version="v1.0.0",
            prompt_sha256="abc123",
            prompt_text="You are an SRE investigator.",
            input_hash="def456",
            model_ids=("openai/gpt-4.1",),
            mcp_endpoints=(),
            skill_activations=(),
            final_reply={"root_cause": "OOM"},
            input_payload={"alert_id": "A-1"},
        )

        # When attempting to mutate a field

        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            bundle.pipeline_type = "support_review"  # type: ignore[misc]

    def test_agent_prompts_defaults_to_empty_tuple(self) -> None:
        # Given a ReplayBundle created without agent_prompts

        # When constructing with default
        bundle = pipeline_types.ReplayBundle(
            run_id=uuid.uuid4(),
            pipeline_type="investigation",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=None,
            prompt_version=None,
            prompt_sha256=None,
            prompt_text=None,
            input_hash=None,
            model_ids=(),
            mcp_endpoints=(),
            skill_activations=(),
            final_reply=None,
            input_payload=None,
        )

        # Then agent_prompts defaults to an empty tuple
        assert bundle.agent_prompts == ()

    def test_agent_prompts_is_populated(self) -> None:
        # Given agent prompt metadata for two agents
        prompts = (
            {"agent_name": "alert_classifier", "prompt_version": "v1", "prompt_sha256": "aaa"},
            {"agent_name": "root_cause_analyser", "prompt_version": "v2", "prompt_sha256": "bbb"},
        )

        # When constructing with agent_prompts
        bundle = pipeline_types.ReplayBundle(
            run_id=uuid.uuid4(),
            pipeline_type="investigation",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=None,
            prompt_version="v1",
            prompt_sha256="aaa",
            prompt_text="You are an SRE investigator.",
            input_hash=None,
            model_ids=(),
            mcp_endpoints=(),
            skill_activations=(),
            final_reply=None,
            input_payload=None,
            agent_prompts=prompts,
        )

        # Then agent_prompts contains both entries
        assert len(bundle.agent_prompts) == 2
        assert bundle.agent_prompts[0]["agent_name"] == "alert_classifier"
        assert bundle.agent_prompts[1]["agent_name"] == "root_cause_analyser"


class TestFetchReplayBundle:
    @pytest.mark.asyncio
    async def test_returns_bundle_with_all_fields(self) -> None:
        # Given a mock database returning a fully-populated row
        mock_db = mock.AsyncMock()
        run_id = uuid.uuid4()
        started = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
        completed = datetime(2026, 3, 15, 10, 5, tzinfo=UTC)
        agent_prompts_data = [
            {
                "agent_name": "alert_classifier",
                "prompt_version": "v2.1.0",
                "prompt_sha256": "sha256hex",
            },
            {
                "agent_name": "root_cause_analyser",
                "prompt_version": "v1.0.0",
                "prompt_sha256": "otherhex",
            },
        ]
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": run_id,
            "pipeline_type": "investigation",
            "started_at": started,
            "completed_at": completed,
            "prompt_version": "v2.1.0",
            "prompt_sha256": "sha256hex",
            "prompt_text": "Investigate the alert.",
            "input_hash": "inputhash123",
            "model_ids_json": ["openai/gpt-4.1", "openai/gpt-4.1-mini"],
            "mcp_endpoints_json": ["http://mcp.local/tools"],
            "skill_activations_json": [{"skill": "k8s_diagnostics", "version": "1.0"}],
            "final_reply": {"root_cause": "CPU spike"},
            "input_json": {"alert_id": "PD-123"},
            "agent_prompts_json": agent_prompts_data,
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the replay bundle
        bundle = await queries.fetch_replay_bundle(db=mock_db, run_id=run_id)

        # Then all fields are mapped correctly
        assert bundle.run_id == run_id
        assert bundle.pipeline_type == "investigation"
        assert bundle.started_at == started
        assert bundle.completed_at == completed
        assert bundle.prompt_version == "v2.1.0"
        assert bundle.prompt_sha256 == "sha256hex"
        assert bundle.prompt_text == "Investigate the alert."
        assert bundle.input_hash == "inputhash123"
        assert bundle.model_ids == ("openai/gpt-4.1", "openai/gpt-4.1-mini")
        assert bundle.mcp_endpoints == ("http://mcp.local/tools",)
        assert bundle.skill_activations == ({"skill": "k8s_diagnostics", "version": "1.0"},)
        assert bundle.final_reply == {"root_cause": "CPU spike"}
        assert bundle.input_payload == {"alert_id": "PD-123"}
        assert bundle.agent_prompts == tuple(agent_prompts_data)

    @pytest.mark.asyncio
    async def test_raises_on_missing_run(self) -> None:
        # Given a mock database that returns no row
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None
        missing_id = uuid.uuid4()

        # When fetching a non-existent pipeline run

        # Then ReplayBundleNotFoundError is raised with the run_id
        with pytest.raises(pipeline_errors.ReplayBundleNotFoundError) as exc_info:
            await queries.fetch_replay_bundle(db=mock_db, run_id=missing_id)

        assert exc_info.value.run_id == missing_id

    @pytest.mark.asyncio
    async def test_handles_null_json_fields(self) -> None:
        # Given a mock database returning a row with None for all JSON columns
        mock_db = mock.AsyncMock()
        run_id = uuid.uuid4()
        started = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": run_id,
            "pipeline_type": "support_review",
            "started_at": started,
            "completed_at": None,
            "prompt_version": None,
            "prompt_sha256": None,
            "prompt_text": None,
            "input_hash": None,
            "model_ids_json": None,
            "mcp_endpoints_json": None,
            "skill_activations_json": None,
            "final_reply": None,
            "input_json": None,
            "agent_prompts_json": None,
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the replay bundle
        bundle = await queries.fetch_replay_bundle(db=mock_db, run_id=run_id)

        # Then tuple fields default to empty tuples and nullable fields are None
        assert bundle.model_ids == ()
        assert bundle.mcp_endpoints == ()
        assert bundle.skill_activations == ()
        assert bundle.agent_prompts == ()
        assert bundle.completed_at is None
        assert bundle.prompt_version is None
        assert bundle.prompt_sha256 is None
        assert bundle.prompt_text is None
        assert bundle.input_hash is None
        assert bundle.final_reply is None
        assert bundle.input_payload is None
