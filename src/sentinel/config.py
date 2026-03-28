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

from sentinel.domain.resilience.circuit_breaker import CircuitBreaker
from sentinel.domain.search import factory as search_factory
from sentinel.domain.search import searcher
from sentinel.domain.sre import holmes_adapter
from sentinel.domain.vendor_adapters.confluence import ConfluenceClient
from sentinel.domain.vendor_adapters.jira import JiraClient
from sentinel.domain.vendor_adapters.observability import (
    BaseObservabilityClient,
    DatadogClient,
    GrafanaClient,
)
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
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
