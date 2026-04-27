from __future__ import annotations

from typing import Any, Self
from unittest import mock

import httpx
import pytest

from sentinel.vendors.confluence import client as confluence_client


def _make_client(
    *,
    base_url: str = "https://acme.atlassian.net/wiki",
    username: str = "publisher@acme.test",
    api_token: str = "secret-token",  # noqa: S107  test fixture credential
    space_key: str = "RUNBOOKS",
    parent_page_id: str | None = None,
) -> confluence_client.ConfluenceClient:
    """Construct a ConfluenceClient with publish-ready defaults for tests."""
    return confluence_client.ConfluenceClient(
        base_url=base_url,
        username=username,
        api_token=api_token,
        space_key=space_key,
        parent_page_id=parent_page_id,
    )


def _make_response(
    *, status_code: int, json_payload: Any = None, text: str = ""
) -> httpx.Response:
    """Construct a real httpx.Response (so callers behave like the live SDK)."""
    if json_payload is not None:
        return httpx.Response(status_code=status_code, json=json_payload)
    return httpx.Response(status_code=status_code, text=text)


class _FakeAsyncHttpClient:
    """
    Stand-in for ``httpx.AsyncClient`` returning queued responses per HTTP verb.

    Records the args of every call so tests can assert on request shapes
    without monkeypatching the real httpx transport (we still go through
    the real Response object so JSON parsing and status-code handling
    matches production).
    """

    def __init__(self) -> None:
        self.get_responses: list[httpx.Response] = []
        self.post_responses: list[httpx.Response] = []
        self.put_responses: list[httpx.Response] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.put_calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.get_calls.append((path, params or {}))
        return self.get_responses.pop(0)

    async def post(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.post_calls.append((path, json or {}))
        return self.post_responses.pop(0)

    async def put(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.put_calls.append((path, json or {}))
        return self.put_responses.pop(0)


class TestIsConfigured:
    def test_returns_false_when_base_url_is_empty(self) -> None:
        # Given a client constructed with an empty base URL
        client = _make_client(base_url="")

        # When is_configured is read
        result = client.is_configured

        # Then it is False (publish should be skipped at the script layer)
        assert result is False

    def test_returns_false_when_username_is_empty(self) -> None:
        # Given a client constructed with an empty username
        client = _make_client(username="")

        # When is_configured is read
        result = client.is_configured

        # Then it is False
        assert result is False

    def test_returns_false_when_api_token_is_empty(self) -> None:
        # Given a client constructed with an empty API token
        client = _make_client(api_token="")

        # When is_configured is read
        result = client.is_configured

        # Then it is False
        assert result is False

    def test_returns_false_when_space_key_is_empty(self) -> None:
        # Given a client constructed with an empty space key
        client = _make_client(space_key="")

        # When is_configured is read
        result = client.is_configured

        # Then it is False
        assert result is False

    def test_returns_true_when_all_required_fields_present(self) -> None:
        # Given a fully-configured client (parent_page_id intentionally None)
        client = _make_client()

        # When is_configured is read
        result = client.is_configured

        # Then it is True even without a parent page id (parent is optional)
        assert result is True


class TestGetPageByTitle:
    @pytest.mark.asyncio
    async def test_returns_page_when_results_present(self) -> None:
        # Given a fake httpx client returning a single matching page result
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(
            _make_response(
                status_code=200,
                json_payload={
                    "results": [
                        {
                            "id": "12345",
                            "title": "k8s-crashloop",
                            "version": {"number": 7},
                            "metadata": {
                                "properties": {
                                    "sentinel_content_sha": {"value": "abc123"},
                                },
                            },
                        },
                    ],
                },
            ),
        )
        client = _make_client()

        # When get_page_by_title is invoked
        with mock.patch.object(
            confluence_client.ConfluenceClient,
            "_build_http_client",
            return_value=fake_http,
        ):
            page = await client.get_page_by_title(title="k8s-crashloop")

        # Then the parsed ConfluencePage carries the page id, version, and content sha
        assert page is not None
        assert page.page_id == "12345"
        assert page.version == 7
        assert page.sentinel_content_sha == "abc123"

    @pytest.mark.asyncio
    async def test_returns_none_on_404_response(self) -> None:
        # Given a fake httpx client returning a 404 to the lookup
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(_make_response(status_code=404, text="not found"))
        client = _make_client()

        # When get_page_by_title is invoked
        with mock.patch.object(
            confluence_client.ConfluenceClient,
            "_build_http_client",
            return_value=fake_http,
        ):
            page = await client.get_page_by_title(title="missing-runbook")

        # Then the result is None (signalling the upsert path to create)
        assert page is None

    @pytest.mark.asyncio
    async def test_returns_none_when_results_list_empty(self) -> None:
        # Given a fake httpx client returning a 200 with an empty results array
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(
            _make_response(status_code=200, json_payload={"results": []}),
        )
        client = _make_client()

        # When get_page_by_title is invoked
        with mock.patch.object(
            confluence_client.ConfluenceClient,
            "_build_http_client",
            return_value=fake_http,
        ):
            page = await client.get_page_by_title(title="not-yet-published")

        # Then the result is None
        assert page is None

    @pytest.mark.asyncio
    async def test_raises_api_error_on_500(self) -> None:
        # Given a fake httpx client returning a 500 to the lookup
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(_make_response(status_code=500, text="boom"))
        client = _make_client()

        # When get_page_by_title is invoked
        with (
            mock.patch.object(
                confluence_client.ConfluenceClient,
                "_build_http_client",
                return_value=fake_http,
            ),
            pytest.raises(confluence_client.ConfluenceAPIError) as exc_info,
        ):
            await client.get_page_by_title(title="anything")

        # Then a ConfluenceAPIError is raised carrying the status and action label
        assert exc_info.value.status_code == 500
        assert exc_info.value.action == "get_page_by_title"


class TestUpsertPage:
    @pytest.mark.asyncio
    async def test_creates_when_page_does_not_exist(self) -> None:
        # Given the lookup returns 404 and the create POST returns 200 with a new page id
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(_make_response(status_code=404, text="not found"))
        fake_http.post_responses.append(
            _make_response(status_code=200, json_payload={"id": "99999"}),
        )
        client = _make_client(parent_page_id="parent-page-1")

        # When upsert_page is invoked for a previously-unseen runbook
        with mock.patch.object(
            confluence_client.ConfluenceClient,
            "_build_http_client",
            return_value=fake_http,
        ):
            result = await client.upsert_page(
                title="k8s-crashloop",
                body_storage="<h1>Body</h1>",
                sentinel_content_sha="sha-create-001",
            )

        # Then the create endpoint was hit with the body, content-sha property, and parent
        assert result.action == "created"
        assert result.page_id == "99999"
        assert result.sentinel_content_sha == "sha-create-001"
        assert len(fake_http.post_calls) == 1
        post_path, post_payload = fake_http.post_calls[0]
        assert post_path == "/rest/api/content"
        assert post_payload["title"] == "k8s-crashloop"
        assert post_payload["space"]["key"] == "RUNBOOKS"
        assert post_payload["body"]["storage"]["value"] == "<h1>Body</h1>"
        assert (
            post_payload["metadata"]["properties"]["sentinel_content_sha"]["value"]
            == "sha-create-001"
        )
        assert post_payload["ancestors"] == [{"id": "parent-page-1"}]
        assert fake_http.put_calls == []

    @pytest.mark.asyncio
    async def test_updates_when_existing_page_has_different_sha(self) -> None:
        # Given the lookup returns an existing page with a stale content sha
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(
            _make_response(
                status_code=200,
                json_payload={
                    "results": [
                        {
                            "id": "55555",
                            "title": "k8s-crashloop",
                            "version": {"number": 3},
                            "metadata": {
                                "properties": {
                                    "sentinel_content_sha": {"value": "stale-sha"},
                                },
                            },
                        },
                    ],
                },
            ),
        )
        fake_http.put_responses.append(
            _make_response(status_code=200, json_payload={"id": "55555"})
        )
        client = _make_client()

        # When upsert_page is invoked with a fresh content sha
        with mock.patch.object(
            confluence_client.ConfluenceClient,
            "_build_http_client",
            return_value=fake_http,
        ):
            result = await client.upsert_page(
                title="k8s-crashloop",
                body_storage="<h1>Updated body</h1>",
                sentinel_content_sha="fresh-sha",
            )

        # Then the update endpoint was hit with version=4 and the fresh sha
        assert result.action == "updated"
        assert result.page_id == "55555"
        assert len(fake_http.put_calls) == 1
        put_path, put_payload = fake_http.put_calls[0]
        assert put_path == "/rest/api/content/55555"
        assert put_payload["version"]["number"] == 4
        assert put_payload["body"]["storage"]["value"] == "<h1>Updated body</h1>"
        assert (
            put_payload["metadata"]["properties"]["sentinel_content_sha"]["value"] == "fresh-sha"
        )
        assert fake_http.post_calls == []

    @pytest.mark.asyncio
    async def test_skips_when_existing_page_sha_matches(self) -> None:
        # Given the lookup returns an existing page whose sha matches the input
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(
            _make_response(
                status_code=200,
                json_payload={
                    "results": [
                        {
                            "id": "77777",
                            "title": "k8s-crashloop",
                            "version": {"number": 9},
                            "metadata": {
                                "properties": {
                                    "sentinel_content_sha": {"value": "matching-sha"},
                                },
                            },
                        },
                    ],
                },
            ),
        )
        client = _make_client()

        # When upsert_page is invoked with the same sha
        with mock.patch.object(
            confluence_client.ConfluenceClient,
            "_build_http_client",
            return_value=fake_http,
        ):
            result = await client.upsert_page(
                title="k8s-crashloop",
                body_storage="<h1>Body</h1>",
                sentinel_content_sha="matching-sha",
            )

        # Then no PUT or POST is issued and the action is skipped_unchanged
        assert result.action == "skipped_unchanged"
        assert result.page_id == "77777"
        assert fake_http.post_calls == []
        assert fake_http.put_calls == []

    @pytest.mark.asyncio
    async def test_raises_when_unconfigured(self) -> None:
        # Given a client missing the api token
        client = _make_client(api_token="")

        # When upsert_page is invoked
        with pytest.raises(confluence_client.ConfluenceUnconfiguredError):
            await client.upsert_page(
                title="any",
                body_storage="any",
                sentinel_content_sha="any",
            )

        # Then ConfluenceUnconfiguredError is raised before any network attempt

    @pytest.mark.asyncio
    async def test_raises_api_error_when_create_returns_non_2xx(self) -> None:
        # Given the lookup returns 404 and the create POST returns 500
        fake_http = _FakeAsyncHttpClient()
        fake_http.get_responses.append(_make_response(status_code=404, text="not found"))
        fake_http.post_responses.append(_make_response(status_code=500, text="server boom"))
        client = _make_client()

        # When upsert_page is invoked
        with (
            mock.patch.object(
                confluence_client.ConfluenceClient,
                "_build_http_client",
                return_value=fake_http,
            ),
            pytest.raises(confluence_client.ConfluenceAPIError) as exc_info,
        ):
            await client.upsert_page(
                title="any",
                body_storage="any",
                sentinel_content_sha="any",
            )

        # Then a ConfluenceAPIError surfaces with the create action label
        assert exc_info.value.status_code == 500
        assert exc_info.value.action == "create_page"


class TestGetPageByTitleUnconfigured:
    @pytest.mark.asyncio
    async def test_get_page_by_title_raises_when_unconfigured(self) -> None:
        # Given a client missing credentials
        client = _make_client(api_token="")

        # When get_page_by_title is invoked
        with pytest.raises(confluence_client.ConfluenceUnconfiguredError):
            await client.get_page_by_title(title="anything")

        # Then ConfluenceUnconfiguredError is raised explicitly (not silently no-op)
