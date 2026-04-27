"""
Daily runbook drift-detection sweep entry point (F6.L.3 / F6.L.4).

Runs the three sweeps from :mod:`sentinel.domain.runbooks.drift` against the
on-disk runbook catalog plus the application DB, persists each detected
:class:`drift_mod.DriftEvent` to ``runbook_drift_history`` (deduping against
existing unresolved rows), and posts one Slack alert per fresh row via the
runbook-owner routing in :mod:`sentinel.application.runbooks._drift_notifier`.

Designed to run as a daily cron / GitHub Actions workflow (see
``docs/operations/runbook-drift-cron.md``). Idempotent on re-run: drifts
already represented by an open (unresolved) row are skipped without a second
write or a second Slack post.

Behaviour by environment:

* **Slack configured** (``SLACK_BOT_TOKEN`` set + a resolved channel): each
  fresh drift writes a row + posts a Slack alert.
* **Slack unconfigured**: writes rows; Slack post no-ops with a structured
  log per the vendor-adapter convention.
* **Tools registry empty**: tools-registry sweep no-ops with a structured
  log; the other two sweeps still run.

The matcher is bound with the cron's deterministic disambiguator
(:func:`drift_mod.build_deterministic_disambiguator`) so fixture-replay
re-runs are reproducible across cron ticks — the dedup-by-detail JSONB
match in :func:`persistence_drift.is_open_drift_recorded` depends on
that determinism.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime

import attrs

from sentinel import config as sentinel_config
from sentinel import settings as settings_mod
from sentinel.application.runbooks import _drift_notifier as drift_notifier_mod
from sentinel.data import database
from sentinel.domain.runbooks import (
    drift as drift_mod,
)
from sentinel.domain.runbooks import (
    loader as runbook_loader,
)
from sentinel.domain.runbooks import (
    matcher as matcher_mod,
)
from sentinel.domain.runbooks import (
    models as runbook_models,
)
from sentinel.domain.runbooks import (
    persistence_drift as persistence_drift_mod,
)
from sentinel.utils import logs
from sentinel.vendors import slack as slack_mod


# ---------------------------------------------------------------------------
# Slack adapter wrapper conforming to the notifier's Protocol surface
# ---------------------------------------------------------------------------


@attrs.frozen(kw_only=True, slots=True)
class _SlackVendorAdapter:
    """
    Thin facade over :mod:`sentinel.vendors.slack` that satisfies the
    drift notifier's Protocol.

    Frozen so the script's wiring stays immutable for the duration of the
    run; the underlying Slack client is module-level and lazily constructed
    inside ``vendors.slack`` so the facade carries no state.
    """

    @property
    def is_configured(self) -> bool:
        return slack_mod.is_slack_configured()

    async def post_drift_alert(
        self,
        *,
        channel: str,
        runbook_id: str,
        content_sha: str,
        drift_type: str,
        drift_severity: str,
        suggested_fix: str,
        resolution_pr_template_url: str,
    ) -> None:
        await slack_mod.post_drift_alert(
            channel=channel,
            runbook_id=runbook_id,
            content_sha=content_sha,
            drift_type=drift_type,
            drift_severity=drift_severity,
            suggested_fix=suggested_fix,
            resolution_pr_template_url=resolution_pr_template_url,
        )


# ---------------------------------------------------------------------------
# Matcher binding (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _build_replay_matcher() -> drift_mod.ReplayMatcher:
    """
    Return a :data:`drift_mod.ReplayMatcher` closure bound to the
    cron's deterministic disambiguator.

    The closure captures an empty :class:`runbook_models.Envelope`-shaped
    sentinel because :func:`matcher_mod.match_runbook` accepts the envelope
    only for forward-compatibility (see its docstring) — fixture-replay
    sweeps do not consult tenant-scoped match policy.
    """
    disambiguator = drift_mod.build_deterministic_disambiguator()

    async def _matcher(
        alert: matcher_mod.MatchableAlert,
        runbooks: Mapping[str, runbook_models.Runbook],
    ) -> runbook_models.RunbookMatch:
        # Envelope is reserved for future tenant-scoped policy in the
        # matcher (see match_runbook docstring); the cron path does not
        # consult tenant policy. We pass a sentinel envelope so the API
        # contract holds without requiring a real ingress envelope.
        return await matcher_mod.match_runbook(
            alert=alert,
            envelope=_CRON_SENTINEL_ENVELOPE,  # type: ignore[arg-type]
            runbooks=runbooks,
            disambiguator=disambiguator,
            rag_fallback=None,
        )

    return _matcher


# Sentinel envelope passed to ``match_runbook`` from the cron. The matcher
# only uses it for forward-compatibility (see its docstring) so a None-typed
# placeholder is acceptable on the cron path. The pipeline path passes the
# real ingress envelope.
_CRON_SENTINEL_ENVELOPE: object = None


# ---------------------------------------------------------------------------
# Per-event persist + notify
# ---------------------------------------------------------------------------


async def _persist_and_notify(
    *,
    event: drift_mod.DriftEvent,
    runbooks: Mapping[str, runbook_models.Runbook],
    session: object,
    slack_adapter: drift_notifier_mod._SlackAdapter,
    fallback_channel: str,
) -> bool:
    """
    Dedup, persist, and notify for one drift event. Returns True on a fresh write.

    The session is typed ``object`` because the script wires it via
    :func:`database.get_session` which yields an :class:`AsyncSession`;
    the persistence helpers internally type it correctly. Keeping the
    annotation loose at this layer avoids re-importing ``AsyncSession``
    purely for typing.
    """
    if await persistence_drift_mod.is_open_drift_recorded(session=session, event=event):  # type: ignore[arg-type]
        logs.log_event(
            "runbook_drift_dedup_skipped",
            params={
                "runbook_id": event.runbook_id,
                "drift_type": event.drift_type,
            },
        )
        return False

    await persistence_drift_mod.write_drift_event(session=session, event=event)  # type: ignore[arg-type]

    runbook = runbooks.get(event.runbook_id)
    runbook_owner = runbook.metadata.owner if runbook is not None else None
    await drift_notifier_mod.notify_drift(
        event=event,
        runbook_owner=runbook_owner or None,
        slack_adapter=slack_adapter,
        fallback_channel=fallback_channel,
    )
    return True


# ---------------------------------------------------------------------------
# Catalog walk (the three sweeps)
# ---------------------------------------------------------------------------


async def run_sweeps(
    *,
    runbooks: Mapping[str, runbook_models.Runbook],
    session: object,
    today: date,
    tool_registry: frozenset[str],
    matcher: drift_mod.ReplayMatcher,
) -> tuple[drift_mod.DriftEvent, ...]:
    """
    Run the three drift sweeps against ``runbooks`` and return the
    aggregated events.

    Public so unit tests can call it directly without spinning up the full
    DB session lifecycle. The script's main entry point assembles the
    arguments from :func:`runbook_loader.discover_runbooks` and
    :func:`database.get_session`.
    """
    fixture_events = await drift_mod.sweep_fixture_replays(
        runbooks=runbooks,
        matcher=matcher,
    )
    stale_events = await drift_mod.sweep_stale_runbooks(
        session=session,  # type: ignore[arg-type]
        runbooks=runbooks,
        today=today,
    )
    tools_events = drift_mod.sweep_tools_registry(
        runbooks=runbooks,
        tool_registry=tool_registry,
    )
    return drift_mod.aggregate_events(fixture_events, stale_events, tools_events)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


async def _run_drift_check(*, today: date) -> int:
    """Run one full drift-check pass against the live database + on-disk catalog."""
    cfg = sentinel_config.get_config()
    runbooks_paths = cfg.runbooks_paths
    if not runbooks_paths:
        logs.log_event(
            "runbook_drift_check_skipped_no_runbooks_paths",
            params={"team_id": cfg.team_id},
        )
        return 0

    runbooks = runbook_loader.discover_runbooks(list(runbooks_paths))
    tool_registry = cfg.allowed_tools
    fallback_channel = cfg.runbook_owners_channel
    slack_adapter = _SlackVendorAdapter()
    matcher = _build_replay_matcher()

    logs.log_event(
        "runbook_drift_check_started",
        params={
            "runbook_count": len(runbooks),
            "tool_registry_size": len(tool_registry),
            "fallback_channel_set": bool(fallback_channel),
            "slack_configured": slack_adapter.is_configured,
        },
    )

    fresh_count = 0
    async with database.get_session() as session:
        events = await run_sweeps(
            runbooks=runbooks,
            session=session,
            today=today,
            tool_registry=tool_registry,
            matcher=matcher,
        )
        for event in events:
            wrote = await _persist_and_notify(
                event=event,
                runbooks=runbooks,
                session=session,
                slack_adapter=slack_adapter,
                fallback_channel=fallback_channel,
            )
            if wrote:
                fresh_count += 1
        await session.commit()  # type: ignore[attr-defined]

    logs.log_event(
        "runbook_drift_check_completed",
        params={
            "event_count": len(events),
            "fresh_count": fresh_count,
            "deduped_count": len(events) - fresh_count,
        },
    )
    return 0


async def _main() -> int:
    """Entry point. Returns the process exit code."""
    today = datetime.now(tz=UTC).date()
    return await _run_drift_check(today=today)


# Re-export so static analysis sees the settings module is imported for env-var
# bootstrap even though we don't call it directly here. The script invokes
# ``sentinel_config.get_config()`` which loads ``Settings`` transitively.
_ = (settings_mod,)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
