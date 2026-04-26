"""
Unit tests for ``CommonConfiguration.load_agents`` and ``BaseConfiguration.agent_for``.

Covers:
- Every pipeline agent gets built with its configured model and skills.
- Per-agent skills are forwarded from ``SKILLS_BY_AGENT`` to the factory.
- Unknown skill names propagate ``SkillNotFoundError`` loudly at load time.
- ``agent_for`` returns cached instances; ``KeyError`` on unknown names.

Individual ``build_agent`` factories are patched so the tests never
construct real PydanticAI ``Agent`` instances (and therefore never try
to hit OpenAI / Anthropic client constructors).
"""

from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from sentinel import config as config_mod
from sentinel.domain import skills as skills_mod
from sentinel.interfaces.graphs import agents
from sentinel.plugins.common import config as plugin_config_mod
from sentinel.settings import settings


_ALL_AGENT_MODULES = (
    agents.alert_classifier,
    agents.root_cause_analyser,
    agents.ticket_reviewer,
    agents.response_drafter,
    agents.chart_generator,
    agents.chart_request_parser,
    agents.intent_router,
    agents.k8s_investigator,
)


@pytest.fixture(autouse=True)
def _reset_module_singleton() -> None:
    """Clear the module-level ``_config`` singleton between tests."""
    config_mod._config = None
    yield
    config_mod._config = None


@pytest.fixture
def stub_factories() -> dict[str, mock.MagicMock]:
    """
    Patch every agent module's ``build_agent`` with a MagicMock sentinel.

    Each sentinel returns a uniquely-identifiable object so tests can
    assert which factory was called and with what arguments.
    """
    with contextlib.ExitStack() as stack:
        stubs: dict[str, mock.MagicMock] = {}
        for module in _ALL_AGENT_MODULES:
            name = module.__name__.rsplit(".", 1)[-1]
            stub = mock.MagicMock(return_value=mock.Mock(name=f"agent_{name}"))
            stack.enter_context(mock.patch.object(module, "build_agent", stub))
            stubs[name] = stub
        yield stubs


def _make_config() -> plugin_config_mod.CommonConfiguration:
    return plugin_config_mod.CommonConfiguration(settings=settings)


class TestLoadAgents:
    def test_populates_every_expected_agent(
        self, stub_factories: dict[str, mock.MagicMock]
    ) -> None:
        # Given a fresh CommonConfiguration and patched agent factories
        cfg = _make_config()

        # When load_agents is called with an empty skill mapping
        with mock.patch.object(config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents)

        # Then every pipeline agent name is available via agent_for
        expected = {
            "alert_classifier",
            "root_cause_analyser",
            "ticket_reviewer",
            "response_drafter",
            "chart_generator",
            "chart_request_parser",
            "intent_router",
            "k8s_investigator",
        }
        for name in expected:
            assert cfg.agent_for(name) is not None

    def test_every_factory_called_exactly_once(
        self, stub_factories: dict[str, mock.MagicMock]
    ) -> None:
        # Given a fresh CommonConfiguration
        cfg = _make_config()

        # When load_agents is called
        with mock.patch.object(config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents)

        # Then every agent module's build_agent was called exactly once
        for stub in stub_factories.values():
            assert stub.call_count == 1

    def test_factories_receive_normalised_model_identifiers(
        self, stub_factories: dict[str, mock.MagicMock]
    ) -> None:
        # Given a fresh CommonConfiguration
        cfg = _make_config()

        # When load_agents is called
        with mock.patch.object(config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents)

        # Then each factory received a model kwarg shaped "provider:name"
        for stub in stub_factories.values():
            kwargs = stub.call_args.kwargs
            assert "model" in kwargs
            assert ":" in kwargs["model"]

    def test_forwards_configured_skills_to_factories(
        self, stub_factories: dict[str, mock.MagicMock]
    ) -> None:
        # Given a skill mapping that assigns specific runbooks per agent
        cfg = _make_config()
        skill_map = {
            "alert_classifier": ("skill-a",),
            "root_cause_analyser": ("skill-a", "skill-b"),
        }

        # When load_agents is called
        with mock.patch.object(config_mod, "SKILLS_BY_AGENT", skill_map):
            cfg.load_agents(agent_module=agents)

        # Then the matching factories got the configured skill tuples
        assert stub_factories["alert_classifier"].call_args.kwargs["skills"] == ("skill-a",)
        assert stub_factories["root_cause_analyser"].call_args.kwargs["skills"] == (
            "skill-a",
            "skill-b",
        )
        # And an unmapped agent receives the empty default
        assert stub_factories["ticket_reviewer"].call_args.kwargs["skills"] == ()

    def test_agent_for_returns_cached_instance(
        self, stub_factories: dict[str, mock.MagicMock]
    ) -> None:
        # Given a configuration with agents loaded
        cfg = _make_config()
        with mock.patch.object(config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents)

        # When agent_for is called twice for the same name
        first = cfg.agent_for("alert_classifier")
        second = cfg.agent_for("alert_classifier")

        # Then the same object identity is returned
        assert first is second

    def test_agent_for_raises_on_unknown_name(
        self, stub_factories: dict[str, mock.MagicMock]
    ) -> None:
        # Given a configuration with agents loaded
        cfg = _make_config()
        with mock.patch.object(config_mod, "SKILLS_BY_AGENT", {}):
            cfg.load_agents(agent_module=agents)

        # When agent_for is called with an unknown name
        # Then KeyError is raised and names the missing agent
        with pytest.raises(KeyError, match="nonexistent"):
            cfg.agent_for("nonexistent")

    def test_agent_for_raises_before_load_agents(self) -> None:
        # Given a fresh Configuration with load_agents NOT called
        cfg = _make_config()

        # When agent_for is called
        # Then KeyError is raised hinting at load_agents()
        with pytest.raises(KeyError, match="load_agents"):
            cfg.agent_for("alert_classifier")

    def test_unknown_skill_name_raises_loudly_end_to_end(self) -> None:
        # Given a CommonConfiguration and a typoed skill name — no factory stubs
        # because we want the real compose_system_prompt to run and raise
        cfg = _make_config()
        skill_map = {"alert_classifier": ("nonexistent-typoed-skill",)}

        # When load_agents is called
        # Then SkillNotFoundError propagates and names the typo
        with (
            mock.patch.object(config_mod, "SKILLS_BY_AGENT", skill_map),
            pytest.raises(skills_mod.SkillNotFoundError, match="nonexistent-typoed-skill"),
        ):
            cfg.load_agents(agent_module=agents)
