"""
Unit tests for the LiteLLM proxy helper.

The helper exposes a single ``get_proxy_kwargs()`` returning either the
provider kwargs ``{"api_base", "api_key"}`` (proxy configured) or
``None`` (in-process SDK fallback). Callers that only need the boolean
"configured?" answer compare the result against ``None`` so settings
are read exactly once per call. Partial configs (one of the two fields
set) emit a structured-log warning and fail safe to ``None`` rather
than sending unauthenticated traffic to a half-wired proxy.
"""

from __future__ import annotations

from pydantic import HttpUrl, SecretStr

from sentinel import config as config_module
from sentinel import settings as settings_module
from sentinel.domain.llm import litellm_proxy


def _build_settings(
    *,
    base_url: HttpUrl | None,
    virtual_key: SecretStr | None,
) -> settings_module.Settings:
    """Build a Settings instance with proxy fields overridden.

    Uses ``model_construct`` to bypass env-file ingestion entirely —
    the helper only reads ``litellm_base_url`` and ``litellm_virtual_key``
    so the rest of the Settings shape is irrelevant for these tests, and
    sidestepping validation keeps the fixture independent of whatever
    the local-dev ``.env`` happens to contain.
    """
    return settings_module.Settings.model_construct(
        litellm_base_url=base_url,
        litellm_virtual_key=virtual_key,
    )


def _install_settings(
    monkeypatch,
    *,
    base_url: HttpUrl | None,
    virtual_key: SecretStr | None,
) -> None:
    """Install a Settings instance behind the cached config singleton."""
    fake_settings = _build_settings(base_url=base_url, virtual_key=virtual_key)
    monkeypatch.setattr(settings_module, "settings", fake_settings)
    # Bypass the config singleton — the helper only needs settings, not a
    # fully-loaded CommonConfiguration with vendor adapters.
    monkeypatch.setattr(config_module, "_config", None, raising=False)

    class _FakeConfig:
        settings = fake_settings

    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda config=None: _FakeConfig(),
    )


class TestGetProxyKwargs:
    def test_returns_api_base_and_api_key_when_configured(self, monkeypatch):
        # Given both proxy fields populated
        _install_settings(
            monkeypatch,
            base_url=HttpUrl("http://proxy.local:4000"),
            virtual_key=SecretStr("sk-virtual-key"),
        )

        # When the helper computes provider kwargs
        kwargs = litellm_proxy.get_proxy_kwargs()

        # Then both api_base + api_key are populated for LiteLLMProvider
        assert kwargs == {
            "api_base": "http://proxy.local:4000/",
            "api_key": "sk-virtual-key",
        }

    def test_returns_none_when_unconfigured(self, monkeypatch):
        # Given the proxy fields are unset
        _install_settings(monkeypatch, base_url=None, virtual_key=None)

        # When the helper computes provider kwargs
        kwargs = litellm_proxy.get_proxy_kwargs()

        # Then None signals the caller to take the in-process SDK path
        assert kwargs is None

    def test_returns_none_when_only_base_url_set(self, monkeypatch):
        # Given a partial proxy config (URL but no key)
        _install_settings(
            monkeypatch,
            base_url=HttpUrl("http://proxy.local:4000"),
            virtual_key=None,
        )
        emitted_events: list[tuple[str, dict[str, object] | None]] = []

        def _capture_event(event, *, params=None):
            emitted_events.append((event, params))

        monkeypatch.setattr(litellm_proxy.logs, "log_event", _capture_event)

        # When the helper computes provider kwargs
        kwargs = litellm_proxy.get_proxy_kwargs()

        # Then it falls back to None AND emits the partial-config
        # structured event so the misconfiguration surfaces in startup logs
        assert kwargs is None
        assert any(event == "litellm_proxy_partial_config" for event, _ in emitted_events)

    def test_returns_none_when_only_virtual_key_set(self, monkeypatch):
        # Given a partial proxy config (key but no URL)
        _install_settings(
            monkeypatch,
            base_url=None,
            virtual_key=SecretStr("sk-virtual-key"),
        )
        emitted_events: list[tuple[str, dict[str, object] | None]] = []

        def _capture_event(event, *, params=None):
            emitted_events.append((event, params))

        monkeypatch.setattr(litellm_proxy.logs, "log_event", _capture_event)

        # When the helper computes provider kwargs
        kwargs = litellm_proxy.get_proxy_kwargs()

        # Then it falls back to None AND emits the partial-config event
        assert kwargs is None
        assert any(event == "litellm_proxy_partial_config" for event, _ in emitted_events)

    def test_does_not_emit_partial_config_event_when_both_unset(self, monkeypatch):
        # Given neither field is set (clean local-dev fallback)
        _install_settings(monkeypatch, base_url=None, virtual_key=None)
        emitted_events: list[tuple[str, dict[str, object] | None]] = []

        def _capture_event(event, *, params=None):
            emitted_events.append((event, params))

        monkeypatch.setattr(litellm_proxy.logs, "log_event", _capture_event)

        # When the helper computes provider kwargs
        litellm_proxy.get_proxy_kwargs()

        # Then no warning fires — fully-unset is the documented dev default,
        # not a misconfiguration
        assert all(event != "litellm_proxy_partial_config" for event, _ in emitted_events)
