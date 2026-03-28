from __future__ import annotations

from unittest.mock import MagicMock, patch

from sentinel.domain.vendor_adapters import jira


class TestJiraClientIsConfigured:
    def test_configured_when_all_fields_set(self):
        # Given a JiraClient with all fields provided
        # When checking is_configured
        client = jira.JiraClient(
            base_url="https://example.atlassian.net",
            api_token="token123",  # noqa: S106
            user_email="user@example.com",
        )

        # Then it reports as configured
        assert client.is_configured is True

    def test_not_configured_when_missing_token(self):
        # Given a JiraClient with no api_token
        # When checking is_configured
        client = jira.JiraClient(
            base_url="https://example.atlassian.net",
            api_token="",
            user_email="user@example.com",
        )

        # Then it reports as not configured
        assert client.is_configured is False

    def test_not_configured_when_all_defaults(self):
        # Given a JiraClient with no config (all env defaults empty)
        # When checking is_configured (assuming env vars not set)
        with patch("sentinel.domain.vendor_adapters.jira.get_settings") as mock_gs:
            mock_gs.return_value.jira_base_url = ""
            mock_gs.return_value.jira_api_token = ""
            mock_gs.return_value.jira_user_email = ""
            client = jira.JiraClient()

        # Then it reports as not configured
        assert client.is_configured is False


class TestJiraClientGetIssue:
    async def test_returns_none_when_not_configured(self):
        # Given an unconfigured JiraClient
        # When getting an issue
        client = jira.JiraClient(base_url="", api_token="", user_email="")
        result = await client.get_issue(issue_key="SUPPORT-42")

        # Then None is returned (skipped)
        assert result is None

    async def test_returns_issue_dict_when_configured(self):
        # Given a configured JiraClient with a mocked JIRA SDK response
        mock_issue = MagicMock()
        mock_issue.id = "10001"
        mock_issue.key = "SUPPORT-42"
        mock_issue.fields.summary = "Cannot log in"
        mock_issue.fields.description = "Login is broken"
        mock_issue.fields.status.__str__ = lambda _: "Open"
        mock_issue.fields.priority.__str__ = lambda _: "High"
        mock_issue.fields.reporter.__str__ = lambda _: "Jane Doe"
        mock_issue.fields.assignee = None
        mock_issue.fields.labels = ["auth"]
        mock_issue.fields.created = "2024-01-01T00:00:00.000+0000"
        mock_issue.fields.updated = "2024-01-02T00:00:00.000+0000"

        mock_jira_sdk = MagicMock()
        mock_jira_sdk.issue.return_value = mock_issue

        client = jira.JiraClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )

        with patch.object(client, "_get_client", return_value=mock_jira_sdk):
            result = await client.get_issue(issue_key="SUPPORT-42")

        # Then a well-formed dict is returned
        assert result is not None
        assert result["key"] == "SUPPORT-42"
        assert result["summary"] == "Cannot log in"
        assert result["labels"] == ["auth"]
        assert result["assignee"] is None

    async def test_returns_none_on_sdk_exception(self):
        # Given a configured client whose SDK raises an exception
        # When get_issue is called
        client = jira.JiraClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )
        mock_sdk = MagicMock()
        mock_sdk.issue.side_effect = Exception("Connection refused")

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.get_issue(issue_key="SUPPORT-42")

        # Then None is returned gracefully
        assert result is None


class TestJiraClientFormatSuggestionComment:
    def test_formats_all_fields(self):
        # Given a fully populated suggestion
        # When formatting as a comment
        client = jira.JiraClient(base_url="x", api_token="x", user_email="x@x.com")  # noqa: S106
        comment = client.format_suggestion_comment(
            suggested_response="Please reset your password at /account/reset.",
            confidence_label="High",
            category="account",
            sources=[{"title": "Login Guide", "url": "https://docs.example.com/login"}],
        )

        # Then all sections appear in the output
        assert "Sentinel Response Suggestion" in comment
        assert "High" in comment
        assert "account" in comment
        assert "Please reset your password" in comment
        assert "Login Guide" in comment
        assert "https://docs.example.com/login" in comment

    def test_formats_without_optional_fields(self):
        # Given only a required suggested_response
        # When formatting as a comment
        client = jira.JiraClient(base_url="x", api_token="x", user_email="x@x.com")  # noqa: S106
        comment = client.format_suggestion_comment(
            suggested_response="Try refreshing the page.",
            confidence_label=None,
            category=None,
            sources=None,
        )

        # Then it still renders without error
        assert "Try refreshing the page." in comment


class TestJiraClientAddInternalComment:
    async def test_returns_false_when_not_configured(self):
        # Given an unconfigured client
        # When adding a comment
        client = jira.JiraClient(base_url="", api_token="", user_email="")
        result = await client.add_internal_comment(issue_key="SUPPORT-1", body="test")

        # Then False is returned
        assert result is False

    async def test_returns_true_on_success(self):
        # Given a configured client with a working SDK
        # When adding a comment
        client = jira.JiraClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )
        mock_sdk = MagicMock()
        mock_sdk.add_comment.return_value = MagicMock()

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.add_internal_comment(issue_key="SUPPORT-1", body="A comment")

        # Then True is returned
        assert result is True

    async def test_returns_false_on_sdk_exception(self):
        # Given a configured client whose SDK raises
        # When adding a comment
        client = jira.JiraClient(
            base_url="https://example.atlassian.net",
            api_token="token",  # noqa: S106
            user_email="user@example.com",
        )
        mock_sdk = MagicMock()
        mock_sdk.add_comment.side_effect = Exception("API error")

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.add_internal_comment(issue_key="SUPPORT-1", body="A comment")

        # Then False is returned gracefully
        assert result is False


class TestJiraClientTransitionIssue:
    async def test_returns_false_when_not_configured(self):
        # Given an unconfigured client
        # When transitioning an issue
        client = jira.JiraClient(base_url="", api_token="", user_email="")
        result = await client.transition_issue(issue_key="S-1", transition_name="Done")

        # Then False is returned
        assert result is False

    async def test_returns_false_when_transition_not_found(self):
        # Given a configured client where the transition name does not exist
        # When transitioning the issue
        client = jira.JiraClient(base_url="x", api_token="x", user_email="x@x.com")  # noqa: S106
        mock_sdk = MagicMock()
        mock_sdk.transitions.return_value = [{"id": "1", "name": "In Progress"}]

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.transition_issue(issue_key="S-1", transition_name="Done")

        # Then False is returned (transition not found)
        assert result is False

    async def test_returns_true_on_successful_transition(self):
        # Given a configured client where the transition exists
        # When transitioning the issue
        client = jira.JiraClient(base_url="x", api_token="x", user_email="x@x.com")  # noqa: S106
        mock_sdk = MagicMock()
        mock_sdk.transitions.return_value = [{"id": "31", "name": "Done"}]

        with patch.object(client, "_get_client", return_value=mock_sdk):
            result = await client.transition_issue(issue_key="S-1", transition_name="Done")

        # Then True is returned and SDK was called
        assert result is True
        mock_sdk.transition_issue.assert_called_once_with("S-1", "31")
