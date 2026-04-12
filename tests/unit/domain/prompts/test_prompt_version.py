"""Unit tests for prompt versioning: ``_git_sha()``, version format, and caching."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from unittest import mock

import pytest
from jinja2 import Environment

from sentinel.domain import prompts
from sentinel.domain.prompts import template as prompt_template_mod


@pytest.fixture(autouse=True)
def _clear_git_sha_cache() -> Iterator[None]:
    """Clear _git_sha lru_cache after each test to prevent cross-test leakage."""
    yield
    prompt_template_mod._git_sha.cache_clear()


class TestGitSha:
    """Tests for the ``_git_sha()`` helper."""

    def test_returns_env_var_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Given the SENTINEL_GIT_SHA environment variable is set,
        When _git_sha is called,
        Then it returns the env var value.
        """
        # Given SENTINEL_GIT_SHA is set
        monkeypatch.setenv("SENTINEL_GIT_SHA", "abc123def456")
        prompt_template_mod._git_sha.cache_clear()

        # When _git_sha is called
        result = prompt_template_mod._git_sha()

        # Then it returns the env var value
        assert result == "abc123def456"

    def test_falls_back_to_git_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Given SENTINEL_GIT_SHA is not set and git is available,
        When _git_sha is called,
        Then it returns the output of git rev-parse HEAD.
        """
        # Given SENTINEL_GIT_SHA is not set and git returns a sha
        monkeypatch.delenv("SENTINEL_GIT_SHA", raising=False)
        prompt_template_mod._git_sha.cache_clear()
        fake_sha = "deadbeef" * 5  # 40 chars
        fake_result = subprocess.CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=0,
            stdout=f"  {fake_sha}  \n",
        )

        # When _git_sha is called
        with mock.patch.object(subprocess, "run", return_value=fake_result):
            result = prompt_template_mod._git_sha()

        # Then it returns the stripped git output
        assert result == fake_sha

    def test_returns_unknown_when_git_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Given SENTINEL_GIT_SHA is not set and git binary is missing,
        When _git_sha is called,
        Then it returns "unknown".
        """
        # Given SENTINEL_GIT_SHA is not set and subprocess raises FileNotFoundError
        monkeypatch.delenv("SENTINEL_GIT_SHA", raising=False)
        prompt_template_mod._git_sha.cache_clear()

        # When _git_sha is called
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
            result = prompt_template_mod._git_sha()

        # Then it returns "unknown"
        assert result == "unknown"

    def test_returns_unknown_on_subprocess_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Given SENTINEL_GIT_SHA is not set and git times out,
        When _git_sha is called,
        Then it returns "unknown".
        """
        # Given SENTINEL_GIT_SHA is not set and subprocess times out
        monkeypatch.delenv("SENTINEL_GIT_SHA", raising=False)
        prompt_template_mod._git_sha.cache_clear()

        # When _git_sha is called
        with mock.patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2),
        ):
            result = prompt_template_mod._git_sha()

        # Then it returns "unknown"
        assert result == "unknown"

    def test_returns_unknown_on_called_process_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Given SENTINEL_GIT_SHA is not set and git exits with a non-zero status,
        When _git_sha is called,
        Then it returns "unknown".
        """
        # Given SENTINEL_GIT_SHA is not set and subprocess raises CalledProcessError
        monkeypatch.delenv("SENTINEL_GIT_SHA", raising=False)
        prompt_template_mod._git_sha.cache_clear()

        # When _git_sha is called
        with mock.patch.object(
            subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = prompt_template_mod._git_sha()

        # Then it returns "unknown"
        assert result == "unknown"

    def test_falls_through_to_subprocess_when_env_var_is_invalid_hex(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Given SENTINEL_GIT_SHA is set to a non-hex string,
        When _git_sha is called,
        Then it ignores the env var and falls through to subprocess.
        """
        # Given SENTINEL_GIT_SHA contains invalid hex characters
        monkeypatch.setenv("SENTINEL_GIT_SHA", "NOT-A-VALID-SHA!")
        prompt_template_mod._git_sha.cache_clear()
        valid_sha = "deadbeef" * 5

        # When _git_sha is called
        fake_result = subprocess.CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=0,
            stdout=f"{valid_sha}\n",
        )
        with mock.patch.object(subprocess, "run", return_value=fake_result):
            result = prompt_template_mod._git_sha()

        # Then it returns the subprocess result, not the invalid env var
        assert result == valid_sha


class TestVersionFormat:
    """Tests for the ``PromptTemplate.version`` field format."""

    def test_version_matches_git_sha_template_name_pattern(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Given a known git SHA,
        When a PromptTemplate is created via from_text,
        Then version matches the pattern "{sha[:12]}:{template_name}".
        """
        # Given a known git SHA
        fake_sha = "abcdef123456" + "7890" * 7
        monkeypatch.setenv("SENTINEL_GIT_SHA", fake_sha)
        prompt_template_mod._git_sha.cache_clear()

        # When a PromptTemplate is created via from_text
        tpl = prompt_template_mod.PromptTemplate.from_text(
            template_name="alert_classifier",
            system_text="You are a classifier.",
        )

        # Then version matches "{sha[:12]}:{template_name}"
        assert tpl.version == f"{fake_sha[:12]}:alert_classifier"


class TestLoadTemplateCaching:
    """Tests for ``load_template()`` LRU cache identity."""

    def test_returns_same_object_on_repeated_calls(self) -> None:
        """
        Given load_template has been called for a template name,
        When load_template is called again with the same name,
        Then the exact same object is returned (identity check).
        """
        # Given load_template is called for alert_classifier
        first = prompts.load_template("alert_classifier")

        # When load_template is called again with the same name
        second = prompts.load_template("alert_classifier")

        # Then the exact same object is returned
        assert first is second


class TestMissingSystemBlock:
    """Tests for missing system block error in ``from_jinja``."""

    def test_raises_value_error_when_system_block_absent(self) -> None:
        """
        Given a Jinja2 template with no system block,
        When PromptTemplate.from_jinja is called,
        Then ValueError is raised with a descriptive message.
        """
        # Given a Jinja2 template with no system block
        env = Environment(autoescape=False)  # noqa: S701
        jinja_tpl = env.from_string("{% block user %}Hello{% endblock %}")

        # When PromptTemplate.from_jinja is called
        # Then ValueError is raised
        with pytest.raises(ValueError, match="has no 'system' block"):
            prompt_template_mod.PromptTemplate.from_jinja(
                template_name="no_system",
                jinja_template=jinja_tpl,
            )
