"""
Three-stage runbook matcher (F6 spec §5) with optional Stage 3 RAG fallback (F6.J).

Stage 1 — deterministic tag pre-filter: ranks every non-deprecated runbook
whose ``applies_to`` block accepts the alert by exact-match count over
``alertnames`` + ``tags``, applying the per-runbook ``min_match_score``
threshold. Stable ordering by ``(-score, runbook_id)``.

Stage 2A — tie disambiguation: when more than one Stage-1 candidate ties at
the top score, a small LLM picks one (or returns ``no_match``).
Confidence threshold ≥ 0.5. Transport-level LLM failure
(``DisambiguatorUnavailableError``) falls back to alphabetical tiebreak.

Stage 2B — zero-match rescue: when Stage 1 yields nothing, pre-filtered
eligible runbooks (severity / resource_kind / mnpi compatible, top-N=8 by
alphabetical id) are presented to the same LLM with the explicit
``no_match`` option. Confidence threshold ≥ 0.6. LLM failure short-circuits
to a straight no-match.

Stage 3 — RAG / pgvector fallback (F6.J): when Stage 2B returns
``no_match`` and a :class:`sentinel.domain.runbooks.rag.RagFallback` is
supplied with ``enabled=True``, the matcher embeds the alert summary and
queries the pre-indexed runbook embeddings for the closest cosine match.
Above ``min_similarity``, the top candidate wins with ``match_method="rag"``;
top-k evidence rows are persisted under a freshly-allocated ``match_id``
recorded back on the ``RagFallback`` for the persistence layer to reuse.

The disambiguator is passed in as a callable (``DisambiguatorFn``) so the
matcher stays purely functional and testable without binding to PydanticAI.
:mod:`sentinel.interfaces.graphs.agents.runbook_disambiguator` provides the
production binding.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.runbooks import models, rag
from sentinel.utils import logs


# Severity scale: P1 highest, P5 lowest. An alert at severity X is "compatible"
# with a runbook requiring severity_min Y iff X is at least as severe as Y
# (i.e. P1 alert is compatible with all severity_min levels; P5 alert only
# with runbooks requiring P5).
_SEVERITY_RANK: dict[str, int] = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5}

_TIE_DISAMBIGUATOR_TOP_K = 3
_ZERO_MATCH_TOP_N = 8
_TIE_CONFIDENCE_THRESHOLD = 0.5
_ZERO_MATCH_CONFIDENCE_THRESHOLD = 0.6


class MatchableAlert(Protocol):
    """
    Minimal alert shape the matcher consumes.

    Defined as a Protocol so callers can pass any alert-like object — the
    ingest pipeline's ``Alert`` entity, a webhook DTO, or a test stub —
    without importing matcher-specific types into the alert module.
    """

    @property
    def alertname(self) -> str: ...

    @property
    def severity(self) -> str: ...

    @property
    def resource_kind(self) -> str: ...

    @property
    def labels(self) -> Mapping[str, str]: ...

    @property
    def pii_class(self) -> str: ...


# A disambiguator function takes the alert summary + (id, description) candidate
# tuples and returns a validated DisambiguatorChoice. Async because the
# production binding calls a PydanticAI agent over LiteLLM.
DisambiguatorFn = Callable[
    [str, tuple[tuple[str, str], ...]],
    Awaitable[models.DisambiguatorChoice],
]


def _severity_compatible(*, alert_severity: str, runbook_severity_min: str) -> bool:
    """
    Return True when the alert is at least as severe as the runbook requires.

    Unknown severities (alert or runbook) are treated as incompatible — this
    fails closed and surfaces label-quality issues to operators.
    """
    alert_rank = _SEVERITY_RANK.get(alert_severity)
    runbook_rank = _SEVERITY_RANK.get(runbook_severity_min)
    if alert_rank is None or runbook_rank is None:
        return False
    return alert_rank <= runbook_rank


def _resource_kind_compatible(
    *, alert_resource_kind: str, runbook_resource_kinds: tuple[str, ...]
) -> bool:
    """Return True when the alert's resource_kind is in the runbook's allow list."""
    if not runbook_resource_kinds:
        return True
    return alert_resource_kind in runbook_resource_kinds


def _excluded_by_labels(
    *,
    alert_labels: Mapping[str, str],
    exclude_labels: Mapping[str, tuple[str, ...]],
) -> bool:
    """Return True when any alert label matches a runbook ``exclude_labels`` value."""
    for key, forbidden_values in exclude_labels.items():
        actual = alert_labels.get(key)
        if actual is not None and actual in forbidden_values:
            return True
    return False


def _count_tag_matches(
    *,
    alert: MatchableAlert,
    alertnames: tuple[str, ...],
    tags: tuple[models.RunbookTag, ...],
) -> int:
    """
    Return the number of exact tag matches between alert and runbook.

    +1 for ``alert.alertname`` appearing in the runbook's ``alertnames``.
    +1 per entry in ``tags`` whose key is in ``alert.labels`` and whose
    value matches.
    """
    score = 0
    if alert.alertname in alertnames:
        score += 1
    for tag in tags:
        if alert.labels.get(tag.key) == tag.value:
            score += 1
    return score


def stage_1_tag_match(
    *, alert: MatchableAlert, runbooks: Mapping[str, models.Runbook]
) -> tuple[models.RunbookCandidate, ...]:
    """
    Run the deterministic Stage 1 tag pre-filter.

    Returns the candidates sorted by ``(-score, runbook_id)`` so callers can
    detect ties at the top score by comparing the first two scores.
    """
    candidates: list[models.RunbookCandidate] = []
    for runbook in runbooks.values():
        meta = runbook.metadata
        # Skip workflow-internal runbooks: the leading underscore convention
        # (e.g. ``_sre-base``, ``_generic-investigation``) marks runbooks
        # that exist only as ``extends:`` parents or as the no-match fallback,
        # never as user-selectable Stage 1 candidates. Without this guard
        # they would tie at score 0 (their ``min_match_score=0`` + empty
        # ``alertnames``/``tags`` lets them pass the threshold) and burn
        # an LLM call on every alert. F6.K cross-cutting fix.
        if meta.runbook_id.startswith("_"):
            continue
        if meta.deprecated_at is not None:
            continue
        if not meta.mnpi_safe and alert.pii_class == "mnpi":
            continue
        if not _severity_compatible(
            alert_severity=alert.severity,
            runbook_severity_min=meta.applies_to.severity_min,
        ):
            continue
        if not _resource_kind_compatible(
            alert_resource_kind=alert.resource_kind,
            runbook_resource_kinds=meta.applies_to.resource_kinds,
        ):
            continue
        if _excluded_by_labels(
            alert_labels=alert.labels,
            exclude_labels=meta.applies_to.exclude_labels,
        ):
            continue
        score = _count_tag_matches(
            alert=alert,
            alertnames=meta.applies_to.alertnames,
            tags=meta.tags,
        )
        if score < meta.min_match_score:
            continue
        candidates.append(
            models.RunbookCandidate(
                runbook_id=meta.runbook_id,
                content_sha=meta.content_sha,
                score=score,
                matched_via="exact_tag",
            )
        )
    return tuple(sorted(candidates, key=lambda c: (-c.score, c.runbook_id)))


def _summarise_alert(alert: MatchableAlert) -> str:
    """Render a short alert summary for the disambiguator prompt."""
    label_pairs = ", ".join(f"{key}={value}" for key, value in sorted(alert.labels.items()))
    return (
        f"alertname={alert.alertname}, severity={alert.severity}, "
        f"resource_kind={alert.resource_kind}, labels=[{label_pairs}]"
    )


def _alphabetical_winner(
    candidates: tuple[models.RunbookCandidate, ...],
) -> models.RunbookCandidate:
    """Return the alphabetically-first candidate as the deterministic tiebreaker."""
    return min(candidates, key=lambda c: c.runbook_id)


async def stage_2a_tie_disambiguate(
    *,
    alert: MatchableAlert,
    candidates: tuple[models.RunbookCandidate, ...],
    runbooks: Mapping[str, models.Runbook],
    disambiguator: DisambiguatorFn,
) -> models.RunbookMatch:
    """
    Disambiguate a Stage 1 tie using the LLM. Falls back to alphabetical on failure.

    Caller is responsible for ensuring ``len(candidates) >= 2`` with shared
    top score.
    """
    top_score = candidates[0].score
    tied = tuple(c for c in candidates if c.score == top_score)
    capped = tied[:_TIE_DISAMBIGUATOR_TOP_K]

    prompt_inputs = tuple(
        (candidate.runbook_id, runbooks[candidate.runbook_id].metadata.description)
        for candidate in capped
    )
    summary = _summarise_alert(alert)

    try:
        choice = await disambiguator(summary, prompt_inputs)
    except models.DisambiguatorUnavailableError as exc:
        logs.log_event(
            "runbook_disambiguator_unavailable",
            params={
                "stage": "tie",
                "candidates": [c.runbook_id for c in capped],
                "error": str(exc),
            },
        )
        winner = _alphabetical_winner(capped)
        return models.RunbookMatch(
            matched_runbook_id=winner.runbook_id,
            content_sha=winner.content_sha,
            match_method="alphabetical_fallback",
            confidence=0.0,
            tag_score=winner.score,
            llm_choice=None,
            llm_justification=None,
            candidates=candidates,
        )

    candidate_ids = {c.runbook_id for c in capped}
    if (
        choice.chosen_runbook_id != "no_match"
        and choice.chosen_runbook_id in candidate_ids
        and choice.confidence >= _TIE_CONFIDENCE_THRESHOLD
    ):
        winning = next(c for c in capped if c.runbook_id == choice.chosen_runbook_id)
        return models.RunbookMatch(
            matched_runbook_id=winning.runbook_id,
            content_sha=winning.content_sha,
            match_method="llm_disambiguator_tie",
            confidence=choice.confidence,
            tag_score=winning.score,
            llm_choice=choice.chosen_runbook_id,
            llm_justification=choice.justification,
            candidates=candidates,
        )

    logs.log_event(
        "runbook_disambiguator_no_confidence",
        params={
            "stage": "tie",
            "candidates": [c.runbook_id for c in capped],
            "llm_choice": choice.chosen_runbook_id,
            "confidence": choice.confidence,
            "threshold": _TIE_CONFIDENCE_THRESHOLD,
        },
    )
    winner = _alphabetical_winner(capped)
    return models.RunbookMatch(
        matched_runbook_id=winner.runbook_id,
        content_sha=winner.content_sha,
        match_method="alphabetical_fallback",
        confidence=choice.confidence,
        tag_score=winner.score,
        llm_choice=choice.chosen_runbook_id,
        llm_justification=choice.justification,
        candidates=candidates,
    )


def _eligible_for_zero_match_rescue(*, alert: MatchableAlert, runbook: models.Runbook) -> bool:
    """Return True when ``runbook`` is a plausible zero-match-rescue candidate."""
    meta = runbook.metadata
    if meta.deprecated_at is not None:
        return False
    if not meta.mnpi_safe and alert.pii_class == "mnpi":
        return False
    if not _severity_compatible(
        alert_severity=alert.severity,
        runbook_severity_min=meta.applies_to.severity_min,
    ):
        return False
    return _resource_kind_compatible(
        alert_resource_kind=alert.resource_kind,
        runbook_resource_kinds=meta.applies_to.resource_kinds,
    )


def _no_match_result(
    candidates: tuple[models.RunbookCandidate, ...],
    *,
    confidence: float = 0.0,
    llm_choice: str | None = None,
    llm_justification: str | None = None,
) -> models.RunbookMatch:
    """Return a canonical no-match :class:`models.RunbookMatch`."""
    return models.RunbookMatch(
        matched_runbook_id=None,
        content_sha=None,
        match_method="no_match",
        confidence=confidence,
        tag_score=None,
        llm_choice=llm_choice,
        llm_justification=llm_justification,
        candidates=candidates,
    )


async def stage_2b_zero_match_rescue(
    *,
    alert: MatchableAlert,
    runbooks: Mapping[str, models.Runbook],
    disambiguator: DisambiguatorFn,
) -> models.RunbookMatch:
    """
    Run the LLM-based zero-match rescue. Falls back to no-match on LLM failure.
    """
    eligible = tuple(
        runbook
        for runbook in runbooks.values()
        if _eligible_for_zero_match_rescue(alert=alert, runbook=runbook)
    )
    eligible_sorted = sorted(eligible, key=lambda r: r.metadata.runbook_id)
    capped = tuple(eligible_sorted[:_ZERO_MATCH_TOP_N])

    rescue_candidates = tuple(
        models.RunbookCandidate(
            runbook_id=runbook.metadata.runbook_id,
            content_sha=runbook.metadata.content_sha,
            score=0,
            matched_via="llm",
        )
        for runbook in capped
    )

    if not capped:
        return _no_match_result(())

    prompt_inputs = tuple(
        (runbook.metadata.runbook_id, runbook.metadata.description) for runbook in capped
    )
    summary = _summarise_alert(alert)

    try:
        choice = await disambiguator(summary, prompt_inputs)
    except models.DisambiguatorUnavailableError as exc:
        logs.log_event(
            "runbook_disambiguator_unavailable",
            params={
                "stage": "zero_match_rescue",
                "eligible": [r.metadata.runbook_id for r in capped],
                "error": str(exc),
            },
        )
        return _no_match_result(rescue_candidates)

    candidate_ids = {r.metadata.runbook_id for r in capped}
    if (
        choice.chosen_runbook_id != "no_match"
        and choice.chosen_runbook_id in candidate_ids
        and choice.confidence >= _ZERO_MATCH_CONFIDENCE_THRESHOLD
    ):
        winning_runbook = next(
            r for r in capped if r.metadata.runbook_id == choice.chosen_runbook_id
        )
        return models.RunbookMatch(
            matched_runbook_id=winning_runbook.metadata.runbook_id,
            content_sha=winning_runbook.metadata.content_sha,
            match_method="llm_zero_match_rescue",
            confidence=choice.confidence,
            tag_score=None,
            llm_choice=choice.chosen_runbook_id,
            llm_justification=choice.justification,
            candidates=rescue_candidates,
        )

    return _no_match_result(
        rescue_candidates,
        confidence=choice.confidence,
        llm_choice=choice.chosen_runbook_id,
        llm_justification=choice.justification,
    )


async def stage_3_rag_fallback(
    *,
    alert: MatchableAlert,
    runbooks: Mapping[str, models.Runbook],
    rag_fallback: rag.RagFallback,
) -> models.RunbookMatch:
    """
    Run the F6.J Stage 3 pgvector RAG fallback. Falls back to no-match on miss.

    Generates a fresh ``match_id`` (recorded back on ``rag_fallback.last_match_id``
    so the persistence layer can reuse it on the ``runbook_match`` row) and
    persists top-k evidence rows whenever any candidate clears the
    ``min_similarity`` threshold. Returns a no-match result when the embedder
    raises, when no candidates are returned, or when no candidate is in the
    discovered catalog (defensive — runbook may have been removed between
    indexing and retrieval).
    """
    summary = _summarise_alert(alert)
    try:
        rag_candidates = await rag.retrieve_top_k(
            session=rag_fallback.session,
            query_text=summary,
            embedder=rag_fallback.embedder,
            k=rag_fallback.top_k,
            min_similarity=rag_fallback.min_similarity,
        )
    except rag.EmbedderUnavailableError as exc:
        logs.log_event(
            "runbook_rag_embedder_unavailable",
            params={
                "stage": "rag_fallback",
                "model_id": rag_fallback.embedder.model_id,
                "error": str(exc),
            },
        )
        rag_fallback.last_candidates = ()
        return _no_match_result(())

    if not rag_candidates:
        rag_fallback.last_candidates = ()
        return _no_match_result(())

    top_candidate = rag_candidates[0]
    matched_runbook = runbooks.get(top_candidate.runbook_id)
    if matched_runbook is None:
        logs.log_event(
            "runbook_rag_candidate_missing_from_catalog",
            params={
                "candidate_runbook_id": top_candidate.runbook_id,
                "candidate_content_sha": top_candidate.content_sha,
            },
        )
        rag_fallback.last_candidates = ()
        return _no_match_result(())

    match_id = uuid.uuid4()
    await rag.write_evidence_rows(
        session=rag_fallback.session,
        match_id=match_id,
        candidates=rag_candidates,
        embedder=rag_fallback.embedder,
    )
    rag_fallback.last_match_id = match_id
    rag_fallback.last_candidates = rag_candidates

    return models.RunbookMatch(
        matched_runbook_id=matched_runbook.metadata.runbook_id,
        content_sha=matched_runbook.metadata.content_sha,
        match_method="rag",
        confidence=top_candidate.cosine_similarity,
        tag_score=None,
        llm_choice=None,
        llm_justification=None,
        candidates=rag.to_runbook_candidates(rag_candidates),
    )


async def match_runbook(
    *,
    alert: MatchableAlert,
    envelope: envelope_mod.Envelope,
    runbooks: Mapping[str, models.Runbook],
    disambiguator: DisambiguatorFn,
    rag_fallback: rag.RagFallback | None = None,
) -> models.RunbookMatch:
    """
    Orchestrate the matcher pipeline. Always returns a :class:`models.RunbookMatch`.

    The ``envelope`` is accepted for symmetry with the pipeline node and to
    keep the API stable when future fields (e.g. tenant-scoped runbook
    overrides) need to participate in matching.

    When ``rag_fallback`` is provided and ``rag_fallback.enabled`` is True,
    a fourth path runs after Stage 2B returns no-match: the F6.J pgvector
    retrieval (Stage 3). The fallback is opt-in per environment via
    ``BaseConfiguration.enable_rag_fallback`` (leader-batched setting).
    """
    del envelope  # Reserved for future tenant-scoped match policy.

    candidates = stage_1_tag_match(alert=alert, runbooks=runbooks)

    if not candidates:
        rescue_match = await stage_2b_zero_match_rescue(
            alert=alert, runbooks=runbooks, disambiguator=disambiguator
        )
        if (
            rescue_match.match_method == "no_match"
            and rag_fallback is not None
            and rag_fallback.enabled
        ):
            return await stage_3_rag_fallback(
                alert=alert, runbooks=runbooks, rag_fallback=rag_fallback
            )
        return rescue_match

    top_score = candidates[0].score
    tied_at_top = sum(1 for c in candidates if c.score == top_score)
    if tied_at_top == 1:
        winner = candidates[0]
        return models.RunbookMatch(
            matched_runbook_id=winner.runbook_id,
            content_sha=winner.content_sha,
            match_method="tag",
            confidence=1.0,
            tag_score=winner.score,
            llm_choice=None,
            llm_justification=None,
            candidates=candidates,
        )

    return await stage_2a_tie_disambiguate(
        alert=alert,
        candidates=candidates,
        runbooks=runbooks,
        disambiguator=disambiguator,
    )
