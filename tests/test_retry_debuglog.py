import asyncio
import os
import stat
import tempfile
import unittest
from contextlib import aclosing
from pathlib import Path

from aide.client.errors import AuthError, NetworkError, RateLimitError
from aide.client.events import Done, RetryEvent, TextDelta
from aide.client.retry import MAX_RETRY_AFTER, backoff_delay, stream_with_retry
from aide.debuglog import DebugLog


class BackoffTests(unittest.TestCase):
    def test_grows_exponentially_and_is_capped(self):
        delays = [backoff_delay(a, base=1, cap=10, rng=lambda: 1.0) for a in range(6)]
        self.assertEqual(delays, [1, 2, 4, 8, 10, 10])

    def test_jitter_stays_between_half_and_full(self):
        self.assertEqual(backoff_delay(2, base=1, cap=100, rng=lambda: 0.0), 2.0)

    def test_retry_after_is_honoured_but_bounded(self):
        self.assertEqual(backoff_delay(0, base=1, retry_after=7, rng=lambda: 1.0), 7)
        self.assertEqual(backoff_delay(0, base=1, retry_after=9999, rng=lambda: 1.0),
                         MAX_RETRY_AFTER)


class StreamRetryTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, open_stream, **kw):
        sleeps = []
        async def fake_sleep(d):
            sleeps.append(d)
        events = []
        async with aclosing(stream_with_retry(open_stream, sleep=fake_sleep,
                                              rng=lambda: 1.0, **kw)) as s:
            async for ev in s:
                events.append(ev)
        return events, sleeps

    def flaky(self, failures, make_error):
        calls = {"n": 0}
        async def open_stream():
            calls["n"] += 1
            if calls["n"] <= failures:
                raise make_error()
            yield TextDelta("ok")
            yield Done("stop")
        return open_stream, calls

    async def test_retries_then_succeeds(self):
        open_stream, calls = self.flaky(2, lambda: RateLimitError("slow", retry_after=5))
        events, sleeps = await self.collect(open_stream, max_retries=3)
        self.assertEqual([type(e) for e in events], [RetryEvent, RetryEvent, TextDelta, Done])
        self.assertEqual((calls["n"], sleeps), (3, [5, 5]))
        self.assertEqual((events[0].attempt, events[1].attempt), (1, 2))

    async def test_non_retryable_error_is_raised_immediately(self):
        open_stream, calls = self.flaky(5, lambda: AuthError("bad key"))
        with self.assertRaises(AuthError):
            await self.collect(open_stream, max_retries=3)
        self.assertEqual(calls["n"], 1)

    async def test_gives_up_after_max_retries(self):
        open_stream, calls = self.flaky(99, lambda: NetworkError("down"))
        with self.assertRaises(NetworkError):
            await self.collect(open_stream, max_retries=2)
        self.assertEqual(calls["n"], 3)

    async def test_no_retry_once_text_was_shown(self):
        calls = {"n": 0}
        async def open_stream():
            calls["n"] += 1
            yield TextDelta("partial")
            raise NetworkError("dropped")
        with self.assertRaises(NetworkError):
            await self.collect(open_stream, max_retries=3)
        self.assertEqual(calls["n"], 1)

    async def test_cancelling_the_consumer_closes_the_inner_stream(self):
        closed = []
        async def open_stream():
            try:
                yield TextDelta("x")
                await asyncio.sleep(10)
            finally:
                closed.append(True)
        async def consume():
            async with aclosing(stream_with_retry(open_stream, max_retries=0)) as s:
                async for _ in s:
                    pass
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(closed, [True])


class DebugLogTests(unittest.TestCase):
    def test_secrets_are_redacted_and_file_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "debug.log"
            log = DebugLog(path, secrets=["supersecretkey"])
            log.log("request", {
                "headers": {"Authorization": "Bearer sk-abcdef123456", "X-Api-Key": "zzz"},
                "json": {"messages": [{"content": "my key is supersecretkey / sk-abcdef123456"}]},
            })
            log.close()
            text = path.read_text(encoding="utf-8")
            for leaked in ("supersecretkey", "sk-abcdef123456", "zzz"):
                self.assertNotIn(leaked, text)
            self.assertIn("***", text)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_disabled_logger_is_a_noop(self):
        log = DebugLog(None)
        self.assertFalse(log.enabled)
        log.log("anything", {"a": 1})
        log.close()


if __name__ == "__main__":
    unittest.main()
