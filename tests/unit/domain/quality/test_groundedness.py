"""
Unit tests for domain.quality.groundedness — F8 deterministic groundedness gate.
"""

from __future__ import annotations

import pytest

from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.quality import groundedness as groundedness_mod


def _make_finding(
    *,
    source: str = "prometheus",
    summary: str = "CPU spike detected",
    evidence_refs: tuple[str, ...] = ("prometheus",),
) -> investigation_entities.Finding:
    return investigation_entities.Finding(
        source=source,
        summary=summary,
        evidence_refs=evidence_refs,
    )


class TestAssessGroundedness:
    def test_finding_with_evidence_ref_passes(self) -> None:
        # Given a finding with an evidence ref pointing to its source
        findings = (_make_finding(source="prometheus", evidence_refs=("prometheus",)),)

        # When groundedness is assessed for a completed investigation
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="ran",
        )

        # Then the verdict passes
        assert verdict.passed is True
        assert verdict.missing_evidence_finding_indices == ()

    def test_finding_without_evidence_ref_fails(self) -> None:
        # Given a finding with no evidence refs
        findings = (_make_finding(source="datadog", evidence_refs=()),)

        # When groundedness is assessed for a completed investigation
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="ran",
        )

        # Then the verdict fails with the index of the offending finding
        assert verdict.passed is False
        assert 0 in verdict.missing_evidence_finding_indices
        assert "1 finding" in verdict.reason

    def test_empty_findings_vacuously_passes(self) -> None:
        # Given no findings at all (e.g. classifier ran but no sources queried)
        findings: tuple[investigation_entities.Finding, ...] = ()

        # When groundedness is assessed
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="ran",
        )

        # Then the verdict passes vacuously
        assert verdict.passed is True
        assert "no findings" in verdict.reason

    def test_skipped_investigation_vacuously_passes(self) -> None:
        # Given a finding without evidence refs but the investigation was skipped
        findings = (_make_finding(evidence_refs=()),)

        # When groundedness is assessed with status=skipped
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="skipped",
        )

        # Then the verdict passes because no investigation ran (no tool calls possible)
        assert verdict.passed is True
        assert "no investigation" in verdict.reason

    def test_failed_investigation_vacuously_passes(self) -> None:
        # Given a finding without evidence refs but the investigation failed
        findings = (_make_finding(evidence_refs=()),)

        # When groundedness is assessed with status=failed
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="failed",
        )

        # Then the verdict passes because no tool calls were possible
        assert verdict.passed is True
        assert "no investigation" in verdict.reason

    def test_mixed_findings_partial_fail(self) -> None:
        # Given two findings — the first grounded, the second not
        findings = (
            _make_finding(source="prometheus", evidence_refs=("prometheus",)),
            _make_finding(source="datadog", evidence_refs=()),
        )

        # When groundedness is assessed
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="ran",
        )

        # Then the verdict fails and only reports the ungrounded index
        assert verdict.passed is False
        assert verdict.missing_evidence_finding_indices == (1,)

    def test_multiple_ungrounded_findings_reported(self) -> None:
        # Given three findings, all without evidence refs
        findings = (
            _make_finding(evidence_refs=()),
            _make_finding(evidence_refs=()),
            _make_finding(evidence_refs=()),
        )

        # When groundedness is assessed
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="ran",
        )

        # Then all three indices are reported
        assert verdict.passed is False
        assert verdict.missing_evidence_finding_indices == (0, 1, 2)
        assert "3 finding" in verdict.reason

    def test_groundedness_verdict_is_frozen(self) -> None:
        # Given a groundedness verdict
        verdict = groundedness_mod.assess_groundedness(
            findings=(_make_finding(),),
            investigation_status="ran",
        )

        # Then it is immutable (attrs.frozen)
        with pytest.raises((AttributeError, TypeError)):
            verdict.passed = False  # type: ignore[misc]

    def test_empty_investigation_status_treated_as_ran(self) -> None:
        # Given an ungrounded finding and an unrecognised status string
        findings = (_make_finding(evidence_refs=()),)

        # When groundedness is assessed with an empty status
        verdict = groundedness_mod.assess_groundedness(
            findings=findings,
            investigation_status="",
        )

        # Then the gate applies (unknown status → assume ran)
        assert verdict.passed is False
