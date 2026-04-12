"""
Replay CLI scaffold.

Load and pretty-print the reproducibility snapshot for a pipeline run.

    python -m sentinel.replay <run_id>

**Scaffold only** — re-execution is deferred to slice 6.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

import attrs
import databases

from sentinel import settings as settings_mod
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries


async def _fetch_and_print(*, run_id: uuid.UUID) -> None:
    """
    Open a read-only DB connection, fetch the replay bundle, and print it.

    :param run_id: Primary key of the pipeline run record.
    :raises pipeline_errors.ReplayBundleNotFoundError: if no row matches *run_id*.
    """
    db_url = settings_mod.get_settings().database_url
    db = databases.Database(str(db_url))
    await db.connect()
    try:
        bundle = await pipeline_queries.fetch_replay_bundle(db=db, run_id=run_id)
        output = json.dumps(attrs.asdict(bundle), indent=2, default=str)
        print(output)  # noqa: T201
    finally:
        await db.disconnect()


def main() -> None:
    """Entry point for ``python -m sentinel.replay``."""
    parser = argparse.ArgumentParser(
        description="Load and pretty-print a pipeline run's replay bundle.",
    )
    parser.add_argument("run_id", type=uuid.UUID, help="Pipeline run UUID")
    args = parser.parse_args()

    try:
        asyncio.run(_fetch_and_print(run_id=args.run_id))
    except pipeline_errors.ReplayBundleNotFoundError:
        print(f"No pipeline run found for run_id={args.run_id}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
