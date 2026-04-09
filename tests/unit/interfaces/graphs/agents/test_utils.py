"""
Unit tests for agent utility functions.

Collaborators (``sentinel.plugins.skills``) are mocked so tests never touch disk.
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


class TestComposeSystemPrompt:
    def test_returns_base_prompt_when_no_skills_requested(self) -> None:
        # Given an empty skill-name tuple
        # When the helper is called
        result = agents_utils.compose_system_prompt(base_prompt="base prompt", skill_names=())

        # Then the base prompt is returned unchanged
        assert result == "base prompt"

    def test_resolves_skill_names_into_section(self) -> None:
        # Given two skills on the catalogue
        alpha = _fake_handle(name="alpha", body="alpha body")
        bravo = _fake_handle(name="bravo", body="bravo body")

        # When compose_system_prompt is called with their names
        with mock.patch.object(skills_mod, "all_installed_skills", return_value=(alpha, bravo)):
            result = agents_utils.compose_system_prompt(
                base_prompt="base", skill_names=("alpha", "bravo")
            )

        # Then both skill bodies appear inside the Applicable Skills section
        assert "base" in result
        assert "## Applicable Skills" in result
        assert "alpha body" in result
        assert "bravo body" in result

    def test_preserves_config_order_not_alphabetical(self) -> None:
        # Given two skills declared in non-alphabetical order in config
        alpha = _fake_handle(name="alpha", body="alpha body")
        bravo = _fake_handle(name="bravo", body="bravo body")

        # When compose_system_prompt is called with bravo before alpha
        with mock.patch.object(skills_mod, "all_installed_skills", return_value=(alpha, bravo)):
            result = agents_utils.compose_system_prompt(
                base_prompt="base", skill_names=("bravo", "alpha")
            )

        # Then bravo appears before alpha (config-driven order wins)
        assert result.index("bravo body") < result.index("alpha body")

    def test_raises_skill_not_found_when_name_unknown(self) -> None:
        # Given a catalogue that does not contain "typoed-name"
        alpha = _fake_handle(name="alpha")

        # When compose_system_prompt is called with the unknown name
        # Then SkillNotFoundError is raised and names the missing skill
        with (
            mock.patch.object(skills_mod, "all_installed_skills", return_value=(alpha,)),
            pytest.raises(skills_mod.SkillNotFoundError, match="typoed-name"),
        ):
            agents_utils.compose_system_prompt(base_prompt="base", skill_names=("typoed-name",))

    def test_raises_with_all_missing_names_when_multiple_unknown(self) -> None:
        # Given a catalogue missing two requested skills
        alpha = _fake_handle(name="alpha")

        # When compose_system_prompt is called with two unknown names
        # Then SkillNotFoundError surfaces both missing names
        with (
            mock.patch.object(skills_mod, "all_installed_skills", return_value=(alpha,)),
            pytest.raises(skills_mod.SkillNotFoundError) as exc_info,
        ):
            agents_utils.compose_system_prompt(
                base_prompt="base", skill_names=("missing-one", "missing-two")
            )
        assert "missing-one" in str(exc_info.value)
        assert "missing-two" in str(exc_info.value)


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
