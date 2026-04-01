from __future__ import annotations

from sentinel.domain.supervisor import entities
from sentinel.interfaces.graphs import common


# Phrases that indicate a generic/fallback response rather than real analysis.
_GENERIC_ROOT_CAUSE_PHRASES: tuple[str, ...] = (
    "manual investigation required",
    "classification failed",
    "root cause analysis unavailable",
    "investigation pending",
    "unable to determine",
)

_GENERIC_REMEDIATION_PHRASES: tuple[str, ...] = (
    "please investigate this alert manually",
    "manual review required",
    "contact support",
    "no remediation available",
)

_GENERIC_SUPPORT_PHRASES: tuple[str, ...] = (
    "manual review required",
    "manual review recommended",
    "classification failed",
    "response drafting failed",
    "no relevant documentation found",
)


def evaluate_sre_quality(*, reply: common.InvestigationReply) -> entities.QualityVerdict:
    """
    Evaluate quality of an SRE investigation output using deterministic rules.

    Check for hallucination signals such as missing root cause, generic
    remediation text, or absent confidence scores.
    """
    issues: list[str] = []

    issues.extend(
        _check_text_field(
            value=reply.root_cause,
            field_name="root_cause",
            generic_phrases=_GENERIC_ROOT_CAUSE_PHRASES,
        )
    )
    issues.extend(_check_remediation(reply.remediation))

    # Check confidence is present.
    if reply.confidence is None:
        issues.append("confidence score is missing")
    elif reply.confidence.total < 0.2:
        issues.append("confidence score is extremely low")

    # Check findings summary when we expect content.
    if reply.findings_summary.strip() == "" and reply.root_cause is not None:
        issues.append("findings_summary is empty despite having a root cause")

    score = _compute_sre_score(reply=reply, issue_count=len(issues))
    passed = len(issues) == 0

    return entities.QualityVerdict(
        passed=passed,
        issues=tuple(issues),
        score=score,
    )


def evaluate_support_quality(*, reply: common.SupportReply) -> entities.QualityVerdict:
    """
    Evaluate quality of a support review output using deterministic rules.

    Check for empty responses, generic fallback text, and missing metadata.
    """
    issues: list[str] = []

    # Check suggested_response quality.
    if reply.suggested_response.strip() == "":
        issues.append("suggested_response is empty")
    elif _contains_generic_phrase(reply.suggested_response, _GENERIC_SUPPORT_PHRASES):
        issues.append("suggested_response contains generic/fallback text")
    elif len(reply.suggested_response.strip()) < 50:
        issues.append("suggested_response is suspiciously short")

    # Check confidence is present.
    if reply.confidence is None:
        issues.append("confidence score is missing")
    elif reply.confidence.total < 0.2:
        issues.append("confidence score is extremely low")

    # Check sources -- a good response should cite documentation.
    if reply.sources is None or len(reply.sources) == 0:
        issues.append("no sources cited in response")

    # Check category was assigned.
    if reply.category is None or reply.category.strip() == "":
        issues.append("ticket category was not determined")

    score = _compute_support_score(reply=reply, issue_count=len(issues))
    passed = len(issues) == 0

    return entities.QualityVerdict(
        passed=passed,
        issues=tuple(issues),
        score=score,
    )


def _check_text_field(
    *,
    value: str | None,
    field_name: str,
    generic_phrases: tuple[str, ...],
) -> list[str]:
    """
    Validate a text field for presence, emptiness, and generic content.

    Return a list of issue descriptions (empty if the field passes).
    """
    if value is None:
        return [f"{field_name} is None"]
    if value.strip() == "":
        return [f"{field_name} is empty"]
    if _contains_generic_phrase(value, generic_phrases):
        return [f"{field_name} contains generic/fallback text"]
    return []


def _check_remediation(value: str | None) -> list[str]:
    """
    Validate a remediation field for presence, quality, and actionability.

    Return a list of issue descriptions (empty if the field passes).
    """
    base_issues = _check_text_field(
        value=value,
        field_name="remediation",
        generic_phrases=_GENERIC_REMEDIATION_PHRASES,
    )
    if base_issues:
        return base_issues
    # value is guaranteed non-None and non-empty at this point.
    assert value is not None
    if not _has_actionable_steps(value):
        return ["remediation lacks actionable steps"]
    return []


def _contains_generic_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """Return True if the text contains any of the generic/fallback phrases."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _has_actionable_steps(remediation: str) -> bool:
    """
    Return True if the remediation text contains numbered or bulleted steps.

    A single vague sentence is not considered actionable.
    """
    # Check for numbered steps (e.g. "1.", "2.") or bullet markers.
    lines = remediation.strip().splitlines()
    if len(lines) < 1:
        return False

    actionable_markers = ("1.", "2.", "-", "*", "- [")
    return any(line.strip().startswith(marker) for line in lines for marker in actionable_markers)


def _compute_sre_score(
    *,
    reply: common.InvestigationReply,
    issue_count: int,
) -> float:
    """
    Compute a 0.0-1.0 quality score for SRE output.

    Starts at 1.0 and deducts for each issue found. Also factors in
    the pipeline's own confidence score when available.
    """
    score = 1.0

    # Deduct per issue (diminishing: first issues are more impactful).
    deduction_per_issue = 0.25
    score -= min(issue_count * deduction_per_issue, 0.8)

    # Blend with pipeline confidence if available.
    if reply.confidence is not None:
        score = (score * 0.6) + (reply.confidence.total * 0.4)

    return round(max(0.0, min(score, 1.0)), 4)


def _compute_support_score(
    *,
    reply: common.SupportReply,
    issue_count: int,
) -> float:
    """
    Compute a 0.0-1.0 quality score for support output.

    Same approach as SRE: deduct per issue, blend with confidence.
    """
    score = 1.0

    deduction_per_issue = 0.25
    score -= min(issue_count * deduction_per_issue, 0.8)

    if reply.confidence is not None:
        score = (score * 0.6) + (reply.confidence.total * 0.4)

    return round(max(0.0, min(score, 1.0)), 4)
