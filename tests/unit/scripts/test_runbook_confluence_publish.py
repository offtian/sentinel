from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest

from sentinel.vendors.confluence import client as confluence_client


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "runbook_confluence_publish.py"
_MODULE_NAME = "sentinel_test_scripts.runbook_confluence_publish"


def _load_publish_module() -> ModuleType:
    """
    Load the publish script as an importable module for the test.

    The script lives outside the ``src/`` package root because it is a
    CI entry point, not application code. Loading via
    ``importlib.util`` keeps the test independent of any sys.path
    mutation while still exercising the script's real top-level
    imports.
    """
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"could not load script module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_PUBLISH = _load_publish_module()


class _FakeRunbookMetadata:
    """Stand-in for ``models.RunbookMetadata`` carrying only the fields the script reads."""

    def __init__(self, *, content_sha: str) -> None:
        self.content_sha = content_sha


class _FakeRunbook:
    """Stand-in for ``models.Runbook`` carrying only the fields the script reads."""

    def __init__(self, *, body: str, content_sha: str) -> None:
        self.body = body
        self.metadata = _FakeRunbookMetadata(content_sha=content_sha)


def _make_catalog(items: dict[str, _FakeRunbook]) -> dict[str, _FakeRunbook]:
    """Return a fresh dict from the (id -> runbook) mapping."""
    return dict(items)


def _make_unconfigured_settings() -> Any:
    """Return a SimpleNamespace mimicking the publish-relevant settings, all empty."""
    settings = mock.MagicMock()
    settings.confluence_base_url = ""
    settings.confluence_user = ""
    settings.confluence_token = None
    settings.confluence_space_key = ""
    settings.confluence_parent_page_id = ""
    return settings


def _make_configured_settings() -> Any:
    """Return a SimpleNamespace mimicking the publish-relevant settings, fully populated."""
    settings = mock.MagicMock()
    settings.confluence_base_url = "https://acme.atlassian.net/wiki"
    settings.confluence_user = "publisher@acme.test"
    token_secret = mock.MagicMock()
    token_secret.get_secret_value.return_value = "secret-token"
    settings.confluence_token = token_secret
    settings.confluence_space_key = "RUNBOOKS"
    settings.confluence_parent_page_id = "parent-page-1"
    return settings


def _make_config(*, settings: Any, runbooks_paths: tuple[Path, ...] = ()) -> Any:
    """Return a stand-in BaseConfiguration with the publish-relevant fields wired."""
    cfg = mock.MagicMock()
    cfg.settings = settings
    cfg.runbooks_paths = runbooks_paths
    return cfg


class TestIsPublishable:
    def test_returns_false_for_underscore_prefixed_id(self) -> None:
        # Given a runbook id that begins with an underscore (private template)
        runbook_id = "_generic-investigation"

        # When _is_publishable is invoked
        result = _PUBLISH._is_publishable(runbook_id)

        # Then the result is False (private templates are not user-facing docs)
        assert result is False

    def test_returns_false_for_autogen_prefixed_id(self) -> None:
        # Given a runbook id that begins with AUTOGEN- (gap-flywheel skeleton)
        runbook_id = "AUTOGEN-abc123"

        # When _is_publishable is invoked
        result = _PUBLISH._is_publishable(runbook_id)

        # Then the result is False (auto-PR drafts shouldn't ship to Confluence)
        assert result is False

    def test_returns_true_for_normal_id(self) -> None:
        # Given a regular runbook id
        runbook_id = "k8s-crashloop"

        # When _is_publishable is invoked
        result = _PUBLISH._is_publishable(runbook_id)

        # Then the result is True
        assert result is True


class TestMain:
    @pytest.mark.asyncio
    async def test_returns_0_when_unconfigured(self) -> None:
        # Given a config whose Confluence settings are all empty
        settings = _make_unconfigured_settings()
        cfg = _make_config(settings=settings)

        # When _main runs
        with mock.patch.object(_PUBLISH.config, "get_config", return_value=cfg):
            exit_code = await _PUBLISH._main()

        # Then the script exits 0 (CI workflow stays green for unconfigured deploys)
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_publishes_each_runbook_in_catalog(self) -> None:
        # Given a configured settings, a catalog of two real runbooks, and a fake client
        # that returns "created" for both upserts
        settings = _make_configured_settings()
        cfg = _make_config(settings=settings, runbooks_paths=(Path("/runbooks-root"),))
        catalog = _make_catalog(
            {
                "k8s-crashloop": _FakeRunbook(body="# Crash", content_sha="sha-crash"),
                "k8s-oom": _FakeRunbook(body="# OOM", content_sha="sha-oom"),
            },
        )
        upsert_calls: list[dict[str, Any]] = []

        async def _fake_upsert(
            *,
            title: str,
            body_storage: str,
            sentinel_content_sha: str,
        ) -> confluence_client.ConfluenceUpsertResult:
            upsert_calls.append(
                {"title": title, "body": body_storage, "sha": sentinel_content_sha},
            )
            return confluence_client.ConfluenceUpsertResult(
                page_id="page-" + title,
                action="created",
                sentinel_content_sha=sentinel_content_sha,
            )

        fake_client = mock.MagicMock(spec=confluence_client.ConfluenceClient)
        fake_client.is_configured = True
        fake_client.upsert_page.side_effect = _fake_upsert

        # When _main runs
        with (
            mock.patch.object(_PUBLISH.config, "get_config", return_value=cfg),
            mock.patch.object(_PUBLISH, "_build_client", return_value=fake_client),
            mock.patch.object(
                _PUBLISH.runbook_loader,
                "discover_runbooks",
                return_value=catalog,
            ),
        ):
            exit_code = await _PUBLISH._main()

        # Then upsert_page was called once per runbook with the runbook id as title
        assert exit_code == 0
        assert len(upsert_calls) == 2
        titles = sorted(call["title"] for call in upsert_calls)
        assert titles == ["k8s-crashloop", "k8s-oom"]
        # And the body was passed through the markdown converter (heading became <h1>)
        assert any("<h1>Crash</h1>" in call["body"] for call in upsert_calls)

    @pytest.mark.asyncio
    async def test_skips_underscore_and_autogen_runbooks(self) -> None:
        # Given a catalog containing one publishable, one underscore-prefixed,
        # and one AUTOGEN- prefixed runbook
        settings = _make_configured_settings()
        cfg = _make_config(settings=settings, runbooks_paths=(Path("/runbooks-root"),))
        catalog = _make_catalog(
            {
                "k8s-crashloop": _FakeRunbook(body="# Crash", content_sha="sha-crash"),
                "_generic-investigation": _FakeRunbook(
                    body="# Generic", content_sha="sha-generic"
                ),
                "AUTOGEN-abc123": _FakeRunbook(body="# Auto", content_sha="sha-auto"),
            },
        )

        published_titles: list[str] = []

        async def _fake_upsert(
            *,
            title: str,
            body_storage: str,
            sentinel_content_sha: str,
        ) -> confluence_client.ConfluenceUpsertResult:
            published_titles.append(title)
            return confluence_client.ConfluenceUpsertResult(
                page_id="page-" + title,
                action="created",
                sentinel_content_sha=sentinel_content_sha,
            )

        fake_client = mock.MagicMock(spec=confluence_client.ConfluenceClient)
        fake_client.is_configured = True
        fake_client.upsert_page.side_effect = _fake_upsert

        # When _main runs
        with (
            mock.patch.object(_PUBLISH.config, "get_config", return_value=cfg),
            mock.patch.object(_PUBLISH, "_build_client", return_value=fake_client),
            mock.patch.object(
                _PUBLISH.runbook_loader,
                "discover_runbooks",
                return_value=catalog,
            ),
        ):
            exit_code = await _PUBLISH._main()

        # Then only the publishable runbook was upserted
        assert exit_code == 0
        assert published_titles == ["k8s-crashloop"]

    @pytest.mark.asyncio
    async def test_returns_1_when_any_runbook_errors(self) -> None:
        # Given a catalog of two runbooks where the first upsert fails with an API error
        settings = _make_configured_settings()
        cfg = _make_config(settings=settings, runbooks_paths=(Path("/runbooks-root"),))
        catalog = _make_catalog(
            {
                "k8s-crashloop": _FakeRunbook(body="# Crash", content_sha="sha-crash"),
                "k8s-oom": _FakeRunbook(body="# OOM", content_sha="sha-oom"),
            },
        )

        call_count = {"value": 0}

        async def _failing_then_succeeding_upsert(
            *,
            title: str,
            body_storage: str,
            sentinel_content_sha: str,
        ) -> confluence_client.ConfluenceUpsertResult:
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise confluence_client.ConfluenceAPIError(
                    status_code=503,
                    body="overloaded",
                    action="create_page",
                )
            return confluence_client.ConfluenceUpsertResult(
                page_id="page-" + title,
                action="created",
                sentinel_content_sha=sentinel_content_sha,
            )

        fake_client = mock.MagicMock(spec=confluence_client.ConfluenceClient)
        fake_client.is_configured = True
        fake_client.upsert_page.side_effect = _failing_then_succeeding_upsert

        # When _main runs
        with (
            mock.patch.object(_PUBLISH.config, "get_config", return_value=cfg),
            mock.patch.object(_PUBLISH, "_build_client", return_value=fake_client),
            mock.patch.object(
                _PUBLISH.runbook_loader,
                "discover_runbooks",
                return_value=catalog,
            ),
        ):
            exit_code = await _PUBLISH._main()

        # Then the script exits 1 (signalling the CI step to fail) but the second
        # runbook still attempted to publish (the loop is resilient per-runbook)
        assert exit_code == 1
        assert call_count["value"] == 2
