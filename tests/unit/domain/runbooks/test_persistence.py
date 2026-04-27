"""
Unit tests for the runbook persistence module (F6.F.1).

Covers the pure-Python ``_serialise_candidates`` helper exhaustively
(matched, no-match, empty, multiple-stage scenarios). The async writer
functions are exercised end-to-end against a real Postgres in
``tests/integration/domain/runbooks/test_match_runbook_pipeline.py``;
mocking SQLAlchemy at this layer would just retest SQLAlchemy.
"""

from __future__ import annotations

from sentinel.domain.runbooks import models, persistence


class TestSerialiseCandidates:
    def test_returns_empty_list_for_no_candidates(self) -> None:
        # Given an empty candidate sequence (e.g. a no-match outcome with no eligible runbooks)
        candidates: tuple[models.RunbookCandidate, ...] = ()

        # When the helper runs
        payload = persistence._serialise_candidates(candidates)

        # Then it returns an empty list (not ``None``) so the JSONB column
        # always stores an array, not a SQL NULL
        assert payload == []

    def test_serialises_each_candidate_into_a_four_key_dict(self) -> None:
        # Given two Stage 1 candidates with distinct scores and shas
        primary_candidate = models.RunbookCandidate(
            runbook_id="k8s-crashloop",
            content_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            score=3,
            matched_via="exact_tag",
        )
        secondary_candidate = models.RunbookCandidate(
            runbook_id="k8s-pod-restart-thrash",
            content_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            score=2,
            matched_via="exact_tag",
        )

        # When the helper runs
        payload = persistence._serialise_candidates((primary_candidate, secondary_candidate))

        # Then each candidate becomes a dict with the four audit-row keys
        # in the exact F6 spec §5.5 shape (consumed by the F6.L drift
        # sweep + F6.M flywheel without re-executing the matcher)
        assert payload == [
            {
                "runbook_id": "k8s-crashloop",
                "content_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "tag_score": 3,
                "matched_via": "exact_tag",
            },
            {
                "runbook_id": "k8s-pod-restart-thrash",
                "content_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "tag_score": 2,
                "matched_via": "exact_tag",
            },
        ]

    def test_preserves_zero_score_candidates_from_stage_2b_rescue(self) -> None:
        # Given a Stage 2B zero-match-rescue candidate (score=0, matched_via="llm")
        rescue_candidate = models.RunbookCandidate(
            runbook_id="k8s-crashloop",
            content_sha="cccccccccccccccccccccccccccccccc",
            score=0,
            matched_via="llm",
        )

        # When the helper runs
        payload = persistence._serialise_candidates((rescue_candidate,))

        # Then the score-0 LLM-only candidate is preserved verbatim — the
        # F6.L drift sweep needs to distinguish "considered by Stage 2B but
        # not picked" from "not considered at all"
        assert payload == [
            {
                "runbook_id": "k8s-crashloop",
                "content_sha": "cccccccccccccccccccccccccccccccc",
                "tag_score": 0,
                "matched_via": "llm",
            },
        ]
