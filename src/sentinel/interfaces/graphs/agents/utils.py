from __future__ import annotations

from typing import Any

from opentelemetry import trace as otel_trace

from sentinel.domain import skills as skills_mod
from sentinel.interfaces.graphs.agents import _cache_settings
from sentinel.utils.observability import spans as obs_spans
from sentinel.utils.observability import usage as obs_usage


def set_agent_span_attributes(
    *,
    prompt_sha256: str,
    model_name: str,
    agent_name: str = "",
) -> None:
    """
    Set typed agent span attributes on the current OTel span per RFC §13.2.

    Emits OTel GenAI semconv keys (``gen_ai.*``) alongside the Sentinel-named
    mandatory attrs (``prompt_version_sha``, ``model_id``) and Langfuse prompt
    registry attrs that promote spans into Langfuse's Prompt registry view.

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
    attrs = obs_spans.AgentSpanAttributes(
        gen_ai_request_model=model_name,
        prompt_version_sha=prompt_sha256,
        model_id=model_name,
        agent_name=agent_name,
    )
    otel_trace.get_current_span().set_attributes(attrs.to_otel_dict())


def stamp_usage_attributes(usage: Any, *, model_name: str) -> None:
    """
    Stamp token/cost UsageAttributes on the current OTel span.

    Called immediately after ``agent.run(...)`` returns at every PydanticAI
    invocation site so Langfuse Generation views receive token counts and
    ``sentinel.cost_usd``. The ``usage`` argument accepts any object with
    ``input_tokens`` and ``output_tokens`` attributes (duck-typed for
    ``pydantic_ai.usage.RunUsage``).

    :param usage: A ``pydantic_ai.usage.RunUsage`` returned by
        ``result.usage()``.
    :param model_name: LiteLLM model string used for cost lookup.
    """
    otel_trace.get_current_span().set_attributes(
        obs_usage.extract_usage(usage, model_name=model_name).to_otel_dict()
    )


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
