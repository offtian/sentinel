"""
Base application configuration: lightweight shell and singleton cache.

The ``Configuration`` class defines the interface that all layers depend
on — settings access, model-name normalisation, and the agent registry.
The **real** wiring lives in ``sentinel.plugins.config.PluginConfiguration``
which inherits from this base and adds vendor adapters, searchers,
toolsets, and agent loading.

Entry-points call ``sentinel.plugins.config.boot()`` once at startup;
every other module retrieves the cached instance via ``get_config()``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from sentinel.settings import Settings, get_settings


# ---------------------------------------------------------------------------
# Skills-per-agent mapping
# ---------------------------------------------------------------------------
#
# Operators declare which on-disk Skills are baked into each agent's system
# prompt here. Skill names must match directory names under
# ``src/sentinel/plugins/skills/<name>/SKILL.md``. Unknown names raise
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


class Configuration(BaseModel):
    """
    Lightweight configuration base holding settings and the agent registry.

    Subclassed by ``sentinel.plugins.config.PluginConfiguration`` which adds
    vendor adapters, search infrastructure, and toolset builders.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: Settings

    # Agent instances — populated by PluginConfiguration.load_agents().
    # Private so Pydantic doesn't try to validate PydanticAI Agent objects.
    # Typed as Any because the registry holds heterogeneous
    # Agent[Deps, Output] specialisations with different type parameters.
    _agents: dict[str, Any] = PrivateAttr(default_factory=dict)

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

        Called by ``PluginConfiguration.load_agents()`` after constructing
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


_config: Configuration | None = None


def get_config(config: Configuration | None = None) -> Configuration:
    """
    Return the cached application configuration.

    On first call, pass a pre-built ``PluginConfiguration`` (typically via
    ``sentinel.plugins.config.boot()``).  Subsequent calls return the
    cached instance.

    :param config: Pre-built configuration to cache on first call.
    """
    global _config  # noqa: PLW0603
    if _config is None:
        _config = config or Configuration(settings=get_settings())
    return _config
