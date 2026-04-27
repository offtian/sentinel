"""
Filesystem loader and walker for the Sentinel runbook catalog (F6).

Reads a four-file runbook directory (``RUNBOOK.md`` + ``tools.yaml`` +
``checks.yaml`` + ``tests.yaml``) into a frozen :class:`models.Runbook`,
computing the canonical ``content_sha`` (sha256[:32] over body + three
canonicalised sidecar yamls; frontmatter excluded so the hash is stable
across ``last_validated`` bumps and yaml whitespace re-formatting),
stripping zero-width characters from the body, and applying build-time
body sanitization rules (LogJack arXiv 2604.15368 indirect-prompt-injection
defence).

``discover_runbooks`` walks one or more roots in declared order and
returns a mapping keyed by ``runbook_id``. First-wins semantics on
collision (RFC §15.10): a runbook in an earlier root shadows one with
the same id in a later root, with a structured warning log. After the
walk, an ``extends:`` resolution pass flattens every child runbook by
merging in its parent chain (root-most parent first, child appended
last); the flattened ``content_sha`` is recomputed so a parent body
edit cascades to every descendant SHA.
"""

from __future__ import annotations

import functools
import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import attrs
import yaml

from sentinel.domain.runbooks import models
from sentinel.utils import logs


# Body separator inserted between parent and child markdown when an extends
# chain is flattened. Authors see a horizontal rule between the inherited
# preamble and the child runbook so the agent prompt visibly delimits the
# two contracts.
_EXTENDS_BODY_SEPARATOR = "\n\n---\n\n"


_RUNBOOK_FILENAME = "RUNBOOK.md"
_TOOLS_FILENAME = "tools.yaml"
_CHECKS_FILENAME = "checks.yaml"
_TESTS_FILENAME = "tests.yaml"

_CONTENT_SHA_HEX_LENGTH = 32
_AUTO_RENDERED_URL_PATTERN = re.compile(r"\[[^\]]+\]\([^)]+\)")
# Zero-width and bidi-override characters: ZWSP/ZWNJ/ZWJ/LRM/RLM (U+200B-U+200F),
# LRE/RLE/PDF/LRO/RLO (U+202A-U+202E), WJ + invisible-operators (U+2060-U+2064),
# LRI/RLI/FSI/PDI (U+2066-U+2069), and the BOM (U+FEFF). Built from explicit
# codepoint ranges so the source file contains no invisible Unicode.
_ZERO_WIDTH_PATTERN = re.compile(
    "["
    + "".join(
        f"{chr(start)}-{chr(stop)}"
        for start, stop in (
            (0x200B, 0x200F),
            (0x202A, 0x202E),
            (0x2060, 0x2064),
            (0x2066, 0x2069),
            (0xFEFF, 0xFEFF),
        )
    )
    + "]"
)

_DEFAULT_MIN_MATCH_SCORE = 2
_VALID_SEVERITIES = frozenset({"P1", "P2", "P3", "P4", "P5"})

_FRONTMATTER_REQUIRED_KEYS = frozenset(
    {
        "runbook_id",
        "description",
        "applies_to",
        "tags",
        "owner",
        "authors",
        "last_validated",
        "deprecated_at",
        "superseded_by",
        "mnpi_safe",
        "canonical_sources",
    }
)
_FRONTMATTER_OPTIONAL_KEYS = frozenset({"content_sha", "min_match_score", "extends"})
_FRONTMATTER_ALLOWED_KEYS = _FRONTMATTER_REQUIRED_KEYS | _FRONTMATTER_OPTIONAL_KEYS

_APPLIES_TO_REQUIRED_KEYS = frozenset(
    {"alertnames", "severity_min", "resource_kinds", "exclude_labels"}
)

_TOOLS_REQUIRED_KEYS = frozenset({"allowed_tools", "max_total_tool_calls", "max_loop_iterations"})
_TOOLS_OPTIONAL_KEYS = frozenset({"denied_tools"})
_TOOLS_ALLOWED_KEYS = _TOOLS_REQUIRED_KEYS | _TOOLS_OPTIONAL_KEYS

_TOOL_ENTRY_REQUIRED_KEYS = frozenset({"name", "max_calls"})

_CHECKS_REQUIRED_KEYS = frozenset({"prescribed_checks", "groundedness_rules", "body_sanitization"})
_CHECK_ENTRY_REQUIRED_KEYS = frozenset({"id", "description", "suggested_tools", "required"})
_GROUNDEDNESS_REQUIRED_KEYS = frozenset({"rule_id", "description"})
_BODY_SANITIZATION_REQUIRED_KEYS = frozenset(
    {"reject_auto_rendered_urls", "allowed_url_locations"}
)

_TESTS_REQUIRED_KEYS = frozenset({"fixtures"})
_FIXTURE_REQUIRED_KEYS = frozenset({"id", "alert_payload_path", "expected"})
_EXPECTED_REQUIRED_KEYS = frozenset({"runbook_id", "match_method"})
_EXPECTED_OPTIONAL_KEYS = frozenset(
    {
        "min_tag_score",
        "required_checks_executed",
        "hypothesis_keywords",
        "confidence_min",
        "forbidden_substrings_in_summary",
    }
)
_EXPECTED_ALLOWED_KEYS = _EXPECTED_REQUIRED_KEYS | _EXPECTED_OPTIONAL_KEYS


def _split_frontmatter(path: Path, raw_text: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, body)`` from a Markdown-with-frontmatter file."""
    if not raw_text.startswith("---"):
        msg = f"{path} is missing YAML frontmatter"
        raise models.RunbookSchemaError(msg)
    parts = raw_text.split("---", 2)
    if len(parts) < 3:
        msg = f"{path} has an unterminated frontmatter block"
        raise models.RunbookSchemaError(msg)
    return parts[1], parts[2].lstrip("\n")


def _require_mapping(value: Any, *, where: str) -> dict[str, Any]:
    """Return ``value`` if it is a mapping, else raise a schema error."""
    if not isinstance(value, dict):
        msg = f"{where} must be a mapping, got {type(value).__name__}"
        raise models.RunbookSchemaError(msg)
    return value


def _require_keys(
    parsed: dict[str, Any],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    where: str,
) -> None:
    """Validate that ``parsed`` contains exactly the required + allowed keys."""
    missing = required - parsed.keys()
    if missing:
        msg = f"{where} missing required keys: {sorted(missing)}"
        raise models.RunbookSchemaError(msg)
    unexpected = parsed.keys() - allowed
    if unexpected:
        msg = f"{where} has unexpected keys: {sorted(unexpected)}"
        raise models.RunbookSchemaError(msg)


def _coerce_str_tuple(value: Any, *, where: str) -> tuple[str, ...]:
    """Return ``value`` as a tuple of strings or raise a schema error."""
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{where} must be a list, got {type(value).__name__}"
        raise models.RunbookSchemaError(msg)
    return tuple(str(item) for item in value)


def _coerce_date(value: Any, *, where: str) -> date | None:
    """Return ``value`` as a ``date`` or raise a schema error. ``None`` passes through."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    msg = f"{where} must be a YAML date (or null), got {type(value).__name__}"
    raise models.RunbookSchemaError(msg)


def _parse_applies_to(parsed: Any, *, path: Path) -> models.RunbookAppliesTo:
    """Parse the ``applies_to`` block of a runbook frontmatter."""
    block = _require_mapping(parsed, where=f"{path} applies_to")
    _require_keys(
        block,
        required=_APPLIES_TO_REQUIRED_KEYS,
        allowed=_APPLIES_TO_REQUIRED_KEYS,
        where=f"{path} applies_to",
    )
    severity_min = str(block["severity_min"])
    if severity_min not in _VALID_SEVERITIES:
        msg = (
            f"{path} applies_to.severity_min={severity_min!r} is not in the "
            f"firm-standard P1..P5 scale (got: {sorted(_VALID_SEVERITIES)})"
        )
        raise models.RunbookSchemaError(msg)
    exclude_raw = block["exclude_labels"]
    exclude_dict = _require_mapping(exclude_raw, where=f"{path} applies_to.exclude_labels")
    exclude_labels = {
        str(key): _coerce_str_tuple(value, where=f"{path} applies_to.exclude_labels[{key!r}]")
        for key, value in exclude_dict.items()
    }
    return models.RunbookAppliesTo(
        alertnames=_coerce_str_tuple(block["alertnames"], where=f"{path} applies_to.alertnames"),
        severity_min=severity_min,
        resource_kinds=_coerce_str_tuple(
            block["resource_kinds"], where=f"{path} applies_to.resource_kinds"
        ),
        exclude_labels=exclude_labels,
    )


def _parse_tags(parsed: Any, *, path: Path) -> tuple[models.RunbookTag, ...]:
    """Parse the ``tags`` list of a runbook frontmatter."""
    if parsed is None:
        return ()
    if not isinstance(parsed, list):
        msg = f"{path} tags must be a list, got {type(parsed).__name__}"
        raise models.RunbookSchemaError(msg)
    out: list[models.RunbookTag] = []
    for index, entry in enumerate(parsed):
        block = _require_mapping(entry, where=f"{path} tags[{index}]")
        _require_keys(
            block,
            required=frozenset({"key", "value"}),
            allowed=frozenset({"key", "value"}),
            where=f"{path} tags[{index}]",
        )
        out.append(models.RunbookTag(key=str(block["key"]), value=str(block["value"])))
    return tuple(out)


def _parse_frontmatter(
    *, path: Path, frontmatter_text: str, content_sha: str
) -> models.RunbookMetadata:
    """Parse the YAML frontmatter of a ``RUNBOOK.md`` into a :class:`models.RunbookMetadata`."""
    parsed = yaml.safe_load(frontmatter_text)
    block = _require_mapping(parsed, where=f"{path} frontmatter")
    _require_keys(
        block,
        required=_FRONTMATTER_REQUIRED_KEYS,
        allowed=_FRONTMATTER_ALLOWED_KEYS,
        where=f"{path} frontmatter",
    )
    min_match_score = block.get("min_match_score", _DEFAULT_MIN_MATCH_SCORE)
    if not isinstance(min_match_score, int):
        msg = f"{path} frontmatter min_match_score must be an int"
        raise models.RunbookSchemaError(msg)
    mnpi_safe = block["mnpi_safe"]
    if not isinstance(mnpi_safe, bool):
        msg = f"{path} frontmatter mnpi_safe must be a bool"
        raise models.RunbookSchemaError(msg)
    superseded_by_raw = block["superseded_by"]
    superseded_by = None if superseded_by_raw is None else str(superseded_by_raw)
    extends_raw = block.get("extends")
    extends = None if extends_raw is None else str(extends_raw)
    return models.RunbookMetadata(
        runbook_id=str(block["runbook_id"]),
        description=str(block["description"]).strip(),
        content_sha=content_sha,
        applies_to=_parse_applies_to(block["applies_to"], path=path),
        tags=_parse_tags(block["tags"], path=path),
        min_match_score=min_match_score,
        owner=str(block["owner"]),
        authors=_coerce_str_tuple(block["authors"], where=f"{path} frontmatter.authors"),
        last_validated=_coerce_date(
            block["last_validated"], where=f"{path} frontmatter.last_validated"
        ),
        deprecated_at=_coerce_date(
            block["deprecated_at"], where=f"{path} frontmatter.deprecated_at"
        ),
        superseded_by=superseded_by,
        mnpi_safe=mnpi_safe,
        canonical_sources=_coerce_str_tuple(
            block["canonical_sources"], where=f"{path} frontmatter.canonical_sources"
        ),
        extends=extends,
    )


def _parse_tools(*, path: Path, raw_text: str) -> models.ToolsConfig:
    """Return the parsed ``tools.yaml`` block as a :class:`models.ToolsConfig`."""
    parsed = yaml.safe_load(raw_text)
    block = _require_mapping(parsed, where=str(path))
    _require_keys(
        block,
        required=_TOOLS_REQUIRED_KEYS,
        allowed=_TOOLS_ALLOWED_KEYS,
        where=str(path),
    )
    allowed_tools = _parse_tool_entries(block["allowed_tools"], path=path)
    denied_tools = _coerce_str_tuple(
        block.get("denied_tools", ()),
        where=f"{path} denied_tools",
    )
    max_total_tool_calls = block["max_total_tool_calls"]
    max_loop_iterations = block["max_loop_iterations"]
    if not isinstance(max_total_tool_calls, int):
        msg = f"{path} max_total_tool_calls must be an int"
        raise models.RunbookSchemaError(msg)
    if not isinstance(max_loop_iterations, int):
        msg = f"{path} max_loop_iterations must be an int"
        raise models.RunbookSchemaError(msg)
    return models.ToolsConfig(
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        max_total_tool_calls=max_total_tool_calls,
        max_loop_iterations=max_loop_iterations,
    )


def _parse_tool_entries(parsed: Any, *, path: Path) -> tuple[models.ToolSpec, ...]:
    """Parse the ``allowed_tools`` list of a ``tools.yaml`` block."""
    if not isinstance(parsed, list):
        msg = f"{path} allowed_tools must be a list, got {type(parsed).__name__}"
        raise models.RunbookSchemaError(msg)
    out: list[models.ToolSpec] = []
    for index, entry in enumerate(parsed):
        block = _require_mapping(entry, where=f"{path} allowed_tools[{index}]")
        _require_keys(
            block,
            required=_TOOL_ENTRY_REQUIRED_KEYS,
            allowed=_TOOL_ENTRY_REQUIRED_KEYS,
            where=f"{path} allowed_tools[{index}]",
        )
        max_calls = block["max_calls"]
        if not isinstance(max_calls, int):
            msg = f"{path} allowed_tools[{index}].max_calls must be an int"
            raise models.RunbookSchemaError(msg)
        out.append(models.ToolSpec(name=str(block["name"]), max_calls=max_calls))
    return tuple(out)


def _parse_checks(*, path: Path, raw_text: str) -> models.ChecksConfig:
    """Return the parsed ``checks.yaml`` block as a :class:`models.ChecksConfig`."""
    parsed = yaml.safe_load(raw_text)
    block = _require_mapping(parsed, where=str(path))
    _require_keys(
        block,
        required=_CHECKS_REQUIRED_KEYS,
        allowed=_CHECKS_REQUIRED_KEYS,
        where=str(path),
    )
    return models.ChecksConfig(
        prescribed_checks=_parse_check_entries(block["prescribed_checks"], path=path),
        groundedness_rules=_parse_groundedness_rules(block["groundedness_rules"], path=path),
        body_sanitization=_parse_body_sanitization(block["body_sanitization"], path=path),
    )


def _parse_check_entries(parsed: Any, *, path: Path) -> tuple[models.CheckSpec, ...]:
    """Parse the ``prescribed_checks`` list of a ``checks.yaml`` block."""
    if not isinstance(parsed, list):
        msg = f"{path} prescribed_checks must be a list, got {type(parsed).__name__}"
        raise models.RunbookSchemaError(msg)
    out: list[models.CheckSpec] = []
    for index, entry in enumerate(parsed):
        block = _require_mapping(entry, where=f"{path} prescribed_checks[{index}]")
        _require_keys(
            block,
            required=_CHECK_ENTRY_REQUIRED_KEYS,
            allowed=_CHECK_ENTRY_REQUIRED_KEYS,
            where=f"{path} prescribed_checks[{index}]",
        )
        required = block["required"]
        if not isinstance(required, bool):
            msg = f"{path} prescribed_checks[{index}].required must be a bool"
            raise models.RunbookSchemaError(msg)
        out.append(
            models.CheckSpec(
                id=str(block["id"]),
                description=str(block["description"]),
                suggested_tools=_coerce_str_tuple(
                    block["suggested_tools"],
                    where=f"{path} prescribed_checks[{index}].suggested_tools",
                ),
                required=required,
            )
        )
    return tuple(out)


def _parse_groundedness_rules(parsed: Any, *, path: Path) -> tuple[models.GroundednessRule, ...]:
    """Parse the ``groundedness_rules`` list of a ``checks.yaml`` block."""
    if not isinstance(parsed, list):
        msg = f"{path} groundedness_rules must be a list, got {type(parsed).__name__}"
        raise models.RunbookSchemaError(msg)
    out: list[models.GroundednessRule] = []
    for index, entry in enumerate(parsed):
        block = _require_mapping(entry, where=f"{path} groundedness_rules[{index}]")
        _require_keys(
            block,
            required=_GROUNDEDNESS_REQUIRED_KEYS,
            allowed=_GROUNDEDNESS_REQUIRED_KEYS,
            where=f"{path} groundedness_rules[{index}]",
        )
        out.append(
            models.GroundednessRule(
                rule_id=str(block["rule_id"]),
                description=str(block["description"]),
            )
        )
    return tuple(out)


def _parse_body_sanitization(parsed: Any, *, path: Path) -> models.BodySanitizationConfig:
    """Parse the ``body_sanitization`` block of a ``checks.yaml`` block."""
    block = _require_mapping(parsed, where=f"{path} body_sanitization")
    _require_keys(
        block,
        required=_BODY_SANITIZATION_REQUIRED_KEYS,
        allowed=_BODY_SANITIZATION_REQUIRED_KEYS,
        where=f"{path} body_sanitization",
    )
    reject = block["reject_auto_rendered_urls"]
    if not isinstance(reject, bool):
        msg = f"{path} body_sanitization.reject_auto_rendered_urls must be a bool"
        raise models.RunbookSchemaError(msg)
    return models.BodySanitizationConfig(
        reject_auto_rendered_urls=reject,
        allowed_url_locations=_coerce_str_tuple(
            block["allowed_url_locations"],
            where=f"{path} body_sanitization.allowed_url_locations",
        ),
    )


def _parse_tests(*, path: Path, raw_text: str) -> tuple[models.TestSpec, ...]:
    """Parse a ``tests.yaml`` block into a tuple of :class:`models.TestSpec`."""
    parsed = yaml.safe_load(raw_text)
    block = _require_mapping(parsed, where=str(path))
    _require_keys(
        block,
        required=_TESTS_REQUIRED_KEYS,
        allowed=_TESTS_REQUIRED_KEYS,
        where=str(path),
    )
    fixtures = block["fixtures"]
    if not isinstance(fixtures, list):
        msg = f"{path} fixtures must be a list, got {type(fixtures).__name__}"
        raise models.RunbookSchemaError(msg)
    out: list[models.TestSpec] = []
    for index, entry in enumerate(fixtures):
        fixture = _require_mapping(entry, where=f"{path} fixtures[{index}]")
        _require_keys(
            fixture,
            required=_FIXTURE_REQUIRED_KEYS,
            allowed=_FIXTURE_REQUIRED_KEYS,
            where=f"{path} fixtures[{index}]",
        )
        out.append(
            models.TestSpec(
                id=str(fixture["id"]),
                alert_payload_path=str(fixture["alert_payload_path"]),
                expected=_parse_test_expected(fixture["expected"], path=path, fixture_index=index),
            )
        )
    return tuple(out)


_VALID_MATCH_METHODS = frozenset(
    {
        "tag",
        "llm_disambiguator_tie",
        "llm_zero_match_rescue",
        "no_match",
        "alphabetical_fallback",
    }
)


def _parse_test_expected(parsed: Any, *, path: Path, fixture_index: int) -> models.TestExpected:
    """Parse the ``expected:`` block of a single fixture entry."""
    block = _require_mapping(parsed, where=f"{path} fixtures[{fixture_index}].expected")
    _require_keys(
        block,
        required=_EXPECTED_REQUIRED_KEYS,
        allowed=_EXPECTED_ALLOWED_KEYS,
        where=f"{path} fixtures[{fixture_index}].expected",
    )
    runbook_id_raw = block["runbook_id"]
    runbook_id = None if runbook_id_raw is None else str(runbook_id_raw)
    min_tag_score = block.get("min_tag_score")
    if min_tag_score is not None and not isinstance(min_tag_score, int):
        msg = f"{path} fixtures[{fixture_index}].expected.min_tag_score must be int or null"
        raise models.RunbookSchemaError(msg)
    confidence_min_raw = block.get("confidence_min")
    confidence_min = None if confidence_min_raw is None else str(confidence_min_raw)
    match_method_raw = str(block["match_method"])
    if match_method_raw not in _VALID_MATCH_METHODS:
        msg = (
            f"{path} fixtures[{fixture_index}].expected.match_method must be one of "
            f"{sorted(_VALID_MATCH_METHODS)}, got {match_method_raw!r}"
        )
        raise models.RunbookSchemaError(msg)
    return models.TestExpected(
        runbook_id=runbook_id,
        match_method=match_method_raw,  # type: ignore[arg-type]
        min_tag_score=min_tag_score,
        required_checks_executed=_coerce_str_tuple(
            block.get("required_checks_executed", ()),
            where=f"{path} fixtures[{fixture_index}].expected.required_checks_executed",
        ),
        hypothesis_keywords=_coerce_str_tuple(
            block.get("hypothesis_keywords", ()),
            where=f"{path} fixtures[{fixture_index}].expected.hypothesis_keywords",
        ),
        confidence_min=confidence_min,
        forbidden_substrings_in_summary=_coerce_str_tuple(
            block.get("forbidden_substrings_in_summary", ()),
            where=f"{path} fixtures[{fixture_index}].expected.forbidden_substrings_in_summary",
        ),
    )


def _strip_zero_width(text: str) -> str:
    """Return ``text`` with zero-width and bidi-override chars removed."""
    return _ZERO_WIDTH_PATTERN.sub("", text)


def _canonicalise_yaml(raw_text: str) -> bytes:
    """
    Return ``raw_text`` re-emitted via ``yaml.safe_dump(sort_keys=True)``.

    This makes the content_sha stable across whitespace and key-ordering
    drift in author edits — only semantic changes flip the hash.
    """
    parsed = yaml.safe_load(raw_text)
    canonical = yaml.safe_dump(parsed, sort_keys=True, default_flow_style=False)
    return canonical.encode("utf-8")


def _compute_content_sha(
    *, body_text: str, tools_text: str, checks_text: str, tests_text: str
) -> str:
    """Compute the canonical sha256[:32] of body + three canonicalised sidecar yamls."""
    digest = hashlib.sha256()
    digest.update(body_text.encode("utf-8"))
    digest.update(_canonicalise_yaml(tools_text))
    digest.update(_canonicalise_yaml(checks_text))
    digest.update(_canonicalise_yaml(tests_text))
    return digest.hexdigest()[:_CONTENT_SHA_HEX_LENGTH]


def _read_required_file(directory: Path, filename: str) -> str:
    """Read a required file as utf-8 text or raise a schema error referencing the dir."""
    path = directory / filename
    if not path.exists():
        msg = f"{directory} is missing required file {filename}"
        raise models.RunbookSchemaError(msg)
    return path.read_text(encoding="utf-8")


def load_runbook(directory: Path) -> models.Runbook:
    """
    Load a four-file runbook directory into a :class:`models.Runbook`.

    Returns the runbook in its **on-disk shape**: ``extends`` is parsed
    onto the metadata but the parent runbook is *not* merged. Extends
    resolution is a catalog-level operation that needs every sibling
    runbook in scope to look up the parent ``runbook_id``; it runs once
    per :func:`discover_runbooks` walk via :func:`_resolve_extends`.
    Single-file callers (e.g. the pre-commit ``content_sha`` computer)
    intentionally see the unflattened body so the on-disk SHA matches
    what authors wrote.

    :raises models.RunbookSchemaError: on missing files, malformed
        frontmatter, or unexpected keys in any of the four files.
    :raises models.RunbookSanitizationError: when
        ``checks.yaml.body_sanitization.reject_auto_rendered_urls`` is true
        and the body contains a ``[text](url)`` markdown link.
    """
    runbook_path = directory / _RUNBOOK_FILENAME
    if not runbook_path.exists():
        msg = f"{directory} is missing required file {_RUNBOOK_FILENAME}"
        raise models.RunbookSchemaError(msg)

    runbook_text = runbook_path.read_text(encoding="utf-8")
    tools_text = _read_required_file(directory, _TOOLS_FILENAME)
    checks_text = _read_required_file(directory, _CHECKS_FILENAME)
    tests_text = _read_required_file(directory, _TESTS_FILENAME)

    frontmatter_text, raw_body = _split_frontmatter(runbook_path, runbook_text)
    sanitised_body = _strip_zero_width(raw_body)

    content_sha = _compute_content_sha(
        body_text=sanitised_body,
        tools_text=tools_text,
        checks_text=checks_text,
        tests_text=tests_text,
    )

    metadata = _parse_frontmatter(
        path=runbook_path, frontmatter_text=frontmatter_text, content_sha=content_sha
    )
    tools_config = _parse_tools(path=directory / _TOOLS_FILENAME, raw_text=tools_text)
    checks_config = _parse_checks(path=directory / _CHECKS_FILENAME, raw_text=checks_text)

    if checks_config.body_sanitization.reject_auto_rendered_urls and (
        _AUTO_RENDERED_URL_PATTERN.search(sanitised_body)
    ):
        msg = (
            f"{runbook_path}: body contains auto-rendered markdown URLs "
            "([text](url)) but checks.yaml body_sanitization.reject_auto_rendered_urls "
            "is true. Move URLs into frontmatter canonical_sources."
        )
        raise models.RunbookSanitizationError(msg)

    test_specs = _parse_tests(path=directory / _TESTS_FILENAME, raw_text=tests_text)

    return models.Runbook(
        metadata=metadata,
        body=sanitised_body,
        tools=tools_config,
        checks=checks_config,
        tests=test_specs,
        directory=directory,
    )


def discover_runbooks(roots: Sequence[Path]) -> Mapping[str, models.Runbook]:
    """
    Walk ``roots`` in declared order and return a mapping keyed by ``runbook_id``.

    First-wins on collision (RFC §15.10): the first root containing a given
    ``runbook_id`` shadows later roots. Emits a structured ``runbook_override``
    warning when shadowing happens. After the walk, :func:`_resolve_extends`
    flattens any runbook with a non-null ``metadata.extends`` against its
    parent chain (root-most ancestor first; child appended last) and
    recomputes the ``content_sha`` over the flattened body + sidecars so a
    parent edit cascades into descendant SHAs. Cached for the life of the
    process via :func:`_discover_runbooks_cached`; tests call
    ``.cache_clear()`` between cases.

    :raises models.RunbookSchemaError: surfaced from :func:`load_runbook`.
    :raises models.RunbookSanitizationError: surfaced from :func:`load_runbook`.
    :raises models.RunbookExtendsCycleError: when an ``extends`` chain
        revisits an ancestor.
    :raises models.RunbookExtendsTooDeepError: when an ``extends`` chain
        exceeds :data:`models.RUNBOOK_EXTENDS_MAX_DEPTH`.
    :raises models.RunbookExtendsTargetNotFoundError: when an ``extends``
        target ``runbook_id`` is not in the discovered catalog.
    """
    return _discover_runbooks_cached(tuple(roots))


@functools.lru_cache(maxsize=1)
def _discover_runbooks_cached(roots: tuple[Path, ...]) -> Mapping[str, models.Runbook]:
    """LRU-cached implementation backing :func:`discover_runbooks`."""
    raw_catalog: dict[str, models.Runbook] = {}
    for root in roots:
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / _RUNBOOK_FILENAME).exists():
                continue
            runbook = load_runbook(child)
            existing = raw_catalog.get(runbook.metadata.runbook_id)
            if existing is not None:
                logs.log_event(
                    "runbook_override",
                    params={
                        "runbook_id": runbook.metadata.runbook_id,
                        "winning_source_dir": str(existing.directory),
                        "shadowed_source_dir": str(runbook.directory),
                    },
                )
                continue
            raw_catalog[runbook.metadata.runbook_id] = runbook
    return _resolve_extends(raw_catalog)


# ---------------------------------------------------------------------------
# extends: chain resolution (F6.K)
# ---------------------------------------------------------------------------


def _resolve_extends(catalog: Mapping[str, models.Runbook]) -> Mapping[str, models.Runbook]:
    """
    Return a new mapping with every ``extends`` chain flattened.

    Walks each runbook's chain root-most-first (parent before child),
    detecting cycles and depth-overruns before any merge work happens, then
    folds parents into the child via :func:`_merge_extends_pair`. The
    flattened runbook's ``content_sha`` is recomputed over the merged body
    plus the merged sidecar yamls so any parent edit cascades to every
    descendant. Pure function — neither argument nor result is mutated;
    a fresh dict is returned.

    :raises models.RunbookExtendsCycleError: when a chain revisits an
        ancestor.
    :raises models.RunbookExtendsTooDeepError: when a chain exceeds
        :data:`models.RUNBOOK_EXTENDS_MAX_DEPTH`.
    :raises models.RunbookExtendsTargetNotFoundError: when a chain
        references a ``runbook_id`` not in ``catalog``.
    """
    flattened: dict[str, models.Runbook] = {}
    for runbook_id, runbook in catalog.items():
        if runbook.metadata.extends is None:
            flattened[runbook_id] = runbook
            continue
        chain = _build_extends_chain(catalog=catalog, child=runbook)
        # ``chain`` is ordered root-most -> child. Fold left so the deepest
        # ancestor is the merge base and each subsequent runbook overrides
        # narrower-scoped fields (per F6.K.2 merge rules).
        merged = chain[0]
        for descendant in chain[1:]:
            merged = _merge_extends_pair(parent=merged, child=descendant)
        logs.log_event(
            "runbook_extends_resolved",
            params={
                "runbook_id": runbook_id,
                "parent_id": runbook.metadata.extends,
                "depth": len(chain) - 1,
            },
        )
        flattened[runbook_id] = merged
    return flattened


def _build_extends_chain(
    *, catalog: Mapping[str, models.Runbook], child: models.Runbook
) -> tuple[models.Runbook, ...]:
    """
    Return the ``extends`` ancestry as ``(root_parent, ..., child)``.

    Walks parent-ward from ``child``, raising on cycles or depth overrun
    *before* any merge work begins. The returned tuple is intentionally
    root-first so callers can fold left and have the child override
    narrower fields (per the F6.K.2 merge rules).

    :raises models.RunbookExtendsCycleError: when an ancestor is revisited.
    :raises models.RunbookExtendsTooDeepError: when the chain exceeds
        :data:`models.RUNBOOK_EXTENDS_MAX_DEPTH`.
    :raises models.RunbookExtendsTargetNotFoundError: when ``extends``
        references a ``runbook_id`` not in ``catalog``.
    """
    visited: list[str] = [child.metadata.runbook_id]
    chain_child_first: list[models.Runbook] = [child]
    cursor = child
    while cursor.metadata.extends is not None:
        # Depth check measured in *links*, not nodes — a chain of length
        # ``RUNBOOK_EXTENDS_MAX_DEPTH`` links has ``MAX_DEPTH + 1`` runbooks.
        if len(chain_child_first) > models.RUNBOOK_EXTENDS_MAX_DEPTH:
            msg = (
                f"runbook {child.metadata.runbook_id!r} extends chain exceeds "
                f"depth {models.RUNBOOK_EXTENDS_MAX_DEPTH}: "
                f"{' -> '.join(visited)}"
            )
            raise models.RunbookExtendsTooDeepError(msg)
        parent_id = cursor.metadata.extends
        if parent_id in visited:
            msg = (
                f"runbook {child.metadata.runbook_id!r} extends chain forms a cycle: "
                f"{' -> '.join([*visited, parent_id])}"
            )
            raise models.RunbookExtendsCycleError(msg)
        parent = catalog.get(parent_id)
        if parent is None:
            msg = (
                f"runbook {cursor.metadata.runbook_id!r} extends "
                f"{parent_id!r} which is not in the discovered catalog"
            )
            raise models.RunbookExtendsTargetNotFoundError(msg)
        visited.append(parent_id)
        chain_child_first.append(parent)
        cursor = parent
    # Reverse so the result is (root, ..., child).
    return tuple(reversed(chain_child_first))


def _merge_tools(*, parent: models.ToolsConfig, child: models.ToolsConfig) -> models.ToolsConfig:
    """
    Return a flattened :class:`models.ToolsConfig` from ``parent`` + ``child``.

    Per F6.K.2 merge rules:

    * ``allowed_tools`` — parent first, child appended; on ``name`` collision
      the child's :class:`models.ToolSpec` (and its ``max_calls``) wins.
    * ``denied_tools`` — set-union of both, sorted for stability.
    * ``max_total_tool_calls`` — ``min(parent, child)`` (fail-closed: the
      tighter cap always wins).
    * ``max_loop_iterations`` — ``min(parent, child)``.
    """
    by_name: dict[str, models.ToolSpec] = {spec.name: spec for spec in parent.allowed_tools}
    for spec in child.allowed_tools:
        by_name[spec.name] = spec
    # Preserve ordering: parent tools first (in their original order), then
    # any new child tools in their original order; collisions stay in the
    # parent slot but carry the child's value.
    ordered: list[models.ToolSpec] = []
    seen: set[str] = set()
    for spec in parent.allowed_tools:
        ordered.append(by_name[spec.name])
        seen.add(spec.name)
    for spec in child.allowed_tools:
        if spec.name in seen:
            continue
        ordered.append(by_name[spec.name])
        seen.add(spec.name)
    denied = tuple(sorted(set(parent.denied_tools) | set(child.denied_tools)))
    return models.ToolsConfig(
        allowed_tools=tuple(ordered),
        denied_tools=denied,
        max_total_tool_calls=min(parent.max_total_tool_calls, child.max_total_tool_calls),
        max_loop_iterations=min(parent.max_loop_iterations, child.max_loop_iterations),
    )


def _merge_checks(
    *, parent: models.ChecksConfig, child: models.ChecksConfig
) -> models.ChecksConfig:
    """
    Return a flattened :class:`models.ChecksConfig` from ``parent`` + ``child``.

    Per F6.K.2 merge rules:

    * ``prescribed_checks`` — parent first, child appended; on ``id``
      collision the child's :class:`models.CheckSpec` overrides the parent's.
    * ``groundedness_rules`` — union by ``rule_id``; child wins on collision.
    * ``body_sanitization`` — child wins (the leaf runbook is closer to the
      operator decision; if both are present the child's narrower scope is
      authoritative).
    """
    checks_by_id: dict[str, models.CheckSpec] = {
        check.id: check for check in parent.prescribed_checks
    }
    for check in child.prescribed_checks:
        checks_by_id[check.id] = check
    ordered_checks: list[models.CheckSpec] = []
    seen_check_ids: set[str] = set()
    for check in parent.prescribed_checks:
        ordered_checks.append(checks_by_id[check.id])
        seen_check_ids.add(check.id)
    for check in child.prescribed_checks:
        if check.id in seen_check_ids:
            continue
        ordered_checks.append(checks_by_id[check.id])
        seen_check_ids.add(check.id)

    rules_by_id: dict[str, models.GroundednessRule] = {
        rule.rule_id: rule for rule in parent.groundedness_rules
    }
    for rule in child.groundedness_rules:
        rules_by_id[rule.rule_id] = rule
    ordered_rules: list[models.GroundednessRule] = []
    seen_rule_ids: set[str] = set()
    for rule in parent.groundedness_rules:
        ordered_rules.append(rules_by_id[rule.rule_id])
        seen_rule_ids.add(rule.rule_id)
    for rule in child.groundedness_rules:
        if rule.rule_id in seen_rule_ids:
            continue
        ordered_rules.append(rules_by_id[rule.rule_id])
        seen_rule_ids.add(rule.rule_id)

    return models.ChecksConfig(
        prescribed_checks=tuple(ordered_checks),
        groundedness_rules=tuple(ordered_rules),
        body_sanitization=child.body_sanitization,
    )


def _serialise_tools(tools: models.ToolsConfig) -> str:
    """Render a :class:`models.ToolsConfig` back to canonical YAML for SHA hashing."""
    payload = {
        "allowed_tools": [
            {"name": spec.name, "max_calls": spec.max_calls} for spec in tools.allowed_tools
        ],
        "denied_tools": list(tools.denied_tools),
        "max_total_tool_calls": tools.max_total_tool_calls,
        "max_loop_iterations": tools.max_loop_iterations,
    }
    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False)


def _serialise_checks(checks: models.ChecksConfig) -> str:
    """Render a :class:`models.ChecksConfig` back to canonical YAML for SHA hashing."""
    payload = {
        "prescribed_checks": [
            {
                "id": check.id,
                "description": check.description,
                "suggested_tools": list(check.suggested_tools),
                "required": check.required,
            }
            for check in checks.prescribed_checks
        ],
        "groundedness_rules": [
            {"rule_id": rule.rule_id, "description": rule.description}
            for rule in checks.groundedness_rules
        ],
        "body_sanitization": {
            "reject_auto_rendered_urls": checks.body_sanitization.reject_auto_rendered_urls,
            "allowed_url_locations": list(checks.body_sanitization.allowed_url_locations),
        },
    }
    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False)


def _serialise_tests(tests: tuple[models.TestSpec, ...]) -> str:
    """Render a tuple of :class:`models.TestSpec` back to canonical YAML for SHA hashing."""
    fixtures: list[dict[str, Any]] = []
    for spec in tests:
        expected: dict[str, Any] = {
            "runbook_id": spec.expected.runbook_id,
            "match_method": spec.expected.match_method,
            "min_tag_score": spec.expected.min_tag_score,
            "required_checks_executed": list(spec.expected.required_checks_executed),
            "hypothesis_keywords": list(spec.expected.hypothesis_keywords),
            "confidence_min": spec.expected.confidence_min,
            "forbidden_substrings_in_summary": list(spec.expected.forbidden_substrings_in_summary),
        }
        fixtures.append(
            {
                "id": spec.id,
                "alert_payload_path": spec.alert_payload_path,
                "expected": expected,
            }
        )
    return yaml.safe_dump({"fixtures": fixtures}, sort_keys=True, default_flow_style=False)


def _merge_extends_pair(*, parent: models.Runbook, child: models.Runbook) -> models.Runbook:
    """
    Return ``child`` flattened against a single ``parent``.

    Body, tools, checks, and tests are merged per the F6.K.2 rules
    (see :func:`_merge_tools`, :func:`_merge_checks`); metadata is copied
    from ``child`` verbatim except ``content_sha`` is recomputed over the
    flattened body + sidecars and ``extends`` is cleared so post-flatten
    consumers do not believe further chain resolution is required.
    """
    merged_body = parent.body + _EXTENDS_BODY_SEPARATOR + child.body
    merged_tools = _merge_tools(parent=parent.tools, child=child.tools)
    merged_checks = _merge_checks(parent=parent.checks, child=child.checks)
    # Tests are independent fixtures: parent first, child appended, no
    # override. A fixture id collision between parent and child is left
    # alone — that's a duplicate-key authoring bug the matcher's golden
    # runner will surface separately, not a merge concern.
    merged_tests = parent.tests + child.tests
    flattened_sha = _compute_content_sha(
        body_text=merged_body,
        tools_text=_serialise_tools(merged_tools),
        checks_text=_serialise_checks(merged_checks),
        tests_text=_serialise_tests(merged_tests),
    )
    flattened_metadata = attrs.evolve(
        child.metadata,
        content_sha=flattened_sha,
        extends=None,
    )
    return models.Runbook(
        metadata=flattened_metadata,
        body=merged_body,
        tools=merged_tools,
        checks=merged_checks,
        tests=merged_tests,
        directory=child.directory,
    )
