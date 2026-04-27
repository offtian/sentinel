"""
Unit tests for the runbook loader.

Exercises filesystem walking, content_sha computation + stability,
frontmatter / sidecar-yaml schema validation, body sanitization
(zero-width strip + auto-rendered URL rejection), deprecated-runbook
loading, first-wins shadowing across discovery roots, and the
runbook_override structured event.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from sentinel.domain.runbooks import loader, models
from sentinel.utils import logs


_DEFAULT_RUNBOOK = """\
---
runbook_id: {runbook_id}
description: |
  {description}
applies_to:
  alertnames: {alertnames}
  severity_min: {severity_min}
  resource_kinds: {resource_kinds}
  exclude_labels: {exclude_labels}
tags:
{tags}
min_match_score: {min_match_score}
owner: {owner}
authors: {authors}
last_validated: {last_validated}
deprecated_at: {deprecated_at}
superseded_by: {superseded_by}
mnpi_safe: {mnpi_safe}
canonical_sources: {canonical_sources}
---

# {runbook_id}

{body}
"""

_DEFAULT_TOOLS = """\
allowed_tools:
  - name: k8s_describe_pod
    max_calls: 5
  - name: k8s_get_events
    max_calls: 3
denied_tools: []
max_total_tool_calls: 30
max_loop_iterations: 8
"""

_DEFAULT_CHECKS_TEMPLATE = """\
prescribed_checks:
  - id: confirm_pod_state
    description: Confirm pod is actually CrashLooping
    suggested_tools: [k8s_describe_pod]
    required: true
  - id: tail_recent_logs
    description: Last 100 log lines before crash
    suggested_tools: [k8s_get_pod_logs]
    required: false
groundedness_rules:
  - rule_id: every_finding_has_evidence
    description: Every finding must cite an evidence_ref
body_sanitization:
  reject_auto_rendered_urls: {reject_urls}
  allowed_url_locations: [canonical_sources, frontmatter]
"""

_DEFAULT_TESTS = """\
fixtures:
  - id: oom-classic
    alert_payload_path: fixtures/oom-classic.json
    expected:
      runbook_id: k8s-crashloop
      match_method: tag
      min_tag_score: 2
      required_checks_executed: [confirm_pod_state]
      hypothesis_keywords: [memory, OOMKilled]
      confidence_min: HIGH
      forbidden_substrings_in_summary: []
"""


def _write_runbook_dir(
    *,
    base: Path,
    runbook_id: str = "k8s-crashloop",
    description: str = "Procedure for investigating CrashLoopBackOff pods.",
    alertnames: str = "[KubePodCrashLooping, PodRestartingTooOften]",
    severity_min: str = "P3",
    resource_kinds: str = "[Pod, Deployment]",
    exclude_labels: str = "{}",
    tags_yaml: str = "  - { key: cluster_class, value: production }",
    min_match_score: int = 2,
    owner: str = "sre-platform",
    authors: str = "[ollie.tian]",
    last_validated: str = "2026-04-26",
    deprecated_at: str = "null",
    superseded_by: str = "null",
    mnpi_safe: str = "true",
    canonical_sources: str = "[]",
    body: str = "## Goal\n\nInvestigate the crashloop.",
    tools_text: str = _DEFAULT_TOOLS,
    checks_text: str | None = None,
    tests_text: str = _DEFAULT_TESTS,
    reject_urls: str = "true",
) -> Path:
    directory = base / runbook_id
    directory.mkdir(parents=True, exist_ok=True)
    runbook_md = _DEFAULT_RUNBOOK.format(
        runbook_id=runbook_id,
        description=description,
        alertnames=alertnames,
        severity_min=severity_min,
        resource_kinds=resource_kinds,
        exclude_labels=exclude_labels,
        tags=tags_yaml,
        min_match_score=min_match_score,
        owner=owner,
        authors=authors,
        last_validated=last_validated,
        deprecated_at=deprecated_at,
        superseded_by=superseded_by,
        mnpi_safe=mnpi_safe,
        canonical_sources=canonical_sources,
        body=body,
    )
    (directory / "RUNBOOK.md").write_text(runbook_md, encoding="utf-8")
    (directory / "tools.yaml").write_text(tools_text, encoding="utf-8")
    checks = (
        checks_text
        if checks_text is not None
        else _DEFAULT_CHECKS_TEMPLATE.format(reject_urls=reject_urls)
    )
    (directory / "checks.yaml").write_text(checks, encoding="utf-8")
    (directory / "tests.yaml").write_text(tests_text, encoding="utf-8")
    return directory


class TestLoadRunbookHappyPath:
    def test_returns_runbook_with_parsed_metadata(self, tmp_path: Path) -> None:
        # Given a valid runbook directory written to tmp_path
        directory = _write_runbook_dir(base=tmp_path)

        # When the loader reads it
        runbook = loader.load_runbook(directory)

        # Then metadata fields parse correctly
        assert runbook.metadata.runbook_id == "k8s-crashloop"
        assert runbook.metadata.applies_to.severity_min == "P3"
        assert runbook.metadata.applies_to.alertnames == (
            "KubePodCrashLooping",
            "PodRestartingTooOften",
        )
        assert runbook.metadata.last_validated == date(2026, 4, 26)
        assert runbook.metadata.deprecated_at is None
        assert runbook.metadata.mnpi_safe is True
        assert runbook.metadata.tags[0].key == "cluster_class"

    def test_parses_tools_into_toolspecs(self, tmp_path: Path) -> None:
        # Given a runbook with two tools in tools.yaml
        directory = _write_runbook_dir(base=tmp_path)

        # When the loader reads it
        runbook = loader.load_runbook(directory)

        # Then the tools tuple contains both with correct max_calls
        assert {spec.name: spec.max_calls for spec in runbook.tools.allowed_tools} == {
            "k8s_describe_pod": 5,
            "k8s_get_events": 3,
        }
        assert runbook.tools.allowed_tool_names == frozenset(
            {"k8s_describe_pod", "k8s_get_events"}
        )
        assert dict(runbook.tools.tool_max_calls) == {
            "k8s_describe_pod": 5,
            "k8s_get_events": 3,
        }

    def test_parses_prescribed_checks_with_required_flag(self, tmp_path: Path) -> None:
        # Given the default checks.yaml with two prescribed_checks
        directory = _write_runbook_dir(base=tmp_path)

        # When the loader reads it
        runbook = loader.load_runbook(directory)

        # Then the required flag is preserved per-check
        check_required = {check.id: check.required for check in runbook.checks.prescribed_checks}
        assert check_required == {"confirm_pod_state": True, "tail_recent_logs": False}


class TestContentSha:
    def test_sha_is_stable_across_reloads(self, tmp_path: Path) -> None:
        # Given a runbook directory loaded twice
        directory = _write_runbook_dir(base=tmp_path)

        # When loaded twice
        first = loader.load_runbook(directory)
        second = loader.load_runbook(directory)

        # Then the content_sha is identical
        assert first.metadata.content_sha == second.metadata.content_sha
        assert len(first.metadata.content_sha) == 32

    def test_sha_is_stable_when_only_last_validated_changes(self, tmp_path: Path) -> None:
        # Given two runbooks differing only in the last_validated frontmatter field
        directory_a = _write_runbook_dir(base=tmp_path / "a", last_validated="2026-04-26")
        directory_b = _write_runbook_dir(base=tmp_path / "b", last_validated="2026-01-01")

        # When both are loaded
        runbook_a = loader.load_runbook(directory_a)
        runbook_b = loader.load_runbook(directory_b)

        # Then content_sha matches because frontmatter is excluded from the hash
        assert runbook_a.metadata.content_sha == runbook_b.metadata.content_sha

    def test_sha_changes_when_body_changes(self, tmp_path: Path) -> None:
        # Given two runbooks with different bodies
        directory_a = _write_runbook_dir(base=tmp_path / "a", body="body one")
        directory_b = _write_runbook_dir(base=tmp_path / "b", body="body two")

        # When both are loaded
        runbook_a = loader.load_runbook(directory_a)
        runbook_b = loader.load_runbook(directory_b)

        # Then content_sha differs
        assert runbook_a.metadata.content_sha != runbook_b.metadata.content_sha


class TestSchemaValidation:
    def test_unknown_frontmatter_key_raises(self, tmp_path: Path) -> None:
        # Given a runbook directory then we mutate RUNBOOK.md to add an unknown key
        directory = _write_runbook_dir(base=tmp_path)
        runbook_md = (directory / "RUNBOOK.md").read_text(encoding="utf-8")
        injected = runbook_md.replace(
            "mnpi_safe: true",
            "mnpi_safe: true\nunexpected_key: surprise",
        )
        (directory / "RUNBOOK.md").write_text(injected, encoding="utf-8")

        # When the loader tries to read it
        # Then a RunbookSchemaError mentions the unexpected key
        with pytest.raises(models.RunbookSchemaError, match="unexpected_key"):
            loader.load_runbook(directory)

    def test_missing_required_frontmatter_key_raises(self, tmp_path: Path) -> None:
        # Given a runbook with mnpi_safe stripped from the frontmatter
        directory = _write_runbook_dir(base=tmp_path)
        runbook_md = (directory / "RUNBOOK.md").read_text(encoding="utf-8")
        broken = runbook_md.replace("mnpi_safe: true\n", "")
        (directory / "RUNBOOK.md").write_text(broken, encoding="utf-8")

        # When loading
        # Then a RunbookSchemaError surfaces the missing key
        with pytest.raises(models.RunbookSchemaError, match="mnpi_safe"):
            loader.load_runbook(directory)

    def test_missing_sidecar_yaml_raises(self, tmp_path: Path) -> None:
        # Given a runbook directory with checks.yaml deleted
        directory = _write_runbook_dir(base=tmp_path)
        (directory / "checks.yaml").unlink()

        # When the loader tries to read it
        # Then it raises RunbookSchemaError pointing at checks.yaml
        with pytest.raises(models.RunbookSchemaError, match="checks.yaml"):
            loader.load_runbook(directory)

    def test_unknown_tools_yaml_key_raises(self, tmp_path: Path) -> None:
        # Given a tools.yaml with an unrecognised top-level key
        directory = _write_runbook_dir(
            base=tmp_path,
            tools_text=_DEFAULT_TOOLS + "extra_field: value\n",
        )

        # When loading
        # Then a RunbookSchemaError lists the unexpected key
        with pytest.raises(models.RunbookSchemaError, match="extra_field"):
            loader.load_runbook(directory)

    def test_invalid_severity_min_raises(self, tmp_path: Path) -> None:
        # Given a runbook whose severity_min is outside the firm-standard P1..P5 scale
        directory = _write_runbook_dir(base=tmp_path, severity_min="P9")

        # When loading
        # Then a RunbookSchemaError surfaces the invalid severity field
        with pytest.raises(models.RunbookSchemaError, match="severity_min"):
            loader.load_runbook(directory)


class TestFrontmatterDefaults:
    def test_min_match_score_defaults_to_2_when_omitted(self, tmp_path: Path) -> None:
        # Given a runbook directory then its RUNBOOK.md has min_match_score stripped
        directory = _write_runbook_dir(base=tmp_path)
        runbook_md = (directory / "RUNBOOK.md").read_text(encoding="utf-8")
        without_score = runbook_md.replace("min_match_score: 2\n", "")
        (directory / "RUNBOOK.md").write_text(without_score, encoding="utf-8")

        # When the loader reads it
        runbook = loader.load_runbook(directory)

        # Then min_match_score defaults to 2 per spec §4.2
        assert runbook.metadata.min_match_score == 2


class TestContentShaCanonicalisation:
    def test_sha_is_stable_across_yaml_whitespace_drift(self, tmp_path: Path) -> None:
        # Given two runbooks identical except for benign whitespace in tools.yaml
        whitespace_drift_tools = _DEFAULT_TOOLS.replace(
            "denied_tools: []\n", "\ndenied_tools: []\n\n"
        )
        directory_canonical = _write_runbook_dir(base=tmp_path / "canonical")
        directory_drifted = _write_runbook_dir(
            base=tmp_path / "drifted", tools_text=whitespace_drift_tools
        )

        # When both are loaded
        canonical_runbook = loader.load_runbook(directory_canonical)
        drifted_runbook = loader.load_runbook(directory_drifted)

        # Then content_sha is unchanged because yaml is canonicalised before hashing
        assert canonical_runbook.metadata.content_sha == drifted_runbook.metadata.content_sha


class TestBodySanitization:
    def test_zero_width_chars_stripped_from_body(self, tmp_path: Path) -> None:
        # Given a runbook body laden with zero-width space and BOM
        body_with_zwsp = "Hidden\u200btext﻿here"
        directory = _write_runbook_dir(base=tmp_path, body=body_with_zwsp)

        # When the loader reads it
        runbook = loader.load_runbook(directory)

        # Then the rendered body has neither character
        assert "\u200b" not in runbook.body
        assert "﻿" not in runbook.body
        assert "Hiddentexthere" in runbook.body

    def test_rejects_auto_rendered_url_when_configured(self, tmp_path: Path) -> None:
        # Given a runbook body containing a [text](url) markdown link with rejection on
        directory = _write_runbook_dir(
            base=tmp_path,
            body="See [the docs](https://example.com) for context.",
            reject_urls="true",
        )

        # When loading
        # Then it raises RunbookSanitizationError pointing the author at canonical_sources
        with pytest.raises(models.RunbookSanitizationError, match="canonical_sources"):
            loader.load_runbook(directory)

    def test_allows_auto_rendered_url_when_rule_disabled(self, tmp_path: Path) -> None:
        # Given a runbook body with a markdown URL but rule turned off
        directory = _write_runbook_dir(
            base=tmp_path,
            body="See [the docs](https://example.com).",
            reject_urls="false",
        )

        # When loading
        runbook = loader.load_runbook(directory)

        # Then it loads successfully
        assert "[the docs](https://example.com)" in runbook.body


class TestLifecycle:
    def test_deprecated_runbook_still_loads(self, tmp_path: Path) -> None:
        # Given a runbook with deprecated_at set in frontmatter
        directory = _write_runbook_dir(
            base=tmp_path,
            deprecated_at="2026-03-01",
            superseded_by="k8s-crashloop-v2",
        )

        # When loading (matcher's job, not loader's, to skip deprecated runbooks)
        runbook = loader.load_runbook(directory)

        # Then loader returns a Runbook with the lifecycle fields populated
        assert runbook.metadata.deprecated_at == date(2026, 3, 1)
        assert runbook.metadata.superseded_by == "k8s-crashloop-v2"


class TestDiscoverRunbooks:
    def test_walks_each_root_in_declared_order(self, tmp_path: Path) -> None:
        # Given two runbooks across two roots, distinct ids
        common_root = tmp_path / "common"
        sre_root = tmp_path / "sre"
        _write_runbook_dir(base=common_root, runbook_id="generic-investigation")
        _write_runbook_dir(base=sre_root, runbook_id="k8s-crashloop")

        # When discover_runbooks walks both
        catalog = loader.discover_runbooks((sre_root, common_root))

        # Then both runbooks appear
        assert set(catalog.keys()) == {"k8s-crashloop", "generic-investigation"}

    def test_first_wins_on_runbook_id_collision(self, tmp_path: Path) -> None:
        # Given the same runbook id in both roots, with body differences for sha drift
        team_root = tmp_path / "team"
        common_root = tmp_path / "common"
        team_dir = _write_runbook_dir(base=team_root, runbook_id="overlap", body="team body")
        common_dir = _write_runbook_dir(base=common_root, runbook_id="overlap", body="common body")

        # When walking team-first then common
        with mock.patch.object(logs, "log_event") as mocked:
            catalog = loader.discover_runbooks((team_root, common_root))

        # Then the team copy wins and a runbook_override event was logged for the shadowed common
        assert catalog["overlap"].directory == team_dir
        override_calls = [
            call
            for call in mocked.call_args_list
            if call.args and call.args[0] == "runbook_override"
        ]
        assert len(override_calls) == 1
        params = override_calls[0].kwargs["params"]
        assert params["runbook_id"] == "overlap"
        assert params["winning_source_dir"] == str(team_dir)
        assert params["shadowed_source_dir"] == str(common_dir)

    def test_skips_directories_without_runbook_md(self, tmp_path: Path) -> None:
        # Given one valid runbook dir and one empty directory in the same root
        root = tmp_path / "root"
        _write_runbook_dir(base=root, runbook_id="real-one")
        (root / "stub").mkdir(parents=True)

        # When discover_runbooks walks the root
        catalog = loader.discover_runbooks((root,))

        # Then only the runbook with RUNBOOK.md appears
        assert set(catalog.keys()) == {"real-one"}

    def test_returns_empty_when_root_does_not_exist(self, tmp_path: Path) -> None:
        # Given a path to a directory that does not exist
        ghost_root = tmp_path / "does-not-exist"

        # When discover_runbooks is called
        catalog = loader.discover_runbooks((ghost_root,))

        # Then an empty mapping is returned (no schema error)
        assert dict(catalog) == {}
