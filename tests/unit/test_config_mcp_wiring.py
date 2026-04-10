from __future__ import annotations

from unittest import mock

from sentinel import settings
from sentinel.plugins import config as plugin_config_mod


class TestBuildK8sInvestigationAdapterMcpWiring:
    def test_injects_mcp_toolsets_from_mcp_servers_setting(self) -> None:
        # Given settings with an MCP server configured
        test_settings = mock.MagicMock(spec=settings.Settings)
        test_settings.k8s_investigation_backend = "native"
        test_settings.k8s_investigator_llm = "openai/gpt-4.1"
        test_settings.mcp_servers = '[{"name": "kubectl", "url": "http://localhost:9090"}]'
        test_settings.k8s_mcp_server_url = ""

        cfg = plugin_config_mod.PluginConfiguration(settings=test_settings)
        stub_runner = mock.AsyncMock()

        # When building the K8s adapter
        adapter = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)

        # Then MCP toolsets are injected
        assert adapter is not None
        assert len(adapter._mcp_toolsets) == 1

    def test_adds_k8s_mcp_server_url_as_additional_toolset(self) -> None:
        # Given settings with both MCP_SERVERS and K8S_MCP_SERVER_URL
        test_settings = mock.MagicMock(spec=settings.Settings)
        test_settings.k8s_investigation_backend = "native"
        test_settings.k8s_investigator_llm = "openai/gpt-4.1"
        test_settings.mcp_servers = '[{"name": "kubectl", "url": "http://localhost:9090"}]'
        test_settings.k8s_mcp_server_url = "http://localhost:9091"

        cfg = plugin_config_mod.PluginConfiguration(settings=test_settings)
        stub_runner = mock.AsyncMock()

        # When building the K8s adapter
        adapter = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)

        # Then both MCP toolsets are injected
        assert adapter is not None
        assert len(adapter._mcp_toolsets) == 2

    def test_no_mcp_toolsets_when_settings_empty(self) -> None:
        # Given settings with no MCP servers
        test_settings = mock.MagicMock(spec=settings.Settings)
        test_settings.k8s_investigation_backend = "native"
        test_settings.k8s_investigator_llm = "openai/gpt-4.1"
        test_settings.mcp_servers = ""
        test_settings.k8s_mcp_server_url = ""

        cfg = plugin_config_mod.PluginConfiguration(settings=test_settings)
        stub_runner = mock.AsyncMock()

        # When building the K8s adapter
        adapter = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)

        # Then no MCP toolsets are injected
        assert adapter is not None
        assert len(adapter._mcp_toolsets) == 0
