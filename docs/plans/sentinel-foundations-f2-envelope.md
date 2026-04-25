# Plan: Sentinel Foundations — Phase F2 Envelope Identity Primitive

**Status:** complete
**Created:** 2026-04-25
**Last updated:** 2026-04-25

## Goal

Land the F2.1 / F2.8 / F2.9 slice of the foundations plan: the
`Envelope` frozen-attrs primitive that every downstream pipeline node,
span, and DB row reads tenant identity from. F2.2..F2.7 (FastAPI
middleware, webhook handlers, pipeline propagation, log binding) are
delivered by parallel agents and depend on the public surface this
slice freezes.

Parent plan:
[`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md)
(specifically F2.1, F2.8, F2.9).

## Scope

### In scope

- `Envelope` `attrs.frozen(kw_only=True, slots=True)` carrying the six
  RFC §3.1 fields: `request_id`, `tenant_id`, `cluster_id`, `region`,
  `pii_class`, `received_at`.
- `PIIClass` `Literal` alias re-exported from the envelope module.
- `to_log_context()` returning a `dict[str, str]` for structlog
  binding, with the F2.8 redaction rule applied when
  `pii_class in ("confidential", "mnpi")`.
- `to_span_attributes()` returning a
  `dict[str, opentelemetry.util.types.AttributeValue]` carrying the
  six envelope-owned mandatory attributes per RFC §13.2.
- `make_envelope(**overrides)` factory in `tests/factories/__init__.py`
  for the rest of the F2 agents to lean on.
- Unit tests in `tests/unit/test_envelope.py` covering construction,
  immutability, span-attribute shape, and redaction behaviour by
  pii_class.

### Out of scope

- F2.2 `RequestIdMiddleware` (FastAPI ingress).
- F2.3 wiring the middleware into the FastAPI app.
- F2.4 webhook envelope construction (PagerDuty / Datadog).
- F2.5 pipeline `State.envelope` plumbing.
- F2.6 / F2.7 span-attribute setting and structlog rebinding inside
  every node.
- F2.9 integration test (`tests/integration/test_request_id_propagation.py`)
  — depends on F3 DB tables and F4 OTel wiring.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| File placement | `src/sentinel/data/envelope.py`, **not** `src/sentinel/domain/envelope.py` as the parent filemap suggested. | The F1 lesson recurred: the `Envelope` is consumed by `config` (via webhook handlers and middleware indirectly), `interfaces`, and `application`. `data/` sits below `domain/` in the import-linter layer order, so every layer above can import freely while `data/` itself stays free of upper-layer dependencies. The python-conventions rule explicitly lists "identity envelopes" as a `data/` example. |
| `PIIClass` typing | `Literal["public", "internal", "confidential", "mnpi"]` re-exported alongside `Envelope`. | Matches the `data/policies.py` convention (`ConfidenceLabel`, `ApproverRole`, `OutputKind`). One canonical alias. |
| Redaction at the log boundary | `to_log_context()` swaps `tenant_id` for `tenant_hash = sha256(tenant_id)[:12]` when `pii_class in ("confidential", "mnpi")`. | F2.8 spec; `request_id` (UUID) is never PII so it stays in every context. Span attributes deliberately keep raw `tenant_id` because they aren't the redaction boundary — exporters apply policy downstream. |
| Datetime guard | `attrs.validators` on `received_at` rejects naive datetimes. | The whole project is tz-aware UTC; making a naive value fail loud at construction prevents the silent UTC misinterpretation downstream. |
| Factory placement | `make_envelope` in `tests/factories/__init__.py` so every other F2 agent imports from the same place as `make_alert`, `make_finding`, etc. | Established factory location; keeps imports consistent. |

## Steps

- [x] **F2.1.0** Confirm filemap delta vs parent plan (record in commit
      message). Capture in this plan's Design Decisions.
- [x] **F2.1.1** RED: write `tests/unit/test_envelope.py` covering
      construction, immutability, span-attribute shape, and PII
      redaction by `pii_class`.
- [x] **F2.1.2** GREEN: implement `src/sentinel/data/envelope.py` with
      `Envelope`, `PIIClass`, `to_log_context()`, `to_span_attributes()`.
- [x] **F2.8.1** Apply the sha256-truncated-to-12-chars redaction rule
      inside `to_log_context()` when
      `pii_class in ("confidential", "mnpi")`.
- [x] **F2.9.1** Add `make_envelope(**overrides)` factory to
      `tests/factories/__init__.py` for downstream F2 agents.
- [x] **F2.1.3** REFACTOR: clean comments, run `just lint` (ruff +
      mypy + import-linter) and confirm zero diagnostics.
- [x] **F2.1.4** Confirm full unit suite green (~796+ tests).

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Initial draft. Filemap: parent plan listed `domain/envelope.py`; this slice lands at `data/envelope.py` instead. | Layer ordering — `data/` is below `domain/` so `config` and webhook handlers compose `Envelope` without an upward import. Aligns with the python-conventions rule that lists identity envelopes as a `data/` shape. |

## Outcome

### What was delivered

- `src/sentinel/data/envelope.py`: `Envelope` `attrs.frozen` carrying
  the six RFC §3.1 fields plus a `_validate_tz_aware` validator that
  rejects naive `received_at` values at construction.
- `PIIClass` `Literal` alias re-exported from the same module.
- `to_span_attributes()` returns the six envelope-owned mandatory OTel
  attributes per RFC §13.2 (`request_id`, `tenant_id`, `cluster_id`,
  `region`, `pii_class`, `received_at`); raw `tenant_id` is preserved
  because span attributes are not the redaction boundary.
- `to_log_context()` returns a `dict[str, str]` for structlog binding;
  applies the F2.8 redaction rule, swapping `tenant_id` for
  `tenant_hash = sha256(tenant_id)[:12]` when
  `pii_class in ("confidential", "mnpi")`. `request_id` is always
  present.
- `make_envelope(**overrides)` factory in `tests/factories/__init__.py`
  for downstream F2 agents.
- 16 unit tests in `tests/unit/test_envelope.py`. Full unit suite
  passes 811/811 (baseline 795 + 16 new). `just lint` clean.

### Follow-up / tech debt

- F2.2..F2.7 (middleware, webhook handlers, pipeline State, structlog
  rebind, span-attribute setting) land as parallel agents on top of
  this surface.
- The integration test for end-to-end propagation
  (`tests/integration/test_request_id_propagation.py`) is in F2.9 of
  the parent plan and depends on F3 DB tables and F4 OTel wiring.
