import unittest

from aide.client.errors import (
    AuthError, BadRequestError, ContextOverflowError, NotFoundError, QuotaError,
    RateLimitError, RequestTimeoutError, ServerError, StreamError,
    error_from_response, error_from_stream_payload, extract_error_detail, parse_retry_after,
)
from aide.client.events import (
    ReasoningDelta, TextDelta, ToolCallDelta, Usage, chunk_finish_reason, parse_chunk,
)
from aide.client.sse import SSEDecoder, iter_sse_data


class SSETests(unittest.IsolatedAsyncioTestCase):
    def feed(self, text):
        d, out = SSEDecoder(), []
        for line in text.split("\n"):
            r = d.feed_line(line)
            if r is not None:
                out.append(r)
        return out

    def test_basic_events(self):
        self.assertEqual(self.feed('data: {"a":1}\n\ndata: [DONE]\n\n'), ['{"a":1}', "[DONE]"])

    def test_multiline_data_comments_and_other_fields(self):
        text = ": keep-alive\nevent: message\nid: 7\ndata: line1\ndata: line2\n\n"
        self.assertEqual(self.feed(text), ["line1\nline2"])

    def test_crlf_and_no_space_after_colon(self):
        self.assertEqual(self.feed("data:{}\r\n\r\n"), ["{}"])

    def test_flush_returns_unterminated_event(self):
        d = SSEDecoder()
        d.feed_line("data: tail")
        self.assertEqual(d.flush(), "tail")
        self.assertIsNone(d.flush())

    async def test_iter_sse_data_flushes_at_eof(self):
        async def lines():
            for line in ["data: one", "", "data: two"]:
                yield line
        self.assertEqual([x async for x in iter_sse_data(lines())], ["one", "two"])


class EventTests(unittest.TestCase):
    def test_text_reasoning_and_usage(self):
        chunk = {"choices": [{"delta": {"content": "Hi", "reasoning_content": "hmm"}}],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        self.assertEqual(parse_chunk(chunk),
                         [ReasoningDelta("hmm"), TextDelta("Hi"), Usage(5, 2, 7)])

    def test_empty_and_role_only_chunks_produce_nothing(self):
        self.assertEqual(parse_chunk({"choices": [{"delta": {"role": "assistant", "content": ""}}]}), [])
        self.assertEqual(parse_chunk({"choices": []}), [])

    def test_tool_call_fragments(self):
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "c1", "function": {"name": "read_file", "arguments": '{"pa'}}]}}]}
        self.assertEqual(parse_chunk(chunk), [ToolCallDelta(1, "c1", "read_file", '{"pa')])

    def test_non_streamed_message_and_content_parts(self):
        obj = {"choices": [{"message": {"content": [{"type": "text", "text": "A"},
                                                     {"type": "text", "text": "B"}]},
                            "finish_reason": "stop"}]}
        self.assertEqual(parse_chunk(obj), [TextDelta("AB")])
        self.assertEqual(chunk_finish_reason(obj), "stop")

    def test_error_payload_raises(self):
        with self.assertRaises(RateLimitError):
            parse_chunk({"error": {"message": "slow down", "code": 429}})
        with self.assertRaises(StreamError):
            parse_chunk({"error": {"message": "boom"}})


class ErrorTests(unittest.TestCase):
    def test_status_mapping(self):
        cases = {401: AuthError, 403: AuthError, 404: NotFoundError, 408: RequestTimeoutError,
                 429: RateLimitError, 500: ServerError, 503: ServerError, 402: QuotaError,
                 400: BadRequestError, 302: BadRequestError}
        for status, cls in cases.items():
            self.assertIsInstance(error_from_response(status, ""), cls, status)

    def test_retryable_flags(self):
        self.assertTrue(error_from_response(429).retryable)
        self.assertTrue(error_from_response(502).retryable)
        self.assertFalse(error_from_response(401).retryable)
        self.assertFalse(error_from_response(400).retryable)

    def test_quota_429_is_not_retried(self):
        err = error_from_response(429, '{"error": {"message": "You exceeded your current quota"}}')
        self.assertIsInstance(err, QuotaError)
        self.assertFalse(err.retryable)

    def test_context_overflow(self):
        body = '{"error": {"message": "This model\'s maximum context length is 8192 tokens"}}'
        self.assertIsInstance(error_from_response(400, body), ContextOverflowError)

    def test_detail_extraction(self):
        self.assertEqual(extract_error_detail('{"error": {"message": "bad key"}}'), "bad key")
        self.assertEqual(extract_error_detail('{"detail": [{"msg": "oops"}]}'), "oops")
        self.assertEqual(extract_error_detail("<html>502 Bad Gateway</html>"), "")
        self.assertEqual(extract_error_detail("plain text"), "plain text")
        self.assertIn("Server said: bad key", error_from_response(
            401, '{"error": "bad key"}').user_message())

    def test_retry_after(self):
        self.assertEqual(parse_retry_after("3"), 3.0)
        self.assertIsNone(parse_retry_after("soon"))
        self.assertIsNone(parse_retry_after(None))
        self.assertEqual(error_from_response(429, "", 4.0).retry_after, 4.0)

    def test_stream_payload_without_status_is_stream_error(self):
        self.assertIsInstance(error_from_stream_payload("just text"), StreamError)


if __name__ == "__main__":
    unittest.main()
