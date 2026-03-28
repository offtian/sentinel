---
paths:
  - "**/*.py"
---

# Python Conventions

## Imports
- Import modules, not objects: `from django import http` not `from django.http import HttpResponse`
- Exception: stdlib objects like `Decimal`, `Optional`, `defaultdict` can be imported directly
- This enables `mock.patch.object(module, "collaborator")` for isolated unit tests

## Function Signatures
- Always specify parameters explicitly with type annotations. Never use `*args`/`**kwargs` without good reason
- If many params, use Introduce Parameter Object (an attrs frozen class) instead of `**kwargs`

## Naming
- Singular nouns for class names: `UserProfile` not `UserDetails`, `EventBatch` not `Events`
- Leading underscore for private functions, classes, methods, modules, module-level vars
- Trailing underscore to avoid name collisions with builtins: `property_`, `transaction_`
- But don't force callers to use underscore-suffixed kwargs — find a different argument name
- Use American English spelling: `serializers.py` not `serialisers.py`

## Data Classes
- Favour `attrs` over `dataclasses`. Use `import attrs` (new API), not `import attr`
- Prefer `@attrs.frozen` over `@attrs.define` — use immutable types when modification isn't required
- Use `tuple[str, ...]` over `list[str]` in frozen attrs classes for full immutability
- Use `frozenset` over `set`, `Sequence` over `list` when data shouldn't change

## Error Handling
- Never catch exceptions and do nothing silently — always log, re-raise, or raise a domain-specific exception
- Prefer raising custom exception classes over returning None/False for failure states
- Let the calling code decide how to handle anticipated edge cases
- For fire-and-forget: use a wrapper that delegates to a private function and catches specific exceptions

## Docstrings
- First sentence should complete: "This function will ..."
- Start with "test" for predicates, "return" for query functions
- Use newline after opening `"""` and before closing `"""`
- Prefer type annotations over documenting types in docstrings
- Document exceptions: `:raises FooError: if the foo is bad`

## HTTP Requests
- Always include `timeout` parameter on HTTP requests
- Prefer `HTTPClient`/`JSONClient` wrappers from services_base