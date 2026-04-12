"""
Jinja2-based prompt loading for PydanticAI agents.

Each ``.j2`` template has two blocks: ``system`` (the system prompt) and
``user`` (the user-facing context, rendered with runtime variables).

Usage::

    from sentinel.domain import prompts

    tpl = prompts.load_template("alert_classifier")
    tpl.system_text  # pre-rendered static system prompt
    tpl.sha256       # content-addressable digest for cache keying
    tpl.render_user(alert_title="...", ...)  # render user block on demand
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sentinel.domain.prompts._handle import PromptTemplate
from sentinel.settings import PROMPTS_DIR


__all__ = ["PromptTemplate", "load_template"]

_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=select_autoescape([]),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


def load_template(template_name: str) -> PromptTemplate:
    """
    Load *template_name*.j2 and return a :class:`PromptTemplate`.

    The ``system`` block is pre-rendered immediately (it must be static).
    The ``user`` block can be rendered later via :meth:`PromptTemplate.render_user`.
    """
    jinja_template = _env.get_template(f"{template_name}.j2")
    return PromptTemplate.from_jinja(
        template_name=template_name,
        jinja_template=jinja_template,
    )
