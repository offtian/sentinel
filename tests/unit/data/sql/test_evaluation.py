"""Tests for evaluation SQLModel table definitions."""

from __future__ import annotations

from sentinel.data.sql import evaluation


class TestEvalRunRecordFields:
    def test_has_agent_name_field(self) -> None:
        # Given an EvalRunRecord model

        # When checking the model fields
        fields = evaluation.EvalRunRecord.model_fields

        # Then agent_name field exists and defaults to None
        assert "agent_name" in fields
        assert fields["agent_name"].default is None

    def test_has_composite_score_field(self) -> None:
        # Given an EvalRunRecord model

        # When checking the model fields
        fields = evaluation.EvalRunRecord.model_fields

        # Then composite_score field exists and defaults to None
        assert "composite_score" in fields
        assert fields["composite_score"].default is None

    def test_has_assertion_details_json_field(self) -> None:
        # Given an EvalRunRecord model

        # When checking the model fields
        fields = evaluation.EvalRunRecord.model_fields

        # Then assertion_details_json field exists and defaults to None
        assert "assertion_details_json" in fields
        assert fields["assertion_details_json"].default is None

    def test_agent_name_is_indexed(self) -> None:
        # Given an EvalRunRecord model

        # When checking the agent_name field info
        field_info = evaluation.EvalRunRecord.model_fields["agent_name"]

        # Then the field metadata indicates it is indexed
        # SQLModel stores index in FieldInfoMetadata within the metadata list
        assert len(field_info.metadata) > 0
        meta = field_info.metadata[0]
        assert meta.index is True
