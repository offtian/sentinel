from __future__ import annotations

from sentinel.plugins.toolsets import kubernetes as k8s_toolsets


class TestBuildKubernetesToolset:
    def test_returns_toolset_with_none_client(self) -> None:
        # Given no client (tools will no-op)
        # When building the toolset
        toolset = k8s_toolsets.build_kubernetes_toolset(
            k8s_client=None, namespace="production"
        )

        # Then the toolset is created successfully
        assert toolset is not None

    def test_builds_with_custom_namespace(self) -> None:
        # Given a custom namespace
        # When building the toolset
        toolset = k8s_toolsets.build_kubernetes_toolset(
            k8s_client=None, namespace="payments"
        )

        # Then the toolset is created successfully
        assert toolset is not None
