from __future__ import annotations

from unittest import mock

from sentinel import bootstrap_otel
from sentinel.utils import metrics


def _make_settings_stub(**attrs: object) -> mock.MagicMock:
    """Return a MagicMock that mimics the module-level Settings singleton."""
    stub = mock.MagicMock()
    for name, value in attrs.items():
        setattr(stub, name, value)
    return stub


class TestInitOtel:
    def setup_method(self):
        bootstrap_otel._initialised = False
        metrics.reset_meters()

    def teardown_method(self):
        bootstrap_otel._initialised = False
        metrics.reset_meters()

    def test_no_op_when_metrics_disabled(self):
        # Given metrics are disabled in settings
        settings_stub = _make_settings_stub(
            otel_metrics_enabled=False,
            otel_service_name="sentinel",
        )
        with mock.patch.object(bootstrap_otel, "settings", settings_stub):
            # When init_otel is called
            bootstrap_otel.init_otel()

            # Then no meter is configured
            assert metrics._meter is None

    def test_initialises_meter_when_enabled(self):
        # Given metrics are enabled
        settings_stub = _make_settings_stub(
            otel_metrics_enabled=True,
            otel_service_name="sentinel-test",
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.object(bootstrap_otel, "HTTPXClientInstrumentor"),
            mock.patch.object(bootstrap_otel, "SystemMetricsInstrumentor"),
        ):
            # When init_otel is called
            bootstrap_otel.init_otel()

            # Then the meter is set
            assert metrics._meter is not None

    def test_swallows_exceptions(self):
        # Given metrics are enabled but Resource.create raises
        settings_stub = _make_settings_stub(
            otel_metrics_enabled=True,
            otel_service_name="sentinel-test",
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.object(bootstrap_otel, "Resource") as mock_resource,
        ):
            mock_resource.create.side_effect = RuntimeError("boom")

            # When init_otel is called — Then no exception escapes
            bootstrap_otel.init_otel()


class TestInitTraces:
    def setup_method(self):
        bootstrap_otel._traces_initialised = False

    def teardown_method(self):
        bootstrap_otel._traces_initialised = False

    def test_no_op_when_traces_disabled(self):
        # Given traces are disabled in settings
        settings_stub = _make_settings_stub(
            otel_traces_enabled=False,
            otel_traces_endpoint="http://tempo:4318",
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.object(bootstrap_otel, "logs") as mock_logs,
        ):
            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then it logs disabled and does not configure logfire
            mock_logs.log_event.assert_called_once_with("otel.traces.disabled")

    def test_no_op_when_no_backend(self):
        # Given traces enabled but neither an OTLP endpoint nor a Langfuse host configured
        settings_stub = _make_settings_stub(
            otel_traces_enabled=True,
            otel_traces_endpoint="",
            langfuse_host=None,
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.object(bootstrap_otel, "logs") as mock_logs,
        ):
            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then it logs that no backend is configured
            mock_logs.log_event.assert_called_once_with("otel.traces.no_backend")

    def test_configures_logfire_when_enabled(self):
        # Given traces enabled with a valid endpoint
        mock_logfire = mock.MagicMock()
        settings_stub = _make_settings_stub(
            otel_traces_enabled=True,
            otel_traces_endpoint="http://tempo:4318",
            otel_service_name="sentinel-test",
            langfuse_host=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace"),
            mock.patch.object(bootstrap_otel, "langfuse_export"),
        ):
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
        settings_stub = _make_settings_stub(
            otel_traces_enabled=True,
            otel_traces_endpoint="http://tempo:4318",
            otel_service_name="sentinel-test",
            langfuse_host=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os") as patched_os,
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace"),
            mock.patch.object(bootstrap_otel, "langfuse_export"),
        ):
            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is set via os.environ.setdefault
            patched_os.environ.setdefault.assert_called_once_with(
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://tempo:4318"
            )

    def test_is_idempotent(self):
        # Given traces already initialised
        mock_logfire = mock.MagicMock()
        settings_stub = _make_settings_stub(
            otel_traces_enabled=True,
            otel_traces_endpoint="http://tempo:4318",
            otel_service_name="sentinel-test",
            langfuse_host=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace"),
            mock.patch.object(bootstrap_otel, "langfuse_export"),
        ):
            # When init_traces is called twice
            bootstrap_otel.init_traces()
            mock_logfire.configure.reset_mock()
            bootstrap_otel.init_traces()

            # Then logfire.configure is not called the second time
            mock_logfire.configure.assert_not_called()

    def test_swallows_exceptions(self):
        # Given traces enabled but logfire import fails
        settings_stub = _make_settings_stub(
            otel_traces_enabled=True,
            otel_traces_endpoint="http://tempo:4318",
            otel_service_name="sentinel-test",
        )
        with (
            mock.patch.object(bootstrap_otel, "settings", settings_stub),
            mock.patch.dict("sys.modules", {"logfire": None}),
        ):
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
        settings_stub = mock.MagicMock()
        settings_stub.otel_traces_enabled = True
        settings_stub.otel_traces_endpoint = "http://tempo:4318"
        settings_stub.otel_service_name = "sentinel-test"
        settings_stub.langfuse_host = langfuse_host
        if langfuse_host:
            pk = mock.MagicMock()
            pk.get_secret_value.return_value = "pk"
            sk = mock.MagicMock()
            sk.get_secret_value.return_value = "sk"
            settings_stub.langfuse_public_key = pk
            settings_stub.langfuse_secret_key = sk
        else:
            settings_stub.langfuse_public_key = None
            settings_stub.langfuse_secret_key = None
        return settings_stub

    def test_validator_registered_when_langfuse_host_unset(self):
        # Given traces enabled but no Langfuse host configured
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(bootstrap_otel, "settings", self._settings(langfuse_host=None)),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_trace.get_tracer_provider.return_value = provider
            propagator_instance = mock.MagicMock()
            validator_instance = mock.MagicMock()
            mock_lf.MandatoryAttributesPropagator.return_value = propagator_instance
            mock_lf.MandatoryAttributesValidator.return_value = validator_instance

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then the propagator and validator are registered, in that order, and no exporter wires up
            assert provider.add_span_processor.call_args_list == [
                mock.call(propagator_instance),
                mock.call(validator_instance),
            ]
            mock_lf.build_langfuse_exporter.assert_not_called()
            mock_batch.assert_not_called()

    def test_validator_and_exporter_registered_when_langfuse_host_set(self):
        # Given traces enabled and a Langfuse host configured
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        exporter_instance = mock.MagicMock()
        batch_processor_instance = mock.MagicMock()
        with (
            mock.patch.object(
                bootstrap_otel, "settings", self._settings(langfuse_host="http://lf.local")
            ),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_trace.get_tracer_provider.return_value = provider
            propagator_instance = mock.MagicMock()
            validator_instance = mock.MagicMock()
            mock_lf.MandatoryAttributesPropagator.return_value = propagator_instance
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
            # And propagator, validator, and exporter processors are registered exactly once each, in order
            assert provider.add_span_processor.call_args_list == [
                mock.call(propagator_instance),
                mock.call(validator_instance),
                mock.call(batch_processor_instance),
            ]

    def test_exporter_skipped_when_build_returns_none(self):
        # Given build_langfuse_exporter returns None (construction failed)
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        with (
            mock.patch.object(
                bootstrap_otel, "settings", self._settings(langfuse_host="http://lf.local")
            ),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_trace.get_tracer_provider.return_value = provider
            propagator_instance = mock.MagicMock()
            validator_instance = mock.MagicMock()
            mock_lf.MandatoryAttributesPropagator.return_value = propagator_instance
            mock_lf.MandatoryAttributesValidator.return_value = validator_instance
            mock_lf.build_langfuse_exporter.return_value = None

            # When init_traces is called
            bootstrap_otel.init_traces()

            # Then only the propagator and validator are registered; no BatchSpanProcessor is built
            assert provider.add_span_processor.call_args_list == [
                mock.call(propagator_instance),
                mock.call(validator_instance),
            ]
            mock_batch.assert_not_called()

    def test_init_traces_is_idempotent(self):
        # Given a fully-configured Langfuse setup
        provider = mock.MagicMock()
        mock_logfire = mock.MagicMock()
        exporter_instance = mock.MagicMock()
        with (
            mock.patch.object(
                bootstrap_otel, "settings", self._settings(langfuse_host="http://lf.local")
            ),
            mock.patch.dict("sys.modules", {"logfire": mock_logfire}),
            mock.patch.object(bootstrap_otel, "os"),
            mock.patch.object(bootstrap_otel, "logs"),
            mock.patch.object(bootstrap_otel, "trace") as mock_trace,
            mock.patch.object(bootstrap_otel, "langfuse_export") as mock_lf,
            mock.patch.object(bootstrap_otel, "BatchSpanProcessor") as mock_batch,
        ):
            mock_trace.get_tracer_provider.return_value = provider
            mock_lf.MandatoryAttributesPropagator.return_value = mock.MagicMock()
            mock_lf.MandatoryAttributesValidator.return_value = mock.MagicMock()
            mock_lf.build_langfuse_exporter.return_value = exporter_instance
            mock_batch.return_value = mock.MagicMock()

            # When init_traces is called twice in a row
            bootstrap_otel.init_traces()
            bootstrap_otel.init_traces()

            # Then propagator, validator, and exporter are registered exactly once each
            assert provider.add_span_processor.call_count == 3
            assert mock_lf.MandatoryAttributesPropagator.call_count == 1
            assert mock_lf.MandatoryAttributesValidator.call_count == 1
            assert mock_lf.build_langfuse_exporter.call_count == 1
