#!/bin/bash
# review-rules.sh (PostToolUse on Edit/Write)
# Checks Python files against sentinel project conventions and outputs warnings.

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only review Python files
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

IS_TEST=false
if [[ "$FILE_PATH" == *test_* || "$FILE_PATH" == *tests* ]]; then
  IS_TEST=true
fi

WARNINGS=""

# --- Python Convention Checks ---

# Check: importing objects instead of modules (most common violation)
# Allow: stdlib, typing, attrs, pydantic, dataclasses (for PydanticAI deps)
BAD_IMPORTS=$(grep -nE '^from [a-z]' "$FILE_PATH" 2>/dev/null | \
  grep -vE '(from typing|from __future__|from collections|from decimal|from pathlib|from dataclasses|from enum|from functools|from contextlib|from unittest|from attrs|from pydantic|from sqlmodel|from sqlalchemy|from fastapi|from starlette|from structlog)' | \
  grep -E 'import [A-Z]' | head -5)

if [[ -n "$BAD_IMPORTS" ]]; then
  WARNINGS+="PYTHON RULE (CRITICAL): Import MODULES not objects. Use 'from sentinel.domain import sre_entities' then 'sre_entities.Alert()', NOT 'from sentinel.domain.sre_entities import Alert':\n$BAD_IMPORTS\n\n"
fi

# Check: inline imports (imports inside functions/methods)
# Exclude: TYPE_CHECKING blocks, noqa, __name__ guards, try/except blocks (optional deps)
INLINE_IMPORTS=$(grep -nE '^\s+(from |import )' "$FILE_PATH" 2>/dev/null | \
  grep -vE '(if TYPE_CHECKING|# noqa|__name__|except ImportError|except ModuleNotFoundError)' | \
  grep -vE '^\s*#' | head -5)

if [[ -n "$INLINE_IMPORTS" ]]; then
  WARNINGS+="PYTHON RULE (CRITICAL): ALL imports MUST be at module level (top of file). No inline imports:\n$INLINE_IMPORTS\n\n"
fi

# Check: use of dataclasses outside of PydanticAI deps/graph state
if [[ "$FILE_PATH" != *agents/* && "$FILE_PATH" != *graphs/* ]]; then
  if grep -qE '(from dataclasses import|@dataclass)' "$FILE_PATH" 2>/dev/null; then
    WARNINGS+="PYTHON RULE: Favour attrs over dataclasses. Use @attrs.frozen or @attrs.define.\n\n"
  fi
fi

# Check: bare except or except Exception with pass
if grep -qE 'except.*:\s*$' "$FILE_PATH" 2>/dev/null; then
  NEXT_LINES=$(grep -A1 -nE 'except.*:\s*$' "$FILE_PATH" 2>/dev/null | grep -E '^\s+pass\s*$')
  if [[ -n "$NEXT_LINES" ]]; then
    WARNINGS+="PYTHON RULE: Never catch exceptions and do nothing silently.\n\n"
  fi
fi

# Check: *args usage (not **kwargs — only positional star-args)
if grep -qE 'def .*[^*]\*[a-z]' "$FILE_PATH" 2>/dev/null; then
  WARNINGS+="PYTHON RULE: Avoid *args — specify parameters explicitly with type annotations.\n\n"
fi

# Check: stdlib logging usage (forbidden — use structlog)
if grep -qE '^import logging|^from logging import' "$FILE_PATH" 2>/dev/null; then
  if [[ "$FILE_PATH" != *utils/logs* && "$FILE_PATH" != *migrations/* ]]; then
    WARNINGS+="PYTHON RULE: stdlib logging is FORBIDDEN. Use structlog via logs.log_event() / logs.log_exception().\n\n"
  fi
fi

# --- Architecture Checks (non-test files) ---
if [[ "$IS_TEST" == false ]]; then
  if [[ "$FILE_PATH" == *domain/* ]]; then
    if grep -qE '(datetime\.now|localtime\.now|date\.today)' "$FILE_PATH" 2>/dev/null; then
      WARNINGS+="ARCH RULE: Minimise now()/today() in domain layer — inject dates as parameters.\n\n"
    fi
  fi

  if [[ "$FILE_PATH" == *application/* ]]; then
    PUB_FUNCS=$(grep -nE '^def [a-z]' "$FILE_PATH" 2>/dev/null | grep -v '^def _' | grep -v '\*, ' | head -5)
    if [[ -n "$PUB_FUNCS" ]]; then
      WARNINGS+="ARCH RULE: Public application functions MUST use keyword-only args (def fn(*, foo, bar):).\n$PUB_FUNCS\n\n"
    fi
  fi
fi

# --- Testing Checks (test files only) ---
if [[ "$IS_TEST" == true ]]; then
  if grep -qE '[a-z_]+[0-9]\s*=' "$FILE_PATH" 2>/dev/null; then
    NUMBERED=$(grep -nE '[a-z_]+[0-9]\s*=' "$FILE_PATH" 2>/dev/null | grep -vE '(sha256|md5|v[0-9]|py[0-9]|h[0-9]|step[0-9]|level[0-9]|dim[0-9]|layer[0-9]|gpt[0-9]|uuid[0-9])' | head -5)
    if [[ -n "$NUMBERED" ]]; then
      WARNINGS+="TEST RULE: Avoid numbered variables (account1, account2) — use descriptive names.\n$NUMBERED\n\n"
    fi
  fi

  if grep -qE 'unittest\.TestCase' "$FILE_PATH" 2>/dev/null; then
    WARNINGS+="TEST RULE: Use plain test classes, not unittest.TestCase inheritance.\n\n"
  fi

  TEST_METHODS=$(grep -c 'async def test_\|def test_' "$FILE_PATH" 2>/dev/null)
  GWT_GIVEN=$(grep -c '# Given' "$FILE_PATH" 2>/dev/null)
  if [[ "$TEST_METHODS" -gt 0 && "$GWT_GIVEN" -lt "$TEST_METHODS" ]]; then
    WARNINGS+="TEST RULE: Every test MUST use # Given / # When / # Then comments ($GWT_GIVEN/$TEST_METHODS have # Given).\n\n"
  fi
fi

if [[ -n "$WARNINGS" ]]; then
  echo -e "Code Review (project rules):\n"
  echo -e "$WARNINGS"
  echo "Review these against .claude/rules/ and fix if applicable."
fi

exit 0
