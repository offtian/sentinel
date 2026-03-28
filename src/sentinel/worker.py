"""
Background worker that polls the job queue and executes pipelines.

This is the module referenced by the Helm chart worker deployment:
``["uv", "run", "python", "-m", "sentinel.worker"]``

The worker claims jobs using ``SELECT ... FOR UPDATE SKIP LOCKED`` so
multiple replicas can run safely without contention.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal

from sentinel import bootstrap
from sentinel.application.jobs import dequeue
from sentinel.application.sre import persist as sre_persist
from sentinel.application.support import persist as support_persist
from sentinel.config import get_config
from sentinel.data import database, job_models
from sentinel.domain.jobs import entities
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import common, sre_investigation, support_review
from sentinel.settings import get_settings
from sentinel.utils import logs


_shutdown_requested = False


def _handle_signal(signum: int, frame: object) -> None:
    global _shutdown_requested  # noqa: PLW0603
    _shutdown_requested = True
    logs.log_event(
        "worker.shutdown_requested",
        params={"signal": signal.Signals(signum).name},
    )


async def _dispatch_job(job_type: str, payload: dict[str, object]) -> str:
    """Route the job to the correct pipeline handler."""
    if job_type == entities.JobType.SRE_INVESTIGATION.value:
        return await _run_sre_investigation(payload)
    if job_type == entities.JobType.SUPPORT_REVIEW.value:
        return await _run_support_review(payload)
    raise ValueError(f"Unknown job type: {job_type}")


async def _execute_job(
    job_record: job_models.JobRequestRecord,
    *,
    worker_id: str,
) -> None:
    """Dispatch a claimed job to the appropriate pipeline."""
    payload = json.loads(job_record.payload_json)
    job_id = job_record.id

    try:
        result_json = await _dispatch_job(job_record.job_type, payload)

        async with database.get_session() as session:
            fresh_record = await dequeue.fetch_job_record(session, job_id=job_id)
            await dequeue.complete_job(
                session,
                job_record=fresh_record,
                result_json=result_json,
                worker_id=worker_id,
            )

    except Exception as exc:
        logs.log_exception(exc, params={"job_id": str(job_id)})
        async with database.get_session() as session:
            fresh_record = await dequeue.fetch_job_record(session, job_id=job_id)
            await dequeue.fail_job(
                session,
                job_record=fresh_record,
                error_message=str(exc),
                worker_id=worker_id,
            )


async def _run_sre_investigation(payload: dict[str, object]) -> str:
    """Execute the SRE investigation pipeline for a job payload."""
    alert = sre_entities.Alert.model_validate(payload)

    cfg = get_config()
    holmes = cfg.build_holmes_adapter()
    pd_client = cfg.pagerduty_client if get_settings().pagerduty_api_key else None

    async def _persist(reply: common.InvestigationReply) -> None:
        if not get_settings().database_url:
            return
        async with database.get_session() as session:
            await sre_persist.save_investigation(
                session,
                alert_source=str(payload.get("source", "webhook")),
                alert_id=reply.alert_id,
                alert_title=str(payload.get("title", reply.alert_id)),
                severity=str(payload.get("severity", "unknown")),
                service=str(payload.get("service", "unknown")),
                root_cause=reply.root_cause,
                remediation=reply.remediation,
                confidence_score=reply.confidence.total if reply.confidence else None,
                findings_json={"summary": reply.findings_summary},
            )

    result = await sre_investigation.investigate_alert(
        alert=alert,
        holmes=holmes,
        pagerduty_client=pd_client,
        persist_fn=_persist,
    )

    return result.model_dump_json()


async def _run_support_review(payload: dict[str, object]) -> str:
    """Execute the support review pipeline for a job payload."""
    ticket = support_entities.Ticket.model_validate(payload)
    cfg = get_config()

    async def _persist(reply: common.SupportReply) -> None:
        if not get_settings().database_url:
            return
        async with database.get_session() as session:
            await support_persist.save_ticket_review(
                session,
                ticket_id=reply.ticket_id,
                ticket_key=reply.ticket_key,
                suggested_response=reply.suggested_response,
                sources_json={"sources": reply.sources} if reply.sources else None,
                confidence_score=reply.confidence.total if reply.confidence else None,
                category=reply.category,
            )

    result = await support_review.review_ticket(
        ticket=ticket,
        document_searcher=cfg.build_document_searcher(),
        ticket_searcher=cfg.build_ticket_searcher(),
        persist_fn=_persist,
    )

    return result.model_dump_json()


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
    async with database.get_session() as session:
        recovered = await dequeue.recover_stale_jobs(session, worker_id=worker_id)
        if recovered:
            logs.log_event(
                "worker.recovered_stale_jobs",
                params={"count": recovered},
            )

    while not _shutdown_requested:
        async with database.get_session() as session:
            job_record = await dequeue.claim_next_job(
                session,
                worker_id=worker_id,
            )

        if job_record is None:
            await asyncio.sleep(poll_interval)
            continue

        try:
            await asyncio.wait_for(
                _execute_job(job_record, worker_id=worker_id),
                timeout=job_timeout,
            )
        except TimeoutError:
            logs.log_event(
                "worker.job_timed_out",
                params={"job_id": str(job_record.id), "timeout": job_timeout},
            )
            async with database.get_session() as session:
                fresh_record = await dequeue.fetch_job_record(session, job_id=job_record.id)
                await dequeue.fail_job(
                    session,
                    job_record=fresh_record,
                    error_message=f"Job timed out after {job_timeout}s",
                    worker_id=worker_id,
                )

    logs.log_event("worker.shutdown_complete", params={"worker_id": worker_id})


async def _main() -> None:
    bootstrap.initialise()

    worker_id = os.environ.get("HOSTNAME", f"worker-{os.getpid()}")

    if get_settings().database_url:
        database.get_engine()
        logs.log_event("worker.database_initialised")

    try:
        await _poll_loop(worker_id=worker_id)
    finally:
        await database.close_engine()


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
