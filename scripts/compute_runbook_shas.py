"""
Recompute ``content_sha`` for every runbook quartet and write back to the
``RUNBOOK.md`` frontmatter (F6.E pre-commit hook).

Walks the configured ``runbooks_paths`` (or the ``--paths`` override for
tests), loads each four-file runbook via ``sentinel.domain.runbooks.loader``
to validate its schema, then re-emits the frontmatter with the freshly
computed canonical sha.

Modes:
- default (write): rewrite frontmatter when the stored sha differs from
  the computed one; idempotent (no-op when the tree is already clean).
- ``--check`` (CI): exit 1 without modifying any file when at least one
  runbook's stored sha drifts from the computed one. Authors must run the
  hook locally and commit the refreshed shas — CI never writes.

Schema errors raised by the loader are caught per-runbook so one bad
runbook does not poison the whole batch; the run as a whole exits non-zero
when any runbook failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import frontmatter

from sentinel import config as sentinel_config
from sentinel.domain.runbooks import loader as runbooks_loader
from sentinel.utils import logs


def _process_runbook(runbook_dir: Path) -> tuple[bool, str, str]:
    """
    Recompute ``content_sha`` for a single runbook and update its frontmatter.

    :returns: ``(changed, runbook_id, fresh_sha)`` where ``changed`` is
        ``True`` iff the on-disk frontmatter sha was updated (or would be in
        ``--check`` mode).
    :raises sentinel.domain.runbooks.models.RunbookSchemaError: when the
        runbook's schema is invalid (re-raised through ``load_runbook``).
    """
    runbook = runbooks_loader.load_runbook(runbook_dir)
    runbook_md = runbook_dir / "RUNBOOK.md"
    # Read text first then parse via ``loads`` rather than the path-based
    # ``frontmatter.load``: the latter goes through ``codecs.open`` which is
    # deprecated under Python 3.14.
    post = frontmatter.loads(runbook_md.read_text(encoding="utf-8"))
    existing = post.get("content_sha")
    fresh = runbook.metadata.content_sha
    if existing == fresh:
        return False, runbook.metadata.runbook_id, fresh
    return True, runbook.metadata.runbook_id, fresh


def _write_runbook_sha(runbook_dir: Path, fresh_sha: str) -> None:
    """Rewrite ``RUNBOOK.md`` with the new ``content_sha`` in its frontmatter."""
    runbook_md = runbook_dir / "RUNBOOK.md"
    post = frontmatter.loads(runbook_md.read_text(encoding="utf-8"))
    post["content_sha"] = fresh_sha
    runbook_md.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def _walk_paths(paths: list[Path]) -> list[Path]:
    """
    Walk ``paths`` and return every directory containing a ``RUNBOOK.md``.

    Non-existent roots are skipped silently — pre-commit will run the hook
    on a fresh checkout where some optional team runbook directories may not
    yet exist.
    """
    found: list[Path] = []
    for root in paths:
        if not root.exists():
            continue
        for runbook_md in root.rglob("RUNBOOK.md"):
            found.append(runbook_md.parent)
    return found


def _resolve_paths(override: list[Path] | None) -> list[Path]:
    """Return the explicit ``--paths`` override if provided, else the configured list."""
    if override is not None:
        return list(override)
    return list(sentinel_config.get_config().runbooks_paths)


def main(argv: list[str] | None = None) -> int:
    """
    Run the recompute / check workflow and return a process exit code.

    :returns: ``0`` on success, ``1`` when ``--check`` finds drift or when any
        runbook fails to load due to a schema error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 without modifying when any frontmatter content_sha differs (CI mode).",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        type=Path,
        default=None,
        help="Override runbooks_paths from config (for tests / scripting).",
    )
    args = parser.parse_args(argv)

    paths = _resolve_paths(args.paths)
    runbook_dirs = _walk_paths(paths)
    if not runbook_dirs:
        logs.log_event(
            "compute_runbook_shas_no_runbooks_found",
            params={"paths": [str(path) for path in paths]},
        )
        return 0

    drift: list[str] = []
    updated: list[str] = []
    failed: list[tuple[str, str]] = []
    for runbook_dir in sorted(runbook_dirs):
        try:
            changed, runbook_id, fresh_sha = _process_runbook(runbook_dir)
        except Exception as exc:
            logs.log_exception(exc, params={"runbook_dir": str(runbook_dir)})
            failed.append((str(runbook_dir), repr(exc)))
            continue
        if not changed:
            continue
        if args.check:
            drift.append(runbook_id)
            continue
        _write_runbook_sha(runbook_dir, fresh_sha)
        updated.append(runbook_id)

    if failed:
        logs.log_event(
            "compute_runbook_shas_schema_errors",
            params={"failures": failed},
        )
        return 1
    if args.check:
        if drift:
            logs.log_event(
                "compute_runbook_shas_drift_detected",
                params={"runbooks_with_stale_sha": drift},
            )
            return 1
        return 0
    logs.log_event(
        "compute_runbook_shas_completed",
        params={"updated": updated, "count": len(updated)},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
