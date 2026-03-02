# Sentinel Multi-Agent Architecture Review

**Reviewer**: Staff MLE, Platform Engineering
**Date**: 2026-04-01
**Scope**: Architecture evaluation, gap analysis, eval framework design

---

## 1. Overall Rating: 7.2 / 10

Sentinel is a well-structured codebase with strong engineering fundamentals. The clean architecture, type safety, and vendor abstraction put it ahead of most internal tooling I've seen at hedge funds. But it's currently a **two-pipeline system masquerading as a multi-agent platform**. The gap between "what's built" and "production multi-agent orchestration for a trading floor" is significant.

---

## 2. What's Good (Keep These)

### 2.1 Layered Architecture with Enforcement

The import-linter contracts in `pyproject.toml` are the right call. Most teams *say* they have clean architecture but violate layer boundaries within weeks. Enforcing `interfaces → application → domain → data` at CI is exactly how you prevent rot. The fact that vendor adapters can't be imported into domain operations (forcing DI) shows real discipline.

### 2.2 Pydantic Graph as Pipeline Orchestrator

Using `pydantic_graph.Graph` with typed `BaseNode[State, Dependencies, Reply]` is a strong choice over LangGraph or raw chains. The state machine semantics are explicit — each node declares its successor type at compile time (`ClassifyAlert → InvestigateWithHolmes | End[...]`). This makes dead-code analysis and type checking possible, which LangGraph can't do.

The pattern of frozen `Dependencies` dataclass with DI + mutable `State` that uses `model_copy(update={...})` for immutable transitions is clean. You avoid the "god context" anti-pattern that plagues most agent frameworks.

### 2.3 Vendor Adapter Abstraction

`BaseHolmesAdapter`, `BaseDocumentSearcher`, `BasePastTicketSearcher` — these ABCs with `is_configured` no-op semantics mean you can deploy without all integrations wired up. This is critical for a hedge fund where you might have Datadog in one fund but Grafana in another.

### 2.4 Structured Logging Convention

Enforcing `structlog` via import-linter (stdlib `logging` is forbidden) and using `logs.log_event("event_name", params={...})` consistently means every agent node emits queryable events. In a platform engineering context, this is table stakes but rarely done well.

### 2.5 Async-First with Proper Concurrency

`asyncio.TaskGroup()` for parallel search (doc + ticket search in support pipeline), `asyncio.gather()` for publishing to Slack + PagerDuty + DB — you're not leaving latency on the table. The `TaskGroup` usage in `SearchDocumentation` is particularly well-structured.

---

## 3. What's Bad (Fix These)

### 3.1 No Real Multi-Agent Orchestration — Just Sequential Pipelines

**Severity: Critical**

Both graphs (`sre_investigation.py`, `support_review.py`) are **linear DAGs**, not agent networks. The SRE pipeline is literally:

```
ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings
```

There's no conditional branching (except the trivial "no search results → early exit" in support), no agent-to-agent delegation, no feedback loops, and no supervisor oversight. In a real incident, the root cause analyser might need to request *additional* Holmes investigation (loopback), or the confidence score might be too low and trigger a different investigation strategy.

**What you have**: Orchestrated pipelines with LLM nodes.
**What you need**: Agents that can delegate, retry with different strategies, escalate, and coordinate.

### 3.2 Confidence Scoring is a Stub

**Severity: High**

Look at `ConfidenceScore.from_total()` — it takes a single float and copies it into all three component scores identically:

```python
# from domain/confidence/entities.py
return ConfidenceScore(
    label=label,
    total=total,
    components=ConfidenceComponents(
        source_count_score=IndividualScore(raw=total, weighted=total),
        relevance_score=IndividualScore(raw=total, weighted=total),
        recency_score=IndividualScore(raw=total, weighted=total),
    ),
)
```

The entire multi-factor confidence model (source_count 0.3, relevance 0.5, recency 0.2) documented in CLAUDE.md and tested in functional tests is **not actually implemented**. `DetermineConfidence` in both pipelines calls `from_total(self.raw_confidence)` where `raw_confidence` is just the LLM's self-reported confidence float. You're trusting the LLM to self-assess — this is known to be unreliable.

The `from_factors()` method referenced in test comments doesn't exist. The functional tests pass because they assert against the LLM's mocked confidence value, not against actual multi-factor computation.

### 3.3 HolmesGPT is a TODO

**Severity: High**

The core differentiator for AI SRE — automated observability investigation — is a placeholder:

```python
# TODO: Integrate with actual HolmesGPT SDK once dependency is resolved.
return HolmesInvestigationResult(
    analysis=f"Investigation pending for alert: {alert.title}",
    tool_calls=[],
    sources_queried=[],
)
```

This means the entire RCA pipeline currently feeds an empty string to the root cause analyser. Your pipeline looks complete in tests because `MockHolmesAdapter` returns canned findings, but in production you're getting `"Investigation pending for alert: ..."` as the Holmes analysis. The root cause agent is essentially hallucinating based on the alert title alone.

### 3.4 Webhook Handlers Run Pipelines Synchronously

**Severity: High**

In `sre/router.py`, the PagerDuty webhook handler `await`s the full investigation pipeline inline:

```python
result = await sre_investigation.investigate_alert(alert=alert, holmes=holmes)
```

PagerDuty expects webhook responses within 16 seconds. An LLM pipeline with 2 agent calls + Holmes investigation will easily exceed that. The `CLAUDE.md` mentions a job queue (`enqueue_investigation()`), but the webhook handlers don't use it. The `application/` layer has only empty `__init__.py` files — the enqueue/dequeue/persist code referenced in CLAUDE.md doesn't exist yet.

### 3.5 State Mutation Inside Nodes

**Severity: Medium**

Despite using `model_copy(update={...})` for immutability, the `State` dataclass itself is mutable, and nodes mutate it via `ctx.state.alert = ...` and `ctx.state.investigation = ...`. This means:

- State transitions aren't auditable (no history of intermediate states)
- If a node fails partway, state is in an inconsistent intermediate
- You can't replay or branch from a checkpoint

For a hedge fund platform where auditability matters, you want event-sourced state: each node appends to a state log, and you can reconstruct any point in the pipeline.

### 3.6 No Error Handling in Pipeline Nodes

**Severity: Medium**

None of the graph nodes have try/except. If `alert_classifier.agent.run()` throws (LiteLLM timeout, rate limit, malformed response), the entire pipeline crashes and the caller gets an unstructured 500. There's no retry logic, no fallback classification, no circuit breaking at the agent level (though `CircuitBreaker` exists in domain, it's only used in `DirectToolsetAdapter` which isn't wired in).

### 3.7 Config Module Anti-pattern

**Severity: Medium**

`_config.py` reads env vars at module import time via `environs`. This means:

- Tests can't override config without monkeypatching module-level globals
- Settings aren't validated at startup (no Pydantic Settings despite CLAUDE.md claiming it exists)
- The `settings.py` referenced in CLAUDE.md doesn't exist — only `_config.py` with raw `env.str()` calls
- Circular import risk: `alert_classifier.py` imports `_config` at module level to construct the agent singleton

### 3.8 Duplicated Webhook Logic

**Severity: Low**

`handle_pagerduty_webhook` and `handle_datadog_webhook` in `sre/router.py` are nearly identical (50+ lines of copy-paste). The only difference is the parse function called. This should be a single handler with strategy dispatch.

---

## 4. What's Missing (Build These)

### 4.1 Supervisor Agent

Your current system has no oversight layer. In a trading floor context, you need:

- **Routing decisions**: The "lightweight intent router" mentioned isn't implemented. You need a supervisor that classifies incoming requests across all agents (SRE, support, coding, customer service) and delegates.
- **Quality gating**: Before publishing any finding or response, a supervisor should evaluate whether the output meets minimum quality bar. Your `DetermineConfidence` node is doing this halfheartedly — the supervisor should be able to reject and re-run with different parameters.
- **Escalation logic**: Low confidence → human handoff, not just a label. The supervisor tracks SLA timers and escalates if the pipeline is taking too long.

Recommended design: A `SupervisorGraph` that wraps each domain pipeline. It receives all inbound events, classifies intent, dispatches to the right sub-graph, evaluates the output, and decides whether to publish, retry with different params, or escalate.

### 4.2 Human Sign-off Agent

For a hedge fund, automated actions on production infrastructure and customer-facing responses **must** have human approval gates. You need:

- **Approval workflow**: Investigation findings posted to Slack with approve/reject buttons. Only approved findings get posted to PagerDuty or Jira.
- **Time-boxed auto-approve**: For HIGH confidence outputs from known alert patterns, auto-approve after N minutes if no human rejects.
- **Audit trail**: Every approval/rejection logged with who, when, why. This is regulatory — FCA/SEC expect audit trails for automated systems that touch production.

### 4.3 Coding Agent

For a platform engineering team, the highest-leverage extension is an agent that can:

- Generate runbooks from investigation findings
- Create Terraform/CDK patches for infrastructure issues (e.g., scaling configs)
- Write Datadog monitors for newly discovered failure modes
- Generate Jira tickets with reproduction steps from investigations

This doesn't need to be an autonomous coder. Start with a "draft code suggestion" agent that proposes changes in a PR-like format for human review.

### 4.4 Customer Service Agent (Trader/Quant-Facing)

Distinct from the Jira support agent. This handles real-time Slack interactions with internal users (traders, quants, PMs). It needs:

- **Context awareness**: Know which desk/fund/strategy the user belongs to, what systems they depend on
- **Proactive notification**: When an investigation finds an issue affecting a specific trading system, proactively notify relevant desks
- **Natural language querying**: "Is the options pricing service healthy?" → runs diagnostics, returns status
- **Incident subscription**: "Notify me when PROD-1234 is resolved" → watches investigation status

### 4.5 Intent Router

The CLAUDE.md mentions an intent router, and there's a `test_intent_detection.py` in unit tests, but the `interfaces/chat/` directory only contains `__pycache__`. The router implementation is missing. Design it as a thin classification layer (could even be a structured output call to gpt-4.1-mini) that routes to the appropriate pipeline/agent.

---

## 5. Pluggable Eval Framework Design

Your current eval approach (golden JSON cases with mocked agents) tests pipeline plumbing, not agent quality. Here's a framework that gives you both abstract metrics and domain-specific evaluation, pluggable per agent.

### 5.1 Architecture

```
sentinel/
  evals/
    framework/
      base.py          # Abstract evaluator protocol + metric types
      runner.py         # Parametrized runner, reporting, CI integration
      metrics.py        # Shared metric implementations (latency, cost, token usage)
      judges.py         # LLM-as-judge base implementations
      datasets.py       # Dataset loader (JSON, YAML, CSV)
    agents/
      alert_classifier/
        evaluator.py    # Domain-specific evaluator
        datasets/       # Golden cases + adversarial cases
        rubrics/        # Grading criteria as structured prompts
      root_cause_analyser/
        evaluator.py
        datasets/
        rubrics/
      ticket_reviewer/
        evaluator.py
        datasets/
        rubrics/
      response_drafter/
        evaluator.py
        datasets/
        rubrics/
      supervisor/
        evaluator.py    # End-to-end routing + quality eval
        datasets/
    pipelines/
      sre_investigation/
        evaluator.py    # Full pipeline eval (agent interactions)
        datasets/
      support_review/
        evaluator.py
        datasets/
```

### 5.2 Abstract Base: The Evaluator Protocol

```python
from __future__ import annotations

import abc
from typing import Any, Generic, TypeVar
from datetime import datetime

import attrs
from pydantic import BaseModel


# ── Metric Types ──────────────────────────────────────────────

class MetricKind(enum.Enum):
    """Whether higher or lower values are better."""
    HIGHER_IS_BETTER = "higher_is_better"   # accuracy, recall
    LOWER_IS_BETTER = "lower_is_better"     # latency, cost
    BINARY = "binary"                        # pass/fail


@attrs.frozen
class MetricResult:
    name: str
    value: float
    kind: MetricKind
    threshold: float | None = None    # pass/fail threshold
    passed: bool | None = None        # None if no threshold set
    metadata: dict[str, Any] = attrs.field(factory=dict)


@attrs.frozen
class EvalResult:
    evaluator_name: str
    case_id: str
    metrics: tuple[MetricResult, ...]
    passed: bool                       # all metrics passed their thresholds
    latency_ms: float
    token_usage: dict[str, int]        # {"input": N, "output": M}
    timestamp: datetime
    raw_output: Any = None             # for debugging


# ── Dataset Types ─────────────────────────────────────────────

InputT = TypeVar("InputT")
ExpectedT = TypeVar("ExpectedT")


@attrs.frozen
class EvalCase(Generic[InputT, ExpectedT]):
    id: str
    input: InputT
    expected: ExpectedT
    tags: tuple[str, ...] = ()         # e.g., ("critical", "regression", "edge-case")
    description: str = ""


# ── Evaluator Protocol ────────────────────────────────────────

class BaseEvaluator(abc.ABC, Generic[InputT, ExpectedT]):
    """
    Each agent/pipeline implements this to define:
    1. What metrics to compute
    2. How to run the agent under test
    3. How to grade the output
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Evaluator identifier, e.g., 'alert_classifier'."""

    @abc.abstractmethod
    def metrics_spec(self) -> tuple[MetricSpec, ...]:
        """Declare which metrics this evaluator computes + thresholds."""

    @abc.abstractmethod
    async def run_agent(
        self,
        case: EvalCase[InputT, ExpectedT],
        *,
        model: str,
    ) -> Any:
        """Execute the agent under test. Returns raw output."""

    @abc.abstractmethod
    async def grade(
        self,
        case: EvalCase[InputT, ExpectedT],
        output: Any,
    ) -> tuple[MetricResult, ...]:
        """Score the output against expected. Returns metric results."""


@attrs.frozen
class MetricSpec:
    name: str
    kind: MetricKind
    threshold: float
    description: str = ""
```

### 5.3 Domain-Specific Metrics Per Agent

**Alert Classifier**:
- `severity_accuracy`: Exact match on severity label (HIGHER_IS_BETTER, threshold 0.9)
- `category_accuracy`: Exact match on category (HIGHER_IS_BETTER, threshold 0.85)
- `escalation_recall`: True positive rate on `requires_immediate_action` (HIGHER_IS_BETTER, threshold 0.95) — you can't miss a critical alert on a trading floor
- `escalation_precision`: False positive rate on escalation (HIGHER_IS_BETTER, threshold 0.7) — alert fatigue kills SRE teams
- `latency_p95_ms`: 95th percentile classification latency (LOWER_IS_BETTER, threshold 3000)

**Root Cause Analyser**:
- `root_cause_relevance`: LLM-as-judge scoring root cause against known ground truth (HIGHER_IS_BETTER, threshold 0.7)
- `remediation_actionability`: LLM-as-judge: are the steps specific enough to execute? (HIGHER_IS_BETTER, threshold 0.6)
- `evidence_grounding`: Fraction of claims that cite specific data from Holmes findings (HIGHER_IS_BETTER, threshold 0.5)
- `hallucination_rate`: Claims that contradict or aren't supported by provided evidence (LOWER_IS_BETTER, threshold 0.1)
- `confidence_calibration`: |self-reported confidence - actual correctness| (LOWER_IS_BETTER, threshold 0.2)

**Ticket Reviewer**:
- `category_accuracy`: Exact match (HIGHER_IS_BETTER, threshold 0.85)
- `urgency_accuracy`: Within 1 level (HIGHER_IS_BETTER, threshold 0.9)
- `search_query_recall`: Do generated queries find the relevant docs? (HIGHER_IS_BETTER, threshold 0.6)
- `key_questions_relevance`: LLM-as-judge: are questions useful for resolution? (HIGHER_IS_BETTER, threshold 0.7)

**Response Drafter**:
- `response_helpfulness`: LLM-as-judge with domain rubric (HIGHER_IS_BETTER, threshold 0.7)
- `source_citation_accuracy`: All cited sources actually exist and are relevant (HIGHER_IS_BETTER, threshold 0.9)
- `tone_appropriateness`: Professional, empathetic, hedge-fund-appropriate (HIGHER_IS_BETTER, threshold 0.8)
- `factual_accuracy`: No contradictions with source docs (HIGHER_IS_BETTER, threshold 0.9)
- `response_completeness`: Addresses all key questions from reviewer (HIGHER_IS_BETTER, threshold 0.7)

**Supervisor (end-to-end)**:
- `routing_accuracy`: Correct pipeline selected (HIGHER_IS_BETTER, threshold 0.95)
- `quality_gate_precision`: Rejected outputs that deserved rejection (HIGHER_IS_BETTER, threshold 0.8)
- `quality_gate_recall`: Caught bad outputs before publishing (HIGHER_IS_BETTER, threshold 0.9)
- `end_to_end_latency_p95`: Full pipeline time (LOWER_IS_BETTER, threshold 30000)
- `escalation_appropriateness`: Escalated when needed, didn't when not (HIGHER_IS_BETTER, threshold 0.85)

### 5.4 LLM-as-Judge Implementation

```python
@attrs.frozen
class JudgeRubric:
    """Domain-specific grading criteria for LLM judge."""
    criteria: str           # What to evaluate
    scale: str              # How to score (1-5, pass/fail, etc.)
    examples: tuple[JudgeExample, ...] = ()  # Few-shot calibration


@attrs.frozen
class JudgeExample:
    input: str
    output: str
    score: float
    reasoning: str


class LLMJudge:
    """
    Reusable LLM-as-judge that takes a rubric and scores outputs.
    Uses a different model than the agent under test to avoid self-eval bias.
    """

    def __init__(
        self,
        *,
        judge_model: str = "openai/gpt-4.1",  # always stronger than agent
        rubric: JudgeRubric,
    ) -> None:
        self._model = judge_model
        self._rubric = rubric

    async def score(
        self,
        *,
        input_text: str,
        output_text: str,
        reference: str | None = None,
    ) -> JudgeVerdict:
        """Returns score 0.0-1.0 with reasoning."""
        ...


@attrs.frozen
class JudgeVerdict:
    score: float           # 0.0 - 1.0
    reasoning: str         # chain-of-thought explanation
    rubric_name: str
```

### 5.5 Eval Runner with CI Integration

```python
class EvalRunner:
    """
    Runs evaluators against datasets, computes aggregate metrics,
    and produces reports suitable for CI gating.
    """

    async def run_eval(
        self,
        evaluator: BaseEvaluator,
        dataset: Sequence[EvalCase],
        *,
        model: str,
        concurrency: int = 5,
        tags: set[str] | None = None,  # filter cases by tag
    ) -> EvalReport:
        ...

    def check_regression(
        self,
        current: EvalReport,
        baseline: EvalReport,
        *,
        max_regression_pct: float = 5.0,
    ) -> RegressionResult:
        """Compare against previous run, flag regressions > threshold."""
        ...


@attrs.frozen
class EvalReport:
    evaluator_name: str
    model: str
    results: tuple[EvalResult, ...]
    aggregate: dict[str, AggregateMetric]  # mean, p50, p95 per metric
    pass_rate: float
    timestamp: datetime
    run_id: str

    def to_json(self) -> str: ...
    def to_markdown(self) -> str: ...
```

### 5.6 Plugin Registration

Each agent registers its evaluator via a simple registry pattern:

```python
# evals/registry.py
_EVALUATORS: dict[str, type[BaseEvaluator]] = {}

def register_evaluator(name: str):
    def decorator(cls: type[BaseEvaluator]):
        _EVALUATORS[name] = cls
        return cls
    return decorator

def get_evaluator(name: str) -> BaseEvaluator:
    return _EVALUATORS[name]()

def list_evaluators() -> list[str]:
    return list(_EVALUATORS.keys())
```

```python
# evals/agents/alert_classifier/evaluator.py
@register_evaluator("alert_classifier")
class AlertClassifierEvaluator(BaseEvaluator[AlertInput, AlertExpected]):
    ...
```

Usage in CI:

```bash
# Run all evals
uv run python -m sentinel.evals --all --model openai/gpt-4.1

# Run specific agent eval
uv run python -m sentinel.evals --agent alert_classifier --model openai/gpt-4.1-mini

# Run with regression check against baseline
uv run python -m sentinel.evals --all --baseline evals/baselines/2026-03-28.json

# Run only critical/regression tagged cases
uv run python -m sentinel.evals --all --tags critical,regression
```

### 5.7 Three Eval Tiers

| Tier | Runs | What It Tests | LLM Calls? | CI Gate? |
|------|------|---------------|------------|----------|
| **Unit** | Every PR | Pipeline plumbing, node transitions, state shape | No (mocked agents) | Yes, must pass |
| **Component** | Nightly | Individual agent quality against golden datasets | Yes (real LLM) | Yes, flag regressions |
| **E2E** | Weekly / pre-release | Full pipeline with real LLM + real searchers (staging) | Yes | Advisory only |

The existing functional tests map to Tier 1. You have nothing for Tier 2 and 3 — that's the gap this framework fills.

---

## 6. Priority-Ordered Recommendations

| # | Item | Effort | Impact | Do When |
|---|------|--------|--------|---------|
| 1 | Wire webhook handlers to job queue (async) | S | Critical — PD will timeout | This sprint |
| 2 | Implement `from_factors()` confidence scoring | S | High — currently a lie | This sprint |
| 3 | Add try/except + retry in pipeline nodes | M | High — production resilience | This sprint |
| 4 | Resolve HolmesGPT dep conflict or build `DirectToolsetAdapter` | L | Critical — core SRE value | Next sprint |
| 5 | Build intent router (thin classifier) | S | High — enables multi-agent | Next sprint |
| 6 | Implement Tier 2 eval framework (component evals) | M | High — catch regressions | Next sprint |
| 7 | Add supervisor graph wrapping both pipelines | L | High — quality gating | Sprint +2 |
| 8 | Human sign-off workflow (Slack approve/reject) | M | Critical for hedge fund compliance | Sprint +2 |
| 9 | Trader-facing customer service agent | L | Medium — high visibility | Sprint +3 |
| 10 | Coding agent (runbook/monitor generation) | L | Medium — force multiplier | Sprint +3 |

---

## 7. Summary

Sentinel's foundation is solid — the layered architecture, type system, and vendor abstraction are production-grade. But you're at an inflection point: the system works as two isolated pipelines, and scaling to a true multi-agent platform requires the orchestration layer (supervisor, routing, approval gates) and the eval infrastructure to validate agent quality at each step. The biggest immediate risks are the synchronous webhook handlers (will break in prod), the stub confidence scoring (misleading metrics), and the HolmesGPT placeholder (core SRE pipeline returns nothing useful).

The eval framework design above gives you a path to catch quality regressions before they hit traders' desks, and the plugin architecture means every new agent you build comes with eval coverage by default.
