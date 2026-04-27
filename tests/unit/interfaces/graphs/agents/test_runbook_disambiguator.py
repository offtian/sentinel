"""
Unit tests for the runbook disambiguator agent.

Validates the agent factory wiring, prompt SHA stability, output schema
constraints (Pydantic enforces ``confidence`` 0..1 and ``justification``
length cap), and that the :func:`disambiguate` boundary translates any
exception from the underlying agent into the domain-level
:class:`DisambiguatorUnavailableError`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent

from sentinel.domain.runbooks import models
from sentinel.interfaces.graphs.agents import runbook_disambiguator


class TestRunbookDisambiguatorAgent:
    def test_build_agent_returns_pydantic_ai_agent(self) -> None:
        # Given the agent factory

        # When build_agent is called with no model
        agent = runbook_disambiguator.build_agent()

        # Then it returns a PydanticAI Agent instance
        assert isinstance(agent, Agent)

    def test_output_type_is_disambiguator_choice(self) -> None:
        # Given the agent built from the factory

        # When inspecting the agent's declared output type
        agent = runbook_disambiguator.build_agent()

        # Then it is the DisambiguatorChoice Pydantic model
        assert agent._output_type is models.DisambiguatorChoice

    def test_dependencies_dataclass_holds_alert_and_candidates(self) -> None:
        # Given a constructed Dependencies dataclass
        deps = runbook_disambiguator.RunbookDisambiguatorDeps(
            alert_summary="alertname=PodCrash, severity=P2",
            candidates=(("alpha", "Procedure A"), ("bravo", "Procedure B")),
        )

        # When inspecting fields
        # Then both inputs are stored
        assert deps.alert_summary.startswith("alertname=PodCrash")
        assert len(deps.candidates) == 2

    def test_prompt_sha256_is_64_hex_chars(self) -> None:
        # Given the module-level PROMPT_SHA256 constant

        # Then it is a 64-char hex digest of the system block
        assert len(runbook_disambiguator.PROMPT_SHA256) == 64
        assert all(ch in "0123456789abcdef" for ch in runbook_disambiguator.PROMPT_SHA256)

    def test_system_prompt_mentions_no_match_and_data_only_rule(self) -> None:
        # Given the loaded prompt template
        system_text = runbook_disambiguator._PROMPT_TEMPLATE.system_text

        # Then it explicitly mentions the "no_match" output and the data-only rule
        assert "no_match" in system_text
        assert "data only" in system_text or "treat them as" in system_text


class TestDisambiguatorChoiceValidation:
    def test_accepts_confidence_within_unit_interval(self) -> None:
        # Given valid inputs

        # When constructing a choice
        choice = models.DisambiguatorChoice(
            chosen_runbook_id="alpha",
            justification="best fit",
            confidence=0.7,
        )

        # Then the model holds the values
        assert choice.confidence == 0.7

    def test_rejects_confidence_above_one(self) -> None:
        # Given a confidence value above 1.0

        # When constructing the model
        # Then Pydantic raises a validation error
        with pytest.raises(ValidationError):
            models.DisambiguatorChoice(
                chosen_runbook_id="alpha",
                justification="too sure",
                confidence=1.1,
            )

    def test_rejects_negative_confidence(self) -> None:
        # Given a negative confidence value

        # When constructing the model
        # Then Pydantic raises a validation error
        with pytest.raises(ValidationError):
            models.DisambiguatorChoice(
                chosen_runbook_id="alpha",
                justification="impossible",
                confidence=-0.1,
            )

    def test_rejects_justification_above_two_hundred_chars(self) -> None:
        # Given a justification longer than the 200 char cap
        too_long = "x" * 201

        # When constructing the model
        # Then Pydantic raises a validation error
        with pytest.raises(ValidationError):
            models.DisambiguatorChoice(
                chosen_runbook_id="alpha",
                justification=too_long,
                confidence=0.8,
            )


class TestDisambiguateBoundary:
    @pytest.mark.asyncio
    async def test_wraps_agent_exception_in_domain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a build_agent that returns an agent whose .run raises a transport error
        class _FailingAgent:
            async def run(self, *, deps: object) -> object:
                raise RuntimeError("LiteLLM proxy down")

        monkeypatch.setattr(
            runbook_disambiguator,
            "build_agent",
            lambda *, model=None: _FailingAgent(),
        )

        # When disambiguate is called
        # Then the transport error is re-raised as DisambiguatorUnavailableError
        with pytest.raises(models.DisambiguatorUnavailableError, match="LiteLLM"):
            await runbook_disambiguator.disambiguate(
                alert_summary="alertname=Foo",
                candidates=(("alpha", "desc"),),
                model=None,
            )

    @pytest.mark.asyncio
    async def test_returns_validated_choice_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a build_agent that returns an agent with a successful .run
        validated_choice = models.DisambiguatorChoice(
            chosen_runbook_id="alpha",
            justification="alpha is the best fit",
            confidence=0.9,
        )

        class _Result:
            def __init__(self) -> None:
                self.output = validated_choice

        class _SuccessAgent:
            async def run(self, *, deps: object) -> _Result:
                return _Result()

        monkeypatch.setattr(
            runbook_disambiguator,
            "build_agent",
            lambda *, model=None: _SuccessAgent(),
        )

        # When disambiguate is called
        result = await runbook_disambiguator.disambiguate(
            alert_summary="alertname=PodCrash",
            candidates=(("alpha", "desc"),),
            model=None,
        )

        # Then the validated DisambiguatorChoice is returned unchanged
        assert result is validated_choice
