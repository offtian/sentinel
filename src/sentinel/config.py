"""
Base application configuration: interface and singleton cache.

The ``BaseConfiguration`` class defines the interface that all layers depend
on — settings access, model-name normalisation, the agent registry, and
stub methods for vendor adapters, searchers, and toolset builders. It also
carries the layered configuration fields that team profiles fill in
(loop caps, confidence thresholds, redaction / approval policies, etc.).

The concrete implementation lives in
``sentinel.plugins.common.config.CommonConfiguration`` which inherits from this
base and adds all domain/plugin wiring. ``get_config()`` auto-creates
the concrete instance via ``importlib`` on first access, so callers
never need to import from the plugins layer.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from sentinel.data.primitives import policies
from sentinel.settings import Settings, settings


TeamId = Literal["sre", "devops", "ace"]


# ---------------------------------------------------------------------------
# Skills-per-agent mapping
# ---------------------------------------------------------------------------
#
# Operators declare which on-disk Skills are baked into each agent's system
# prompt here. Skill names must match directory names under
# ``src/sentinel/domain/skills/<name>/SKILL.md``. Unknown names raise
# ``SkillNotFoundError`` loudly at ``load_agents()`` time so typos surface
# at startup rather than silently dropping runbooks.
#
# Agents listed with an empty tuple get only their base Jinja system prompt.
# Agents omitted from this mapping are also built with no configured skills;
# for ``root_cause_analyser`` and ``response_drafter`` the runtime
# ``@agent.system_prompt`` second-layer dynamic injection still fires on
# top, keyed off classifier / ticket-category output.
#
# Edit this mapping and restart the process to change which runbooks each
# agent sees. No code change to agent modules is required.

SKILLS_BY_AGENT: dict[str, tuple[str, ...]] = {
    "alert_classifier": (),
    # Analyser starts with no static skills: relevant runbooks attach via
    # the dynamic ``_inject_runbook_skills`` hook (category-matched) and
    # the matched-runbook body comes in via
    # ``_inject_runbook_body_quarantined``. Bundling every runbook into
    # every analyser run wastes ~3.5k input tokens and dilutes the prompt
    # with advice unrelated to the alert.
    "root_cause_analyser": (),
    "ticket_reviewer": (),
    "response_drafter": (
        "auth-error-response",
        "rate-limit-response",
    ),
    "chart_generator": ("chart-helm-best-practices",),
    "chart_request_parser": (),
    "intent_router": (),
    "k8s_investigator": ("k8s-crashloop-runbook",),
}


# ---------------------------------------------------------------------------
# Model-name normalisation
# ---------------------------------------------------------------------------


def _normalise_model_name(model_name: str) -> str:
    """
    Normalise LLM model names for pydantic-ai routing.

    - ``ollama/<model>`` → ``openai:<model>``: PydanticAI's OpenAI-compat
      client is repointed at Ollama via ``OPENAI_BASE_URL`` set in
      :func:`sentinel.bootstrap._configure_llm_env` (Ollama exposes an
      OpenAI-compatible endpoint at ``<base>/v1``). PydanticAI's ``litellm:``
      prefix is **not** the in-process LiteLLM SDK — it builds an OpenAI
      HTTP client that targets ``LITELLM_BASE_URL`` (a proxy). With no
      proxy configured locally that route falls back to ``api.openai.com``,
      which 401s for Ollama model names; bypassing it for ollama lets us
      hit the local daemon directly.
    - ``openai/<model>`` → ``litellm:openai/<model>``: kept on the
      LiteLLM proxy path for parity with prod, where ``LITELLM_BASE_URL``
      points at the firm-shared LiteLLM proxy.
    """
    model_name = model_name.removeprefix("litellm_proxy/")
    if model_name.startswith("ollama/"):
        return f"openai:{model_name.removeprefix('ollama/')}"
    return f"litellm:{model_name}"


# ---------------------------------------------------------------------------
# Base Configuration
# ---------------------------------------------------------------------------


class BaseConfiguration(BaseModel):
    """
    Application configuration interface.

    Defines settings access, model-name normalisation, the agent registry,
    and stub methods for vendor adapters, searchers, and toolset builders.

    The concrete implementation
    ``sentinel.plugins.common.config.CommonConfiguration`` overrides the stubs
    with real wiring.  ``get_config()`` auto-creates the concrete
    instance so callers never interact with this base directly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: Settings

    # Agent instances — populated by CommonConfiguration.load_agents().
    # Private so Pydantic doesn't try to validate PydanticAI Agent objects.
    # Typed as Any because the registry holds heterogeneous
    # Agent[Deps, Output] specialisations with different type parameters.
    _agents: dict[str, Any] = PrivateAttr(default_factory=dict)

    # -- Vendor adapters (populated by load_vendors) -------------------------

    observability_client: Any | None = None
    pagerduty_client: Any | None = None
    jira_client: Any | None = None
    confluence_client: Any | None = None
    observability_circuit_breaker: Any | None = None

    # -- Layered configuration fields with firm-wide defaults -----------------

    investigation_loop_cap: int = 8
    investigation_timeout_seconds: int = 300
    enrichment_timeout_seconds: int = 30
    job_poll_interval_seconds: float = 1.0
    job_max_retries: int = 3
    job_max_concurrent_per_worker: int = 4

    confidence_publish_min: float = 0.7
    confidence_human_review_min: float = 0.4

    redaction_policy: policies.RedactionPolicy = Field(
        default_factory=policies.RedactionPolicy.default,
    )

    case_retrieval_top_k: int = 5
    case_retrieval_show_top_n_to_agent: int = 3
    case_retrieval_min_redaction_score: float = 0.9

    eval_groundedness_min: float = 0.7
    enable_replay_bundle: bool = True

    runbooks_paths: tuple[Path, ...] = ()
    skills_paths: tuple[Path, ...] = ()
    tool_modules: tuple[str, ...] = ()
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    allowed_skills: frozenset[str] = Field(default_factory=frozenset)

    output_channels: tuple[policies.OutputChannel, ...] = ()
    approval_policy: policies.ApprovalPolicy = Field(
        default_factory=policies.ApprovalPolicy.empty,
    )
    model_id_primary: str = ""
    model_id_judge: str = ""

    envelope_strict_mode: bool = Field(
        default=False,
        description=("When True, hard-fail on missing tenant_id at webhook ingress (R-IN-3)."),
    )

    @property
    def team_id(self) -> TeamId:
        """
        Return the team profile this config represents.

        Reads from ``settings.team_profile`` so the discriminator stays
        in one place. Subclasses may override with a hardcoded literal
        when team profiles need divergent behaviour.
        """
        return self.settings.team_profile

    # -- LLM model names (normalised for pydantic-ai) -------------------------

    @staticmethod
    def _normalise_model_name(model_name: str) -> str:
        return _normalise_model_name(model_name)

    @property
    def classifier_model(self) -> str:
        return _normalise_model_name(self.settings.alert_classifier_llm)

    @property
    def analyser_model(self) -> str:
        return _normalise_model_name(self.settings.root_cause_llm)

    @property
    def investigator_model(self) -> str:
        """
        F7 Sentinel-native investigator agent model.

        Reuses the ``root_cause_llm`` setting today — investigator and
        analyser are both SRE-focused and benefit from the same model
        choice. If divergence is needed later (e.g. a smaller model for
        evidence-gathering and a stronger one for synthesis), promote
        this to its own ``investigator_llm`` Settings field.
        """
        return _normalise_model_name(self.settings.root_cause_llm)

    @property
    def reviewer_model(self) -> str:
        return _normalise_model_name(self.settings.ticket_reviewer_llm)

    @property
    def drafter_model(self) -> str:
        return _normalise_model_name(self.settings.response_drafter_llm)

    @property
    def disambiguator_model(self) -> str:
        """
        Stage 2 runbook disambiguator model (F6).

        Falls back to ``alert_classifier_llm`` when unset so deployments that
        don't override get a small, fast model "for free".
        """
        chosen = self.settings.runbook_disambiguator_llm or self.settings.alert_classifier_llm
        return _normalise_model_name(chosen)

    @property
    def enable_rag_fallback(self) -> bool:
        """
        Return whether the F6.J Stage 3 RAG fallback is enabled.

        Surfaced from ``Settings.runbook_rag_fallback_enabled`` so domain-layer
        callers (the matcher orchestrator + the application-layer reindex
        daemon) consult the configuration contract rather than ``Settings``
        directly. Defaults to False — the catalog ships RAG-disabled until an
        operator opts in.
        """
        return self.settings.runbook_rag_fallback_enabled

    @property
    def langgraph_sre_enabled(self) -> bool:
        """
        Return True when the SRE pipeline should route to the LangGraph workflow.

        Surfaced from ``Settings.langgraph_sre_enabled`` (W2 feature flag) so
        the worker routing decision reads from the configuration contract rather
        than ``Settings`` directly. Defaults to False — the Pydantic Graph
        pipeline remains live until an operator opts in.
        """
        return self.settings.langgraph_sre_enabled

    @property
    def embedder_model(self) -> str:
        """
        Return the normalised embedder model identifier for the F6.J Stage 3 path.

        Routes through the same ``provider/model`` -> ``provider:model``
        normalisation as the other LLM knobs so the embedder participates in
        the same LiteLLM-proxy / Ollama-direct routing logic.
        """
        return _normalise_model_name(self.settings.runbook_embedder_llm)

    @property
    def rag_min_similarity(self) -> float:
        """
        Return the cosine-similarity threshold for Stage 3 candidate filtering.

        Surfaced off ``BaseConfiguration`` rather than read directly off
        ``Settings`` so domain-layer code respects the layered-config rule.
        """
        return self.settings.runbook_rag_min_similarity

    @property
    def rag_top_k(self) -> int:
        """
        Return the top-k retrieval depth for the F6.J Stage 3 path.

        Surfaced off ``BaseConfiguration`` rather than read directly off
        ``Settings`` so the audit trail's evidence-row depth is one config
        knob, not two.
        """
        return self.settings.runbook_rag_top_k

    @property
    def k8s_investigator_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_investigator_llm)

    @property
    def intent_router_model(self) -> str:
        return _normalise_model_name(self.settings.intent_router_llm)

    @property
    def chart_parser_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_chart_parser_llm)

    @property
    def chart_generator_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_chart_generator_llm)

    @property
    def chart_max_retries(self) -> int:
        return self.settings.k8s_chart_max_retries

    @property
    def require_approval_below_confidence(self) -> float:
        """
        Return the confidence threshold below which a workflow run
        requires human approval before publishing.

        Surfacing this on ``BaseConfiguration`` keeps domain-layer code
        pointed at config rather than at ``Settings`` directly, per
        the layered-architecture rule in ``application.md``.
        """
        return self.settings.require_approval_below_confidence

    @property
    def runbook_owners_channel(self) -> str:
        """
        Return the fallback Slack channel for runbook drift notifications.

        Used by ``scripts/runbook_drift_check.py`` (F6.L) when a runbook's
        frontmatter ``owner`` field does not map to a team-specific channel.
        Empty string disables the fallback so drift on unowned runbooks is
        logged only — no Slack noise to a channel that has no maintainers.
        """
        return self.settings.runbook_owners_channel

    # -- Agent registry ------------------------------------------------------

    def set_agents(self, agents: dict[str, Any]) -> None:
        """
        Store pre-built agent instances in the registry.

        Called by ``CommonConfiguration.load_agents()`` after constructing
        each agent via its ``build_agent`` factory.
        """
        self._agents = dict(agents)

    def agent_for(self, name: str) -> Any:
        """
        Return the pre-built agent registered under ``name``.

        :raises KeyError: if ``name`` is not in the agent registry — either
            because ``load_agents()`` has not been called or because the
            name is not a recognised pipeline agent.
        """
        if name not in self._agents:
            msg = f"Unknown agent name: {name!r} (call load_agents() first?)"
            raise KeyError(msg)
        return self._agents[name]

    # -- Lifecycle (overridden by CommonConfiguration) -----------------------

    def load_vendors(self) -> None:
        """
        Build long-lived vendor adapter instances.

        Override in ``CommonConfiguration``.
        """

    def load_agents(self, *, agent_module: Any) -> None:
        """
        Build every pipeline agent with its configured skills baked in.

        Override in ``CommonConfiguration``.

        :param agent_module: The ``sentinel.interfaces.graphs.agents``
            module (or a compatible stub for testing).
        """

    # -- Builder stubs (overridden by CommonConfiguration) -------------------

    def build_mcp_toolsets(self) -> tuple[Any, ...]:
        """Build and memoise shared MCP toolsets. Override in subclass."""
        raise NotImplementedError

    # F7: build_holmes_adapter removed — the HolmesGPT adapter is archived
    # and replaced by the Sentinel-native investigator agent registered via
    # ``load_agents``. Callers should pass ``investigator_toolsets`` to
    # ``investigation.investigate_alert`` instead of building a Holmes
    # adapter. The hook is retained as a comment for archaeological
    # reference; removing it entirely would break old replay bundles that
    # mention the symbol in their captured stack traces.
    # def build_holmes_adapter(self) -> Any:
    #     """Build the appropriate Holmes adapter. Override in subclass."""
    #     raise NotImplementedError

    def build_k8s_investigation_adapter(self, *, agent_runner: Any) -> Any:
        """Build the K8s investigation adapter. Override in subclass."""
        raise NotImplementedError

    def build_challenger_adapter(self) -> Any:
        """Build the challenger adapter for A/B comparison. Override in subclass."""
        raise NotImplementedError

    def build_document_searcher(self) -> Any:
        """Build the configured document searcher. Override in subclass."""
        raise NotImplementedError

    def build_ticket_searcher(self) -> Any:
        """Build the configured past-ticket searcher. Override in subclass."""
        raise NotImplementedError

    def build_metrics_searcher(self) -> Any:
        """Build a metrics searcher. Override in subclass."""
        raise NotImplementedError

    def build_observability_toolset(self, *, service_name: str = "") -> Any:
        """Build the observability toolset. Override in subclass."""
        raise NotImplementedError

    def build_support_search_toolset(self) -> Any:
        """Build the support search toolset. Override in subclass."""
        raise NotImplementedError

    def build_ticket_triage_toolset(self) -> Any:
        """Build the ticket triage toolset. Override in subclass."""
        raise NotImplementedError


# Module:Class references for each team profile. Resolved at startup
# via importlib so the config layer doesn't statically depend on the
# plugins layer above. Devops/ACE references land in their own plans.
TEAM_CONFIG_REFS: dict[TeamId, str] = {
    "sre": "sentinel.plugins.common.config:CommonConfiguration",
}

_config: BaseConfiguration | None = None


def _build_default_config() -> BaseConfiguration:
    """Resolve the team-specific concrete configuration and load vendors."""
    if settings.team_profile not in TEAM_CONFIG_REFS:
        raise NotImplementedError(
            f"team profile {settings.team_profile!r} not yet wired — "
            "add an entry to TEAM_CONFIG_REFS pointing at its concrete "
            "configuration class.",
        )

    module_path, class_name = TEAM_CONFIG_REFS[settings.team_profile].split(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    instance: BaseConfiguration = cls(settings=settings)
    instance.load_vendors()
    return instance


def get_config(config: BaseConfiguration | None = None) -> BaseConfiguration:
    """
    Return the cached application configuration.

    On first access, dynamically builds a ``CommonConfiguration`` with
    vendor adapters loaded.  Callers that need agents should call
    ``get_config().load_agents(agent_module=...)`` at startup.

    :param config: Pre-built configuration to cache (used by tests).
    """
    global _config  # noqa: PLW0603
    if _config is None:
        if config is not None:
            _config = config
        else:
            _config = _build_default_config()
    return _config
