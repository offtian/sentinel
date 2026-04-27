"""
Confluence REST API client for the F6 runbook write-side PR-bot.

Wraps the subset of the Confluence Cloud REST API needed to publish
runbooks as pages, with content-sha-gated upserts so re-runs of the
publish script are no-ops when the on-disk runbook hasn't changed.

The client follows the vendor-adapter no-op pattern from
:mod:`sentinel.domain.vendor_adapters` (``is_configured`` predicate)
but with one deliberate divergence: when unconfigured, the methods
*raise* :class:`ConfluenceUnconfiguredError` instead of silently
succeeding. F6.N.4 requires that publish failures stay visible in CI;
silently no-op'ing on missing creds would let a misconfigured deploy
stop publishing without anyone noticing. The script-level entry point
(:mod:`scripts.runbook_confluence_publish`) gates on ``is_configured``
*before* calling the client and exits zero in that case, so the
"missing creds == clean exit" UX is preserved at the right layer.

The client speaks JSON to the v1 ``/rest/api/content`` endpoint with
HTTP Basic auth (``username:api_token``). All requests carry
``timeout=10.0`` per project HTTP-conventions rule.
"""

from __future__ import annotations

from typing import Any, Literal

import attrs
import httpx

from sentinel.utils import logs


_REQUEST_TIMEOUT_SECONDS = 10.0
_CONTENT_TYPE_PAGE = "page"
_REPRESENTATION_STORAGE = "storage"
_CONTENT_SHA_PROPERTY_KEY = "sentinel_content_sha"
_HTTP_NOT_FOUND = 404


class ConfluenceUnconfiguredError(RuntimeError):
    """
    Raised when a :class:`ConfluenceClient` method is invoked while ``is_configured`` is false.

    The client deliberately surfaces this rather than silently no-op'ing
    so a misconfigured CI deploy can't stop publishing runbooks without
    anyone noticing. Callers that want to skip on missing creds should
    gate on ``client.is_configured`` *before* calling.
    """


class ConfluenceAPIError(RuntimeError):
    """
    Wraps a non-2xx response from the Confluence REST API.

    Carries the HTTP status code and (truncated) response body so the
    structured-log entry from :func:`sentinel.utils.logs.log_exception`
    has enough context to debug a publish failure without a separate
    Sentry round-trip.
    """

    def __init__(self, *, status_code: int, body: str, action: str) -> None:
        # Truncate body to keep log lines bounded; the full body is
        # available in the upstream HTTP-instrumentation span.
        truncated = body if len(body) <= 500 else body[:500] + "...[truncated]"
        super().__init__(
            f"Confluence {action} failed: HTTP {status_code} — {truncated}",
        )
        self.status_code = status_code
        self.body = body
        self.action = action


@attrs.frozen(kw_only=True, slots=True)
class ConfluencePage:
    """
    Snapshot of a Confluence page from the perspective of the runbook PR-bot.

    Carries only the four fields the upsert path needs: the page's
    Confluence ID, its current title (so the caller can verify a
    rename), its monotonic version (Confluence requires the new
    version to be ``current + 1`` on PUT), and the
    ``sentinel_content_sha`` page property if previously set (used to
    short-circuit republishing when the on-disk content hasn't moved).
    """

    page_id: str
    title: str
    version: int
    sentinel_content_sha: str | None


@attrs.frozen(kw_only=True, slots=True)
class ConfluenceUpsertResult:
    """
    Outcome of a single :meth:`ConfluenceClient.upsert_page` call.

    The ``action`` discriminator drives the publish-script's per-runbook
    summary counters (created / updated / skipped_unchanged), which then
    surface as a single structured log line per publish run.
    """

    page_id: str
    action: Literal["created", "updated", "skipped_unchanged"]
    sentinel_content_sha: str


@attrs.frozen(kw_only=True, slots=True)
class ConfluenceClient:
    """
    Minimal Confluence REST client for runbook publish-only flows.

    Exposes :meth:`get_page_by_title` for read-side lookups and
    :meth:`upsert_page` for the publish path. Both methods short-circuit
    with :class:`ConfluenceUnconfiguredError` when ``is_configured`` is
    false — callers gate on the predicate at the entry point so missing
    creds in CI exit zero rather than failing the workflow.

    Construction is pure data assignment; no network calls happen until
    a method is invoked. This keeps tests cheap (construct in a
    fixture, mock httpx in the method tests).
    """

    base_url: str
    username: str
    api_token: str
    space_key: str
    parent_page_id: str | None = None

    @property
    def is_configured(self) -> bool:
        """
        Return True when all four required credential fields are non-empty.

        ``parent_page_id`` is intentionally optional: Confluence allows
        creating top-level pages in a space without a parent, and many
        small deployments will not bother nesting the runbook catalog.
        """
        return bool(self.base_url and self.username and self.api_token and self.space_key)

    def _ensure_configured(self) -> None:
        """Raise :class:`ConfluenceUnconfiguredError` when the client lacks credentials."""
        if not self.is_configured:
            logs.log_event(
                "confluence_client_unconfigured",
                params={"space_key": self.space_key, "has_base_url": bool(self.base_url)},
            )
            raise ConfluenceUnconfiguredError(
                "ConfluenceClient is missing one or more of: base_url, username, "
                "api_token, space_key. Set the corresponding settings or gate "
                "on `client.is_configured` at the call site.",
            )

    def _build_http_client(self) -> httpx.AsyncClient:
        """
        Return a fresh :class:`httpx.AsyncClient` with auth + timeout configured.

        A new client is created per call rather than cached on the
        instance because :class:`attrs.frozen` instances are immutable
        and httpx clients hold an event-loop-bound connection pool;
        sharing one across loops (a real risk in a script that may be
        invoked from a sync entry point and a pytest async loop) leaks
        warnings. The connection-establishment cost is negligible for
        the publish workload (low double-digit pages per run).
        """
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.username, self.api_token),
            timeout=_REQUEST_TIMEOUT_SECONDS,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def get_page_by_title(self, *, title: str) -> ConfluencePage | None:
        """
        Return the Confluence page with the given title in the configured space, or None.

        Looks up via ``GET /rest/api/content?spaceKey=...&title=...&expand=version,metadata.properties``.
        Returns ``None`` when the page does not exist (empty results
        list) or when the API responds with a 404. Other non-2xx
        responses raise :class:`ConfluenceAPIError`.

        :raises ConfluenceUnconfiguredError: when the client lacks credentials.
        :raises ConfluenceAPIError: on non-2xx responses other than 404.
        """
        self._ensure_configured()
        async with self._build_http_client() as http:
            response = await http.get(
                "/rest/api/content",
                params={
                    "spaceKey": self.space_key,
                    "title": title,
                    "expand": "version,metadata.properties",
                },
            )
        if response.status_code == _HTTP_NOT_FOUND:
            logs.log_event("confluence_page_lookup_not_found", params={"title": title})
            return None
        if response.status_code >= 400:
            raise ConfluenceAPIError(
                status_code=response.status_code,
                body=response.text,
                action="get_page_by_title",
            )
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not results:
            return None
        return _parse_page(results[0])

    async def upsert_page(
        self,
        *,
        title: str,
        body_storage: str,
        sentinel_content_sha: str,
    ) -> ConfluenceUpsertResult:
        """
        Create or update the page with ``title``, gated on ``sentinel_content_sha``.

        Behaviour:

        1. Look up the existing page by title in the configured space.
        2. If absent, POST a new page with the given storage-format
           body and stamp ``sentinel_content_sha`` as a page property
           (``action="created"``).
        3. If present and the existing page property already matches
           ``sentinel_content_sha``, return ``action="skipped_unchanged"``
           without touching Confluence (idempotent re-publish).
        4. Otherwise PUT an updated body, bump the version, and refresh
           the page property (``action="updated"``).

        :raises ConfluenceUnconfiguredError: when the client lacks credentials.
        :raises ConfluenceAPIError: on non-2xx responses from create/update.
        """
        self._ensure_configured()
        existing = await self.get_page_by_title(title=title)
        if existing is None:
            page_id = await self._create_page(
                title=title,
                body_storage=body_storage,
                sentinel_content_sha=sentinel_content_sha,
            )
            logs.log_event(
                "confluence_page_created",
                params={
                    "title": title,
                    "page_id": page_id,
                    "sentinel_content_sha": sentinel_content_sha,
                },
            )
            return ConfluenceUpsertResult(
                page_id=page_id,
                action="created",
                sentinel_content_sha=sentinel_content_sha,
            )
        if existing.sentinel_content_sha == sentinel_content_sha:
            logs.log_event(
                "confluence_page_skipped_unchanged",
                params={
                    "title": title,
                    "page_id": existing.page_id,
                    "sentinel_content_sha": sentinel_content_sha,
                },
            )
            return ConfluenceUpsertResult(
                page_id=existing.page_id,
                action="skipped_unchanged",
                sentinel_content_sha=sentinel_content_sha,
            )
        await self._update_page(
            existing=existing,
            title=title,
            body_storage=body_storage,
            sentinel_content_sha=sentinel_content_sha,
        )
        logs.log_event(
            "confluence_page_updated",
            params={
                "title": title,
                "page_id": existing.page_id,
                "previous_sha": existing.sentinel_content_sha,
                "sentinel_content_sha": sentinel_content_sha,
                "new_version": existing.version + 1,
            },
        )
        return ConfluenceUpsertResult(
            page_id=existing.page_id,
            action="updated",
            sentinel_content_sha=sentinel_content_sha,
        )

    async def _create_page(
        self,
        *,
        title: str,
        body_storage: str,
        sentinel_content_sha: str,
    ) -> str:
        """POST a new Confluence page and stamp the content-sha property; return its id."""
        payload: dict[str, Any] = {
            "type": _CONTENT_TYPE_PAGE,
            "title": title,
            "space": {"key": self.space_key},
            "body": {
                _REPRESENTATION_STORAGE: {
                    "value": body_storage,
                    "representation": _REPRESENTATION_STORAGE,
                },
            },
            "metadata": {
                "properties": {
                    _CONTENT_SHA_PROPERTY_KEY: {"value": sentinel_content_sha},
                },
            },
        }
        if self.parent_page_id:
            payload["ancestors"] = [{"id": self.parent_page_id}]
        async with self._build_http_client() as http:
            response = await http.post("/rest/api/content", json=payload)
        if response.status_code >= 400:
            raise ConfluenceAPIError(
                status_code=response.status_code,
                body=response.text,
                action="create_page",
            )
        body = response.json()
        page_id = str(body.get("id", "")) if isinstance(body, dict) else ""
        if not page_id:
            raise ConfluenceAPIError(
                status_code=response.status_code,
                body=response.text,
                action="create_page_response_missing_id",
            )
        return page_id

    async def _update_page(
        self,
        *,
        existing: ConfluencePage,
        title: str,
        body_storage: str,
        sentinel_content_sha: str,
    ) -> None:
        """PUT an updated body to ``existing.page_id`` and refresh the content-sha property."""
        new_version = existing.version + 1
        payload: dict[str, Any] = {
            "id": existing.page_id,
            "type": _CONTENT_TYPE_PAGE,
            "title": title,
            "space": {"key": self.space_key},
            "version": {"number": new_version},
            "body": {
                _REPRESENTATION_STORAGE: {
                    "value": body_storage,
                    "representation": _REPRESENTATION_STORAGE,
                },
            },
            "metadata": {
                "properties": {
                    _CONTENT_SHA_PROPERTY_KEY: {"value": sentinel_content_sha},
                },
            },
        }
        async with self._build_http_client() as http:
            response = await http.put(f"/rest/api/content/{existing.page_id}", json=payload)
        if response.status_code >= 400:
            raise ConfluenceAPIError(
                status_code=response.status_code,
                body=response.text,
                action="update_page",
            )


def _parse_page(raw: dict[str, Any]) -> ConfluencePage:
    """Translate a Confluence REST payload into a :class:`ConfluencePage`."""
    page_id = str(raw.get("id", ""))
    title = str(raw.get("title", ""))
    version_block = raw.get("version") or {}
    version_number_raw = version_block.get("number", 1) if isinstance(version_block, dict) else 1
    version_number = int(version_number_raw) if isinstance(version_number_raw, int) else 1
    metadata_block = raw.get("metadata") or {}
    properties_block = (
        metadata_block.get("properties", {}) if isinstance(metadata_block, dict) else {}
    )
    sha_block = (
        properties_block.get(_CONTENT_SHA_PROPERTY_KEY)
        if isinstance(properties_block, dict)
        else None
    )
    sentinel_content_sha: str | None = None
    if isinstance(sha_block, dict):
        value = sha_block.get("value")
        sentinel_content_sha = str(value) if value is not None else None
    return ConfluencePage(
        page_id=page_id,
        title=title,
        version=version_number,
        sentinel_content_sha=sentinel_content_sha,
    )
