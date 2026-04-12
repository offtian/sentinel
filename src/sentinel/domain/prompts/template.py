"""
Immutable template wrapping a rendered system prompt and its content hash.

The ``sha256`` field enables downstream cache-control decisions (e.g.
Anthropic / OpenAI prompt caching via LiteLLM) without coupling prompt
loading to any specific LLM provider.

The template also holds a reference to the underlying Jinja2 template so
it can render the ``user`` block on demand with runtime variables.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import attrs


if TYPE_CHECKING:
    from jinja2 import Template


_VERSION = "1"


@attrs.frozen
class PromptTemplate:
    """
    Immutable wrapper around a Jinja2 prompt template.

    The ``system_text`` is pre-rendered at load time (static, no runtime
    variables) and its SHA-256 digest is computed for cache keying.  The
    ``user`` block is rendered on demand via :meth:`render_user`.
    """

    template_name: str
    system_text: str
    sha256: str
    version: str = _VERSION
    _jinja_template: Template | None = attrs.field(
        default=None,
        eq=False,
        hash=False,
        repr=False,
    )

    @staticmethod
    def _compute_digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    @classmethod
    def from_jinja(
        cls,
        *,
        template_name: str,
        jinja_template: Template,
    ) -> PromptTemplate:
        """
        Build from a Jinja2 template, pre-rendering the ``system`` block.

        :param template_name: Logical name (without ``.j2`` suffix).
        :param jinja_template: Loaded Jinja2 template with ``system`` and
            optionally ``user`` blocks.
        :raises ValueError: If the template has no ``system`` block.
        """
        block_fn = jinja_template.blocks.get("system")
        if block_fn is None:
            msg = f"Template {template_name}.j2 has no 'system' block"
            raise ValueError(msg)
        system_text = "".join(block_fn(jinja_template.new_context())).strip()
        return cls(
            template_name=template_name,
            system_text=system_text,
            sha256=cls._compute_digest(system_text),
            jinja_template=jinja_template,
        )

    @classmethod
    def from_text(
        cls,
        *,
        template_name: str,
        system_text: str,
    ) -> PromptTemplate:
        """
        Build from pre-rendered text (useful in tests without Jinja2 templates).

        :param template_name: Logical name of the template.
        :param system_text: Fully rendered system-prompt string.
        """
        return cls(
            template_name=template_name,
            system_text=system_text,
            sha256=cls._compute_digest(system_text),
        )

    def render_user(self, **kwargs: object) -> str:
        """
        Render the ``user`` block with the given runtime variables.

        :raises RuntimeError: If the template was built via ``from_text``
            (no Jinja2 template available).
        :raises ValueError: If the underlying template has no ``user`` block.
        """
        if self._jinja_template is None:
            msg = (
                f"PromptTemplate '{self.template_name}' was built without a "
                "Jinja2 template — cannot render user block"
            )
            raise RuntimeError(msg)
        block_fn = self._jinja_template.blocks.get("user")
        if block_fn is None:
            msg = f"Template {self.template_name}.j2 has no 'user' block"
            raise ValueError(msg)
        ctx = self._jinja_template.new_context(vars=kwargs)
        return "".join(block_fn(ctx)).strip()
