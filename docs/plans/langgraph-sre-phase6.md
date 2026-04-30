# Plan: LangGraph SRE Migration — Phase 6 Integration Tests

**Status:** complete
**Created:** 2026-04-30
**Last updated:** 2026-04-30

## Goal

Phase 6 of the LangGraph SRE migration: integration and parity tests for the new workflow.
See parent plan: [langgraph-sre-migration](langgraph-sre-migration.md)

## Tasks

- [x] T37 Happy-path workflow test (MemorySaver)
- [x] T38 Interrupt + approve integration test
- [x] T39 Reject path integration test
- [x] T40 Checkpoint persistence / crash-recovery test
- [x] T41 Legacy vs workflow parity test
- [x] T42 Span attribute contract tests (NodeSpanAttributes, AgentSpanAttributes, UsageAttributes, ToolSpanAttributes)
- [x] T43 State completeness test (checkpoint fields post happy-path run)
