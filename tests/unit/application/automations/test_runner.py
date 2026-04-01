from __future__ import annotations

import pytest

from sentinel.application.automations import runner


class TestRunAutomation:
    async def test_run_registered_automation(self):
        # Given a registered automation name
        # When running it
        result = await runner.run_automation(
            automation_name="repo_health_check",
            params={"repos": ["sentinel"]},
        )

        # Then it returns a result dict
        assert result["automation"] == "repo_health_check"
        assert result["status"] == "completed"

    async def test_run_unknown_automation_raises(self):
        # Given an unknown automation name
        # When running it
        # Then it raises UnknownAutomationError
        with pytest.raises(runner.UnknownAutomationError, match="nonexistent"):
            await runner.run_automation(
                automation_name="nonexistent",
                params={},
            )


class TestListAutomations:
    def test_list_includes_repo_health_check(self):
        # Given the automation registry
        # When listing automations
        automations = runner.list_automations()

        # Then repo_health_check is included
        assert "repo_health_check" in automations

    def test_list_returns_sorted_names(self):
        # Given the automation registry
        # When listing automations
        automations = runner.list_automations()

        # Then the list is sorted
        assert automations == sorted(automations)
