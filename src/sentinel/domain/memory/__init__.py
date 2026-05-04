"""
Long-term per-tenant/cluster incident memory (entities + recall + persistence).

Two pipeline integration points consume this package:

* ``analyse_root_cause`` reads via :func:`embeddings.retrieve_similar_incidents`
  (with :func:`queries.fetch_recent_for_cluster` as the embedder-unavailable
  fallback) so the analyser prompt sees prior incidents on the same fund and
  cluster that look like the current alert.
* ``publish_findings`` writes via :func:`operations.persist_incident_memory`
  + :func:`embeddings.index_incident_memory` so every confidently-resolved
  investigation feeds the recall index for future runs.

The retrieval primitives mirror :mod:`sentinel.domain.runbooks.rag` — same
``LiteLLMEmbedder`` contract, same pgvector ``<=>`` cosine-distance query,
same vector-literal trick for asyncpg compatibility — keeping the long-term
memory surface area minimal and the retrieval shape consistent with the
existing F6.J runbook RAG path.
"""
