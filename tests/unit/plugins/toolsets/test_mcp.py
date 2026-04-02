from __future__ import annotations

import json

from sentinel.plugins.toolsets import mcp as mcp_toolsets


class TestParseMcpServerConfigs:
    def test_parses_empty_string_to_empty_tuple(self) -> None:
        # Given an empty config string

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config_json="")

        # Then no servers are returned
        assert result == ()

    def test_parses_whitespace_only_to_empty_tuple(self) -> None:
        # Given whitespace-only config

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config_json="   ")

        # Then no servers are returned
        assert result == ()

    def test_parses_invalid_json_to_empty_tuple(self) -> None:
        # Given invalid JSON

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config_json="not json")

        # Then no servers are returned (logged warning)
        assert result == ()

    def test_parses_http_server_config(self) -> None:
        # Given a JSON config with an HTTP server
        config = json.dumps([{"name": "kubectl", "url": "http://localhost:9000/mcp"}])

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config_json=config)

        # Then one HTTP server config is returned
        assert len(result) == 1
        assert result[0].name == "kubectl"
        assert result[0].url == "http://localhost:9000/mcp"
        assert result[0].command is None

    def test_parses_stdio_server_config(self) -> None:
        # Given a JSON config with a stdio server
        config = json.dumps(
            [
                {
                    "name": "kubectl-mcp",
                    "command": "kubectl-mcp-server",
                    "args": ["--namespace", "default"],
                }
            ]
        )

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config_json=config)

        # Then the stdio config is parsed
        assert len(result) == 1
        assert result[0].name == "kubectl-mcp"
        assert result[0].command == "kubectl-mcp-server"
        assert result[0].args == ("--namespace", "default")
        assert result[0].url is None

    def test_parses_multiple_servers(self) -> None:
        # Given a JSON config with multiple servers
        config = json.dumps(
            [
                {"name": "obs", "url": "http://localhost:9000"},
                {"name": "k8s", "command": "kubectl-mcp", "args": []},
            ]
        )

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config_json=config)

        # Then both are parsed
        assert len(result) == 2
        assert result[0].name == "obs"
        assert result[1].name == "k8s"
