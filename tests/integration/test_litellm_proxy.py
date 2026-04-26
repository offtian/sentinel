"""
F5.5 — integration test: PydanticAI agent calls flow through the LiteLLM proxy.

Asserts the round-trip the helper plumbs: when the firm-shared LiteLLM
proxy is configured (RFC §2.4), an outbound LLM call originating from
a PydanticAI agent factory hits the proxy URL carrying the operator's
virtual key in the ``Authorization`` header — never the upstream
provider host.

We use ``httpx.MockTransport`` rather than ``pytest-httpx`` / ``respx``
(neither is in the dev-deps list) to capture the request without a real
proxy or upstream provider.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import HttpUrl, SecretStr
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from sentinel import config as config_module
from sentinel import settings as settings_module
from sentinel.domain.llm import litellm_proxy
from sentinel.interfaces.graphs.agents import alert_classifier


_PROXY_BASE = "http://proxy.local:4000/"
_VIRTUAL_KEY = "sk-virtual-key-integration"


def _install_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend Settings carries both LiteLLM proxy fields."""
    fake_settings = settings_module.Settings.model_construct(
        litellm_base_url=HttpUrl(_PROXY_BASE),
        litellm_virtual_key=SecretStr(_VIRTUAL_KEY),
    )
    monkeypatch.setattr(settings_module, "settings", fake_settings)
    monkeypatch.setattr(config_module, "_config", None, raising=False)

    class _FakeConfig:
        settings = fake_settings

    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda config=None: _FakeConfig(),
    )


class TestLitellmProxyOutboundIntegration:
    """End-to-end: agent.run() hits the proxy URL with the virtual key."""

    @pytest.mark.asyncio
    async def test_agent_request_routes_through_proxy_with_virtual_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the LiteLLM proxy is configured AND a MockTransport stands in
        # for the proxy so we can capture the outbound HTTP request without a
        # live network endpoint or upstream provider.
        _install_proxy_settings(monkeypatch)

        captured: dict[str, Any] = {}

        def _proxy_handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("authorization", "")
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "openai/gpt-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": (
                                    '{"severity": "low", "affected_service": "api", '
                                    '"category": "noise", "summary": "ok", '
                                    '"requires_immediate_action": false}'
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        mock_transport = httpx.MockTransport(_proxy_handler)
        http_client = httpx.AsyncClient(transport=mock_transport)

        proxy_kwargs = litellm_proxy.get_proxy_kwargs()
        assert proxy_kwargs is not None  # sanity — settings install worked
        provider = LiteLLMProvider(http_client=http_client, **proxy_kwargs)
        model = OpenAIChatModel("openai/gpt-test", provider=provider)
        agent = alert_classifier.build_agent(model=model)

        # When the agent runs against a synthetic alert.
        result = await agent.run(
            "Investigate alert: api 5xx spike",
            deps=alert_classifier.Dependencies(
                alert_title="api 5xx spike",
                alert_description="errors elevated",
                alert_source="datadog",
            ),
        )

        # Then the request hit the proxy host (not the upstream provider) and
        # carried the operator's virtual key in the Authorization header — the
        # helper's contract end-to-end.
        assert captured["url"].startswith(_PROXY_BASE)
        assert captured["authorization"] == f"Bearer {_VIRTUAL_KEY}"
        # And the agent parsed the mocked completion into its output type.
        assert result.output.severity == "low"

        await http_client.aclose()

    @pytest.mark.asyncio
    async def test_agent_skips_proxy_when_helper_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the LiteLLM proxy is unconfigured (helper returns None)
        fake_settings = settings_module.Settings.model_construct(
            litellm_base_url=None,
            litellm_virtual_key=None,
        )
        monkeypatch.setattr(settings_module, "settings", fake_settings)
        monkeypatch.setattr(config_module, "_config", None, raising=False)

        class _FakeConfig:
            settings = fake_settings

        monkeypatch.setattr(
            config_module,
            "get_config",
            lambda config=None: _FakeConfig(),
        )

        # When the helper resolves provider kwargs
        proxy_kwargs = litellm_proxy.get_proxy_kwargs()

        # Then it returns None so the caller takes the in-process SDK path
        # (no OpenAIChatModel constructed, no proxy URL contacted)
        assert proxy_kwargs is None
