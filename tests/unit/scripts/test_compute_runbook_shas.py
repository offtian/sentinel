"""
Unit tests for ``scripts/compute_runbook_shas.py`` (F6.E pre-commit hook).

Exercises:
- placeholder content_sha gets re-written with the loader-computed sha;
- already-current SHAs are left untouched (idempotent);
- ``--check`` mode returns 1 on drift without writing;
- ``--check`` mode returns 0 on a clean tree;
- empty tree path-list returns 0;
- a single broken runbook does not block the rest of the batch.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import frontmatter
import pytest


_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

compute_runbook_shas = importlib.import_module("compute_runbook_shas")


_DEFAULT_TOOLS = """\
allowed_tools:
  - name: k8s_describe_pod
    max_calls: 5
denied_tools: []
max_total_tool_calls: 30
max_loop_iterations: 8
"""

_DEFAULT_CHECKS = """\
prescribed_checks:
  - id: confirm_pod_state
    description: Confirm pod is actually CrashLooping
    suggested_tools: [k8s_describe_pod]
    required: true
groundedness_rules:
  - rule_id: every_finding_has_evidence
    description: Every finding must cite an evidence_ref
body_sanitization:
  reject_auto_rendered_urls: true
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


def _write_runbook_quartet(
    *,
    base: Path,
    runbook_id: str = "k8s-crashloop",
    content_sha: str = "PLACEHOLDER",
    body: str = "## Goal\n\nInvestigate the crashloop.\n",
    extra_frontmatter_key: str | None = None,
) -> Path:
    """
    Write a four-file runbook quartet under ``base / runbook_id``.

    ``extra_frontmatter_key`` lets tests inject an unexpected key to trigger
    schema validation failure inside the loader.
    """
    directory = base / runbook_id
    directory.mkdir(parents=True, exist_ok=True)
    extra_line = f"{extra_frontmatter_key}: poison\n" if extra_frontmatter_key else ""
    runbook_md = (
        "---\n"
        f"runbook_id: {runbook_id}\n"
        "description: |\n"
        "  Test runbook for hash-script unit tests.\n"
        f"content_sha: {content_sha}\n"
        "applies_to:\n"
        "  alertnames: [KubePodCrashLooping]\n"
        "  severity_min: P3\n"
        "  resource_kinds: [Pod]\n"
        "  exclude_labels: {}\n"
        "tags:\n"
        "  - { key: cluster_class, value: production }\n"
        "min_match_score: 2\n"
        "owner: sre-platform\n"
        "authors: [ollie.tian]\n"
        "last_validated: 2026-04-26\n"
        "deprecated_at: null\n"
        "superseded_by: null\n"
        "mnpi_safe: true\n"
        "canonical_sources: []\n"
        f"{extra_line}"
        "---\n"
        "\n"
        f"# {runbook_id}\n"
        f"\n{body}"
    )
    (directory / "RUNBOOK.md").write_text(runbook_md, encoding="utf-8")
    (directory / "tools.yaml").write_text(_DEFAULT_TOOLS, encoding="utf-8")
    (directory / "checks.yaml").write_text(_DEFAULT_CHECKS, encoding="utf-8")
    (directory / "tests.yaml").write_text(_DEFAULT_TESTS, encoding="utf-8")
    return directory


def _read_frontmatter_sha(runbook_dir: Path) -> str | None:
    runbook_md = runbook_dir / "RUNBOOK.md"
    return frontmatter.loads(runbook_md.read_text(encoding="utf-8")).get("content_sha")


class TestMain:
    def test_writes_sha_when_placeholder(self, tmp_path: Path) -> None:
        # Given a runbook quartet whose frontmatter sha is the literal placeholder
        _write_runbook_quartet(base=tmp_path)

        # When the script runs in write mode against tmp_path
        return_code = compute_runbook_shas.main(["--paths", str(tmp_path)])

        # Then it returns 0 and rewrites the frontmatter with the real 32-char sha
        assert return_code == 0
        new_sha = _read_frontmatter_sha(tmp_path / "k8s-crashloop")
        assert isinstance(new_sha, str)
        assert new_sha != "PLACEHOLDER"
        assert len(new_sha) == 32
        assert all(character in "0123456789abcdef" for character in new_sha)

    def test_no_change_when_already_current(self, tmp_path: Path) -> None:
        # Given a runbook quartet first hashed by the script (now clean)
        _write_runbook_quartet(base=tmp_path)
        compute_runbook_shas.main(["--paths", str(tmp_path)])
        runbook_md = tmp_path / "k8s-crashloop" / "RUNBOOK.md"
        before_text = runbook_md.read_text(encoding="utf-8")
        before_mtime = runbook_md.stat().st_mtime_ns

        # When the script runs again on the same clean tree
        return_code = compute_runbook_shas.main(["--paths", str(tmp_path)])

        # Then it returns 0 and the file content + mtime are unchanged
        assert return_code == 0
        assert runbook_md.read_text(encoding="utf-8") == before_text
        assert runbook_md.stat().st_mtime_ns == before_mtime

    def test_check_mode_returns_1_on_drift(self, tmp_path: Path) -> None:
        # Given a runbook quartet whose frontmatter sha is stale
        _write_runbook_quartet(base=tmp_path, content_sha="deadbeef" * 4)
        runbook_md = tmp_path / "k8s-crashloop" / "RUNBOOK.md"
        before_text = runbook_md.read_text(encoding="utf-8")

        # When the script runs in --check mode
        return_code = compute_runbook_shas.main(["--check", "--paths", str(tmp_path)])

        # Then it returns 1 and leaves the frontmatter untouched (CI must not write)
        assert return_code == 1
        assert runbook_md.read_text(encoding="utf-8") == before_text

    def test_check_mode_returns_0_when_clean(self, tmp_path: Path) -> None:
        # Given a runbook quartet whose sha is already up-to-date
        _write_runbook_quartet(base=tmp_path)
        compute_runbook_shas.main(["--paths", str(tmp_path)])

        # When the script runs in --check mode
        return_code = compute_runbook_shas.main(["--check", "--paths", str(tmp_path)])

        # Then it returns 0
        assert return_code == 0

    def test_no_runbooks_returns_0(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        # Given a paths argument pointing to an empty directory
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        # When the script runs against it
        return_code = compute_runbook_shas.main(["--paths", str(empty_dir)])

        # Then it returns 0 (no-op) without raising
        assert return_code == 0

    def test_schema_error_in_one_runbook_does_not_block_others(self, tmp_path: Path) -> None:
        # Given two quartets — one valid with a placeholder, one with a poisoned frontmatter key
        valid_dir = _write_runbook_quartet(
            base=tmp_path, runbook_id="valid-runbook", content_sha="PLACEHOLDER"
        )
        _write_runbook_quartet(
            base=tmp_path,
            runbook_id="broken-runbook",
            extra_frontmatter_key="unexpected_key",
        )

        # When the script runs in write mode against the parent directory
        return_code = compute_runbook_shas.main(["--paths", str(tmp_path)])

        # Then the run reports failure (return code 1) but the valid runbook's sha was still written
        assert return_code == 1
        valid_sha = _read_frontmatter_sha(valid_dir)
        assert valid_sha != "PLACEHOLDER"
        assert isinstance(valid_sha, str)
        assert len(valid_sha) == 32
