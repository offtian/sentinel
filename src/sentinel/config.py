"""
Application-level configuration: wire vendor adapters, searchers, and agents.

The ``Configuration`` class holds the application ``Settings`` and exposes
``load_*()`` methods that build pre-configured infrastructure for each
pipeline.  Entry points (API lifespan, worker, Slack bot) call
``get_config()`` once at startup.

Usage::

    from sentinel.config import get_config

    config = get_config()
    holmes = config.build_holmes_adapter()
    doc_searcher = config.build_document_searcher()
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic_ai.toolsets import FunctionToolset

from sentinel.domain.resilience.circuit_breaker import CircuitBreaker
from sentinel.domain.search import factory as search_factory
from sentinel.domain.search import searcher
from sentinel.domain.sre import holmes_adapter, investigation
from sentinel.domain.vendor_adapters.confluence import ConfluenceClient
from sentinel.domain.vendor_adapters.jira import JiraClient
from sentinel.domain.vendor_adapters.observability import (
    BaseObservabilityClient,
    DatadogClient,
    GrafanaClient,
)
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.plugins.toolsets import documentation as doc_toolsets
from sentinel.plugins.toolsets import observability as obs_toolsets
from sentinel.settings import Settings, get_settings
from sentinel.utils import logs


logger = logs.get_logger()


# ---------------------------------------------------------------------------
# Application Configuration
# ---------------------------------------------------------------------------


def _normalise_model_name(model_name: str) -> str:
    """
    Normalise LLM model names for pydantic-ai.

    Config uses ``openai/gpt-4.1-mini``; pydantic-ai expects ``openai:gpt-4.1-mini``.
    LiteLLM runs as an OpenAI-compatible proxy on ``OPENAI_BASE_URL``.
    """
    model_name = model_name.removeprefix("litellm_proxy/")
    if "/" in model_name:
        provider, name = model_name.split("/", 1)
        model_name = f"{provider}:{name}"
    return model_name


class Configuration(BaseModel):
    """
    Application-wide configuration holding settings and wired infrastructure.

    Call ``load_vendors()`` to build long-lived vendor adapter instances,
    then use the ``build_*()`` helpers to construct pipeline dependencies.
    Designed to be created once per process (API startup, worker, CLI).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: Settings

    # Vendor adapters — populated by load_vendors()
    observability_client: BaseObservabilityClient | None = None
    pagerduty_client: PagerDutyClient | None = None
    jira_client: JiraClient | None = None
    confluence_client: ConfluenceClient | None = None

    # Resilience — populated by load_vendors()
    observability_circuit_breaker: CircuitBreaker | None = None

    def load_vendors(self) -> None:
        """
        Build long-lived vendor adapter instances.

        The observability backend is selected by ``OBSERVABILITY_BACKEND``.
        When unset, defaults to ``"grafana"`` in local dev and ``"datadog"``
        in production.

        Adapters are no-ops when their credentials are not configured.
        The circuit breaker persists across jobs so that failure state
        accumulates correctly.
        """
        backend = self.settings.observability_backend
        if not backend:
            backend = "grafana" if self.settings.is_local else "datadog"

        if backend == "grafana":
            self.observability_client = GrafanaClient()
        else:
            self.observability_client = DatadogClient()

        self.pagerduty_client = PagerDutyClient()
        self.observability_circuit_breaker = CircuitBreaker(name="observability")

        if self.settings.jira_base_url:
            self.jira_client = JiraClient()

        if self.settings.confluence_base_url:
            self.confluence_client = ConfluenceClient()

        logger.info(
            "Vendor adapters loaded",
            observability_backend=backend,
            observability_configured=self.observability_client.is_configured,
            pagerduty=self.pagerduty_client is not None,
            jira=self.jira_client is not None,
            confluence=self.confluence_client is not None,
        )

    # -- LLM model names (normalised for pydantic-ai) -------------------------

    @property
    def classifier_model(self) -> str:
        return _normalise_model_name(self.settings.alert_classifier_llm)

    @property
    def analyser_model(self) -> str:
        return _normalise_model_name(self.settings.root_cause_llm)

    @property
    def reviewer_model(self) -> str:
        return _normalise_model_name(self.settings.ticket_reviewer_llm)

    @property
    def drafter_model(self) -> str:
        return _normalise_model_name(self.settings.response_drafter_llm)

    # -- SRE pipeline helpers ------------------------------------------------

    def build_holmes_adapter(self) -> holmes_adapter.BaseHolmesAdapter:
        """
        Build the appropriate Holmes adapter based on configuration.

        When ``holmesgpt_enabled`` is True and a Datadog client is available,
        return a ``DirectToolsetAdapter`` that queries observability data.
        Otherwise return a disabled ``HolmesAdapter`` stub.
        """
        if self.settings.holmesgpt_enabled and self.observability_client is not None:
            return holmes_adapter.DirectToolsetAdapter(
                observability_client=self.observability_client,
                circuit_breaker=self.observability_circuit_breaker,
            )
        return holmes_adapter.HolmesAdapter(enabled=False)

    def build_k8s_investigation_adapter(
        self,
    ) -> investigation.K8sInvestigationAdapter | None:
        """
        Build the K8s investigation adapter based on configuration.

        Returns None when K8s investigation is disabled.
        Injects MCP client toolsets from ``MCP_SERVERS`` and
        ``K8S_MCP_SERVER_URL`` settings when available.

        :returns: A K8sInvestigationAdapter or None.
        """
        backend = self.settings.k8s_investigation_backend
        if not backend:
            return None

        if backend in ("native", "both"):
            from sentinel.domain.sre import k8s_native_agent
            from sentinel.interfaces.graphs.agents import k8s_runner
            from sentinel.plugins.toolsets import mcp as mcp_toolset_mod

            mcp_toolsets = list(
                mcp_toolset_mod.build_mcp_toolsets(config_json=self.settings.mcp_servers)
            )

            if self.settings.k8s_mcp_server_url:
                from pydantic_ai.mcp import MCPServerSSE

                mcp_toolsets.append(MCPServerSSE(url=self.settings.k8s_mcp_server_url))

            return k8s_native_agent.NativeK8sAgent(
                k8s_client=None,  # Wire real K8s client when kubernetes lib is integrated
                model_name=_normalise_model_name(self.settings.k8s_investigator_llm),
                mcp_toolsets=tuple(mcp_toolsets),
                agent_runner=k8s_runner.run_k8s_agent,
            )

        return None  # kagent adapter wired separately

    @property
    def k8s_investigator_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_investigator_llm)

    # -- Chart generation helpers --------------------------------------------

    @property
    def chart_parser_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_chart_parser_llm)

    @property
    def chart_generator_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_chart_generator_llm)

    @property
    def chart_max_retries(self) -> int:
        return self.settings.k8s_chart_max_retries

    def build_chart_generation_kwargs(
        self,
        *,
        parser_model: str = "",
        generator_model: str = "",
        max_retries: int | None = None,
    ) -> dict[str, object]:
        """
        Build keyword arguments for ``chart_generation.generate_chart()``.

        Callers can override individual settings (e.g. the Streamlit UI passes
        user-selected models) while the rest falls back to configuration.

        :param parser_model: Override for the parser LLM model.
        :param generator_model: Override for the generator LLM model.
        :param max_retries: Override for max self-heal retries.
        :returns: kwargs dict ready to pass to ``generate_chart()``.
        """
        return {
            "parser_model": parser_model or self.chart_parser_model,
            "generator_model": generator_model or self.chart_generator_model,
            "max_retries": max_retries if max_retries is not None else self.chart_max_retries,
        }

    # -- Support pipeline helpers --------------------------------------------

    def build_document_searcher(self) -> searcher.BaseDocumentSearcher | None:
        """Build the configured document searcher, or None if unconfigured."""
        return search_factory.build_document_searcher()

    def build_ticket_searcher(self) -> searcher.BasePastTicketSearcher | None:
        """Build the configured past-ticket searcher, or None if unconfigured."""
        return search_factory.build_ticket_searcher()

    def build_metrics_searcher(self) -> searcher.BaseMetricsSearcher | None:
        """Build a metrics searcher if Datadog is configured."""
        return search_factory.build_metrics_searcher()

    # -- Toolset builders (injected at agent.run() time) ----------------------

    def build_observability_toolset(self, *, service_name: str = "") -> FunctionToolset[object]:
        """
        Build a read-only observability toolset for SRE investigation agents.

        Tools include log search, metric queries, and error trace lookup.
        Each tool no-ops when the observability client is unconfigured.

        :param service_name: Default service for queries (from the alert).
        """
        return obs_toolsets.build_observability_toolset(
            observability_client=self.observability_client,
            service_name=service_name,
        )

    def build_support_search_toolset(self) -> FunctionToolset[object]:
        """
        Build a read-only search toolset for the response drafter agent.

        Tools include documentation search and past-ticket resolution lookup.
        """
        return doc_toolsets.build_support_search_toolset(
            document_searcher=self.build_document_searcher(),
            ticket_searcher=self.build_ticket_searcher(),
        )

    def build_ticket_triage_toolset(self) -> FunctionToolset[object]:
        """
        Build a read-only toolset for the ticket reviewer agent.

        Tools include duplicate/similar ticket detection.
        """
        return doc_toolsets.build_ticket_triage_toolset(
            ticket_searcher=self.build_ticket_searcher(),
        )


_config: Configuration | None = None


def get_config(cfg: Settings | None = None) -> Configuration:
    """
    Return the cached application configuration, creating it on first call.

    Calls ``load_vendors()`` so all adapters are ready to use.

    :param cfg: Override settings. Defaults to the module-level singleton.
    """
    global _config  # noqa: PLW0603
    if _config is None:
        _config = Configuration(settings=cfg or get_settings())
        _config.load_vendors()
        logger.info("Application configuration initialised")
    return _config
