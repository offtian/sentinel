"""
Centralised configuration via Pydantic Settings.

All tuneable parameters in one place, overridable via environment variables.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = _PACKAGE_ROOT.parent.parent  # src/sentinel -> src -> repo root
PLUGINS_DIR = _PACKAGE_ROOT / "plugins"
PROMPTS_DIR = PLUGINS_DIR / "prompts"


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
    sre_auto_investigate: bool = True
    sre_slack_channel: str = ""

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
    support_auto_draft: bool = True
    support_slack_channel: str = ""
    # Options: "confluence" | "mock" | "bedrock_knowledge_base" (future)
    document_searcher: str = "bedrock_knowledge_base"


class LLMSettings(BaseSettings):
    """LLM gateway and model routing settings."""

    ai_gateway_url: str = "http://localhost:11434"
    intent_router_llm: str = "ollama/qwen3:8b"
    alert_classifier_llm: str = "ollama/qwen3:8b"
    root_cause_llm: str = "ollama/qwen3:8b"
    ticket_reviewer_llm: str = "ollama/qwen3:8b"
    response_drafter_llm: str = "ollama/qwen3:8b"


class Settings(LLMSettings, SRESettings, K8sChartSettings, SupportSettings):
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
    otel_service_name: str = "sentinel"
    worker_metrics_port: int = 8001


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached settings singleton, creating it on first call."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings
