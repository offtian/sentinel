from __future__ import annotations

from unittest.mock import MagicMock, patch

from sentinel.domain.vendor_adapters import confluence


class TestConfluenceClientIsConfigured:
    def test_configured_when_all_fields_set(self):
        # Given a ConfluenceClient with all credentials
        # When checking is_configured
        client = confluence.ConfluenceClient(
            base_url="https://example.atlassian.net",
            api_token="token123",  # noqa: S106
            user_email="user@example.com",
        )

        # Then it reports as configured
        assert client.is_configured is True

    def test_not_configured_when_missing_token(self):
        # Given a ConfluenceClient with empty token
        # When checking is_configured
        client = confluence.ConfluenceClient(
            base_url="https://example.atlassian.net",
            api_token="",
            user_email="user@example.com",
        )

        # Then it reports as not configured
        assert client.is_configured is False


class TestConfluenceClientSearch:
    async def test_returns_empty_list_when_not_configured(self):
        # Given an unconfigured client
        # When searching
        client = confluence.ConfluenceClient(base_url="", api_token="", user_email="")
        results = await client.search(cql='type=page AND text~"login"')

        # Then an empty list is returned
        assert results == []

    async def test_returns_results_when_configured(self):
        # Given a configured client with a mocked Confluence SDK response
        mock_response = {
            "results": [
                {
                    "content": {
                        "id": "123",
                        "title": "Login Guide",
                        "space": {"name": "Documentation", "key": "DOCS"},
                        "_links": {"webui": "/wiki/spaces/DOCS/pages/123"},
                    },
                    "excerpt": "To reset your password...",
                    "lastModified": "2024-01-01T00:00:00.000Z",
                }
            ]
        }

        mock_sdk = MagicMock()
        mock_sdk.cql.return_value = mock_response

        client = confluence.ConfluenceClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )

        with patch.object(client, "_get_client", return_value=mock_sdk):
            results = await client.search(cql='type=page AND text~"login"', limit=5)

        # Then results are structured correctly
        assert len(results) == 1
        assert results[0]["title"] == "Login Guide"
        assert results[0]["id"] == "123"
        assert results[0]["space"] == "Documentation"
        assert "https://example.atlassian.net" in results[0]["url"]

    async def test_returns_empty_list_on_sdk_exception(self):
        # Given a configured client whose SDK raises
        # When searching
        client = confluence.ConfluenceClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )
        mock_sdk = MagicMock()
        mock_sdk.cql.side_effect = Exception("API unreachable")

        with patch.object(client, "_get_client", return_value=mock_sdk):
            results = await client.search(cql="type=page", limit=5)

        # Then an empty list is returned gracefully
        assert results == []


class TestConfluenceClientGetPageContent:
    async def test_returns_none_when_not_configured(self):
        # Given an unconfigured client
        # When fetching page content
        client = confluence.ConfluenceClient(base_url="", api_token="", user_email="")
        result = await client.get_page_content(page_id="123")

        # Then None is returned
        assert result is None

    async def test_returns_page_dict_when_configured(self):
        # Given a configured client with a mocked SDK page response
        mock_page = {
            "id": "123",
            "title": "Login Guide",
            "body": {"storage": {"value": "<p>Reset your password here.</p>"}},
            "space": {"name": "Documentation"},
            "_links": {"webui": "/wiki/spaces/DOCS/pages/123"},
            "version": {"when": "2024-01-01T00:00:00.000Z"},
        }

        mock_sdk = MagicMock()
        mock_sdk.get_page_by_id.return_value = mock_page

        client = confluence.ConfluenceClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.get_page_content(page_id="123")

        # Then a structured dict with body_text is returned
        assert result is not None
        assert result["title"] == "Login Guide"
        assert "Reset your password" in result["body_text"]

    async def test_returns_none_on_sdk_exception(self):
        # Given a configured client whose SDK raises
        # When fetching page content
        client = confluence.ConfluenceClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )
        mock_sdk = MagicMock()
        mock_sdk.get_page_by_id.side_effect = Exception("Page not found")

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.get_page_content(page_id="999")

        # Then None is returned gracefully
        assert result is None


class TestHtmlToPlainText:
    def test_strips_html_tags(self):
        # Given an HTML string with tags
        # When converting to plain text
        result = confluence._html_to_plain_text("<p>Hello <b>world</b></p>")

        # Then tags are removed
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result
        assert "world" in result

    def test_decodes_html_entities(self):
        # Given HTML with entities
        # When converting to plain text
        result = confluence._html_to_plain_text("AT&amp;T &lt;company&gt;")

        # Then entities are decoded
        assert "AT&T" in result
        assert "<company>" in result

    def test_normalises_whitespace(self):
        # Given HTML with excessive newlines
        # When converting to plain text
        result = confluence._html_to_plain_text("<p>First</p>\n\n\n\n<p>Second</p>")

        # Then multiple blank lines are collapsed
        assert "\n\n\n" not in result
