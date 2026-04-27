"""
Confluence vendor adapter for the F6 runbook write-side PR-bot.

The Sentinel runbook catalog is filesystem-in-git as the source of truth.
This package owns the *one-way* publish path: on every merge to main, the
:mod:`scripts.runbook_confluence_publish` script reads runbooks from disk
and upserts them as Confluence pages so non-engineers can browse the
catalog without git access. **Confluence is a read-only consumer** —
edits made there are discarded by the next publish, which deliberately
drives editors back to the PR workflow.

Two modules:

* :mod:`client` — minimal HTTP client wrapping the Confluence REST API
  (storage-format pages, ``sentinel_content_sha`` page-property gating
  for idempotent skips). No-op-when-unconfigured pattern matches the
  vendor-adapter convention in :mod:`sentinel.domain.vendor_adapters`,
  but raises explicitly via :class:`client.ConfluenceUnconfiguredError`
  rather than silently succeeding (per F6.N.4: publish failures must
  surface in CI).
* :mod:`converter` — pure-Python Markdown → Confluence storage-format
  conversion. No external binary dependency (no pandoc).

See :mod:`scripts.runbook_confluence_publish` for the entry point and
``docs/operations/runbooks-confluence.md`` for operations.
"""

from __future__ import annotations
