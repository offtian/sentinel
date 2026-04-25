"""
Replay CLI.

Load, inspect, or replay a pipeline run from its stored snapshot.

    python -m sentinel.replay <run_id>              # print bundle
    python -m sentinel.replay <run_id> --replay     # re-execute pipeline
    python -m sentinel.replay <run_id> --diff       # re-execute and diff
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import attrs
import databases

from sentinel import config as config_mod
from sentinel import settings as settings_mod
from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.pipeline import tracer as pipeline_tracer
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import sre_investigation, support_review
from sentinel.interfaces.graphs.agents import k8s_runner


def _envelope_for_replay(bundle: pipeline_types.ReplayBundle) -> envelope_mod.Envelope:
    """
    Mint an Envelope for a replay re-execution.

    Replay synthesises a deterministic envelope tied to the original run.
    Until F4.5 ships full envelope persistence on ``ReplayBundle``, the
    replay leg uses a fresh ``request_id`` so its spans are distinguishable
    from the original run's spans, while keeping placeholders for the
    multi-tenant fields.
    """
    return envelope_mod.Envelope(
        request_id=uuid.uuid4(),
        tenant_id="replay",
        cluster_id="replay",
        region="replay",
        pii_class="internal",
        received_at=datetime.now(tz=UTC),
    )


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


async def _replay_pipeline(*, run_id: uuid.UUID, show_diff: bool) -> None:
    """
    Re-execute a pipeline from its stored snapshot.

    Load the ReplayBundle, reconstruct the pipeline configuration,
    re-run with the stored input, and optionally diff against the
    original output.

    :param run_id: Primary key of the pipeline run record.
    :param show_diff: When True, print a unified diff and exit 3 on drift.
    :raises pipeline_errors.ReplayBundleNotFoundError: if no row matches *run_id*.
    """
    db_url = settings_mod.get_settings().database_url
    db = databases.Database(str(db_url))
    await db.connect()
    try:
        bundle = await pipeline_queries.fetch_replay_bundle(db=db, run_id=run_id)

        cfg = config_mod.get_config()

        result: pipeline_types.InvestigationReply | pipeline_types.SupportReply

        if bundle.pipeline_type == "sre_investigation":
            result = await _replay_sre(bundle=bundle, cfg=cfg, db=db)
        elif bundle.pipeline_type == "support_review":
            result = await _replay_support(bundle=bundle, cfg=cfg, db=db)
        else:
            print(f"Unknown pipeline type: {bundle.pipeline_type}", file=sys.stderr)  # noqa: T201
            sys.exit(2)

        replay_output = json.loads(result.model_dump_json())

        if show_diff:
            _print_diff(original=bundle.final_reply, replayed=replay_output)
        else:
            print(json.dumps(replay_output, indent=2, default=str))  # noqa: T201
    finally:
        await db.disconnect()


async def _replay_sre(
    *,
    bundle: pipeline_types.ReplayBundle,
    cfg: config_mod.BaseConfiguration,
    db: databases.Database,
) -> pipeline_types.InvestigationReply:
    """
    Re-execute an SRE investigation from a stored bundle.

    :param bundle: The replay snapshot containing the original input.
    :param cfg: Application configuration with agent registry and adapters.
    :param db: Database connection for execution tracing.
    """
    alert = sre_entities.Alert.model_validate(bundle.input_payload)

    et = pipeline_tracer.ExecutionTracer(db=db)
    await et.start_pipeline(
        pipeline_type="sre_investigation_replay",
        input_data=bundle.input_payload,
    )

    result = await sre_investigation.investigate_alert(
        alert,
        envelope=_envelope_for_replay(bundle),
        agent_for=cfg.agent_for,
        holmes=cfg.build_holmes_adapter(),
        trace_collector=et,
        k8s_adapter=cfg.build_k8s_investigation_adapter(
            agent_runner=k8s_runner.run_k8s_agent,
        ),
        challenger_adapter=cfg.build_challenger_adapter(),
    )

    await et.complete_pipeline(
        status="completed",
        output_data=json.loads(result.model_dump_json()),
        final_reply=json.loads(result.model_dump_json()),
    )

    return result


async def _replay_support(
    *,
    bundle: pipeline_types.ReplayBundle,
    cfg: config_mod.BaseConfiguration,
    db: databases.Database,
) -> pipeline_types.SupportReply:
    """
    Re-execute a support review from a stored bundle.

    :param bundle: The replay snapshot containing the original input.
    :param cfg: Application configuration with agent registry and adapters.
    :param db: Database connection for execution tracing.
    """
    ticket = support_entities.Ticket.model_validate(bundle.input_payload)

    et = pipeline_tracer.ExecutionTracer(db=db)
    await et.start_pipeline(
        pipeline_type="support_review_replay",
        input_data=bundle.input_payload,
    )

    result = await support_review.review_ticket(
        ticket,
        envelope=_envelope_for_replay(bundle),
        agent_for=cfg.agent_for,
        document_searcher=cfg.build_document_searcher(),
        ticket_searcher=cfg.build_ticket_searcher(),
        trace_collector=et,
    )

    await et.complete_pipeline(
        status="completed",
        output_data=json.loads(result.model_dump_json()),
        final_reply=json.loads(result.model_dump_json()),
    )

    return result


def _print_diff(
    *,
    original: dict[str, Any] | None,
    replayed: dict[str, Any],
) -> None:
    """
    Print a human-readable unified diff between original and replayed outputs.

    Exit with code 3 when differences are found (drift detected),
    or print a success message and return normally when outputs match.

    :param original: The original pipeline output from the stored bundle.
    :param replayed: The output from the replay execution.
    """
    original_str = json.dumps(original or {}, indent=2, sort_keys=True, default=str)
    replayed_str = json.dumps(replayed, indent=2, sort_keys=True, default=str)

    diff = difflib.unified_diff(
        original_str.splitlines(keepends=True),
        replayed_str.splitlines(keepends=True),
        fromfile="original",
        tofile="replayed",
    )

    diff_text = "".join(diff)
    if diff_text:
        print(diff_text)  # noqa: T201
        sys.exit(3)
    else:
        print("No differences found — replay matches original output.")  # noqa: T201


def main() -> None:
    """Entry point for ``python -m sentinel.replay``."""
    parser = argparse.ArgumentParser(
        description="Load, inspect, or replay a pipeline run.",
    )
    parser.add_argument("run_id", type=uuid.UUID, help="Pipeline run UUID")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Re-execute the pipeline from stored snapshot",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Re-execute and diff against original output",
    )
    args = parser.parse_args()

    try:
        if args.replay or args.diff:
            asyncio.run(_replay_pipeline(run_id=args.run_id, show_diff=args.diff))
        else:
            asyncio.run(_fetch_and_print(run_id=args.run_id))
    except pipeline_errors.ReplayBundleNotFoundError:
        print(f"No pipeline run found for run_id={args.run_id}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
