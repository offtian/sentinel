"""
Unit tests for LiteLLM proxy routing across PydanticAI agent factories.

Each of the 5 foundation agent factories (alert_classifier,
root_cause_analyser, k8s_investigator, ticket_reviewer,
response_drafter) must, when the helper reports the proxy is
configured, construct its agent against an ``OpenAIChatModel`` whose
provider points at the firm-shared LiteLLM proxy URL with the operator's
virtual key. When unconfigured, the existing string-model path stands
unchanged so ``just run-api`` keeps working without a proxy.

Plumbing is centralised in
``sentinel.interfaces.graphs.agents.utils.resolve_agent_model`` so each
factory's diff is a single call swap; this test module verifies both
the helper and that each factory wires it in.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from sentinel.domain.llm import litellm_proxy
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    k8s_investigator,
    response_drafter,
    root_cause_analyser,
    ticket_reviewer,
)
from sentinel.interfaces.graphs.agents import utils as agents_utils


_PROXY_KWARGS = {"api_base": "http://proxy.local:4000/", "api_key": "sk-virtual-key"}


def _enable_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the firm-shared LiteLLM proxy is configured."""
    monkeypatch.setattr(litellm_proxy, "is_proxy_configured", lambda: True)
    monkeypatch.setattr(litellm_proxy, "get_proxy_kwargs", lambda: dict(_PROXY_KWARGS))


def _disable_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the proxy is unconfigured (local-dev fallback path)."""
    monkeypatch.setattr(litellm_proxy, "is_proxy_configured", lambda: False)
    monkeypatch.setattr(litellm_proxy, "get_proxy_kwargs", dict)


def _agent_model(agent: Agent[Any, Any]) -> Any:
    """Return the underlying model object the Agent was built against."""
    return agent.model


class TestResolveAgentModel:
    def test_returns_input_string_when_proxy_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is unconfigured (local-dev fallback)
        _disable_proxy(monkeypatch)

        # When resolving the model identifier
        resolved = agents_utils.resolve_agent_model("litellm:openai/gpt-4.1-mini")

        # Then the original string passes through unchanged so PydanticAI
        # follows its existing in-process LiteLLM path
        assert resolved == "litellm:openai/gpt-4.1-mini"

    def test_returns_test_placeholder_unchanged_when_proxy_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is unconfigured AND the placeholder "test" model
        _disable_proxy(monkeypatch)

        # When resolving the test placeholder
        resolved = agents_utils.resolve_agent_model("test")

        # Then "test" is preserved so unit tests that monkey-patch .run
        # don't need a real provider
        assert resolved == "test"

    def test_returns_openai_chat_model_pointed_at_proxy_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is configured
        _enable_proxy(monkeypatch)

        # When resolving the model identifier
        resolved = agents_utils.resolve_agent_model("litellm:openai/gpt-4.1-mini")

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

        # When resolving the bare model identifier
        resolved = agents_utils.resolve_agent_model("openai/gpt-4.1-mini")

        # Then the bare name is used as-is on the OpenAIChatModel
        assert isinstance(resolved, OpenAIChatModel)
        assert resolved.model_name == "openai/gpt-4.1-mini"

    def test_returns_test_placeholder_unchanged_even_when_proxy_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is configured AND the placeholder "test" model
        _enable_proxy(monkeypatch)

        # When resolving the placeholder
        resolved = agents_utils.resolve_agent_model("test")

        # Then "test" passes through (unit-test fixtures still want the
        # placeholder, not a real proxy connection)
        assert resolved == "test"

    def test_passes_through_none_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured AND a None model (factory default)
        _enable_proxy(monkeypatch)

        # When resolving None
        resolved = agents_utils.resolve_agent_model(None)

        # Then None is returned so the factory's "test" fallback still fires
        assert resolved is None


class TestAlertClassifierProxyRouting:
    def test_uses_proxy_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured
        _enable_proxy(monkeypatch)

        # When the alert classifier agent is built with a real model id
        agent = alert_classifier.build_agent(model="litellm:openai/gpt-4.1-mini")

        # Then the agent is wired to the proxy via OpenAIChatModel
        model = _agent_model(agent)
        assert isinstance(model, OpenAIChatModel)
        assert model.base_url == _PROXY_KWARGS["api_base"]

    def test_falls_back_to_placeholder_when_proxy_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given the proxy is unconfigured AND the placeholder "test" model
        # so the Agent constructor doesn't require provider credentials
        _disable_proxy(monkeypatch)

        # When the alert classifier agent is built
        agent = alert_classifier.build_agent(model="test")

        # Then the agent is NOT wired to a proxied OpenAIChatModel — the
        # in-process path stands and PydanticAI keeps the placeholder
        # behaviour unit tests rely on
        model = _agent_model(agent)
        assert not isinstance(model, OpenAIChatModel)


class TestRootCauseAnalyserProxyRouting:
    def test_uses_proxy_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured
        _enable_proxy(monkeypatch)

        # When the root cause analyser agent is built
        agent = root_cause_analyser.build_agent(model="litellm:openai/gpt-4.1")

        # Then the agent is wired to the proxy
        model = _agent_model(agent)
        assert isinstance(model, OpenAIChatModel)
        assert model.base_url == _PROXY_KWARGS["api_base"]


class TestK8sInvestigatorProxyRouting:
    def test_uses_proxy_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured
        _enable_proxy(monkeypatch)

        # When the K8s investigator agent is built
        agent = k8s_investigator.build_agent(model="litellm:openai/gpt-4.1")

        # Then the agent is wired to the proxy
        model = _agent_model(agent)
        assert isinstance(model, OpenAIChatModel)
        assert model.base_url == _PROXY_KWARGS["api_base"]


class TestTicketReviewerProxyRouting:
    def test_uses_proxy_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured
        _enable_proxy(monkeypatch)

        # When the ticket reviewer agent is built
        agent = ticket_reviewer.build_agent(model="litellm:openai/gpt-4.1-mini")

        # Then the agent is wired to the proxy
        model = _agent_model(agent)
        assert isinstance(model, OpenAIChatModel)
        assert model.base_url == _PROXY_KWARGS["api_base"]


class TestResponseDrafterProxyRouting:
    def test_uses_proxy_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the proxy is configured
        _enable_proxy(monkeypatch)

        # When the response drafter agent is built
        agent = response_drafter.build_agent(model="litellm:openai/gpt-4.1")

        # Then the agent is wired to the proxy
        model = _agent_model(agent)
        assert isinstance(model, OpenAIChatModel)
        assert model.base_url == _PROXY_KWARGS["api_base"]
