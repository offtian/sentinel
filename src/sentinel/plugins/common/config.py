"""
Common configuration: wire vendor adapters, searchers, toolsets, and agents.

``CommonConfiguration`` inherits from the lightweight base
``sentinel.config.BaseConfiguration`` and adds all domain/plugin
integration.  It is the **central truth** for how domain services
and plugins are composed at runtime.

``sentinel.config.get_config()`` auto-creates a ``CommonConfiguration``
via importlib on first access, so callers never need to import this
module directly.
"""

from __future__ import annotations

import threading
from typing import Any

from kubernetes_asyncio import client as k8s_async_client
from pydantic import ConfigDict, PrivateAttr
from pydantic_ai import models as pydantic_ai_models
from pydantic_ai.mcp import MCPServerSSE
from pydantic_ai.models import Model as PydanticAIModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from pydantic_ai.toolsets import AbstractToolset

from sentinel import config as base_config_mod
from sentinel.config import BaseConfiguration
from sentinel.domain.investigations import adapters, holmes_adapter, k8s_native_agent
from sentinel.domain.investigations import kagent_adapter as kagent_adapter_mod
from sentinel.domain.llm import litellm_proxy
from sentinel.domain.resilience.circuit_breaker import CircuitBreaker
from sentinel.domain.search import factory as search_factory
from sentinel.domain.search import searcher
from sentinel.domain.vendor_adapters.confluence import ConfluenceClient
from sentinel.domain.vendor_adapters.jira import JiraClient
from sentinel.domain.vendor_adapters.observability import (
    BaseObservabilityClient,
    DatadogClient,
    GrafanaClient,
)
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.plugins.models import capturing as capturing_model_mod
from sentinel.plugins.toolsets import documentation as doc_toolsets
from sentinel.plugins.toolsets import mcp as mcp_toolset_mod
from sentinel.plugins.toolsets import observability as obs_toolsets
from sentinel.utils import logs
from sentinel.vendors import kubernetes_client as k8s_client_mod


try:
    from holmes.core import tools as holmes_tools_mod
    from holmes.plugins import toolsets as holmes_toolsets_mod

    _HOLMES_SDK_AVAILABLE = True
except ImportError:
    _HOLMES_SDK_AVAILABLE = False


logger = logs.get_logger()

_mcp_build_lock = threading.Lock()


def _strip_wiki_suffix(url: str) -> str:
    return url.rstrip("/").removesuffix("/wiki")


class CommonConfiguration(BaseConfiguration):
    """
    Full application configuration with domain and plugin wiring.

    Extends the lightweight base ``BaseConfiguration`` with vendor adapters,
    search infrastructure, toolset builders, and agent loading.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Vendor adapters — populated by load_vendors()
    observability_client: BaseObservabilityClient | None = None
    pagerduty_client: PagerDutyClient | None = None
    jira_client: JiraClient | None = None
    confluence_client: ConfluenceClient | None = None

    # Resilience — populated by load_vendors()
    observability_circuit_breaker: CircuitBreaker | None = None

    # Memoised shared MCP toolsets — populated lazily by build_mcp_toolsets().
    _mcp_toolsets: tuple[Any, ...] | None = PrivateAttr(default=None)
    _k8s_client_cache: k8s_client_mod.KubernetesClient | None = PrivateAttr(default=None)

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

    # -- MCP toolsets --------------------------------------------------------

    def build_mcp_toolsets(self) -> tuple[Any, ...]:
        """
        Build and memoise the shared MCP toolsets from ``MCP_SERVERS``.

        Parse the JSON env var via ``plugins.toolsets.mcp.build_mcp_toolsets``
        exactly once per ``Configuration`` instance.  Subsequent calls return
        the cached tuple.  Thread-safe via a module-level lock.

        :returns: Tuple of PydanticAI-compatible MCP toolset instances.
        """
        cached = self._mcp_toolsets
        if cached is not None:
            return cached

        with _mcp_build_lock:
            # Double-check after acquiring the lock.
            cached = self._mcp_toolsets
            if cached is not None:
                return cached

            result = mcp_toolset_mod.build_mcp_toolsets(
                config_json=self.settings.mcp_servers,
            )
            self._mcp_toolsets = result
            return result

    # -- SRE pipeline helpers ------------------------------------------------

    def build_holmes_adapter(self) -> holmes_adapter.BaseHolmesAdapter:
        """
        Build the appropriate Holmes adapter based on configuration.

        When ``holmes_backend`` is ``"sdk"`` and the HolmesGPT SDK is
        installed, return a ``HolmesAdapter`` that uses ``ToolCallingLLM``.
        Otherwise (default ``"direct"``), return a ``DirectToolsetAdapter``
        that queries vendor adapters directly.
        """
        if not self.settings.holmesgpt_enabled:
            return holmes_adapter.HolmesAdapter(enabled=False)

        if self.settings.holmes_backend == "sdk":
            toolsets = self._build_holmes_sdk_toolsets()
            return holmes_adapter.HolmesAdapter(
                enabled=True,
                model=self._normalise_model_name(self.settings.holmes_sdk_model),
                api_key=None,
                api_base=None,
                toolsets=toolsets,
            )

        if self.observability_client is not None:
            return holmes_adapter.DirectToolsetAdapter(
                observability_client=self.observability_client,
                circuit_breaker=self.observability_circuit_breaker,
            )
        return holmes_adapter.HolmesAdapter(enabled=False)

    def _build_holmes_sdk_toolsets(self) -> tuple[Any, ...]:
        """Wire HolmesGPT built-in toolsets from Sentinel settings (no-op when unconfigured)."""
        if not _HOLMES_SDK_AVAILABLE:
            logger.warning("holmes_sdk_not_installed")
            return ()

        settings = self.settings
        raw_config: dict[str, dict[str, Any]] = {}
        by_name: dict[str, Any] = {}

        if settings.confluence_base_url and settings.jira_user_email and settings.jira_api_token:
            confluence_toolset = holmes_toolsets_mod.ConfluenceToolset()
            by_name[confluence_toolset.name] = confluence_toolset
            raw_config[confluence_toolset.name] = {
                "enabled": True,
                "config": {
                    "api_url": _strip_wiki_suffix(settings.confluence_base_url),
                    "user": settings.jira_user_email,
                    "api_key": settings.jira_api_token,
                },
            }

        if settings.notion_token:
            notion_toolset = holmes_toolsets_mod.NotionToolset()
            by_name[notion_toolset.name] = notion_toolset
            raw_config[notion_toolset.name] = {
                "enabled": True,
                "config": {
                    "additional_headers": {
                        "Authorization": f"Bearer {settings.notion_token}",
                    },
                },
            }

        enabled: list[Any] = []
        if raw_config:
            overrides = holmes_toolsets_mod.load_toolsets_from_config(
                raw_config,
                strict_check=False,
            )
            for override in overrides:
                target = by_name.get(override.name)
                if target is not None:
                    target.override_with(override)

            for toolset in by_name.values():
                toolset.check_prerequisites(silent=True)
                if toolset.status == holmes_tools_mod.ToolsetStatusEnum.ENABLED:
                    enabled.append(toolset)

        logger.info(
            "holmes_sdk_toolsets_loaded",
            configured=list(raw_config.keys()),
            enabled=[ts.name for ts in enabled],
        )
        return tuple(enabled)

    def _build_k8s_client(self) -> k8s_client_mod.KubernetesClient:
        """
        Build a Kubernetes client, caching the instance for reuse.

        Uses the synchronous constructor which attempts in-cluster config.
        The client's ``is_configured`` property indicates whether a valid
        cluster connection was established.
        """
        if self._k8s_client_cache is None:
            self._k8s_client_cache = k8s_client_mod.KubernetesClient()
        return self._k8s_client_cache

    def _build_k8s_custom_objects_api(self) -> k8s_async_client.CustomObjectsApi | None:
        """
        Build a CustomObjectsApi for CRD operations.

        Return None when no K8s cluster is reachable, so the kagent adapter
        gracefully degrades via its ``is_configured`` check.
        """
        k8s_client = self._build_k8s_client()
        if not k8s_client.is_configured:
            return None

        return k8s_async_client.CustomObjectsApi()

    def build_k8s_investigation_adapter(
        self,
        *,
        agent_runner: k8s_native_agent.AgentRunner,
    ) -> adapters.K8sInvestigationAdapter | None:
        """
        Build the K8s investigation adapter based on configuration.

        Returns None when K8s investigation is disabled.
        Injects MCP client toolsets from ``MCP_SERVERS`` and
        ``K8S_MCP_SERVER_URL`` settings when available.

        :param agent_runner: Callable that executes the K8s investigator
            agent — injected by the caller to avoid importing interfaces.
        :returns: A K8sInvestigationAdapter or None.
        """
        backend = self.settings.k8s_investigation_backend
        if not backend:
            return None

        if backend in ("native", "both"):
            # Shared MCP servers from MCP_SERVERS (memoised).
            mcp_toolsets = list(self.build_mcp_toolsets())

            # K8S_MCP_SERVER_URL is a K8s-specific extra, appended after the
            # shared tuple to avoid polluting the shared cache.
            if self.settings.k8s_mcp_server_url:
                mcp_toolsets.append(MCPServerSSE(url=self.settings.k8s_mcp_server_url))

            return k8s_native_agent.NativeK8sAgent(
                k8s_client=self._build_k8s_client(),
                model_name=self._normalise_model_name(self.settings.k8s_investigator_llm),
                mcp_toolsets=tuple(mcp_toolsets),
                agent_runner=agent_runner,
            )

        return None  # kagent adapter wired separately

    def build_challenger_adapter(
        self,
    ) -> adapters.BaseInvestigationAdapter | None:
        """
        Build the challenger adapter for A/B comparison mode.

        Returns None when ``CHALLENGER_ADAPTER`` is unset or empty.
        Supported values: ``"native_k8s"``, ``"kagent"``.

        :returns: A BaseInvestigationAdapter or None when disabled.
        """
        backend = self.settings.challenger_adapter
        if not backend:
            return None

        if backend == "kagent":
            return kagent_adapter_mod.KagentAdapter(
                k8s_api_client=self._build_k8s_custom_objects_api(),
                kagent_namespace=self.settings.kagent_namespace,
                timeout_seconds=self.settings.kagent_investigation_timeout_seconds,
            )

        if backend == "native_k8s":
            return k8s_native_agent.NativeK8sAgent(
                k8s_client=self._build_k8s_client(),
                model_name=self._normalise_model_name(self.settings.k8s_investigator_llm),
            )

        logger.warning(
            "Unknown challenger adapter",
            challenger_adapter=backend,
        )
        return None

    # -- Agent registry ------------------------------------------------------

    def _build_agent_model(self, model_name: str | None) -> PydanticAIModel | str | None:
        """
        Return the model handle each PydanticAI agent factory feeds into ``Agent(...)``.

        When the firm-shared LiteLLM proxy is configured (RFC §2.4), returns
        an :class:`OpenAIChatModel` whose :class:`LiteLLMProvider` points at
        the proxy URL with the operator's virtual key. Otherwise returns
        ``model_name`` unchanged so PydanticAI follows its existing
        in-process LiteLLM SDK path — keeping ``just run-api`` working
        without a proxy.

        Special cases preserved verbatim from the previous
        ``interfaces/graphs/agents/utils.resolve_agent_model`` location so
        the placeholder paths unit tests rely on still fire:

        * ``None`` -> ``None`` so the factory's ``"test"`` fallback applies.
        * ``"test"`` -> ``"test"`` for the same reason — production never
          passes the placeholder; unit tests do.

        :param model_name: The model identifier resolved by
            :func:`sentinel.config._normalise_model_name`
            (e.g. ``"litellm:openai/gpt-4.1-mini"``), ``None``, or
            ``"test"``.
        """
        if model_name is None or model_name == "test":
            return model_name

        proxy_kwargs = litellm_proxy.get_proxy_kwargs()
        if proxy_kwargs is None:
            return model_name
        # Strip the ``litellm:`` prefix when present — ``LiteLLMProvider``
        # already sets ``system="litellm"`` on the resulting OpenAIChatModel,
        # and the bare ``provider/model`` form is what its API expects.
        bare_model_name = model_name.removeprefix("litellm:")
        provider = LiteLLMProvider(**proxy_kwargs)
        return OpenAIChatModel(bare_model_name, provider=provider)

    def load_agents(self, *, agent_module: Any) -> None:
        """
        Build every pipeline agent with its configured skills baked in.

        The ``agent_module`` namespace is injected by the caller so this
        module never imports from ``interfaces``.  Each sub-attribute
        (e.g. ``agent_module.alert_classifier``) must expose a
        ``build_agent(*, model, skills)`` factory.

        :param agent_module: The ``sentinel.interfaces.graphs.agents``
            module (or a compatible stub for testing).
        :raises sentinel.domain.skills.SkillNotFoundError: if any
            ``SKILLS_BY_AGENT`` entry names a skill that is not installed
            on disk under ``src/sentinel/domain/skills/``.
        """
        agents = {
            "alert_classifier": agent_module.alert_classifier.build_agent(
                model=self._build_agent_model(self.classifier_model),
                skills=base_config_mod.SKILLS_BY_AGENT.get("alert_classifier", ()),
            ),
            "root_cause_analyser": agent_module.root_cause_analyser.build_agent(
                model=self._build_agent_model(self.analyser_model),
                skills=base_config_mod.SKILLS_BY_AGENT.get("root_cause_analyser", ()),
            ),
            "ticket_reviewer": agent_module.ticket_reviewer.build_agent(
                model=self._build_agent_model(self.reviewer_model),
                skills=base_config_mod.SKILLS_BY_AGENT.get("ticket_reviewer", ()),
            ),
            "response_drafter": agent_module.response_drafter.build_agent(
                model=self._build_agent_model(self.drafter_model),
                skills=base_config_mod.SKILLS_BY_AGENT.get("response_drafter", ()),
            ),
            "chart_generator": agent_module.chart_generator.build_agent(
                model=self.chart_generator_model,
                skills=base_config_mod.SKILLS_BY_AGENT.get("chart_generator", ()),
            ),
            "chart_request_parser": agent_module.chart_request_parser.build_agent(
                model=self.chart_parser_model,
                skills=base_config_mod.SKILLS_BY_AGENT.get("chart_request_parser", ()),
            ),
            "intent_router": agent_module.intent_router.build_agent(
                model=self.intent_router_model,
                skills=base_config_mod.SKILLS_BY_AGENT.get("intent_router", ()),
            ),
            "k8s_investigator": agent_module.k8s_investigator.build_agent(
                model=self._build_agent_model(self.k8s_investigator_model),
                skills=base_config_mod.SKILLS_BY_AGENT.get("k8s_investigator", ()),
            ),
        }
        if self.enable_replay_bundle:
            agents = {
                name: self._wrap_agent_for_capture(agent=agent, agent_name=name)
                for name, agent in agents.items()
            }
        self.set_agents(agents)
        logger.info("Agents loaded", count=len(agents))

    @staticmethod
    def _wrap_agent_for_capture(*, agent: Any, agent_name: str) -> Any:
        """
        Replace *agent*'s default model with a :class:`CapturingModel` for replay capture.

        PydanticAI ``Agent`` instances expose ``.model`` as either a
        :class:`pydantic_ai.models.Model` instance, a known model name
        string, or ``None``.  We resolve the current value to a concrete
        ``Model`` via :func:`pydantic_ai.models.infer_model`, wrap it in a
        :class:`~sentinel.plugins.models.capturing.CapturingModel` tagged
        with *agent_name*, and reassign it back through the public
        ``.model`` setter — leaving every other agent attribute (skills,
        tools, prompts, output_type) untouched.

        Non-Agent objects (e.g. ``mock.Mock`` test stubs) and agents whose
        ``.model`` cannot be resolved are returned unchanged.  This keeps
        the wrapping wholly opt-in: tests that don't care about replay
        capture keep working without ceremony.
        """
        try:
            current_model = agent.model
        except AttributeError:
            return agent
        if current_model is None:
            return agent
        try:
            resolved = pydantic_ai_models.infer_model(current_model)
        except Exception as exc:
            logs.log_event(
                "agent_capture_wrap_skipped",
                params={
                    "agent_name": agent_name,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return agent
        try:
            agent.model = capturing_model_mod.CapturingModel(
                wrapped=resolved,
                agent_name=agent_name,
            )
        except (AttributeError, TypeError) as exc:
            logs.log_event(
                "agent_capture_wrap_skipped",
                params={
                    "agent_name": agent_name,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return agent
        logs.log_event("agent_capture_wrap_attached", params={"agent_name": agent_name})
        return agent

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

    def build_observability_toolset(self, *, service_name: str = "") -> AbstractToolset[object]:
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

    def build_support_search_toolset(self) -> AbstractToolset[object]:
        """
        Build a read-only search toolset for the response drafter agent.

        Tools include documentation search and past-ticket resolution lookup.
        """
        return doc_toolsets.build_support_search_toolset(
            document_searcher=self.build_document_searcher(),
            ticket_searcher=self.build_ticket_searcher(),
        )

    def build_ticket_triage_toolset(self) -> AbstractToolset[object]:
        """
        Build a read-only toolset for the ticket reviewer agent.

        Tools include duplicate/similar ticket detection.
        """
        return doc_toolsets.build_ticket_triage_toolset(
            ticket_searcher=self.build_ticket_searcher(),
        )
