"""
Unit tests for ``CommonConfiguration._build_agent_model``.

The proxy-aware Model construction was lifted off
``interfaces/graphs/agents/utils.resolve_agent_model`` onto
``CommonConfiguration`` so the interfaces layer no longer imports
LiteLLM provider machinery (per ``application.md``: vendor adapters,
agents, and infra clients live on ``CommonConfiguration``).

Each case asserts the helper behaves identically to its predecessor:

* ``None`` / ``"test"`` -> passthrough (preserves placeholder paths
  unit tests rely on).
* Proxy unconfigured -> string passthrough so PydanticAI follows its
  in-process LiteLLM SDK route.
* Proxy configured -> ``OpenAIChatModel`` whose ``LiteLLMProvider``
  points at the proxy URL with the operator's virtual key, with the
  ``litellm:`` prefix stripped to avoid double-prefixing.

The end-to-end class re-asserts proxy routing through the public
``load_agents`` -> ``agent_for`` path so the helper's call-site wiring
is exercised together with the per-factory plumbing.
"""

from __future__ import annotations

from unittest import mock

import pytest
from pydantic_ai.models.openai import OpenAIChatModel

from sentinel import config as base_config_mod
from sentinel import settings as settings_mod
from sentinel.domain.llm import litellm_proxy
from sentinel.interfaces.graphs import agents as agents_mod
from sentinel.plugins.common import config as plugins_config_mod


_PROXY_KWARGS = {"api_base": "http://proxy.local:4000/", "api_key": "sk-virtual-key"}


def _enable_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the firm-shared LiteLLM proxy is configured."""
    monkeypatch.setattr(litellm_proxy, "is_proxy_configured", lambda: True)
    monkeypatch.setattr(litellm_proxy, "get_proxy_kwargs", lambda: dict(_PROXY_KWARGS))


def _disable_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the proxy is unconfigured (local-dev fallback path)."""
    monkeypatch.setattr(litellm_proxy, "is_proxy_configured", lambda: False)
    monkeypatch.setattr(litellm_proxy, "get_proxy_kwargs", dict)


def _make_settings() -> mock.MagicMock:
    """
    Build a Settings mock with the fields ``CommonConfiguration`` model
    properties read off so the singleton's local ``.env`` parsing is
    bypassed (the project's local ``.env`` ships with empty ``HttpUrl``
    fields that fail Pydantic validation in unit-test environments).
    """
    s = mock.MagicMock(spec=settings_mod.Settings)
    s.alert_classifier_llm = "openai/gpt-test"
    s.root_cause_llm = "openai/gpt-test"
    s.ticket_reviewer_llm = "openai/gpt-test"
    s.response_drafter_llm = "openai/gpt-test"
    s.k8s_investigator_llm = "openai/gpt-test"
    s.intent_router_llm = "openai/gpt-test"
    s.k8s_chart_parser_llm = "openai/gpt-test"
    s.k8s_chart_generator_llm = "openai/gpt-test"
    s.team_profile = "sre"
    return s


def _make_config() -> plugins_config_mod.CommonConfiguration:
    """Build a CommonConfiguration for direct method invocation in tests."""
    return plugins_config_mod.CommonConfiguration(settings=_make_settings())


def _underlying(agent: object) -> object:
    """Return the agent's model, unwrapping the replay-capture wrapper if present."""
    model = agent.model
    return getattr(model, "wrapped", model)


class TestBuildAgentModel:
    def test_returns_input_string_when_proxy_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a CommonConfiguration AND the proxy is unconfigured
        _disable_proxy(monkeypatch)
        cfg = _make_config()

        # When the helper resolves a real model identifier
        resolved = cfg._build_agent_model("litellm:openai/gpt-4.1-mini")

        # Then the original string passes through unchanged so PydanticAI
        # follows its existing in-process LiteLLM path
        assert resolved == "litellm:openai/gpt-4.1-mini"

    def test_returns_test_placeholder_unchanged_when_proxy_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is unconfigured AND the placeholder "test" model
        _disable_proxy(monkeypatch)
        cfg = _make_config()

        # When the helper resolves the test placeholder
        resolved = cfg._build_agent_model("test")

        # Then "test" is preserved so unit tests that monkey-patch .run
        # don't need a real provider
        assert resolved == "test"

    def test_returns_openai_chat_model_pointed_at_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a CommonConfiguration AND the proxy is configured
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When the helper resolves a real model identifier
        resolved = cfg._build_agent_model("litellm:openai/gpt-4.1-mini")

        # Then an OpenAIChatModel pointing at the proxy URL is returned
        assert isinstance(resolved, OpenAIChatModel)
        assert resolved.base_url == _PROXY_KWARGS["api_base"]
        # The litellm: prefix is stripped because the LiteLLMProvider already
        # sets system="litellm" — passing it through would double-prefix.
        assert resolved.model_name == "openai/gpt-4.1-mini"
        assert resolved.system == "litellm"

    def test_strips_litellm_prefix_when_proxy_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is configured AND a bare model id (no prefix)
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When the helper resolves the bare model identifier
        resolved = cfg._build_agent_model("openai/gpt-4.1-mini")

        # Then the bare name is used as-is on the OpenAIChatModel
        assert isinstance(resolved, OpenAIChatModel)
        assert resolved.model_name == "openai/gpt-4.1-mini"

    def test_returns_test_placeholder_unchanged_even_when_proxy_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is configured AND the placeholder "test" model
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When the helper resolves the placeholder
        resolved = cfg._build_agent_model("test")

        # Then "test" passes through (unit-test fixtures still want the
        # placeholder, not a real proxy connection)
        assert resolved == "test"

    def test_passes_through_none_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured AND a None model (factory default)
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When the helper resolves None
        resolved = cfg._build_agent_model(None)

        # Then None is returned so the factory's "test" fallback still fires
        assert resolved is None


class TestLoadAgentsProxyRouting:
    """End-to-end: each foundation factory's agent routes through the proxy."""

    def test_alert_classifier_uses_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a fresh CommonConfiguration AND the proxy is configured
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When agents are loaded with no skills
        with mock.patch.object(base_config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents_mod)

        # Then the classifier's underlying model is an OpenAIChatModel
        # pointed at the proxy URL
        underlying = _underlying(cfg.agent_for("alert_classifier"))
        assert isinstance(underlying, OpenAIChatModel)
        assert underlying.base_url == _PROXY_KWARGS["api_base"]

    def test_root_cause_analyser_uses_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a CommonConfiguration AND the proxy is configured
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When agents are loaded
        with mock.patch.object(base_config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents_mod)

        # Then the analyser is wired to the proxy
        underlying = _underlying(cfg.agent_for("root_cause_analyser"))
        assert isinstance(underlying, OpenAIChatModel)
        assert underlying.base_url == _PROXY_KWARGS["api_base"]

    def test_k8s_investigator_uses_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a CommonConfiguration AND the proxy is configured
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When agents are loaded
        with mock.patch.object(base_config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents_mod)

        # Then the K8s investigator is wired to the proxy
        underlying = _underlying(cfg.agent_for("k8s_investigator"))
        assert isinstance(underlying, OpenAIChatModel)
        assert underlying.base_url == _PROXY_KWARGS["api_base"]

    def test_ticket_reviewer_uses_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a CommonConfiguration AND the proxy is configured
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When agents are loaded
        with mock.patch.object(base_config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents_mod)

        # Then the ticket reviewer is wired to the proxy
        underlying = _underlying(cfg.agent_for("ticket_reviewer"))
        assert isinstance(underlying, OpenAIChatModel)
        assert underlying.base_url == _PROXY_KWARGS["api_base"]

    def test_response_drafter_uses_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a CommonConfiguration AND the proxy is configured
        _enable_proxy(monkeypatch)
        cfg = _make_config()

        # When agents are loaded
        with mock.patch.object(base_config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents_mod)

        # Then the response drafter is wired to the proxy
        underlying = _underlying(cfg.agent_for("response_drafter"))
        assert isinstance(underlying, OpenAIChatModel)
        assert underlying.base_url == _PROXY_KWARGS["api_base"]
