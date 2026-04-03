from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from sentinel.application.charts import commit
from sentinel.domain.charts import entities


class TestWriteChartFiles:
    def test_writes_files_to_output_directory(self, tmp_path: Path):
        # Given a chart output with two files
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
                entities.GeneratedFile(
                    path="templates/service.yaml",
                    content="apiVersion: v1\nkind: Service",
                ),
                entities.GeneratedFile(
                    path="Chart.yaml",
                    content="apiVersion: v2\nname: api-gateway\nversion: 0.1.0",
                ),
            ),
        )

        # When writing files
        output_dir = commit.write_chart_files(chart=chart, gitops_root=tmp_path)

        # Then files are written to gitops_root/api-gateway/
        assert output_dir == tmp_path / "api-gateway"
        assert (output_dir / "templates" / "deployment.yaml").exists()
        assert (output_dir / "templates" / "service.yaml").exists()
        assert (output_dir / "Chart.yaml").exists()

        deployment_content = (output_dir / "templates" / "deployment.yaml").read_text()
        assert "Deployment" in deployment_content

    def test_overwrites_existing_files(self, tmp_path: Path):
        # Given existing files in the output directory
        existing_dir = tmp_path / "api-gateway" / "templates"
        existing_dir.mkdir(parents=True)
        (existing_dir / "deployment.yaml").write_text("old content")

        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="new content",
                ),
            ),
        )

        # When writing files
        commit.write_chart_files(chart=chart, gitops_root=tmp_path)

        # Then files are overwritten
        content = (tmp_path / "api-gateway" / "templates" / "deployment.yaml").read_text()
        assert content == "new content"


class TestCommitToGitOps:
    def test_calls_git_and_gh_commands(self, tmp_path: Path):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="Chart.yaml",
                    content="apiVersion: v2\nname: api-gateway",
                ),
            ),
        )

        # When committing to GitOps
        with mock.patch.object(commit, "_run_command") as mock_run:
            mock_run.return_value = (0, "https://github.com/org/repo/pull/42", "")

            asyncio.run(
                commit.commit_to_gitops(
                    chart=chart,
                    gitops_root=tmp_path,
                    branch_prefix="chart",
                )
            )

        # Then git commands were called and PR URL returned
        assert mock_run.call_count >= 1

    def test_raises_on_git_checkout_failure(self, tmp_path: Path):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="Chart.yaml",
                    content="apiVersion: v2\nname: api-gateway",
                ),
            ),
        )

        # When git checkout fails
        with mock.patch.object(commit, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "fatal: branch already exists")

            # Then GitOpsCommitError is raised
            with pytest.raises(commit.GitOpsCommitError, match="Branch creation failed"):
                asyncio.run(commit.commit_to_gitops(chart=chart, gitops_root=tmp_path))

    def test_raises_on_gh_pr_create_failure(self, tmp_path: Path):
        # Given a chart output where git succeeds but gh fails
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="Chart.yaml",
                    content="apiVersion: v2\nname: api-gateway",
                ),
            ),
        )

        # When gh pr create fails
        with mock.patch.object(commit, "_run_command") as mock_run:

            def side_effect(*args, **kwargs):
                if args[0] == "gh":
                    return (1, "", "gh auth login required")
                return (0, "ok", "")

            mock_run.side_effect = side_effect

            # Then GitOpsCommitError is raised with PR creation message
            with pytest.raises(commit.GitOpsCommitError, match="PR creation failed"):
                asyncio.run(commit.commit_to_gitops(chart=chart, gitops_root=tmp_path))
