"""
Replay CLI for the F4.7 RFC §3.8 ReplayBundle.

    python -m sentinel.replay <run_id>              # print canonical bundle JSON + sha
    python -m sentinel.replay <run_id> --replay     # re-execute against recorded I/O
    python -m sentinel.replay <run_id> --diff       # re-execute and diff vs original

Replay swaps every agent's ``model`` for a single shared
:class:`~sentinel.plugins.models.recorded.RecordedModel` and every toolset
slot for a single shared
:class:`~sentinel.plugins.toolsets.recorded.RecordedToolset` so the run is
deterministic against the captured I/O. Drift in tool / LLM call order,
name, or inputs raises
:class:`~sentinel.domain.pipeline.errors.RecordedReplayMismatchError` and
exits non-zero.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
import uuid
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel import config as config_mod
from sentinel import settings as settings_mod
from sentinel.data.sql import tracing
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import investigation, support_review
from sentinel.interfaces.graphs.agents import k8s_runner
from sentinel.plugins.models import recorded as recorded_model_mod
from sentinel.plugins.toolsets import recorded as recorded_toolset_mod
from sentinel.utils import replay_bundle as bundle_mod


async def _fetch_pipeline_type(*, db: databases.Database, run_id: uuid.UUID) -> str:
    """Return the ``pipeline_type`` for a run row, or raise if missing."""
    query = select(tracing.PipelineRunRecord).where(
        col(tracing.PipelineRunRecord.id) == run_id,
    )
    row = await db.fetch_one(query)
    if row is None:
        raise pipeline_errors.ReplayBundleNotFoundError(run_id)
    return str(dict(row._mapping)["pipeline_type"])  # noqa: SLF001


async def _print_bundle(*, run_id: uuid.UUID) -> None:
    """
    Open a read-only DB connection, fetch the recorded bundle, and print it.

    Prints the canonical JSON of the F4.7 ReplayBundle followed by its
    sha256 digest on the final line so the output is both human-readable
    and scriptable.
    """
    db_url = settings_mod.get_settings().database_url
    db = databases.Database(str(db_url))
    await db.connect()
    try:
        bundle = await pipeline_queries.fetch_recorded_replay_bundle(db=db, run_id=run_id)
        print(bundle_mod.to_canonical_json(bundle))  # noqa: T201
        print(f"# bundle_sha: {bundle.bundle_sha}", file=sys.stderr)  # noqa: T201
    finally:
        await db.disconnect()


def _install_recorded_substitutes(
    *,
    cfg: config_mod.BaseConfiguration,
    bundle: bundle_mod.ReplayBundle,
) -> recorded_toolset_mod.RecordedToolset:
    """
    Swap every registered agent's ``model`` to a shared :class:`RecordedModel`.

    Returns the matching :class:`RecordedToolset` instance so the caller
    can plug it into every toolset slot the pipeline uses. Both
    substitutes share the bundle's recorded I/O queues — one global
    ordered timeline per kind, mirroring the capture-side global builder.
    """
    recorded_model = recorded_model_mod.RecordedModel(bundle.llm_io)
    for agent in cfg._agents.values():  # noqa: SLF001 — internal registry by design
        agent.model = recorded_model
    return recorded_toolset_mod.RecordedToolset(bundle.tool_io)


async def _replay_pipeline(*, run_id: uuid.UUID, show_diff: bool) -> None:
    """
    Re-execute a pipeline against its recorded ReplayBundle.

    Loads the F4.7 bundle (sha-verified on read), installs recorded
    substitutes for every agent's model and every toolset slot, runs the
    pipeline against ``bundle.envelope`` + ``bundle.alert_payload``, and
    optionally diffs against ``bundle.final_outputs``.

    :raises pipeline_errors.ReplayBundleNotFoundError: if no recorded
        bundle exists for *run_id*.
    :raises pipeline_errors.ReplayBundleSHAMismatchError: if the stored
        sha disagrees with the recomputed bundle sha.
    """
    db_url = settings_mod.get_settings().database_url
    db = databases.Database(str(db_url))
    await db.connect()
    try:
        bundle = await pipeline_queries.fetch_recorded_replay_bundle(db=db, run_id=run_id)
        pipeline_type = await _fetch_pipeline_type(db=db, run_id=run_id)

        cfg = config_mod.get_config()
        recorded_toolset = _install_recorded_substitutes(cfg=cfg, bundle=bundle)

        if pipeline_type in ("investigation", "sre_investigation_replay"):
            replayed = await _replay_sre(
                bundle=bundle,
                cfg=cfg,
                recorded_toolset=recorded_toolset,
            )
        elif pipeline_type in ("support_review", "support_review_replay"):
            replayed = await _replay_support(
                bundle=bundle,
                cfg=cfg,
                recorded_toolset=recorded_toolset,
            )
        else:
            print(f"Unknown pipeline type: {pipeline_type}", file=sys.stderr)  # noqa: T201
            sys.exit(2)

        if show_diff:
            _print_diff(original=bundle.final_outputs, replayed=replayed)
        else:
            print(json.dumps(replayed, indent=2, default=str))  # noqa: T201
    finally:
        await db.disconnect()


async def _replay_sre(
    *,
    bundle: bundle_mod.ReplayBundle,
    cfg: config_mod.BaseConfiguration,
    recorded_toolset: recorded_toolset_mod.RecordedToolset,
) -> dict[str, Any]:
    """
    Re-execute the SRE investigation pipeline against the recorded bundle.

    No ``trace_collector`` is passed: replay must not open a fresh
    capture window (no live LLM/tool calls happen anyway, but binding a
    builder would also write a spurious ``pipeline_runs`` row).
    """
    alert = alert_entities.Alert.model_validate(bundle.alert_payload)

    result = await investigation.investigate_alert(
        alert=alert,
        envelope=bundle.envelope,
        agent_for=cfg.agent_for,
        holmes=cfg.build_holmes_adapter(),
        post_to_slack=False,
        k8s_adapter=cfg.build_k8s_investigation_adapter(
            agent_runner=k8s_runner.run_k8s_agent,
        ),
        challenger_adapter=cfg.build_challenger_adapter(),
        classifier_toolsets=(recorded_toolset,),
        analyser_toolsets=(recorded_toolset,),
    )
    payload: dict[str, Any] = json.loads(result.model_dump_json())
    return payload


async def _replay_support(
    *,
    bundle: bundle_mod.ReplayBundle,
    cfg: config_mod.BaseConfiguration,
    recorded_toolset: recorded_toolset_mod.RecordedToolset,
) -> dict[str, Any]:
    """Re-execute the support-review pipeline against the recorded bundle."""
    ticket = support_entities.Ticket.model_validate(bundle.alert_payload)

    result = await support_review.review_ticket(
        ticket=ticket,
        envelope=bundle.envelope,
        agent_for=cfg.agent_for,
        document_searcher=cfg.build_document_searcher(),
        ticket_searcher=cfg.build_ticket_searcher(),
        reviewer_toolsets=(recorded_toolset,),
        drafter_toolsets=(recorded_toolset,),
    )
    payload: dict[str, Any] = json.loads(result.model_dump_json())
    return payload


def _print_diff(
    *,
    original: dict[str, Any] | None,
    replayed: dict[str, Any],
) -> None:
    """
    Print a human-readable unified diff between original and replayed outputs.

    Exit code 3 on drift, 0 on match.
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
        help="Re-execute the pipeline against recorded LLM and tool I/O",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Re-execute and diff against the original final outputs (exit 3 on drift)",
    )
    args = parser.parse_args()

    try:
        if args.replay or args.diff:
            asyncio.run(_replay_pipeline(run_id=args.run_id, show_diff=args.diff))
        else:
            asyncio.run(_print_bundle(run_id=args.run_id))
    except pipeline_errors.ReplayBundleNotFoundError:
        print(f"No recorded ReplayBundle for run_id={args.run_id}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    except pipeline_errors.ReplayBundleSHAMismatchError as exc:
        print(f"Bundle sha mismatch: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(4)
    except pipeline_errors.RecordedReplayMismatchError as exc:
        print(f"Replay drift: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(5)


if __name__ == "__main__":
    main()
