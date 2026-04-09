"""
Base application configuration: interface and singleton cache.

The ``BaseConfiguration`` class defines the interface that all layers depend
on — settings access, model-name normalisation, the agent registry, and
stub methods for vendor adapters, searchers, and toolset builders.

The concrete implementation lives in
``sentinel.plugins.config.CommonConfiguration`` which inherits from this
base and adds all domain/plugin wiring.  ``get_config()`` auto-creates
the concrete instance via ``importlib`` on first access, so callers
never need to import from the plugins layer.
"""

from __future__ import annotations

import importlib
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from sentinel.settings import Settings, get_settings


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
    "root_cause_analyser": (
        "k8s-crashloop-runbook",
        "database-connection-runbook",
        "latency-spike-runbook",
    ),
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
    Normalise LLM model names for pydantic-ai.

    Config uses ``openai/gpt-4.1-mini``; pydantic-ai expects ``openai:gpt-4.1-mini``.
    LiteLLM runs as an OpenAI-compatible proxy on ``OPENAI_BASE_URL``.
    """
    model_name = model_name.removeprefix("litellm_proxy/")
    if "/" in model_name:
        provider, name = model_name.split("/", 1)
        model_name = f"{provider}:{name}"
    return model_name


# ---------------------------------------------------------------------------
# Base Configuration
# ---------------------------------------------------------------------------


class BaseConfiguration(BaseModel):
    """
    Application configuration interface.

    Defines settings access, model-name normalisation, the agent registry,
    and stub methods for vendor adapters, searchers, and toolset builders.

    The concrete implementation
    ``sentinel.plugins.config.CommonConfiguration`` overrides the stubs
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
    def reviewer_model(self) -> str:
        return _normalise_model_name(self.settings.ticket_reviewer_llm)

    @property
    def drafter_model(self) -> str:
        return _normalise_model_name(self.settings.response_drafter_llm)

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

    def build_holmes_adapter(self) -> Any:
        """Build the appropriate Holmes adapter. Override in subclass."""
        raise NotImplementedError

    def build_k8s_investigation_adapter(self, *, agent_runner: Any) -> Any:
        """Build the K8s investigation adapter. Override in subclass."""
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


_CONFIG_CLASS_PATH = "sentinel.plugins.config"
_CONFIG_CLASS_NAME = "CommonConfiguration"

_config: BaseConfiguration | None = None


def _build_default_config() -> BaseConfiguration:
    """
    Build a ``CommonConfiguration`` via importlib.

    Dynamically imports the concrete class to avoid a static dependency
    from the ``config`` layer to the ``plugins`` layer.
    """
    module = importlib.import_module(_CONFIG_CLASS_PATH)
    kls = getattr(module, _CONFIG_CLASS_NAME)
    instance: BaseConfiguration = kls(settings=get_settings())
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
