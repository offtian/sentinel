from __future__ import annotations

from sentinel.plugins import skills as skills_mod


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


def append_skills_to_prompt(*, base_prompt: str, category: str, max_skills: int = 5) -> str:
    """
    Append Skills matching ``category`` onto ``base_prompt``.

    Delegates to ``sentinel.plugins.skills.load_skills_for`` and appends a
    structured ``## Applicable Skills`` section. When no skills match, the
    base prompt is returned unchanged.
    """
    handles = skills_mod.load_skills_for(category=category, max_skills=max_skills)
    if not handles:
        return base_prompt
    return f"{base_prompt}\n\n---\n{_format_skills_section(handles)}"


def render_skills_section(*, category: str, max_skills: int = 5) -> str:
    """
    Return the Skills Markdown section for ``category``, or an empty string.

    Intended for use inside PydanticAI ``@agent.system_prompt`` functions
    where the static system prompt is supplied separately and the returned
    string is concatenated onto it by PydanticAI at run time.
    """
    handles = skills_mod.load_skills_for(category=category, max_skills=max_skills)
    if not handles:
        return ""
    return _format_skills_section(handles)
