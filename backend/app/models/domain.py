from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class DataType(StrEnum):
    TRIAL_BALANCE = "TRIAL_BALANCE"
    JOURNAL = "JOURNAL"


class TransformationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProcessingStage(StrEnum):
    QUEUED = "QUEUED"
    PROFILING = "PROFILING"
    PLANNING = "PLANNING"
    TRANSFORMING = "TRANSFORMING"
    VALIDATING = "VALIDATING"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ControlStatus(StrEnum):
    NOT_PERFORMED = "NOT_PERFORMED"
    PASSED = "PASSED"
    WARNINGS = "WARNINGS"
    FAILED = "FAILED"


class UserPublic(BaseModel):
    id: str
    name: str
    email: str
    role: UserRole


class LoginInput(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)


class RetryInput(BaseModel):
    feedback: str | None = Field(default=None, max_length=4000)


class TargetColumn(BaseModel):
    key: str
    label: str
    kind: Literal["text", "number", "date"] = "text"
    required: bool = False


class RowRules(BaseModel):
    skip_empty: bool = True
    skip_keywords: list[str] = Field(default_factory=lambda: ["итого", "всего"])
    inherit_fields: list[str] = Field(default_factory=list)
    account_tree: bool = False
    classify_account_rows: bool = False
    standard_money_order: bool = False


class ValueOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_field: str
    from_row: int = Field(ge=1)
    to_row: int | None = Field(default=None, ge=1)
    value: str | float | None
    reason: str | None = Field(default=None, max_length=500)


class HierarchyLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["account", "analytic"] = "analytic"


class HierarchyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_column: int = Field(ge=1)
    levels: list[HierarchyLevel] = Field(min_length=2)


class TransformationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    sheet_name: str
    header_row: int = Field(ge=1)
    header_start_row: int | None = Field(default=None, ge=1)
    header_end_row: int | None = Field(default=None, ge=1)
    data_start_row: int = Field(ge=1)
    data_end_row: int = Field(ge=1)
    control_rows: list[int] = Field(default_factory=list)
    column_mapping: dict[str, int | None]
    target_columns: list[TargetColumn]
    row_rules: RowRules = Field(default_factory=RowRules)
    hierarchy: HierarchyRule | None = None
    value_overrides: list[ValueOverride] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


class TemplateInfo(BaseModel):
    id: str
    data_type: DataType
    name: str
    description: str
    columns: list[TargetColumn]


class TransformationSummary(BaseModel):
    id: str
    source_filename: str
    data_type: DataType
    template_id: str
    status: TransformationStatus
    current_stage: ProcessingStage
    processed_rows: int
    unprocessed_rows: int
    control_status: ControlStatus
    version: int
    created_at: datetime
    updated_at: datetime


class TransformationDetail(TransformationSummary):
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    controls: dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False


class PreviewResponse(BaseModel):
    columns: list[TargetColumn]
    rows: list[dict[str, Any]]
    total: int
    version: int


class AuditLogItem(BaseModel):
    id: int
    user_id: str | None
    user_name: str | None
    user_email: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    outcome: str
    details: dict[str, Any]
    created_at: datetime
