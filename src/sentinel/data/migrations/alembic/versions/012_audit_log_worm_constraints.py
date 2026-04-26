"""
Extend audit_log with WORM constraints + chain hashing (RFC 12.3.10).

Revision ID: 012
Revises: 011
Create Date: 2026-04-26

Adds three columns to ``audit_log``:
- request_id   UUID    nullable indexed   link back to alert_request envelope
- prev_hash    text    nullable           prior row's row_hash in the same
                                          request_id chain (NULL for first row)
- row_hash     text    nullable Python-side; populated server-side by the
                                          ``audit_log_compute_row_hash`` BEFORE
                                          INSERT trigger so Python writers do
                                          not have to compute it. The column
                                          stays nullable in the schema because
                                          rows that pre-date this migration
                                          carry no row_hash (no backfill in
                                          this slice — only new INSERTs are
                                          hashed).

Trigger contract:
- ``audit_log_compute_row_hash`` (BEFORE INSERT) computes ``row_hash`` as a
  hex SHA-256 over the concatenation, in this exact order, of:

      coalesce(prev_hash, '') ||
      actor ||
      action ||
      resource_type ||
      resource_id ||
      details_json ||
      timestamp::text

  The order is part of the on-disk contract — replay code that re-derives the
  hash MUST follow the same column order. Hashing inputs are deliberately the
  business-meaningful columns; ``id`` is excluded because the trigger fires
  before the row is committed and the PK is application-supplied. Nullable
  columns participating in the digest are coalesced to '' to keep the digest
  deterministic across rows that omit optional fields.

- ``audit_log_worm_guard`` (BEFORE UPDATE OR DELETE) raises immediately. The
  application role *should* be INSERT-only at the grant layer; the trigger is
  defence-in-depth so a misconfigured role still cannot mutate the chain.

Requires the ``pgcrypto`` extension for ``digest()``. The migration enables
pgcrypto idempotently; ``downgrade()`` deliberately does NOT drop pgcrypto
because other features may depend on it.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


_CREATE_PGCRYPTO = "CREATE EXTENSION IF NOT EXISTS pgcrypto;"

_CREATE_COMPUTE_ROW_HASH_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_compute_row_hash()
RETURNS TRIGGER AS $$
BEGIN
    -- Column concat order is part of the RFC 12.3.10 on-disk contract. Replay
    -- code re-derives row_hash with this exact order; do NOT reorder without
    -- bumping the migration revision.
    NEW.row_hash := encode(
        digest(
            coalesce(NEW.prev_hash, '') ||
            NEW.actor ||
            NEW.action ||
            NEW.resource_type ||
            NEW.resource_id ||
            NEW.details_json ||
            NEW.timestamp::text,
            'sha256'
        ),
        'hex'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_COMPUTE_ROW_HASH_TRIGGER = """
CREATE TRIGGER audit_log_compute_row_hash_trigger
    BEFORE INSERT ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_compute_row_hash();
"""

_CREATE_WORM_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_worm_guard()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only - UPDATE/DELETE forbidden';
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_WORM_GUARD_UPDATE_TRIGGER = """
CREATE TRIGGER audit_log_worm_guard_update_trigger
    BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_worm_guard();
"""

_CREATE_WORM_GUARD_DELETE_TRIGGER = """
CREATE TRIGGER audit_log_worm_guard_delete_trigger
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_worm_guard();
"""

_DROP_COMPUTE_ROW_HASH_TRIGGER = (
    "DROP TRIGGER IF EXISTS audit_log_compute_row_hash_trigger ON audit_log;"
)
_DROP_WORM_GUARD_UPDATE_TRIGGER = (
    "DROP TRIGGER IF EXISTS audit_log_worm_guard_update_trigger ON audit_log;"
)
_DROP_WORM_GUARD_DELETE_TRIGGER = (
    "DROP TRIGGER IF EXISTS audit_log_worm_guard_delete_trigger ON audit_log;"
)
_DROP_COMPUTE_ROW_HASH_FUNCTION = "DROP FUNCTION IF EXISTS audit_log_compute_row_hash();"
_DROP_WORM_GUARD_FUNCTION = "DROP FUNCTION IF EXISTS audit_log_worm_guard();"


def upgrade() -> None:
    # -- Columns --
    op.add_column(
        "audit_log",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_audit_log_request_id", "audit_log", ["request_id"])
    op.add_column(
        "audit_log",
        sa.Column("prev_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("row_hash", sa.Text(), nullable=True),
    )

    # -- pgcrypto (idempotent) + chain-hash trigger --
    op.execute(_CREATE_PGCRYPTO)
    op.execute(_CREATE_COMPUTE_ROW_HASH_FUNCTION)
    op.execute(_CREATE_COMPUTE_ROW_HASH_TRIGGER)

    # -- WORM guard trigger --
    op.execute(_CREATE_WORM_GUARD_FUNCTION)
    op.execute(_CREATE_WORM_GUARD_UPDATE_TRIGGER)
    op.execute(_CREATE_WORM_GUARD_DELETE_TRIGGER)


def downgrade() -> None:
    # -- Drop triggers + functions (pgcrypto stays — may be used elsewhere) --
    op.execute(_DROP_WORM_GUARD_DELETE_TRIGGER)
    op.execute(_DROP_WORM_GUARD_UPDATE_TRIGGER)
    op.execute(_DROP_COMPUTE_ROW_HASH_TRIGGER)
    op.execute(_DROP_WORM_GUARD_FUNCTION)
    op.execute(_DROP_COMPUTE_ROW_HASH_FUNCTION)

    # -- Columns + index (reverse order of upgrade) --
    op.drop_column("audit_log", "row_hash")
    op.drop_column("audit_log", "prev_hash")
    op.drop_index("ix_audit_log_request_id", table_name="audit_log")
    op.drop_column("audit_log", "request_id")
