# MCP Wiring — Phase C Step 18

## Goal

Wire the MCP integration end-to-end: inject MCP client toolsets into the K8s native agent via config.py, and replace the stub investigation MCP tools with real job queue operations.

## Components

### 1. MCP client toolsets into K8s agent (config.py)

`Config.build_k8s_investigation_adapter()` currently creates `NativeK8sAgent` with `mcp_toolsets=()`. Wire it to call `build_mcp_toolsets(config_json=settings.mcp_servers)` and pass the result. When `K8S_MCP_SERVER_URL` is also set, add it as an additional HTTP server.

**File:** `src/sentinel/config.py`

Changes:
- Import `plugins.toolsets.mcp` 
- Build MCP toolsets from `settings.mcp_servers` + optional `settings.k8s_mcp_server_url`
- Pass as `mcp_toolsets` to `NativeK8sAgent`

### 2. Investigation MCP tools — wire to job queue

Replace the two stubs in `interfaces/mcp/tools/investigation.py` with real implementations:

**`trigger_investigation`** — creates a job request in the database via `databases` and returns the job ID. Needs a `databases.Database` instance injected via the existing `configure()` pattern on the MCP server.

**`get_investigation_status`** — queries `job_requests` table by ID and returns status + result summary.

**Files:**
- Modify: `src/sentinel/interfaces/mcp/tools/investigation.py` — accept `db` parameter, run real queries
- Modify: `src/sentinel/interfaces/mcp/server.py` — add `db` to module state, pass to investigation tools

### 3. MCP server configure() — add database

Extend `configure()` to accept an optional `databases.Database` instance. Pass it through to `trigger_investigation` and `get_investigation_status`.

## Not changing

- MCP server structure, observability tools, documentation tools (already wired)
- MCP client builder `plugins/toolsets/mcp.py` (already works)
- K8s agent runner (already accepts mcp_toolsets)
- Settings (MCP_SERVERS, K8S_MCP_SERVER_URL already exist)

## File summary

| Action | File |
|--------|------|
| Modify | `src/sentinel/config.py` — build and inject MCP toolsets |
| Modify | `src/sentinel/interfaces/mcp/tools/investigation.py` — real job queue operations |
| Modify | `src/sentinel/interfaces/mcp/server.py` — add `db` to configure() and pass through |
| Create | `tests/unit/interfaces/mcp/test_investigation_tools.py` — tests for investigation tools |
| Create | `tests/unit/test_config_mcp_wiring.py` — test MCP toolset injection |
