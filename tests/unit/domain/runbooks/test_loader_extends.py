"""
Unit tests for the F6.K ``extends`` chain resolver.

Covers passthrough, single-link merge, multi-link chain ordering, parent
missing, cycle detection, depth overrun, the per-field merge rules
(tools collisions + caps + denied union, checks collisions + groundedness
union, body sanitization), the ``content_sha`` cascade when a parent
body changes, and the contract that ``load_runbook`` returns the on-disk
shape unflattened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.domain.runbooks import loader, models


_DEFAULT_TOOLS = """\
allowed_tools:
  - name: tool_a
    max_calls: 1
denied_tools: []
max_total_tool_calls: 30
max_loop_iterations: 8
"""

_EMPTY_TOOLS = """\
allowed_tools: []
denied_tools: []
max_total_tool_calls: 30
max_loop_iterations: 8
"""

_DEFAULT_CHECKS = """\
prescribed_checks: []
groundedness_rules:
  - rule_id: every_finding_has_evidence
    description: Every finding cites evidence
body_sanitization:
  reject_auto_rendered_urls: true
  allowed_url_locations: [canonical_sources, frontmatter]
"""

_DEFAULT_TESTS = """\
fixtures:
  - id: noop
    alert_payload_path: fixtures/noop.json
    expected:
      runbook_id: null
      match_method: no_match
      required_checks_executed: []
      hypothesis_keywords: []
      forbidden_substrings_in_summary: []
"""


def _runbook_md(
    *,
    runbook_id: str,
    body: str = "Default body.",
    extends: str | None = None,
    description: str | None = None,
) -> str:
    """Build a minimal RUNBOOK.md with optional extends in the frontmatter."""
    extends_line = f"extends: {extends}\n" if extends is not None else ""
    description_text = description if description is not None else f"{runbook_id} description"
    return (
        "---\n"
        f"runbook_id: {runbook_id}\n"
        f"description: |\n  {description_text}\n"
        "applies_to:\n"
        "  alertnames: []\n"
        "  severity_min: P5\n"
        "  resource_kinds: []\n"
        "  exclude_labels: {}\n"
        "tags: []\n"
        "min_match_score: 0\n"
        "owner: sre-platform\n"
        "authors: [ollie.tian]\n"
        "last_validated: 2026-04-26\n"
        "deprecated_at: null\n"
        "superseded_by: null\n"
        "mnpi_safe: true\n"
        "canonical_sources: []\n"
        f"{extends_line}"
        "---\n\n"
        f"{body}\n"
    )


def _write_runbook(
    *,
    base: Path,
    runbook_id: str,
    body: str = "Default body.",
    extends: str | None = None,
    tools_text: str = _DEFAULT_TOOLS,
    checks_text: str = _DEFAULT_CHECKS,
    tests_text: str = _DEFAULT_TESTS,
    description: str | None = None,
) -> Path:
    """Write the four-file runbook quartet under ``base / runbook_id``."""
    directory = base / runbook_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "RUNBOOK.md").write_text(
        _runbook_md(runbook_id=runbook_id, body=body, extends=extends, description=description),
        encoding="utf-8",
    )
    (directory / "tools.yaml").write_text(tools_text, encoding="utf-8")
    (directory / "checks.yaml").write_text(checks_text, encoding="utf-8")
    (directory / "tests.yaml").write_text(tests_text, encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def _clear_discover_cache() -> None:
    """Reset the discovery LRU cache between tests so tmp_path roots aren't shared."""
    loader._discover_runbooks_cached.cache_clear()


class TestExtendsPassthrough:
    def test_extends_none_passthrough(self, tmp_path: Path) -> None:
        # Given a single runbook with no extends frontmatter field
        _write_runbook(base=tmp_path, runbook_id="solo", body="Solo body.")

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the runbook is unchanged: extends is None, body is the on-disk body
        assert "solo" in catalog
        runbook = catalog["solo"]
        assert runbook.metadata.extends is None
        assert "Solo body." in runbook.body


class TestExtendsBodyMerge:
    def test_extends_depth_one_merges_body(self, tmp_path: Path) -> None:
        # Given a parent runbook and a child runbook that extends it
        _write_runbook(base=tmp_path, runbook_id="parent", body="PARENT_BODY")
        _write_runbook(base=tmp_path, runbook_id="child", body="CHILD_BODY", extends="parent")

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the child's body is parent + separator + child (parent first)
        merged_body = catalog["child"].body
        assert "PARENT_BODY" in merged_body
        assert "CHILD_BODY" in merged_body
        assert merged_body.index("PARENT_BODY") < merged_body.index("CHILD_BODY")
        assert "\n\n---\n\n" in merged_body

    def test_extends_depth_two_merges_chain(self, tmp_path: Path) -> None:
        # Given a three-link chain: grandchild -> child -> grandparent
        _write_runbook(base=tmp_path, runbook_id="grandparent", body="GRANDPARENT_BODY")
        _write_runbook(base=tmp_path, runbook_id="child", body="CHILD_BODY", extends="grandparent")
        _write_runbook(
            base=tmp_path,
            runbook_id="grandchild",
            body="GRANDCHILD_BODY",
            extends="child",
        )

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the merged body orders grandparent -> child -> grandchild (root first)
        merged_body = catalog["grandchild"].body
        assert merged_body.index("GRANDPARENT_BODY") < merged_body.index("CHILD_BODY")
        assert merged_body.index("CHILD_BODY") < merged_body.index("GRANDCHILD_BODY")


class TestExtendsErrors:
    def test_extends_target_missing_raises(self, tmp_path: Path) -> None:
        # Given a child runbook that references a parent that does not exist
        _write_runbook(base=tmp_path, runbook_id="orphan", extends="ghost-parent")

        # When the catalog is discovered
        # Then RunbookExtendsTargetNotFoundError surfaces with both ids
        with pytest.raises(models.RunbookExtendsTargetNotFoundError, match="ghost-parent"):
            loader.discover_runbooks((tmp_path,))

    def test_extends_cycle_detected(self, tmp_path: Path) -> None:
        # Given two runbooks that extend each other (A extends B extends A)
        _write_runbook(base=tmp_path, runbook_id="a", extends="b")
        _write_runbook(base=tmp_path, runbook_id="b", extends="a")

        # When the catalog is discovered
        # Then RunbookExtendsCycleError surfaces
        with pytest.raises(models.RunbookExtendsCycleError, match="cycle"):
            loader.discover_runbooks((tmp_path,))

    def test_extends_too_deep_raises(self, tmp_path: Path) -> None:
        # Given a chain longer than RUNBOOK_EXTENDS_MAX_DEPTH (default 5)
        # link0 extends link1 extends link2 ... extends link6 (7 nodes, 6 links > 5)
        depth = models.RUNBOOK_EXTENDS_MAX_DEPTH + 1
        for index in range(depth + 1):
            parent = f"link{index + 1}" if index < depth else None
            _write_runbook(base=tmp_path, runbook_id=f"link{index}", extends=parent)

        # When the catalog is discovered
        # Then RunbookExtendsTooDeepError surfaces against the deepest leaf
        with pytest.raises(models.RunbookExtendsTooDeepError, match="depth"):
            loader.discover_runbooks((tmp_path,))


class TestExtendsToolsMerge:
    def test_extends_tool_collision_child_wins_max_calls(self, tmp_path: Path) -> None:
        # Given a parent with tool_x: max_calls=10 and a child with tool_x: max_calls=3
        parent_tools = (
            "allowed_tools:\n"
            "  - name: tool_x\n"
            "    max_calls: 10\n"
            "denied_tools: []\n"
            "max_total_tool_calls: 30\n"
            "max_loop_iterations: 8\n"
        )
        child_tools = (
            "allowed_tools:\n"
            "  - name: tool_x\n"
            "    max_calls: 3\n"
            "denied_tools: []\n"
            "max_total_tool_calls: 30\n"
            "max_loop_iterations: 8\n"
        )
        _write_runbook(base=tmp_path, runbook_id="parent", tools_text=parent_tools)
        _write_runbook(
            base=tmp_path,
            runbook_id="child",
            tools_text=child_tools,
            extends="parent",
        )

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the child's max_calls (3) overrides the parent's (10)
        flattened_tools = catalog["child"].tools
        tool_x = next(spec for spec in flattened_tools.allowed_tools if spec.name == "tool_x")
        assert tool_x.max_calls == 3

    def test_extends_tool_caps_take_min(self, tmp_path: Path) -> None:
        # Given a parent with max_total_tool_calls=20 and a child with =30
        parent_tools = (
            "allowed_tools: []\n"
            "denied_tools: []\n"
            "max_total_tool_calls: 20\n"
            "max_loop_iterations: 5\n"
        )
        child_tools = (
            "allowed_tools: []\n"
            "denied_tools: []\n"
            "max_total_tool_calls: 30\n"
            "max_loop_iterations: 9\n"
        )
        _write_runbook(base=tmp_path, runbook_id="parent", tools_text=parent_tools)
        _write_runbook(
            base=tmp_path,
            runbook_id="child",
            tools_text=child_tools,
            extends="parent",
        )

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the tighter cap wins (min): max_total=20, max_loop=5
        flattened_tools = catalog["child"].tools
        assert flattened_tools.max_total_tool_calls == 20
        assert flattened_tools.max_loop_iterations == 5


class TestExtendsChecksMerge:
    def test_extends_check_collision_child_overrides(self, tmp_path: Path) -> None:
        # Given parent and child both define a prescribed_check id=confirm_pod_state
        parent_checks = (
            "prescribed_checks:\n"
            "  - id: confirm_pod_state\n"
            "    description: parent description for the check\n"
            "    suggested_tools: [parent_tool]\n"
            "    required: false\n"
            "groundedness_rules: []\n"
            "body_sanitization:\n"
            "  reject_auto_rendered_urls: true\n"
            "  allowed_url_locations: [canonical_sources, frontmatter]\n"
        )
        child_checks = (
            "prescribed_checks:\n"
            "  - id: confirm_pod_state\n"
            "    description: child description for the check\n"
            "    suggested_tools: [child_tool]\n"
            "    required: true\n"
            "groundedness_rules: []\n"
            "body_sanitization:\n"
            "  reject_auto_rendered_urls: true\n"
            "  allowed_url_locations: [canonical_sources, frontmatter]\n"
        )
        _write_runbook(base=tmp_path, runbook_id="parent", checks_text=parent_checks)
        _write_runbook(
            base=tmp_path,
            runbook_id="child",
            checks_text=child_checks,
            extends="parent",
        )

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the child's check (description, suggested_tools, required) wins
        merged_checks = catalog["child"].checks.prescribed_checks
        confirm = next(check for check in merged_checks if check.id == "confirm_pod_state")
        assert confirm.description == "child description for the check"
        assert confirm.suggested_tools == ("child_tool",)
        assert confirm.required is True
        assert len(merged_checks) == 1

    def test_extends_groundedness_rules_union(self, tmp_path: Path) -> None:
        # Given parent has rule_a + rule_b, child has rule_b + rule_c (rule_b overrides)
        parent_checks = (
            "prescribed_checks: []\n"
            "groundedness_rules:\n"
            "  - rule_id: rule_a\n"
            "    description: parent rule a\n"
            "  - rule_id: rule_b\n"
            "    description: parent rule b\n"
            "body_sanitization:\n"
            "  reject_auto_rendered_urls: true\n"
            "  allowed_url_locations: [canonical_sources, frontmatter]\n"
        )
        child_checks = (
            "prescribed_checks: []\n"
            "groundedness_rules:\n"
            "  - rule_id: rule_b\n"
            "    description: child rule b\n"
            "  - rule_id: rule_c\n"
            "    description: child rule c\n"
            "body_sanitization:\n"
            "  reject_auto_rendered_urls: true\n"
            "  allowed_url_locations: [canonical_sources, frontmatter]\n"
        )
        _write_runbook(base=tmp_path, runbook_id="parent", checks_text=parent_checks)
        _write_runbook(
            base=tmp_path,
            runbook_id="child",
            checks_text=child_checks,
            extends="parent",
        )

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the union has rule_a + rule_b + rule_c, with child's rule_b winning
        merged_rules = catalog["child"].checks.groundedness_rules
        rules_by_id = {rule.rule_id: rule.description for rule in merged_rules}
        assert rules_by_id == {
            "rule_a": "parent rule a",
            "rule_b": "child rule b",
            "rule_c": "child rule c",
        }


class TestExtendsContentShaCascade:
    def test_content_sha_changes_when_parent_body_changes(self, tmp_path: Path) -> None:
        # Given a grandchild whose chain root (grandparent) has a known body
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        for root, grandparent_body in (
            (first_root, "GRANDPARENT_BODY_VERSION_ONE"),
            (second_root, "GRANDPARENT_BODY_VERSION_TWO"),
        ):
            _write_runbook(base=root, runbook_id="grandparent", body=grandparent_body)
            _write_runbook(base=root, runbook_id="child", body="CHILD_BODY", extends="grandparent")
            _write_runbook(
                base=root,
                runbook_id="grandchild",
                body="GRANDCHILD_BODY",
                extends="child",
            )

        # When both catalogs are loaded (cache cleared between)
        first_catalog = loader.discover_runbooks((first_root,))
        loader._discover_runbooks_cached.cache_clear()
        second_catalog = loader.discover_runbooks((second_root,))

        # Then the grandchild's flattened content_sha differs because the
        # grandparent body was edited in the second tree
        assert (
            first_catalog["grandchild"].metadata.content_sha
            != second_catalog["grandchild"].metadata.content_sha
        )


class TestExtendsMetadataCleanup:
    def test_extends_field_dropped_in_flattened_metadata(self, tmp_path: Path) -> None:
        # Given a child runbook that extends a parent
        _write_runbook(base=tmp_path, runbook_id="parent")
        _write_runbook(base=tmp_path, runbook_id="child", extends="parent")

        # When the catalog is discovered
        catalog = loader.discover_runbooks((tmp_path,))

        # Then the flattened child's metadata.extends is None even though the
        # source frontmatter declared extends: parent
        assert catalog["child"].metadata.extends is None
        # And other identity fields are preserved verbatim from the child
        assert catalog["child"].metadata.runbook_id == "child"


class TestLoadRunbookContract:
    def test_load_runbook_does_not_resolve_extends(self, tmp_path: Path) -> None:
        # Given a parent and child runbook on disk
        _write_runbook(base=tmp_path, runbook_id="parent", body="PARENT_BODY")
        child_dir = _write_runbook(
            base=tmp_path,
            runbook_id="child",
            body="CHILD_BODY",
            extends="parent",
        )

        # When loading just the child via load_runbook (single-file contract)
        runbook = loader.load_runbook(child_dir)

        # Then the body is the on-disk child body only — no parent merge
        assert "CHILD_BODY" in runbook.body
        assert "PARENT_BODY" not in runbook.body
        # And the extends field is preserved on the metadata so a downstream
        # caller can still see the unresolved chain
        assert runbook.metadata.extends == "parent"
