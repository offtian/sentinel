"""
Regression lock on the shipped Skills catalogue.

This test reads the real on-disk skills directory (not a tmp_path) to
assert the expected seed skills are present and well-formed. It exists
to catch accidental deletions or schema drift.
"""

from __future__ import annotations

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


class TestSeedCatalogue:
    def test_catalogue_contains_expected_seed_skills(self) -> None:
        # Given the on-disk skills directory shipped with the package
        # When the loader is invoked against the real catalogue
        skills_mod._load_all_skills.cache_clear()
        try:
            loaded = skills_mod._load_all_skills()
        finally:
            skills_mod._load_all_skills.cache_clear()

        # Then exactly the expected seed skills are present
        loaded_names = {handle.name for handle in loaded}
        assert loaded_names == EXPECTED_SEED_SKILLS

    def test_every_seed_skill_has_a_stable_sha256(self) -> None:
        # Given the on-disk skills directory
        # When the loader parses every seed
        skills_mod._load_all_skills.cache_clear()
        try:
            loaded = skills_mod._load_all_skills()
        finally:
            skills_mod._load_all_skills.cache_clear()

        # Then every handle has a 64-character hex digest
        for handle in loaded:
            assert len(handle.sha256) == 64
            assert all(character in "0123456789abcdef" for character in handle.sha256)

    def test_seed_skills_are_sorted_alphabetically(self) -> None:
        # Given the on-disk skills directory
        # When loaded
        skills_mod._load_all_skills.cache_clear()
        try:
            loaded = skills_mod._load_all_skills()
        finally:
            skills_mod._load_all_skills.cache_clear()

        # Then the returned order is ascending by name
        names = [handle.name for handle in loaded]
        assert names == sorted(names)
