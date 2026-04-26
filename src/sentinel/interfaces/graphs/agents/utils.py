from __future__ import annotations

from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from sentinel.domain import skills as skills_mod
from sentinel.domain.llm import litellm_proxy
from sentinel.interfaces.graphs.agents import _cache_settings


_PLACEHOLDER_MODEL = "test"
_LITELLM_PREFIX = "litellm:"


def resolve_agent_model(model: str | None) -> Any:
    """
    Return the model handle each agent factory passes into ``Agent(...)``.

    When the firm-shared LiteLLM proxy is configured (RFC §2.4), returns
    an ``OpenAIChatModel`` whose ``LiteLLMProvider`` points at the proxy
    URL with the operator's virtual key. Otherwise returns ``model``
    unchanged so PydanticAI follows its existing in-process LiteLLM SDK
    path — keeping ``just run-api`` working without a proxy.

    Special cases preserved verbatim:

    * ``None`` -> ``None`` so the factory's existing ``model or "test"``
      fallback still fires for unit-test paths that monkey-patch
      ``Agent.run``.
    * ``"test"`` -> ``"test"`` for the same reason — production never
      passes the placeholder; tests do.

    :param model: The model identifier resolved by
        :func:`sentinel.config._normalise_model_name` (e.g.
        ``"litellm:openai/gpt-4.1-mini"``), or ``None`` / ``"test"``.
    """
    if model is None or model == _PLACEHOLDER_MODEL:
        return model
    if not litellm_proxy.is_proxy_configured():
        return model

    proxy_kwargs = litellm_proxy.get_proxy_kwargs()
    # Strip the ``litellm:`` prefix when present — ``LiteLLMProvider``
    # already sets ``system="litellm"`` on the resulting OpenAIChatModel,
    # and the bare ``provider/model`` form is what its API expects.
    bare_model_name = model.removeprefix(_LITELLM_PREFIX)
    provider = LiteLLMProvider(**proxy_kwargs)
    return OpenAIChatModel(bare_model_name, provider=provider)


def set_agent_span_attributes(
    *,
    prompt_sha256: str,
    model_name: str,
    agent_name: str = "",
) -> None:
    """
    Set the prompt_version_sha and model_id mandatory OTel attributes on the
    current span per RFC §13.2, plus Langfuse-namespaced prompt attributes
    (``langfuse.prompt.name``, ``langfuse.prompt.version``) that promote
    spans into Langfuse's Prompt registry view.

    Called immediately before ``agent.run(...)`` at every PydanticAI invocation
    site so the agent's LLM child spans inherit these attributes (the six
    envelope-derived attrs are set by ``_node_helpers.run_node_with_envelope``).

    :param prompt_sha256: The agent module's ``PROMPT_SHA256`` constant.
    :param model_name: Result of :func:`get_model_name` on the agent. Empty
        string (test/mock model) skips the ``model_id`` attribute.
    :param agent_name: Identifier for the agent (e.g. ``"alert_classifier"``).
        When provided, sets ``langfuse.prompt.name`` so Langfuse can group
        spans by prompt. Empty skips the attribute.
    """
    attributes: dict[str, str] = {
        "prompt_version_sha": prompt_sha256,
        "langfuse.prompt.version": prompt_sha256,
    }
    if model_name:
        attributes["model_id"] = model_name
    if agent_name:
        attributes["langfuse.prompt.name"] = agent_name
    otel_trace.get_current_span().set_attributes(attributes)


def get_model_name(agent: Any) -> str:
    """
    Extract the model name string from a pre-built PydanticAI agent.

    Returns an empty string for test/mock models or when the attribute
    is not accessible.
    """
    try:
        name = agent.model.model_name
        return name if isinstance(name, str) else ""
    except AttributeError:
        return ""


def build_cache_settings(
    *,
    model_name: str,
    prompt_sha256: str,
) -> dict[str, Any] | None:
    """
    Return PydanticAI ``model_settings`` enabling system-prompt caching.

    Delegates to :func:`_cache_settings.build_cache_settings`.
    """
    return _cache_settings.build_cache_settings(
        model_name=model_name,
        prompt_sha256=prompt_sha256,
    )


def _format_skills_section(handles: tuple[skills_mod.SkillHandle, ...]) -> str:
    """
    Render a tuple of SkillHandle objects into the canonical Markdown section.
    """
    sections = [f"### {handle.name} (v{handle.version})\n{handle.body}" for handle in handles]
    return "## Applicable Skills\n\n" + "\n\n".join(sections)


def compose_system_prompt(*, base_prompt: str, skill_names: tuple[str, ...]) -> str:
    """
    Append the named Skills onto ``base_prompt`` in the given order.

    Looks each name up against the installed catalogue and appends the
    Markdown Skills section. Skill order is preserved from ``skill_names``
    (not alphabetised) so operators control the presentation order in
    ``config.load_agents()``.

    :raises sentinel.domain.skills.SkillNotFoundError: if any name in
        ``skill_names`` is not in the installed catalogue.
    """
    if not skill_names:
        return base_prompt

    catalogue = {handle.name: handle for handle in skills_mod.all_installed_skills()}
    missing = [name for name in skill_names if name not in catalogue]
    if missing:
        msg = f"Unknown skill name(s) requested: {', '.join(missing)}"
        raise skills_mod.SkillNotFoundError(msg)

    handles = tuple(catalogue[name] for name in skill_names)
    return f"{base_prompt}\n\n---\n{_format_skills_section(handles)}"


def render_skills_section(*, category: str, max_skills: int = 5) -> str:
    """
    Return the Skills Markdown section for ``category``, or an empty string.

    Used by second-layer dynamic system-prompt injection hooks in
    ``root_cause_analyser`` and ``response_drafter`` agents, which add
    category-specific runbook skills at runtime on top of the static skills
    baked in by ``compose_system_prompt``.
    """
    handles = skills_mod.load_skills_for(category=category, max_skills=max_skills)
    if not handles:
        return ""
    return _format_skills_section(handles)
