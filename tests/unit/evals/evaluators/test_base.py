"""Tests for evaluators.base — shared resolve_field utility."""

from __future__ import annotations

import pytest

from sentinel.evals.evaluators import base


class TestResolveField:
    def test_resolves_top_level_key(self) -> None:
        # Given a flat payload
        payload = {"name": "test"}

        # When resolving a top-level field
        result = base.resolve_field(payload=payload, field_path="name")

        # Then the value is returned
        assert result == "test"

    def test_resolves_nested_key(self) -> None:
        # Given a nested payload
        payload = {"output": {"severity": "high"}}

        # When resolving a dot-separated path
        result = base.resolve_field(payload=payload, field_path="output.severity")

        # Then the nested value is returned
        assert result == "high"

    def test_resolves_deeply_nested_key(self) -> None:
        # Given a deeply nested payload
        payload = {"a": {"b": {"c": {"d": 42}}}}

        # When resolving a deep path
        result = base.resolve_field(payload=payload, field_path="a.b.c.d")

        # Then the deeply nested value is returned
        assert result == 42

    def test_raises_key_error_for_missing_segment(self) -> None:
        # Given a payload missing the requested key
        payload = {"output": {"severity": "high"}}

        # When resolving a path with a missing segment
        # Then a KeyError is raised
        with pytest.raises(KeyError):
            base.resolve_field(payload=payload, field_path="output.nonexistent")

    def test_resolves_list_values(self) -> None:
        # Given a payload with list values
        payload = {"output": {"items": ["a", "b", "c"]}}

        # When resolving the list field
        result = base.resolve_field(payload=payload, field_path="output.items")

        # Then the list is returned
        assert result == ["a", "b", "c"]
