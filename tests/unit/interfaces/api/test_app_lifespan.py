"""
Unit tests for the FastAPI lifespan wiring that builds the LangGraph
support-review checkpointer and compiled graph at startup.

The tests mock infrastructure side-effects (database engine, OTel
instrumentation, async_db, agent loading) so they exercise the
workflow-graph wiring in isolation. Integration coverage of
``build_checkpointer`` against the real Postgres test database lives in
``tests/integration/interfaces/workflows/test_checkpointer.py``.
"""

from __future__ import annotations

from unittest import mock

import fastapi
import pytest

from sentinel.interfaces.api import app as app_module


class _LifespanInfra:
    """Bundle every infrastructure mock the lifespan calls.

    Tests retrieve the langgraph builder mocks from this fixture to
    assert wiring; the bootstrap / OTel / database mocks exist purely
    so the lifespan body runs to completion without touching real
    services.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.saver = mock.MagicMock(name="AsyncPostgresSaver")
        self.close_handle = mock.AsyncMock(name="checkpointer_close")
        self.compiled_graph = mock.MagicMock(name="CompiledSupportReviewGraph")
        self.build_checkpointer = mock.AsyncMock(
            return_value=(self.saver, self.close_handle),
        )
        self.build_graph = mock.MagicMock(return_value=self.compiled_graph)

        monkeypatch.setattr(app_module.bootstrap, "initialise", lambda: None)
        monkeypatch.setattr(app_module.bootstrap_otel, "init_otel", lambda: None)
        monkeypatch.setattr(
            app_module.bootstrap_otel,
            "instrument_sqlalchemy",
            lambda **_: None,
        )
        monkeypatch.setattr(app_module.async_db, "connect_db", mock.AsyncMock())
        monkeypatch.setattr(app_module.async_db, "disconnect_db", mock.AsyncMock())
        monkeypatch.setattr(
            app_module.database,
            "get_engine",
            lambda: mock.MagicMock(sync_engine=mock.MagicMock()),
        )
        monkeypatch.setattr(app_module.database, "close_engine", mock.AsyncMock())
        monkeypatch.setattr(
            app_module.workflows_checkpointer,
            "build_checkpointer",
            self.build_checkpointer,
        )
        monkeypatch.setattr(
            app_module.workflows_support_review,
            "build_support_review_graph",
            self.build_graph,
        )

        cfg_stub = mock.MagicMock(name="Configuration")
        monkeypatch.setattr(app_module.config_mod, "get_config", lambda: cfg_stub)


class TestSupportReviewLifespan:
    @pytest.mark.asyncio
    async def test_populates_compiled_graph_on_app_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the lifespan with infra side-effects mocked but the
        # workflow-graph wiring left as the code under test
        infra = _LifespanInfra(monkeypatch)
        fresh_app = fastapi.FastAPI()

        # When the lifespan startup runs to yield
        async with app_module.lifespan(fresh_app):
            # Then build_checkpointer ran and its saver was passed to
            # build_support_review_graph
            infra.build_checkpointer.assert_awaited_once()
            infra.build_graph.assert_called_once_with(checkpointer=infra.saver)

            # And both the compiled graph and the close callable land
            # on app.state for the routers to read
            assert fresh_app.state.support_review_graph is infra.compiled_graph
            assert fresh_app.state.support_review_checkpointer_close is infra.close_handle

        # And the close callable runs exactly once on shutdown
        infra.close_handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_graph_when_database_url_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given a Settings instance with database_url unset (the
        # graceful-degradation branch where the engine is not connected)
        infra = _LifespanInfra(monkeypatch)
        settings_stub = mock.MagicMock(database_url="", langgraph_checkpoint_dsn=None)
        monkeypatch.setattr(app_module, "get_settings", lambda: settings_stub)
        fresh_app = fastapi.FastAPI()

        # When the lifespan runs
        async with app_module.lifespan(fresh_app):
            # Then no langgraph wiring fires and app.state holds None
            # placeholders so attribute access from routers is well-defined
            infra.build_checkpointer.assert_not_called()
            infra.build_graph.assert_not_called()
            assert fresh_app.state.support_review_graph is None
            assert fresh_app.state.support_review_checkpointer_close is None

        # And no close callable is awaited on shutdown
        infra.close_handle.assert_not_called()
