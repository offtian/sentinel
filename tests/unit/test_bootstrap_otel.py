from __future__ import annotations

from unittest import mock

from sentinel import bootstrap_otel
from sentinel.utils import metrics


class TestInitOtel:
    def setup_method(self):
        bootstrap_otel._initialised = False
        metrics.reset_meters()

    def teardown_method(self):
        bootstrap_otel._initialised = False
        metrics.reset_meters()

    def test_no_op_when_metrics_disabled(self):
        # Given metrics are disabled in settings
        with mock.patch.object(bootstrap_otel, "get_settings") as mock_settings:
            mock_settings.return_value.otel_metrics_enabled = False
            mock_settings.return_value.otel_service_name = "sentinel"

            # When init_otel is called
            bootstrap_otel.init_otel()

            # Then no meter is configured
            assert metrics._meter is None

    def test_initialises_meter_when_enabled(self):
        # Given metrics are enabled
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.object(bootstrap_otel, "HTTPXClientInstrumentor"),
            mock.patch.object(bootstrap_otel, "SystemMetricsInstrumentor"),
        ):
            mock_settings.return_value.otel_metrics_enabled = True
            mock_settings.return_value.otel_service_name = "sentinel-test"

            # When init_otel is called
            bootstrap_otel.init_otel()

            # Then the meter is set
            assert metrics._meter is not None

    def test_swallows_exceptions(self):
        # Given an init that raises
        with mock.patch.object(bootstrap_otel, "get_settings") as mock_settings:
            mock_settings.return_value.otel_metrics_enabled = True
            mock_settings.side_effect = RuntimeError("boom")

            # When init_otel is called — Then no exception escapes
            bootstrap_otel.init_otel()


class TestInitTraces:
    def setup_method(self):
        bootstrap_otel._traces_initialised = False

    def teardown_method(self):
        bootstrap_otel._traces_initialised = False

    def test_no_op_when_traces_disabled(self):
        # Given traces are disabled in settings
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.object(bootstrap_otel, "logs") as mock_logs,
        ):
            mock_settings.return_value.otel_traces_enabled = False
            mock_settings.return_value.otel_traces_endpoint = "http://tempo:4318"

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then it logs disabled and does not configure logfire
            mock_logs.log_event.assert_called_once_with("otel.traces.disabled")

    def test_no_op_when_no_endpoint(self):
        # Given traces enabled but no endpoint configured
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.object(bootstrap_otel, "logs") as mock_logs,
        ):
            mock_settings.return_value.otel_traces_enabled = True
            mock_settings.return_value.otel_traces_endpoint = ""

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then it logs disabled
            mock_logs.log_event.assert_called_once_with("otel.traces.disabled")

    def test_configures_logfire_when_enabled(self):
        # Given traces enabled with a valid endpoint
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
        ):
            mock_settings.return_value.otel_traces_enabled = True
            mock_settings.return_value.otel_traces_endpoint = "http://tempo:4318"
            mock_settings.return_value.otel_service_name = "sentinel-test"

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then logfire.configure is called with send_to_logfire=False
            mock_logfire.configure.assert_called_once_with(
                send_to_logfire=False,
                service_name="sentinel-test",
            )

    def test_sets_otel_endpoint_env_var(self):
        # Given traces enabled with a valid endpoint
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os") as patched_os,
            mock.patch.object(bootstrap_otel, "logs"),
        ):
            mock_settings.return_value.otel_traces_enabled = True
            mock_settings.return_value.otel_traces_endpoint = "http://tempo:4318"
            mock_settings.return_value.otel_service_name = "sentinel-test"

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then OTEL_EXPORTER_OTLP_ENDPOINT is set via os.environ.setdefault
            patched_os.environ.setdefault.assert_called_once_with(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318"
            )

    def test_is_idempotent(self):
        # Given traces already initialised
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
        ):
            mock_settings.return_value.otel_traces_enabled = True
            mock_settings.return_value.otel_traces_endpoint = "http://tempo:4318"
            mock_settings.return_value.otel_service_name = "sentinel-test"

            # When init_traces is called twice
            bootstrap_otel.init_traces()
            mock_logfire.configure.reset_mock()
            bootstrap_otel.init_traces()

            # Then logfire.configure is not called the second time
            mock_logfire.configure.assert_not_called()

    def test_swallows_exceptions(self):
        # Given get_settings raises
        with mock.patch.object(bootstrap_otel, "get_settings") as mock_settings:
            mock_settings.side_effect = RuntimeError("boom")

            # When init_traces is called — Then no exception escapes
            bootstrap_otel.init_traces()
