"""
F6.J Stage 3 RAG fallback for the runbook matcher (RFC §4.2).

Three layers compose this module:

1. :class:`Embedder` Protocol — the abstract embedder contract.
2. :class:`LiteLLMEmbedder` — the production embedder, wraps the in-process
   ``litellm.aembedding`` SDK call. Same in-process path as the other LLM
   agents so the embedding I/O is captured into the F4 replay bundle as
   an ``LLMIOEntry`` with ``tool_name="runbook_embedder"``.
3. :func:`index_runbook` and :func:`retrieve_top_k` — the indexing and
   retrieval primitives the matcher's Stage 3 path depends on.

The retrieval path runs ``ORDER BY embedding <=> :query LIMIT k`` against
pgvector's HNSW index over ``vector_cosine_ops``. ``<=>`` is cosine
distance in pgvector (range ``[0, 2]``); cosine similarity is computed
as ``1 - distance`` and filtered by ``min_similarity``.

A separate :func:`write_evidence_rows` helper persists the top-k
candidates into ``runbook_rag_match_evidence`` so replay can reconstruct
the original ranking from the audit table without re-querying the live
HNSW index.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

import attrs
import litellm
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from sentinel.data.sql import runbook_embeddings as rag_records
from sentinel.domain.runbooks import models as runbook_models
from sentinel.utils import logs


# Length cap on each embedded section. Empty sections are skipped so the
# embedder never sees an empty prompt; long bodies are truncated at the
# byte boundary the embedder advertises (1536-d models cap input at ~8k
# tokens; we keep a generous floor that leaves headroom for the prompt
# template and tokeniser overhead).
_MAX_SECTION_CHARS: int = 32_000


# Section names mirror the ``EmbeddingSection`` Literal in the SQLModel.
_INDEXED_SECTIONS: tuple[str, ...] = ("description", "body", "applies_to")


class EmbedderUnavailableError(RuntimeError):
    """
    Raised when the embedder cannot be reached.

    Caught by the matcher Stage 3 path so a transport-level embedding
    failure short-circuits to a no-match result rather than blocking the
    pipeline.
    """


class Embedder(Protocol):
    """Embed an arbitrary string into a fixed-dimension vector."""

    @property
    def model_id(self) -> str:
        """Return the canonical model identifier (e.g. ``openai/text-embedding-3-small``)."""

    @property
    def model_version(self) -> str:
        """Return the embedder version pin (bumped manually when intent changes)."""

    async def embed(self, text: str) -> tuple[float, ...]:
        """Embed ``text`` and return its fixed-dimension vector."""


@attrs.frozen(kw_only=True, slots=True)
class LiteLLMEmbedder:
    """
    LiteLLM-SDK-backed :class:`Embedder` for production use.

    Calls the in-process ``litellm.aembedding`` API (same path the other
    PydanticAI agents take), so the embedding I/O is captured into the
    F4 replay bundle automatically. The model id pins the embedder; the
    model version is a manual bump knob — flip it when the intent of
    the embedded text changes (e.g. canonicalisation rules) so old rows
    are partitioned from new rows by the unique-key composite without a
    physical rebuild.
    """

    model_id: str
    model_version: str = "v1"

    async def embed(self, text: str) -> tuple[float, ...]:
        """
        Embed ``text`` via ``litellm.aembedding`` and return the vector.

        :raises EmbedderUnavailableError: if the LiteLLM call fails for
            any reason. The hot path treats embedder unavailability as a
            no-match outcome rather than a fatal error.
        """
        try:
            response = await litellm.aembedding(model=self.model_id, input=text)
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"model_id": self.model_id, "model_version": self.model_version},
            )
            raise EmbedderUnavailableError(
                f"litellm.aembedding failed for {self.model_id}: {exc}"
            ) from exc
        # LiteLLM returns ``{"data": [{"embedding": [floats], ...}], ...}``
        # for both OpenAI-compatible and Ollama embedders.
        data = response.data if hasattr(response, "data") else response["data"]
        first = data[0]
        embedding = first["embedding"] if isinstance(first, dict) else first.embedding
        return tuple(float(value) for value in embedding)


@attrs.frozen(kw_only=True, slots=True)
class RunbookRagCandidate:
    """One candidate emitted by :func:`retrieve_top_k`."""

    runbook_id: str
    content_sha: str
    embedding_section: str
    cosine_similarity: float
    rank: int


@attrs.define(kw_only=True, slots=True)
class RagFallback:
    """
    Bundle of Stage 3 dependencies the matcher orchestrator consumes.

    Pure-data carrier so :func:`sentinel.domain.runbooks.matcher.match_runbook`
    keeps a single new kwarg signature regardless of how many knobs the
    Stage 3 path acquires later. ``enabled=False`` short-circuits Stage 3
    inside the orchestrator without the caller having to pass ``None``
    when the catalog is built once and the toggle flips per environment.

    Mutable (``attrs.define``, not ``attrs.frozen``) so the matcher can
    record the per-invocation ``last_match_id`` and ``last_candidates``
    side-channel state for the persistence layer (F6.F) to drain when it
    writes the ``runbook_match`` row. The same ``match_id`` is reused for
    both the audit row and its evidence trail so the FK is never broken.
    """

    embedder: Embedder
    session: AsyncSession
    enabled: bool = True
    top_k: int = 5
    min_similarity: float = 0.78
    last_match_id: UUID | None = None
    last_candidates: tuple[RunbookRagCandidate, ...] = ()


def _section_text(*, runbook: runbook_models.Runbook, section: str) -> str:
    """Return the canonical text fed to the embedder for ``section``."""
    if section == "description":
        return runbook.metadata.description.strip()
    if section == "body":
        return runbook.body.strip()
    if section == "applies_to":
        applies_to = runbook.metadata.applies_to
        alertnames_part = ", ".join(applies_to.alertnames)
        resource_part = ", ".join(applies_to.resource_kinds)
        tags_part = ", ".join(f"{tag.key}={tag.value}" for tag in runbook.metadata.tags)
        return (
            f"alertnames: {alertnames_part}; "
            f"severity_min: {applies_to.severity_min}; "
            f"resource_kinds: {resource_part}; "
            f"tags: {tags_part}"
        ).strip()
    msg = f"unknown embedding section: {section!r}"
    raise ValueError(msg)


def _sha256_truncated(text: str) -> str:
    """Return ``sha256(text)[:32]`` — same convention as runbook ``content_sha``."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:32]


def _truncate_for_embedder(text: str) -> str:
    """Cap ``text`` at :data:`_MAX_SECTION_CHARS` to keep embedder input bounded."""
    if len(text) <= _MAX_SECTION_CHARS:
        return text
    return text[:_MAX_SECTION_CHARS]


async def _row_already_indexed(
    *,
    session: AsyncSession,
    runbook_id: str,
    content_sha: str,
    section: str,
    model_id: str,
    model_version: str,
) -> bool:
    """Return True when an embedding row already exists for the identity tuple."""
    stmt = sa.select(sa.literal(1)).where(
        col(rag_records.RunbookEmbeddingRecord.runbook_id) == runbook_id,
        col(rag_records.RunbookEmbeddingRecord.content_sha) == content_sha,
        col(rag_records.RunbookEmbeddingRecord.embedding_section) == section,
        col(rag_records.RunbookEmbeddingRecord.embedding_model) == model_id,
        col(rag_records.RunbookEmbeddingRecord.embedding_model_ver) == model_version,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def index_runbook(
    *,
    session: AsyncSession,
    runbook: runbook_models.Runbook,
    embedder: Embedder,
) -> None:
    """
    Index ``runbook``'s description / body / applies_to sections as embeddings.

    Idempotent: skips any section row that already exists for the unique
    identity ``(runbook_id, content_sha, section, model_id, model_version)``.
    Empty sections are also skipped so the embedder never receives empty
    input.

    :raises EmbedderUnavailableError: bubbles up from the embedder. The
        application-layer indexing daemon is expected to handle this
        per-runbook rather than aborting the whole catalog walk.
    """
    indexed_sections: list[str] = []
    for section in _INDEXED_SECTIONS:
        raw_text = _section_text(runbook=runbook, section=section)
        if not raw_text:
            continue
        text = _truncate_for_embedder(raw_text)
        already_indexed = await _row_already_indexed(
            session=session,
            runbook_id=runbook.metadata.runbook_id,
            content_sha=runbook.metadata.content_sha,
            section=section,
            model_id=embedder.model_id,
            model_version=embedder.model_version,
        )
        if already_indexed:
            continue
        embedding = await embedder.embed(text)
        record = rag_records.RunbookEmbeddingRecord(
            runbook_id=runbook.metadata.runbook_id,
            content_sha=runbook.metadata.content_sha,
            embedding_section=section,
            embedding_model=embedder.model_id,
            embedding_model_ver=embedder.model_version,
            embedding_dim=len(embedding),
            embedding=list(embedding),
            source_text=text,
            source_text_sha=_sha256_truncated(text),
        )
        session.add(record)
        indexed_sections.append(section)
    if indexed_sections:
        await session.flush()
        logs.log_event(
            "runbook_embeddings_indexed",
            params={
                "runbook_id": runbook.metadata.runbook_id,
                "content_sha": runbook.metadata.content_sha,
                "sections": indexed_sections,
                "model_id": embedder.model_id,
                "model_version": embedder.model_version,
            },
        )


async def retrieve_top_k(
    *,
    session: AsyncSession,
    query_text: str,
    embedder: Embedder,
    k: int = 5,
    min_similarity: float = 0.78,
) -> tuple[RunbookRagCandidate, ...]:
    """
    Embed ``query_text`` and return up to ``k`` runbook candidates.

    Filters embeddings by the embedder's ``model_id`` + ``model_version`` so
    multi-model rows in the same table never cross-contaminate. Orders by
    cosine distance (ascending) and converts to similarity (``1 - distance``)
    for the caller. Drops candidates below ``min_similarity``.

    :returns: A possibly-empty tuple of :class:`RunbookRagCandidate` sorted
        by descending similarity.
    """
    query_embedding = await embedder.embed(query_text)
    # pgvector's ``<=>`` operator returns cosine distance. We bind the
    # embedding as a python list — pgvector + asyncpg accept either a list
    # of floats or a pre-formatted vector literal.
    stmt = sa.text(
        "SELECT runbook_id, content_sha, embedding_section, "
        "1 - (embedding <=> CAST(:query_embedding AS vector)) AS similarity "
        "FROM runbook_embeddings "
        "WHERE embedding_model = :model AND embedding_model_ver = :model_ver "
        "ORDER BY embedding <=> CAST(:query_embedding AS vector) "
        "LIMIT :k"
    )
    result = await session.execute(
        stmt,
        {
            "query_embedding": _vector_literal(query_embedding),
            "model": embedder.model_id,
            "model_ver": embedder.model_version,
            "k": k,
        },
    )
    rows = result.all()
    candidates: list[RunbookRagCandidate] = []
    for rank, row in enumerate(rows, start=1):
        runbook_id, content_sha, section, similarity = row
        similarity_float = float(similarity)
        if similarity_float < min_similarity:
            continue
        candidates.append(
            RunbookRagCandidate(
                runbook_id=str(runbook_id),
                content_sha=str(content_sha),
                embedding_section=str(section),
                cosine_similarity=similarity_float,
                rank=rank,
            )
        )
    return tuple(candidates)


def _vector_literal(embedding: Sequence[float]) -> str:
    """
    Return the pgvector text literal form of ``embedding``.

    pgvector accepts ``[0.1,0.2,...]`` as the canonical text form. Going
    via the text codec keeps the bound parameter compatible with both
    asyncpg (which lacks a first-class vector type unless the pgvector
    register is wired into the connection) and the bare-text path used
    by this module.
    """
    return "[" + ",".join(f"{value:.7f}" for value in embedding) + "]"


async def write_evidence_rows(
    *,
    session: AsyncSession,
    match_id: UUID,
    candidates: Sequence[RunbookRagCandidate],
    embedder: Embedder,
) -> None:
    """
    Persist top-k Stage 3 candidates as ``runbook_rag_match_evidence`` rows.

    Called by the matcher orchestrator after a Stage 3 retrieval so replay
    can reconstruct the ranking from the audit table without re-running
    the live HNSW index. Empty ``candidates`` is a no-op.
    """
    if not candidates:
        return
    for candidate in candidates:
        record = rag_records.RunbookRagMatchEvidenceRecord(
            match_id=match_id,
            candidate_runbook_id=candidate.runbook_id,
            candidate_content_sha=candidate.content_sha,
            embedding_section=candidate.embedding_section,
            cosine_similarity=candidate.cosine_similarity,
            rank=candidate.rank,
            embedding_model=embedder.model_id,
            embedding_model_ver=embedder.model_version,
        )
        session.add(record)
    await session.flush()


def to_runbook_candidates(
    candidates: Sequence[RunbookRagCandidate],
) -> tuple[runbook_models.RunbookCandidate, ...]:
    """
    Project Stage 3 retrieval rows to :class:`runbook_models.RunbookCandidate`.

    The matcher's :class:`runbook_models.RunbookMatch` carries
    ``candidates`` as :class:`runbook_models.RunbookCandidate` for the
    ``runbook_match.candidates_json`` audit column. We collapse the
    per-section retrieval rows by ``runbook_id`` (winner = highest
    similarity) so the audit row mirrors Stage 1/2 semantics where one
    runbook id appears once.
    """
    best_per_runbook: dict[str, RunbookRagCandidate] = {}
    for candidate in candidates:
        prior = best_per_runbook.get(candidate.runbook_id)
        if prior is None or candidate.cosine_similarity > prior.cosine_similarity:
            best_per_runbook[candidate.runbook_id] = candidate
    return tuple(
        runbook_models.RunbookCandidate(
            runbook_id=candidate.runbook_id,
            content_sha=candidate.content_sha,
            score=0,
            matched_via=f"rag:{candidate.embedding_section}",
        )
        for candidate in sorted(
            best_per_runbook.values(),
            key=lambda c: -c.cosine_similarity,
        )
    )


__all__ = (
    "Embedder",
    "EmbedderUnavailableError",
    "LiteLLMEmbedder",
    "RagFallback",
    "RunbookRagCandidate",
    "index_runbook",
    "retrieve_top_k",
    "to_runbook_candidates",
    "write_evidence_rows",
)
