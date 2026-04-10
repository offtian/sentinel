"""
Unit tests for the Skills loader.

Exercises SkillHandle construction, frontmatter parsing, SHA-256 stability,
deterministic ordering, applies_to glob matching, empty-category fallback,
and the skill_activated structlog event.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest import mock

import pytest

from sentinel.domain import skills as skills_mod
from sentinel.utils import logs


FRONTMATTER_TEMPLATE = """---
name: {name}
description: {description}
version: {version}
applies_to: {applies_to}
---

# {name}

{body}
"""


def _write_skill(
    *,
    base_dir: Path,
    name: str,
    description: str = "desc",
    version: str = "0.1.0",
    applies_to: str = "[]",
    body: str = "placeholder body",
) -> Path:
    skill_dir = base_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        FRONTMATTER_TEMPLATE.format(
            name=name,
            description=description,
            version=version,
            applies_to=applies_to,
            body=body,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the loader at an empty tmp skills directory and clear its cache."""
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", tmp_path)
    skills_mod._load_all_skills.cache_clear()
    yield tmp_path
    skills_mod._load_all_skills.cache_clear()


class TestSkillHandle:
    def test_is_frozen_and_hashable(self) -> None:
        # Given two identical handles built with the same values
        first = skills_mod.SkillHandle(
            name="a",
            version="0.1.0",
            description="d",
            applies_to=("foo_*",),
            body="body",
            sha256="x" * 64,
        )
        second = skills_mod.SkillHandle(
            name="a",
            version="0.1.0",
            description="d",
            applies_to=("foo_*",),
            body="body",
            sha256="x" * 64,
        )

        # When compared and mutated
        # Then attribute assignment is forbidden (frozen) and equality holds
        with pytest.raises(attr_error := Exception):  # noqa: F841
            first.name = "b"  # type: ignore[misc]
        assert first == second


class TestLoadAllSkills:
    def test_returns_handles_sorted_by_name(self, skills_dir: Path) -> None:
        # Given three skills written in non-alphabetical order
        _write_skill(base_dir=skills_dir, name="charlie")
        _write_skill(base_dir=skills_dir, name="alpha")
        _write_skill(base_dir=skills_dir, name="bravo")

        # When the loader reads them
        result = skills_mod._load_all_skills()

        # Then they come back alphabetically by name
        assert [handle.name for handle in result] == ["alpha", "bravo", "charlie"]

    def test_sha256_is_stable_across_calls(self, skills_dir: Path) -> None:
        # Given a single skill on disk
        path = _write_skill(base_dir=skills_dir, name="stable")
        expected = hashlib.sha256(path.read_bytes()).hexdigest()

        # When the loader is invoked twice (second call served from cache)
        first_call = skills_mod._load_all_skills()
        skills_mod._load_all_skills.cache_clear()
        second_call = skills_mod._load_all_skills()

        # Then the hash is identical and matches the raw-byte digest
        assert first_call[0].sha256 == expected
        assert second_call[0].sha256 == expected

    def test_raises_when_frontmatter_missing_required_field(self, skills_dir: Path) -> None:
        # Given a skill whose frontmatter omits the name field
        bad_dir = skills_dir / "bad-skill"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text(
            "---\ndescription: no name\nversion: 0.1.0\napplies_to: []\n---\nbody\n",
            encoding="utf-8",
        )

        # When the loader tries to read it
        # Then it raises SkillFrontmatterError
        with pytest.raises(skills_mod.SkillFrontmatterError):
            skills_mod._load_all_skills()

    def test_raises_when_frontmatter_is_not_a_mapping(self, skills_dir: Path) -> None:
        # Given a skill whose frontmatter is a list, not a dict
        bad_dir = skills_dir / "listy"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text("---\n- one\n- two\n---\nbody\n", encoding="utf-8")

        # When the loader tries to read it
        # Then it raises SkillFrontmatterError
        with pytest.raises(skills_mod.SkillFrontmatterError):
            skills_mod._load_all_skills()

    def test_skips_directories_without_skill_md(self, skills_dir: Path) -> None:
        # Given one valid skill and one empty directory
        _write_skill(base_dir=skills_dir, name="valid")
        (skills_dir / "not-a-skill").mkdir()

        # When the loader reads them
        result = skills_mod._load_all_skills()

        # Then only the valid skill is returned
        assert [handle.name for handle in result] == ["valid"]


class TestLoadSkillsFor:
    def test_returns_only_matching_categories_via_glob(self, skills_dir: Path) -> None:
        # Given skills with different applies_to patterns
        _write_skill(base_dir=skills_dir, name="k8s-runbook", applies_to='["k8s_*"]')
        _write_skill(base_dir=skills_dir, name="db-runbook", applies_to='["database_*"]')

        # When loading skills for a k8s category
        result = skills_mod.load_skills_for(category="k8s_crashloop", max_skills=10)

        # Then only the k8s runbook is returned
        assert [handle.name for handle in result] == ["k8s-runbook"]

    def test_wildcard_skill_with_empty_applies_to_matches_any_category(
        self, skills_dir: Path
    ) -> None:
        # Given a skill whose applies_to is empty (universal)
        _write_skill(base_dir=skills_dir, name="org-style", applies_to="[]")

        # When loading for any category
        result = skills_mod.load_skills_for(category="any_random_category", max_skills=10)

        # Then the universal skill is returned
        assert [handle.name for handle in result] == ["org-style"]

    def test_matching_is_case_insensitive(self, skills_dir: Path) -> None:
        # Given a skill with lowercase pattern
        _write_skill(base_dir=skills_dir, name="auth", applies_to='["auth_*"]')

        # When loading with an uppercase category
        result = skills_mod.load_skills_for(category="AUTH_DENIED", max_skills=10)

        # Then the skill matches
        assert [handle.name for handle in result] == ["auth"]

    def test_truncates_to_max_after_sorting(self, skills_dir: Path) -> None:
        # Given more skills than max_skills, all matching
        for letter in ("delta", "alpha", "charlie", "bravo"):
            _write_skill(base_dir=skills_dir, name=letter, applies_to='["all"]')

        # When loading with max_skills=2
        result = skills_mod.load_skills_for(category="all", max_skills=2)

        # Then the first two alphabetical skills are returned
        assert [handle.name for handle in result] == ["alpha", "bravo"]

    def test_returns_empty_tuple_when_no_match(self, skills_dir: Path) -> None:
        # Given a skill that only applies to k8s
        _write_skill(base_dir=skills_dir, name="k8s-only", applies_to='["k8s_*"]')

        # When loading for an unrelated category
        result = skills_mod.load_skills_for(category="network_partition", max_skills=10)

        # Then the result is an empty tuple
        assert result == ()

    def test_returns_empty_tuple_when_catalogue_is_empty(self, skills_dir: Path) -> None:
        # Given no skills on disk
        # When loading any category
        result = skills_mod.load_skills_for(category="whatever", max_skills=10)

        # Then the result is an empty tuple
        assert result == ()

    def test_emits_skill_activated_event_per_skill(self, skills_dir: Path) -> None:
        # Given two matching skills
        _write_skill(base_dir=skills_dir, name="alpha", applies_to='["cat_*"]')
        _write_skill(base_dir=skills_dir, name="bravo", applies_to='["cat_*"]')

        # When loading for a matching category, with log_event patched
        with mock.patch.object(logs, "log_event") as mocked:
            skills_mod.load_skills_for(category="cat_foo", max_skills=10)

        # Then one skill_activated event was emitted per skill
        activated_calls = [
            call
            for call in mocked.call_args_list
            if call.args and call.args[0] == "skill_activated"
        ]
        assert len(activated_calls) == 2
        emitted_names = {call.kwargs["params"]["skill_name"] for call in activated_calls}
        assert emitted_names == {"alpha", "bravo"}

    def test_emits_skills_no_match_when_empty(self, skills_dir: Path) -> None:
        # Given no matching skills
        _write_skill(base_dir=skills_dir, name="only-k8s", applies_to='["k8s_*"]')

        # When loading for a non-matching category
        with mock.patch.object(logs, "log_event") as mocked:
            skills_mod.load_skills_for(category="billing_error", max_skills=10)

        # Then a single skills_no_match event is emitted
        no_match_calls = [
            call
            for call in mocked.call_args_list
            if call.args and call.args[0] == "skills_no_match"
        ]
        assert len(no_match_calls) == 1
        assert no_match_calls[0].kwargs["params"]["category"] == "billing_error"

    def test_activation_event_carries_version_and_sha256(self, skills_dir: Path) -> None:
        # Given a single matching skill
        _write_skill(
            base_dir=skills_dir,
            name="detail",
            version="1.2.3",
            applies_to='["any"]',
        )

        # When loaded
        with mock.patch.object(logs, "log_event") as mocked:
            skills_mod.load_skills_for(category="any", max_skills=10)

        # Then the activation event carries version and sha256
        activated = next(
            call
            for call in mocked.call_args_list
            if call.args and call.args[0] == "skill_activated"
        )
        assert activated.kwargs["params"]["version"] == "1.2.3"
        assert len(activated.kwargs["params"]["sha256"]) == 64
