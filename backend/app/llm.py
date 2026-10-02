"""Model gateway (specs/02-agent-runtime.md, "Model gateway" section).

This is the ONLY module allowed to import a provider SDK (MG-2). The rest of
the codebase — including the future runner — depends solely on the `LLM`
Protocol and the `ChatResponse` / `ToolCall` dataclasses defined here.

MG-1: reads MODEL_BASE_URL, MODEL_API_KEY, MODEL_NAME, JUDGE_MODEL_NAME,
IMPROVER_MODEL_NAME from env.
MG-2: no provider SDK imports outside this module.
MG-3 (P1): per-version model override — handled by callers passing `model=`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Protocol

import openai  # ONLY module in the codebase allowed to do this (MG-2).
from dotenv import load_dotenv

# Load backend/.env once at import time so MODEL_BASE_URL / MODEL_API_KEY /
# MODEL_NAME (MG-1) are populated from the gitignored .env file in dev and in
# scripts, without overriding real environment variables that are already set
# (e.g. in CI or a container).
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


@dataclass
class ToolCall:
    """One function/tool call requested by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string, as returned by the provider


@dataclass
class ChatResponse:
    """Normalized response from any LLM implementation."""

    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None


class LLM(Protocol):
    """Model gateway interface (specs/02-agent-runtime.md)."""

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0,
        response_format: Optional[dict] = None,
    ) -> ChatResponse: ...


@dataclass
class ModelGatewayConfig:
    """MG-1 env configuration."""

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    judge_model_name: Optional[str] = None
    improver_model_name: Optional[str] = None

    @classmethod
    def from_env(cls) -> "ModelGatewayConfig":
        return cls(
            base_url=os.environ.get("MODEL_BASE_URL"),
            api_key=os.environ.get("MODEL_API_KEY"),
            model_name=os.environ.get("MODEL_NAME"),
            judge_model_name=os.environ.get("JUDGE_MODEL_NAME"),
            improver_model_name=os.environ.get("IMPROVER_MODEL_NAME"),
        )


def improver_model_name() -> Optional[str]:
    """MG-1: `IMPROVER_MODEL_NAME` env var, read here (the gateway) and used
    by the improver (specs/05) as a per-call `model=` override for its own
    diagnose+propose LLM call -- falls back to `MODEL_NAME` (i.e. `model=None`
    on the `chat()` call) when unset, same fallback convention
    `JUDGE_MODEL_NAME` already uses at call sites (e.g. `main.py`'s
    `judge_model = os.environ.get("JUDGE_MODEL_NAME")`, passed straight
    through as `model=` to `OpenAICompatLLM.chat`, which falls back to
    `self.config.model_name` when `model` is None).

    Before this phase, `ModelGatewayConfig.improver_model_name` was read from
    the env (MG-1) but never consumed anywhere as an actual model override --
    this function is the first real use of it.
    """
    return ModelGatewayConfig.from_env().improver_model_name


class OpenAICompatLLM:
    """Real `LLM` implementation using the OpenAI Python SDK against a
    configurable `base_url` (so any OpenAI-compatible endpoint, e.g. vLLM,
    works per G-5 / open question in specs/02-agent-runtime.md).
    """

    def __init__(self, config: Optional[ModelGatewayConfig] = None):
        self.config = config or ModelGatewayConfig.from_env()
        self._client = openai.AsyncOpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key or "not-configured",
        )

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0,
        response_format: Optional[dict] = None,
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.config.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        completion = await self._client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message

        tool_calls = None
        if getattr(message, "tool_calls", None):
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in message.tool_calls
            ]

        return ChatResponse(content=message.content, tool_calls=tool_calls)


class FakeLLM:
    """Test double: returns scripted `ChatResponse`s in order, deterministically.

    Used by runner/improver tests (specs/00-overview.md §6 Constraints) so no
    test layer besides integration/e2e depends on real model output
    (specs/07-verification-and-validation.md §1).
    """

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict] = []

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0,
        response_format: Optional[dict] = None,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "temperature": temperature,
                "response_format": response_format,
            }
        )
        if self._index >= len(self._responses):
            raise IndexError("FakeLLM script exhausted: no more scripted responses")
        response = self._responses[self._index]
        self._index += 1
        return response
