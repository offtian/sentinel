"""
F8 — Deterministic groundedness gate (RFC §5.4, R-QG-1).

Every Finding in a completed investigation must cite at least one evidence
reference (a source queried during the investigation). Findings without refs
indicate the LLM synthesised claims without tool-call backing — the primary
hallucination signal Sentinel's quality gate must block before publishing.

Vacuous-pass rules (no gate applied):
- investigation_status in ("skipped", "failed") — no tool calls were possible
- no findings at all — nothing to ground

Soft-fail behaviour (F8 foundations decision):
- When the verdict fails, the pipeline forces needs_approval=True rather than
  hard-terminating the investigation. A human reviewer sees the low-confidence
  result and can reject or approve with context.
"""

from __future__ import annotations

from collections.abc import Sequence

import attrs

from sentinel.domain.investigations import entities as investigation_entities


# Statuses where no investigation tool calls ran; the gate vacuously passes
# because grounding against zero evidence would always fail.
_NO_INVESTIGATION_STATUSES: frozenset[str] = frozenset({"skipped", "failed"})


@attrs.frozen(kw_only=True, slots=True)
class GroundednessVerdict:
    """
    Result of a deterministic groundedness assessment (RFC §5.4).

    ``passed`` is ``True`` iff every finding in the assessment carried at
    least one ``evidence_ref``, or the investigation ran no tool calls
    (vacuous pass), or there were no findings.

    ``missing_evidence_finding_indices`` lists the zero-based positions of
    findings that lacked any evidence reference — empty on pass.

    ``reason`` is a human-readable summary suitable for log events and the
    ``quality_verdict.verdict_reason`` DB column.
    """

    passed: bool
    missing_evidence_finding_indices: tuple[int, ...]
    reason: str


def assess_groundedness(
    *,
    findings: Sequence[investigation_entities.Finding],
    investigation_status: str,
) -> GroundednessVerdict:
    """
    Return a :class:`GroundednessVerdict` for the supplied findings.

    :param findings: The ordered list of findings produced by the analysis
        node. Each finding must carry at least one ``evidence_ref`` to pass.
    :param investigation_status: Status string from
        ``_investigation_context["status"]``. One of ``"ran"``, ``"empty"``,
        ``"skipped"``, ``"failed"``.
    :returns: A frozen verdict object.
    """
    if investigation_status in _NO_INVESTIGATION_STATUSES:
        return GroundednessVerdict(
            passed=True,
            missing_evidence_finding_indices=(),
            reason="no investigation performed",
        )

    if not findings:
        return GroundednessVerdict(
            passed=True,
            missing_evidence_finding_indices=(),
            reason="no findings to ground",
        )

    missing = tuple(i for i, f in enumerate(findings) if not f.evidence_refs)
    if missing:
        count = len(missing)
        return GroundednessVerdict(
            passed=False,
            missing_evidence_finding_indices=missing,
            reason=f"{count} finding(s) lack evidence references",
        )

    return GroundednessVerdict(
        passed=True,
        missing_evidence_finding_indices=(),
        reason="all findings grounded",
    )
