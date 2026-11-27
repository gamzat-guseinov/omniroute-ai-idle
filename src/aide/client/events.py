"""Typed stream events and the mapping from OpenAI-style chunks to them."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

from .errors import OmniRouteError, error_from_stream_payload


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """Reasoning/"thinking" tokens sent by some models (kept apart from the answer)."""
    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    """Fragment of a native tool call; fragments with the same index belong together."""
    index: int
    id: str | None
    name: str | None
    arguments: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class Done:
    finish_reason: str | None  # "stop", "length", "tool_calls", ... or None if not reported


@dataclass(frozen=True)
class RetryEvent:
    attempt: int
    max_retries: int
    delay: float
    error: OmniRouteError


StreamEvent = Union[TextDelta, ReasoningDelta, ToolCallDelta, Usage, Done, RetryEvent]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    prompt, completion = _int(raw.get("prompt_tokens")), _int(raw.get("completion_tokens"))
    total = _int(raw.get("total_tokens")) or prompt + completion
    return Usage(prompt, completion, total) if (prompt or completion or total) else None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # [{"type": "text", "text": "..."}]
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def chunk_finish_reason(obj: dict[str, Any]) -> str | None:
    for choice in obj.get("choices") or []:
        if isinstance(choice, dict) and choice.get("finish_reason"):
            return str(choice["finish_reason"])
    return None


def parse_chunk(obj: dict[str, Any]) -> list[StreamEvent]:
    """Map one streamed chunk (or one whole non-streamed response) to events.

    Raises an OmniRouteError if the chunk is an error payload.
    """
    if obj.get("error"):
        raise error_from_stream_payload(obj["error"])
    events: list[StreamEvent] = []
    for choice in obj.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or choice.get("message") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            events.append(ReasoningDelta(reasoning))
        text = _content_text(delta.get("content"))
        if text:
            events.append(TextDelta(text))
        for position, call in enumerate(delta.get("tool_calls") or []):
            fn = call.get("function") or {}
            events.append(ToolCallDelta(
                index=_int(call.get("index", position)),
                id=call.get("id"),
                name=fn.get("name"),
                arguments=fn.get("arguments") or "",
            ))
    usage = _usage(obj.get("usage"))
    if usage:
        events.append(usage)
    return events
