# Slack Vendor Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `vendors/slack.py` into a typed, modular package that eliminates Block Kit duplication, adds type-safe event parsing, wraps the raw SDK behind `AsyncSlackClient`, and improves observability.

**Architecture:** Convert the flat `vendors/slack.py` (455 lines) into a `vendors/slack/` package with four focused modules — `_blocks.py` (all Block Kit builders), `_parsers.py` (typed Pydantic event models), `_client.py` (`AsyncSlackClient` wrapper), and `__init__.py` (posting functions + backward-compat public API). Event handlers consume the new modules — removing the two duplicate Block Kit functions that currently live there.

**Tech Stack:** `slack_sdk.web.async_client.AsyncWebClient`, `slack_bolt.async_app.AsyncApp`, `pydantic.BaseModel`, `structlog` (via `sentinel.utils.logs`)

---

## Scope

### In scope

- Create `vendors/slack/` package (rename `vendors/slack.py` → `vendors/slack/__init__.py`; add `_blocks.py`, `_parsers.py`, `_client.py`)
- Consolidate all Block Kit builders into `vendors/slack/_blocks.py` (removing the two copies in `event_handlers.py`)
- Add type-safe event models (`MentionEvent`, `MessageEvent`) in `vendors/slack/_parsers.py`; update `event_handlers.py` to use them
- Wrap `AsyncWebClient` in `vendors/slack/_client.py` (`AsyncSlackClient` class with typed returns and error wrapping); update posting functions in `__init__.py` to use it
- Create `interfaces/slack/constants.py` for action ID / block ID strings
- Add `ExceptionGroup` handling to `handle_app_mention` / `handle_direct_message`
- Add structured event-type logging constants to event handlers

### Out of scope

- Background task support (asyncio retry loop) — own plan if needed
- Slack interactive action/view/shortcut handlers — only when those interactions are added
- Migrating existing integration tests off `unittest.mock.patch` to use `AsyncSlackClient`
- `interfaces/chat/app.py` and `interfaces/slack/event_handlers.py` LangGraph routing (covered by langgraph-sre-migration plan)

---

## Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Package vs flat module | Convert to `vendors/slack/` package | 455-line module violates 400-line guideline; sub-modules have clear single responsibilities |
| Block Kit builder signatures | Flat primitives (strings), not domain objects | Vendor layer must not import domain entities — callers unpack their models |
| Parser models base class | `pydantic.BaseModel(frozen=True)` | Boundary types per `python.md` exception; validated at ingress point |
| `AsyncSlackClient` error | Wrap `slack_sdk.errors.SlackApiError` into a local `SlackApiError` | Prevents SDK types leaking into callers; allows mocking without SDK dep |
| Backward compat | `vendors/slack/__init__.py` re-exports all public names unchanged | `from sentinel.vendors import slack` still resolves; zero caller churn |
| `constants.py` location | `interfaces/slack/constants.py` | Action IDs belong to the interface layer, not the vendor |

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/sentinel/vendors/slack.py` | → rename → `vendors/slack/__init__.py` | Posting functions + public API + backward-compat re-exports |
| `src/sentinel/vendors/slack/_blocks.py` | CREATE | All Block Kit builders (investigation, support, approval, drift) |
| `src/sentinel/vendors/slack/_parsers.py` | CREATE | `MentionEvent`, `MessageEvent` Pydantic models + parser functions |
| `src/sentinel/vendors/slack/_client.py` | CREATE | `AsyncSlackClient` wrapper + `SlackApiError` + singleton factory |
| `src/sentinel/interfaces/slack/constants.py` | CREATE | `ACTION_APPROVE_INVESTIGATION`, `ACTION_REJECT_INVESTIGATION`, block ID strings |
| `src/sentinel/interfaces/slack/event_handlers.py` | MODIFY | Remove duplicate Block Kit functions; use parsers; use constants; add `ExceptionGroup` handling |
| `tests/unit/vendors/slack/__init__.py` | CREATE | Package marker |
| `tests/unit/vendors/slack/test_blocks.py` | CREATE | Block Kit builder unit tests |
| `tests/unit/vendors/slack/test_parsers.py` | CREATE | Event parser unit tests |
| `tests/unit/vendors/slack/test_client.py` | CREATE | `AsyncSlackClient` unit tests |
| `tests/unit/interfaces/slack/test_event_handlers.py` | CREATE | Handler observability + error path tests |

---

## Tasks

### Task 1: Create `vendors/slack/_blocks.py` — consolidated Block Kit builders

**Files:**
- Create: `src/sentinel/vendors/slack/_blocks.py`
- Create: `tests/unit/vendors/slack/__init__.py` (empty)
- Create: `tests/unit/vendors/slack/test_blocks.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/vendors/slack/test_blocks.py
from __future__ import annotations

from sentinel.vendors.slack import _blocks as slack_blocks


class TestInvestigationSummaryBlocks:
    def test_includes_alert_id_and_confidence(self) -> None:
        # Given an investigation result with high confidence
        blocks = slack_blocks.investigation_summary_blocks(
            alert_id="P-001",
            alert_title="CPU spike",
            root_cause="OOM killer",
            remediation="Increase limit",
            confidence_label="High",
            findings_summary="Pod OOMKilled 3 times",
        )

        # When we check the header and fields section
        texts = _all_texts(blocks)

        # Then alert ID, confidence emoji, and root cause are present
        assert any("P-001" in t for t in texts)
        assert any(":large_green_circle:" in t for t in texts)
        assert any("OOM killer" in t for t in texts)

    def test_omits_remediation_block_when_none(self) -> None:
        # Given no remediation supplied
        blocks = slack_blocks.investigation_summary_blocks(
            alert_id="P-002",
            alert_title="Disk full",
            root_cause="Log rotation disabled",
            remediation=None,
            confidence_label="Low",
            findings_summary="",
        )

        # Then no remediation section appears
        texts = _all_texts(blocks)
        assert not any("Remediation" in t for t in texts)

    def test_unknown_confidence_falls_back_to_red_circle(self) -> None:
        # Given an unrecognised confidence label
        blocks = slack_blocks.investigation_summary_blocks(
            alert_id="X",
            alert_title="X",
            root_cause=None,
            remediation=None,
            confidence_label="Unknown",
            findings_summary="",
        )

        texts = _all_texts(blocks)
        assert any(":red_circle:" in t for t in texts)


class TestApprovalRequestBlocks:
    def test_includes_approve_and_reject_buttons(self) -> None:
        # Given an investigation pending approval
        blocks = slack_blocks.approval_request_blocks(
            investigation_id="inv-123",
            alert_id="P-001",
            alert_title="CPU spike",
            root_cause="OOM",
            remediation="Scale up",
            confidence_label="Medium",
            findings_summary="",
        )

        # Then an actions block with both buttons appears
        actions = [b for b in blocks if b["type"] == "actions"]
        assert len(actions) == 1
        elements = actions[0]["elements"]
        action_ids = [e["action_id"] for e in elements]
        assert "approve_investigation" in action_ids
        assert "reject_investigation" in action_ids

    def test_block_id_encodes_investigation_id(self) -> None:
        # Given investigation_id "inv-456"
        blocks = slack_blocks.approval_request_blocks(
            investigation_id="inv-456",
            alert_id="P-002",
            alert_title="Disk full",
            root_cause=None,
            remediation=None,
            confidence_label=None,
            findings_summary="",
        )

        # Then the actions block_id contains the investigation_id
        actions = [b for b in blocks if b["type"] == "actions"]
        assert actions[0]["block_id"] == "approval_inv-456"


class TestSupportSummaryBlocks:
    def test_includes_ticket_key_and_response(self) -> None:
        # Given a support reply
        blocks = slack_blocks.support_summary_blocks(
            ticket_key="SUPP-99",
            ticket_summary="Cannot login",
            suggested_response="Reset password via /account",
            confidence_label="High",
            category="account",
        )

        texts = _all_texts(blocks)
        assert any("SUPP-99" in t for t in texts)
        assert any("Reset password" in t for t in texts)


class TestDriftAlertBlocks:
    def test_includes_runbook_and_severity(self) -> None:
        # Given a drift event
        blocks = slack_blocks.drift_alert_blocks(
            runbook_id="runbook/deploy-rollback",
            content_sha="abc123",
            drift_type="content_changed",
            drift_severity="high",
            suggested_fix="Re-sync from source",
            resolution_pr_template_url="https://github.com/org/repo/compare",
        )

        texts = _all_texts(blocks)
        assert any("runbook/deploy-rollback" in t for t in texts)
        assert any(":red_circle:" in t for t in texts)


def _all_texts(blocks: list[dict]) -> list[str]:
    """Recursively collect all text strings from a Block Kit payload."""
    texts: list[str] = []
    for block in blocks:
        _collect_texts(block, texts)
    return texts


def _collect_texts(obj: object, acc: list[str]) -> None:
    if isinstance(obj, str):
        acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_texts(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_texts(item, acc)
```

- [x] **Step 2: Run tests to confirm they fail**

```bash
just test tests/unit/vendors/slack/test_blocks.py
```

Expected: `ModuleNotFoundError: No module named 'sentinel.vendors.slack._blocks'`

- [x] **Step 3: Create the package structure and `_blocks.py`**

Create `src/sentinel/vendors/slack/__init__.py` (empty — will be populated in Task 2). Then:

```python
# src/sentinel/vendors/slack/_blocks.py
from __future__ import annotations


_CONFIDENCE_EMOJI: dict[str, str] = {
    "High": ":large_green_circle:",
    "Medium": ":large_yellow_circle:",
}
_CONFIDENCE_EMOJI_DEFAULT = ":red_circle:"

_DRIFT_SEVERITY_EMOJI: dict[str, str] = {
    "high": ":red_circle:",
    "medium": ":large_yellow_circle:",
    "low": ":large_blue_circle:",
}


def investigation_summary_blocks(
    *,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for an investigation summary."""
    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Investigation: {alert_title[:140]}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Alert ID:* {alert_id}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:* {confidence_emoji} {confidence_label or 'Unknown'}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause:*\n{root_cause or '_Unable to determine._'}",
            },
        },
    ]

    if remediation:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Remediation:*\n{remediation}"},
            }
        )

    if findings_summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Findings:*\n{findings_summary}"},
            }
        )

    return blocks


def approval_request_blocks(
    *,
    investigation_id: str,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for an approval request with Approve/Reject buttons."""
    blocks = investigation_summary_blocks(
        alert_id=alert_id,
        alert_title=f"Approval Required: {alert_title}",
        root_cause=root_cause,
        remediation=remediation,
        confidence_label=confidence_label,
        findings_summary=findings_summary,
    )
    blocks.append(
        {
            "type": "actions",
            "block_id": f"approval_{investigation_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Publish"},
                    "style": "primary",
                    "action_id": "approve_investigation",
                    "value": investigation_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "reject_investigation",
                    "value": investigation_id,
                },
            ],
        }
    )
    return blocks


def support_summary_blocks(
    *,
    ticket_key: str,
    ticket_summary: str,
    suggested_response: str,
    confidence_label: str | None,
    category: str | None,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for a support response suggestion."""
    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Response Suggestion: {ticket_key}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Ticket:* {ticket_summary[:200]}"},
                {"type": "mrkdwn", "text": f"*Category:* {category or 'Unknown'}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:* {confidence_emoji} {confidence_label or 'Unknown'}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Suggested Response:*\n{suggested_response}"},
        },
    ]


def drift_alert_blocks(
    *,
    runbook_id: str,
    content_sha: str,
    drift_type: str,
    drift_severity: str,
    suggested_fix: str,
    resolution_pr_template_url: str,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for a runbook drift alert."""
    severity_emoji = _DRIFT_SEVERITY_EMOJI.get(drift_severity, ":white_circle:")

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Runbook Drift: {drift_type}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Runbook:* `{runbook_id}`"},
                {"type": "mrkdwn", "text": f"*Content SHA:* `{content_sha}`"},
                {"type": "mrkdwn", "text": f"*Severity:* {severity_emoji} {drift_severity}"},
                {"type": "mrkdwn", "text": f"*Drift type:* {drift_type}"},
            ],
        },
    ]
    if suggested_fix:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Suggested fix:*\n{suggested_fix}"},
            }
        )
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{resolution_pr_template_url}|Open resolution PR template>",
            },
        }
    )
    return blocks
```

- [x] **Step 4: Run tests to confirm they pass**

```bash
just test tests/unit/vendors/slack/test_blocks.py
```

Expected: 7 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/vendors/slack/__init__.py \
        src/sentinel/vendors/slack/_blocks.py \
        tests/unit/vendors/slack/__init__.py \
        tests/unit/vendors/slack/test_blocks.py
git commit -m "feat(vendors/slack): create package skeleton and consolidated _blocks.py"
```

---

### Task 2: Convert `vendors/slack.py` → `vendors/slack/__init__.py` and use `_blocks.py`

**Files:**
- Delete: `src/sentinel/vendors/slack.py`
- Modify: `src/sentinel/vendors/slack/__init__.py` (populate from old slack.py, using `_blocks`)

- [x] **Step 1: Verify callers before touching anything**

```bash
grep -rn "from sentinel.vendors import slack\|from sentinel.vendors.slack" \
    src/sentinel/ | grep -v __pycache__
```

Expected output (three callers, all using `from sentinel.vendors import slack`):
```
src/sentinel/interfaces/graphs/investigation.py:…from sentinel.vendors import slack
src/sentinel/interfaces/workflows/sre_investigation.py:…from sentinel.vendors import slack as slack_mod
src/sentinel/application/runbooks/_drift_notifier.py:… (protocol reference only)
```

- [x] **Step 2: Populate `vendors/slack/__init__.py` from old `slack.py`**

Copy the entire content of `src/sentinel/vendors/slack.py` into `src/sentinel/vendors/slack/__init__.py`, then make three edits:

**Edit 1** — add `_blocks` import at the top (after existing imports):
```python
from sentinel.vendors.slack import _blocks as _slack_blocks
```

**Edit 2** — replace `_build_investigation_blocks(...)` call inside `post_investigation_summary` with:
```python
blocks = _slack_blocks.investigation_summary_blocks(
    alert_id=alert_id,
    alert_title=alert_title,
    root_cause=root_cause,
    remediation=remediation,
    confidence_label=confidence_label,
    findings_summary=findings_summary,
)
```

**Edit 3** — replace the inline blocks construction inside `post_approval_request` with:
```python
blocks = _slack_blocks.approval_request_blocks(
    investigation_id=investigation_id,
    alert_id=alert_id,
    alert_title=alert_title,
    root_cause=root_cause,
    remediation=remediation,
    confidence_label=confidence_label,
    findings_summary=findings_summary,
)
```

**Edit 4** — replace `_build_support_blocks(...)` call inside `post_support_suggestion` with:
```python
blocks = _slack_blocks.support_summary_blocks(
    ticket_key=ticket_key,
    ticket_summary=ticket_summary,
    suggested_response=suggested_response,
    confidence_label=confidence_label,
    category=category,
)
```

**Edit 5** — replace `_build_drift_blocks(...)` call inside `post_drift_alert` with:
```python
blocks = _slack_blocks.drift_alert_blocks(
    runbook_id=runbook_id,
    content_sha=content_sha,
    drift_type=drift_type,
    drift_severity=drift_severity,
    suggested_fix=suggested_fix,
    resolution_pr_template_url=resolution_pr_template_url,
)
```

**Edit 6** — delete the four private `_build_*` functions and the `_CONFIDENCE_EMOJI`, `_DRIFT_SEVERITY_EMOJI` dicts from `__init__.py` (they now live in `_blocks.py`).

- [x] **Step 3: Delete the old flat module**

```bash
git rm src/sentinel/vendors/slack.py
```

- [x] **Step 4: Run full test suite**

```bash
just test && just lint
```

Expected: all tests green, no import errors. The package `vendors/slack/__init__.py` exports the same public names (`post_investigation_summary`, `post_approval_request`, `post_support_suggestion`, `post_drift_alert`, `is_slack_configured`, `store_pending_approval`) so all callers are unaffected.

- [x] **Step 5: Commit**

```bash
git add src/sentinel/vendors/slack/__init__.py
git commit -m "refactor(vendors/slack): convert to package, use _blocks for Block Kit construction"
```

---

### Task 3: Remove duplicate Block Kit functions from `event_handlers.py`

**Files:**
- Modify: `src/sentinel/interfaces/slack/event_handlers.py`

- [x] **Step 1: Identify the two duplicate functions**

In `event_handlers.py`, the following functions duplicate logic already in `_blocks.py`:
- `_investigation_blocks(reply: InvestigationReply, alert_title: str)` — lines ~71–119
- `_support_blocks(reply: SupportReply, ticket_summary: str)` — lines ~122–169

- [x] **Step 2: Update `event_handlers.py`**

Add import at module level (alongside existing imports):
```python
from sentinel.vendors.slack import _blocks as slack_blocks
```

Replace `_investigation_blocks(reply, alert.title)` call site in `_run_sre` with:
```python
blocks = slack_blocks.investigation_summary_blocks(
    alert_id=reply.alert_id,
    alert_title=alert.title,
    root_cause=reply.root_cause,
    remediation=reply.remediation,
    confidence_label=reply.confidence.label.value if reply.confidence else None,
    findings_summary=reply.findings_summary or "",
)
```

Replace `_support_blocks(reply, ticket.summary)` call site in `_run_support` with:
```python
blocks = slack_blocks.support_summary_blocks(
    ticket_key=ticket.key,
    ticket_summary=ticket.summary,
    suggested_response=reply.suggested_response,
    confidence_label=reply.confidence.label.value if reply.confidence else None,
    category=reply.category,
)
```

Delete the entire `_investigation_blocks` and `_support_blocks` function definitions.

- [x] **Step 3: Run tests**

```bash
just test && just lint
```

Expected: all green. The `_investigation_blocks_from_outcome` function added by the LangGraph migration (T34) should remain untouched.

- [ ] **Step 4: Commit**

```bash
git add src/sentinel/interfaces/slack/event_handlers.py
git commit -m "refactor(slack): remove duplicate Block Kit functions from event_handlers"
```

---

### Task 4: Create `vendors/slack/_parsers.py` — typed event models

**Files:**
- Create: `src/sentinel/vendors/slack/_parsers.py`
- Create: `tests/unit/vendors/slack/test_parsers.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/vendors/slack/test_parsers.py
from __future__ import annotations

import pytest

from sentinel.vendors.slack import _parsers as slack_parsers


class TestParseMentionEvent:
    def test_extracts_user_channel_thread_and_text(self) -> None:
        # Given a raw Slack app_mention event dict
        raw = {
            "type": "app_mention",
            "user": "U12345",
            "channel": "C99999",
            "ts": "1700000001.000000",
            "thread_ts": "1700000000.000000",
            "text": "<@UBOT> investigate CPU spike",
        }

        # When parsed
        event = slack_parsers.parse_mention_event(raw)

        # Then structured fields are accessible
        assert event.user_id == "U12345"
        assert event.channel == "C99999"
        assert event.thread_ts == "1700000000.000000"
        assert event.message_ts == "1700000001.000000"
        assert event.text == "<@UBOT> investigate CPU spike"

    def test_thread_ts_falls_back_to_ts_when_absent(self) -> None:
        # Given a mention not inside a thread
        raw = {
            "type": "app_mention",
            "user": "U12345",
            "channel": "C99999",
            "ts": "1700000001.000000",
            "text": "@bot help",
        }

        # When parsed
        event = slack_parsers.parse_mention_event(raw)

        # Then thread_ts mirrors ts
        assert event.thread_ts == "1700000001.000000"


class TestParseMessageEvent:
    def test_extracts_standard_dm_fields(self) -> None:
        # Given a DM message event
        raw = {
            "type": "message",
            "user": "U54321",
            "channel": "D11111",
            "ts": "1700000002.000000",
            "text": "what's the root cause?",
            "channel_type": "im",
        }

        # When parsed
        event = slack_parsers.parse_message_event(raw)

        # Then fields are available
        assert event.user_id == "U54321"
        assert event.channel == "D11111"
        assert event.channel_type == "im"

    def test_missing_user_raises_validation_error(self) -> None:
        # Given a malformed event with no user field
        raw = {"type": "message", "channel": "C1", "ts": "1.0", "text": "hi"}

        # Then parsing raises ValidationError (not a silent empty string)
        with pytest.raises(Exception):
            slack_parsers.parse_message_event(raw)
```

- [x] **Step 2: Run tests to confirm failure**

```bash
just test tests/unit/vendors/slack/test_parsers.py
```

Expected: `ModuleNotFoundError: No module named 'sentinel.vendors.slack._parsers'`

- [x] **Step 3: Implement `_parsers.py`**

```python
# src/sentinel/vendors/slack/_parsers.py
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MentionEvent(BaseModel, frozen=True):
    """Parsed Slack app_mention event."""

    user_id: str
    channel: str
    thread_ts: str
    message_ts: str
    text: str


class MessageEvent(BaseModel, frozen=True):
    """Parsed Slack message event (DM or channel)."""

    user_id: str
    channel: str
    thread_ts: str
    message_ts: str
    text: str
    channel_type: str


def parse_mention_event(event: dict[str, Any]) -> MentionEvent:
    """Parse a raw Slack app_mention event dict into a typed model."""
    ts = event["ts"]
    return MentionEvent(
        user_id=event["user"],
        channel=event["channel"],
        thread_ts=event.get("thread_ts") or ts,
        message_ts=ts,
        text=event.get("text", ""),
    )


def parse_message_event(event: dict[str, Any]) -> MessageEvent:
    """Parse a raw Slack message event dict into a typed model."""
    ts = event["ts"]
    return MessageEvent(
        user_id=event["user"],
        channel=event["channel"],
        thread_ts=event.get("thread_ts") or ts,
        message_ts=ts,
        text=event.get("text", ""),
        channel_type=event.get("channel_type", ""),
    )
```

- [x] **Step 4: Run tests to confirm they pass**

```bash
just test tests/unit/vendors/slack/test_parsers.py
```

Expected: 4 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/vendors/slack/_parsers.py tests/unit/vendors/slack/test_parsers.py
git commit -m "feat(vendors/slack): typed event models in _parsers.py"
```

---

### Task 5: Update `event_handlers.py` to use typed event parsers

**Files:**
- Modify: `src/sentinel/interfaces/slack/event_handlers.py`
- Create: `tests/unit/interfaces/slack/__init__.py` (if missing)
- Create: `tests/unit/interfaces/slack/test_event_handlers.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/interfaces/slack/test_event_handlers.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.slack import event_handlers


_MENTION_EVENT = {
    "type": "app_mention",
    "user": "U12345",
    "channel": "C99999",
    "ts": "1700000001.000000",
    "thread_ts": "1700000000.000000",
    "text": "<@UBOT> investigate CPU spike",
}

_DM_EVENT = {
    "type": "message",
    "user": "U54321",
    "channel": "D11111",
    "ts": "1700000002.000000",
    "text": "high latency on checkout",
    "channel_type": "im",
}


class TestHandleAppMention:
    @pytest.mark.asyncio
    async def test_passes_clean_text_to_handle_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a mention event and a mocked _handle_request
        captured: dict = {}

        async def fake_handle(text, *, client, channel, thread_ts, user_id):
            captured["text"] = text
            captured["channel"] = channel

        monkeypatch.setattr(event_handlers, "_handle_request", fake_handle)
        fake_client = mock.AsyncMock()
        fake_ack = mock.AsyncMock()

        # When the handler is invoked
        await event_handlers.handle_app_mention(
            event=_MENTION_EVENT, client=fake_client, ack=fake_ack
        )

        # Then _handle_request receives the text and correct channel
        assert captured["channel"] == "C99999"
        assert "investigate CPU spike" in captured["text"]

    @pytest.mark.asyncio
    async def test_posts_error_message_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given _handle_request raises unexpectedly
        async def boom(*_, **__):
            raise RuntimeError("agent down")

        monkeypatch.setattr(event_handlers, "_handle_request", boom)
        monkeypatch.setattr(event_handlers.logs, "log_exception", mock.MagicMock())
        fake_client = mock.AsyncMock()
        fake_ack = mock.AsyncMock()

        # When the handler is invoked
        await event_handlers.handle_app_mention(
            event=_MENTION_EVENT, client=fake_client, ack=fake_ack
        )

        # Then a user-facing error is posted to the thread
        fake_client.chat_postMessage.assert_awaited_once()
        call_kwargs = fake_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C99999"
        assert "wrong" in call_kwargs["text"].lower() or "error" in call_kwargs["text"].lower()


class TestHandleDirectMessage:
    @pytest.mark.asyncio
    async def test_skips_bot_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given a message event from a bot
        bot_event = {**_DM_EVENT, "bot_id": "B999"}
        captured: list = []

        async def fake_handle(*_, **__):
            captured.append(True)

        monkeypatch.setattr(event_handlers, "_handle_request", fake_handle)
        fake_client = mock.AsyncMock()
        fake_ack = mock.AsyncMock()

        # When the handler is invoked
        await event_handlers.handle_direct_message(
            event=bot_event, client=fake_client, ack=fake_ack
        )

        # Then _handle_request is never called (bot messages are ignored)
        assert captured == []
```

- [x] **Step 2: Run tests to confirm they fail**

```bash
just test tests/unit/interfaces/slack/test_event_handlers.py
```

Expected: FAIL (handlers currently do raw dict access, no typed parsing)

- [x] **Step 3: Update `event_handlers.py` to use parsers**

Add import (module level):
```python
from sentinel.vendors.slack import _parsers as slack_parsers
```

Replace `handle_app_mention` body:
```python
@app.event("app_mention")
async def handle_app_mention(
    event: dict[str, Any],
    client: Any,
    ack: AsyncAck,
) -> None:
    """Handle @Sentinel mentions in any channel."""
    await ack()
    mention = slack_parsers.parse_mention_event(event)
    try:
        await _handle_request(
            mention.text,
            client=client,
            channel=mention.channel,
            thread_ts=mention.thread_ts,
            user_id=mention.user_id,
        )
    except Exception as exc:
        logs.log_exception(exc)
        await client.chat_postMessage(
            channel=mention.channel,
            thread_ts=mention.thread_ts,
            text=":x: Something went wrong while processing your request. Please try again.",
        )
```

Replace `handle_direct_message` body:
```python
@app.event("message")
async def handle_direct_message(
    event: dict[str, Any],
    client: Any,
    ack: AsyncAck,
) -> None:
    """Handle direct messages to the bot."""
    await ack()
    if event.get("bot_id") or event.get("subtype") or event.get("channel_type") != "im":
        return
    message = slack_parsers.parse_message_event(event)
    try:
        await _handle_request(
            message.text,
            client=client,
            channel=message.channel,
            thread_ts=message.thread_ts,
            user_id=message.user_id,
        )
    except Exception as exc:
        logs.log_exception(exc)
        await client.chat_postMessage(
            channel=message.channel,
            thread_ts=message.thread_ts,
            text=":x: Something went wrong while processing your request. Please try again.",
        )
```

- [x] **Step 4: Run tests**

```bash
just test tests/unit/interfaces/slack/ && just lint
```

Expected: all PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/interfaces/slack/event_handlers.py \
        tests/unit/interfaces/slack/__init__.py \
        tests/unit/interfaces/slack/test_event_handlers.py
git commit -m "refactor(slack): use typed event parsers in event handlers"
```

---

### Task 6: Create `vendors/slack/_client.py` — `AsyncSlackClient` wrapper

**Files:**
- Create: `src/sentinel/vendors/slack/_client.py`
- Create: `tests/unit/vendors/slack/test_client.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/vendors/slack/test_client.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.vendors.slack import _client as slack_client_mod


class TestAsyncSlackClient:
    def test_is_configured_returns_false_when_no_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given no Slack bot token in settings
        fake_settings = mock.MagicMock()
        fake_settings.slack_bot_token = ""
        monkeypatch.setattr(slack_client_mod, "settings", fake_settings)

        client = slack_client_mod.AsyncSlackClient(token=None)

        # Then is_configured is False
        assert client.is_configured is False

    def test_is_configured_returns_true_when_token_present(self) -> None:
        # Given a valid token
        client = slack_client_mod.AsyncSlackClient(token="xoxb-fake-token")

        # Then is_configured is True
        assert client.is_configured is True

    @pytest.mark.asyncio
    async def test_post_message_calls_sdk(self) -> None:
        # Given a client with a mocked raw SDK client
        mock_sdk = mock.AsyncMock()
        mock_sdk.chat_postMessage.return_value = {"ok": True, "ts": "1.0"}

        client = slack_client_mod.AsyncSlackClient(token="xoxb-test")
        client._sdk = mock_sdk  # inject mock

        # When posting a message
        await client.post_message(channel="C1", text="hello", blocks=[])

        # Then the SDK's chat_postMessage is called
        mock_sdk.chat_postMessage.assert_awaited_once_with(
            channel="C1",
            text="hello",
            blocks=[],
        )

    @pytest.mark.asyncio
    async def test_post_message_wraps_sdk_error(self) -> None:
        # Given a client whose SDK raises SlackApiError
        from slack_sdk.errors import SlackApiError

        mock_sdk = mock.AsyncMock()
        mock_sdk.chat_postMessage.side_effect = SlackApiError(
            message="channel_not_found", response={"ok": False}
        )
        client = slack_client_mod.AsyncSlackClient(token="xoxb-test")
        client._sdk = mock_sdk

        # Then post_message raises SlackClientError (our own error type)
        with pytest.raises(slack_client_mod.SlackClientError, match="channel_not_found"):
            await client.post_message(channel="C1", text="hi", blocks=[])
```

- [x] **Step 2: Run tests to confirm failure**

```bash
just test tests/unit/vendors/slack/test_client.py
```

Expected: `ModuleNotFoundError: No module named 'sentinel.vendors.slack._client'`

- [x] **Step 3: Implement `_client.py`**

```python
# src/sentinel/vendors/slack/_client.py
from __future__ import annotations

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from sentinel.settings import settings
from sentinel.utils import logs


class SlackClientError(Exception):
    """Raised when the Slack SDK returns an API error."""


class AsyncSlackClient:
    """Type-safe wrapper around ``slack_sdk.web.async_client.AsyncWebClient``."""

    def __init__(self, *, token: str | None) -> None:
        self._token = token
        self._sdk: AsyncWebClient | None = (
            AsyncWebClient(token=token) if token else None
        )

    @property
    def is_configured(self) -> bool:
        """Return True when a bot token was supplied."""
        return self._sdk is not None

    async def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict[str, object]],
    ) -> str | None:
        """
        Post a message to ``channel``.

        Return the message ``ts``, or None if the client is not configured.
        Raises :exc:`SlackClientError` on API errors.
        """
        if self._sdk is None:
            logs.log_event(
                "slack_post_skipped",
                params={"reason": "No token configured", "channel": channel},
            )
            return None
        try:
            response = await self._sdk.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
            )
            return response.get("ts")
        except SlackApiError as exc:
            raise SlackClientError(str(exc.response.get("error", exc))) from exc

    async def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, object]],
    ) -> None:
        """Edit an existing message in-place. Raises :exc:`SlackClientError` on failure."""
        if self._sdk is None:
            return
        try:
            await self._sdk.chat_update(
                channel=channel,
                ts=ts,
                text=text,
                blocks=blocks,
            )
        except SlackApiError as exc:
            raise SlackClientError(str(exc.response.get("error", exc))) from exc


_singleton: AsyncSlackClient | None = None


def get_client() -> AsyncSlackClient:
    """Return a module-level singleton ``AsyncSlackClient`` (no-op when unconfigured)."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = AsyncSlackClient(token=settings.slack_bot_token or None)
    return _singleton
```

- [x] **Step 4: Run tests**

```bash
just test tests/unit/vendors/slack/test_client.py
```

Expected: 4 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/vendors/slack/_client.py tests/unit/vendors/slack/test_client.py
git commit -m "feat(vendors/slack): AsyncSlackClient wrapper with typed errors"
```

---

### Task 7: Update posting functions to use `AsyncSlackClient`

**Files:**
- Modify: `src/sentinel/vendors/slack/__init__.py`

- [x] **Step 1: Identify the three `_get_client()` call sites in `__init__.py`**

Each posting function calls `client = _get_client()` then checks `if not target_channel or not client`. They call raw SDK methods directly. We will replace them with `AsyncSlackClient`.

- [x] **Step 2: Update `__init__.py`**

Add import (module level):
```python
from sentinel.vendors.slack import _client as slack_client_mod
```

Remove:
```python
from slack_sdk.web.async_client import AsyncWebClient
_client: AsyncWebClient | None = None

def _get_client() -> AsyncWebClient | None: ...
```

Replace every `client = _get_client()` block with:
```python
slack = slack_client_mod.get_client()
```

For `post_investigation_summary`, replace the posting block:
```python
# Before:
try:
    await client.chat_postMessage(channel=target_channel, text=fallback_text, blocks=blocks)
    ...
except Exception as exc:
    logs.log_exception(exc, ...)

# After:
try:
    await slack.post_message(channel=target_channel, text=fallback_text, blocks=blocks)
    logs.log_event("slack_investigation_posted", params={"channel": target_channel, "alert_id": alert_id})
except slack_client_mod.SlackClientError as exc:
    logs.log_exception(exc, params={"alert_id": alert_id, "channel": target_channel})
```

Apply the same pattern to `post_support_suggestion`, `post_approval_request` (use `post_message`), and `post_drift_alert`.

For `post_approval_request` (which returns `ts`), capture the return value:
```python
ts = await slack.post_message(channel=target_channel, text=fallback_text, blocks=blocks)
return ts
```

Replace the no-op guard in each function:
```python
# Before:
client = _get_client()
if not target_channel or not client:
    ...
    return

# After:
if not target_channel or not slack_client_mod.get_client().is_configured:
    logs.log_event("slack_post_skipped", params={"reason": "unconfigured", ...})
    return
slack = slack_client_mod.get_client()
```

- [x] **Step 3: Run full test suite**

```bash
just test && just lint
```

Expected: all green

- [ ] **Step 4: Commit**

```bash
git add src/sentinel/vendors/slack/__init__.py
git commit -m "refactor(vendors/slack): posting functions use AsyncSlackClient"
```

---

### Task 8: Create `interfaces/slack/constants.py` and add structured logging

**Files:**
- Create: `src/sentinel/interfaces/slack/constants.py`
- Modify: `src/sentinel/interfaces/slack/event_handlers.py`

- [x] **Step 1: Create `constants.py`**

```python
# src/sentinel/interfaces/slack/constants.py
from __future__ import annotations

# Slack action_id values (must match Block Kit button action_id in _blocks.py)
ACTION_APPROVE_INVESTIGATION = "approve_investigation"
ACTION_REJECT_INVESTIGATION = "reject_investigation"

# Structured log event names for Slack operations
EVENT_REQUEST_RECEIVED = "slack.request_received"
EVENT_REQUEST_ERROR = "slack.request_error"
EVENT_SRE_COMPLETE = "slack.sre_investigation_complete"
EVENT_SUPPORT_COMPLETE = "slack.support_review_complete"
EVENT_INTENT_CLASSIFIED = "slack.intent_classified"
```

- [x] **Step 2: Update `event_handlers.py` to use constants for logging**

Add import:
```python
from sentinel.interfaces.slack import constants as slack_constants
```

Replace ad-hoc `logs.log_event("slack_request_received", ...)` calls with:
```python
logs.log_event(
    slack_constants.EVENT_REQUEST_RECEIVED,
    params={"user_id": user_id, "channel": channel, "intent": classified_intent.value},
)
```

Replace `logs.log_event("slack_investigation_complete", ...)` with:
```python
logs.log_event(
    slack_constants.EVENT_SRE_COMPLETE,
    params={"alert_id": alert.id, "channel": channel},
)
```

Replace `logs.log_event("slack_support_review_complete", ...)` with:
```python
logs.log_event(
    slack_constants.EVENT_SUPPORT_COMPLETE,
    params={"ticket_key": ticket.key, "channel": channel},
)
```

- [x] **Step 3: Add `ExceptionGroup` handling to both event handlers**

The current handlers catch bare `Exception`. Python 3.11+ raises `ExceptionGroup` when multiple async tasks fail concurrently. Update `handle_app_mention`:

```python
@app.event("app_mention")
async def handle_app_mention(
    event: dict[str, Any],
    client: Any,
    ack: AsyncAck,
) -> None:
    """Handle @Sentinel mentions in any channel."""
    await ack()
    mention = slack_parsers.parse_mention_event(event)
    try:
        await _handle_request(
            mention.text,
            client=client,
            channel=mention.channel,
            thread_ts=mention.thread_ts,
            user_id=mention.user_id,
        )
    except* Exception as eg:  # noqa: E225 — Python 3.11+ except* syntax
        for exc in eg.exceptions:
            logs.log_exception(exc)
        logs.log_event(
            slack_constants.EVENT_REQUEST_ERROR,
            params={"channel": mention.channel, "error_count": len(eg.exceptions)},
        )
        await client.chat_postMessage(
            channel=mention.channel,
            thread_ts=mention.thread_ts,
            text=":x: Something went wrong while processing your request. Please try again.",
        )
```

Apply the identical pattern to `handle_direct_message`.

Note: `except*` requires Python 3.11+. Confirm with `python --version`. If the project is on 3.11+, use `except*`. If 3.10, keep `except Exception`.

- [x] **Step 4: Run tests**

```bash
just test && just lint
```

Expected: all green

- [x] **Step 5: Commit**

```bash
git add src/sentinel/interfaces/slack/constants.py \
        src/sentinel/interfaces/slack/event_handlers.py
git commit -m "feat(slack): constants.py for action IDs and structured log event names"
```

---

### Task 9: Export new modules from `vendors/slack/__init__.py` and final wiring check

**Files:**
- Modify: `src/sentinel/vendors/slack/__init__.py`

- [x] **Step 1: Add re-exports to `__init__.py` for clean external access**

At the bottom of `vendors/slack/__init__.py`, add:
```python
# Public sub-module symbols for callers that want typed access
from sentinel.vendors.slack._blocks import (
    investigation_summary_blocks,
    approval_request_blocks,
    support_summary_blocks,
    drift_alert_blocks,
)
from sentinel.vendors.slack._client import AsyncSlackClient, SlackClientError, get_client
from sentinel.vendors.slack._parsers import (
    MentionEvent,
    MessageEvent,
    parse_mention_event,
    parse_message_event,
)

__all__ = [
    # Posting functions (existing public API)
    "post_investigation_summary",
    "post_approval_request",
    "post_support_suggestion",
    "post_drift_alert",
    "is_slack_configured",
    "store_pending_approval",
    # Block Kit builders
    "investigation_summary_blocks",
    "approval_request_blocks",
    "support_summary_blocks",
    "drift_alert_blocks",
    # Client
    "AsyncSlackClient",
    "SlackClientError",
    "get_client",
    # Parsers
    "MentionEvent",
    "MessageEvent",
    "parse_mention_event",
    "parse_message_event",
]
```

- [x] **Step 2: Run full suite one final time**

```bash
just lint && just test
```

Expected: ruff clean, mypy clean, all tests PASS

- [x] **Step 3: Final commit**

```bash
git add src/sentinel/vendors/slack/__init__.py
git commit -m "refactor(vendors/slack): export typed API from package __init__"
```

---

## Self-Review

**Spec coverage:**
- Block Kit duplication → Tasks 1-3 ✅
- Typed event parsing → Tasks 4-5 ✅
- `AsyncSlackClient` wrapper → Tasks 6-7 ✅
- Structured logging + constants → Task 8 ✅
- `ExceptionGroup` handling → Task 8 ✅
- Package restructure (backward compat) → Task 2 ✅
- Public `__all__` exports → Task 9 ✅

**Placeholder scan:** No TBDs, no "similar to task N" references, all function names and signatures are consistent across tasks.

**Type consistency:**
- `investigation_summary_blocks(...)` defined in Task 1, called in Tasks 2 and 3 with identical parameter names ✅
- `AsyncSlackClient.post_message(*, channel, text, blocks)` defined in Task 6, used in Task 7 ✅
- `parse_mention_event` / `parse_message_event` defined in Task 4, imported in Task 5 ✅
- `slack_constants.EVENT_REQUEST_RECEIVED` defined in Task 8, used in same task ✅

---

## Changes

| Date | What changed | Why |
|---|---|---|
| 2026-04-30 | Initial draft | Identified from alfredo/sentinel comparison |

## Outcome

Merged as PR #34 (2026-04-30). All 9 tasks completed across commits `f129d85` → `122d2d2`.

### What was delivered
- `vendors/slack/` package (4 modules): `_blocks.py`, `_parsers.py`, `_client.py`, `__init__.py`
- All Block Kit builders consolidated in `_blocks.py`; duplicates removed from `event_handlers.py`
- Typed Pydantic event models (`MentionEvent`, `MessageEvent`) in `_parsers.py`; event handlers use them
- `AsyncSlackClient` wrapper with typed errors (`SlackClientError`) in `_client.py`; posting functions use it
- `interfaces/slack/constants.py` for action IDs and structured log event names
- `ExceptionGroup` handling added to both event handlers
- Full `__all__` public API re-exported from `vendors/slack/__init__.py`
- Integration tests rewritten to match refactored API (post-PR fix in `da10ce8`)

### Follow-up / tech debt
- Background task support (asyncio retry loop) — consider if Sentinel adds long-running Slack searches
- Slack interactive action handlers (`@app.action(...)`) — when approval buttons are wired to Slack callbacks rather than HTTP endpoints
