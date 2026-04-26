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


class TestInitTracesLangfuseWiring:
    def setup_method(self):
        bootstrap_otel._traces_initialised = False

    def teardown_method(self):
        bootstrap_otel._traces_initialised = False

    def _settings(self, *, langfuse_host: str | None = None) -> mock.MagicMock:
        """
        Return a Settings stub with traces enabled and the given Langfuse host.
        """
        settings = mock.MagicMock()
        settings.otel_traces_enabled = True
        settings.otel_traces_endpoint = "http://tempo:4318"
        settings.otel_service_name = "sentinel-test"
        settings.langfuse_host = langfuse_host
        if langfuse_host:
            pk = mock.MagicMock()
            pk.get_secret_value.return_value = "pk"
            sk = mock.MagicMock()
            sk.get_secret_value.return_value = "sk"
            settings.langfuse_public_key = pk
            settings.langfuse_secret_key = sk
        else:
            settings.langfuse_public_key = None
            settings.langfuse_secret_key = None
        return settings

    def test_validator_registered_when_langfuse_host_unset(self):
        # Given traces enabled but no Langfuse host configured
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_settings.return_value = self._settings(langfuse_host=None)
            mock_trace.get_tracer_provider.return_value = provider
            validator_instance = mock.MagicMock()
            mock_lf.MandatoryAttributesValidator.return_value = validator_instance

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then the validator is registered exactly once and no exporter wires up
            provider.add_span_processor.assert_called_once_with(validator_instance)
            mock_lf.build_langfuse_exporter.assert_not_called()
            mock_batch.assert_not_called()

    def test_validator_and_exporter_registered_when_langfuse_host_set(self):
        # Given traces enabled and a Langfuse host configured
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        exporter_instance = mock.MagicMock()
        batch_processor_instance = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_settings.return_value = self._settings(langfuse_host="http://lf.local")
            mock_trace.get_tracer_provider.return_value = provider
            validator_instance = mock.MagicMock()
            mock_lf.MandatoryAttributesValidator.return_value = validator_instance
            mock_lf.build_langfuse_exporter.return_value = exporter_instance
            mock_batch.return_value = batch_processor_instance

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then the exporter is built with kwargs unwrapped from SecretStr
            mock_lf.build_langfuse_exporter.assert_called_once_with(
                host="http://lf.local",
                public_key="pk",
                secret_key="sk",  # noqa: S106 — test fixture, not a real secret
            )
            # And the BatchSpanProcessor wraps the exporter
            mock_batch.assert_called_once_with(exporter_instance)
            # And both processors are registered exactly once on the provider
            assert provider.add_span_processor.call_args_list == [
                mock.call(validator_instance),
                mock.call(batch_processor_instance),
            ]

    def test_exporter_skipped_when_build_returns_none(self):
        # Given build_langfuse_exporter returns None (construction failed)
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_settings.return_value = self._settings(langfuse_host="http://lf.local")
            mock_trace.get_tracer_provider.return_value = provider
            validator_instance = mock.MagicMock()
            mock_lf.MandatoryAttributesValidator.return_value = validator_instance
            mock_lf.build_langfuse_exporter.return_value = None

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then only the validator is registered; no BatchSpanProcessor is built
            provider.add_span_processor.assert_called_once_with(validator_instance)
            mock_batch.assert_not_called()

    def test_init_traces_is_idempotent(self):
        # Given a fully-configured Langfuse setup
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        exporter_instance = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "get_settings") as mock_settings,
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_settings.return_value = self._settings(langfuse_host="http://lf.local")
            mock_trace.get_tracer_provider.return_value = provider
            mock_lf.MandatoryAttributesValidator.return_value = mock.MagicMock()
            mock_lf.build_langfuse_exporter.return_value = exporter_instance
            mock_batch.return_value = mock.MagicMock()

            # When init_traces is called twice in a row
            bootstrap_otel.init_traces()
            bootstrap_otel.init_traces()

            # Then validator and exporter are registered exactly once each
            assert provider.add_span_processor.call_count == 2
            assert mock_lf.MandatoryAttributesValidator.call_count == 1
            assert mock_lf.build_langfuse_exporter.call_count == 1
