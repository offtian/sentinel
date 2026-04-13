"""
Tests for MCP server API key authentication middleware.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from sentinel.interfaces.mcp import server as mcp_server


def _build_test_client(*, api_key: str = "") -> TestClient:
    """
    Build a Starlette TestClient around the MCP ASGI app with the given API key.
    """
    app = mcp_server.build_asgi_app(api_key=api_key)
    return TestClient(app, raise_server_exceptions=False)


class TestMcpAuthDisabled:
    """When MCP_SERVER_API_KEY is empty, auth is disabled (local dev mode)."""

    def test_requests_pass_when_api_key_is_empty(self) -> None:
        # Given an MCP server with no API key configured (empty string)
        client = _build_test_client(api_key="")

        # When a request is made without an X-API-Key header
        response = client.get("/sse")

        # Then the request is not rejected with 401
        assert response.status_code != 401


class TestMcpAuthEnabled:
    """When MCP_SERVER_API_KEY is set, requests must include a valid key."""

    def test_requests_pass_with_correct_api_key(self) -> None:
        # Given an MCP server with an API key configured
        client = _build_test_client(api_key="secret-key-123")

        # When a request is made with the correct X-API-Key header
        response = client.get("/sse", headers={"X-API-Key": "secret-key-123"})

        # Then the request is not rejected with 401
        assert response.status_code != 401

    def test_requests_rejected_when_api_key_missing(self) -> None:
        # Given an MCP server with an API key configured
        client = _build_test_client(api_key="secret-key-123")

        # When a request is made without an X-API-Key header
        response = client.get("/sse")

        # Then the request is rejected with 401 Unauthorized
        assert response.status_code == 401

    def test_requests_rejected_when_api_key_wrong(self) -> None:
        # Given an MCP server with an API key configured
        client = _build_test_client(api_key="secret-key-123")

        # When a request is made with an incorrect X-API-Key header
        response = client.get("/sse", headers={"X-API-Key": "wrong-key"})

        # Then the request is rejected with 401 Unauthorized
        assert response.status_code == 401

    def test_rejection_response_body_contains_unauthorized(self) -> None:
        # Given an MCP server with an API key configured
        client = _build_test_client(api_key="secret-key-123")

        # When a request is made without an API key
        response = client.get("/sse")

        # Then the response body indicates unauthorized
        assert "unauthorized" in response.text.lower()
