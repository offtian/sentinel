"""
Unit tests for the LiteLLM proxy helper.

The helper exposes ``is_proxy_configured()`` and ``get_proxy_kwargs()``
so PydanticAI agent factories can plumb proxy ``base_url`` + virtual
key into their model construction without each factory duplicating
the conditional logic. Behaviour:

- Both env vars set -> kwargs returned with ``api_base`` + ``api_key``.
- Either env var unset -> empty kwargs (caller falls back to today's
  in-process LiteLLM SDK behaviour) plus a structured-log warning when
  the partial-config case is hit (operator forgot one of the two).
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


class TestIsProxyConfigured:
    def test_returns_true_when_base_url_and_virtual_key_set(self, monkeypatch):
        # Given both proxy fields populated
        _install_settings(
            monkeypatch,
            base_url=HttpUrl("http://proxy.local:4000"),
            virtual_key=SecretStr("sk-virtual-key"),
        )

        # When checking proxy configuration
        configured = litellm_proxy.is_proxy_configured()

        # Then it reports configured
        assert configured is True

    def test_returns_false_when_both_unset(self, monkeypatch):
        # Given no proxy fields set
        _install_settings(monkeypatch, base_url=None, virtual_key=None)

        # When checking proxy configuration
        configured = litellm_proxy.is_proxy_configured()

        # Then it reports unconfigured
        assert configured is False

    def test_returns_false_when_only_base_url_set(self, monkeypatch):
        # Given only the URL is set (operator forgot the virtual key)
        _install_settings(
            monkeypatch,
            base_url=HttpUrl("http://proxy.local:4000"),
            virtual_key=None,
        )

        # When checking proxy configuration
        configured = litellm_proxy.is_proxy_configured()

        # Then it falls back to unconfigured
        assert configured is False

    def test_returns_false_when_only_virtual_key_set(self, monkeypatch):
        # Given only the virtual key is set
        _install_settings(
            monkeypatch,
            base_url=None,
            virtual_key=SecretStr("sk-virtual-key"),
        )

        # When checking proxy configuration
        configured = litellm_proxy.is_proxy_configured()

        # Then it falls back to unconfigured
        assert configured is False


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

    def test_returns_empty_dict_when_unconfigured(self, monkeypatch):
        # Given the proxy fields are unset
        _install_settings(monkeypatch, base_url=None, virtual_key=None)

        # When the helper computes provider kwargs
        kwargs = litellm_proxy.get_proxy_kwargs()

        # Then no kwargs are returned (caller skips the proxy path)
        assert kwargs == {}

    def test_returns_empty_dict_when_only_base_url_set(self, monkeypatch):
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

        # Then it falls back to unconfigured AND emits the partial-config
        # structured event so the misconfiguration surfaces in startup logs
        assert kwargs == {}
        assert any(event == "litellm_proxy_partial_config" for event, _ in emitted_events)

    def test_returns_empty_dict_when_only_virtual_key_set(self, monkeypatch):
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

        # Then it falls back to unconfigured AND emits the partial-config event
        assert kwargs == {}
        assert any(event == "litellm_proxy_partial_config" for event, _ in emitted_events)
