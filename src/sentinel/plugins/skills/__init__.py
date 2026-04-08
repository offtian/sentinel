"""
Skills runtime: on-disk runbook catalogue with deterministic loading.

A Skill is a ``SKILL.md`` file inside its own directory under this package.
The file has YAML frontmatter and a Markdown body::

    ---
    name: k8s-crashloop-runbook
    description: Procedure for investigating CrashLoopBackOff pods
    version: 0.1.0
    applies_to: ["k8s_*", "kubernetes_crashloop"]
    ---

    # K8s CrashLoopBackOff Runbook

    ...body...

``load_skills_for(category=..., max_skills=...)`` returns a deterministic
``tuple[SkillHandle, ...]`` of skills whose ``applies_to`` patterns match the
classifier's free-form category string (case-insensitive fnmatch globs). An
empty ``applies_to`` list means the skill is universal.

Every activated skill emits a ``skill_activated`` structlog event carrying the
skill name, version, sha256, and the category it was matched for — the replay
and audit slices consume this.
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
from pathlib import Path
from typing import Any

import attrs
import yaml

from sentinel.utils import logs


SKILLS_DIR = Path(__file__).parent

_REQUIRED_FRONTMATTER_KEYS = frozenset({"name", "description", "version", "applies_to"})


class SkillFrontmatterError(ValueError):
    """
    Raised when a SKILL.md file has missing or malformed frontmatter.
    """


@attrs.frozen
class SkillHandle:
    """
    Immutable handle to a loaded skill, carrying rendered body and content hash.
    """

    name: str
    version: str
    description: str
    applies_to: tuple[str, ...]
    body: str
    sha256: str


def _parse_skill_file(path: Path) -> SkillHandle:
    """
    Parse a SKILL.md file into a SkillHandle.

    :raises SkillFrontmatterError: if the file has no frontmatter, the
        frontmatter is not a mapping, or required keys are missing.
    """
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_text = raw_bytes.decode("utf-8")

    if not raw_text.startswith("---"):
        msg = f"{path} is missing YAML frontmatter"
        raise SkillFrontmatterError(msg)

    parts = raw_text.split("---", 2)
    if len(parts) < 3:
        msg = f"{path} has an unterminated frontmatter block"
        raise SkillFrontmatterError(msg)

    frontmatter_text = parts[1]
    body = parts[2].lstrip("\n")

    parsed = yaml.safe_load(frontmatter_text)
    if not isinstance(parsed, dict):
        msg = f"{path} frontmatter must be a mapping, got {type(parsed).__name__}"
        raise SkillFrontmatterError(msg)

    missing = _REQUIRED_FRONTMATTER_KEYS - parsed.keys()
    if missing:
        msg = f"{path} frontmatter missing required keys: {sorted(missing)}"
        raise SkillFrontmatterError(msg)

    applies_to_raw = parsed["applies_to"]
    if not isinstance(applies_to_raw, list):
        msg = f"{path} frontmatter 'applies_to' must be a list"
        raise SkillFrontmatterError(msg)

    return SkillHandle(
        name=str(parsed["name"]),
        version=str(parsed["version"]),
        description=str(parsed["description"]),
        applies_to=tuple(str(pattern) for pattern in applies_to_raw),
        body=body,
        sha256=digest,
    )


def all_installed_skills() -> tuple[SkillHandle, ...]:
    """
    Return the full cached catalogue of installed Skills, sorted by name.

    Public accessor used by callers that need to list the catalogue
    without filtering by category (e.g. the ``list_skills`` FastMCP tool).
    Internally delegates to the lru-cached ``_load_all_skills``.
    """
    return _load_all_skills()


@functools.lru_cache(maxsize=1)
def _load_all_skills() -> tuple[SkillHandle, ...]:
    """
    Read every ``<dir>/SKILL.md`` under ``SKILLS_DIR`` sorted by directory name.

    Cached for the life of the process; tests call ``.cache_clear()`` between
    cases.

    :raises SkillFrontmatterError: if any discovered SKILL.md is malformed.
    """
    handles: list[SkillHandle] = []
    for child in sorted(SKILLS_DIR.iterdir()):
        if not child.is_dir():
            continue
        skill_path = child / "SKILL.md"
        if not skill_path.exists():
            continue
        handles.append(_parse_skill_file(skill_path))
    return tuple(sorted(handles, key=lambda handle: handle.name))


def _matches_category(*, applies_to: tuple[str, ...], category: str) -> bool:
    """
    Return True if ``category`` matches any ``applies_to`` glob.

    An empty ``applies_to`` tuple is treated as universal (always matches).
    Matching is case-insensitive.
    """
    if not applies_to:
        return True
    category_lower = category.lower()
    return any(fnmatch.fnmatchcase(category_lower, pattern.lower()) for pattern in applies_to)


def load_skills_for(*, category: str, max_skills: int) -> tuple[SkillHandle, ...]:
    """
    Return skills matching ``category``, deterministically sorted and truncated.

    Emits one ``skill_activated`` structlog event per returned skill, or a
    single ``skills_no_match`` event when the catalogue has no matches.

    :raises SkillFrontmatterError: if the catalogue contains any malformed
        SKILL.md files (surfaced from ``_load_all_skills``).
    """
    all_skills = _load_all_skills()
    matching = tuple(
        handle
        for handle in all_skills
        if _matches_category(applies_to=handle.applies_to, category=category)
    )
    selected = matching[:max_skills]

    if not selected:
        logs.log_event(
            "skills_no_match",
            params={"category": category, "catalogue_size": len(all_skills)},
        )
        return ()

    for handle in selected:
        params: dict[str, Any] = {
            "skill_name": handle.name,
            "version": handle.version,
            "sha256": handle.sha256,
            "category": category,
        }
        logs.log_event("skill_activated", params=params)

    return selected
