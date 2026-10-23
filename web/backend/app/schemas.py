"""API入出力のPydanticモデル。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pc.database import normalize_uid


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StudentCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    card_uid: str = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("card_uid")
    @classmethod
    def validate_uid(cls, value: str) -> str:
        return normalize_uid(value)


class RfidRegistrationStartRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    port: str = Field(min_length=1, max_length=100)
    mode: Literal["SERIAL"] = "SERIAL"

    @field_validator("name", "port")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class CourseCreateRequest(StrictModel):
    course_name: str = Field(min_length=1, max_length=200)

    @field_validator("course_name")
    @classmethod
    def validate_course_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("course_name must not be blank")
        return value


class EnrollmentCreateRequest(StrictModel):
    student_id: int = Field(gt=0)


class SessionCreateRequest(StrictModel):
    course_id: int = Field(gt=0)


class RuntimeStartRequest(StrictModel):
    session_id: int = Field(gt=0)
    mode: Literal["MOCK", "SERIAL"] = "SERIAL"
    port: str | None = None

    @field_validator("port")
    @classmethod
    def normalize_port(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_port_for_serial(self) -> "RuntimeStartRequest":
        if self.mode == "SERIAL" and not self.port:
            raise ValueError("port is required for SERIAL mode")
        return self


class MockScanRequest(StrictModel):
    card_uid: str = Field(min_length=1, max_length=20)

    @field_validator("card_uid")
    @classmethod
    def validate_uid(cls, value: str) -> str:
        return normalize_uid(value)


class EventQuery(StrictModel):
    server_instance_id: str | None = None
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)


class EventBatch(StrictModel):
    server_instance_id: str
    first_sequence: int
    last_sequence: int
    next_after_sequence: int
    reset_required: bool
    events: list[dict[str, Any]]
