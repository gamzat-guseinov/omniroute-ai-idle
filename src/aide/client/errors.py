"""Typed, human-readable errors for the OmniRoute client."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class OmniRouteError(Exception):
    """Base class. `retryable` tells the retry loop whether trying again can help."""

    retryable = False

    def __init__(self, message: str, *, status: int | None = None,
                 detail: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail
        self.retry_after = retry_after

    def user_message(self) -> str:
        return f"{self.message} Server said: {self.detail}" if self.detail else self.message


class AuthError(OmniRouteError):
    pass


class NotFoundError(OmniRouteError):
    pass


class QuotaError(OmniRouteError):
    pass


class BadRequestError(OmniRouteError):
    pass


class ContextOverflowError(OmniRouteError):
    pass


class RateLimitError(OmniRouteError):
    retryable = True


class ServerError(OmniRouteError):
    retryable = True


class RequestTimeoutError(OmniRouteError):
    retryable = True


class NetworkError(OmniRouteError):
    retryable = True


class StreamError(OmniRouteError):
    """The response broke off midway. Never retried automatically: text was already shown."""


_CONTEXT_RE = re.compile(
    r"context[_ ](length|window)|maximum context|too many tokens|prompt is too long|"
    r"reduce the length|exceeds the (model.s )?(maximum )?(context|token)", re.I)
_QUOTA_RE = re.compile(r"insufficient[_ ]quota|exceeded your current quota|out of credits|"
                       r"billing|payment required|insufficient (funds|credits)", re.I)


def _shorten(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _find_message(data: object) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("error", "message", "detail", "msg"):
            if key in data:
                found = _find_message(data[key])
                if found:
                    return found
    if isinstance(data, list) and data:
        return _find_message(data[0])
    return ""


def extract_error_detail(body: str) -> str:
    """Pull a short, readable message out of an error response body."""
    body = (body or "").strip()
    if not body or body.startswith("<"):  # empty, or an HTML error page from a proxy
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        return _shorten(body)
    return _shorten(_find_message(data) or body)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None


def error_from_response(status: int, body: str = "",
                        retry_after: float | None = None) -> OmniRouteError:
    detail = extract_error_detail(body)
    kw = {"status": status, "detail": detail}
    if status in (401, 403):
        return AuthError(f"The API key was rejected (HTTP {status}). Check OMNIROUTE_API_KEY "
                         f"or run `aide --reconfigure`.", **kw)
    if status == 404:
        return NotFoundError("Endpoint or model not found (HTTP 404). Check the base URL "
                             "(it usually ends in /v1) and the model name.", **kw)
    if status == 402 or (status == 429 and _QUOTA_RE.search(detail)):
        return QuotaError(f"Quota or credits exhausted (HTTP {status}).", **kw)
    if status == 408:
        return RequestTimeoutError("The server timed out waiting for the request (HTTP 408).", **kw)
    if status == 429:
        return RateLimitError("Rate limit reached (HTTP 429).", retry_after=retry_after, **kw)
    if status >= 500:
        return ServerError(f"The server had a problem (HTTP {status}).",
                           retry_after=retry_after, **kw)
    if 300 <= status < 400:
        return BadRequestError(f"The server redirected the request (HTTP {status}). Check the "
                               f"base URL (http vs https, path).", **kw)
    if status in (400, 413, 422) and _CONTEXT_RE.search(detail):
        return ContextOverflowError("The conversation is too long for this model's context "
                                    "window. Use /clear to start fresh.", **kw)
    return BadRequestError(f"The server rejected the request (HTTP {status}).", **kw)


def error_from_stream_payload(err: object) -> OmniRouteError:
    """Build an error from an {"error": ...} object received inside a stream."""
    body = json.dumps({"error": err})
    if isinstance(err, dict):
        for key in ("status", "code", "status_code"):
            try:
                number = int(err.get(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if 400 <= number <= 599:
                return error_from_response(number, body)
    return StreamError("The server reported an error in the middle of the response.",
                       detail=extract_error_detail(body))
