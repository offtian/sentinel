"""
Unit tests for ``append_skills_to_prompt``.

The helper concatenates matching Skills onto an agent's base system prompt.
Collaborators (``sentinel.plugins.skills.load_skills_for``) are mocked so
these tests never touch disk.
"""

from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.graphs.agents import utils as agents_utils
from sentinel.plugins import skills as skills_mod


def _fake_handle(
    *,
    name: str,
    version: str = "0.1.0",
    body: str = "runbook body here",
) -> skills_mod.SkillHandle:
    return skills_mod.SkillHandle(
        name=name,
        version=version,
        description="fake",
        applies_to=("any",),
        body=body,
        sha256="f" * 64,
    )


class TestAppendSkillsToPrompt:
    def test_returns_base_prompt_when_no_skills_match(self) -> None:
        # Given a base prompt and a loader that returns no skills
        base_prompt = "You are a helpful assistant."

        # When the helper is called with log_event patched out
        with mock.patch.object(skills_mod, "load_skills_for", return_value=()):
            result = agents_utils.append_skills_to_prompt(
                base_prompt=base_prompt, category="no_match", max_skills=5
            )

        # Then the base prompt is returned unchanged
        assert result == base_prompt

    def test_appends_skills_section_with_header(self) -> None:
        # Given one matching skill
        skill = _fake_handle(name="k8s-crashloop", body="Step 1: check pods")

        # When the helper is called
        with mock.patch.object(skills_mod, "load_skills_for", return_value=(skill,)):
            result = agents_utils.append_skills_to_prompt(
                base_prompt="base", category="k8s_crashloop", max_skills=5
            )

        # Then the result contains the Applicable Skills section header
        assert "## Applicable Skills" in result
        assert "base" in result
        assert "Step 1: check pods" in result

    def test_includes_skill_name_and_version_in_section(self) -> None:
        # Given a skill with a specific version
        skill = _fake_handle(name="db-runbook", version="1.2.3")

        # When the helper is called
        with mock.patch.object(skills_mod, "load_skills_for", return_value=(skill,)):
            result = agents_utils.append_skills_to_prompt(
                base_prompt="base", category="database_error", max_skills=5
            )

        # Then the section labels the skill with its name and version
        assert "db-runbook" in result
        assert "v1.2.3" in result

    def test_preserves_order_from_loader(self) -> None:
        # Given two matching skills in a specific order
        alpha = _fake_handle(name="alpha", body="alpha body")
        bravo = _fake_handle(name="bravo", body="bravo body")

        # When the helper is called
        with mock.patch.object(skills_mod, "load_skills_for", return_value=(alpha, bravo)):
            result = agents_utils.append_skills_to_prompt(
                base_prompt="base", category="any", max_skills=5
            )

        # Then alpha appears before bravo in the output
        assert result.index("alpha body") < result.index("bravo body")

    def test_passes_max_skills_through_to_loader(self) -> None:
        # Given a loader mock
        with mock.patch.object(skills_mod, "load_skills_for", return_value=()) as mocked:
            # When called with a specific max_skills
            agents_utils.append_skills_to_prompt(
                base_prompt="base", category="cat_a", max_skills=3
            )

        # Then the loader was invoked with the same max_skills
        mocked.assert_called_once_with(category="cat_a", max_skills=3)

    @pytest.mark.parametrize("blank_input", ["", "   ", "\n\n"])
    def test_preserves_whitespace_only_base_prompt(self, blank_input: str) -> None:
        # Given a blank base prompt and no matching skills
        with mock.patch.object(skills_mod, "load_skills_for", return_value=()):
            # When the helper is called
            result = agents_utils.append_skills_to_prompt(
                base_prompt=blank_input, category="none", max_skills=5
            )

        # Then the blank prompt is returned as-is
        assert result == blank_input


class TestRenderSkillsSection:
    def test_returns_empty_string_when_no_skills_match(self) -> None:
        # Given a loader that returns no skills
        with mock.patch.object(skills_mod, "load_skills_for", return_value=()):
            # When the helper is called
            result = agents_utils.render_skills_section(category="no_match", max_skills=5)

        # Then the result is an empty string
        assert result == ""

    def test_returns_section_without_leading_separator(self) -> None:
        # Given one matching skill
        skill = _fake_handle(name="alpha", body="body")

        # When the helper is called
        with mock.patch.object(skills_mod, "load_skills_for", return_value=(skill,)):
            result = agents_utils.render_skills_section(category="any", max_skills=5)

        # Then the section begins with the heading (no leading '---')
        assert result.startswith("## Applicable Skills")
        assert "alpha" in result
        assert "body" in result

    def test_passes_arguments_through_to_loader(self) -> None:
        # Given a loader mock
        with mock.patch.object(skills_mod, "load_skills_for", return_value=()) as mocked:
            # When called with specific kwargs
            agents_utils.render_skills_section(category="cat_b", max_skills=2)

        # Then the loader was invoked with the same kwargs
        mocked.assert_called_once_with(category="cat_b", max_skills=2)
