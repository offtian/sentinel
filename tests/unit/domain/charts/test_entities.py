from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.charts import entities


class TestPortSpec:
    def test_creates_with_defaults(self):
        # Given a port spec with only required fields
        port = entities.PortSpec(container_port=8080)

        # Then defaults are set
        assert port.container_port == 8080
        assert port.protocol == "TCP"
        assert port.name == ""


class TestResourceSpec:
    def test_creates_with_all_fields(self):
        # Given resource requests and limits
        spec = entities.ResourceSpec(
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="512Mi",
        )

        # Then all values are stored
        assert spec.cpu_request == "100m"
        assert spec.memory_limit == "512Mi"


class TestChartRequest:
    def test_creates_with_required_fields(self):
        # Given a chart request with all required fields
        now = datetime(2026, 4, 3, tzinfo=UTC)
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="Deploy a Python web service called api-gateway on port 8080",
            requested_at=now,
        )

        # Then fields are set
        assert request.requester == "alice"
        assert request.team == "platform"
        assert request.raw_message.startswith("Deploy")
        assert request.requested_at == now


class TestChartSpec:
    def test_creates_with_minimal_fields(self):
        # Given a chart spec with only required fields
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="nginx:latest",
        )

        # Then defaults are populated
        assert spec.service_name == "api-gateway"
        assert spec.image == "nginx:latest"
        assert spec.ports == ()
        assert spec.replicas is None
        assert spec.resources is None
        assert spec.run_as_non_root is True
        assert spec.env_vars == ()
        assert spec.dependencies == ()
        assert spec.extra_resources == ()

    def test_creates_with_all_fields(self):
        # Given a fully specified chart spec
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:v1.2.3",
            ports=(entities.PortSpec(container_port=8080, name="http"),),
            replicas=entities.ReplicaSpec(min_replicas=2, max_replicas=5),
            resources=entities.ResourceSpec(
                cpu_request="100m",
                cpu_limit="500m",
                memory_request="128Mi",
                memory_limit="512Mi",
            ),
            run_as_non_root=True,
            env_vars=(entities.EnvVarSpec(name="LOG_LEVEL", value="info"),),
            dependencies=(entities.DependencySpec(name="redis", port=6379),),
            extra_resources=("NetworkPolicy", "PodDisruptionBudget"),
        )

        # Then all values are stored
        assert len(spec.ports) == 1
        assert spec.replicas.max_replicas == 5
        assert spec.resources.cpu_limit == "500m"
        assert spec.env_vars[0].name == "LOG_LEVEL"
        assert spec.dependencies[0].name == "redis"
        assert "NetworkPolicy" in spec.extra_resources


class TestTeamPolicy:
    def test_creates_with_all_fields(self):
        # Given a team policy
        policy = entities.TeamPolicy(
            team="platform",
            namespace="platform-prod",
            max_memory="2Gi",
            max_cpu="2000m",
            max_replicas=10,
            require_network_policy=True,
            require_non_root=True,
            allowed_egress=(entities.EgressRule(host="redis.internal", port=6379),),
            default_labels={"team": "platform", "env": "production"},
        )

        # Then all values are stored
        assert policy.team == "platform"
        assert policy.max_replicas == 10
        assert policy.require_non_root is True
        assert len(policy.allowed_egress) == 1
        assert policy.default_labels["team"] == "platform"


class TestPolicyViolation:
    def test_creates_with_all_fields(self):
        # Given a policy violation
        violation = entities.PolicyViolation(
            field="memory_limit",
            requested="4Gi",
            allowed="2Gi",
            message="Memory limit exceeds team maximum of 2Gi",
        )

        # Then fields are set
        assert violation.field == "memory_limit"
        assert violation.requested == "4Gi"


class TestGeneratedFile:
    def test_creates_with_path_and_content(self):
        # Given a generated file
        gf = entities.GeneratedFile(
            path="templates/deployment.yaml",
            content="apiVersion: apps/v1\nkind: Deployment",
        )

        # Then fields are set
        assert gf.path == "templates/deployment.yaml"
        assert "Deployment" in gf.content


class TestValidationResult:
    def test_creates_passing_result(self):
        # Given a passing validation
        result = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
        )

        # Then it passes and has no errors
        assert result.helm_template_ok is True
        assert result.kubeconform_ok is True
        assert result.errors == ()
        assert result.warnings == ()

    def test_creates_failing_result(self):
        # Given a failing validation with errors
        result = entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=("template rendering failed: missing required field 'image'",),
            warnings=("deprecated API version apps/v1beta1",),
        )

        # Then errors and warnings are captured
        assert result.helm_template_ok is False
        assert len(result.errors) == 1
        assert len(result.warnings) == 1


class TestChartOutput:
    def test_creates_with_required_fields(self):
        # Given a chart output
        output = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
            ),
        )

        # Then defaults are set
        assert output.service_name == "api-gateway"
        assert len(output.files) == 1
        assert output.validation_result is None
        assert output.policy_violations == ()
        assert output.generation_attempts == 1
        assert output.confidence_score is None
