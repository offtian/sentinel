"""
PydanticAI agent definitions for Sentinel pipelines.

Each sub-module owns one agent: its Dependencies dataclass, output model,
base Jinja system prompt, module-level helper functions (instructions,
dynamic system-prompt injection), and a ``build_agent(*, model, skills)``
factory. ``sentinel.config.BaseConfiguration.load_agents`` calls every
factory at startup and the resulting instances are retrieved via
``BaseConfiguration.agent_for(name)``.

Sub-modules are re-exported here so callers can do::

    from sentinel.interfaces.graphs import agents

    agents.alert_classifier.build_agent(model=..., skills=(...,))
"""

from __future__ import annotations

from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    chart_generator,
    chart_request_parser,
    intent_router,
    k8s_investigator,
    k8s_runner,
    response_drafter,
    root_cause_analyser,
    ticket_reviewer,
    utils,
)


__all__ = (
    "alert_classifier",
    "chart_generator",
    "chart_request_parser",
    "intent_router",
    "k8s_investigator",
    "k8s_runner",
    "response_drafter",
    "root_cause_analyser",
    "ticket_reviewer",
    "utils",
)
