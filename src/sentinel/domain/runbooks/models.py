"""
Frozen domain models for the Sentinel runbook catalog (F6).

These shapes are pure data: they bind to no vendor SDK, perform no I/O, and
carry no business logic. The :class:`Runbook` composite is the contract
loaded from a four-file directory (``RUNBOOK.md`` + ``tools.yaml`` +
``checks.yaml`` + ``tests.yaml``) by :mod:`sentinel.domain.runbooks.loader`
and consumed by :mod:`sentinel.domain.runbooks.matcher`.

Match outcomes (``RunbookCandidate``, ``RunbookMatch``) and the
LLM-disambiguator output (``DisambiguatorChoice``) live here so callers and
the matcher share one definition without a layer-cycle.

See ``docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md`` for
the full schema and matcher algorithm.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import attrs
from pydantic import BaseModel, ConfigDict, Field


MatchMethod = Literal[
    "tag",
    "llm_disambiguator_tie",
    "llm_zero_match_rescue",
    "no_match",
    "alphabetical_fallback",
    "rag",
]


# Hard depth cap on the ``extends:`` chain (F6.K). Five levels is generous
# enough for any real-world preamble decomposition (firm-base -> team-base
# -> incident-class-base -> specific-runbook) while still bounding the
# recursive merge cost and the cycle-detection working set.
RUNBOOK_EXTENDS_MAX_DEPTH: int = 5


class RunbookSchemaError(ValueError):
    """
    Raised when a runbook's frontmatter or sidecar YAML fails schema validation.

    Surfaces unexpected keys explicitly so authors catch typos at load time
    rather than silently dropping fields.
    """


class RunbookExtendsCycleError(ValueError):
    """
    Raised when a runbook's ``extends:`` chain forms a cycle.

    The exception message includes the full visited chain so the operator
    can trace which runbook re-references an ancestor.
    """


class RunbookExtendsTooDeepError(ValueError):
    """
    Raised when an ``extends:`` chain exceeds :data:`RUNBOOK_EXTENDS_MAX_DEPTH`.

    Bounds recursion in :func:`sentinel.domain.runbooks.loader.discover_runbooks`
    so a hostile or buggy authoring loop cannot exhaust the stack.
    """


class RunbookExtendsTargetNotFoundError(LookupError):
    """
    Raised when ``extends:`` references a runbook id absent from the catalog.

    Distinct from :class:`RunbookNotFoundError` so the loader's catalog-wide
    consistency check surfaces a different failure mode than a runtime
    lookup miss.
    """


class RunbookSanitizationError(ValueError):
    """
    Raised when a runbook body fails build-time sanitization rules.

    Specifically: when ``checks.yaml.body_sanitization.reject_auto_rendered_urls``
    is enabled and the body contains a ``[text](url)`` markdown link
    (LogJack arXiv 2604.15368 indirect-prompt-injection defence).
    """


class RunbookNotFoundError(LookupError):
    """
    Raised when a runbook lookup misses against the discovered catalog.

    Used by callers that resolve a ``runbook_id`` against
    :func:`sentinel.domain.runbooks.loader.discover_runbooks`'s mapping and
    need a typed failure (rather than a bare ``KeyError``) so persistence
    and replay layers can match the exception explicitly.
    """


class DisambiguatorUnavailableError(RuntimeError):
    """
    Raised when the LLM disambiguator cannot be reached.

    Caught by matcher Stage 2A (falls back to alphabetical tiebreak) and
    Stage 2B (returns a straight no-match) so the pipeline never blocks
    on a transport-level LLM failure.
    """


@attrs.frozen(kw_only=True, slots=True)
class RunbookTag:
    """A single deterministic key/value match dimension on a runbook."""

    key: str
    value: str


@attrs.frozen(kw_only=True, slots=True)
class RunbookAppliesTo:
    """Structured pre-filter rules from a runbook's frontmatter ``applies_to``."""

    alertnames: tuple[str, ...]
    severity_min: str
    resource_kinds: tuple[str, ...]
    exclude_labels: Mapping[str, tuple[str, ...]]


@attrs.frozen(kw_only=True, slots=True)
class RunbookMetadata:
    """Frontmatter contract for a runbook (RFC §4.2)."""

    runbook_id: str
    description: str
    content_sha: str
    applies_to: RunbookAppliesTo
    tags: tuple[RunbookTag, ...]
    min_match_score: int
    owner: str
    authors: tuple[str, ...]
    last_validated: date | None
    deprecated_at: date | None
    superseded_by: str | None
    mnpi_safe: bool
    canonical_sources: tuple[str, ...]
    extends: str | None = None


@attrs.frozen(kw_only=True, slots=True)
class ToolSpec:
    """One entry in a runbook's ``tools.yaml`` allowed_tools list."""

    name: str
    max_calls: int


@attrs.frozen(kw_only=True, slots=True)
class ToolsConfig:
    """
    The ``tools.yaml`` block in normalised form.

    Carries the raw allowed-tool list plus ergonomic ``allowed_tool_names``
    and ``tool_max_calls`` views the F7 toolset wrapper consumes at
    O(1) per dispatch.
    """

    allowed_tools: tuple[ToolSpec, ...]
    denied_tools: tuple[str, ...]
    max_total_tool_calls: int
    max_loop_iterations: int

    @property
    def allowed_tool_names(self) -> frozenset[str]:
        """Return the frozen set of tool names this runbook authorises."""
        return frozenset(spec.name for spec in self.allowed_tools)

    @property
    def tool_max_calls(self) -> Mapping[str, int]:
        """Return ``{tool_name: max_calls}`` for the F7 toolset wrapper to enforce."""
        return {spec.name: spec.max_calls for spec in self.allowed_tools}


@attrs.frozen(kw_only=True, slots=True)
class CheckSpec:
    """One prescribed check that the matcher pre-populates as an investigation_task."""

    id: str
    description: str
    suggested_tools: tuple[str, ...]
    required: bool


@attrs.frozen(kw_only=True, slots=True)
class GroundednessRule:
    """A single groundedness rule the F8 quality gate enforces against findings."""

    rule_id: str
    description: str


@attrs.frozen(kw_only=True, slots=True)
class BodySanitizationConfig:
    """Build-time rules the loader applies to the runbook body before injection."""

    reject_auto_rendered_urls: bool = True
    allowed_url_locations: tuple[str, ...] = ("canonical_sources", "frontmatter")


@attrs.frozen(kw_only=True, slots=True)
class ChecksConfig:
    """
    The ``checks.yaml`` block in normalised form.

    Composes prescribed checks (pre-populated as investigation_task rows),
    groundedness rules (consumed by the F8 quality gate), and the build-time
    body sanitization config (consumed by the loader).
    """

    prescribed_checks: tuple[CheckSpec, ...]
    groundedness_rules: tuple[GroundednessRule, ...]
    body_sanitization: BodySanitizationConfig


@attrs.frozen(kw_only=True, slots=True)
class TestExpected:
    """The ``expected:`` block of a single ``tests.yaml`` fixture entry."""

    runbook_id: str | None
    match_method: MatchMethod
    min_tag_score: int | None = None
    required_checks_executed: tuple[str, ...] = ()
    hypothesis_keywords: tuple[str, ...] = ()
    confidence_min: str | None = None
    forbidden_substrings_in_summary: tuple[str, ...] = ()


@attrs.frozen(kw_only=True, slots=True)
class TestSpec:
    """One golden fixture from a runbook's ``tests.yaml``."""

    id: str
    alert_payload_path: str
    expected: TestExpected


@attrs.frozen(kw_only=True, slots=True)
class Runbook:
    """
    The full loaded runbook composite — frontmatter + body + three sidecar yamls.

    ``metadata.runbook_id`` is exposed as :attr:`runbook_id` for convenience
    so callers (matcher, persistence, agent dependency wiring) can avoid the
    double attribute lookup.
    """

    metadata: RunbookMetadata
    body: str
    tools: ToolsConfig
    checks: ChecksConfig
    tests: tuple[TestSpec, ...]
    directory: Path

    @property
    def runbook_id(self) -> str:
        """Return the immutable runbook id from the metadata for caller convenience."""
        return self.metadata.runbook_id


@attrs.frozen(kw_only=True, slots=True)
class RunbookCandidate:
    """One candidate emitted by Stage 1 tag matching for inclusion in audit rows."""

    runbook_id: str
    content_sha: str
    score: int
    matched_via: str


@attrs.frozen(kw_only=True, slots=True)
class RunbookMatch:
    """
    Result of the full matcher pipeline.

    Always populated — including no-match. ``matched_runbook_id is None``
    iff ``match_method == "no_match"``. ``candidates`` is always populated
    (Stage 1 top-k on success; Stage 2B pre-filter top-N on no-match) so
    the ``runbook_match`` audit row can answer "why this runbook and not
    another?" without re-executing the matcher.
    """

    matched_runbook_id: str | None
    content_sha: str | None
    match_method: MatchMethod
    confidence: float
    tag_score: int | None
    llm_choice: str | None
    llm_justification: str | None
    candidates: tuple[RunbookCandidate, ...]

    def __attrs_post_init__(self) -> None:
        no_match = self.match_method == "no_match"
        if no_match and self.matched_runbook_id is not None:
            msg = (
                "RunbookMatch invariant violated: match_method='no_match' but "
                f"matched_runbook_id={self.matched_runbook_id!r}"
            )
            raise ValueError(msg)
        if not no_match and self.matched_runbook_id is None:
            msg = (
                "RunbookMatch invariant violated: matched_runbook_id is None but "
                f"match_method={self.match_method!r}"
            )
            raise ValueError(msg)


class DisambiguatorChoice(BaseModel):
    """
    Pydantic-validated output of the runbook disambiguator agent.

    ``chosen_runbook_id`` is either one of the candidate ids passed in, or
    the literal ``"no_match"`` so the matcher can route to the generic
    playbook without an out-of-band signal.
    """

    model_config = ConfigDict(extra="forbid")

    chosen_runbook_id: str
    justification: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
