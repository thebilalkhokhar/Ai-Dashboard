"""Builds chat models for the agents (LangChain init_chat_model).

Values come only from server/.env:
    LLM_PROVIDER  e.g. "openai"   (OpenAI chosen by the user in October 2026)
    LLM_API_KEY   the provider key - never logged, never printed
    MODEL_SUB     e.g. "gpt-4o-mini"      (sub-agents)
    MODEL_MASTER  e.g. "gpt-4o"           (Master CEO agent)

Every agent answer is structured output validated against a Pydantic model in
app/schemas/agents.py. The LLM never does math: it receives pre-computed JSON.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional, Tuple, Type

from pydantic import BaseModel

from app.config import settings

TEMPERATURE = 0.2  # low: we want boring, consistent answers


def _require(name: str) -> str:
    value = getattr(settings, name, "")
    if not value:
        raise RuntimeError(f"Missing {name} in server/.env - agents cannot run without it.")
    return value


def build_model(role: str = "sub", temperature: float = TEMPERATURE, **kwargs: Any):
    """One chat model. role: 'sub' -> MODEL_SUB, 'master' -> MODEL_MASTER."""
    provider = _require("LLM_PROVIDER").strip().lower()
    model_name = _require("MODEL_MASTER" if role == "master" else "MODEL_SUB").strip()
    api_key = _require("LLM_API_KEY")

    from langchain.chat_models import init_chat_model

    return init_chat_model(
        f"{provider}:{model_name}",
        api_key=api_key,
        temperature=temperature,
        **kwargs,
    )


def usage_of(message: Any) -> dict:
    """Token usage out of a LangChain AIMessage (varies slightly per provider)."""
    meta = getattr(message, "usage_metadata", None) or {}
    raw = getattr(message, "response_metadata", None) or {}
    token_usage = raw.get("token_usage") or {}
    if not meta and token_usage:
        meta = {
            "input_tokens": token_usage.get("prompt_tokens") or 0,
            "output_tokens": token_usage.get("completion_tokens") or 0,
        }
    total = int(
        meta.get("total_tokens")
        or (int(meta.get("input_tokens", 0) or 0) + int(meta.get("output_tokens", 0) or 0))
    )
    return {
        "input_tokens": int(meta.get("input_tokens", 0) or 0),
        "output_tokens": int(meta.get("output_tokens", 0) or 0),
        "total_tokens": total,
    }


class StructuredCaller:
    """Uniform call interface for agents.

    Agents call `await caller.ainvoke(messages)` and get
    (validated_model, usage_dict) back. Real models use LangChain structured
    output with include_raw=True so token usage is kept.
    """

    def __init__(self, model: Any, schema: Type[BaseModel]) -> None:
        self._inner = model.with_structured_output(schema, include_raw=True)

    async def ainvoke(self, messages: Any) -> Tuple[BaseModel, dict]:
        result = await self._inner.ainvoke(messages)
        if isinstance(result, dict) and "parsed" in result:
            return result["parsed"], usage_of(result.get("raw"))
        # some versions return the parsed object directly (no usage data)
        return result, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class FakeCaller:
    """Deterministic stand-in so tests never touch the real API.

    It reads the LAST message's JSON payload, runs the injected answer
    function on it and validates the result against the schema.
    """

    def __init__(self, schema: Type[BaseModel], answer: Callable[[dict], dict]) -> None:
        self._schema = schema
        self._answer = answer

    async def ainvoke(self, messages: Any) -> Tuple[BaseModel, dict]:
        payload = messages[-1]
        if isinstance(payload, dict):  # LangChain message dict form
            payload = payload.get("content", "")
        elif not isinstance(payload, str):
            payload = getattr(payload, "content", "")
        # the user message is pure JSON in our pipeline
        blob = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        start, end = blob.find("{"), blob.rfind("}")
        payload_dict = json.loads(blob[start : end + 1]) if start != -1 and end != -1 else {}

        answer = self._answer(payload_dict)
        return self._schema.model_validate(answer), {
            "input_tokens": len(blob) // 4,
            "output_tokens": len(json.dumps(answer)) // 4,
            "total_tokens": len(blob) // 4 + len(json.dumps(answer)) // 4,
        }


def make_caller(model: Any, schema: Type[BaseModel]) -> StructuredCaller:
    """Real caller; tests use FakeCaller instead."""
    return StructuredCaller(model, schema)


def empty_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_usage(total: dict, extra: dict) -> dict:
    return {
        "input_tokens": total.get("input_tokens", 0) + extra.get("input_tokens", 0),
        "output_tokens": total.get("output_tokens", 0) + extra.get("output_tokens", 0),
        "total_tokens": total.get("total_tokens", 0) + extra.get("total_tokens", 0),
    }
