"""
Markdown → Confluence storage-format conversion for the runbook PR-bot.

Confluence "storage format" is XHTML with a small set of namespaced
``ac:`` macros (notably ``ac:structured-macro`` for code blocks). This
module renders the well-formed Markdown subset that Sentinel runbooks
produce — headings, paragraphs, lists, tables, fenced code, inline code,
bold/italic, and links — without reaching for an external binary like
pandoc. The conversion is intentionally narrow: anything outside the
runbook authoring contract is passed through as best-effort HTML and the
upstream sanitization layer (loader's body sanitization) is responsible
for rejecting unsafe input *before* it reaches this module.

HTML comments (``<!-- ... -->``) are stripped from the final output so
LogJack-style indirect-prompt-injection payloads embedded in author
comments cannot leak into the published page.

Only the ``markdown_to_confluence_storage`` function is part of the
public API; the helpers are private.
"""

from __future__ import annotations

import re

import markdown


# Renderer pipeline configuration: keep the extension list narrow on
# purpose so we don't accept author input outside the documented
# authoring contract. ``fenced_code`` enables triple-backtick blocks;
# ``tables`` enables GitHub-flavoured tables; ``sane_lists`` keeps list
# numbering deterministic across re-publishes.
_MARKDOWN_EXTENSIONS = ("fenced_code", "tables", "sane_lists")

_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_FENCED_CODE_BLOCK_PATTERN = re.compile(
    r"<pre><code(?P<attrs>[^>]*)>(?P<body>.*?)</code></pre>",
    flags=re.DOTALL,
)
_LANGUAGE_CLASS_PATTERN = re.compile(r'class="language-(?P<lang>[\w+-]+)"')


def _detect_language(attrs: str) -> str:
    """Return the fenced-code language hint or empty string when absent."""
    match = _LANGUAGE_CLASS_PATTERN.search(attrs)
    if match is None:
        return ""
    return match.group("lang")


def _render_storage_code_macro(*, language: str, body: str) -> str:
    """
    Render a Confluence ``ac:structured-macro`` code block from raw HTML body text.

    The body is wrapped in a CDATA section because Confluence's storage
    format requires it for the ``ac:plain-text-body`` macro parameter
    and CDATA bypasses XHTML escaping that would otherwise mangle
    ``<``/``>``/``&`` characters in code samples.
    """
    language_param = (
        f'<ac:parameter ac:name="language">{language}</ac:parameter>' if language else ""
    )
    return (
        '<ac:structured-macro ac:name="code">'
        + language_param
        + f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>"
        + "</ac:structured-macro>"
    )


def _replace_fenced_code_blocks(html_text: str) -> str:
    """Rewrite ``markdown.markdown`` ``<pre><code>`` output into Confluence code macros."""

    def _replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        body = match.group("body")
        # Markdown library escapes ``<>&`` inside the code body to be safe in
        # raw HTML — but the storage-format CDATA wrapper does not need that
        # escaping, and leaving it in distorts code samples in the published
        # page. Reverse the three escapes the markdown lib introduces.
        body = body.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        return _render_storage_code_macro(language=_detect_language(attrs), body=body)

    return _FENCED_CODE_BLOCK_PATTERN.sub(_replace, html_text)


def markdown_to_confluence_storage(*, markdown_text: str) -> str:
    """
    Convert ``markdown_text`` to Confluence storage format (XHTML + ``ac:`` macros).

    The output is suitable for posting to the Confluence REST API as
    ``body.storage.value``. Behaviour:

    * Headings, paragraphs, bold/italic, lists, tables, inline code, and
      links pass through as standard XHTML.
    * Fenced code blocks become ``<ac:structured-macro ac:name="code">``
      with a language parameter detected from the ``language-XYZ``
      class. Body wrapped in CDATA (storage-format requirement).
    * HTML comments are stripped (defence-in-depth against indirect
      prompt-injection payloads in author comments).

    :param markdown_text: A runbook body written in CommonMark + GitHub
        tables. Sanitization (zero-width chars, auto-rendered URL
        rejection) is the responsibility of the loader, not this
        function.
    :returns: A storage-format string. Always non-None; empty input
        yields the empty string.
    """
    if markdown_text == "":
        return ""
    html_body = markdown.markdown(
        markdown_text,
        extensions=list(_MARKDOWN_EXTENSIONS),
        output_format="xhtml",
    )
    storage_body = _replace_fenced_code_blocks(html_body)
    return _HTML_COMMENT_PATTERN.sub("", storage_body)
