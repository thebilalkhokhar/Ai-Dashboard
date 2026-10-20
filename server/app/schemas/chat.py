"""Request schema for the read-only chat endpoint."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("thread_id", "message")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value
