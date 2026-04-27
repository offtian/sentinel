"""
Centralised configuration via Pydantic Settings.

All tuneable parameters in one place, overridable via environment variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = _PACKAGE_ROOT.parent.parent  # src/sentinel -> src -> repo root
PLUGINS_DIR = _PACKAGE_ROOT / "plugins"
DOMAIN_DIR = _PACKAGE_ROOT / "domain"
PROMPTS_DIR = DOMAIN_DIR / "prompts"


class SRESettings(BaseSettings):
    """SRE-specific vendor and feature settings."""

    # Observability backend: "datadog" | "grafana" | "" (auto: grafana for localdev, datadog otherwise)
    observability_backend: str = ""

    # Datadog credentials (when observability_backend = "datadog")
    pagerduty_api_key: str = ""
    datadog_api_key: str = ""
    datadog_app_key: str = ""

    # Grafana credentials (when observability_backend = "grafana")
    grafana_url: str = ""
    grafana_api_token: str = ""
    grafana_prometheus_datasource_uid: str = ""
    grafana_loki_datasource_uid: str = ""
    grafana_tempo_datasource_uid: str = ""

    holmesgpt_enabled: bool = True
    # Investigation backend: "direct" (default — queries vendors directly) or
    # "sdk" (uses HolmesGPT SDK ToolCallingLLM with built-in toolsets).
    holmes_backend: str = "sdk"
    # LLM model used by HolmesGPT SDK when holmes_backend="sdk".
    holmes_sdk_model: str = "openai/gpt-4.1"
    sre_auto_investigate: bool = True
    sre_slack_channel: str = ""

    # F6.L drift-detection cron — fallback Slack channel for runbook drift
    # alerts when the runbook frontmatter ``owner`` does not map to a
    # team-specific channel. Empty string disables the fallback so drift on
    # unowned runbooks is logged only.
    runbook_owners_channel: str = "#sre-runbook-owners"

    # Approval gate: investigations below this confidence threshold require human approval.
    # Set to 0.0 to disable (all findings auto-publish).
    require_approval_below_confidence: float = 0.7
    # Seconds before a pending approval auto-approves (0 = never auto-approve).
    approval_timeout_seconds: int = 0

    # K8s investigation agent
    k8s_investigation_backend: str = ""  # "native", "kagent", "both", or "" (disabled)
    k8s_investigator_llm: str = "ollama/qwen3-coder:30b"
    k8s_cluster_name: str = ""
    k8s_default_namespace: str = ""

    # Kagent
    kagent_investigation_timeout_seconds: int = 120
    kagent_namespace: str = "kagent-system"

    # Challenger adapter for A/B comparison mode.
    # Values: "" (disabled), "native_k8s", "kagent"
    challenger_adapter: str = ""

    # MCP
    mcp_servers: str = ""  # JSON list: [{"name": "...", "url": "..."}, ...]
    k8s_mcp_server_url: str = ""
    mcp_server_port: int = 8811
    mcp_server_api_key: str = ""


class K8sChartSettings(BaseSettings):
    """K8s chart coding agent settings."""

    k8s_chart_generator_llm: str = "ollama/qwen3-coder:30b"
    k8s_chart_parser_llm: str = "ollama/qwen3:8b"
    k8s_chart_auto_validate: bool = False
    k8s_chart_auto_sandbox: bool = False
    k8s_chart_sandbox_context: str = ""
    k8s_chart_max_retries: int = 3


class SupportSettings(BaseSettings):
    """Support-specific vendor and feature settings."""

    jira_base_url: str = ""
    jira_api_token: str = ""
    jira_user_email: str = ""
    jira_project_keys: str = ""
    confluence_base_url: str = ""
    confluence_space_keys: str = ""
    notion_token: str = ""
    support_auto_draft: bool = True
    support_slack_channel: str = ""
    # Options: "confluence" | "mock" | "bedrock_knowledge_base" (future)
    document_searcher: str = "bedrock_knowledge_base"


class ConfluencePublishSettings(BaseSettings):
    """
    F6.N — Confluence write-side PR-bot configuration.

    Confluence is a *read-only consumer* of the on-disk runbook
    catalog. The publish script (``scripts/runbook_confluence_publish``)
    no-ops cleanly when these are empty so CI doesn't fail on
    deployments that haven't wired Confluence yet. Once wired, the
    fields combine with ``confluence_user`` to provide HTTP Basic auth
    against the Confluence Cloud REST API.

    ``confluence_base_url`` here **shadows** the read-side
    ``SupportSettings.confluence_base_url`` field intentionally — same
    instance, same URL, but the write-side script reads via the
    publish-specific names so the two flows can diverge later (e.g.
    different space keys per pipeline) without breaking each other.
    """

    confluence_user: str = ""
    confluence_token: SecretStr | None = None
    confluence_space_key: str = ""
    confluence_parent_page_id: str = ""


class LLMSettings(BaseSettings):
    """LLM model routing settings."""

    ollama_base_url: str = "http://localhost:11434"
    intent_router_llm: str = "ollama/qwen3:8b"
    alert_classifier_llm: str = "ollama/qwen3:8b"
    root_cause_llm: str = "ollama/qwen3:8b"
    ticket_reviewer_llm: str = "ollama/qwen3:8b"
    response_drafter_llm: str = "ollama/qwen3:8b"
    # Stage 2 disambiguator (F6 runbook matcher). Empty string falls back to
    # alert_classifier_llm at config-resolve time so a separate small model can
    # be wired without setting it explicitly.
    runbook_disambiguator_llm: str = ""
    # Stage 3 RAG fallback (F6.J). Disabled by default — opt-in per environment.
    # When True, a no-match Stage 2B result triggers pgvector retrieval against
    # pre-indexed runbook embeddings. When False, the matcher short-circuits to
    # the no-match result without any embedder I/O.
    runbook_rag_fallback_enabled: bool = False
    # Embedder model for the F6.J Stage 3 path. Provider/model format; same
    # convention as the other LLM knobs. Defaults to OpenAI's
    # text-embedding-3-small (1536-d) which matches the runbook_embeddings
    # column dimension lock.
    runbook_embedder_llm: str = "openai/text-embedding-3-small"
    # Cosine-similarity threshold below which Stage 3 candidates are dropped.
    # Tuned defensively at 0.78 — the matcher prefers a no-match over a noisy
    # near-miss because the generic playbook + approval gate are the safer
    # downstream path.
    runbook_rag_min_similarity: float = 0.78
    # Top-k retrieval depth for Stage 3. Five gives enough recall to write a
    # useful evidence trail without inflating the audit table.
    runbook_rag_top_k: int = 5


class Settings(
    LLMSettings,
    SRESettings,
    K8sChartSettings,
    SupportSettings,
    ConfluencePublishSettings,
):
    """
    Application-wide settings, composed from domain-specific base classes.

    Every field is overridable via environment variables matching
    the upper-cased field name (e.g. ``DATABASE_URL``, ``WORKER_JOB_TIMEOUT``).
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    environment: str = "production"
    database_url: str = ""

    # LangGraph checkpointer DSN. Unset = defaults to ``database_url`` (after
    # stripping the SQLAlchemy ``+asyncpg`` driver suffix) at bootstrap time.
    # The ``langgraph-checkpoint-postgres`` saver speaks plain libpq, so the
    # SQLAlchemy-flavoured URL is not directly usable here.
    langgraph_checkpoint_dsn: str | None = None

    # Cloud / cluster identity context (RFC §3.1) — populated onto the
    # Envelope so every span and DB row carries cluster + region scope.
    # Empty values fall back to the "unknown" sentinel at envelope time.
    region: str = ""

    team_profile: Literal["sre", "devops", "ace"] = "sre"

    # Unset = in-process LiteLLM SDK fallback.
    litellm_base_url: HttpUrl | None = None
    litellm_virtual_key: SecretStr | None = None

    # Unset = OTel console exporter fallback.
    langfuse_host: HttpUrl | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None

    otel_collector_endpoint: HttpUrl | None = None
    runbooks_root: Path = _PACKAGE_ROOT / "domain" / "runbooks"

    @field_validator(
        "litellm_base_url",
        "langfuse_host",
        "otel_collector_endpoint",
        "litellm_virtual_key",
        "langfuse_public_key",
        "langfuse_secret_key",
        "confluence_token",
        mode="before",
    )
    @classmethod
    def _empty_string_to_none(cls, value: object) -> object:
        # Why: .env ships these knobs commented-as-empty (LITELLM_BASE_URL=)
        # which pydantic-settings reads as "" and HttpUrl/SecretStr validation
        # rejects. Treat empty string as "unset" so optional URL/secret fields
        # follow their None-default fallback path.
        if isinstance(value, str) and value == "":
            return None
        return value

    @property
    def is_local(self) -> bool:
        """Return True when running in local development (docker-compose or local K8s)."""
        return self.environment == "localdev"

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_signing_secret: str = ""

    # Worker
    worker_poll_interval: float = 1.0
    worker_job_timeout: int = 300
    worker_max_retries: int = 3

    # Observability
    dd_service: str = "sentinel"
    dd_env: str = "production"
    otel_metrics_enabled: bool = True
    otel_traces_enabled: bool = True
    otel_traces_endpoint: str = ""
    otel_service_name: str = "sentinel"
    worker_metrics_port: int = 8001


settings = Settings()
