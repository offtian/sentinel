# Plan: K8s Agent and MCP Integration

**Status:** in-progress
**Created:** 2026-04-02
**Last updated:** 2026-04-02

## Goal

Add Kubernetes-native investigation agents and MCP integration to the Sentinel AI SRE platform, enabling direct K8s cluster investigation without relying solely on HolmesGPT.

## Scope

### In scope
- AuditEntry, InvestigationContext, InvestigationResult domain types
- BaseInvestigationAdapter and K8sInvestigationAdapter abstractions
- Native K8s investigation agent
- Kagent adapter for CRD-based investigation
- MCP integration for tool discovery

### Out of scope
- Changes to the existing HolmesGPT adapter
- UI changes
- New webhook endpoints

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Audit trail type | `@attrs.frozen` AuditEntry with typed envelope + freeform payload | Stable queryable fields, extensible without schema changes |
| Immutable collections | `tuple[...]` over `list[...]` | Full immutability in frozen attrs classes |
| Adapter hierarchy | ABC with `BaseInvestigationAdapter` > `K8sInvestigationAdapter` | Shared contract for all backends, K8s-specific extensions |

## Steps

- [x] Step 1: Create AuditEntry, InvestigationContext, InvestigationResult domain types
- [ ] Step 2: Implement native K8s investigation agent
- [ ] Step 3: Implement kagent adapter
- [ ] Step 4: Add MCP integration

## Changes

| Date | What changed | Why |
|------|-------------|-----|

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
