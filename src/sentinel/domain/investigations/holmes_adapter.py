"""
ARCHIVED — F7 (2026-04-27).

The HolmesGPT integration is no longer wired into any active code path.
The Sentinel-native investigator agent
(:mod:`sentinel.interfaces.graphs.agents.investigator`) replaces it: it
owns the observability toolset (logs, metrics, traces) and reports a
structured ``InvestigationFindings`` to the downstream root-cause
analyser. The split between gather (investigator) and synthesise
(analyser) stages, plus the ``DetermineConfidence`` evidence floor,
addresses the F6→F7 hallucination finding where the analyser confidently
diagnosed on zero evidence.

The original implementation — ``BaseHolmesAdapter``, ``HolmesAdapter``
(SDK-backed), ``DirectToolsetAdapter`` (vendor-direct), and the
``HolmesInvestigationResult`` dataclass — is preserved in git history
on ``main`` prior to the F7 cutover commit. Recover via::

    git show main~1:src/sentinel/domain/investigations/holmes_adapter.py

This stub remains importable so any latent reference (older replay
bundles capturing stack traces, third-party scripts) does not crash on
``ImportError``. ``BaseHolmesAdapter`` and ``HolmesInvestigationResult``
are re-exported as minimal shape-compatible placeholders for the same
reason — they are NOT instantiated by any production code path.
"""

from __future__ import annotations

import abc
from typing import Any

import attrs

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import adapters


@attrs.frozen
class HolmesInvestigationResult:
    """ARCHIVED placeholder — see module docstring."""

    analysis: str
    tool_calls: list[dict[str, Any]]
    sources_queried: list[str]


class BaseHolmesAdapter(adapters.BaseInvestigationAdapter):
    """ARCHIVED placeholder — see module docstring."""

    @abc.abstractmethod
    async def investigate(  # type: ignore[override]
        self,
        *,
        alert: alert_entities.Alert,
        context: adapters.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
        """Archived — concrete subclasses are not instantiated by F7+ code."""
