"""Tests for the IncidentMemory and SimilarIncident frozen entities."""

from __future__ import annotations

import attrs
import pytest

from tests import factories


class TestIncidentMemory:
    def test_is_frozen(self) -> None:
        # Given a constructed IncidentMemory
        memory = factories.make_incident_memory()

        # When attempting to mutate a field
        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            memory.tenant_id = "different"  # type: ignore[misc]

    def test_carries_tenant_and_cluster_scoping(self) -> None:
        # Given a memory factory call with explicit scoping
        memory = factories.make_incident_memory(
            tenant_id="fund-alpha",
            cluster_id="prod-eu-west-1",
        )

        # When reading the scope fields
        # Then they round-trip
        assert memory.tenant_id == "fund-alpha"
        assert memory.cluster_id == "prod-eu-west-1"


class TestSimilarIncident:
    def test_carries_similarity_and_section(self) -> None:
        # Given a memory and explicit similarity / section
        memory = factories.make_incident_memory()

        # When constructing the SimilarIncident
        hit = factories.make_similar_incident(
            memory=memory, similarity=0.83, matched_section="root_cause"
        )

        # Then the projection round-trips
        assert hit.memory is memory
        assert hit.similarity == 0.83
        assert hit.matched_section == "root_cause"
