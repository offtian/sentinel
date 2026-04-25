"""
Unit tests for ``CommonConfiguration.build_mcp_toolsets()``.

Covers memoisation, thread-safety, graceful degrade on empty/malformed
input, and per-instance cache isolation.
"""

from __future__ import annotations

import concurrent.futures
from unittest import mock

from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio

from sentinel import settings as settings_mod
from sentinel.plugins.common import config as plugins_config_mod


def _make_settings(**overrides: object) -> mock.MagicMock:
    """Build a mock Settings with sensible defaults and optional overrides."""
    s = mock.MagicMock(spec=settings_mod.Settings)
    s.mcp_servers = ""
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _make_config(**overrides: object) -> plugins_config_mod.CommonConfiguration:
    """Build a CommonConfiguration with mock settings."""
    return plugins_config_mod.CommonConfiguration(settings=_make_settings(**overrides))


class TestBuildMcpToolsets:
    def test_returns_empty_tuple_when_mcp_servers_unset(self) -> None:
        # Given a Configuration with no MCP_SERVERS configured
        cfg = _make_config(mcp_servers="")

        # When build_mcp_toolsets is called
        result = cfg.build_mcp_toolsets()

        # Then an empty tuple is returned
        assert result == ()

    def test_returns_empty_tuple_when_mcp_servers_malformed_json(self) -> None:
        # Given a Configuration with malformed JSON in MCP_SERVERS
        cfg = _make_config(mcp_servers="not valid json{")

        # When build_mcp_toolsets is called
        result = cfg.build_mcp_toolsets()

        # Then an empty tuple is returned (graceful degrade)
        assert result == ()

    def test_builds_single_sse_server_from_url(self) -> None:
        # Given a Configuration with a single HTTP MCP server
        mcp_json = '[{"name": "datadog", "url": "http://localhost:9090/sse"}]'
        cfg = _make_config(mcp_servers=mcp_json)

        # When build_mcp_toolsets is called
        result = cfg.build_mcp_toolsets()

        # Then a single MCPServerSSE is returned
        assert len(result) == 1
        assert isinstance(result[0], MCPServerSSE)

    def test_builds_single_stdio_server_from_command(self) -> None:
        # Given a Configuration with a single stdio MCP server
        mcp_json = '[{"name": "confluence", "command": "npx", "args": ["-y", "@confluence/mcp"]}]'
        cfg = _make_config(mcp_servers=mcp_json)

        # When build_mcp_toolsets is called
        result = cfg.build_mcp_toolsets()

        # Then a single MCPServerStdio is returned
        assert len(result) == 1
        assert isinstance(result[0], MCPServerStdio)

    def test_builds_multiple_mixed_servers_in_declaration_order(self) -> None:
        # Given a Configuration with mixed HTTP and stdio servers
        mcp_json = (
            "["
            '{"name": "datadog", "url": "http://localhost:9090/sse"},'
            '{"name": "confluence", "command": "npx", "args": ["-y", "@confluence/mcp"]},'
            '{"name": "github", "url": "http://localhost:9091/sse"}'
            "]"
        )
        cfg = _make_config(mcp_servers=mcp_json)

        # When build_mcp_toolsets is called
        result = cfg.build_mcp_toolsets()

        # Then three toolsets are returned in declaration order
        assert len(result) == 3
        assert isinstance(result[0], MCPServerSSE)
        assert isinstance(result[1], MCPServerStdio)
        assert isinstance(result[2], MCPServerSSE)

    def test_memoises_result_across_calls(self) -> None:
        # Given a Configuration with an MCP server configured
        mcp_json = '[{"name": "datadog", "url": "http://localhost:9090/sse"}]'
        cfg = _make_config(mcp_servers=mcp_json)

        # When build_mcp_toolsets is called twice
        first_call = cfg.build_mcp_toolsets()
        second_call = cfg.build_mcp_toolsets()

        # Then the same object identity is returned (memoised)
        assert first_call is second_call

    def test_thread_safe_under_concurrent_first_call(self) -> None:
        # Given a Configuration with an MCP server configured
        mcp_json = '[{"name": "datadog", "url": "http://localhost:9090/sse"}]'
        cfg = _make_config(mcp_servers=mcp_json)

        # When build_mcp_toolsets is called concurrently from 8 threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(cfg.build_mcp_toolsets) for _ in range(8)]
            results = [f.result() for f in futures]

        # Then all threads receive the same identity-equal tuple
        assert all(r is results[0] for r in results)

    def test_two_configurations_have_independent_caches(self) -> None:
        # Given two Configuration instances with different MCP server configs
        first_json = '[{"name": "datadog", "url": "http://localhost:9090/sse"}]'
        second_json = '[{"name": "github", "url": "http://localhost:9091/sse"}]'

        first_cfg = _make_config(mcp_servers=first_json)
        second_cfg = _make_config(mcp_servers=second_json)

        # When each builds its toolsets
        first_result = first_cfg.build_mcp_toolsets()
        second_result = second_cfg.build_mcp_toolsets()

        # Then the caches are independent (different identity)
        assert first_result is not second_result
        assert len(first_result) == 1
        assert len(second_result) == 1
