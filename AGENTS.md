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
from sentinel.domain.sre_entities import Alert

# CORRECT
from sentinel.domain import sre_entities
alert = sre_entities.Alert(...)
```

### Logging
- `structlog` exclusively — stdlib `logging` forbidden (enforced by import-linter)
- `logs.log_event("event_name", params={...})` for events
- `logs.log_exception(exc, params={...})` for errors

### Testing Factories
Located in `tests/factories/__init__.py`:
`make_alert()`, `make_ticket()`, `make_investigation()`, `make_finding()`, `make_doc_source()`, `make_response_suggestion()`, `make_confidence_score()`

### Git
See global rules in `~/.claude/rules/git-workflow.md` for commit message format and PR workflow.
