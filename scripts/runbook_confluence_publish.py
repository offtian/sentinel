"""
Publish Sentinel runbooks to Confluence (idempotent, content-sha gated).

Reads the on-disk runbook catalog via :func:`sentinel.domain.runbooks.loader.discover_runbooks`,
converts each runbook body from Markdown to Confluence storage format,
and upserts a Confluence page per runbook. The publish is idempotent:
runbooks whose ``content_sha`` already matches the
``sentinel_content_sha`` page property in Confluence are skipped
without a network write.

Designed to run as a CI step on every merge to ``main`` (see
``.github/workflows/runbook-publish.yml``). Behaviour by environment:

* **Configured** (``confluence_base_url`` / ``_user`` / ``_token`` /
  ``_space_key`` all set): walks the catalog, publishes each runbook,
  exits 0 on success or 1 if any runbook errored.
* **Unconfigured** (any required field empty): logs
  ``runbook_confluence_publish_skipped_unconfigured`` and exits 0.
  This keeps the CI workflow green for deployments that haven't
  wired Confluence yet — the script is opt-in by configuration.

Runbooks whose ``runbook_id`` starts with ``_`` (private workflow
templates like ``_generic-investigation``) or ``AUTOGEN-`` (gap-flywheel
auto-PR scaffolds from F6.M) are skipped: those are internal authoring
artefacts, not user-facing documentation.

Confluence is a **read-only consumer** of the runbook catalog —
edits made there are discarded by the next publish. See
``docs/operations/runbooks-confluence.md``.
"""

from __future__ import annotations

import asyncio
import sys

from sentinel import config
from sentinel.domain.runbooks import loader as runbook_loader
from sentinel.utils import logs
from sentinel.vendors.confluence import client as confluence_client_mod
from sentinel.vendors.confluence import converter as confluence_converter_mod


_PRIVATE_RUNBOOK_PREFIX = "_"
_AUTOGEN_RUNBOOK_PREFIX = "AUTOGEN-"


def _build_client() -> confluence_client_mod.ConfluenceClient:
    """Construct a :class:`ConfluenceClient` from current settings."""
    cfg = config.get_config()
    settings = cfg.settings
    token_secret = settings.confluence_token
    api_token = token_secret.get_secret_value() if token_secret is not None else ""
    return confluence_client_mod.ConfluenceClient(
        base_url=settings.confluence_base_url,
        username=settings.confluence_user,
        api_token=api_token,
        space_key=settings.confluence_space_key,
        parent_page_id=settings.confluence_parent_page_id or None,
    )


def _is_publishable(runbook_id: str) -> bool:
    """Return True when ``runbook_id`` should be published to Confluence."""
    if runbook_id.startswith(_PRIVATE_RUNBOOK_PREFIX):
        return False
    return not runbook_id.startswith(_AUTOGEN_RUNBOOK_PREFIX)


async def _publish_one(
    *,
    client: confluence_client_mod.ConfluenceClient,
    runbook_id: str,
    runbook: object,
) -> str:
    """
    Publish a single runbook and return the upsert action.

    Returns one of ``created``, ``updated``, ``skipped_unchanged``, or
    ``error``. The error case is converted to a string here (rather
    than re-raised) so the caller can keep going through the rest of
    the catalog and report a per-publish summary at the end.
    """
    body_storage = confluence_converter_mod.markdown_to_confluence_storage(
        markdown_text=runbook.body,  # type: ignore[attr-defined]
    )
    try:
        result = await client.upsert_page(
            title=runbook_id,
            body_storage=body_storage,
            sentinel_content_sha=runbook.metadata.content_sha,  # type: ignore[attr-defined]
        )
    except Exception as exc:
        logs.log_exception(exc, params={"runbook_id": runbook_id})
        return "error"
    return result.action


async def _main() -> int:
    """Entry point. Returns the process exit code (0 success / unconfigured, 1 errors)."""
    cfg = config.get_config()
    client = _build_client()
    if not client.is_configured:
        logs.log_event(
            "runbook_confluence_publish_skipped_unconfigured",
            params={"reason": "missing_creds_or_space"},
        )
        return 0

    catalog = runbook_loader.discover_runbooks(cfg.runbooks_paths)
    summary: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "skipped_unchanged": 0,
        "error": 0,
    }
    for runbook_id, runbook in sorted(catalog.items()):
        if not _is_publishable(runbook_id):
            continue
        action = await _publish_one(client=client, runbook_id=runbook_id, runbook=runbook)
        summary[action] = summary.get(action, 0) + 1

    logs.log_event("runbook_confluence_publish_summary", params=summary)
    return 1 if summary["error"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
