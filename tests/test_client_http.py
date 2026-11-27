"""End-to-end client tests against httpx.MockTransport (needs the httpx package)."""
import json
import tempfile
import unittest
from contextlib import aclosing
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

from aide.config import Config
from aide.debuglog import DebugLog

if httpx is not None:
    from aide.client.errors import (AuthError, NetworkError, RequestTimeoutError, StreamError)
    from aide.client.events import Done, RetryEvent, TextDelta, Usage
    from aide.client.omniroute import OmniRouteClient

KEY = "k-secret-123"


def sse(*chunks, done=True):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def delta(text=None, finish=None):
    return {"choices": [{"delta": {"content": text} if text else {}, "finish_reason": finish}]}


async def no_sleep(_):
    return None


@unittest.skipUnless(httpx, "httpx is not installed")
class ClientTests(unittest.IsolatedAsyncioTestCase):
    def make(self, handler, debug=None, **cfg):
        config = Config(base_url="http://gw.test/v1", api_key=KEY, model="m", max_retries=2, **cfg)
        client = OmniRouteClient(config, transport=httpx.MockTransport(handler),
                                 sleep=no_sleep, debug=debug)
        self.addAsyncCleanup(client.aclose)
        return client

    async def run_chat(self, client):
        events = []
        async with aclosing(client.stream_chat([{"role": "user", "content": "hi"}])) as s:
            async for ev in s:
                events.append(ev)
        return events

    async def test_streams_text_usage_and_done_and_sends_expected_request(self):
        seen = {}
        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
                delta("Hel"), delta("lo"), delta(finish="stop"),
                {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}))
        events = await self.run_chat(self.make(handler))
        self.assertEqual(events, [TextDelta("Hel"), TextDelta("lo"), Usage(3, 2, 5), Done("stop")])
        self.assertEqual(seen["url"], "http://gw.test/v1/chat/completions")
        self.assertEqual(seen["auth"], f"Bearer {KEY}")
        self.assertTrue(seen["body"]["stream"])
        self.assertEqual(seen["body"]["model"], "m")

    async def test_invalid_key_raises_auth_error(self):
        client = self.make(lambda r: httpx.Response(401, json={"error": {"message": "nope"}}))
        with self.assertRaises(AuthError) as ctx:
            await self.run_chat(client)
        self.assertIn("nope", ctx.exception.user_message())

    async def test_rate_limit_is_retried(self):
        calls = {"n": 0}
        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow"})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse(delta("ok"), delta(finish="stop")))
        events = await self.run_chat(self.make(handler))
        self.assertIsInstance(events[0], RetryEvent)
        self.assertIn(TextDelta("ok"), events)

    async def test_non_streaming_json_response_is_supported(self):
        obj = {"choices": [{"message": {"content": "whole"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        events = await self.run_chat(self.make(lambda r: httpx.Response(200, json=obj)))
        self.assertEqual(events, [TextDelta("whole"), Usage(1, 1, 2), Done("stop")])

    async def test_garbled_chunk_is_skipped(self):
        body = b"data: {not json\n\n" + sse(delta("fine"), delta(finish="stop"))
        client = self.make(lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body))
        self.assertEqual(await self.run_chat(client), [TextDelta("fine"), Done("stop")])

    async def test_timeout_and_network_errors(self):
        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)
        with self.assertRaises(RequestTimeoutError):
            await self.run_chat(self.make(timeout))
        def refused(request):
            raise httpx.ConnectError("refused", request=request)
        with self.assertRaises(NetworkError):
            await self.run_chat(self.make(refused))

    async def test_connection_drop_midway_is_not_retried(self):
        calls = {"n": 0}
        async def body():
            yield sse(delta("partial"), done=False)
            raise httpx.ReadError("reset")
        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())
        events = []
        with self.assertRaises(StreamError):
            async with aclosing(self.make(handler).stream_chat([])) as s:
                async for ev in s:
                    events.append(ev)
        self.assertEqual((events, calls["n"]), ([TextDelta("partial")], 1))

    async def test_api_key_never_reaches_the_debug_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "debug.log"
            debug = DebugLog(path, secrets=[KEY])
            client = self.make(lambda r: httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=sse(delta("x"), delta(finish="stop"))), debug=debug)
            await self.run_chat(client)
            debug.close()
            text = path.read_text(encoding="utf-8")
            self.assertIn('"kind": "request"', text)
            self.assertNotIn(KEY, text)


if __name__ == "__main__":
    unittest.main()
