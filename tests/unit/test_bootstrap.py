from __future__ import annotations

from unittest import mock

from sentinel import bootstrap


class TestInitialiseLitellmProxyLogging:
    def setup_method(self):
        bootstrap._initialised = False

    def teardown_method(self):
        bootstrap._initialised = False

    def test_logs_proxy_disabled_when_base_url_unset(self):
        # Given Settings with litellm_base_url unset (in-process SDK fallback)
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap.settings, "get_settings") as mock_settings,
        ):
            mock_settings.return_value.litellm_base_url = None

            # When initialise() runs
            bootstrap.initialise()

            # Then a litellm_proxy_disabled event is emitted at WARNING level with
            # an in_process_sdk fallback marker, and configure_logging still ran
            mock_logs.configure_logging.assert_called_once()
            mock_logs.get_logger.return_value.warning.assert_called_once_with(
                "litellm_proxy_disabled",
                fallback="in_process_sdk",
            )

    def test_logs_proxy_enabled_when_base_url_set(self):
        # Given Settings with litellm_base_url pointing at a proxy host
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap.settings, "get_settings") as mock_settings,
        ):
            proxy_url = "https://litellm.internal/"
            mock_settings.return_value.litellm_base_url = proxy_url

            # When initialise() runs
            bootstrap.initialise()

            # Then a litellm_proxy_enabled event is emitted at INFO level carrying
            # the proxy host (and never the virtual key)
            mock_logs.log_event.assert_any_call(
                "litellm_proxy_enabled",
                params={"host": proxy_url},
            )

    def test_proxy_enabled_log_never_includes_virtual_key(self):
        # Given a fully-configured proxy with a virtual key set
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap.settings, "get_settings") as mock_settings,
        ):
            mock_settings.return_value.litellm_base_url = "https://litellm.internal/"
            mock_settings.return_value.litellm_virtual_key = mock.MagicMock(
                get_secret_value=lambda: "sk-very-secret",
            )

            # When initialise() runs
            bootstrap.initialise()

            # Then no log_event call carries the virtual key in any param value
            for call in mock_logs.log_event.call_args_list:
                params = call.kwargs.get("params") or (call.args[1] if len(call.args) > 1 else {})
                for value in (params or {}).values():
                    assert "sk-very-secret" not in str(value)

    def test_is_idempotent(self):
        # Given initialise() already ran once
        with (
            mock.patch.object(bootstrap, "_configure_llm_env"),
            mock.patch.object(bootstrap, "bootstrap_otel"),
            mock.patch.object(bootstrap, "logs") as mock_logs,
            mock.patch.object(bootstrap.settings, "get_settings") as mock_settings,
        ):
            mock_settings.return_value.litellm_base_url = None

            # When initialise() is called twice
            bootstrap.initialise()
            mock_logs.get_logger.return_value.warning.reset_mock()
            bootstrap.initialise()

            # Then the proxy-disabled warning is not re-emitted on the second call
            mock_logs.get_logger.return_value.warning.assert_not_called()
