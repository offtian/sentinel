"""
Pipeline for K8s Helm chart generation.

Flow: ParseRequest -> LoadPolicy -> MergeSpec -> GenerateChart ->
      ValidateChart -> confidence scoring -> CommitToGitOps

The ValidateChart step loops back to GenerateChart on syntax errors
(max retries controlled by settings). Policy violations are reported
in the reply.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from typing import Any

from pydantic_ai.toolsets import AbstractToolset

from sentinel.application.charts import commit as chart_commit
from sentinel.domain.charts import confidence as chart_confidence
from sentinel.domain.charts import entities, policies, validation
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.interfaces.graphs.agents import chart_generator, chart_request_parser
from sentinel.utils import logs


logger = logs.get_logger()


def _get_agent_model_name(agent: Any) -> str:
    """
    Extract the model name from a PydanticAI agent for audit metadata.

    :param agent: A PydanticAI agent instance.
    :returns: The model name string, or empty string if not accessible.
    """
    try:
        name = agent.model.model_name
        return name if isinstance(name, str) else ""
    except AttributeError:
        return ""


async def _parse_request(
    *,
    request: entities.ChartRequest,
    agent_for: Callable[[str], Any],
) -> entities.ChartSpec:
    """
    Run the chart request parser agent.

    :param request: The raw chart request from the user.
    :param agent_for: Callable that returns a pre-built agent by name.
    :returns: A structured ChartSpec extracted from the request.
    """
    parser_agent = agent_for("chart_request_parser")
    result = await parser_agent.run(
        user_prompt=request.raw_message,
        deps=chart_request_parser.Dependencies(
            raw_message=request.raw_message,
            requester=request.requester,
            team=request.team,
        ),
    )
    return result.output  # type: ignore[no-any-return]


async def _load_policy(*, team: str) -> entities.TeamPolicy:
    """
    Load team policy from YAML.

    :param team: Team name to load policy for.
    :returns: The parsed TeamPolicy.
    :raises FileNotFoundError: if no policy file exists for the team.
    """
    return policies.load_team_policy(team=team)


async def _generate_chart_files(
    *,
    spec: entities.ChartSpec,
    policy: entities.TeamPolicy,
    agent_for: Callable[[str], Any],
    error_context: str = "",
    chart_generator_toolsets: Sequence[AbstractToolset[object]] = (),
) -> tuple[entities.GeneratedFile, ...]:
    """
    Run the chart generator agent.

    :param spec: The merged chart specification.
    :param policy: The team's policy constraints.
    :param agent_for: Callable that returns a pre-built agent by name.
    :param error_context: Errors from a previous attempt for self-heal.
    :param chart_generator_toolsets: Toolsets injected at agent.run() time.
    :returns: A tuple of generated chart files.
    """
    user_prompt = f"Generate Helm chart for {spec.service_name}"
    if error_context:
        user_prompt += (
            f"\n\nYour PREVIOUS attempt FAILED validation with these errors:\n{error_context}\n\n"
            "IMPORTANT: Do NOT use Helm template functions like {{ include ... }}, {{ .Values.* }}, "
            "{{ .Release.Name }}, or any {{ ... }} expressions. Output PLAIN YAML with literal values only. "
            "Every file must be self-contained and pass helm template + kubeconform."
        )

    generator_agent = agent_for("chart_generator")
    result = await generator_agent.run(
        user_prompt=user_prompt,
        deps=chart_generator.Dependencies(
            service_name=spec.service_name,
            image=spec.image,
            spec_json=spec.model_dump_json(),
            policy_json=policy.model_dump_json(),
        ),
        toolsets=list(chart_generator_toolsets) or None,
    )
    return result.output.files  # type: ignore[no-any-return]


async def _commit_chart(
    *,
    chart: entities.ChartOutput,
) -> str:
    """
    Commit chart to GitOps directory and open a PR.

    :param chart: The validated chart output.
    :returns: The pull request URL or error message.
    """
    return await chart_commit.commit_to_gitops(chart=chart)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _make_reply(
    *,
    service_name: str,
    pipeline_start: float,
    timings: list[pipeline_types.ChartStepTiming],
    parser_model: str,
    generator_model: str,
    **kwargs: object,
) -> pipeline_types.ChartGenerationReply:
    """Build a reply with timing fields populated."""
    return pipeline_types.ChartGenerationReply(
        service_name=service_name,
        total_duration_ms=_elapsed_ms(pipeline_start),
        step_timings=tuple(timings),
        parser_model=parser_model,
        generator_model=generator_model,
        **kwargs,  # type: ignore[arg-type]
    )


async def _parse_and_load_policy(
    *,
    request: entities.ChartRequest,
    agent_for: Callable[[str], Any],
    timings: list[pipeline_types.ChartStepTiming],
) -> pipeline_types.ChartGenerationReply | tuple[entities.ChartSpec, entities.TeamPolicy]:
    """
    Run request parsing and policy loading concurrently.

    :param request: The raw chart request from the user.
    :param agent_for: Callable that returns a pre-built agent by name.
    :param timings: Mutable list to append step timings to.
    :returns: A (spec, policy) tuple on success, or an early-exit reply on failure.
    """
    step_start = time.monotonic()
    parse_result, policy_result = await asyncio.gather(
        _parse_request(request=request, agent_for=agent_for),
        _load_policy(team=request.team),
        return_exceptions=True,
    )
    timings.append(
        pipeline_types.ChartStepTiming(
            step="ParseRequest+LoadPolicy",
            duration_ms=_elapsed_ms(step_start),
        )
    )

    if isinstance(parse_result, BaseException):
        if isinstance(parse_result, Exception):
            logs.log_exception(parse_result, params={"node": "ParseRequest"})
            return pipeline_types.ChartGenerationReply(
                service_name="unknown",
                error=f"Failed to parse request: {parse_result}",
            )
        raise parse_result

    spec: entities.ChartSpec = parse_result

    logs.log_event(
        "chart_request_parsed",
        params={"service_name": spec.service_name, "image": spec.image},
    )

    if isinstance(policy_result, FileNotFoundError):
        logs.log_exception(policy_result, params={"node": "LoadPolicy", "team": request.team})
        return pipeline_types.ChartGenerationReply(
            service_name=spec.service_name,
            error=f"Policy not found: {policy_result}",
        )
    if isinstance(policy_result, BaseException):
        raise policy_result

    policy: entities.TeamPolicy = policy_result
    return spec, policy


async def _generate_and_validate_loop(
    *,
    spec: entities.ChartSpec,
    policy: entities.TeamPolicy,
    violations: tuple[entities.PolicyViolation, ...],
    agent_for: Callable[[str], Any],
    max_retries: int,
    timings: list[pipeline_types.ChartStepTiming],
    chart_generator_toolsets: Sequence[AbstractToolset[object]] = (),
) -> tuple[entities.ChartOutput | None, entities.ValidationResult | None, int, str]:
    """
    Run the generate -> validate -> self-heal loop.

    :returns: (chart_output or None, validation_result or None, attempts, last_error)
    """
    error_context = ""
    files: tuple[entities.GeneratedFile, ...] = ()

    for attempt in range(max_retries + 1):
        generation_attempts = attempt + 1

        gen_start = time.monotonic()
        try:
            files = await _generate_chart_files(
                spec=spec,
                policy=policy,
                agent_for=agent_for,
                error_context=error_context,
                chart_generator_toolsets=chart_generator_toolsets,
            )
        except Exception as exc:
            logs.log_exception(
                exc, params={"node": "GenerateChart", "attempt": generation_attempts}
            )
            timings.append(
                pipeline_types.ChartStepTiming(
                    step=f"GenerateChart (attempt {generation_attempts})",
                    duration_ms=_elapsed_ms(gen_start),
                )
            )
            error_context = str(exc)
            continue
        timings.append(
            pipeline_types.ChartStepTiming(
                step=f"GenerateChart (attempt {generation_attempts})",
                duration_ms=_elapsed_ms(gen_start),
            )
        )

        chart_output = entities.ChartOutput(
            service_name=spec.service_name,
            files=files,
            policy_violations=violations,
            generation_attempts=generation_attempts,
        )

        val_start = time.monotonic()
        validation_result = await validation.validate_chart(chart=chart_output)
        timings.append(
            pipeline_types.ChartStepTiming(
                step=f"ValidateChart (attempt {generation_attempts})",
                duration_ms=_elapsed_ms(val_start),
            )
        )

        if validation_result.helm_template_ok and validation_result.kubeconform_ok:
            return chart_output, validation_result, generation_attempts, ""

        error_context = "\n".join(validation_result.errors)
        logs.log_event(
            "chart_validation_failed_retrying",
            params={
                "service_name": spec.service_name,
                "attempt": generation_attempts,
                "errors": validation_result.errors,
            },
        )

    return None, None, max_retries + 1, error_context


async def generate_chart(
    *,
    request: entities.ChartRequest,
    agent_for: Callable[[str], Any],
    max_retries: int = 2,
    chart_generator_toolsets: Sequence[AbstractToolset[object]] = (),
) -> pipeline_types.ChartGenerationReply:
    """
    Run the full chart generation pipeline.

    :param request: The raw chart request from the user.
    :param agent_for: Callable that returns a pre-built agent by name.
    :param max_retries: Maximum generation retry attempts on validation failure.
    :returns: A ChartGenerationReply with results.
    """
    pipeline_start = time.monotonic()
    timings: list[pipeline_types.ChartStepTiming] = []

    # Steps 1+2: Parse request and load policy concurrently
    result = await _parse_and_load_policy(
        request=request,
        agent_for=agent_for,
        timings=timings,
    )
    if isinstance(result, pipeline_types.ChartGenerationReply):
        return result

    spec, policy = result

    parser_model = _get_agent_model_name(agent_for("chart_request_parser"))
    generator_model = _get_agent_model_name(agent_for("chart_generator"))

    # Step 3: Merge spec with policy
    step_start = time.monotonic()
    merged_spec, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)
    timings.append(
        pipeline_types.ChartStepTiming(step="MergeSpec", duration_ms=_elapsed_ms(step_start))
    )

    # Steps 4+5: Generate and validate (with self-heal loop)
    (
        chart_output,
        validation_result,
        generation_attempts,
        last_error,
    ) = await _generate_and_validate_loop(
        spec=merged_spec,
        policy=policy,
        violations=violations,
        agent_for=agent_for,
        max_retries=max_retries,
        timings=timings,
        chart_generator_toolsets=chart_generator_toolsets,
    )

    if chart_output is None or validation_result is None:
        return _make_reply(
            service_name=spec.service_name,
            pipeline_start=pipeline_start,
            timings=timings,
            parser_model=parser_model,
            generator_model=generator_model,
            validation_passed=False,
            policy_violations=len(violations),
            generation_attempts=generation_attempts,
            error=f"Validation failed after {generation_attempts} attempts: {last_error}",
        )

    # Step 6: Confidence scoring
    step_start = time.monotonic()
    score = chart_confidence.calculate_chart_confidence(
        schema_valid=validation_result.kubeconform_ok,
        template_renders=validation_result.helm_template_ok,
        template_has_warnings=len(validation_result.warnings) > 0,
        policy_compliant=len(violations) == 0,
        policy_auto_resolved=len(violations) > 0,
        spec_coverage=1.0,
        retry_count=generation_attempts - 1,
    )
    timings.append(
        pipeline_types.ChartStepTiming(
            step="ConfidenceScoring", duration_ms=_elapsed_ms(step_start)
        )
    )

    chart_output = chart_output.model_copy(
        update={"validation_result": validation_result, "confidence_score": score.total}
    )

    # Step 7: Commit to GitOps
    step_start = time.monotonic()
    pr_url = ""
    commit_error: str | None = None
    try:
        pr_url = await _commit_chart(chart=chart_output)
    except Exception as exc:
        logs.log_exception(exc, params={"node": "CommitToGitOps"})
        commit_error = f"Commit failed: {exc}"
    timings.append(
        pipeline_types.ChartStepTiming(step="CommitToGitOps", duration_ms=_elapsed_ms(step_start))
    )

    return _make_reply(
        service_name=spec.service_name,
        pipeline_start=pipeline_start,
        timings=timings,
        parser_model=parser_model,
        generator_model=generator_model,
        files_generated=len(chart_output.files),
        validation_passed=True,
        policy_violations=len(violations),
        generation_attempts=generation_attempts,
        confidence=score,
        pr_url=pr_url,
        error=commit_error,
    )
