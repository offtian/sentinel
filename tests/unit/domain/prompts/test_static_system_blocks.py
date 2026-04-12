"""
Guard-rail test: every ``.j2`` template's ``system`` block renders without runtime variables.

This locks in the invariant that system blocks are truly static — a prerequisite
for Anthropic prompt caching, which requires deterministic system prompts.
"""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from sentinel.domain.prompts import _env
from sentinel.settings import PROMPTS_DIR


_TEMPLATE_NAMES = sorted(p.stem for p in PROMPTS_DIR.glob("*.j2"))


@pytest.mark.parametrize("template_name", _TEMPLATE_NAMES)
def test_system_block_renders_without_runtime_variables(template_name: str) -> None:
    """
    Given a Jinja2 template with a ``system`` block,
    When rendered with an empty context,
    Then no ``UndefinedError`` is raised — the block is fully static.
    """
    template = _env.get_template(f"{template_name}.j2")
    block_fn = template.blocks.get("system")
    assert block_fn is not None, f"{template_name}.j2 is missing a 'system' block"

    try:
        text = "".join(block_fn(template.new_context())).strip()
    except UndefinedError as exc:
        pytest.fail(f"{template_name}.j2 system block references a runtime variable: {exc}")

    assert len(text) > 0, f"{template_name}.j2 system block rendered to empty string"
