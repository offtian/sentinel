from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.charts import entities as chart_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.investigations import adapters, holmes_adapter
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.runbooks import models as runbook_models
from sentinel.domain.support import entities as support_entities


_FIXED_ENVELOPE_REQUEST_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def make_envelope(
    *,
    request_id: uuid.UUID | None = None,
    tenant_id: str = "pm-default",
    cluster_id: str = "dev-eu-west-1",
    region: str = "eu-west-1",
    pii_class: envelope_mod.PIIClass = "internal",
    received_at: datetime | None = None,
) -> envelope_mod.Envelope:
    if request_id is None:
        request_id = _FIXED_ENVELOPE_REQUEST_ID
    if received_at is None:
        received_at = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    return envelope_mod.Envelope(
        request_id=request_id,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        region=region,
        pii_class=pii_class,
        received_at=received_at,
    )


def make_alert(
    *,
    alert_id: str = "P123ABC",
    source: str = "pagerduty",
    title: str = "High CPU usage on web-01",
    description: str = "CPU usage exceeded 90% for 5 minutes",
    severity: alert_entities.AlertSeverity = alert_entities.AlertSeverity.HIGH,
    service: str = "api-service",
    triggered_at: datetime | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> alert_entities.Alert:
    return alert_entities.Alert(
        id=alert_id,
        source=source,  # type: ignore[arg-type]
        title=title,
        description=description,
        severity=severity,
        service=service,
        triggered_at=triggered_at or datetime(2024, 1, 1, tzinfo=UTC),
        raw_payload=raw_payload or {},
    )


def make_finding(
    *,
    source: str = "datadog_logs",
    summary: str = "Error rate increased 5x in last 10 minutes",
    relevance: float = 0.9,
    raw_data: str | None = None,
) -> investigation_entities.Finding:
    return investigation_entities.Finding(
        source=source,
        summary=summary,
        relevance=relevance,
        raw_data=raw_data,
    )


def make_investigation(
    *,
    alert: alert_entities.Alert | None = None,
    status: investigation_entities.InvestigationStatus = investigation_entities.InvestigationStatus.PENDING,
    findings: list[investigation_entities.Finding] | None = None,
    root_cause: str | None = None,
    remediation: str | None = None,
    confidence_score: float | None = None,
) -> investigation_entities.Investigation:
    return investigation_entities.Investigation(
        alert=alert or make_alert(),
        status=status,
        findings=findings or [],
        root_cause=root_cause,
        remediation=remediation,
        confidence_score=confidence_score,
    )


def make_ticket(
    *,
    ticket_id: str = "10001",
    key: str = "SUPPORT-42",
    summary: str = "Cannot log in to dashboard",
    description: str = "I've been unable to log in since yesterday morning.",
    reporter: str = "Jane Doe",
    priority: str = "High",
    created_at: datetime | None = None,
    labels: list[str] | None = None,
    comments: list[support_entities.TicketComment] | None = None,
) -> support_entities.Ticket:
    return support_entities.Ticket(
        id=ticket_id,
        key=key,
        summary=summary,
        description=description,
        reporter=reporter,
        priority=priority,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
        labels=labels or [],
        comments=comments or [],
    )


def make_doc_source(
    *,
    title: str = "Login Troubleshooting Guide",
    url: str = "https://docs.example.com/login",
    source_type: str = "confluence",
    excerpt: str = "To reset your password, visit the account portal...",
    relevance: float = 0.9,
) -> support_entities.DocSource:
    return support_entities.DocSource(
        title=title,
        url=url,
        source_type=source_type,  # type: ignore[arg-type]
        excerpt=excerpt,
        relevance=relevance,
    )


def make_response_suggestion(
    *,
    ticket_id: str = "10001",
    suggested_response: str = "Based on our docs, you can reset your password at /account/reset.",
    sources: list[support_entities.DocSource] | None = None,
    confidence_score: float | None = 0.85,
    category: str | None = "account",
) -> support_entities.ResponseSuggestion:
    return support_entities.ResponseSuggestion(
        ticket_id=ticket_id,
        suggested_response=suggested_response,
        sources=sources or [make_doc_source()],
        confidence_score=confidence_score,
        category=category,
    )


def make_confidence_score(
    *,
    total: float = 0.8,
) -> confidence_entities.ConfidenceScore:
    return confidence_entities.ConfidenceScore.from_total(total)


class MockHolmesAdapter(holmes_adapter.BaseHolmesAdapter):
    """Mock adapter for testing."""

    def __init__(self, *, result: holmes_adapter.HolmesInvestigationResult | None = None) -> None:
        self._result = result or holmes_adapter.HolmesInvestigationResult(
            analysis="Mock investigation: no issues found.",
            tool_calls=[
                {"tool": "datadog_query_logs", "result": "No errors in last 30 minutes"},
                {"tool": "kubernetes_get_pods", "result": "All pods healthy"},
            ],
            sources_queried=["datadog_logs", "kubernetes"],
        )

    @property
    def is_configured(self) -> bool:
        return True

    async def investigate(
        self,
        *,
        alert: alert_entities.Alert,
        context: holmes_adapter.adapters.InvestigationContext | None = None,
    ) -> holmes_adapter.HolmesInvestigationResult:
        return self._result


def make_chart_request(
    *,
    requester: str = "alice",
    team: str = "platform",
    raw_message: str = "Deploy a Python web service called api-gateway on port 8080 with 256Mi memory",
    requested_at: datetime | None = None,
) -> chart_entities.ChartRequest:
    return chart_entities.ChartRequest(
        requester=requester,
        team=team,
        raw_message=raw_message,
        requested_at=requested_at or datetime(2026, 4, 1, tzinfo=UTC),
    )


def make_chart_spec(
    *,
    service_name: str = "api-gateway",
    image: str = "myrepo/api-gateway:latest",
    ports: tuple[chart_entities.PortSpec, ...] = (
        chart_entities.PortSpec(container_port=8080, name="http"),
    ),
    replicas: chart_entities.ReplicaSpec | None = None,
    resources: chart_entities.ResourceSpec | None = None,
    run_as_non_root: bool = True,
    env_vars: tuple[chart_entities.EnvVarSpec, ...] = (),
    dependencies: tuple[chart_entities.DependencySpec, ...] = (),
    extra_resources: tuple[str, ...] = (),
) -> chart_entities.ChartSpec:
    return chart_entities.ChartSpec(
        service_name=service_name,
        image=image,
        ports=ports,
        replicas=replicas or chart_entities.ReplicaSpec(min_replicas=2, max_replicas=5),
        resources=resources
        or chart_entities.ResourceSpec(
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="256Mi",
        ),
        run_as_non_root=run_as_non_root,
        env_vars=env_vars,
        dependencies=dependencies,
        extra_resources=extra_resources,
    )


def make_team_policy(
    *,
    team: str = "platform",
    namespace: str = "platform-prod",
    max_memory: str = "2Gi",
    max_cpu: str = "2000m",
    max_replicas: int = 10,
    require_network_policy: bool = True,
    require_non_root: bool = True,
    allowed_egress: tuple[chart_entities.EgressRule, ...] = (),
    default_labels: dict[str, str] | None = None,
) -> chart_entities.TeamPolicy:
    return chart_entities.TeamPolicy(
        team=team,
        namespace=namespace,
        max_memory=max_memory,
        max_cpu=max_cpu,
        max_replicas=max_replicas,
        require_network_policy=require_network_policy,
        require_non_root=require_non_root,
        allowed_egress=allowed_egress,
        default_labels=default_labels or {"team": team},
    )


def make_generated_file(
    *,
    path: str = "templates/deployment.yaml",
    content: str = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
) -> chart_entities.GeneratedFile:
    return chart_entities.GeneratedFile(path=path, content=content)


def make_audit_entry(
    *,
    adapter_name: str = "native_k8s",
    action: str = "tool_call",
    tool_name: str | None = "get_pod_status",
    status: str = "success",
    duration_ms: int = 42,
    error_code: str | None = None,
    payload: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> adapters.AuditEntry:
    return adapters.AuditEntry(
        timestamp=timestamp or datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        adapter_name=adapter_name,
        action=action,
        tool_name=tool_name,
        status=status,
        duration_ms=duration_ms,
        error_code=error_code,
        payload=payload or {},
    )


def make_investigation_result(
    *,
    findings: tuple[investigation_entities.Finding, ...] | None = None,
    sources_queried: tuple[str, ...] = ("kubernetes", "datadog_logs"),
    duration_ms: int = 350,
    adapter_name: str = "native_k8s",
    audit_trail: tuple[adapters.AuditEntry, ...] | None = None,
) -> adapters.InvestigationResult:
    return adapters.InvestigationResult(
        findings=findings
        or (make_finding(source="kubernetes", summary="Pod restarting due to OOMKilled"),),
        sources_queried=sources_queried,
        duration_ms=duration_ms,
        adapter_name=adapter_name,
        audit_trail=audit_trail or (make_audit_entry(adapter_name=adapter_name),),
    )


class MockKagentAdapter(adapters.K8sInvestigationAdapter):
    """Mock kagent adapter for testing comparison mode."""

    def __init__(
        self,
        *,
        findings: tuple[investigation_entities.Finding, ...] = (),
        delay_ms: int = 0,
    ) -> None:
        self._findings = findings or (
            make_finding(source="kagent", summary="CRD investigation: pod OOMKilled"),
        )
        self._delay_ms = delay_ms

    @property
    def is_configured(self) -> bool:
        return True

    async def investigate(
        self,
        *,
        alert: alert_entities.Alert,
        context: adapters.InvestigationContext | None = None,
    ) -> adapters.InvestigationResult:
        if self._delay_ms > 0:
            await asyncio.sleep(self._delay_ms / 1000)
        return adapters.InvestigationResult(
            findings=self._findings,
            sources_queried=("kagent_crd",),
            duration_ms=self._delay_ms or 200,
            adapter_name="kagent",
            audit_trail=(
                make_audit_entry(
                    adapter_name="kagent",
                    action="crd_operation",
                    tool_name=None,
                    duration_ms=self._delay_ms or 200,
                ),
            ),
        )


def make_validation_result(
    *,
    helm_template_ok: bool = True,
    kubeconform_ok: bool = True,
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> chart_entities.ValidationResult:
    return chart_entities.ValidationResult(
        helm_template_ok=helm_template_ok,
        kubeconform_ok=kubeconform_ok,
        errors=errors,
        warnings=warnings,
    )


def make_runbook(
    *,
    runbook_id: str = "k8s-crashloop",
    description: str = "CrashLoop investigation runbook",
    content_sha: str = "deadbeef" * 4,
    allowed_tools: tuple[str, ...] = ("k8s_get_pod_logs", "k8s_get_events"),
    denied_tools: tuple[str, ...] = (),
    max_total_tool_calls: int = 20,
    max_loop_iterations: int = 10,
    tool_max_calls: int = 5,
    body: str = "Investigate the crashlooping pod.",
    mnpi_safe: bool = True,
) -> runbook_models.Runbook:
    metadata = runbook_models.RunbookMetadata(
        runbook_id=runbook_id,
        description=description,
        content_sha=content_sha,
        applies_to=runbook_models.RunbookAppliesTo(
            alertnames=("KubePodCrashLooping",),
            severity_min="warning",
            resource_kinds=("Pod",),
            exclude_labels={},
        ),
        tags=(runbook_models.RunbookTag(key="category", value="k8s"),),
        min_match_score=2,
        owner="sre-team",
        authors=("test",),
        last_validated=None,
        deprecated_at=None,
        superseded_by=None,
        mnpi_safe=mnpi_safe,
        canonical_sources=(),
    )
    tools = runbook_models.ToolsConfig(
        allowed_tools=tuple(
            runbook_models.ToolSpec(name=n, max_calls=tool_max_calls) for n in allowed_tools
        ),
        denied_tools=denied_tools,
        max_total_tool_calls=max_total_tool_calls,
        max_loop_iterations=max_loop_iterations,
    )
    checks = runbook_models.ChecksConfig(
        prescribed_checks=(),
        groundedness_rules=(),
        body_sanitization=runbook_models.BodySanitizationConfig(),
    )
    return runbook_models.Runbook(
        metadata=metadata,
        body=body,
        tools=tools,
        checks=checks,
        tests=(),
        directory=Path("/var/runbooks") / runbook_id,
    )


def make_chart_output(
    *,
    service_name: str = "api-gateway",
    files: tuple[chart_entities.GeneratedFile, ...] | None = None,
    validation_result: chart_entities.ValidationResult | None = None,
    policy_violations: tuple[chart_entities.PolicyViolation, ...] = (),
    generation_attempts: int = 1,
    confidence_score: float | None = None,
) -> chart_entities.ChartOutput:
    return chart_entities.ChartOutput(
        service_name=service_name,
        files=files or (make_generated_file(),),
        validation_result=validation_result,
        policy_violations=policy_violations,
        generation_attempts=generation_attempts,
        confidence_score=confidence_score,
    )
