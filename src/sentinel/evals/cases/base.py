"""
Case and rubric definitions, plus JSON dataset loading.

Each golden dataset JSON file is loaded into ``pydantic_evals.Case``
objects with the appropriate evaluators attached per agent type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import attrs
import pydantic_evals

from sentinel.evals import types
from sentinel.evals.evaluators import (
    chart_evaluators,
    keyword_coverage,
    safety,
    semantic,
    structural,
)


_DATASETS_DIR = Path(__file__).parent.parent / "datasets"

_AGENT_DATASET_FILES: dict[str, str] = {
    "alert_classifier": "alert_classifier_cases.json",
    "root_cause_analyser": "root_cause_cases.json",
    "response_drafter": "response_drafter_cases.json",
    "chart_generator": "chart_generation_cases.json",
    "intent_router": "intent_router_cases.json",
    "ticket_reviewer": "ticket_reviewer_cases.json",
    "k8s_investigator": "k8s_investigator_cases.json",
}

AGENT_NAMES: tuple[str, ...] = tuple(_AGENT_DATASET_FILES.keys())


@attrs.frozen
class Rubric:
    """
    A named evaluation criterion for a case.

    Each rubric maps to one or more evaluator checks.
    """

    name: str
    description: str


def _load_json_dataset(*, file_name: str) -> list[dict[str, Any]]:
    """
    Read and parse a JSON dataset file from the datasets directory.

    :raises FileNotFoundError: if the dataset file does not exist.
    """
    path = _DATASETS_DIR / file_name
    with path.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def _build_alert_classifier_evaluators() -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for alert classifier cases.

    Checks: severity match, category match, non-empty summary, no generic phrases.
    """
    return [
        structural.StructuralCheck(
            field_path="output.severity",
            expected_field_path="expected.severity",
            check_type="exact_match",
            rubric="Severity label matches expected value (case-insensitive)",
        ),
        structural.StructuralCheck(
            field_path="output.category",
            expected_field_path="expected.category",
            check_type="exact_match",
            rubric="Category label matches expected value (case-insensitive)",
        ),
        structural.StructuralCheck(
            field_path="output.summary",
            check_type="non_empty",
            rubric="Summary is non-empty",
        ),
        safety.GenericPhraseCheck(
            field_path="output.summary",
            rubric="Summary does not contain generic fallback text",
        ),
    ]


def _build_root_cause_evaluators(
    *, case: dict[str, Any]
) -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for a root cause analyser case.

    Checks: faithfulness, keyword coverage, remediation, evidence,
    confidence, completeness, hallucination.
    """
    expected = case["expected"]
    return [
        semantic.FaithfulnessCheck(
            source_field_path="input.holmes_analysis",
            output_field_path="output.root_cause",
            rubric="Root cause analysis is faithful to the Holmes investigation data",
        ),
        keyword_coverage.KeywordCoverage(
            field_path="output.root_cause",
            keywords=tuple(expected["root_cause_keywords"]),
            threshold=0.5,
            rubric="Root cause text covers expected keywords",
        ),
        structural.StructuralCheck(
            field_path="output.evidence",
            check_type="has_items",
            rubric="Evidence list is non-empty",
        ),
        semantic.CompletenessCheck(
            output_field_path="output.remediation_steps",
            aspects_field_path="expected.root_cause_keywords",
            rubric="Remediation steps address all identified issues",
        ),
        structural.StructuralCheck(
            field_path="output.confidence",
            expected_field_path="expected.min_confidence",
            check_type="gte",
            rubric="Confidence meets minimum threshold",
        ),
        safety.HallucinationCheck(
            source_field_path="input.holmes_analysis",
            output_field_path="output.root_cause",
            rubric="Root cause does not contain hallucinated claims beyond source data",
        ),
    ]


def _build_response_drafter_evaluators(
    *, case: dict[str, Any]
) -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for a response drafter case.

    Checks: relevance, sources, keywords, tone, completeness, generic phrases.
    """
    expected = case["expected"]
    return [
        semantic.RelevanceCheck(
            input_field_path="input.ticket_description",
            output_field_path="output.response",
            rubric="Response directly addresses the ticket description",
        ),
        structural.StructuralCheck(
            field_path="output.sources_used",
            check_type="has_items",
            rubric="At least one source is cited",
        ),
        keyword_coverage.KeywordCoverage(
            field_path="output.response",
            keywords=tuple(expected["response_keywords"]),
            threshold=0.4,
            rubric="Response text covers expected keywords",
        ),
        safety.ToneCheck(
            field_path="output.response",
            rubric="Response uses professional, empathetic tone",
        ),
        semantic.CompletenessCheck(
            output_field_path="output.response",
            aspects_field_path="expected.response_keywords",
            rubric="Response covers all expected topics",
        ),
        safety.GenericPhraseCheck(
            field_path="output.response",
            rubric="Response does not contain generic fallback text",
        ),
    ]


def _build_chart_generator_evaluators() -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for chart generator cases.

    Checks: required files present, file count meets minimum.
    """
    return [
        chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment", "service"),
            rubric="Output contains Deployment and Service templates",
        ),
        chart_evaluators.SpecCoverageCheck(
            min_files_field="expected.min_files",
            rubric="Generated file count meets case minimum",
        ),
    ]


def _build_intent_router_evaluators() -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for intent router cases.

    Checks: intent classification accuracy, rationale coherence.
    """
    return [
        structural.StructuralCheck(
            field_path="output.intent",
            expected_field_path="expected.intent",
            check_type="exact_match",
            rubric="Intent classification matches expected value",
        ),
        semantic.CoherenceCheck(
            field_path="output.rationale",
            rubric="Rationale is coherent and logically explains the classification",
        ),
    ]


def _build_ticket_reviewer_evaluators() -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for ticket reviewer cases.

    Checks: category accuracy, urgency accuracy, question relevance, search queries.
    """
    return [
        structural.StructuralCheck(
            field_path="output.category",
            expected_field_path="expected.category",
            check_type="exact_match",
            rubric="Category matches expected classification",
        ),
        structural.StructuralCheck(
            field_path="output.urgency",
            expected_field_path="expected.urgency",
            check_type="exact_match",
            rubric="Urgency level matches expected value",
        ),
        semantic.RelevanceCheck(
            input_field_path="input.ticket_description",
            output_field_path="output.key_questions",
            rubric="Key questions are relevant to the ticket content",
        ),
        structural.StructuralCheck(
            field_path="output.search_queries",
            check_type="has_items",
            rubric="Search queries list is non-empty",
        ),
    ]


def _build_k8s_investigator_evaluators() -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for k8s investigator cases.

    Checks: faithfulness, evidence, completeness, confidence, hallucination.
    """
    return [
        semantic.FaithfulnessCheck(
            source_field_path="input.pod_logs",
            output_field_path="output.root_cause",
            rubric="Root cause analysis is faithful to the pod logs and events",
        ),
        structural.StructuralCheck(
            field_path="output.evidence",
            check_type="has_items",
            rubric="Evidence list is non-empty",
        ),
        semantic.CompletenessCheck(
            output_field_path="output.remediation_steps",
            aspects_field_path="expected.root_cause_keywords",
            rubric="Remediation steps address the identified issues",
        ),
        structural.StructuralCheck(
            field_path="output.confidence",
            expected_field_path="expected.min_confidence",
            check_type="gte",
            rubric="Confidence meets minimum threshold",
        ),
        safety.HallucinationCheck(
            source_field_path="input.pod_logs",
            output_field_path="output.root_cause",
            rubric="Analysis does not contain hallucinated claims beyond source data",
        ),
    ]


_EVALUATOR_BUILDERS: dict[str, Any] = {
    "alert_classifier": lambda case: _build_alert_classifier_evaluators(),
    "root_cause_analyser": lambda case: _build_root_cause_evaluators(case=case),
    "response_drafter": lambda case: _build_response_drafter_evaluators(case=case),
    "chart_generator": lambda _case=None: _build_chart_generator_evaluators(),
    "intent_router": lambda case: _build_intent_router_evaluators(),
    "ticket_reviewer": lambda case: _build_ticket_reviewer_evaluators(),
    "k8s_investigator": lambda case: _build_k8s_investigator_evaluators(),
}


def load_cases(
    *,
    agent_name: str,
) -> pydantic_evals.Dataset[types.InputData, str, Any]:
    """
    Load golden cases for the given agent and return a pydantic_evals Dataset.

    Each JSON case is wrapped in a ``pydantic_evals.Case`` with the appropriate
    evaluators attached based on the agent type.

    :param agent_name: One of the agent names in ``_AGENT_DATASET_FILES``.
    :raises ValueError: if agent_name is not recognized.
    :raises FileNotFoundError: if the dataset file does not exist.
    """
    if agent_name not in _AGENT_DATASET_FILES:
        raise ValueError(
            f"Unknown agent name: {agent_name!r}. "
            f"Expected one of: {sorted(_AGENT_DATASET_FILES.keys())}"
        )

    file_name = _AGENT_DATASET_FILES[agent_name]
    raw_cases = _load_json_dataset(file_name=file_name)
    builder = _EVALUATOR_BUILDERS[agent_name]

    eval_cases: list[pydantic_evals.Case[types.InputData, str, Any]] = []
    for raw_case in raw_cases:
        evaluator_instances = builder(raw_case)
        eval_cases.append(
            pydantic_evals.Case(
                name=raw_case["id"],
                inputs=types.InputData(
                    agent_name=agent_name,
                    case_payload=raw_case,
                ),
                expected_output=None,
                evaluators=evaluator_instances,
                metadata={
                    "description": raw_case.get("description", ""),
                },
            )
        )

    return pydantic_evals.Dataset(cases=eval_cases)
