"""
Domain entities for the K8s chart coding agent.

These Pydantic models represent the data flowing through the chart generation
pipeline — from raw user request to validated Helm chart output.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PortSpec(BaseModel):
    container_port: int
    protocol: str = "TCP"
    name: str = ""


class ResourceSpec(BaseModel):
    cpu_request: str = ""
    cpu_limit: str = ""
    memory_request: str = ""
    memory_limit: str = ""


class ReplicaSpec(BaseModel):
    min_replicas: int = 1
    max_replicas: int = 3


class DependencySpec(BaseModel):
    name: str
    port: int = 0


class EnvVarSpec(BaseModel):
    name: str
    value: str = ""
    secret_ref: str = ""


class EgressRule(BaseModel):
    host: str
    port: int


class ChartRequest(BaseModel):
    requester: str
    team: str
    raw_message: str
    requested_at: datetime


class ChartSpec(BaseModel):
    service_name: str
    image: str
    ports: tuple[PortSpec, ...] = ()
    replicas: ReplicaSpec | None = None
    resources: ResourceSpec | None = None
    run_as_non_root: bool = True
    env_vars: tuple[EnvVarSpec, ...] = ()
    dependencies: tuple[DependencySpec, ...] = ()
    extra_resources: tuple[str, ...] = ()


class TeamPolicy(BaseModel):
    team: str
    namespace: str = ""
    max_memory: str = ""
    max_cpu: str = ""
    max_replicas: int = 0
    require_network_policy: bool = False
    require_non_root: bool = True
    allowed_egress: tuple[EgressRule, ...] = ()
    default_labels: dict[str, str] = Field(default_factory=dict)


class PolicyViolation(BaseModel):
    field: str
    requested: str
    allowed: str
    message: str


class GeneratedFile(BaseModel):
    path: str
    content: str


class ValidationResult(BaseModel):
    helm_template_ok: bool
    kubeconform_ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ChartOutput(BaseModel):
    service_name: str
    files: tuple[GeneratedFile, ...] = ()
    validation_result: ValidationResult | None = None
    policy_violations: tuple[PolicyViolation, ...] = ()
    generation_attempts: int = 1
    confidence_score: float | None = None
