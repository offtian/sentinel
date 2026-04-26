"""Tests for the databases.Database singleton."""

from __future__ import annotations

from unittest import mock

import databases
import pytest

from sentinel.data import db


class TestGetDb:
    def test_returns_database_instance(self, patch_settings) -> None:
        # Given a configured database URL
        fake = patch_settings(db)
        fake.database_url = "postgresql+asyncpg://user:pass@localhost/sentinel"
        db._db = None  # Reset singleton

        # When get_db is called
        result = db.get_db()

        # Then it returns a databases.Database instance
        assert isinstance(result, databases.Database)

    def test_returns_same_instance_on_second_call(self, patch_settings) -> None:
        # Given get_db has been called once
        fake = patch_settings(db)
        fake.database_url = "postgresql+asyncpg://user:pass@localhost/sentinel"
        db._db = None
        first = db.get_db()

        # When get_db is called again
        second = db.get_db()

        # Then the same instance is returned
        assert first is second

    def test_raises_when_no_database_url(self, patch_settings) -> None:
        # Given no database URL is configured
        fake = patch_settings(db)
        fake.database_url = ""
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
