"""
Background worker that polls the job queue and executes pipelines.

This is the module referenced by the Helm chart worker deployment:
``["uv", "run", "python", "-m", "sentinel.worker"]``

The worker claims jobs using ``SELECT ... FOR UPDATE SKIP LOCKED`` so
multiple replicas can run safely without contention.

Supports ``--run-once`` mode for Kubernetes CronJob execution: claims
a single job, executes it, and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from typing import Any

import databases

from sentinel import bootstrap
from sentinel import config as config_mod
from sentinel.application.automations import runner as automation_runner
from sentinel.data import database
from sentinel.data import db as async_db
from sentinel.domain import prompts
from sentinel.domain.jobs import entities
from sentinel.domain.jobs import operations as job_ops
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.pipeline import tracer as pipeline_tracer
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import operations as sre_ops
from sentinel.domain.support import entities as support_entities
from sentinel.domain.support import operations as support_ops
from sentinel.interfaces.graphs import agents as agent_module
from sentinel.interfaces.graphs import common, sre_investigation, support_review
from sentinel.interfaces.graphs.agents import k8s_runner
from sentinel.settings import get_settings
from sentinel.utils import logs


def _collect_model_ids(settings: object, *attr_names: str) -> list[str]:
    """Return model ID strings from the settings object for the given attribute names."""
    return [str(getattr(settings, name, "")) for name in attr_names if getattr(settings, name, "")]


_shutdown_requested = False


def _handle_signal(signum: int, frame: object) -> None:
    global _shutdown_requested  # noqa: PLW0603
    _shutdown_requested = True
    logs.log_event(
        "worker.shutdown_requested",
        params={"signal": signal.Signals(signum).name},
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sentinel background worker")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Claim and execute a single job, then exit. Designed for CronJob usage.",
    )
    return parser.parse_args()


def _get_optional_db() -> databases.Database | None:
    """Return the database connection, or None if not configured."""
    try:
        return async_db.get_db()
    except RuntimeError:
        return None


async def _dispatch_job(job_type: str, payload: dict[str, object]) -> str:
    """Route the job to the correct pipeline handler."""
    if job_type == entities.JobType.SRE_INVESTIGATION.value:
        return await _run_sre_investigation(payload)
    if job_type == entities.JobType.SUPPORT_REVIEW.value:
        return await _run_support_review(payload)
    if job_type == entities.JobType.SCHEDULED_AUTOMATION.value:
        return await _run_scheduled_automation(payload)
    raise ValueError(f"Unknown job type: {job_type}")


async def _execute_job(
    job_dict: dict[str, Any],
    *,
    worker_id: str,
) -> None:
    """Dispatch a claimed job to the appropriate pipeline."""
    payload = json.loads(str(job_dict["payload_json"]))
    job_id = job_dict["id"]

    try:
        result_json = await _dispatch_job(str(job_dict["job_type"]), payload)

        db = async_db.get_db()
        await job_ops.complete_job(
            db=db,
            job_id=job_id,
            result_json=result_json,
            worker_id=worker_id,
        )

    except Exception as exc:
        logs.log_exception(exc, params={"job_id": str(job_id)})
        db = async_db.get_db()
        should_retry = job_dict["retry_count"] < job_dict["max_retries"]
        await job_ops.fail_job(
            db=db,
            job_id=job_id,
            error_message=str(exc),
            worker_id=worker_id,
            should_retry=should_retry,
        )


async def _run_sre_investigation(payload: dict[str, object]) -> str:
    """Execute the SRE investigation pipeline for a job payload."""
    alert = sre_entities.Alert.model_validate(payload)

    cfg = config_mod.get_config()
    settings = get_settings()
    holmes = cfg.build_holmes_adapter()
    pd_client = cfg.pagerduty_client if settings.pagerduty_api_key else None
    k8s_adapter = cfg.build_k8s_investigation_adapter(
        agent_runner=k8s_runner.run_k8s_agent,
    )
    challenger_adapter = cfg.build_challenger_adapter()

    db = _get_optional_db()
    et = pipeline_tracer.ExecutionTracer(db=db)

    # Build replay snapshot metadata — ALL agent prompts
    classifier_tpl = prompts.load_template("alert_classifier")
    analyser_tpl = prompts.load_template("root_cause_analyser")
    agent_prompts = [
        {
            "agent_name": "alert_classifier",
            "prompt_version": classifier_tpl.version,
            "prompt_sha256": classifier_tpl.sha256,
        },
        {
            "agent_name": "root_cause_analyser",
            "prompt_version": analyser_tpl.version,
            "prompt_sha256": analyser_tpl.sha256,
        },
    ]
    input_hash = pipeline_queries.canonical_input_hash(payload=alert.model_dump())
    model_ids = _collect_model_ids(settings, "alert_classifier_llm", "root_cause_llm")

    await et.start_pipeline(
        pipeline_type="sre_investigation",
        input_data=alert.model_dump(),
        input_hash=input_hash,
        model_ids_json=model_ids,
        mcp_endpoints_json=[],
        skill_activations_json=[],
        # Keep lead-agent scalar fields for backward compatibility
        prompt_version=classifier_tpl.version,
        prompt_sha256=classifier_tpl.sha256,
        prompt_text=classifier_tpl.system_text,
        agent_prompts_json=agent_prompts,
    )

    async def _persist(reply: common.InvestigationReply) -> None:
        if db is None:
            return
        await sre_ops.persist_investigation(
            db=db,
            alert_source=str(payload.get("source", "webhook")),
            alert_id=reply.alert_id,
            alert_title=str(payload.get("title", reply.alert_id)),
            severity=str(payload.get("severity", "unknown")),
            service=str(payload.get("service", "unknown")),
            root_cause=reply.root_cause,
            remediation=reply.remediation,
            confidence_score=reply.confidence.total if reply.confidence else None,
            findings_json={"summary": reply.findings_summary},
            trace_id=et.trace_id,
        )

    shared_mcp = cfg.build_mcp_toolsets()
    observability_toolset = cfg.build_observability_toolset(
        service_name=str(payload.get("service", "")),
    )

    try:
        result = await sre_investigation.investigate_alert(
            alert=alert,
            agent_for=cfg.agent_for,
            holmes=holmes,
            pagerduty_client=pd_client,
            persist_fn=_persist,
            trace_collector=et,
            classifier_toolsets=shared_mcp,
            analyser_toolsets=(observability_toolset, *shared_mcp),
            k8s_adapter=k8s_adapter,
            challenger_adapter=challenger_adapter,
        )
    except Exception:
        await et.complete_pipeline(status="failed", error_message="pipeline raised")
        raise

    result_data = json.loads(result.model_dump_json())
    await et.complete_pipeline(
        status="completed",
        output_data=result_data,
        final_reply=result_data,
    )

    return result.model_dump_json()


async def _run_support_review(payload: dict[str, object]) -> str:
    """Execute the support review pipeline for a job payload."""
    ticket = support_entities.Ticket.model_validate(payload)
    cfg = config_mod.get_config()
    settings = get_settings()

    db = _get_optional_db()
    et = pipeline_tracer.ExecutionTracer(db=db)

    # Build replay snapshot metadata — ALL agent prompts
    reviewer_tpl = prompts.load_template("ticket_reviewer")
    drafter_tpl = prompts.load_template("response_drafter")
    agent_prompts = [
        {
            "agent_name": "ticket_reviewer",
            "prompt_version": reviewer_tpl.version,
            "prompt_sha256": reviewer_tpl.sha256,
        },
        {
            "agent_name": "response_drafter",
            "prompt_version": drafter_tpl.version,
            "prompt_sha256": drafter_tpl.sha256,
        },
    ]
    input_hash = pipeline_queries.canonical_input_hash(payload=ticket.model_dump())
    model_ids = _collect_model_ids(settings, "ticket_reviewer_llm", "response_drafter_llm")

    await et.start_pipeline(
        pipeline_type="support_review",
        input_data=ticket.model_dump(),
        input_hash=input_hash,
        model_ids_json=model_ids,
        mcp_endpoints_json=[],
        skill_activations_json=[],
        # Keep lead-agent scalar fields for backward compatibility
        prompt_version=reviewer_tpl.version,
        prompt_sha256=reviewer_tpl.sha256,
        prompt_text=reviewer_tpl.system_text,
        agent_prompts_json=agent_prompts,
    )

    async def _persist(reply: common.SupportReply) -> None:
        if db is None:
            return
        await support_ops.persist_ticket_review(
            db=db,
            ticket_id=reply.ticket_id,
            ticket_key=reply.ticket_key,
            suggested_response=reply.suggested_response,
            sources_json={"sources": reply.sources} if reply.sources else None,
            confidence_score=reply.confidence.total if reply.confidence else None,
            category=reply.category,
            trace_id=et.trace_id,
        )

    shared_mcp = cfg.build_mcp_toolsets()

    try:
        result = await support_review.review_ticket(
            ticket=ticket,
            agent_for=cfg.agent_for,
            document_searcher=cfg.build_document_searcher(),
            ticket_searcher=cfg.build_ticket_searcher(),
            persist_fn=_persist,
            trace_collector=et,
            reviewer_toolsets=(cfg.build_ticket_triage_toolset(), *shared_mcp),
            drafter_toolsets=(cfg.build_support_search_toolset(), *shared_mcp),
        )
    except Exception:
        await et.complete_pipeline(status="failed", error_message="pipeline raised")
        raise

    result_data = json.loads(result.model_dump_json())
    await et.complete_pipeline(
        status="completed",
        output_data=result_data,
        final_reply=result_data,
    )

    return result.model_dump_json()


async def _run_scheduled_automation(payload: dict[str, object]) -> str:
    """Execute a scheduled automation job."""
    automation_name = str(payload.get("automation_name", ""))
    raw_params = payload.get("params", {}) or {}
    params: dict[str, object] = dict(raw_params) if isinstance(raw_params, dict) else {}

    result = await automation_runner.run_automation(
        automation_name=automation_name,
        params=params,
    )
    return json.dumps(result, default=str)


async def _poll_loop(*, worker_id: str) -> None:
    """Main poll loop: claim and execute jobs until shutdown is requested."""
    poll_interval = get_settings().worker_poll_interval
    job_timeout = get_settings().worker_job_timeout

    logs.log_event(
        "worker.started",
        params={
            "worker_id": worker_id,
            "poll_interval": poll_interval,
            "job_timeout": job_timeout,
        },
    )

    # Recover any jobs left running by a previous crash of this worker
    db = async_db.get_db()
    await job_ops.recover_stale_jobs(db=db, worker_id=worker_id)

    while not _shutdown_requested:
        db = async_db.get_db()
        job_dict = await job_ops.claim_next_job(db=db, worker_id=worker_id)

        if job_dict is None:
            await asyncio.sleep(poll_interval)
            continue

        try:
            await asyncio.wait_for(
                _execute_job(job_dict, worker_id=worker_id),
                timeout=job_timeout,
            )
        except TimeoutError:
            logs.log_event(
                "worker.job_timed_out",
                params={"job_id": str(job_dict["id"]), "timeout": job_timeout},
            )
            db = async_db.get_db()
            should_retry = job_dict["retry_count"] < job_dict["max_retries"]
            await job_ops.fail_job(
                db=db,
                job_id=job_dict["id"],
                error_message=f"Job timed out after {job_timeout}s",
                worker_id=worker_id,
                should_retry=should_retry,
            )

    logs.log_event("worker.shutdown_complete", params={"worker_id": worker_id})


async def _run_once(*, worker_id: str) -> None:
    """Claim and execute a single job, then exit. Designed for CronJob usage."""
    job_timeout = get_settings().worker_job_timeout

    logs.log_event(
        "worker.run_once_started",
        params={"worker_id": worker_id, "job_timeout": job_timeout},
    )

    db = async_db.get_db()
    job_dict = await job_ops.claim_next_job(db=db, worker_id=worker_id)

    if job_dict is None:
        logs.log_event("worker.run_once_no_jobs")
        return

    try:
        await asyncio.wait_for(
            _execute_job(job_dict, worker_id=worker_id),
            timeout=job_timeout,
        )
    except TimeoutError:
        logs.log_event(
            "worker.job_timed_out",
            params={"job_id": str(job_dict["id"]), "timeout": job_timeout},
        )
        db = async_db.get_db()
        should_retry = job_dict["retry_count"] < job_dict["max_retries"]
        await job_ops.fail_job(
            db=db,
            job_id=job_dict["id"],
            error_message=f"Job timed out after {job_timeout}s",
            worker_id=worker_id,
            should_retry=should_retry,
        )

    logs.log_event("worker.run_once_complete", params={"worker_id": worker_id})


async def _main() -> None:
    bootstrap.initialise()
    cfg = config_mod.get_config()
    cfg.load_agents(agent_module=agent_module)
    args = _parse_args()

    worker_id = os.environ.get("HOSTNAME", f"worker-{os.getpid()}")

    if get_settings().database_url:
        database.get_engine()
        await async_db.connect_db()
        logs.log_event("worker.database_initialised")

    try:
        if args.run_once:
            await _run_once(worker_id=worker_id)
        else:
            await _poll_loop(worker_id=worker_id)
    finally:
        if get_settings().database_url:
            await async_db.disconnect_db()
        await database.close_engine()


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
