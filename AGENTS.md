# AGENTS.md

Coding conventions for AI agents working in this repository.

**Rules are loaded automatically** from `.claude/rules/` based on file paths:
- `python.md` — imports (module-level only, import modules not objects), attrs, naming, structlog, error handling
- `application.md` — layered architecture, exception handling, system clock, keyword args
- `testing.md` — GWT comments (mandatory), test structure, factories, time handling, variable naming
- `sentinel.md` — PydanticAI agents, Pydantic Graph pipelines, vendor adapters, configuration, skills

## Quick Reference

### Import Pattern (most common violation)
```python
# WRONG
from sentinel.domain.alerts.entities import Alert

# CORRECT
from sentinel.domain.alerts import entities as alert_entities
alert = alert_entities.Alert(...)
```

### Logging
- `structlog` exclusively — stdlib `logging` forbidden (enforced by import-linter)
- `logs.log_event("event_name", params={...})` for events
- `logs.log_exception(exc, params={...})` for errors

### Testing Factories
Located in `tests/factories/__init__.py`:
`make_alert()`, `make_ticket()`, `make_investigation()`, `make_finding()`, `make_doc_source()`, `make_response_suggestion()`, `make_confidence_score()`

### Workflow (LangGraph) Import Pattern

New pipeline code lives in `interfaces/workflows/`. Import the module, not the function:

```python
# CORRECT — import the module
from sentinel.interfaces.workflows import sre_investigation
outcome = await sre_investigation.investigate_alert(alert=alert, envelope=envelope, graph=graph)

# CORRECT — import the module
from sentinel.interfaces.workflows import support_review
reply = await support_review.review_ticket(ticket=ticket, envelope=envelope, ...)
```

All LangGraph graph nodes must be wrapped with `with_envelope` from
`interfaces/workflows/_envelope.py` for RFC §3.1 identity propagation.

Do NOT import from `interfaces/graphs/_archive/` — that package is reference-only and
import-linter contracts forbid new dependencies on it.

### Git
See global rules in `~/.claude/rules/git-workflow.md` for commit message format and PR workflow.
