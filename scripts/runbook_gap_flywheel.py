"""
Weekly fingerprint-clustering + auto-PR flywheel for the runbook catalog (F6.M).

Walks recent ``runbook_match`` rows where ``match_method = 'no_match'``, groups
them by deterministic fingerprint
(``sha256(sorted_alert_labels || classification_category)[:16]``), and upserts
:class:`sentinel.data.sql.runbook_gap_cluster.RunbookGapClusterRecord` rows.
Clusters whose ``member_count`` crosses ``flywheel_min_cluster_size`` AND that
do not already have an open draft PR get one auto-generated under
``src/sentinel/plugins/teams/sre/runbooks/AUTOGEN-<fingerprint>/``, committed
to a new ``flywheel/runbook-gap-<fingerprint>`` branch, and pushed up to
GitHub via ``gh pr create --draft``.

Designed to run as a weekly cron / GitHub Actions workflow (see
``docs/operations/runbook-flywheel.md``). Idempotent on re-run: clusters with
an existing ``draft_pr_url`` are skipped without a second PR; new no-match
rows that fingerprint to an existing cluster increment ``flywheel_iteration``
and ``member_count`` so chronicity is observable.

Behaviour by environment:

* **Configured** (gh CLI on PATH, repo write permissions): walks the
  no-match rows, clusters, opens PRs for the qualifying ones.
* **Dry-run** (``--dry-run``): clusters and logs without writing or shelling
  out. Useful for the first scheduled run before operators authorise the
  flywheel to write to the catalog.

The PR-opening function is parameterised on ``subprocess_runner`` and
``git_runner`` callables so unit tests can stub them out without spinning
up a real shell. The default runners delegate to
``asyncio.create_subprocess_exec``.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import attrs
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from sentinel import config as sentinel_config
from sentinel.data import database
from sentinel.domain.runbooks import flywheel as flywheel_mod
from sentinel.utils import logs


_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "src" / "sentinel" / "domain" / "runbooks" / "templates"
_AUTOGEN_RUNBOOK_DIR = _REPO_ROOT / "src" / "sentinel" / "plugins" / "teams" / "sre" / "runbooks"
_TEMPLATE_FILENAME = "autogen_runbook.j2"
_BRANCH_PREFIX = "flywheel/runbook-gap-"
_AUTOGEN_PREFIX = "AUTOGEN-"
_PR_TITLE_TEMPLATE = "feat(runbook): autogen scaffold for runbook-gap fingerprint {fingerprint}"
_SAMPLE_MEMBER_LIMIT = 5

# Fail-closed defaults applied when ``BaseConfiguration`` does not expose the
# F6.M knobs. Once the leader wires the settings additions, the runtime
# values come from the config; these constants are the safety net so the
# script can never be invoked with an unbounded threshold or template team.
_DEFAULT_LOOKBACK_DAYS = 7
_DEFAULT_MIN_CLUSTER_SIZE = 3
_DEFAULT_PR_TEMPLATE_TEAM = "sre-platform"


SubprocessRunner = Callable[[Sequence[str]], Awaitable["SubprocessResult"]]
GitRunner = Callable[[Sequence[str]], Awaitable["SubprocessResult"]]


@dataclass(frozen=True)
class SubprocessResult:
    """Outcome of one shelled-out command. Exit code, captured stdout, stderr."""

    returncode: int
    stdout: str
    stderr: str


@attrs.frozen(kw_only=True, slots=True)
class FlywheelConfig:
    """
    Runtime knobs the script consumes.

    Lives as a frozen attrs class so tests can build one explicitly without
    going through the full ``get_config`` chain. The script's main entrypoint
    derives the runtime instance from ``BaseConfiguration``.
    """

    lookback_days: int
    min_cluster_size: int
    pr_template_team: str


def _resolve_config() -> FlywheelConfig:
    """Build a :class:`FlywheelConfig` from the active ``BaseConfiguration``."""
    cfg = sentinel_config.get_config()
    lookback = getattr(cfg, "flywheel_lookback_days", _DEFAULT_LOOKBACK_DAYS)
    min_size = getattr(cfg, "flywheel_min_cluster_size", _DEFAULT_MIN_CLUSTER_SIZE)
    team = getattr(cfg, "flywheel_pr_template_team", _DEFAULT_PR_TEMPLATE_TEAM)
    return FlywheelConfig(
        lookback_days=int(lookback),
        min_cluster_size=int(min_size),
        pr_template_team=str(team),
    )


def _build_jinja_env(*, templates_dir: Path = _TEMPLATES_DIR) -> Environment:
    """
    Construct the Jinja env used for the autogen runbook.

    ``StrictUndefined`` so missing template variables raise loudly at render
    time -- a silent ``"None"`` in a frontmatter field would yield a runbook
    that fails the loader's schema check downstream.
    """
    # Why autoescape=False: the rendered output is YAML/Markdown for the runbook
    # quartet, not HTML. HTML escaping would corrupt YAML keys ("&amp;" inside
    # strings), break Markdown links, and turn `{{ var }}` substitutions into
    # garbled HTML entities. The templated values are sourced from internal
    # cluster records (no untrusted user input).
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 — YAML/Markdown output (see docstring above)
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )


def render_runbook_skeleton(
    *,
    cluster: flywheel_mod.GapCluster,
    today: date,
    templates_dir: Path = _TEMPLATES_DIR,
) -> str:
    """
    Render the autogen ``RUNBOOK.md`` skeleton from the Jinja template.

    Public so tests can call it directly without spinning up the full
    flywheel run. ``today`` is injected (not derived from the system clock)
    to keep the rendered ``last_validated`` field deterministic in tests.
    """
    env = _build_jinja_env(templates_dir=templates_dir)
    template = env.get_template(_TEMPLATE_FILENAME)
    sample_members = cluster.members[:_SAMPLE_MEMBER_LIMIT]
    return template.render(
        fingerprint=cluster.fingerprint,
        member_count=cluster.member_count,
        services=sorted(cluster.distinct_services),
        distinct_alertnames=sorted(cluster.distinct_alertnames),
        sample_members=sample_members,
        today=today.isoformat(),
    )


def render_stub_tools_yaml() -> str:
    """Return a minimal ``tools.yaml`` skeleton for the autogen runbook."""
    return (
        "allowed_tools:\n"
        "  - name: TODO_replace_me\n"
        "    max_calls: 1\n"
        "denied_tools: []\n"
        "max_total_tool_calls: 10\n"
        "max_loop_iterations: 4\n"
    )


def render_stub_checks_yaml() -> str:
    """Return a minimal ``checks.yaml`` skeleton for the autogen runbook."""
    return (
        "prescribed_checks:\n"
        "  - id: todo_describe_check\n"
        "    description: TODO -- describe one check the agent must perform\n"
        "    suggested_tools: [TODO_replace_me]\n"
        "    required: false\n"
        "groundedness_rules:\n"
        "  - rule_id: every_finding_has_evidence\n"
        "    description: Every finding must cite an evidence_ref\n"
        "body_sanitization:\n"
        "  reject_auto_rendered_urls: true\n"
        "  allowed_url_locations: [canonical_sources, frontmatter]\n"
    )


def render_stub_tests_yaml(cluster: flywheel_mod.GapCluster) -> str:
    """Return a minimal ``tests.yaml`` skeleton seeded from the cluster sample."""
    sample = cluster.members[0]
    return (
        "fixtures:\n"
        f"  - id: {cluster.fingerprint}-sample\n"
        "    alert_payload_path: fixtures/sample.json\n"
        "    expected:\n"
        f"      runbook_id: AUTOGEN-{cluster.fingerprint}\n"
        "      match_method: tag\n"
        "      min_tag_score: 1\n"
        "      required_checks_executed: [todo_describe_check]\n"
        f"      hypothesis_keywords: [{sample.alertname}]\n"
        "      confidence_min: LOW\n"
        "      forbidden_substrings_in_summary: []\n"
    )


def _branch_name(*, fingerprint: str) -> str:
    """Return the deterministic branch name for a cluster fingerprint."""
    return f"{_BRANCH_PREFIX}{fingerprint}"


def _pr_title(*, fingerprint: str) -> str:
    """Return the PR title for a cluster fingerprint."""
    return _PR_TITLE_TEMPLATE.format(fingerprint=fingerprint)


def _pr_body(
    *,
    cluster: flywheel_mod.GapCluster,
    config: FlywheelConfig,
) -> str:
    """Return the markdown PR body describing the gap cluster."""
    services = ", ".join(sorted(cluster.distinct_services)) or "(none)"
    alertnames = ", ".join(sorted(cluster.distinct_alertnames)) or "(none)"
    return (
        "## Auto-generated runbook gap\n\n"
        f"**Fingerprint:** `{cluster.fingerprint}`\n\n"
        f"**Cluster size:** {cluster.member_count} alerts in the last "
        f"{config.lookback_days} days\n\n"
        f"**Distinct services:** {services}\n\n"
        f"**Distinct alertnames:** {alertnames}\n\n"
        f"**Routing:** review queued for `@{config.pr_template_team}`\n\n"
        "### What to do\n\n"
        "1. Edit the rendered `RUNBOOK.md` to fill in the TODO sections.\n"
        "2. Tighten `tools.yaml` to only the tools your investigation needs.\n"
        "3. Wire one real fixture into `tests.yaml` from the sample alerts below.\n"
        "4. Mark the PR ready for review when the runbook passes `just lint` and `just test`.\n\n"
        "### Sample clustered alerts\n\n"
        + "\n".join(
            f"- `{member.alertname}` ({member.service}) at "
            f"{member.matched_at.isoformat()}: {member.summary}"
            for member in cluster.members[:_SAMPLE_MEMBER_LIMIT]
        )
        + "\n"
    )


async def _default_subprocess_runner(args: Sequence[str]) -> SubprocessResult:
    """Run an external command via ``asyncio.create_subprocess_exec`` and capture output."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    returncode = process.returncode if process.returncode is not None else -1
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )


def _write_runbook_quartet(
    *,
    runbook_dir: Path,
    runbook_md: str,
    tools_yaml: str,
    checks_yaml: str,
    tests_yaml: str,
) -> None:
    """Materialise the four-file runbook quartet on disk under ``runbook_dir``."""
    runbook_dir.mkdir(parents=True, exist_ok=True)
    (runbook_dir / "RUNBOOK.md").write_text(runbook_md, encoding="utf-8")
    (runbook_dir / "tools.yaml").write_text(tools_yaml, encoding="utf-8")
    (runbook_dir / "checks.yaml").write_text(checks_yaml, encoding="utf-8")
    (runbook_dir / "tests.yaml").write_text(tests_yaml, encoding="utf-8")


async def _run_or_raise(
    *,
    args: Sequence[str],
    runner: SubprocessRunner,
    context: str,
) -> SubprocessResult:
    """Invoke ``runner(args)`` and raise with structured context on non-zero exit."""
    result = await runner(args)
    if result.returncode != 0:
        msg = (
            f"{context} failed (exit {result.returncode}): "
            f"command={shlex.join(args)} stderr={result.stderr.strip()}"
        )
        raise RuntimeError(msg)
    return result


_PR_URL_TOKEN = "https://github.com/"  # noqa: S105 — public URL prefix, not a credential


def _extract_pr_url(stdout: str) -> str:
    """
    Return the GitHub PR URL emitted by ``gh pr create``.

    ``gh`` writes the URL on its own line. Other lines may carry warnings
    or status output; the first line that starts with the github.com prefix
    is the URL. Falls back to the trimmed full stdout when no such line is
    found, so callers can still log a sensible value.
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(_PR_URL_TOKEN):
            return stripped
    return stdout.strip()


async def _open_draft_pr(
    *,
    cluster: flywheel_mod.GapCluster,
    today: date,
    config: FlywheelConfig,
    repo_root: Path = _REPO_ROOT,
    autogen_root: Path = _AUTOGEN_RUNBOOK_DIR,
    templates_dir: Path = _TEMPLATES_DIR,
    subprocess_runner: SubprocessRunner | None = None,
    git_runner: GitRunner | None = None,
) -> str:
    """
    Render the skeleton, commit it on a fresh branch, and open a draft PR.

    Returns the PR URL captured from ``gh pr create``'s stdout.

    The two runners are split so tests can stub them independently. In
    production both default to :func:`_default_subprocess_runner`. Errors
    from any subprocess raise :class:`RuntimeError` with the failed command
    + stderr embedded; the caller logs and continues with the next cluster.
    """
    runner = subprocess_runner or _default_subprocess_runner
    git = git_runner or runner
    branch = _branch_name(fingerprint=cluster.fingerprint)
    runbook_dir = autogen_root / f"{_AUTOGEN_PREFIX}{cluster.fingerprint}"
    relative_runbook_dir = runbook_dir.relative_to(repo_root)

    runbook_md = render_runbook_skeleton(cluster=cluster, today=today, templates_dir=templates_dir)
    tools_yaml = render_stub_tools_yaml()
    checks_yaml = render_stub_checks_yaml()
    tests_yaml = render_stub_tests_yaml(cluster=cluster)

    # 1. Fresh branch off the current HEAD.
    await _run_or_raise(
        args=("git", "checkout", "-b", branch),
        runner=git,
        context=f"git checkout -b {branch}",
    )
    # 2. Render quartet.
    _write_runbook_quartet(
        runbook_dir=runbook_dir,
        runbook_md=runbook_md,
        tools_yaml=tools_yaml,
        checks_yaml=checks_yaml,
        tests_yaml=tests_yaml,
    )
    # 3. Stage + commit.
    await _run_or_raise(
        args=("git", "add", str(relative_runbook_dir)),
        runner=git,
        context="git add",
    )
    commit_message = (
        f"feat(runbook): autogen scaffold for fingerprint {cluster.fingerprint}\n\n"
        f"Auto-generated by runbook_gap_flywheel.\n"
        f"Routing: @{config.pr_template_team}\n"
    )
    await _run_or_raise(
        args=("git", "commit", "-m", commit_message),
        runner=git,
        context="git commit",
    )
    # 4. Push.
    await _run_or_raise(
        args=("git", "push", "-u", "origin", branch),
        runner=git,
        context=f"git push origin {branch}",
    )
    # 5. Open draft PR.
    pr_args = (
        "gh",
        "pr",
        "create",
        "--draft",
        "--title",
        _pr_title(fingerprint=cluster.fingerprint),
        "--body",
        _pr_body(cluster=cluster, config=config),
    )
    pr_result = await _run_or_raise(
        args=pr_args,
        runner=runner,
        context="gh pr create",
    )
    return _extract_pr_url(pr_result.stdout)


async def _process_cluster(
    *,
    session: object,
    cluster: flywheel_mod.GapCluster,
    config: FlywheelConfig,
    today: date,
    dry_run: bool,
    subprocess_runner: SubprocessRunner | None,
    git_runner: GitRunner | None,
) -> None:
    """
    Upsert one cluster and (when qualified) open its draft PR.

    The qualifier is "post-upsert ``member_count >= min_cluster_size`` AND no
    open draft PR". Both branches log structured events so the operator can
    reconstruct the run from the log timeline alone.
    """
    if cluster.member_count < config.min_cluster_size:
        logs.log_event(
            "runbook_flywheel_cluster_below_threshold",
            params={
                "fingerprint": cluster.fingerprint,
                "member_count": cluster.member_count,
                "min_cluster_size": config.min_cluster_size,
            },
        )
        return

    record = await flywheel_mod.upsert_cluster(session=session, cluster=cluster)  # type: ignore[arg-type]
    if record.member_count < config.min_cluster_size:
        return
    if record.draft_pr_url is not None:
        logs.log_event(
            "runbook_flywheel_pr_already_open",
            params={
                "fingerprint": cluster.fingerprint,
                "pr_url": record.draft_pr_url,
                "iteration": record.flywheel_iteration,
            },
        )
        return
    if dry_run:
        logs.log_event(
            "runbook_flywheel_dry_run_skip_pr",
            params={
                "fingerprint": cluster.fingerprint,
                "member_count": record.member_count,
            },
        )
        return

    try:
        pr_url = await _open_draft_pr(
            cluster=cluster,
            today=today,
            config=config,
            subprocess_runner=subprocess_runner,
            git_runner=git_runner,
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"fingerprint": cluster.fingerprint, "stage": "open_draft_pr"},
        )
        return

    record.draft_pr_url = pr_url
    record.draft_pr_opened_at = datetime.now(tz=UTC)
    await session.commit()  # type: ignore[attr-defined]
    logs.log_event(
        "runbook_flywheel_pr_opened",
        params={
            "fingerprint": cluster.fingerprint,
            "pr_url": pr_url,
            "iteration": record.flywheel_iteration,
        },
    )


async def _run_flywheel(
    *,
    today: date,
    dry_run: bool,
    subprocess_runner: SubprocessRunner | None = None,
    git_runner: GitRunner | None = None,
) -> int:
    """Run one full flywheel pass against the live database."""
    config = _resolve_config()
    cutoff = datetime.now(tz=UTC) - timedelta(days=config.lookback_days)
    logs.log_event(
        "runbook_flywheel_started",
        params={
            "lookback_days": config.lookback_days,
            "min_cluster_size": config.min_cluster_size,
            "dry_run": dry_run,
        },
    )
    async with database.get_session() as session:
        members = await flywheel_mod.query_recent_no_matches(session=session, since=cutoff)
        clusters = flywheel_mod.cluster_no_match_members(members)
        for cluster in clusters:
            await _process_cluster(
                session=session,
                cluster=cluster,
                config=config,
                today=today,
                dry_run=dry_run,
                subprocess_runner=subprocess_runner,
                git_runner=git_runner,
            )
        await session.commit()
    logs.log_event(
        "runbook_flywheel_completed",
        params={
            "no_match_count": len(members),
            "cluster_count": len(clusters),
            "dry_run": dry_run,
        },
    )
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser for the script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Cluster + log without writing rows or opening PRs.",
    )
    return parser


async def _main() -> int:
    """Entry point. Returns the process exit code (0 success / dry-run, 1 errors)."""
    parser = _build_arg_parser()
    args = parser.parse_args()
    today = datetime.now(tz=UTC).date()
    return await _run_flywheel(today=today, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
