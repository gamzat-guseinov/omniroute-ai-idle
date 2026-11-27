"""Server-Sent Events decoding (only the parts OpenAI-style streaming needs)."""
from __future__ import annotations

from contextlib import aclosing
from typing import AsyncIterator


class SSEDecoder:
    """Feed it lines (without newline characters); it returns the data of finished events."""

    def __init__(self) -> None:
        self._data: list[str] = []

    def feed_line(self, line: str) -> str | None:
        line = line.rstrip("\r")
        if line == "":  # a blank line ends the event
            return self.flush()
        if line.startswith(":"):  # comment / keep-alive
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        return None  # "event:", "id:", "retry:" are irrelevant here

    def flush(self) -> str | None:
        if not self._data:
            return None
        data, self._data = "\n".join(self._data), []
        return data


async def iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Yield the data payload of every SSE event found in an async stream of lines."""
    decoder = SSEDecoder()
    async with aclosing(lines) as source:  # type: ignore[type-var]
        async for line in source:
            data = decoder.feed_line(line)
            if data is not None:
                yield data
    tail = decoder.flush()  # stream ended without a final blank line
    if tail is not None:
        yield tail
