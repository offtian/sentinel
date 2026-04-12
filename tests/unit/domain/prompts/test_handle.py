"""Unit tests for :class:`PromptTemplate`."""

from __future__ import annotations

import attrs
import pytest

from sentinel.domain.prompts._handle import PromptTemplate


class TestFromText:
    """Tests for the ``from_text`` factory classmethod."""

    def test_populates_all_fields(self) -> None:
        """
        Given a template name and text,
        When PromptTemplate.from_text is called,
        Then all fields are populated correctly.
        """
        handle = PromptTemplate.from_text(template_name="foo", text="hello world")

        assert handle.template_name == "foo"
        assert handle.text == "hello world"
        assert isinstance(handle.sha256, str)
        assert len(handle.sha256) == 64
        assert handle.version == "1"

    def test_sha256_deterministic(self) -> None:
        """
        Given the same text,
        When two handles are created,
        Then they produce identical SHA-256 digests.
        """
        a = PromptTemplate.from_text(template_name="x", text="same")
        b = PromptTemplate.from_text(template_name="x", text="same")

        assert a.sha256 == b.sha256

    def test_sha256_changes_with_text(self) -> None:
        """
        Given different texts,
        When two handles are created,
        Then their SHA-256 digests differ.
        """
        a = PromptTemplate.from_text(template_name="x", text="alpha")
        b = PromptTemplate.from_text(template_name="x", text="beta")

        assert a.sha256 != b.sha256


class TestImmutability:
    """Verify that PromptTemplate is truly frozen."""

    def test_frozen_rejects_mutation(self) -> None:
        """
        Given a PromptTemplate instance,
        When attempting to set an attribute,
        Then attrs.exceptions.FrozenInstanceError is raised.
        """
        handle = PromptTemplate.from_text(template_name="t", text="immutable")

        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            handle.text = "mutated"  # type: ignore[misc]
