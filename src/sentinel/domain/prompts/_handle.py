"""
Immutable template wrapping a rendered system prompt and its content hash.

The ``sha256`` field enables downstream cache-control decisions (e.g.
Anthropic / OpenAI prompt caching via LiteLLM) without coupling prompt
loading to any specific LLM provider.
"""

from __future__ import annotations

import hashlib

import attrs


_VERSION = "1"


@attrs.frozen
class PromptTemplate:
    """Immutable wrapper for a rendered system-prompt text and its SHA-256 hash."""

    template_name: str
    text: str
    sha256: str
    version: str = _VERSION

    @classmethod
    def from_text(cls, *, template_name: str, text: str) -> PromptTemplate:
        """
        Build a template from raw rendered text, computing the SHA-256 digest.

        :param template_name: Logical name of the Jinja2 template (without ``.j2``).
        :param text: Fully rendered system-prompt string.
        """
        digest = hashlib.sha256(text.encode()).hexdigest()
        return cls(template_name=template_name, text=text, sha256=digest)
