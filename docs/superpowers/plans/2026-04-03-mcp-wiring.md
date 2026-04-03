# MCP Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire MCP client toolsets into the K8s native agent via config.py, and replace stub investigation MCP tools with real database-backed operations.

**Architecture:** config.py builds MCP toolsets from `MCP_SERVERS` + `K8S_MCP_SERVER_URL` settings and injects them into `NativeK8sAgent`. The MCP server's investigation tools use the `databases` library to enqueue jobs and query status from `job_requests`.

**Tech Stack:** Python 3.13, PydanticAI MCPServerHTTP, databases (asyncpg), FastMCP

**Spec:** `docs/superpowers/specs/2026-04-03-mcp-wiring-design.md`

---

## File Structure

### Modified files

| File | What changes |
|------|-------------|
| `src/sentinel/config.py` | Build MCP toolsets, inject into `NativeK8sAgent` |
| `src/sentinel/interfaces/mcp/tools/investigation.py` | Replace stubs with real DB queries |
| `src/sentinel/interfaces/mcp/server.py` | Add `db` to `configure()`, pass to investigation tools |
| `tests/unit/interfaces/mcp/test_server.py` | Update investigation tool tests for new signatures |

### New files

| File | Responsibility |
|------|---------------|
| `tests/unit/test_config_mcp_wiring.py` | Test MCP toolset injection into K8s adapter |

---

## Task 1: Wire MCP Client Toolsets into Config

**Files:**
- Modify: `src/sentinel/config.py:159-183`
- Create: `tests/unit/test_config_mcp_wiring.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/test_config_mcp_wiring.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel import config, settings


class TestBuildK8sInvestigationAdapterMcpWiring:
    def test_injects_mcp_toolsets_from_mcp_servers_setting(self) -> None:
        # Given settings with an MCP server configured
        test_settings = mock.MagicMock(spec=settings.Settings)
        test_settings.k8s_investigation_backend = "native"
        test_settings.k8s_investigator_llm = "openai/gpt-4.1"
        test_settings.mcp_servers = '[{"name": "kubectl", "url": "http://localhost:9090"}]'
        test_settings.k8s_mcp_server_url = ""

        cfg = config.Config(settings=test_settings)

        # When building the K8s adapter
        with mock.patch(
            "sentinel.interfaces.graphs.agents.k8s_runner.run_k8s_agent"
        ):
            adapter = cfg.build_k8s_investigation_adapter()

        # Then MCP toolsets are injected
        assert adapter is not None
        assert len(adapter._mcp_toolsets) == 1

    def test_adds_k8s_mcp_server_url_as_additional_toolset(self) -> None:
        # Given settings with both MCP_SERVERS and K8S_MCP_SERVER_URL
        test_settings = mock.MagicMock(spec=settings.Settings)
        test_settings.k8s_investigation_backend = "native"
        test_settings.k8s_investigator_llm = "openai/gpt-4.1"
        test_settings.mcp_servers = '[{"name": "kubectl", "url": "http://localhost:9090"}]'
        test_settings.k8s_mcp_server_url = "http://localhost:9091"

        cfg = config.Config(settings=test_settings)

        # When building the K8s adapter
        with mock.patch(
            "sentinel.interfaces.graphs.agents.k8s_runner.run_k8s_agent"
        ):
            adapter = cfg.build_k8s_investigation_adapter()

        # Then both MCP toolsets are injected
        assert adapter is not None
        assert len(adapter._mcp_toolsets) == 2

    def test_no_mcp_toolsets_when_settings_empty(self) -> None:
        # Given settings with no MCP servers
        test_settings = mock.MagicMock(spec=settings.Settings)
        test_settings.k8s_investigation_backend = "native"
        test_settings.k8s_investigator_llm = "openai/gpt-4.1"
        test_settings.mcp_servers = ""
        test_settings.k8s_mcp_server_url = ""

        cfg = config.Config(settings=test_settings)

        # When building the K8s adapter
        with mock.patch(
            "sentinel.interfaces.graphs.agents.k8s_runner.run_k8s_agent"
        ):
            adapter = cfg.build_k8s_investigation_adapter()

        # Then no MCP toolsets are injected
        assert adapter is not None
        assert len(adapter._mcp_toolsets) == 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/test_config_mcp_wiring.py -v`
Expected: FAIL — `Config` doesn't read `mcp_servers` in `build_k8s_investigation_adapter` yet.

- [x] **Step 3: Implement MCP toolset injection in config.py**

In `src/sentinel/config.py`, replace the `build_k8s_investigation_adapter` method (lines 159-183):

```python
    def build_k8s_investigation_adapter(
        self,
    ) -> investigation.K8sInvestigationAdapter | None:
        """
        Build the K8s investigation adapter based on configuration.

        Returns None when K8s investigation is disabled.
        Injects MCP client toolsets from ``MCP_SERVERS`` and
        ``K8S_MCP_SERVER_URL`` settings when available.

        :returns: A K8sInvestigationAdapter or None.
        """
        backend = self.settings.k8s_investigation_backend
        if not backend:
            return None

        if backend in ("native", "both"):
            from sentinel.domain.sre import k8s_native_agent
            from sentinel.interfaces.graphs.agents import k8s_runner
            from sentinel.plugins.toolsets import mcp as mcp_toolset_mod

            mcp_toolsets = list(
                mcp_toolset_mod.build_mcp_toolsets(config_json=self.settings.mcp_servers)
            )

            if self.settings.k8s_mcp_server_url:
                from pydantic_ai.mcp import MCPServerHTTP

                mcp_toolsets.append(MCPServerHTTP(url=self.settings.k8s_mcp_server_url))

            return k8s_native_agent.NativeK8sAgent(
                k8s_client=None,  # Wire real K8s client when kubernetes lib is integrated
                model_name=_normalise_model_name(self.settings.k8s_investigator_llm),
                mcp_toolsets=tuple(mcp_toolsets),
                agent_runner=k8s_runner.run_k8s_agent,
            )

        return None  # kagent adapter wired separately
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/test_config_mcp_wiring.py -v`
Expected: All 3 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/sentinel/config.py tests/unit/test_config_mcp_wiring.py
git commit -m "feat: wire MCP client toolsets into K8s investigation adapter"
```

---

## Task 2: Investigation MCP Tools — Real Database Operations

**Files:**
- Modify: `src/sentinel/interfaces/mcp/tools/investigation.py`
- Modify: `tests/unit/interfaces/mcp/test_server.py`

- [x] **Step 1: Write the failing tests**

Replace the two existing investigation tool tests in `tests/unit/interfaces/mcp/test_server.py` with tests for the new database-backed implementations:

```python
class TestMcpInvestigationTools:
    @pytest.mark.asyncio
    async def test_trigger_investigation_inserts_job_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When triggering an investigation
        result = await inv_tools.trigger_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="P123",
            description="CPU spike on api-service",
        )

        # Then a job is enqueued and the result contains a job ID
        mock_db.execute.assert_called_once()
        assert "job_id" in result.lower() or "P123" in result

    @pytest.mark.asyncio
    async def test_trigger_investigation_returns_error_when_db_none(self) -> None:
        # Given no database connection
        # When triggering an investigation
        result = await inv_tools.trigger_investigation(
            db=None,
            alert_source="pagerduty",
            alert_id="P123",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_job_status(self) -> None:
        # Given a mock database with a completed job
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = {
            "id": "abc-123",
            "status": "completed",
            "job_type": "SRE_INVESTIGATION",
            "created_at": "2026-04-03T12:00:00+00:00",
        }

        # When checking status
        result = await inv_tools.get_investigation_status(
            db=mock_db,
            investigation_id="abc-123",
        )

        # Then the status is returned
        assert "completed" in result.lower()

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_not_found(self) -> None:
        # Given a mock database with no matching job
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When checking status
        result = await inv_tools.get_investigation_status(
            db=mock_db,
            investigation_id="nonexistent",
        )

        # Then a not-found message is returned
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_fallback_when_db_none(self) -> None:
        # Given no database connection
        # When checking status
        result = await inv_tools.get_investigation_status(
            db=None,
            investigation_id="abc-123",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/interfaces/mcp/test_server.py::TestMcpInvestigationTools -v`
Expected: FAIL — functions don't accept `db` parameter yet.

- [x] **Step 3: Implement real investigation tools**

Replace `src/sentinel/interfaces/mcp/tools/investigation.py` entirely:

```python
"""
MCP server tools for triggering and querying investigations.

Uses the databases library for async PostgreSQL access.
When the database is not configured, returns fallback messages.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import databases

from sentinel.utils import logs


async def trigger_investigation(
    *,
    db: databases.Database | None,
    alert_source: str,
    alert_id: str,
    description: str = "",
) -> str:
    """
    Trigger an SRE investigation by inserting a job request.

    :param db: The async database connection (None when unconfigured).
    :param alert_source: Source of the alert (e.g. "pagerduty", "datadog").
    :param alert_id: Unique identifier for the alert.
    :param description: Optional description of the alert.
    :returns: Confirmation message with the job ID.
    """
    if db is None:
        return "Database not available. Cannot enqueue investigation."

    job_id = uuid.uuid4()
    payload = json.dumps({
        "alert_source": alert_source,
        "alert_id": alert_id,
        "description": description,
    })
    idempotency_key = f"mcp:sre:{alert_source}:{alert_id}"

    try:
        query = """
            INSERT INTO job_requests (
                id, job_type, payload_json, payload_hash, status,
                priority, requested_by, idempotency_key, max_retries
            ) VALUES (
                :id, :job_type, :payload_json, :payload_hash, :status,
                :priority, :requested_by, :idempotency_key, :max_retries
            )
        """
        await db.execute(
            query=query,
            values={
                "id": job_id,
                "job_type": "SRE_INVESTIGATION",
                "payload_json": payload,
                "payload_hash": str(uuid.uuid4())[:16],
                "status": "pending",
                "priority": 1,
                "requested_by": f"mcp:{alert_source}",
                "idempotency_key": idempotency_key,
                "max_retries": 3,
            },
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "trigger_investigation", "alert_id": alert_id})
        return f"Failed to enqueue investigation: {type(exc).__name__}"

    return f"Investigation triggered. job_id={job_id} alert={alert_source}/{alert_id}"


async def get_investigation_status(
    *,
    db: databases.Database | None,
    investigation_id: str,
) -> str:
    """
    Check the status of a job request by ID.

    :param db: The async database connection (None when unconfigured).
    :param investigation_id: The job request UUID.
    :returns: Status message with job details.
    """
    if db is None:
        return "Database not available. Cannot check investigation status."

    try:
        query = """
            SELECT id, status, job_type, created_at
            FROM job_requests
            WHERE id = :id
        """
        row = await db.fetch_one(query=query, values={"id": investigation_id})
    except Exception as exc:
        logs.log_exception(
            exc, params={"tool": "get_investigation_status", "id": investigation_id}
        )
        return f"Status lookup failed: {type(exc).__name__}"

    if row is None:
        return f"Investigation {investigation_id} not found."

    row_dict: dict[str, Any] = dict(row)
    status = row_dict.get("status", "unknown")
    job_type = row_dict.get("job_type", "unknown")
    created = row_dict.get("created_at", "")

    return f"Investigation {investigation_id}: status={status}, type={job_type}, created={created}"
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/interfaces/mcp/test_server.py::TestMcpInvestigationTools -v`
Expected: All 5 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/sentinel/interfaces/mcp/tools/investigation.py tests/unit/interfaces/mcp/test_server.py
git commit -m "feat: wire investigation MCP tools to real database operations"
```

---

## Task 3: MCP Server — Add Database to configure()

**Files:**
- Modify: `src/sentinel/interfaces/mcp/server.py`

- [x] **Step 1: Update configure() and tool wiring**

In `src/sentinel/interfaces/mcp/server.py`, make these changes:

Add import at top (after existing imports):
```python
import databases
```

Add `_db` to the module-level state (after `_doc_searcher_builder`):
```python
_db: databases.Database | None = None
```

Update `configure()` to accept `db`:
```python
def configure(
    *,
    observability_client: obs_base.BaseObservabilityClient | None = None,
    document_searcher_builder: Callable[[], searcher.BaseDocumentSearcher | None] | None = None,
    db: databases.Database | None = None,
) -> None:
    """
    Inject runtime dependencies from a higher layer (config/main).

    Must be called before the MCP server handles any tool requests.
    """
    global _obs_client, _doc_searcher_builder, _db  # noqa: PLW0603
    _obs_client = observability_client
    _doc_searcher_builder = document_searcher_builder
    _db = db
```

Update the `trigger_investigation` tool to pass `db`:
```python
@mcp.tool()
async def trigger_investigation(alert_source: str, alert_id: str, description: str = "") -> str:
    """Trigger an SRE investigation for an alert. Returns a job ID."""
    return await inv_tools.trigger_investigation(
        db=_db,
        alert_source=alert_source,
        alert_id=alert_id,
        description=description,
    )
```

Update the `get_investigation_status` tool to pass `db`:
```python
@mcp.tool()
async def get_investigation_status(investigation_id: str) -> str:
    """Check the status of a running investigation."""
    return await inv_tools.get_investigation_status(db=_db, investigation_id=investigation_id)
```

- [x] **Step 2: Verify the module imports cleanly**

Run: `python -c "from sentinel.interfaces.mcp import server; print('OK')"`
Expected: `OK`

- [x] **Step 3: Run all MCP tests**

Run: `just test tests/unit/interfaces/mcp/ -v`
Expected: All tests PASS.

- [x] **Step 4: Commit**

```bash
git add src/sentinel/interfaces/mcp/server.py
git commit -m "feat: add database injection to MCP server configure()"
```

---

## Task 4: Full Verification

- [x] **Step 1: Run full unit test suite**

Run: `just test`
Expected: All tests PASS.

- [x] **Step 2: Run linter**

Run: `just lint`
Expected: No errors.

- [x] **Step 3: Fix any lint issues**

Run: `just lint-fix` if formatting needed, then re-run `just lint`.

- [x] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve lint issues from MCP wiring"
```

- [x] **Step 5: Update plan checkboxes**

In `docs/plans/k8s-agent-and-mcp-integration.md`, check off Step 18:

```
- [x] Step 18: Wire MCP client into K8s native agent (optional kubectl MCP server)
```

```bash
git add docs/plans/k8s-agent-and-mcp-integration.md
git commit -m "docs: mark Phase C Step 18 complete"
```
