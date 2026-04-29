"""
Unit tests for the FastAPI lifespan wiring that builds the LangGraph
support-review and SRE investigation checkpointers and compiled graphs
at startup.

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
        self.compiled_support_graph = mock.MagicMock(name="CompiledSupportReviewGraph")
        self.compiled_sre_graph = mock.MagicMock(name="CompiledSREGraph")
        self.build_checkpointer = mock.AsyncMock(
            return_value=(self.saver, self.close_handle),
        )
        self.build_support_graph = mock.MagicMock(return_value=self.compiled_support_graph)
        self.build_sre_graph = mock.MagicMock(return_value=self.compiled_sre_graph)

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
            self.build_support_graph,
        )
        monkeypatch.setattr(
            app_module.workflows_sre_investigation,
            "build_sre_investigation_graph",
            self.build_sre_graph,
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
            infra.build_support_graph.assert_called_once_with(checkpointer=infra.saver)

            # And both the compiled graph and the close callable land
            # on app.state for the routers to read
            assert fresh_app.state.support_review_graph is infra.compiled_support_graph
            assert fresh_app.state.support_review_checkpointer_close is infra.close_handle

        # And the close callable runs exactly once on shutdown
        infra.close_handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_graph_when_database_url_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_settings,
    ) -> None:
        # Given a Settings instance with database_url unset (the
        # graceful-degradation branch where the engine is not connected)
        infra = _LifespanInfra(monkeypatch)
        fake = patch_settings(app_module)
        fake.database_url = ""
        fake.langgraph_checkpoint_dsn = None
        fresh_app = fastapi.FastAPI()

        # When the lifespan runs
        async with app_module.lifespan(fresh_app):
            # Then no langgraph wiring fires and app.state holds None
            # placeholders so attribute access from routers is well-defined
            infra.build_checkpointer.assert_not_called()
            infra.build_support_graph.assert_not_called()
            assert fresh_app.state.support_review_graph is None
            assert fresh_app.state.support_review_checkpointer_close is None

        # And no close callable is awaited on shutdown
        infra.close_handle.assert_not_called()


class TestSREInvestigationLifespan:
    @pytest.mark.asyncio
    async def test_populates_sre_graph_on_app_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the lifespan with infra side-effects mocked
        infra = _LifespanInfra(monkeypatch)
        fresh_app = fastapi.FastAPI()

        # When the lifespan startup runs to yield
        async with app_module.lifespan(fresh_app):
            # Then build_sre_investigation_graph was called with the same
            # saver that was used for the support graph (shared checkpointer)
            infra.build_sre_graph.assert_called_once_with(checkpointer=infra.saver)

            # And the compiled SRE graph lands on app.state
            assert fresh_app.state.sre_investigation_graph is infra.compiled_sre_graph

    @pytest.mark.asyncio
    async def test_sre_graph_is_none_when_no_database(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_settings,
    ) -> None:
        # Given a Settings instance with database_url unset
        infra = _LifespanInfra(monkeypatch)
        fake = patch_settings(app_module)
        fake.database_url = ""
        fake.langgraph_checkpoint_dsn = None
        fresh_app = fastapi.FastAPI()

        # When the lifespan runs without a database
        async with app_module.lifespan(fresh_app):
            # Then the SRE graph was not built and app.state holds None
            infra.build_sre_graph.assert_not_called()
            assert fresh_app.state.sre_investigation_graph is None

    @pytest.mark.asyncio
    async def test_both_graphs_share_the_same_checkpointer_saver(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the lifespan with infra mocked
        infra = _LifespanInfra(monkeypatch)
        fresh_app = fastapi.FastAPI()

        # When the lifespan runs
        async with app_module.lifespan(fresh_app):
            # Then both graph builders received the same saver instance
            # (build_checkpointer is called once, saver is shared)
            support_call_kwargs = infra.build_support_graph.call_args
            sre_call_kwargs = infra.build_sre_graph.call_args

            assert support_call_kwargs.kwargs["checkpointer"] is infra.saver
            assert sre_call_kwargs.kwargs["checkpointer"] is infra.saver

        # And the single close handle is awaited exactly once on shutdown
        infra.close_handle.assert_awaited_once()
