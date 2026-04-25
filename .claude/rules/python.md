---
paths:
  - "**/*.py"
---

# Python Conventions

## Imports (CRITICAL — most common violation)

ALWAYS import MODULES, NEVER import objects/functions/classes directly:

```python
# WRONG — importing objects directly
from sentinel.domain.sre_entities import Alert, Investigation
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient

# CORRECT — importing modules, then using module.Object in code
from sentinel.domain import sre_entities
from sentinel.domain.vendor_adapters import pagerduty

alert = sre_entities.Alert(...)
client = pagerduty.PagerDutyClient(...)
```

Why: This enables `mock.patch.object(module, "Object")` for isolated unit tests.

Exception: stdlib objects like `Decimal`, `Optional`, `defaultdict`, `datetime` can be imported directly.

ALL imports MUST be at module level (top of file) — NEVER use inline/deferred imports inside functions, classes, or methods.

Exception: `if TYPE_CHECKING:` blocks and `if __name__ == "__main__":` blocks are acceptable.

## Function Signatures
- Always specify parameters explicitly with type annotations. Never use `*args`/`**kwargs` without good reason
- If many params, use Introduce Parameter Object (an attrs frozen class) instead of `**kwargs`

## Naming
- Singular nouns for class names: `Alert` not `Alerts`, `Investigation` not `Investigations`
- Leading underscore for private functions, classes, methods, modules, module-level vars
- Trailing underscore to avoid name collisions with builtins
- Use American English spelling: `serializers.py` not `serialisers.py`

## Data Classes
- Favour `attrs` over `dataclasses`. Use `import attrs` (new API), not `import attr`
- Prefer `@attrs.frozen` over `@attrs.define` — use immutable types when modification isn't required
- Use `tuple[str, ...]` over `list[str]` in frozen attrs classes for full immutability
- Use `frozenset` over `set`, `Sequence` over `list` when data shouldn't change
- Exception: `@dataclasses.dataclass` is acceptable for PydanticAI agent Dependencies and Graph node state
- Exception: `pydantic.BaseModel` is the contract for configuration types (`Settings`, `BaseConfiguration`) and API/webhook boundary types — attrs is for pure-Python domain shapes

## Shared Primitives (`data/`)

Pure data shapes that both `config` and `domain` need to agree on live in `src/sentinel/data/` alongside the SQLModel tables. Examples: policy types (`ApprovalPolicy`, `OutputChannel`, `RedactionPolicy`), identity envelopes (`Envelope` carrying `request_id` / `tenant_id` / `pii_class`), capability tokens, discriminator enums (`TeamId`, `ApproverRole`, `OutputKind`).

- `attrs.frozen(kw_only=True, slots=True)` — immutable, kw-only construction, slot-backed.
- Pure data only: no vendor binding, no business logic, no I/O. If the type needs a vendor SDK or a query, it belongs in `domain/` instead.
- For primitives carried by config (`ApprovalPolicy`, `RedactionPolicy`), expose a `.default()` classmethod returning firm-wide-safe values and a `.empty()` classmethod returning a fail-closed placeholder. `BaseConfiguration` composes them via `Field(default_factory=Primitive.default)` (or `.empty`). Mis-wired pipelines fail closed instead of silently permitting.
- No `Settings`-driven primitives — every primitive is constructed in code with deterministic defaults. Env-var knobs that customise a primitive's fields surface on `BaseConfiguration` via `@property`, not on the primitive itself.
- Use `Literal` aliases for shared discriminator strings (`ConfidenceLabel`, `ApproverRole`, `OutputKind`, `TeamId`). When a discriminator overlaps with an existing domain enum (e.g., `domain.confidence.entities.ConfidenceLabel`), reuse the existing enum rather than introducing a parallel `Literal` with different casing — duplicates silently mismatch on equality. If the discriminator must live in `data/` for layering reasons, lift the canonical enum down to `data/` and re-export from the domain module so there is exactly one definition.
- Why `data/` and not `domain/`: `data/` sits below `config` in the import-linter layer order, so `config.py` can compose primitives without an extra layer reshuffle. `domain/` and above can also import freely from `data/` (lower layer). Both layers depend on the same shape without either importing the other — that's the point.

## Error Handling
- Never catch exceptions and do nothing silently — always log, re-raise, or raise a domain-specific exception
- Prefer raising custom exception classes over returning None/False for failure states
- Let the calling code decide how to handle anticipated edge cases

## Logging
- Use `structlog` exclusively — stdlib `logging` is forbidden (enforced by import-linter)
- `logs.log_event("event_name", params={...})` for structured events
- `logs.log_exception(exc, params={...})` for errors
- Never format exception message into the log string

## Docstrings
- First sentence should complete: "This function will ..."
- Start with "test" for predicates, "return" for query functions
- Use newline after opening `"""` and before closing `"""`
- Prefer type annotations over documenting types in docstrings
- Document exceptions: `:raises FooError: if the foo is bad`

## HTTP Requests
- Always include `timeout` parameter on HTTP requests
