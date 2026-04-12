"""Unit tests for :class:`PromptTemplate`."""

from __future__ import annotations

import attrs
import pytest
from jinja2 import Environment

from sentinel.domain.prompts._handle import PromptTemplate


def _make_jinja_template(
    *,
    system: str = "You are a bot.",
    user: str = "Hello {{ name }}",
) -> object:
    """Build a minimal Jinja2 template with system and user blocks."""
    env = Environment(autoescape=False)  # noqa: S701
    source = (
        f"{{% block system %}}{system}{{% endblock %}}{{% block user %}}{user}{{% endblock %}}"
    )
    return env.from_string(source)


class TestFromText:
    """Tests for the ``from_text`` factory classmethod."""

    def test_populates_all_fields(self) -> None:
        """
        Given a template name and system text,
        When PromptTemplate.from_text is called,
        Then all fields are populated correctly.
        """
        tpl = PromptTemplate.from_text(template_name="foo", system_text="hello world")

        assert tpl.template_name == "foo"
        assert tpl.system_text == "hello world"
        assert isinstance(tpl.sha256, str)
        assert len(tpl.sha256) == 64
        assert tpl.version == "1"

    def test_sha256_deterministic(self) -> None:
        """
        Given the same text,
        When two templates are created,
        Then they produce identical SHA-256 digests.
        """
        a = PromptTemplate.from_text(template_name="x", system_text="same")
        b = PromptTemplate.from_text(template_name="x", system_text="same")

        assert a.sha256 == b.sha256

    def test_sha256_changes_with_text(self) -> None:
        """
        Given different texts,
        When two templates are created,
        Then their SHA-256 digests differ.
        """
        a = PromptTemplate.from_text(template_name="x", system_text="alpha")
        b = PromptTemplate.from_text(template_name="x", system_text="beta")

        assert a.sha256 != b.sha256


class TestFromJinja:
    """Tests for the ``from_jinja`` factory classmethod."""

    def test_pre_renders_system_block(self) -> None:
        """
        Given a Jinja2 template with a system block,
        When PromptTemplate.from_jinja is called,
        Then system_text contains the rendered system block.
        """
        jinja_tpl = _make_jinja_template(system="You are helpful.")

        tpl = PromptTemplate.from_jinja(
            template_name="test",
            jinja_template=jinja_tpl,
        )

        assert tpl.system_text == "You are helpful."
        assert len(tpl.sha256) == 64

    def test_raises_when_system_block_missing(self) -> None:
        """
        Given a Jinja2 template without a system block,
        When PromptTemplate.from_jinja is called,
        Then ValueError is raised.
        """
        env = Environment(autoescape=False)  # noqa: S701
        jinja_tpl = env.from_string("{% block user %}hi{% endblock %}")

        with pytest.raises(ValueError, match="has no 'system' block"):
            PromptTemplate.from_jinja(
                template_name="bad",
                jinja_template=jinja_tpl,
            )


class TestRenderUser:
    """Tests for the ``render_user`` method."""

    def test_renders_user_block_with_variables(self) -> None:
        """
        Given a PromptTemplate built from a Jinja2 template,
        When render_user is called with variables,
        Then the user block is rendered with those variables.
        """
        jinja_tpl = _make_jinja_template(user="Hello {{ name }}")
        tpl = PromptTemplate.from_jinja(
            template_name="test",
            jinja_template=jinja_tpl,
        )

        result = tpl.render_user(name="World")

        assert result == "Hello World"

    def test_raises_when_built_from_text(self) -> None:
        """
        Given a PromptTemplate built via from_text (no Jinja2 template),
        When render_user is called,
        Then RuntimeError is raised.
        """
        tpl = PromptTemplate.from_text(template_name="t", system_text="sys")

        with pytest.raises(RuntimeError, match="cannot render user block"):
            tpl.render_user(name="x")

    def test_raises_when_user_block_missing(self) -> None:
        """
        Given a Jinja2 template without a user block,
        When render_user is called,
        Then ValueError is raised.
        """
        env = Environment(autoescape=False)  # noqa: S701
        jinja_tpl = env.from_string("{% block system %}sys{% endblock %}")
        tpl = PromptTemplate.from_jinja(
            template_name="no_user",
            jinja_template=jinja_tpl,
        )

        with pytest.raises(ValueError, match="has no 'user' block"):
            tpl.render_user()


class TestImmutability:
    """Verify that PromptTemplate is truly frozen."""

    def test_frozen_rejects_mutation(self) -> None:
        """
        Given a PromptTemplate instance,
        When attempting to set an attribute,
        Then attrs.exceptions.FrozenInstanceError is raised.
        """
        tpl = PromptTemplate.from_text(template_name="t", system_text="immutable")

        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            tpl.system_text = "mutated"  # type: ignore[misc]
