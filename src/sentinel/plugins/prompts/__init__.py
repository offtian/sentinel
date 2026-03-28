"""
Jinja2-based prompt loading for PydanticAI agents.

Each ``.j2`` template has two blocks: ``system`` (the system prompt) and
``user`` (the user-facing context, rendered with runtime variables).

Usage::

    from sentinel.plugins.prompts import load_system_prompt, render_user_prompt

    SYSTEM_PROMPT = load_system_prompt("alert_classifier")
    user_msg = render_user_prompt("alert_classifier", alert_title="...", ...)
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sentinel.settings import PROMPTS_DIR


_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=select_autoescape([]),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


def load_system_prompt(template_name: str) -> str:
    """Return the ``system`` block from *template_name*.j2, rendered without variables."""
    template = _env.get_template(f"{template_name}.j2")
    # Render the system block only — no runtime vars needed.
    block_fn = template.blocks.get("system")
    if block_fn is None:
        msg = f"Template {template_name}.j2 has no 'system' block"
        raise ValueError(msg)
    return "".join(block_fn(template.new_context())).strip()


def render_user_prompt(template_name: str, **kwargs: object) -> str:
    """Render the ``user`` block from *template_name*.j2 with the given variables."""
    template = _env.get_template(f"{template_name}.j2")
    block_fn = template.blocks.get("user")
    if block_fn is None:
        msg = f"Template {template_name}.j2 has no 'user' block"
        raise ValueError(msg)
    ctx = template.new_context(vars=kwargs)
    return "".join(block_fn(ctx)).strip()
