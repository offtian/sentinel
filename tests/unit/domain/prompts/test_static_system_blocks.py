"""
Guard-rail test: every ``.j2`` template's ``system`` block renders without runtime variables.

This locks in the invariant that system blocks are truly static — a prerequisite
for Anthropic prompt caching, which requires deterministic system prompts.
"""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from sentinel.domain import prompts
from sentinel.settings import PROMPTS_DIR


_TEMPLATE_NAMES = sorted(p.stem for p in PROMPTS_DIR.glob("*.j2"))


@pytest.mark.parametrize("template_name", _TEMPLATE_NAMES)
def test_system_block_renders_without_runtime_variables(template_name: str) -> None:
    """
    Given a Jinja2 template with a ``system`` block,
    When loaded via ``load_template``,
    Then the system_text is non-empty (no UndefinedError during rendering).
    """
    try:
        tpl = prompts.load_template(template_name)
    except UndefinedError as exc:
        pytest.fail(f"{template_name}.j2 system block references a runtime variable: {exc}")

    assert len(tpl.system_text) > 0, f"{template_name}.j2 system block rendered to empty string"
