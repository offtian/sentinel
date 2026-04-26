from __future__ import annotations

from unittest import mock

from sentinel import bootstrap


def _fake_settings(
    *,
    base_url: str | None,
    virtual_key: str | None,
) -> mock.MagicMock:
    """
    Build a Settings stand-in with the two LiteLLM proxy fields populated.

    Bootstrap reads ``settings.litellm_base_url`` and
    ``settings.litellm_virtual_key`` directly (it must not import
    ``domain.llm.litellm_proxy`` per the import-linter layering contract),
    so tests stub the singleton rather than the helper.
    """
    fake = mock.MagicMock()
    fake.litellm_base_url = base_url
    fake.litellm_virtual_key = virtual_key
    return fake


class TestInitialiseLitellmProxyLogging:
    def setup_method(self):
        bootstrap._initialised = False

    def teardown_method(self):
        bootstrap._initialised = False

    def test_logs_proxy_disabled_when_unconfigured(self):
        # Given Settings has neither proxy field set (in-process SDK fallback)
        fake = _fake_settings(base_url=None, virtual_key=None)
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap, "settings", fake),
        ):
            # When initialise() runs
            bootstrap.initialise()

            # Then a litellm.proxy.disabled event is emitted with an in_process_sdk
            # fallback marker, and configure_logging still ran
            mock_logs.configure_logging.assert_called_once()
            mock_logs.log_event.assert_any_call(
                "litellm.proxy.disabled",
                params={"fallback": "in_process_sdk"},
            )

    def test_logs_proxy_disabled_when_only_base_url_set(self):
        # Given a partial proxy config (URL but no virtual key)
        fake = _fake_settings(base_url="https://litellm.internal/", virtual_key=None)
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap, "settings", fake),
        ):
            # When initialise() runs
            bootstrap.initialise()

            # Then bootstrap matches the helper's fail-safe semantics — partial
            # config is logged as disabled rather than enabled
            mock_logs.log_event.assert_any_call(
                "litellm.proxy.disabled",
                params={"fallback": "in_process_sdk"},
            )

    def test_logs_proxy_enabled_when_configured(self):
        # Given Settings carries both proxy fields populated
        proxy_url = "https://litellm.internal/"
        virtual_key = mock.MagicMock(get_secret_value=lambda: "sk-very-secret")
        fake = _fake_settings(base_url=proxy_url, virtual_key=virtual_key)
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap, "settings", fake),
        ):
            # When initialise() runs
            bootstrap.initialise()

            # Then a litellm.proxy.enabled event is emitted carrying the proxy host
            # (and never the virtual key)
            mock_logs.log_event.assert_any_call(
                "litellm.proxy.enabled",
                params={"host": proxy_url},
            )

    def test_proxy_enabled_log_never_includes_virtual_key(self):
        # Given a fully-configured proxy with a virtual key set on Settings
        virtual_key = mock.MagicMock(get_secret_value=lambda: "sk-very-secret")
        fake = _fake_settings(base_url="https://litellm.internal/", virtual_key=virtual_key)
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap, "settings", fake),
        ):
            # When initialise() runs
            bootstrap.initialise()

            # Then no log_event call carries the virtual key in any param value
            for call in mock_logs.log_event.call_args_list:
                params = call.kwargs.get("params") or (call.args[1] if len(call.args) > 1 else {})
                for value in (params or {}).values():
                    assert "sk-very-secret" not in str(value)

    def test_is_idempotent(self):
        # Given initialise() already ran once with the proxy unconfigured
        fake = _fake_settings(base_url=None, virtual_key=None)
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap, "settings", fake),
        ):
            # When initialise() is called twice
            bootstrap.initialise()
            mock_logs.log_event.reset_mock()
            bootstrap.initialise()

            # Then the proxy-disabled event is not re-emitted on the second call
            mock_logs.log_event.assert_not_called()
