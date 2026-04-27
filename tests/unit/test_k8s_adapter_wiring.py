from __future__ import annotations

from unittest import mock

import pytest

from sentinel import settings
from sentinel import worker as worker_mod
from sentinel.domain.investigations import adapters, k8s_native_agent, kagent_adapter
from sentinel.plugins.common import config as plugin_config_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def disabled_settings() -> mock.MagicMock:
    """Settings with K8s investigation disabled (empty backend)."""
    s = mock.MagicMock(spec=settings.Settings)
    s.k8s_investigation_backend = ""
    s.challenger_adapter = ""
    s.mcp_servers = ""
    s.k8s_mcp_server_url = ""
    s.k8s_investigator_llm = "openai/gpt-4.1"
    s.kagent_namespace = "kagent-system"
    s.kagent_investigation_timeout_seconds = 120
    return s


@pytest.fixture
def native_settings() -> mock.MagicMock:
    """Settings with K8s investigation set to 'native'."""
    s = mock.MagicMock(spec=settings.Settings)
    s.k8s_investigation_backend = "native"
    s.challenger_adapter = ""
    s.mcp_servers = ""
    s.k8s_mcp_server_url = ""
    s.k8s_investigator_llm = "openai/gpt-4.1"
    s.kagent_namespace = "kagent-system"
    s.kagent_investigation_timeout_seconds = 120
    return s


@pytest.fixture
def both_settings() -> mock.MagicMock:
    """Settings with K8s investigation set to 'both' and kagent challenger."""
    s = mock.MagicMock(spec=settings.Settings)
    s.k8s_investigation_backend = "both"
    s.challenger_adapter = "kagent"
    s.mcp_servers = ""
    s.k8s_mcp_server_url = ""
    s.k8s_investigator_llm = "openai/gpt-4.1"
    s.kagent_namespace = "kagent-system"
    s.kagent_investigation_timeout_seconds = 120
    return s


@pytest.fixture
def stub_runner() -> mock.AsyncMock:
    return mock.AsyncMock()


# ---------------------------------------------------------------------------
# Config adapter building: build_k8s_investigation_adapter
# ---------------------------------------------------------------------------


class TestBuildK8sInvestigationAdapter:
    def test_returns_none_when_backend_is_empty(
        self, disabled_settings: mock.MagicMock, stub_runner: mock.AsyncMock
    ) -> None:
        # Given settings with K8S_INVESTIGATION_BACKEND=""
        cfg = plugin_config_mod.CommonConfiguration(settings=disabled_settings)

        # When building the K8s investigation adapter
        result = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)

        # Then None is returned because K8s investigation is disabled
        assert result is None

    def test_returns_native_k8s_agent_when_backend_is_native(
        self, native_settings: mock.MagicMock, stub_runner: mock.AsyncMock
    ) -> None:
        # Given settings with K8S_INVESTIGATION_BACKEND="native"
        cfg = plugin_config_mod.CommonConfiguration(settings=native_settings)

        # When building the K8s investigation adapter
        result = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)

        # Then a NativeK8sAgent instance is returned
        assert isinstance(result, k8s_native_agent.NativeK8sAgent)

    def test_returns_native_k8s_agent_when_backend_is_both(
        self, both_settings: mock.MagicMock, stub_runner: mock.AsyncMock
    ) -> None:
        # Given settings with K8S_INVESTIGATION_BACKEND="both"
        cfg = plugin_config_mod.CommonConfiguration(settings=both_settings)

        # When building the K8s investigation adapter
        result = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)

        # Then a NativeK8sAgent instance is returned as the primary adapter
        assert isinstance(result, k8s_native_agent.NativeK8sAgent)


# ---------------------------------------------------------------------------
# Config adapter building: build_challenger_adapter
# ---------------------------------------------------------------------------


class TestBuildChallengerAdapter:
    def test_returns_none_when_challenger_is_empty(
        self, disabled_settings: mock.MagicMock
    ) -> None:
        # Given settings with CHALLENGER_ADAPTER=""
        cfg = plugin_config_mod.CommonConfiguration(settings=disabled_settings)

        # When building the challenger adapter
        result = cfg.build_challenger_adapter()

        # Then None is returned because challenger mode is disabled
        assert result is None

    def test_returns_kagent_adapter_when_challenger_is_kagent(
        self, disabled_settings: mock.MagicMock
    ) -> None:
        # Given settings with CHALLENGER_ADAPTER="kagent"
        disabled_settings.challenger_adapter = "kagent"
        cfg = plugin_config_mod.CommonConfiguration(settings=disabled_settings)

        # When building the challenger adapter
        result = cfg.build_challenger_adapter()

        # Then a KagentAdapter instance is returned
        assert isinstance(result, kagent_adapter.KagentAdapter)

    def test_both_backend_produces_primary_and_challenger(
        self, both_settings: mock.MagicMock, stub_runner: mock.AsyncMock
    ) -> None:
        # Given settings with K8S_INVESTIGATION_BACKEND="both" and CHALLENGER_ADAPTER="kagent"
        cfg = plugin_config_mod.CommonConfiguration(settings=both_settings)

        # When building primary and challenger adapters
        primary = cfg.build_k8s_investigation_adapter(agent_runner=stub_runner)
        challenger = cfg.build_challenger_adapter()

        # Then both adapters are returned and are different types
        assert primary is not None
        assert challenger is not None
        assert isinstance(primary, k8s_native_agent.NativeK8sAgent)
        assert isinstance(challenger, kagent_adapter.KagentAdapter)


# ---------------------------------------------------------------------------
# Worker wiring: investigate_alert receives adapters from worker
# ---------------------------------------------------------------------------


class TestWorkerK8sAdapterWiring:
    """Verify that _run_sre_investigation passes K8s adapters through to investigate_alert.

    These tests mock every worker dependency (DB, prompts, tracer) so we can
    inspect the keyword arguments forwarded to investigate_alert.
    """

    @staticmethod
    def _make_worker_settings(*, backend: str = "", challenger: str = "") -> mock.MagicMock:
        s = mock.MagicMock(spec=settings.Settings)
        s.k8s_investigation_backend = backend
        s.challenger_adapter = challenger
        s.mcp_servers = ""
        s.k8s_mcp_server_url = ""
        s.k8s_investigator_llm = "openai/gpt-4.1"
        s.alert_classifier_llm = "openai/gpt-4.1-mini"
        s.root_cause_llm = "openai/gpt-4.1"
        s.sre_auto_investigate = True
        # F7: holmesgpt_enabled archived; field commented out in
        # Settings. Stub no longer needs to set it.
        s.require_approval_below_confidence = 0.7
        s.approval_timeout_seconds = 0
        s.pagerduty_api_key = ""
        s.kagent_namespace = "kagent-system"
        s.kagent_investigation_timeout_seconds = 120
        return s

    @staticmethod
    def _make_config(
        *,
        k8s_adapter: mock.MagicMock | None = None,
        challenger: mock.MagicMock | None = None,
    ) -> mock.MagicMock:
        cfg = mock.MagicMock()
        cfg.build_k8s_investigation_adapter.return_value = k8s_adapter
        cfg.build_challenger_adapter.return_value = challenger
        # F7: build_holmes_adapter archived. Worker no longer calls it.
        cfg.build_mcp_toolsets.return_value = ()
        cfg.build_observability_toolset.return_value = mock.MagicMock()
        cfg.agent_for = mock.MagicMock()
        cfg.pagerduty_client = None
        return cfg

    @staticmethod
    def _make_fake_investigate() -> mock.AsyncMock:
        fake = mock.AsyncMock()
        fake.return_value = mock.MagicMock(
            model_dump_json=mock.MagicMock(return_value='{"alert_id": "test"}'),
            model_dump=mock.MagicMock(return_value={"alert_id": "test"}),
        )
        return fake

    @staticmethod
    def _make_fake_template() -> mock.MagicMock:
        tpl = mock.MagicMock()
        tpl.version = "1.0"
        tpl.sha256 = "abc123"
        tpl.system_text = "test prompt"
        return tpl

    @pytest.mark.asyncio
    async def test_worker_passes_k8s_adapter_when_backend_configured(self) -> None:
        # Given a worker with K8S_INVESTIGATION_BACKEND="native"
        worker_settings = self._make_worker_settings(backend="native")
        fake_k8s_adapter = mock.MagicMock(spec=adapters.K8sInvestigationAdapter)
        fake_cfg = self._make_config(k8s_adapter=fake_k8s_adapter)
        fake_investigate = self._make_fake_investigate()
        fake_tpl = self._make_fake_template()

        with (
            mock.patch.object(worker_mod, "settings", worker_settings),
            mock.patch.object(worker_mod, "config_mod") as patched_config_mod,
            mock.patch.object(worker_mod, "investigation") as mock_sre_mod,
            mock.patch.object(worker_mod, "prompts") as mock_prompts,
            mock.patch.object(worker_mod, "pipeline_tracer") as mock_tracer_mod,
            mock.patch.object(worker_mod, "pipeline_queries"),
            mock.patch.object(worker_mod, "_get_optional_db", return_value=None),
        ):
            patched_config_mod.get_config.return_value = fake_cfg
            mock_sre_mod.investigate_alert = fake_investigate
            mock_prompts.load_template.return_value = fake_tpl
            mock_tracer_mod.ExecutionTracer.return_value = mock.AsyncMock()

            # When the worker runs an SRE investigation
            await worker_mod._run_sre_investigation(
                {
                    "id": "test-123",
                    "source": "pagerduty",
                    "title": "High CPU",
                    "description": "CPU usage exceeded 90%",
                    "severity": "high",
                    "service": "api",
                    "triggered_at": "2026-04-12T20:00:00Z",
                },
            )

            # Then investigate_alert is called with k8s_adapter
            call_kwargs = fake_investigate.call_args.kwargs
            assert call_kwargs.get("k8s_adapter") is fake_k8s_adapter

    @pytest.mark.asyncio
    async def test_worker_passes_challenger_adapter_when_configured(self) -> None:
        # Given a worker with K8S_INVESTIGATION_BACKEND="both" and CHALLENGER_ADAPTER="kagent"
        worker_settings = self._make_worker_settings(backend="both", challenger="kagent")
        fake_k8s_adapter = mock.MagicMock(spec=adapters.K8sInvestigationAdapter)
        fake_challenger = mock.MagicMock(spec=adapters.BaseInvestigationAdapter)
        fake_cfg = self._make_config(k8s_adapter=fake_k8s_adapter, challenger=fake_challenger)
        fake_investigate = self._make_fake_investigate()
        fake_tpl = self._make_fake_template()

        with (
            mock.patch.object(worker_mod, "settings", worker_settings),
            mock.patch.object(worker_mod, "config_mod") as patched_config_mod,
            mock.patch.object(worker_mod, "investigation") as mock_sre_mod,
            mock.patch.object(worker_mod, "prompts") as mock_prompts,
            mock.patch.object(worker_mod, "pipeline_tracer") as mock_tracer_mod,
            mock.patch.object(worker_mod, "pipeline_queries"),
            mock.patch.object(worker_mod, "_get_optional_db", return_value=None),
        ):
            patched_config_mod.get_config.return_value = fake_cfg
            mock_sre_mod.investigate_alert = fake_investigate
            mock_prompts.load_template.return_value = fake_tpl
            mock_tracer_mod.ExecutionTracer.return_value = mock.AsyncMock()

            # When the worker runs an SRE investigation
            await worker_mod._run_sre_investigation(
                {
                    "id": "test-456",
                    "source": "pagerduty",
                    "title": "Pod CrashLoop",
                    "description": "Pod payments-7f8b6c is in CrashLoopBackOff",
                    "severity": "critical",
                    "service": "payments",
                    "triggered_at": "2026-04-12T20:00:00Z",
                },
            )

            # Then investigate_alert is called with both k8s_adapter and challenger_adapter
            call_kwargs = fake_investigate.call_args.kwargs
            assert call_kwargs.get("k8s_adapter") is fake_k8s_adapter
            assert call_kwargs.get("challenger_adapter") is fake_challenger
