"""
SQLModel table definitions for comparison_runs and eval_runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, SQLModel


class ComparisonRunRecord(SQLModel, table=True):
    __tablename__ = "comparison_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    investigation_record_id: uuid.UUID = Field(index=True)
    baseline_adapter: str
    challenger_adapter: str
    baseline_result_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    challenger_result_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    comparison_result_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    baseline_duration_ms: int
    challenger_duration_ms: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EvalRunRecord(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    dataset_name: str = Field(index=True)
    total_cases: int
    passed_cases: int
    failed_cases: int
    average_score: float | None = None
    results_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    run_duration_ms: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
