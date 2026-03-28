---
paths:
  - "**/*.py"
---

# Application & Architecture Conventions

## Layered Architecture
- `interfaces/` — views, Celery tasks, CLI commands. Translate interface events into domain calls. NO application logic here.
- `application/usecases/` — orchestrate use-case journeys. Single entry point per use-case.
- `domain/` — reusable business logic, agnostic of which use-case calls it.
- `data/` — Django models only. Models should be thin with no business logic.

## Domain Layer Structure
- Package by domain category: `domain/$category/$subcategory/`
- Read-only: `queries.py` (or `queries/` package)
- Write: `operations.py` (or `operations/` package)
- For queries: categorise by the type of object returned
- For operations: categorise by the primary object being operated on

## Application Layer Structure
- `application/usecases/$category/$usecase_name/`
- Import public objects into `__init__.py` (including exception classes)
- Prefix private modules with `_` (e.g. `_trigger.py`, `_documents.py`)
- Public functions MUST use keyword-only args: `def do_thing(*, foo, bar):`
- Public functions MUST have docstrings with params, return types, and raisable exceptions

## Events
- `params`: things known BEFORE the event, `meta`: things known AFTER
- Pass model IDs not instances: `params={'bill_id': bill.id}`
- Call `.isoformat()` on dates/datetimes in event payloads
- Use reverse domain name notation for event types: `"comms.message.send-success"`

## Exception Handling
- Distinguish anticipated vs unanticipated exceptions
- Anticipated: handle gracefully, don't log to Sentry
- Unanticipated: log to Sentry with `logger.exception("descriptive message")` — Sentry picks up the exception automatically
- NEVER format exception message into the log message — pass data as logger args: `logger.exception("Failed for %s", x)`
- Exception classes must be importable from the same module as the function that raises them
- Prefer defining exceptions in the module where they're raised, avoid shared `exceptions.py`

## Celery Tasks
- Declare with specific queue: `@app.task(queue=settings.MY_QUEUE)`
- Use kwargs-only with `**kwargs` catchall: `def my_task(*, foo, bar, **kwargs):`
- Call with: `my_task.apply_async(kwargs={"foo": 1, "bar": 2})`

## System Clock
- Minimise `localtime.now()`/`localtime.today()` calls in domain layer
- Compute dates at interface layer, pass them in as explicit parameters
- Never use `def fn(*, base_date=None):` defaulting to today — require the caller to pass it

## Time Periods
- Use datetime fields, not date fields (avoids DST edge cases)
- Upper bound should be nullable and exclusive: `active_from`, `active_to = None`

## Keyword Arguments
- Always use kwargs when calling functions where positional args aren't obvious
- Always use keyword-only args (`*`) for public domain/application functions