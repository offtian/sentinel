from __future__ import annotations

from unittest import mock

from sentinel import bootstrap_otel
from sentinel.utils import metrics


class TestInitOtel:
    def setup_method(self):
        bootstrap_otel._initialised = False

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
