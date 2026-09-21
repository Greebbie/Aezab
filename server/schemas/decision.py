"""Configuration for a bounded semantic choice inside a workflow."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DecisionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    description: str = Field(min_length=1, max_length=1000)


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal["typesafe"] = "typesafe"
    instructions: str = Field(min_length=1, max_length=4000)
    input_fields: list[str] = Field(min_length=1, max_length=20)
    choices: list[DecisionChoice] = Field(min_length=2, max_length=32)
    result_key: str = Field(default="route", max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")
    min_confidence: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    min_probability: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)

    @field_validator("input_fields")
    @classmethod
    def validate_input_fields(cls, fields: list[str]) -> list[str]:
        if any(not field.strip() or field.startswith("_") or len(field) > 128 for field in fields):
            raise ValueError("Input fields must name public collected fields")
        if len(set(fields)) != len(fields):
            raise ValueError("Input fields must be unique")
        return fields

    @model_validator(mode="after")
    def validate_choices(self) -> DecisionConfig:
        values = [choice.value for choice in self.choices]
        if len(set(values)) != len(values):
            raise ValueError("Choice values must be unique")
        if "other" not in values:
            raise ValueError("Include an 'other' choice for insufficient evidence or no match")
        if self.result_key in self.input_fields:
            raise ValueError("Result key must not overwrite an input field")
        return self
