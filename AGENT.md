# AGENT.md

Conventions and coding patterns for AI agents working in this repository.

## Python

### Imports
- Import modules, not objects: `from sentinel.domain import sre_entities` not `from sentinel.domain.sre_entities import Alert`
- Exception: stdlib objects like `Decimal`, `Optional`, `defaultdict` can be imported directly
- This enables `mock.patch.object(module, "collaborator")` for isolated unit tests

### Data Classes
- Prefer `@attrs.frozen` over `@attrs.define` — immutable by default
- Use `import attrs` (new API), not `import attr`
- Use `tuple[str, ...]` over `list[str]` in frozen attrs classes for full immutability
- Use `frozenset` over `set`, `Sequence` over `list` when data shouldn't change

### Function Signatures
- Public domain/application functions MUST use keyword-only args: `def do_thing(*, foo, bar):`
- Always specify parameters explicitly with type annotations
- If many params, use Introduce Parameter Object (an attrs frozen class) instead of `**kwargs`

### Naming
- Singular nouns for classes: `Alert` not `Alerts`
- Leading underscore for private functions, classes, methods, modules
- American English spelling: `serializers.py` not `serialisers.py`

### Docstrings
- First sentence completes: "This function will ..."
- Newline after opening `"""` and before closing `"""`
- Document exceptions: `:raises FooError: if the foo is bad`
- Prefer type annotations over documenting types in docstrings

### Error Handling
- Never catch exceptions silently — log, re-raise, or raise domain-specific exception
- Distinguish anticipated (handle gracefully, no Sentry) vs unanticipated (log to Sentry)
- Exception classes must be importable from the same module as the function that raises them
- Never format exception into log message — pass as logger args: `logger.exception("Failed for %s", x)`

### HTTP Requests
- Always include `timeout` parameter

## Architecture

### Layer Rules
- `interfaces/` — Translate interface events into domain calls. No application logic.
- `application/` — Orchestrate use-case journeys. Single entry point per use-case.
- `domain/` — Reusable business logic, agnostic of which use-case calls it.
- `data/` — Database models only. Models should be thin with no business logic.

### Domain Layer
- Package by domain category: `domain/$category/`
- Read-only: `queries.py`, Write: `operations.py`

### Application Layer
- Public functions MUST have docstrings with params, return types, and raisable exceptions
- Prefix private modules with `_` (e.g., `_trigger.py`)

### Events
- `params`: things known BEFORE the event, `meta`: things known AFTER
- Pass model IDs not instances: `params={'bill_id': bill.id}`
- Call `.isoformat()` on dates/datetimes in event payloads

### System Clock
- Minimise `now()`/`today()` calls in domain layer
- Compute dates at interface layer, pass them in as explicit parameters
- Never use `def fn(*, base_date=None):` defaulting to today — require the caller to pass it

## Logging

- Use `structlog` exclusively (stdlib `logging` forbidden, enforced by import-linter)
- `logs.log_event(event_name, params={...})` for structured events
- `logs.log_exception(exc, params={...})` for errors

## Testing

### Structure
- Unit/integration: mirror src structure — `src/path/to/foo.py` → `tests/unit/path/to/test_foo.py`
- Functional: name after the use-case — `tests/functional/test_sre_investigation.py`
- Group tests per object: `class TestSomeFunction:`
- Method names complete a sentence with the class: `test_returns_none_when_input_is_empty`

### GWT Comments (mandatory)
Every test method uses full-sentence Given/When/Then comments:
```python
def test_returns_high_when_severity_is_critical(self):
    # Given a critical alert
    alert = make_alert(severity=AlertSeverity.CRITICAL)

    # When confidence is calculated
    result = calculate_confidence(alert=alert)

    # Then the score is high
    assert result.score > 0.8
```

### Variable Naming
- NEVER use numbered variables (`alert1`, `alert2`)
- Use descriptive names: `critical_alert`, `low_priority_alert`

### Time
- Unit tests: inject dates/datetimes as parameters
- Integration/functional: use freezegun to control system clock

### Factories
- Located in `tests/factories/__init__.py`
- Available: `make_alert()`, `make_ticket()`, `make_investigation()`, `make_finding()`, `make_doc_source()`, `make_response_suggestion()`, `make_confidence_score()`

## Git

- Imperative mood: "Add feature" not "Added feature"
- Each commit does ONE thing — don't mix refactoring with functional changes
- Keep PRs small and focused
- Link to the Asana ticket in the PR description
