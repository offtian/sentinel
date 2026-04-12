"""
Unit tests for ``CommonConfiguration._build_holmes_sdk_toolsets()``.

Covers the explicit per-toolset wiring of HolmesGPT built-in Confluence
and Notion toolsets.  Each toolset is included only when its required
settings are populated (no-op pattern).
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from holmes.core import tools as holmes_tools_mod

from sentinel import settings as settings_mod
from sentinel.plugins import config as plugins_config_mod


def _make_settings(**overrides: object) -> mock.MagicMock:
    """Build a mock Settings with sensible defaults and optional overrides."""
    s = mock.MagicMock(spec=settings_mod.Settings)
    s.holmesgpt_enabled = True
    s.holmes_backend = "sdk"
    s.holmes_sdk_model = "openai/gpt-4.1"
    s.ollama_base_url = "http://localhost:11434"
    s.confluence_base_url = ""
    s.jira_user_email = ""
    s.jira_api_token = ""
    s.notion_token = ""
    s.observability_backend = ""
    s.is_local = False
    s.mcp_servers = ""
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _make_config(**overrides: object) -> plugins_config_mod.CommonConfiguration:
    """Build a CommonConfiguration with mock settings."""
    return plugins_config_mod.CommonConfiguration(settings=_make_settings(**overrides))


def _stub_check_prerequisites(self: Any, *, silent: bool = False) -> None:
    self.status = holmes_tools_mod.ToolsetStatusEnum.ENABLED


class TestBuildHolmesSdkToolsets:
    def test_returns_empty_tuple_when_no_settings_configured(self) -> None:
        # Given a Configuration with no Holmes toolset settings populated
        cfg = _make_config()

        # When _build_holmes_sdk_toolsets is called
        result = cfg._build_holmes_sdk_toolsets()

        # Then an empty tuple is returned
        assert result == ()

    def test_returns_empty_tuple_when_holmes_sdk_not_installed(self) -> None:
        # Given a Configuration with toolset settings populated but the SDK flagged unavailable
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net",
            jira_user_email="alice@acme.com",
            jira_api_token="token-abc",  # noqa: S106
            notion_token="secret-xyz",  # noqa: S106
        )

        # When _build_holmes_sdk_toolsets is called with the SDK availability flag forced off
        with mock.patch.object(plugins_config_mod, "_HOLMES_SDK_AVAILABLE", new=False):
            result = cfg._build_holmes_sdk_toolsets()

        # Then an empty tuple is returned regardless of populated settings
        assert result == ()

    def test_includes_confluence_when_all_three_settings_set(self) -> None:
        # Given a Configuration with confluence_base_url + jira_user_email + jira_api_token
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net",
            jira_user_email="alice@acme.com",
            jira_api_token="token-abc",  # noqa: S106
        )

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then exactly one toolset named "confluence" is returned
        assert len(result) == 1
        assert result[0].name == "confluence"

    def test_skips_confluence_when_user_email_missing(self) -> None:
        # Given a Configuration missing jira_user_email
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net",
            jira_user_email="",
            jira_api_token="token-abc",  # noqa: S106
        )

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then confluence is not in the result
        assert all(ts.name != "confluence" for ts in result)

    def test_skips_confluence_when_api_key_missing(self) -> None:
        # Given a Configuration missing jira_api_token
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net",
            jira_user_email="alice@acme.com",
            jira_api_token="",
        )

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then confluence is not in the result
        assert all(ts.name != "confluence" for ts in result)

    def test_strips_wiki_suffix_from_confluence_base_url(self) -> None:
        # Given a Configuration whose confluence_base_url ends in /wiki
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net/wiki",
            jira_user_email="alice@acme.com",
            jira_api_token="token-abc",  # noqa: S106
        )

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then the matched confluence toolset's api_url has the /wiki suffix stripped
        confluence_toolset = next(ts for ts in result if ts.name == "confluence")
        assert confluence_toolset.config["api_url"] == "https://acme.atlassian.net"

    def test_strips_wiki_suffix_with_trailing_slash(self) -> None:
        # Given a Configuration whose confluence_base_url ends in /wiki/
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net/wiki/",
            jira_user_email="alice@acme.com",
            jira_api_token="token-abc",  # noqa: S106
        )

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then the trailing slash and /wiki are both stripped
        confluence_toolset = next(ts for ts in result if ts.name == "confluence")
        assert confluence_toolset.config["api_url"] == "https://acme.atlassian.net"

    def test_includes_notion_when_token_set(self) -> None:
        # Given a Configuration with only notion_token populated
        cfg = _make_config(notion_token="secret-xyz")  # noqa: S106

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then exactly one toolset named "notion" is returned with a bearer auth header
        assert len(result) == 1
        assert result[0].name == "notion"
        assert result[0].config["additional_headers"]["Authorization"] == "Bearer secret-xyz"

    def test_includes_both_confluence_and_notion_when_both_configured(self) -> None:
        # Given a Configuration with both confluence and notion settings populated
        cfg = _make_config(
            confluence_base_url="https://acme.atlassian.net",
            jira_user_email="alice@acme.com",
            jira_api_token="token-abc",  # noqa: S106
            notion_token="secret-xyz",  # noqa: S106
        )

        # When _build_holmes_sdk_toolsets is called with stubbed prerequisites
        with mock.patch.object(
            holmes_tools_mod.Toolset,
            "check_prerequisites",
            _stub_check_prerequisites,
        ):
            result = cfg._build_holmes_sdk_toolsets()

        # Then both confluence and notion are present (order-agnostic)
        assert {ts.name for ts in result} == {"confluence", "notion"}
