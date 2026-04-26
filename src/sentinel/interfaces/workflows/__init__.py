"""
LangGraph-backed workflow harness.

This package houses the new orchestration layer adopted in PR(N+1) of the
PydanticAI + LangGraph adoption plan. The support pipeline migrates here
first; SRE and chart pipelines will follow in subsequent migration phases

Phase 1 (Foundation) only scaffolds the package; node modules, the graph
builder, the F2 envelope decorator, and the `AsyncPostgresSaver` builder
land in subsequent phases.
"""
