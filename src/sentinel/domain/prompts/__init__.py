"""
Jinja2-based prompt loading for PydanticAI agents.

Each ``.j2`` template has two blocks: ``system`` (the system prompt) and
``user`` (the user-facing context, rendered with runtime variables).

Usage::

    from sentinel.domain.prompts import load_system_prompt, render_user_prompt

    tpl = load_system_prompt("alert_classifier")
    tpl.text   # the rendered string
    tpl.sha256 # content-addressable digest for cache keying

    user_msg = render_user_prompt("alert_classifier", alert_title="...", ...)
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sentinel.domain.prompts._handle import PromptTemplate
from sentinel.settings import PROMPTS_DIR


__all__ = ["PromptTemplate", "load_system_prompt", "render_user_prompt"]

_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=select_autoescape([]),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


def load_system_prompt(template_name: str) -> PromptTemplate:
    """Return a :class:`PromptTemplate` for the ``system`` block of *template_name*.j2."""
    template = _env.get_template(f"{template_name}.j2")
    block_fn = template.blocks.get("system")
    if block_fn is None:
        msg = f"Template {template_name}.j2 has no 'system' block"
        raise ValueError(msg)
    text = "".join(block_fn(template.new_context())).strip()
    return PromptTemplate.from_text(template_name=template_name, text=text)


def render_user_prompt(template_name: str, **kwargs: object) -> str:
    """Render the ``user`` block from *template_name*.j2 with the given variables."""
    template = _env.get_template(f"{template_name}.j2")
    block_fn = template.blocks.get("user")
    if block_fn is None:
        msg = f"Template {template_name}.j2 has no 'user' block"
        raise ValueError(msg)
    ctx = template.new_context(vars=kwargs)
    return "".join(block_fn(ctx)).strip()
