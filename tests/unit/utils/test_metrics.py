from __future__ import annotations

from unittest import mock

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from sentinel.utils import metrics


class TestRecordInvestigationCompleted:
    def test_does_not_raise_when_metrics_disabled(self):
        # Given metrics are disabled
        with mock.patch.object(metrics, "_meter", None):
            # When recording an investigation
            # Then no exception is raised
            metrics.record_investigation_completed(
                confidence_label="high",
                approval_required=False,
                outcome="completed",
            )

    def test_swallows_exceptions_during_recording(self):
        # Given a meter that raises on use
        broken_counter = mock.Mock()
        broken_counter.add.side_effect = RuntimeError("boom")
        with mock.patch.object(metrics, "_investigations_total", broken_counter):
            # When recording — Then no exception escapes
            metrics.record_investigation_completed(
                confidence_label="high",
                approval_required=False,
                outcome="completed",
            )


class TestInitMeters:
    def setup_method(self):
        metrics.reset_meters()

    def teardown_method(self):
        metrics.reset_meters()

    def test_records_investigations_after_init(self):
        # Given meters initialised against an in-memory reader
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("test")
        metrics.init_meters(meter=meter)

        # When recording an investigation
        metrics.record_investigation_completed(
            confidence_label="high",
            approval_required=False,
            outcome="completed",
        )

        # Then the counter is incremented
        data = reader.get_metrics_data()
        names = {
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "sentinel_investigations_total" in names

    def test_records_pipeline_node_duration(self):
        # Given meters initialised against an in-memory reader
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("test")
        metrics.init_meters(meter=meter)

        # When recording a node duration
        metrics.record_pipeline_node_duration(
            pipeline="investigation",
            node="classify_alert",
            duration_seconds=0.42,
            status="ok",
        )

        # Then the histogram observation is exported
        data = reader.get_metrics_data()
        names = {
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "sentinel_pipeline_node_duration_seconds" in names
