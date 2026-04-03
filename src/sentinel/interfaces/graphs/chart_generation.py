"""
Pipeline for K8s Helm chart generation.

Flow: ParseRequest -> LoadPolicy -> MergeSpec -> GenerateChart ->
      ValidateChart -> confidence scoring -> CommitToGitOps

The ValidateChart step loops back to GenerateChart on syntax errors
(max retries controlled by settings). Policy violations are reported
in the reply.
"""

from __future__ import annotations

from sentinel.application.charts import commit as chart_commit
from sentinel.domain.charts import confidence as chart_confidence
from sentinel.domain.charts import entities, policies, validation
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.interfaces.graphs.agents import chart_generator, chart_request_parser, utils
from sentinel.utils import logs


logger = logs.get_logger()


async def _parse_request(
    *,
    request: entities.ChartRequest,
    model: str,
) -> entities.ChartSpec:
    """
    Run the chart request parser agent.

    :param request: The raw chart request from the user.
    :param model: LLM model name for request parsing.
    :returns: A structured ChartSpec extracted from the request.
    """
    result = await chart_request_parser.agent.run(
        user_prompt=request.raw_message,
        model=utils.get_model_with_gateway(model),
        deps=chart_request_parser.Dependencies(
            raw_message=request.raw_message,
            requester=request.requester,
            team=request.team,
        ),
    )
    return result.output


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
    model: str,
    error_context: str = "",
) -> tuple[entities.GeneratedFile, ...]:
    """
    Run the chart generator agent.

    :param spec: The merged chart specification.
    :param policy: The team's policy constraints.
    :param model: LLM model name for chart generation.
    :param error_context: Errors from a previous attempt for self-heal.
    :returns: A tuple of generated chart files.
    """
    user_prompt = f"Generate Helm chart for {spec.service_name}"
    if error_context:
        user_prompt += (
            f"\n\nPrevious attempt failed with errors:\n{error_context}\nPlease fix these issues."
        )

    result = await chart_generator.agent.run(
        user_prompt=user_prompt,
        model=utils.get_model_with_gateway(model),
        deps=chart_generator.Dependencies(
            service_name=spec.service_name,
            image=spec.image,
            spec_json=spec.model_dump_json(),
            policy_json=policy.model_dump_json(),
        ),
    )
    return result.output.files


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


async def generate_chart(
    *,
    request: entities.ChartRequest,
    parser_model: str = "",
    generator_model: str = "",
    max_retries: int | None = None,
) -> pipeline_types.ChartGenerationReply:
    """
    Run the full chart generation pipeline.

    :param request: The raw chart request from the user.
    :param parser_model: LLM model for request parsing.
    :param generator_model: LLM model for chart generation.
    :param max_retries: Override for K8S_CHART_MAX_RETRIES.
    :returns: A ChartGenerationReply with results.
    """
    # Callers should provide models via config.build_chart_generation_kwargs().
    # Fall back to config singleton if not provided.
    if not parser_model or not generator_model or max_retries is None:
        from sentinel.config import get_config

        cfg = get_config()
        defaults = cfg.build_chart_generation_kwargs()
        parser_model = parser_model or str(defaults["parser_model"])
        generator_model = generator_model or str(defaults["generator_model"])
        if max_retries is None:
            max_retries = int(defaults["max_retries"])

    # Step 1: Parse request
    try:
        spec = await _parse_request(request=request, model=parser_model)
    except Exception as exc:
        logs.log_exception(exc, params={"node": "ParseRequest"})
        return pipeline_types.ChartGenerationReply(
            service_name="unknown",
            error=f"Failed to parse request: {exc}",
        )

    logs.log_event(
        "chart_request_parsed",
        params={"service_name": spec.service_name, "image": spec.image},
    )

    # Step 2: Load policy
    try:
        policy = await _load_policy(team=request.team)
    except FileNotFoundError as exc:
        logs.log_exception(exc, params={"node": "LoadPolicy", "team": request.team})
        return pipeline_types.ChartGenerationReply(
            service_name=spec.service_name,
            error=f"Policy not found: {exc}",
        )

    # Step 3: Merge spec with policy
    merged_spec, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

    if violations:
        logs.log_event(
            "policy_violations_detected",
            params={
                "service_name": spec.service_name,
                "violation_count": len(violations),
            },
        )

    # Steps 4+5: Generate and validate (with self-heal loop)
    generation_attempts = 0
    error_context = ""
    validation_result: entities.ValidationResult | None = None
    files: tuple[entities.GeneratedFile, ...] = ()

    for attempt in range(max_retries + 1):
        generation_attempts = attempt + 1

        try:
            files = await _generate_chart_files(
                spec=merged_spec,
                policy=policy,
                model=generator_model,
                error_context=error_context,
            )
        except Exception as exc:
            logs.log_exception(
                exc, params={"node": "GenerateChart", "attempt": generation_attempts}
            )
            error_context = str(exc)
            continue

        chart_output = entities.ChartOutput(
            service_name=spec.service_name,
            files=files,
            policy_violations=violations,
            generation_attempts=generation_attempts,
        )

        validation_result = await validation.validate_chart(chart=chart_output)

        if validation_result.helm_template_ok and validation_result.kubeconform_ok:
            break

        # Self-heal: feed errors back to the generator
        error_context = "\n".join(validation_result.errors)
        logs.log_event(
            "chart_validation_failed_retrying",
            params={
                "service_name": spec.service_name,
                "attempt": generation_attempts,
                "errors": validation_result.errors,
            },
        )
    else:
        # Exhausted retries
        return pipeline_types.ChartGenerationReply(
            service_name=spec.service_name,
            files_generated=len(files),
            validation_passed=False,
            policy_violations=len(violations),
            generation_attempts=generation_attempts,
            error=f"Validation failed after {generation_attempts} attempts: {error_context}",
        )

    # Step 6: Confidence scoring
    score = chart_confidence.calculate_chart_confidence(
        schema_valid=validation_result.kubeconform_ok,
        template_renders=validation_result.helm_template_ok,
        template_has_warnings=len(validation_result.warnings) > 0,
        policy_compliant=len(violations) == 0,
        policy_auto_resolved=len(violations) > 0,
        spec_coverage=1.0,
        retry_count=generation_attempts - 1,
    )

    chart_output = chart_output.model_copy(
        update={
            "validation_result": validation_result,
            "confidence_score": score.total,
        }
    )

    # Step 7: Commit to GitOps
    try:
        pr_url = await _commit_chart(chart=chart_output)
    except Exception as exc:
        logs.log_exception(exc, params={"node": "CommitToGitOps"})
        pr_url = f"Commit failed: {exc}"

    logs.log_event(
        "chart_generation_completed",
        params={
            "service_name": spec.service_name,
            "attempts": generation_attempts,
            "confidence": score.total,
            "pr_url": pr_url,
        },
    )

    return pipeline_types.ChartGenerationReply(
        service_name=spec.service_name,
        files_generated=len(chart_output.files),
        validation_passed=True,
        policy_violations=len(violations),
        generation_attempts=generation_attempts,
        confidence=score,
        pr_url=pr_url,
    )
