"""
MCP client toolset builder for PydanticAI agents.

Parses ``MCP_SERVERS`` config and returns PydanticAI-compatible
MCP toolsets that can be injected at ``agent.run(toolsets=[...])``.
"""

from __future__ import annotations

import json
from typing import Any

import attrs

from sentinel.utils import logs


logger = logs.get_logger()


@attrs.frozen
class McpServerConfig:
    """Parsed MCP server configuration."""

    name: str
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()


def parse_mcp_server_configs(*, config_json: str) -> tuple[McpServerConfig, ...]:
    """
    Parse the ``MCP_SERVERS`` env var into server configs.

    Accepts a JSON list of objects with either:
    - ``{"name": "...", "url": "..."}`` for HTTP servers
    - ``{"name": "...", "command": "...", "args": [...]}`` for stdio servers

    :param config_json: JSON string from the MCP_SERVERS env var.
    :returns: Tuple of parsed server configs.
    """
    if not config_json.strip():
        return ()

    try:
        servers = json.loads(config_json)
    except json.JSONDecodeError:
        logger.warning("Invalid MCP_SERVERS JSON, ignoring", config=config_json[:100])
        return ()

    configs: list[McpServerConfig] = []
    for server in servers:
        name = server.get("name", "unnamed")
        url = server.get("url")
        command = server.get("command")
        args = tuple(server.get("args", []))
        configs.append(McpServerConfig(name=name, url=url, command=command, args=args))

    return tuple(configs)


def build_mcp_toolsets(*, config_json: str) -> tuple[Any, ...]:
    """
    Build PydanticAI-compatible MCP toolsets from config.

    Return a tuple of ``MCPServerHTTP`` or ``MCPServerStdio`` instances.

    :param config_json: JSON string from the MCP_SERVERS env var.
    :returns: Tuple of MCP toolset instances.
    """
    configs = parse_mcp_server_configs(config_json=config_json)
    if not configs:
        return ()

    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio

    toolsets: list[Any] = []
    for config in configs:
        if config.url:
            toolsets.append(MCPServerSSE(url=config.url))
            logger.info("MCP HTTP client configured", name=config.name, url=config.url)
        elif config.command:
            toolsets.append(MCPServerStdio(config.command, args=list(config.args)))
            logger.info("MCP stdio client configured", name=config.name, command=config.command)

    return tuple(toolsets)
