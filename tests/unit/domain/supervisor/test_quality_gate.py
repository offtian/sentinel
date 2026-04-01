from __future__ import annotations

import attrs
import pytest

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.supervisor import entities, quality_gate
from sentinel.interfaces.graphs import common


def _make_sre_reply(
    *,
    alert_id: str = "P123ABC",
    root_cause: str | None = "Memory leak in request handler caused OOMKill",
    remediation: str | None = "1. Increase memory limit to 4Gi\n2. Deploy fix for handler",
    confidence: confidence_entities.ConfidenceScore | None = None,
    findings_summary: str = "- [datadog_logs] Error rate spike\n- [kubernetes] Pod OOMKilled",
) -> common.InvestigationReply:
    if confidence is None:
        confidence = confidence_entities.ConfidenceScore.from_total(0.75)
    return common.InvestigationReply(
        alert_id=alert_id,
        root_cause=root_cause,
        remediation=remediation,
        confidence=confidence,
        findings_summary=findings_summary,
    )


def _make_support_reply(
    *,
    ticket_id: str = "10001",
    ticket_key: str = "SUPPORT-42",
    suggested_response: str = (
        "Hi Jane,\n\n"
        "It sounds like your SSO session may have expired. Please try:\n"
        "1. Clear your browser cookies\n"
        "2. Visit /account/reset to reset your password\n\n"
        "Let us know if this resolves the issue."
    ),
    sources: list[dict[str, str]] | None = None,
    confidence: confidence_entities.ConfidenceScore | None = None,
    category: str | None = "account",
) -> common.SupportReply:
    if confidence is None:
        confidence = confidence_entities.ConfidenceScore.from_total(0.75)
    if sources is None:
        sources = [{"title": "Login Guide", "url": "https://docs.example.com/login"}]
    return common.SupportReply(
        ticket_id=ticket_id,
        ticket_key=ticket_key,
        suggested_response=suggested_response,
        sources=sources,
        confidence=confidence,
        category=category,
    )


class TestEvaluateSreQuality:
    def test_passes_for_complete_high_quality_reply(self) -> None:
        # Given a complete SRE reply with root cause, remediation, and confidence
        reply = _make_sre_reply()

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it passes with no issues and a high score
        assert verdict.passed is True
        assert verdict.issues == ()
        assert verdict.score > 0.7

    def test_fails_when_root_cause_is_none(self) -> None:
        # Given a reply with no root cause
        reply = _make_sre_reply(root_cause=None)

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it fails with a root_cause issue
        assert verdict.passed is False
        assert any("root_cause is None" in issue for issue in verdict.issues)

    def test_fails_when_root_cause_is_empty(self) -> None:
        # Given a reply with an empty root cause
        reply = _make_sre_reply(root_cause="  ")

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it fails with a root_cause issue
        assert verdict.passed is False
        assert any("root_cause is empty" in issue for issue in verdict.issues)

    def test_fails_when_root_cause_is_generic(self) -> None:
        # Given a reply with a generic fallback root cause
        reply = _make_sre_reply(
            root_cause="Root cause analysis unavailable -- LLM error. Manual investigation required.",
        )

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it fails with a generic text issue
        assert verdict.passed is False
        assert any("generic" in issue for issue in verdict.issues)

    def test_fails_when_remediation_is_none(self) -> None:
        # Given a reply with no remediation
        reply = _make_sre_reply(remediation=None)

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it fails
        assert verdict.passed is False
        assert any("remediation is None" in issue for issue in verdict.issues)

    def test_fails_when_remediation_is_generic(self) -> None:
        # Given a reply with generic remediation text
        reply = _make_sre_reply(remediation="Please investigate this alert manually.")

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it flags generic remediation
        assert verdict.passed is False
        assert any("generic" in issue for issue in verdict.issues)

    def test_fails_when_remediation_lacks_actionable_steps(self) -> None:
        # Given a reply with a vague single-line remediation
        reply = _make_sre_reply(remediation="Fix the memory issue somehow.")

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it flags lack of actionable steps
        assert verdict.passed is False
        assert any("actionable" in issue for issue in verdict.issues)

    def test_fails_when_confidence_is_missing(self) -> None:
        # Given a reply with no confidence score
        reply = _make_sre_reply(
            confidence=confidence_entities.ConfidenceScore.from_total(0.75),
        )
        # Override to None via model_copy
        reply = reply.model_copy(update={"confidence": None})

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it flags missing confidence
        assert verdict.passed is False
        assert any("confidence" in issue for issue in verdict.issues)

    def test_fails_when_confidence_is_extremely_low(self) -> None:
        # Given a reply with an extremely low confidence score
        reply = _make_sre_reply(
            confidence=confidence_entities.ConfidenceScore.from_total(0.1),
        )

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it flags low confidence
        assert verdict.passed is False
        assert any("extremely low" in issue for issue in verdict.issues)

    def test_fails_when_findings_summary_empty_but_root_cause_present(self) -> None:
        # Given a reply with a root cause but empty findings summary
        reply = _make_sre_reply(findings_summary="")

        # When we evaluate quality
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it flags the empty findings summary
        assert verdict.passed is False
        assert any("findings_summary" in issue for issue in verdict.issues)

    def test_score_decreases_with_more_issues(self) -> None:
        # Given two replies: one with one issue, one with multiple issues
        one_issue_reply = _make_sre_reply(remediation=None)
        many_issues_reply = _make_sre_reply(
            root_cause=None,
            remediation=None,
            findings_summary="",
        )
        many_issues_reply = many_issues_reply.model_copy(update={"confidence": None})

        # When we evaluate both
        one_issue_verdict = quality_gate.evaluate_sre_quality(reply=one_issue_reply)
        many_issues_verdict = quality_gate.evaluate_sre_quality(reply=many_issues_reply)

        # Then the reply with more issues has a lower score
        assert many_issues_verdict.score < one_issue_verdict.score

    def test_verdict_is_immutable(self) -> None:
        # Given a quality verdict
        reply = _make_sre_reply()
        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        # Then it should be frozen (immutable)
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            verdict.passed = False  # type: ignore[misc]


class TestEvaluateSupportQuality:
    def test_passes_for_complete_high_quality_reply(self) -> None:
        # Given a complete support reply
        reply = _make_support_reply()

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it passes
        assert verdict.passed is True
        assert verdict.issues == ()
        assert verdict.score > 0.7

    def test_fails_when_suggested_response_is_empty(self) -> None:
        # Given a reply with an empty response
        reply = _make_support_reply(suggested_response="")

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags the empty response
        assert verdict.passed is False
        assert any("empty" in issue for issue in verdict.issues)

    def test_fails_when_suggested_response_is_generic(self) -> None:
        # Given a reply with generic fallback text
        reply = _make_support_reply(
            suggested_response="No relevant documentation found for this ticket. Manual review recommended.",
        )

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags generic text
        assert verdict.passed is False
        assert any("generic" in issue for issue in verdict.issues)

    def test_fails_when_suggested_response_is_suspiciously_short(self) -> None:
        # Given a reply with a very short response
        reply = _make_support_reply(suggested_response="Try restarting your computer.")

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags the short response
        assert verdict.passed is False
        assert any("short" in issue for issue in verdict.issues)

    def test_fails_when_confidence_is_missing(self) -> None:
        # Given a reply with no confidence
        reply = _make_support_reply()
        reply = reply.model_copy(update={"confidence": None})

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags missing confidence
        assert verdict.passed is False
        assert any("confidence" in issue for issue in verdict.issues)

    def test_fails_when_no_sources_cited(self) -> None:
        # Given a reply with no sources
        reply = _make_support_reply(sources=[])

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags missing sources
        assert verdict.passed is False
        assert any("sources" in issue for issue in verdict.issues)

    def test_fails_when_category_is_missing(self) -> None:
        # Given a reply with no category
        reply = _make_support_reply(category=None)

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags missing category
        assert verdict.passed is False
        assert any("category" in issue for issue in verdict.issues)

    def test_fails_when_category_is_empty_string(self) -> None:
        # Given a reply with an empty category
        reply = _make_support_reply(category="  ")

        # When we evaluate quality
        verdict = quality_gate.evaluate_support_quality(reply=reply)

        # Then it flags missing category
        assert verdict.passed is False
        assert any("category" in issue for issue in verdict.issues)

    def test_score_blends_with_confidence(self) -> None:
        # Given two replies with different confidence levels but both passing
        high_confidence_reply = _make_support_reply(
            confidence=confidence_entities.ConfidenceScore.from_total(0.95),
        )
        low_confidence_reply = _make_support_reply(
            confidence=confidence_entities.ConfidenceScore.from_total(0.45),
        )

        # When we evaluate both
        high_verdict = quality_gate.evaluate_support_quality(reply=high_confidence_reply)
        low_verdict = quality_gate.evaluate_support_quality(reply=low_confidence_reply)

        # Then higher confidence yields a higher score
        assert high_verdict.score > low_verdict.score


class TestContainsGenericPhrase:
    def test_detects_phrase_case_insensitively(self) -> None:
        # Given text with a generic phrase in different case
        text = "Root Cause Analysis Unavailable -- please check manually"

        # When we check for generic phrases
        result = quality_gate._contains_generic_phrase(
            text, quality_gate._GENERIC_ROOT_CAUSE_PHRASES
        )

        # Then it detects the match
        assert result is True

    def test_returns_false_for_specific_text(self) -> None:
        # Given text that is specific and actionable
        text = "Memory leak in the connection pool caused OOMKill on pod api-service-7b8c"

        # When we check for generic phrases
        result = quality_gate._contains_generic_phrase(
            text, quality_gate._GENERIC_ROOT_CAUSE_PHRASES
        )

        # Then no match is found
        assert result is False


class TestHasActionableSteps:
    def test_detects_numbered_steps(self) -> None:
        # Given remediation with numbered steps
        text = "1. Increase memory limit\n2. Deploy the fix"

        # When we check for actionable steps
        result = quality_gate._has_actionable_steps(text)

        # Then it is considered actionable
        assert result is True

    def test_detects_bullet_points(self) -> None:
        # Given remediation with bullet points
        text = "- Restart the service\n- Monitor for recurrence"

        # When we check for actionable steps
        result = quality_gate._has_actionable_steps(text)

        # Then it is considered actionable
        assert result is True

    def test_rejects_vague_single_line(self) -> None:
        # Given a vague single-line remediation
        text = "Fix the issue by looking at the logs."

        # When we check for actionable steps
        result = quality_gate._has_actionable_steps(text)

        # Then it is not considered actionable
        assert result is False
