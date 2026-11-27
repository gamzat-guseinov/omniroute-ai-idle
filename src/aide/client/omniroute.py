"""Streaming client for the OmniRoute API (OpenAI-compatible /chat/completions)."""
from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from .. import __version__
from ..config import Config
from ..debuglog import DebugLog
from .errors import (
    NetworkError,
    OmniRouteError,
    RequestTimeoutError,
    StreamError,
    error_from_response,
    parse_retry_after,
)
from .events import Done, StreamEvent, chunk_finish_reason, parse_chunk
from .retry import stream_with_retry
from .sse import iter_sse_data


class OmniRouteClient:
    def __init__(
        self,
        config: Config,
        *,
        debug: DebugLog | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cfg = config
        self._debug = debug or DebugLog(None)
        self._sleep = sleep
        timeout = httpx.Timeout(config.timeout, connect=min(config.timeout, 15.0))
        self._http = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,  # a redirected POST would silently lose its body
            headers={"User-Agent": f"aide/{__version__}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion as typed events (retries transient failures).

        Use as:  async with aclosing(client.stream_chat(msgs)) as stream: async for ev in stream: ...
        Cancelling the surrounding task closes the HTTP connection.
        """
        cfg = self._cfg
        payload: dict[str, Any] = {
            "model": model or cfg.model,
            "messages": messages,
            "stream": True,
            "temperature": cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or cfg.max_tokens,
        }
        if cfg.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
        return stream_with_retry(
            lambda: self._stream_once(payload),
            max_retries=cfg.max_retries,
            sleep=self._sleep,
        )

    # ------------------------------------------------------------------ one attempt
    async def _stream_once(self, payload: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        cfg = self._cfg
        url = f"{cfg.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.api_key}", "Accept": "text/event-stream"}
        self._debug.log("request", {"method": "POST", "url": url, "headers": headers,
                                    "json": payload})
        got_data = False
        try:
            async with self._http.stream("POST", url, json=payload, headers=headers) as resp:
                content_type = resp.headers.get("content-type", "")
                if not resp.is_success:
                    raw = (await resp.aread()).decode("utf-8", "replace")
                    body = raw or resp.headers.get("location", "")
                    self._debug.log("response_error", {"status": resp.status_code, "body": body})
                    raise error_from_response(
                        resp.status_code, body, parse_retry_after(resp.headers.get("retry-after")))
                self._debug.log("response_start",
                                {"status": resp.status_code, "content_type": content_type})

                if "application/json" in content_type:
                    # The gateway ignored stream=true and sent one complete response.
                    obj = self._loads((await resp.aread()).decode("utf-8", "replace"))
                    self._debug.log("response_body", obj)
                    for event in parse_chunk(obj):
                        got_data = True
                        yield event
                    yield Done(chunk_finish_reason(obj))
                    return

                finish: str | None = None
                async with aclosing(iter_sse_data(resp.aiter_lines())) as chunks:
                    async for data in chunks:
                        if data.strip() == "[DONE]":
                            break
                        self._debug.log("chunk", data)
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue  # ignore a garbled keep-alive/partial line
                        if not isinstance(obj, dict):
                            continue
                        finish = chunk_finish_reason(obj) or finish
                        for event in parse_chunk(obj):
                            got_data = True
                            yield event
                yield Done(finish)
        except httpx.InvalidURL as exc:
            raise OmniRouteError(f"The base URL {cfg.base_url!r} is not a valid URL.") from exc
        except httpx.TimeoutException as exc:
            if got_data:
                raise StreamError("The server stopped responding in the middle of the answer "
                                  f"(no data for {cfg.timeout:g}s).") from exc
            raise RequestTimeoutError(f"No response from the server within {cfg.timeout:g}s.",
                                      detail=type(exc).__name__) from exc
        except httpx.TransportError as exc:
            if got_data:
                raise StreamError("The connection dropped in the middle of the answer.",
                                  detail=str(exc)) from exc
            raise NetworkError(f"Cannot reach {cfg.base_url}. Check the URL and your network.",
                               detail=str(exc) or type(exc).__name__) from exc

    @staticmethod
    def _loads(text: str) -> dict[str, Any]:
        try:
            obj = json.loads(text)
        except ValueError as exc:
            raise StreamError("The server sent a response that is not valid JSON.") from exc
        if not isinstance(obj, dict):
            raise StreamError("The server sent an unexpected response format.")
        return obj
