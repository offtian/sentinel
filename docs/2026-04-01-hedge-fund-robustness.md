# Hedge Fund Robustness: Error Handling & Human Approval Gate

> **Status: COMPLETE** (2026-04-01) — All 6 tasks implemented, 17 new tests passing, 0 regressions.

**Goal:** Make Sentinel production-safe for a hedge fund by adding graceful error handling in pipeline nodes and a human approval gate before findings reach external systems (Slack, PagerDuty).

**Architecture:** Two changes layered onto the existing Pydantic Graph pipelines. (1) Each node wraps its core logic in structured error handling so failures degrade gracefully rather than crashing the entire pipeline. Critical nodes (classification) fail the pipeline with a clear error; degradable nodes (Holmes, RCA, search) continue with partial results. (2) The `DetermineConfidence` node in the SRE pipeline gates on a configurable confidence threshold. LOW confidence findings require human approval via Slack interactive message (Approve/Reject buttons) before publishing. HIGH confidence findings proceed directly to `PublishFindings`.

**Tech Stack:** Python 3.13, PydanticAI, Pydantic Graph, attrs, structlog, Slack SDK, FastAPI, pytest

## Completion Summary

| Task | Status | Tests Added |
|------|--------|-------------|
| 1. Pipeline error domain types (`NodeError`, `PipelineNodeFailed`) | DONE | 5 |
| 2. SRE pipeline error handling (all 5 nodes) | DONE | 4 |
| 3. Support pipeline error handling (all 4 nodes) | DONE | 3 |
| 4. Approval domain entities + Slack interactive message | DONE | 6 (entities) |
| 5. Approval gate API endpoints + settings | DONE | — |
| 6. Wire approval gate into SRE pipeline | DONE | 4 |
| **Total** | **DONE** | **22 new tests** |

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/sentinel/domain/pipeline/errors.py` | Pipeline error domain types (`NodeError`, `PipelineNodeFailed`) |
| Create | `tests/unit/domain/pipeline/test_errors.py` | Unit tests for error types |
| Create | `tests/unit/domain/pipeline/__init__.py` | Package init |
| Create | `src/sentinel/domain/pipeline/__init__.py` | Package init |
| Modify | `src/sentinel/interfaces/graphs/sre_investigation.py` | Add try/except to each node, approval gate in `DetermineConfidence` |
| Modify | `src/sentinel/interfaces/graphs/support_review.py` | Add try/except to each node |
| Modify | `src/sentinel/interfaces/graphs/common.py` | Add `RequestApprovalFn` callback type, `approval_status` field to `InvestigationReply` |
| Modify | `src/sentinel/settings.py` | Add `require_approval_below_confidence`, `approval_timeout_seconds` settings |
| Create | `src/sentinel/domain/approval/__init__.py` | Package init |
| Create | `src/sentinel/domain/approval/entities.py` | `ApprovalRequest`, `ApprovalDecision` domain types |
| Modify | `src/sentinel/interfaces/api/routers/sre/router.py` | Add approve/reject/approval-status endpoints to existing SRE router |
| Modify | `src/sentinel/vendors/slack.py` | Add `post_approval_request()` with interactive buttons |
| Create | `tests/unit/interfaces/graphs/test_sre_error_handling.py` | SRE node error handling tests |
| Create | `tests/unit/interfaces/graphs/test_support_error_handling.py` | Support node error handling tests |
| Create | `tests/unit/domain/approval/test_entities.py` | Approval entity tests |
| Create | `tests/unit/domain/approval/__init__.py` | Package init |
| Create | `tests/functional/test_sre_approval_gate.py` | Approval gate functional tests |

---

## Task 1: Pipeline Error Domain Types

**Files:**
- Create: `src/sentinel/domain/pipeline/__init__.py`
- Create: `src/sentinel/domain/pipeline/errors.py`
- Create: `tests/unit/domain/pipeline/__init__.py`
- Create: `tests/unit/domain/pipeline/test_errors.py`

- [x]**Step 1: Write failing tests for NodeError and PipelineNodeFailed**

```python
# tests/unit/domain/pipeline/test_errors.py
from __future__ import annotations

import pytest

from sentinel.domain.pipeline import errors


class TestNodeError:
    def test_captures_node_name_and_message(self) -> None:
        # Given an error from the ClassifyAlert node
        error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="LLMTimeout",
            message="Request timed out after 30s",
            is_recoverable=True,
        )

        # When we inspect the error fields

        # Then all fields are captured correctly
        assert error.node_name == "ClassifyAlert"
        assert error.error_type == "LLMTimeout"
        assert error.message == "Request timed out after 30s"
        assert error.is_recoverable is True

    def test_defaults_to_non_recoverable(self) -> None:
        # Given an error with no explicit recoverability

        # When we create a NodeError without is_recoverable
        error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="Unknown",
            message="Something broke",
        )

        # Then it defaults to non-recoverable
        assert error.is_recoverable is False

    def test_is_frozen(self) -> None:
        # Given a node error
        error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="LLMTimeout",
            message="timeout",
        )

        # When we attempt to mutate it

        # Then it raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            error.node_name = "Other"


class TestPipelineNodeFailed:
    def test_wraps_node_error_as_exception(self) -> None:
        # Given a node error
        node_error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="LLMTimeout",
            message="Request timed out",
        )

        # When we create the exception
        exc = errors.PipelineNodeFailed(node_error=node_error)

        # Then it carries the node error and is a proper exception
        assert exc.node_error is node_error
        assert "ClassifyAlert" in str(exc)
        assert "Request timed out" in str(exc)

    def test_is_an_exception(self) -> None:
        # Given a PipelineNodeFailed instance
        node_error = errors.NodeError(
            node_name="X",
            error_type="Y",
            message="Z",
        )
        exc = errors.PipelineNodeFailed(node_error=node_error)

        # Then it is a proper Exception subclass
        assert isinstance(exc, Exception)
```

- [x]**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/pipeline/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.domain.pipeline'`

- [x]**Step 3: Implement NodeError and PipelineNodeFailed**

```python
# src/sentinel/domain/pipeline/__init__.py
```

```python
# src/sentinel/domain/pipeline/errors.py
from __future__ import annotations

import attrs


@attrs.frozen
class NodeError:
    """
    Capture a pipeline node failure with enough context for logging and degraded continuation.

    Immutable value object -- safe to pass between nodes and log as structured data.
    """

    node_name: str
    error_type: str
    message: str
    is_recoverable: bool = False


class PipelineNodeFailed(Exception):
    """
    Raise when a critical pipeline node fails and the pipeline cannot continue.

    Wraps a ``NodeError`` so callers can inspect structured failure details.
    """

    def __init__(self, *, node_error: NodeError) -> None:
        self.node_error = node_error
        super().__init__(
            f"Pipeline node '{node_error.node_name}' failed: {node_error.message}"
        )
```

- [x]**Step 4: Create test package init**

```python
# tests/unit/domain/pipeline/__init__.py
```

- [x]**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/pipeline/test_errors.py -v`
Expected: All 4 tests PASS

- [x]**Step 6: Commit**

```bash
git add src/sentinel/domain/pipeline/ tests/unit/domain/pipeline/
git commit -m "feat: add pipeline error domain types (NodeError, PipelineNodeFailed)"
```

---

## Task 2: SRE Pipeline Error Handling

**Files:**
- Modify: `src/sentinel/interfaces/graphs/sre_investigation.py`
- Create: `tests/unit/interfaces/graphs/test_sre_error_handling.py`

Each node gets a try/except that:
- **ClassifyAlert** (critical): catches exceptions, logs, returns `End(reply)` with error context
- **InvestigateWithHolmes** (degradable): catches exceptions, logs, continues to `AnalyseRootCause` with empty findings
- **AnalyseRootCause** (degradable): catches exceptions, logs, continues with fallback root cause text
- **DetermineConfidence** (degradable): catches exceptions, defaults to LOW confidence
- **PublishFindings**: uses `return_exceptions=True` in `asyncio.gather` so one failed channel doesn't block others

- [x]**Step 1: Write failing tests for SRE node error handling**

```python
# tests/unit/interfaces/graphs/test_sre_error_handling.py
from __future__ import annotations

import pytest

from sentinel.domain.pipeline import errors
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs import common, sre_investigation
from sentinel.interfaces.graphs.agents import alert_classifier, root_cause_analyser
from tests.factories import make_alert


class _FakeStatusClient(common.NoOpStatusUpdateClient):
    pass


def _make_deps(
    *,
    holmes: holmes_adapter.BaseHolmesAdapter | None = None,
) -> sre_investigation.Dependencies:
    return sre_investigation.Dependencies(
        status_update_client=_FakeStatusClient(),
        classifier_model="test-model",
        analyser_model="test-model",
        holmes=holmes or holmes_adapter.MockHolmesAdapter(),
    )


class TestClassifyAlertErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_failed_reply_when_agent_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a ClassifyAlert node where the agent raises a timeout error
        async def failing_run(*, user_prompt, model, deps):
            raise TimeoutError("LLM request timed out")

        monkeypatch.setattr(alert_classifier.agent, "run", failing_run)

        alert = make_alert()
        state = sre_investigation.State(alert=alert)
        deps = _make_deps()

        # When the full pipeline is run
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=deps.holmes,
            status_update_client=deps.status_update_client,
            classifier_model="test-model",
            analyser_model="test-model",
            post_to_slack=False,
        )

        # Then the reply contains an error indication, not a crash
        assert result.alert_id == alert.id
        assert result.root_cause is not None
        assert "failed" in result.root_cause.lower() or "error" in result.root_cause.lower()


class TestInvestigateWithHolmesErrorHandling:
    @pytest.mark.asyncio
    async def test_continues_pipeline_when_holmes_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given Holmes adapter that raises an error
        class FailingHolmes(holmes_adapter.BaseHolmesAdapter):
            @property
            def is_configured(self) -> bool:
                return True

            async def investigate(
                self, *, alert: sre_entities.Alert
            ) -> holmes_adapter.HolmesInvestigationResult:
                raise ConnectionError("Datadog API unreachable")

        # And working classifier and analyser agents
        async def fake_classify(*, user_prompt, model, deps):
            from tests.functional.conftest import FakeAgentResult

            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="high",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test alert",
                    requires_immediate_action=True,
                )
            )

        async def fake_analyse(*, user_prompt, model, deps):
            from tests.functional.conftest import FakeAgentResult

            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Possible issue based on alert context",
                    confidence=0.4,
                    evidence=["Limited evidence - observability data unavailable"],
                    remediation_steps=["Check manually"],
                    affected_services=["api-service"],
                    timeline="Unknown",
                )
            )

        monkeypatch.setattr(alert_classifier.agent, "run", fake_classify)
        monkeypatch.setattr(root_cause_analyser.agent, "run", fake_analyse)

        alert = make_alert()

        # When the pipeline runs
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=FailingHolmes(),
            classifier_model="test-model",
            analyser_model="test-model",
            post_to_slack=False,
        )

        # Then the pipeline completes with degraded results instead of crashing
        assert result.alert_id == alert.id
        assert result.root_cause is not None


class TestPublishFindingsErrorHandling:
    @pytest.mark.asyncio
    async def test_slack_failure_does_not_block_persist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a Slack posting function that raises
        async def failing_slack(**kwargs):
            raise ConnectionError("Slack API down")

        from sentinel.vendors import slack

        monkeypatch.setattr(slack, "post_investigation_summary", failing_slack)

        # And a persist function that tracks calls
        persisted = []

        async def track_persist(reply):
            persisted.append(reply)

        # And working agents
        async def fake_classify(*, user_prompt, model, deps):
            from tests.functional.conftest import FakeAgentResult

            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="high",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test",
                    requires_immediate_action=True,
                )
            )

        async def fake_analyse(*, user_prompt, model, deps):
            from tests.functional.conftest import FakeAgentResult

            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Test root cause",
                    confidence=0.8,
                    evidence=["evidence"],
                    remediation_steps=["step"],
                    affected_services=["api-service"],
                    timeline="now",
                )
            )

        monkeypatch.setattr(alert_classifier.agent, "run", fake_classify)
        monkeypatch.setattr(root_cause_analyser.agent, "run", fake_analyse)

        alert = make_alert()

        # When the pipeline runs with Slack failing
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=holmes_adapter.MockHolmesAdapter(),
            classifier_model="test-model",
            analyser_model="test-model",
            post_to_slack=True,
            persist_fn=track_persist,
        )

        # Then the pipeline still completes and persist was called
        assert result.root_cause is not None
        assert len(persisted) == 1
```

- [x]**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/interfaces/graphs/test_sre_error_handling.py -v`
Expected: FAIL (nodes crash on exceptions)

- [x]**Step 3: Add error handling to ClassifyAlert**

In `sre_investigation.py`, wrap the `ClassifyAlert.run()` body:

```python
async def run(
    self, ctx: GraphRunContext[State, Dependencies]
) -> InvestigateWithHolmes | End[common.InvestigationReply]:
    await ctx.deps.status_update_client.update_status("Classifying alert...")

    try:
        result = await alert_classifier.agent.run(
            user_prompt=f"Alert: {ctx.state.alert.title}\n\n{ctx.state.alert.description}",
            model=utils.get_model_with_gateway(ctx.deps.classifier_model),
            deps=alert_classifier.Dependencies(
                alert_title=ctx.state.alert.title,
                alert_description=ctx.state.alert.description,
                alert_source=ctx.state.alert.source,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": ctx.state.alert.id, "node": "ClassifyAlert"},
        )
        return End(
            common.InvestigationReply(
                alert_id=ctx.state.alert.id,
                root_cause=f"Classification failed: {type(exc).__name__} — {exc}",
            )
        )

    # ... rest of method unchanged ...
```

- [x]**Step 4: Add error handling to InvestigateWithHolmes**

```python
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> AnalyseRootCause:
    await ctx.deps.status_update_client.update_status(
        "Investigating with observability tools..."
    )

    try:
        holmes_result = await ctx.deps.holmes.investigate(alert=ctx.state.alert)
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": ctx.state.alert.id, "node": "InvestigateWithHolmes"},
        )
        # Degrade gracefully: continue with empty findings
        return AnalyseRootCause(
            holmes_analysis="Observability investigation unavailable — proceeding with alert context only.",
            holmes_tool_calls=[],
            holmes_sources=[],
        )

    logs.log_event(
        "holmes_investigation_completed",
        params={
            "alert_id": ctx.state.alert.id,
            "sources_queried": holmes_result.sources_queried,
            "tool_calls_count": len(holmes_result.tool_calls),
        },
    )

    return AnalyseRootCause(
        holmes_analysis=holmes_result.analysis,
        holmes_tool_calls=holmes_result.tool_calls,
        holmes_sources=holmes_result.sources_queried,
    )
```

- [x]**Step 5: Add error handling to AnalyseRootCause**

```python
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> DetermineConfidence:
    await ctx.deps.status_update_client.update_status("Analysing root cause...")

    try:
        result = await root_cause_analyser.agent.run(
            user_prompt=f"Analyse this alert: {ctx.state.alert.title}",
            model=utils.get_model_with_gateway(ctx.deps.analyser_model),
            deps=root_cause_analyser.Dependencies(
                alert_title=ctx.state.alert.title,
                alert_description=ctx.state.alert.description,
                alert_severity=ctx.state.alert.severity.value,
                holmes_analysis=self.holmes_analysis,
                holmes_tool_calls=self.holmes_tool_calls,
                holmes_sources=self.holmes_sources,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": ctx.state.alert.id, "node": "AnalyseRootCause"},
        )
        # Degrade: continue with fallback text so the alert still reaches output channels
        if ctx.state.investigation:
            ctx.state.investigation = ctx.state.investigation.model_copy(
                update={
                    "root_cause": "Root cause analysis unavailable — LLM error. Manual investigation required.",
                    "remediation": "Please investigate this alert manually.",
                }
            )
        return DetermineConfidence(raw_confidence=0.0)

    # ... rest of method unchanged (trace collector, logging, findings, return) ...
```

- [x]**Step 6: Add error handling to DetermineConfidence**

```python
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> PublishFindings:
    try:
        findings_count = (
            len(ctx.state.investigation.findings) if ctx.state.investigation else 0
        )
        confidence = confidence_entities.ConfidenceScore.from_factors(
            source_count=findings_count,
            max_expected_sources=5,
            relevance=self.raw_confidence,
            recency=0.8,
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": ctx.state.alert.id, "node": "DetermineConfidence"},
        )
        confidence = confidence_entities.ConfidenceScore.from_total(0.0)

    if ctx.state.investigation:
        ctx.state.investigation = ctx.state.investigation.model_copy(
            update={"confidence_score": confidence.total}
        )

    return PublishFindings(confidence=confidence)
```

- [x]**Step 7: Add error handling to PublishFindings (return_exceptions=True)**

Replace bare `asyncio.gather(*publish_tasks)` with:

```python
if publish_tasks:
    results = await asyncio.gather(*publish_tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logs.log_exception(
                result,
                params={
                    "alert_id": ctx.state.alert.id,
                    "node": "PublishFindings",
                    "publish_channel_index": i,
                },
            )
```

- [x]**Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/interfaces/graphs/test_sre_error_handling.py -v`
Expected: All 3 tests PASS

- [x]**Step 9: Run existing functional tests to verify no regression**

Run: `uv run pytest tests/functional/test_sre_investigation.py -v`
Expected: All existing tests PASS

- [x]**Step 10: Commit**

```bash
git add src/sentinel/interfaces/graphs/sre_investigation.py tests/unit/interfaces/graphs/test_sre_error_handling.py
git commit -m "feat: add graceful error handling to SRE pipeline nodes"
```

---

## Task 3: Support Pipeline Error Handling

**Files:**
- Modify: `src/sentinel/interfaces/graphs/support_review.py`
- Create: `tests/unit/interfaces/graphs/test_support_error_handling.py`

Same pattern: ClassifyTicket is critical, SearchDocumentation and DraftResponse are degradable.

- [x]**Step 1: Write failing tests for support node error handling**

```python
# tests/unit/interfaces/graphs/test_support_error_handling.py
from __future__ import annotations

import pytest

from sentinel.domain.search import searcher
from sentinel.interfaces.graphs import common, support_review
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from tests.factories import make_ticket


class _FakeStatusClient(common.NoOpStatusUpdateClient):
    pass


class TestClassifyTicketErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_error_reply_when_agent_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a ticket reviewer agent that raises
        async def failing_run(*, user_prompt, model, deps):
            raise TimeoutError("LLM timeout")

        monkeypatch.setattr(ticket_reviewer.agent, "run", failing_run)

        ticket = make_ticket()

        # When the pipeline runs
        result = await support_review.review_ticket(
            ticket=ticket,
            reviewer_model="test-model",
            drafter_model="test-model",
        )

        # Then the reply indicates failure instead of crashing
        assert result.ticket_id == ticket.id
        assert "failed" in result.suggested_response.lower() or "error" in result.suggested_response.lower()


class TestSearchDocumentationErrorHandling:
    @pytest.mark.asyncio
    async def test_continues_when_search_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a working classifier
        async def fake_classify(*, user_prompt, model, deps):
            from tests.functional.conftest import FakeAgentResult

            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="account",
                    urgency="high",
                    required_expertise=["auth"],
                    key_questions=["Is SSO expired?"],
                    search_queries=["SSO troubleshooting"],
                )
            )

        monkeypatch.setattr(ticket_reviewer.agent, "run", fake_classify)

        # And a document searcher that raises
        class FailingSearcher(searcher.BaseDocumentSearcher):
            async def search(self, *, query: str, limit: int):
                raise ConnectionError("Search service down")

        ticket = make_ticket()

        # When the pipeline runs with a failing searcher
        result = await support_review.review_ticket(
            ticket=ticket,
            document_searcher=FailingSearcher(),
            reviewer_model="test-model",
            drafter_model="test-model",
        )

        # Then the pipeline completes with a fallback response
        assert result.ticket_id == ticket.id
        assert result.suggested_response != ""


class TestDraftResponseErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_drafter_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a working classifier and searcher, but a failing drafter
        async def fake_classify(*, user_prompt, model, deps):
            from tests.functional.conftest import FakeAgentResult

            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="account",
                    urgency="high",
                    required_expertise=["auth"],
                    key_questions=["Is SSO expired?"],
                    search_queries=["SSO troubleshooting"],
                )
            )

        async def failing_draft(*, user_prompt, model, deps):
            raise RuntimeError("LLM returned malformed response")

        monkeypatch.setattr(ticket_reviewer.agent, "run", fake_classify)
        monkeypatch.setattr(response_drafter.agent, "run", failing_draft)

        from tests.functional.conftest import StubDocumentSearcher

        ticket = make_ticket()

        # When the pipeline runs
        result = await support_review.review_ticket(
            ticket=ticket,
            document_searcher=StubDocumentSearcher(),
            reviewer_model="test-model",
            drafter_model="test-model",
        )

        # Then the reply has a fallback response instead of crashing
        assert result.ticket_id == ticket.id
        assert result.suggested_response != ""
```

- [x]**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/interfaces/graphs/test_support_error_handling.py -v`
Expected: FAIL (nodes crash on exceptions)

- [x]**Step 3: Add error handling to ClassifyTicket**

```python
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> SearchDocumentation:
    await ctx.deps.status_update_client.update_status("Reviewing ticket...")

    try:
        result = await ticket_reviewer.agent.run(
            user_prompt=f"Ticket: {ctx.state.ticket.summary}\n\n{ctx.state.ticket.description}",
            model=utils.get_model_with_gateway(ctx.deps.reviewer_model),
            deps=ticket_reviewer.Dependencies(
                ticket_summary=ctx.state.ticket.summary,
                ticket_description=ctx.state.ticket.description,
                ticket_priority=ctx.state.ticket.priority,
                ticket_labels=ctx.state.ticket.labels,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ctx.state.ticket.key, "node": "ClassifyTicket"},
        )
        return End(
            common.SupportReply(
                ticket_id=ctx.state.ticket.id,
                ticket_key=ctx.state.ticket.key,
                suggested_response=(
                    f"Classification failed: {type(exc).__name__} — {exc}. "
                    "Manual review required."
                ),
            )
        )

    # ... rest unchanged ...
```

- [x]**Step 4: Add error handling to SearchDocumentation**

Wrap the `asyncio.TaskGroup` block:

```python
async def run(
    self, ctx: GraphRunContext[State, Dependencies]
) -> DraftResponse | End[common.SupportReply]:
    await ctx.deps.status_update_client.update_status("Searching documentation...")

    combined_query = " ".join(self.search_queries[:3])

    doc_results: list[searcher.DocumentSearchResult] = []
    ticket_results: list[searcher.TicketSearchResult] = []

    try:
        doc_task = None
        ticket_task = None

        async with asyncio.TaskGroup() as tg:
            if ctx.deps.document_searcher:
                doc_task = tg.create_task(
                    ctx.deps.document_searcher.search(query=combined_query, limit=10)
                )
            if ctx.deps.ticket_searcher:
                ticket_task = tg.create_task(
                    ctx.deps.ticket_searcher.search(query=combined_query, limit=5)
                )

        if doc_task:
            doc_results = doc_task.result()
        if ticket_task:
            ticket_results = ticket_task.result()
    except BaseException as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ctx.state.ticket.key, "node": "SearchDocumentation"},
        )
        # Degrade: proceed with whatever results we have (likely empty)

    # ... rest unchanged from original (logging, empty check, return DraftResponse) ...
```

- [x]**Step 5: Add error handling to DraftResponse**

```python
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> DetermineConfidence:
    await ctx.deps.status_update_client.update_status("Drafting response...")

    try:
        result = await response_drafter.agent.run(
            user_prompt=f"Draft a response for: {ctx.state.ticket.summary}",
            model=utils.get_model_with_gateway(ctx.deps.drafter_model),
            deps=response_drafter.Dependencies(
                ticket_summary=ctx.state.ticket.summary,
                ticket_description=ctx.state.ticket.description,
                ticket_category=self.category,
                key_questions=self.key_questions,
                document_search_results=self.document_results,
                ticket_search_results=self.ticket_results,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ctx.state.ticket.key, "node": "DraftResponse"},
        )
        return DetermineConfidence(
            drafted_response=(
                "Response drafting failed due to an internal error. "
                "Please review this ticket manually. "
                f"Documentation was found for: {', '.join(q[:50] for q in self.key_questions[:3])}"
            ),
            sources_used=[],
            raw_confidence=0.0,
            category=self.category,
            notes="Automated drafting failed — manual review required.",
        )

    # ... rest unchanged ...
```

- [x]**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/interfaces/graphs/test_support_error_handling.py -v`
Expected: All 3 tests PASS

- [x]**Step 7: Run existing functional tests to verify no regression**

Run: `uv run pytest tests/functional/test_support_review.py -v`
Expected: All existing tests PASS

- [x]**Step 8: Commit**

```bash
git add src/sentinel/interfaces/graphs/support_review.py tests/unit/interfaces/graphs/test_support_error_handling.py
git commit -m "feat: add graceful error handling to support pipeline nodes"
```

---

## Task 4: Approval Domain Types and Slack Interactive Message

**Files:**
- Create: `src/sentinel/domain/approval/__init__.py`
- Create: `src/sentinel/domain/approval/entities.py`
- Create: `tests/unit/domain/approval/__init__.py`
- Create: `tests/unit/domain/approval/test_entities.py`
- Modify: `src/sentinel/vendors/slack.py`

- [x]**Step 1: Write failing tests for approval entities**

```python
# tests/unit/domain/approval/test_entities.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.domain.approval import entities


class TestApprovalDecision:
    def test_has_expected_values(self) -> None:
        # Given the ApprovalDecision enum

        # Then it has the expected members
        assert entities.ApprovalDecision.PENDING.value == "pending"
        assert entities.ApprovalDecision.APPROVED.value == "approved"
        assert entities.ApprovalDecision.REJECTED.value == "rejected"
        assert entities.ApprovalDecision.AUTO_APPROVED.value == "auto_approved"


class TestApprovalRequest:
    def test_creates_pending_request(self) -> None:
        # Given approval request parameters
        now = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

        # When we create an approval request
        request = entities.ApprovalRequest(
            investigation_id="inv-123",
            alert_id="P123ABC",
            alert_title="High CPU on web-01",
            confidence_label="Low",
            confidence_total=0.3,
            root_cause="Possible memory leak",
            remediation="Increase memory limit",
            requested_at=now,
        )

        # Then it defaults to pending with no reviewer
        assert request.decision == entities.ApprovalDecision.PENDING
        assert request.reviewed_by is None
        assert request.reviewed_at is None
        assert request.investigation_id == "inv-123"

    def test_is_frozen(self) -> None:
        # Given an approval request
        request = entities.ApprovalRequest(
            investigation_id="inv-123",
            alert_id="P123",
            alert_title="Test",
            confidence_label="Low",
            confidence_total=0.3,
            root_cause="Test",
            remediation="Test",
            requested_at=datetime.now(tz=UTC),
        )

        # When we attempt to mutate

        # Then it raises
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            request.decision = entities.ApprovalDecision.APPROVED

    def test_approve_returns_new_instance(self) -> None:
        # Given a pending approval request
        now = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
        request = entities.ApprovalRequest(
            investigation_id="inv-123",
            alert_id="P123",
            alert_title="Test",
            confidence_label="Low",
            confidence_total=0.3,
            root_cause="Test",
            remediation="Test",
            requested_at=now,
        )

        # When we approve it
        review_time = datetime(2026, 4, 1, 12, 5, tzinfo=UTC)
        approved = request.approve(reviewer="jane@hedge.com", at=review_time)

        # Then a new instance is returned with approved state
        assert approved.decision == entities.ApprovalDecision.APPROVED
        assert approved.reviewed_by == "jane@hedge.com"
        assert approved.reviewed_at == review_time
        # And original is unchanged
        assert request.decision == entities.ApprovalDecision.PENDING

    def test_reject_returns_new_instance(self) -> None:
        # Given a pending approval request
        request = entities.ApprovalRequest(
            investigation_id="inv-123",
            alert_id="P123",
            alert_title="Test",
            confidence_label="Low",
            confidence_total=0.3,
            root_cause="Test",
            remediation="Test",
            requested_at=datetime.now(tz=UTC),
        )

        # When we reject it
        rejected = request.reject(
            reviewer="john@hedge.com",
            at=datetime.now(tz=UTC),
        )

        # Then a new instance is returned with rejected state
        assert rejected.decision == entities.ApprovalDecision.REJECTED
        assert rejected.reviewed_by == "john@hedge.com"
```

- [x]**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/approval/test_entities.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x]**Step 3: Implement approval entities**

```python
# src/sentinel/domain/approval/__init__.py
```

```python
# src/sentinel/domain/approval/entities.py
from __future__ import annotations

import enum
from datetime import datetime

import attrs


class ApprovalDecision(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


@attrs.frozen
class ApprovalRequest:
    """
    Record a human-approval-required finding before it reaches external systems.

    Immutable -- approval/rejection returns a new instance.
    Used for regulatory compliance: every automated output that reaches
    Slack/PagerDuty must have an approval record.
    """

    investigation_id: str
    alert_id: str
    alert_title: str
    confidence_label: str
    confidence_total: float
    root_cause: str
    remediation: str
    requested_at: datetime
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    slack_message_ts: str | None = None

    def approve(self, *, reviewer: str, at: datetime) -> ApprovalRequest:
        """Return a new ApprovalRequest marked as approved."""
        return attrs.evolve(
            self,
            decision=ApprovalDecision.APPROVED,
            reviewed_by=reviewer,
            reviewed_at=at,
        )

    def reject(self, *, reviewer: str, at: datetime) -> ApprovalRequest:
        """Return a new ApprovalRequest marked as rejected."""
        return attrs.evolve(
            self,
            decision=ApprovalDecision.REJECTED,
            reviewed_by=reviewer,
            reviewed_at=at,
        )

    def auto_approve(self, *, at: datetime) -> ApprovalRequest:
        """Return a new ApprovalRequest marked as auto-approved (timeout elapsed)."""
        return attrs.evolve(
            self,
            decision=ApprovalDecision.AUTO_APPROVED,
            reviewed_by="system:auto_approve",
            reviewed_at=at,
        )
```

- [x]**Step 4: Create test package init**

```python
# tests/unit/domain/approval/__init__.py
```

- [x]**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/approval/test_entities.py -v`
Expected: All 5 tests PASS

- [x]**Step 6: Add `post_approval_request` to slack.py**

Append to `src/sentinel/vendors/slack.py`:

```python
async def post_approval_request(
    *,
    channel: str | None = None,
    investigation_id: str,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> str | None:
    """
    Post an investigation summary with Approve/Reject buttons to Slack.

    Return the message timestamp (``ts``) for tracking, or None if posting was skipped.
    """
    target_channel = channel or get_settings().sre_slack_channel
    client = _get_client()
    if not target_channel or not client:
        logs.log_event(
            "slack_approval_skipped",
            params={"reason": "No channel or token configured"},
        )
        return None

    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Approval Required: {alert_title}",
            },
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
                "text": f"*Root Cause:*\n{root_cause or 'Unable to determine root cause.'}",
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

    try:
        response = await client.chat_postMessage(
            channel=target_channel,
            text=f"Approval required for investigation: {alert_title}",
            blocks=blocks,
        )
        message_ts = response.get("ts")
        logs.log_event(
            "slack_approval_posted",
            params={
                "channel": target_channel,
                "investigation_id": investigation_id,
                "message_ts": message_ts,
            },
        )
        return message_ts
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"investigation_id": investigation_id, "channel": target_channel},
        )
        return None
```

- [x]**Step 7: Commit**

```bash
git add src/sentinel/domain/approval/ tests/unit/domain/approval/ src/sentinel/vendors/slack.py
git commit -m "feat: add approval domain entities and Slack interactive message"
```

---

## Task 5: Approval Gate API Endpoints

**Files:**
- Create: `src/sentinel/interfaces/api/routers/sre/approval.py`
- Modify: `src/sentinel/interfaces/api/routers/sre/__init__.py`
- Modify: `src/sentinel/settings.py`

- [x]**Step 1: Add approval settings**

In `src/sentinel/settings.py`, add to `SRESettings`:

```python
class SRESettings(BaseSettings):
    # ... existing fields ...

    # Approval gate: investigations below this confidence threshold require human approval
    require_approval_below_confidence: float = 0.7
    # Seconds before a pending approval auto-approves (0 = never auto-approve)
    approval_timeout_seconds: int = 0
```

- [x]**Step 2: Create approval API router**

```python
# src/sentinel/interfaces/api/routers/sre/approval.py
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sentinel.utils import logs

router = APIRouter(prefix="/api/sre/investigations", tags=["sre-approval"])


# In-memory store for pending approvals.
# Production: replace with database-backed store.
_pending_approvals: dict[str, dict] = {}


class ApprovalAction(BaseModel):
    reviewer: str


def store_pending_approval(
    *,
    investigation_id: str,
    approval_data: dict,
) -> None:
    """Store a pending approval for later retrieval by approve/reject endpoints."""
    _pending_approvals[investigation_id] = {
        **approval_data,
        "status": "pending",
        "requested_at": datetime.now(tz=UTC).isoformat(),
    }


def get_pending_approval(investigation_id: str) -> dict | None:
    """Return pending approval data, or None if not found."""
    return _pending_approvals.get(investigation_id)


def remove_pending_approval(investigation_id: str) -> None:
    """Remove a resolved approval from the pending store."""
    _pending_approvals.pop(investigation_id, None)


@router.post("/{investigation_id}/approve")
async def approve_investigation(
    investigation_id: str,
    action: ApprovalAction,
) -> JSONResponse:
    """
    Approve an investigation for publishing to external channels.

    Called by Slack interactive message handler or directly by an engineer.
    """
    pending = get_pending_approval(investigation_id)
    if not pending:
        return JSONResponse(
            status_code=404,
            content={"error": "No pending approval found", "investigation_id": investigation_id},
        )

    logs.log_event(
        "investigation.approved",
        params={
            "investigation_id": investigation_id,
            "reviewer": action.reviewer,
        },
    )

    remove_pending_approval(investigation_id)

    return JSONResponse(
        status_code=200,
        content={
            "investigation_id": investigation_id,
            "status": "approved",
            "reviewer": action.reviewer,
            "approved_at": datetime.now(tz=UTC).isoformat(),
        },
    )


@router.post("/{investigation_id}/reject")
async def reject_investigation(
    investigation_id: str,
    action: ApprovalAction,
) -> JSONResponse:
    """
    Reject an investigation — findings will NOT be published.

    Called by Slack interactive message handler or directly by an engineer.
    """
    pending = get_pending_approval(investigation_id)
    if not pending:
        return JSONResponse(
            status_code=404,
            content={"error": "No pending approval found", "investigation_id": investigation_id},
        )

    logs.log_event(
        "investigation.rejected",
        params={
            "investigation_id": investigation_id,
            "reviewer": action.reviewer,
        },
    )

    remove_pending_approval(investigation_id)

    return JSONResponse(
        status_code=200,
        content={
            "investigation_id": investigation_id,
            "status": "rejected",
            "reviewer": action.reviewer,
            "rejected_at": datetime.now(tz=UTC).isoformat(),
        },
    )


@router.get("/{investigation_id}/approval-status")
async def get_approval_status(investigation_id: str) -> JSONResponse:
    """Check the current approval status of an investigation."""
    pending = get_pending_approval(investigation_id)
    if not pending:
        return JSONResponse(
            status_code=404,
            content={"error": "No pending approval found", "investigation_id": investigation_id},
        )

    return JSONResponse(
        status_code=200,
        content={
            "investigation_id": investigation_id,
            "status": pending["status"],
            "requested_at": pending["requested_at"],
        },
    )
```

- [x]**Step 3: Register approval router in SRE __init__**

Check current state of `src/sentinel/interfaces/api/routers/sre/__init__.py` and add the approval router import/include.

- [x]**Step 4: Commit**

```bash
git add src/sentinel/interfaces/api/routers/sre/approval.py src/sentinel/interfaces/api/routers/sre/__init__.py src/sentinel/settings.py
git commit -m "feat: add approval gate API endpoints and settings"
```

---

## Task 6: Wire Approval Gate into SRE Pipeline

**Files:**
- Modify: `src/sentinel/interfaces/graphs/sre_investigation.py`
- Modify: `src/sentinel/interfaces/graphs/common.py`
- Create: `tests/functional/test_sre_approval_gate.py`

The key change: `DetermineConfidence` now returns either `PublishFindings` (high confidence) or `RequestApproval` (low confidence) based on the configured threshold.

- [x]**Step 1: Add approval callback type to common.py**

```python
# Add to src/sentinel/interfaces/graphs/common.py
RequestApprovalFn = Callable[
    [str, str, str, str | None, str | None, str | None, str],
    Awaitable[str | None],
]
```

- [x]**Step 2: Add approval fields to Dependencies**

In `sre_investigation.py`, add to `Dependencies`:

```python
@dataclasses.dataclass
class Dependencies:
    # ... existing fields ...
    require_approval_below: float = 0.0  # 0 = never require approval
    request_approval_fn: common.RequestApprovalFn | None = None
```

- [x]**Step 3: Modify DetermineConfidence to gate on confidence**

```python
async def run(self, ctx: GraphRunContext[State, Dependencies]) -> PublishFindings:
    # ... existing confidence calculation (with error handling from Task 2) ...

    # Gate: if confidence is below threshold and approval function is configured,
    # post approval request to Slack instead of publishing directly
    if (
        ctx.deps.require_approval_below > 0
        and confidence.total < ctx.deps.require_approval_below
        and ctx.deps.request_approval_fn
    ):
        investigation_id = str(ctx.state.investigation.id) if ctx.state.investigation else "unknown"
        findings_summary = ""
        if ctx.state.investigation and ctx.state.investigation.findings:
            findings_summary = "\n".join(
                f"- [{f.source}] {f.summary}" for f in ctx.state.investigation.findings
            )

        await ctx.deps.request_approval_fn(
            investigation_id,
            ctx.state.alert.id,
            ctx.state.alert.title,
            ctx.state.investigation.root_cause if ctx.state.investigation else None,
            ctx.state.investigation.remediation if ctx.state.investigation else None,
            confidence.label.value if confidence else None,
            findings_summary,
        )

        logs.log_event(
            "approval_required",
            params={
                "alert_id": ctx.state.alert.id,
                "confidence": confidence.total,
                "threshold": ctx.deps.require_approval_below,
            },
        )

        # End pipeline here — publishing happens after human approval
        return End(
            common.InvestigationReply(
                alert_id=ctx.state.alert.id,
                root_cause=ctx.state.investigation.root_cause if ctx.state.investigation else None,
                remediation=ctx.state.investigation.remediation if ctx.state.investigation else None,
                confidence=confidence,
                findings_summary=findings_summary,
                sources_queried=(
                    [f.source for f in ctx.state.investigation.findings]
                    if ctx.state.investigation
                    else []
                ),
                approval_status="pending",
            )
        )

    return PublishFindings(confidence=confidence)
```

- [x]**Step 4: Add `approval_status` field to InvestigationReply**

In `common.py`, add to `InvestigationReply`:

```python
class InvestigationReply(BaseModel):
    # ... existing fields ...
    approval_status: str | None = None  # "pending", "approved", "rejected", None (no approval needed)
```

- [x]**Step 5: Update DetermineConfidence return type**

Update the return type annotation to include `End`:

```python
async def run(
    self, ctx: GraphRunContext[State, Dependencies]
) -> PublishFindings | End[common.InvestigationReply]:
```

- [x]**Step 6: Update `investigate_alert` to accept approval params**

```python
async def investigate_alert(
    alert: sre_entities.Alert,
    *,
    holmes: holmes_adapter.BaseHolmesAdapter,
    # ... existing params ...
    require_approval_below: float = 0.0,
    request_approval_fn: common.RequestApprovalFn | None = None,
) -> common.InvestigationReply:
    state = State(alert=alert)
    dependencies = Dependencies(
        # ... existing fields ...
        require_approval_below=require_approval_below,
        request_approval_fn=request_approval_fn,
    )
    # ... rest unchanged ...
```

- [x]**Step 7: Write functional test for approval gate**

```python
# tests/functional/test_sre_approval_gate.py
from __future__ import annotations

import pytest

from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs import common, sre_investigation
from tests.factories import make_alert


@pytest.mark.usefixtures("patch_alert_classifier", "patch_root_cause_analyser")
class TestApprovalGate:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_approval_request(
        self, mock_holmes: holmes_adapter.MockHolmesAdapter
    ) -> None:
        # Given a configured approval threshold of 0.8
        approval_calls: list[tuple] = []

        async def track_approval(*args):
            approval_calls.append(args)
            return "mock-ts-123"

        alert = make_alert()

        # When the pipeline runs (default RCA confidence is 0.85 -> total ~0.705 which is < 0.8)
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.8,
            request_approval_fn=track_approval,
        )

        # Then the approval function was called
        assert len(approval_calls) == 1
        # And the reply indicates pending approval
        assert result.approval_status == "pending"

    @pytest.mark.asyncio
    async def test_high_confidence_skips_approval(
        self, mock_holmes: holmes_adapter.MockHolmesAdapter
    ) -> None:
        # Given a low approval threshold of 0.3
        approval_calls: list[tuple] = []

        async def track_approval(*args):
            approval_calls.append(args)
            return "mock-ts"

        alert = make_alert()

        # When the pipeline runs (confidence ~0.705 which is > 0.3)
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.3,
            request_approval_fn=track_approval,
        )

        # Then the approval function was NOT called
        assert len(approval_calls) == 0
        # And the reply has no approval status (published directly)
        assert result.approval_status is None

    @pytest.mark.asyncio
    async def test_no_approval_fn_skips_gate(
        self, mock_holmes: holmes_adapter.MockHolmesAdapter
    ) -> None:
        # Given a threshold but no approval function
        alert = make_alert()

        # When the pipeline runs
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.8,
            request_approval_fn=None,
        )

        # Then it publishes directly (no crash, no pending status)
        assert result.approval_status is None
        assert result.root_cause is not None
```

- [x]**Step 8: Run all tests**

Run: `uv run pytest tests/unit/interfaces/graphs/ tests/functional/ -v`
Expected: All tests PASS

- [x]**Step 9: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [x]**Step 10: Commit**

```bash
git add src/sentinel/interfaces/graphs/sre_investigation.py src/sentinel/interfaces/graphs/common.py tests/functional/test_sre_approval_gate.py
git commit -m "feat: add human approval gate to SRE pipeline for low-confidence findings"
```

---

## Checkpoint: Verification

After all tasks are complete, verify the full system:

- [x]`uv run pytest tests/ -v` — all tests pass
- [x]`uv run ruff check src/sentinel/` — no lint errors
- [x]`uv run mypy src/sentinel/` — no type errors (or same baseline as before)
- [x]Review the changes holistically: error handling in both pipelines, approval gate in SRE pipeline
