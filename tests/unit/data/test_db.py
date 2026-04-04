"""Tests for the databases.Database singleton."""

from __future__ import annotations

from unittest import mock

import databases
import pytest

from sentinel.data import db


class TestGetDb:
    def test_returns_database_instance(self) -> None:
        # Given a configured database URL
        mock_settings = mock.MagicMock()
        mock_settings.database_url = "postgresql+asyncpg://user:pass@localhost/sentinel"

        with mock.patch.object(db, "get_settings", return_value=mock_settings):
            db._db = None  # Reset singleton

            # When get_db is called
            result = db.get_db()

            # Then it returns a databases.Database instance
            assert isinstance(result, databases.Database)

    def test_returns_same_instance_on_second_call(self) -> None:
        # Given get_db has been called once
        mock_settings = mock.MagicMock()
        mock_settings.database_url = "postgresql+asyncpg://user:pass@localhost/sentinel"

        with mock.patch.object(db, "get_settings", return_value=mock_settings):
            db._db = None
            first = db.get_db()

            # When get_db is called again
            second = db.get_db()

            # Then the same instance is returned
            assert first is second

    def test_raises_when_no_database_url(self) -> None:
        # Given no database URL is configured
        mock_settings = mock.MagicMock()
        mock_settings.database_url = ""

        with mock.patch.object(db, "get_settings", return_value=mock_settings):
            db._db = None

            # When get_db is called
            # Then it raises RuntimeError
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                db.get_db()


class TestConnectDb:
    @pytest.mark.asyncio
    async def test_connect_calls_database_connect(self) -> None:
        # Given a Database instance
        mock_db = mock.AsyncMock(spec=databases.Database)
        db._db = mock_db

        # When connect_db is called
        await db.connect_db()

        # Then the database connect method is called
        mock_db.connect.assert_awaited_once()


class TestDisconnectDb:
    @pytest.mark.asyncio
    async def test_disconnect_calls_database_disconnect(self) -> None:
        # Given a connected Database instance
        mock_db = mock.AsyncMock(spec=databases.Database)
        db._db = mock_db

        # When disconnect_db is called
        await db.disconnect_db()

        # Then the database disconnect method is called
        mock_db.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_resets_singleton(self) -> None:
        # Given a connected Database instance
        mock_db = mock.AsyncMock(spec=databases.Database)
        db._db = mock_db

        # When disconnect_db is called
        await db.disconnect_db()

        # Then the singleton is reset
        assert db._db is None
