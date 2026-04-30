"""Unit tests for the F1 config layering refactor.

Exercises the layered fields added to ``BaseConfiguration`` plus the
new env-var fields on ``Settings``, alongside the policy primitives in
``data/policies.py``.
"""

from __future__ import annotations

from pathlib import Path

import attrs
import pytest

from sentinel import config as config_mod
from sentinel import settings as settings_mod
from sentinel.data.primitives import policies


class TestApprovalPolicy:
    """Tests for the frozen ``ApprovalPolicy`` primitive (RFC §15.9)."""

    def test_initialises_with_kw_only_defaults(self) -> None:
        # Given no overrides
        # When the policy is constructed with all defaults
        policy = policies.ApprovalPolicy()

        # Then the defaults match the RFC §15.9 specification
        assert policy.require_human_below_label == "HIGH"
        assert policy.approver_role == "oncall"
        assert policy.approval_timeout_seconds == 900
        assert policy.auto_approve_after_n_clean_runs is None
        assert policy.require_human_first_send_of_template is False

    def test_empty_returns_compliance_holding_policy(self) -> None:
        # Given the placeholder factory
        # When ApprovalPolicy.empty() is called
        placeholder = policies.ApprovalPolicy.empty()

        # Then the placeholder routes to compliance and never auto-approves
        assert placeholder.approver_role == "compliance"
        assert placeholder.approval_timeout_seconds == 0
        assert placeholder.require_human_first_send_of_template is True

    def test_is_immutable_when_frozen(self) -> None:
        # Given a constructed policy
        policy = policies.ApprovalPolicy()

        # When a field is reassigned
        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            policy.approval_timeout_seconds = 1234  # type: ignore[misc]

    def test_uses_kw_only_construction(self) -> None:
        # Given a positional argument
        # When constructed positionally
        # Then attrs raises TypeError because kw_only=True
        with pytest.raises(TypeError):
            policies.ApprovalPolicy("HIGH")  # type: ignore[misc]


class TestOutputChannel:
    """Tests for the frozen ``OutputChannel`` primitive (RFC §15.9)."""

    def test_constructs_with_required_kw_only_args(self) -> None:
        # Given the three required kwargs
        # When the channel is constructed
        channel = policies.OutputChannel(
            kind="slack_channel",
            target="#sre-oncall",
            min_confidence_label="MEDIUM",
        )

        # Then the fields round-trip
        assert channel.kind == "slack_channel"
        assert channel.target == "#sre-oncall"
        assert channel.min_confidence_label == "MEDIUM"

    def test_is_immutable_when_frozen(self) -> None:
        # Given a constructed channel
        channel = policies.OutputChannel(
            kind="pagerduty_note",
            target="P1",
            min_confidence_label="HIGH",
        )

        # When a field is reassigned
        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            channel.target = "elsewhere"  # type: ignore[misc]

    def test_requires_kind_target_and_label_kwargs(self) -> None:
        # Given missing required kwargs
        # When the channel is constructed without them
        # Then attrs raises TypeError
        with pytest.raises(TypeError):
            policies.OutputChannel()  # type: ignore[call-arg]


class TestRedactionPolicy:
    """Tests for the frozen ``RedactionPolicy`` primitive (RFC §15.9)."""

    def test_initialises_with_empty_deny_patterns_default(self) -> None:
        # Given no overrides
        # When the policy is constructed with all defaults
        policy = policies.RedactionPolicy()

        # Then deny_patterns is an empty tuple and judge_score_min is 0.9
        assert policy.deny_patterns == ()
        assert policy.judge_score_min == 0.9

    def test_default_returns_firm_wide_policy_with_secret_patterns(self) -> None:
        # Given the firm-wide default factory
        # When RedactionPolicy.default() is called
        firm_policy = policies.RedactionPolicy.default()

        # Then the policy carries the secret-detection pattern and a
        # tenant-scoping cross-PM pattern
        assert any("api[_-]?key" in pattern for pattern in firm_policy.deny_patterns)
        assert any("pm-" in pattern for pattern in firm_policy.deny_patterns)
        assert firm_policy.judge_score_min == 0.9

    def test_empty_returns_reject_everything_score(self) -> None:
        # Given the placeholder factory
        # When RedactionPolicy.empty() is called
        placeholder = policies.RedactionPolicy.empty()

        # Then deny_patterns is empty but judge_score_min is 1.0 — meaning
        # any judge call rejects every output (placeholder safety net)
        assert placeholder.deny_patterns == ()
        assert placeholder.judge_score_min == 1.0

    def test_is_immutable_when_frozen(self) -> None:
        # Given a constructed policy
        policy = policies.RedactionPolicy()

        # When a field is reassigned
        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            policy.judge_score_min = 0.5  # type: ignore[misc]


class TestBaseConfigurationLayeredFields:
    """Tests for the F1 layered fields added to ``BaseConfiguration``."""

    def test_constructs_with_firm_wide_pipeline_defaults(self) -> None:
        # Given default Settings
        # When BaseConfiguration is constructed
        config = config_mod.BaseConfiguration(settings=settings_mod.Settings())

        # Then the firm-wide defaults are populated
        assert config.investigation_loop_cap == 8
        assert config.investigation_timeout_seconds == 300
        assert config.confidence_publish_min == 0.7
        assert config.enable_replay_bundle is True

    def test_redaction_policy_default_is_firm_wide_with_secret_patterns(self) -> None:
        # Given default Settings
        # When BaseConfiguration is constructed
        config = config_mod.BaseConfiguration(settings=settings_mod.Settings())

        # Then redaction_policy carries the firm-wide deny patterns
        assert any("api[_-]?key" in p for p in config.redaction_policy.deny_patterns)
        assert config.redaction_policy.judge_score_min == 0.9

    def test_approval_policy_default_is_compliance_holding_placeholder(self) -> None:
        # Given default Settings
        # When BaseConfiguration is constructed
        config = config_mod.BaseConfiguration(settings=settings_mod.Settings())

        # Then approval_policy routes to compliance and never auto-approves
        # (filled in by team configs once approval gates wire up)
        assert config.approval_policy.approver_role == "compliance"
        assert config.approval_policy.approval_timeout_seconds == 0

    def test_team_id_reads_from_settings(self) -> None:
        # Given Settings with the default team_profile
        # When BaseConfiguration is constructed
        config = config_mod.BaseConfiguration(settings=settings_mod.Settings())

        # Then team_id mirrors settings.team_profile
        assert config.team_id == "sre"
        assert config.team_id == config.settings.team_profile

    def test_collection_defaults_are_empty(self) -> None:
        # Given default Settings
        # When BaseConfiguration is constructed
        config = config_mod.BaseConfiguration(settings=settings_mod.Settings())

        # Then every collection placeholder is empty
        assert config.allowed_tools == frozenset()
        assert config.allowed_skills == frozenset()
        assert config.output_channels == ()
        assert config.runbooks_paths == ()
        assert config.skills_paths == ()
        assert config.tool_modules == ()

    def test_envelope_strict_mode_default_is_false(self) -> None:
        # Given default Settings
        # When BaseConfiguration is constructed
        config = config_mod.BaseConfiguration(settings=settings_mod.Settings())

        # Then envelope_strict_mode defaults to False (soft-fail)
        # so dev/foundation deployments warn-and-continue while
        # production deployments can flip the flag for R-IN-3.
        assert config.envelope_strict_mode is False

    def test_envelope_strict_mode_can_be_overridden_at_construction(self) -> None:
        # Given a caller that wants strict ingress validation
        # When BaseConfiguration is constructed with envelope_strict_mode=True
        config = config_mod.BaseConfiguration(
            settings=settings_mod.Settings(),
            envelope_strict_mode=True,
        )

        # Then the override is honoured
        assert config.envelope_strict_mode is True

    def test_caller_can_override_field_at_construction(self) -> None:
        # Given a custom runbooks-path override
        custom_paths = (Path("custom-runbooks"),)

        # When BaseConfiguration is constructed with the override
        config = config_mod.BaseConfiguration(
            settings=settings_mod.Settings(),
            runbooks_paths=custom_paths,
        )

        # Then the override applies and other defaults are unchanged
        assert config.runbooks_paths == custom_paths
        assert config.skills_paths == ()
        assert config.investigation_loop_cap == 8


class TestSettingsFoundationsFields:
    """Tests for the new RFC §15.3 fields added to ``Settings`` in F1.2."""

    def test_team_profile_defaults_to_sre(self) -> None:
        # Given no override
        # When Settings is constructed
        settings = settings_mod.Settings()

        # Then team_profile defaults to "sre" — the only profile F1
        # actually wires up; "devops"/"ace" raise NotImplementedError
        # in get_config()
        assert settings.team_profile == "sre"

    def test_litellm_proxy_fields_default_to_none(self) -> None:
        # Given no LiteLLM proxy env
        # When Settings is constructed
        settings = settings_mod.Settings()

        # Then proxy fields are None so the local-dev fallback path
        # (in-process LiteLLM SDK) keeps working without env config
        assert settings.litellm_base_url is None
        assert settings.litellm_virtual_key is None

    def test_langfuse_fields_default_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given no Langfuse env (local .env may carry dev keys for
        # docker-compose; explicitly delete before constructing Settings so
        # the test asserts the in-code defaults rather than dev-stack state)
        for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            monkeypatch.delenv(key, raising=False)

        # When Settings is constructed without an .env file (defaults only)
        settings = settings_mod.Settings(_env_file=None)

        # Then every Langfuse field is None — the OTel exporter falls
        # back to console output when the host is unset
        assert settings.langfuse_host is None
        assert settings.langfuse_public_key is None
        assert settings.langfuse_secret_key is None

    def test_otel_collector_endpoint_defaults_to_none(self) -> None:
        # Given no OTel collector env
        # When Settings is constructed
        settings = settings_mod.Settings()

        # Then otel_collector_endpoint is None — separate from the
        # existing otel_traces_endpoint which targets a single signal
        assert settings.otel_collector_endpoint is None

    def test_runbooks_root_defaults_to_packaged_path(self) -> None:
        # Given no override
        # When Settings is constructed
        settings = settings_mod.Settings()

        # Then runbooks_root points at the in-repo runbooks location
        # (loader resolves team-specific subdirectories from this root)
        assert isinstance(settings.runbooks_root, Path)
        assert settings.runbooks_root.parts[-1] == "runbooks"

    def test_langgraph_sre_enabled_defaults_to_false(self) -> None:
        # Given no override
        # When Settings is constructed
        settings = settings_mod.Settings()

        # Then langgraph_sre_enabled defaults to False — the W2 feature
        # flag that routes SRE investigations to the LangGraph workflow is
        # off by default so the Pydantic Graph pipeline remains live until
        # an operator opts in
        assert settings.langgraph_sre_enabled is False


class TestLangGraphSREFeatureFlag:
    """Tests for the W2 LangGraph SRE feature flag surfaced on BaseConfiguration."""

    def test_langgraph_sre_enabled_returns_false_when_settings_is_false(self) -> None:
        # Given Settings with langgraph_sre_enabled=False (default)
        s = settings_mod.Settings()

        # When BaseConfiguration is constructed from those settings
        config = config_mod.BaseConfiguration(settings=s)

        # Then langgraph_sre_enabled property reflects the False setting
        assert config.langgraph_sre_enabled is False

    def test_langgraph_sre_enabled_returns_true_when_settings_is_true(self) -> None:
        # Given Settings with langgraph_sre_enabled explicitly set to True
        s = settings_mod.Settings(langgraph_sre_enabled=True)

        # When BaseConfiguration is constructed from those settings
        config = config_mod.BaseConfiguration(settings=s)

        # Then langgraph_sre_enabled property reflects the True setting
        assert config.langgraph_sre_enabled is True
