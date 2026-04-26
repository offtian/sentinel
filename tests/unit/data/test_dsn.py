"""Tests for the libpq DSN translation helper."""

from __future__ import annotations

from sentinel.data import _dsn


class TestToLibpq:
    def test_strips_asyncpg_driver_suffix(self) -> None:
        # Given a SQLAlchemy-flavoured URL with the +asyncpg driver suffix
        sqlalchemy_url = "postgresql+asyncpg://user:pass@localhost:5432/sentinel"

        # When the URL is translated to its libpq form
        libpq_url = _dsn.to_libpq(sqlalchemy_url)

        # Then the +asyncpg suffix is removed and the rest is preserved
        assert libpq_url == "postgresql://user:pass@localhost:5432/sentinel"

    def test_leaves_libpq_url_unchanged(self) -> None:
        # Given a URL that is already in libpq form (no +asyncpg suffix)
        libpq_url = "postgresql://user:pass@localhost:5432/sentinel"

        # When the URL is translated
        translated = _dsn.to_libpq(libpq_url)

        # Then it is returned unchanged
        assert translated == libpq_url

    def test_preserves_query_string_and_path(self) -> None:
        # Given a URL with a query string and path
        sqlalchemy_url = (
            "postgresql+asyncpg://user@host/sentinel?sslmode=require&application_name=sentinel"
        )

        # When the URL is translated
        libpq_url = _dsn.to_libpq(sqlalchemy_url)

        # Then everything after the scheme survives the strip
        assert libpq_url == (
            "postgresql://user@host/sentinel?sslmode=require&application_name=sentinel"
        )
