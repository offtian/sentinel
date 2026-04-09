"""
Unit tests for the ``list_skills`` FastMCP tool.

The tool reads the on-disk Skills catalogue via the Skills loader and
returns a JSON-serialised list of ``{name, version, description, applies_to}``
entries sorted by name. Bodies are deliberately excluded from the wire.

The plan spec named this an integration test, but the tool has no
database or network dependency — it only calls the loader, which reads
files. A unit test against the real seed catalogue is sufficient.
"""

from __future__ import annotations

import json

import pytest

from sentinel.interfaces.mcp import server as mcp_server
from sentinel.plugins import skills as skills_mod


EXPECTED_SEED_SKILLS = frozenset(
    {
        "k8s-crashloop-runbook",
        "database-connection-runbook",
        "latency-spike-runbook",
        "auth-error-response",
        "rate-limit-response",
        "chart-helm-best-practices",
    }
)


class TestListSkills:
    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        skills_mod._load_all_skills.cache_clear()
        yield
        skills_mod._load_all_skills.cache_clear()

    async def test_returns_seed_catalogue_sorted_by_name(self) -> None:
        # Given the shipped on-disk seed catalogue
        # When the list_skills tool is invoked
        result_json = await mcp_server.list_skills()

        # Then the payload is a JSON list of the six seed skills
        parsed = json.loads(result_json)
        assert isinstance(parsed, list)
        names = [entry["name"] for entry in parsed]
        assert set(names) == EXPECTED_SEED_SKILLS

        # And the list is sorted alphabetically
        assert names == sorted(names)

    async def test_entries_expose_metadata_but_not_body(self) -> None:
        # Given the shipped seed catalogue
        # When the list_skills tool is invoked
        result_json = await mcp_server.list_skills()

        # Then every entry has metadata fields and no body field
        parsed = json.loads(result_json)
        for entry in parsed:
            assert set(entry.keys()) == {"name", "version", "description", "applies_to"}
            assert isinstance(entry["applies_to"], list)
            assert "body" not in entry

    async def test_returns_a_string_suitable_for_the_mcp_wire(self) -> None:
        # Given the shipped seed catalogue
        # When the list_skills tool is invoked
        result = await mcp_server.list_skills()

        # Then the return type is a JSON-serialisable string
        assert isinstance(result, str)
        json.loads(result)  # does not raise
