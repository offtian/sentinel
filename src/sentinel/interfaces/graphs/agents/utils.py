from __future__ import annotations

from sentinel.domain import skills as skills_mod


def get_model_with_gateway(model_name: str) -> str:
    """
    Normalise model names for pydantic-ai and route via LiteLLM.

    - Config uses \"openai/gpt-4.1-mini\" style names.
    - pydantic-ai expects \"openai:gpt-4.1-mini\".
    - LiteLLM runs as an OpenAI-compatible proxy on OPENAI_BASE_URL,
      so we don't need a custom provider name pydantic-ai's built-in
      OpenAI provider will hit the proxy.
    """
    # Strip any legacy litellm_proxy/ prefix
    model_name = model_name.removeprefix("litellm_proxy/")

    # Convert \"provider/model\" → \"provider:model\" if needed
    if "/" in model_name:
        provider, name = model_name.split("/", 1)
        model_name = f"{provider}:{name}"

    return model_name


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
