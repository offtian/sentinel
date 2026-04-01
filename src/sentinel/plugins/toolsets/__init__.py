"""
PydanticAI toolset factories for Sentinel agents.

Each factory builds a ``FunctionToolset`` from domain tool functions and
the vendor adapters available at runtime.  Toolsets are injected at
``agent.run(toolsets=[...])`` time — agents themselves remain tool-free
at definition time.

Toolset tiers:

- **Read-only** — investigation agents (RCA, ticket reviewer).  Tools
  can query observability and search backends but cannot mutate state.
- **Approval-gated** — action agents (response drafter, future coding
  agent).  Write-capable tools are wrapped in
  ``ApprovalRequiredToolset`` so human sign-off is required.
- **None** — classifier agents receive no toolsets.
"""
