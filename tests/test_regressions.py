import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import grok_search.server as server
from grok_search.providers.grok import GrokSearchProvider, _is_retryable_exception


@pytest.mark.asyncio
async def test_web_search_reports_grok_failure(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    calls = []

    async def fail_search(self, query, platform=""):
        calls.append((query, platform))
        raise RuntimeError("synthetic Grok failure")

    monkeypatch.setattr(GrokSearchProvider, "search", fail_search)

    async with Client(server.mcp) as client:
        try:
            result = await client.call_tool(
                "web_search",
                {"query": "test query", "platform": "GitHub"},
            )
        except ToolError:
            assert calls == [("test query", "GitHub")]
            return

    assert calls == [("test query", "GitHub")]
    assert result.is_error, "Grok failures must not become empty successful results"


@pytest.mark.asyncio
async def test_tavily_disabled_blocks_all_tavily_requests(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "false")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    tavily_requests = []

    class BlockingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            tavily_requests.append(url)
            raise AssertionError("unexpected Tavily request")

    async def grok_success(self, query, platform=""):
        return "Grok answer"

    async def firecrawl_fallback(url, ctx=None):
        return "Firecrawl fallback"

    monkeypatch.setattr(httpx, "AsyncClient", BlockingAsyncClient)
    monkeypatch.setattr(GrokSearchProvider, "search", grok_success)
    monkeypatch.setattr(server, "_call_firecrawl_scrape", firecrawl_fallback)

    search_result = await server.web_search("test query", extra_sources=1)
    fetch_result = await server.web_fetch("https://example.invalid")
    map_result = await server.web_map("https://example.invalid")

    assert search_result["content"] == "Grok answer"
    assert fetch_result == "Firecrawl fallback"
    assert isinstance(map_result, str)
    assert tavily_requests == [], "TAVILY_ENABLED=false must block search, extract, and map requests"


@pytest.mark.asyncio
async def test_firecrawl_scrape_retries_transient_connect_error(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key")
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "2")
    attempts = 0

    class SuccessfulResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"markdown": "# recovered"}}

    class RetryAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("synthetic disconnect")
            return SuccessfulResponse()

    monkeypatch.setattr(httpx, "AsyncClient", RetryAsyncClient)

    result = await server._call_firecrawl_scrape("https://example.invalid")

    assert result == "# recovered"
    assert attempts == 2


@pytest.mark.asyncio
async def test_streaming_parser_ignores_empty_and_usage_events():
    class StreamingResponse:
        async def aiter_lines(self):
            for line in (
                'data: {"choices": []}',
                'data: {"usage": {"total_tokens": 1}}',
                'data: {"choices": [{"delta": {"content": "hello"}}]}',
                "data: [DONE]",
            ):
                yield line

    provider = GrokSearchProvider("https://grok.invalid", "test-grok-key", "test-model")

    assert await provider._parse_streaming_response(StreamingResponse()) == "hello"


@pytest.mark.asyncio
async def test_streaming_response_raises_for_upstream_error():
    nested_marker = "PRIVATE_NESTED_ERROR_MARKER"
    extra_marker = "PRIVATE_EXTRA_ERROR_MARKER"
    payloads = (
        (
            {
                "error": {
                    "code": "upstream_stream_interrupted" + "x" * 500,
                    "type": "server_error",
                    "message": {"private": nested_marker},
                    "debug": extra_marker,
                }
            },
            "upstream_stream_interrupted",
        ),
        (
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "response_failed" + "x" * 500,
                        "type": "server_error",
                        "message": "response failed",
                        "details": {"private": nested_marker},
                        "debug": extra_marker,
                    },
                },
            },
            "response_failed",
        ),
    )

    class StreamingResponse:
        def __init__(self, payload):
            self.payload = payload

        async def aiter_lines(self):
            if self.payload.get("type") == "response.failed":
                yield "event: response.failed"
            yield f"data: {json.dumps(self.payload)}"

    provider = GrokSearchProvider("https://grok.invalid", "test-grok-key", "test-model")

    for payload, expected_code in payloads:
        with pytest.raises(RuntimeError) as exc_info:
            await provider._parse_streaming_response(StreamingResponse(payload))

        error_text = str(exc_info.value)
        assert "Grok upstream stream error" in error_text
        assert expected_code in error_text
        assert nested_marker not in error_text
        assert extra_marker not in error_text
        assert len(error_text) <= 550


@pytest.mark.asyncio
async def test_streaming_parser_distinguishes_post_start_protocol_errors():
    marker = "PRIVATE_TRANSPORT_MARKER"

    class StreamingResponse:
        def __init__(self, started):
            self.started = started

        async def aiter_lines(self):
            if self.started:
                yield 'data: {"choices": []}'
            raise httpx.RemoteProtocolError(marker)

    provider = GrokSearchProvider("https://grok.invalid", "test-grok-key", "test-model")

    with pytest.raises(RuntimeError) as post_start:
        await provider._parse_streaming_response(StreamingResponse(started=True))

    error_text = str(post_start.value)
    assert error_text.startswith("Grok upstream stream error:")
    assert "code=upstream_stream_interrupted" in error_text
    assert "type=transport_error" in error_text
    assert marker not in error_text
    assert not _is_retryable_exception(post_start.value)

    with pytest.raises(httpx.RemoteProtocolError) as pre_start:
        await provider._parse_streaming_response(StreamingResponse(started=False))

    assert _is_retryable_exception(pre_start.value)


@pytest.mark.asyncio
async def test_server_registers_exact_tool_surface():
    expected = {
        "get_config_info",
        "get_sources",
        "switch_model",
        "toggle_builtin_tools",
        "web_fetch",
        "web_map",
        "web_search",
    }

    async with Client(server.mcp) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == expected
