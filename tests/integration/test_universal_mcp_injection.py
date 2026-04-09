"""
Integration tests for universal MCP injection.

Covers:
- K8s investigation adapter mounts shared MCP servers exactly once
  (no double-mount when both MCP_SERVERS and K8S_MCP_SERVER_URL are set).
- build_mcp_toolsets is the single source of truth used by
  build_k8s_investigation_adapter.
"""

from __future__ import annotations

from unittest import mock

from pydantic_ai.mcp import MCPServerSSE

from sentinel import settings as settings_mod
from sentinel.plugins import config as plugins_config_mod


def _make_settings(**overrides: object) -> mock.MagicMock:
    """Build a mock Settings with sensible defaults and optional overrides."""
    s = mock.MagicMock(spec=settings_mod.Settings)
    s.mcp_servers = ""
    s.k8s_investigation_backend = ""
    s.k8s_mcp_server_url = ""
    s.k8s_investigator_llm = "openai/gpt-4.1"
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _make_config(**overrides: object) -> plugins_config_mod.CommonConfiguration:
    """Build a CommonConfiguration with mock settings."""
    return plugins_config_mod.CommonConfiguration(settings=_make_settings(**overrides))


def _stub_agent_runner(*args: object, **kwargs: object) -> None:
    """No-op agent runner for tests that don't exercise agent execution."""


class TestK8sNoDoubleMountRegression:
    def test_shared_mcp_server_appears_exactly_once_in_k8s_adapter(self) -> None:
        # Given settings with both MCP_SERVERS and K8S_MCP_SERVER_URL
        cfg = _make_config(
            mcp_servers='[{"name": "datadog", "url": "http://localhost:9090/sse"}]',
            k8s_investigation_backend="native",
            k8s_mcp_server_url="http://localhost:9091/sse",
        )

        # When building the K8s adapter
        adapter = cfg.build_k8s_investigation_adapter(agent_runner=_stub_agent_runner)

        # Then the shared MCP server (datadog) appears exactly once
        assert adapter is not None
        sse_urls = [t.url for t in adapter._mcp_toolsets if isinstance(t, MCPServerSSE)]
        datadog_urls = [u for u in sse_urls if "9090" in str(u)]
        assert len(datadog_urls) == 1

        # And the K8s-specific MCP server is also present
        k8s_urls = [u for u in sse_urls if "9091" in str(u)]
        assert len(k8s_urls) == 1

        # And total count is 2 (one shared + one K8s-specific)
        assert len(adapter._mcp_toolsets) == 2

    def test_build_k8s_adapter_uses_memoised_build_mcp_toolsets(self) -> None:
        # Given settings with MCP_SERVERS configured
        cfg = _make_config(
            mcp_servers='[{"name": "datadog", "url": "http://localhost:9090/sse"}]',
            k8s_investigation_backend="native",
        )

        # When building the K8s adapter and separately calling build_mcp_toolsets
        adapter = cfg.build_k8s_investigation_adapter(agent_runner=_stub_agent_runner)
        shared_toolsets = cfg.build_mcp_toolsets()

        # Then the adapter's first toolset is the same identity as the shared tuple's first element
        assert adapter is not None
        assert adapter._mcp_toolsets[0] is shared_toolsets[0]
