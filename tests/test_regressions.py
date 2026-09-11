import asyncio

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import grok_search.server as server
from grok_search.providers.grok import GrokSearchProvider


@pytest.mark.asyncio
async def test_web_search_reports_grok_failure(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "false")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    calls = []

    async def fail_search(self, query, platform=""):
        calls.append((query, platform))
        raise RuntimeError("synthetic Grok failure")

    monkeypatch.setattr(GrokSearchProvider, "search", fail_search)

    with pytest.raises(RuntimeError, match="synthetic Grok failure"):
        await server.web_search("test query", platform="GitHub")
    assert calls == [("test query", "GitHub")]


@pytest.mark.asyncio
async def test_provider_local_timeout_error_propagates(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "false")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def fail_search(self, query, platform=""):
        raise asyncio.TimeoutError("provider-local timeout")

    monkeypatch.setattr(GrokSearchProvider, "search", fail_search)

    with pytest.raises(asyncio.TimeoutError, match="provider-local timeout"):
        await server.web_search("query")


@pytest.mark.asyncio
async def test_grok_search_uses_one_attempt_while_fetch_keeps_default(monkeypatch):
    calls = []

    async def capture(self, headers, payload, ctx=None, total_attempts=None):
        calls.append(total_attempts)
        return "ok"

    monkeypatch.setattr(GrokSearchProvider, "_execute_stream_with_retry", capture)
    provider = GrokSearchProvider("https://grok.invalid", "test-grok-key", "test-model")

    assert await provider.search("query") == "ok"
    assert await provider.fetch("https://example.invalid") == "ok"
    assert calls == [1, None]


@pytest.mark.asyncio
async def test_invalid_explicit_model_is_rejected_before_provider_coroutines(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    provider_calls = []

    async def available_models(*args, **kwargs):
        return ["known-model"]

    def unexpected_grok_search(*args, **kwargs):
        provider_calls.append("grok")
        raise AssertionError("Grok coroutine must not be created for an invalid model")

    def unexpected_tavily_search(*args, **kwargs):
        provider_calls.append("tavily")
        raise AssertionError("Tavily coroutine must not be created for an invalid model")

    monkeypatch.setattr(server, "_get_available_models_cached", available_models)
    monkeypatch.setattr(GrokSearchProvider, "search", unexpected_grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", unexpected_tavily_search)

    result = await server.web_search("query", model="invalid-model", extra_sources=1)

    assert provider_calls == []
    assert set(result) == {"session_id", "content", "sources_count"}
    assert result["sources_count"] == 0
    assert "invalid-model" in result["content"]
    assert "content_source" not in result


@pytest.mark.asyncio
async def test_web_search_runs_grok_and_tavily_concurrently_and_grok_wins(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    grok_started = asyncio.Event()
    tavily_started = asyncio.Event()
    tavily_calls = []

    async def grok_search(self, query, platform=""):
        grok_started.set()
        await tavily_started.wait()
        return "Grok answer"

    async def tavily_search(query, max_results=6):
        tavily_calls.append((query, max_results))
        tavily_started.set()
        await grok_started.wait()
        return [{"title": "Tavily", "url": "https://tavily.invalid/1", "content": "excerpt"}]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)

    result = await asyncio.wait_for(server.web_search("query", extra_sources=1), timeout=0.5)
    cached = await server.get_sources(result["session_id"])

    assert result == {
        "session_id": result["session_id"],
        "content": "Grok answer",
        "sources_count": 1,
        "content_source": "grok",
    }
    assert tavily_calls == [("query", 5)]
    assert cached["sources"][0]["provider"] == "tavily"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_sources", [None, 0])
async def test_tavily_standby_falls_back_with_default_or_zero_extras(monkeypatch, extra_sources):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    tavily_calls = []

    async def grok_search(self, query, platform=""):
        return ""

    async def tavily_search(query, max_results=6):
        tavily_calls.append((query, max_results))
        return [{"title": "Standby", "url": "https://tavily.invalid/standby", "content": "fallback"}]

    async def unexpected_firecrawl_search(query, limit=14):
        raise AssertionError("Firecrawl must not run with zero supplementary sources")

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)
    monkeypatch.setattr(server, "_call_firecrawl_search", unexpected_firecrawl_search)

    kwargs = {} if extra_sources is None else {"extra_sources": extra_sources}
    result = await server.web_search("query", **kwargs)
    cached = await server.get_sources(result["session_id"])

    assert result["content_source"] == "tavily_search_fallback"
    assert result["sources_count"] == 0
    assert "fallback" in result["content"]
    assert tavily_calls == [("query", 5)]
    assert cached["sources"] == []


@pytest.mark.asyncio
async def test_tavily_standby_is_not_exposed_when_grok_wins_with_zero_extras(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    tavily_calls = []

    async def grok_search(self, query, platform=""):
        return "Grok answer"

    async def tavily_search(query, max_results=6):
        tavily_calls.append((query, max_results))
        return [{"title": "Standby", "url": "https://tavily.invalid/standby", "content": "hidden"}]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)

    result = await server.web_search("query", extra_sources=0)
    cached = await server.get_sources(result["session_id"])

    assert result == {
        "session_id": result["session_id"],
        "content": "Grok answer",
        "sources_count": 0,
        "content_source": "grok",
    }
    assert tavily_calls == [("query", 5)]
    assert cached["sources"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("grok_result,parsed_blank", [("", False), ("source-only", True)])
async def test_blank_grok_uses_raw_tavily_fallback_and_caches_sources(
    monkeypatch, grok_result, parsed_blank
):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def grok_search(self, query, platform=""):
        return grok_result

    async def tavily_search(query, max_results=6):
        return [{"title": " Raw title ", "url": " https://tavily.invalid/1 ", "content": " Raw excerpt "}]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)
    if parsed_blank:
        monkeypatch.setattr(
            server,
            "split_answer_and_sources",
            lambda value: ("  ", [{"url": "https://grok.invalid/source"}]),
        )

    result = await server.web_search("query", extra_sources=1)
    cached = await server.get_sources(result["session_id"])

    assert result["content_source"] == "tavily_search_fallback"
    assert result["content"].startswith(
        "Tavily search fallback\n\nUntrusted search excerpts, not a Grok-generated answer"
    )
    assert "Raw excerpt" in result["content"]
    assert result["content"].strip()
    assert any(source.get("provider") == "tavily" for source in cached["sources"])


@pytest.mark.asyncio
async def test_grok_deadline_uses_completed_tavily(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(server, "_GROK_SEARCH_DEADLINE_SECONDS", 0.001)

    async def grok_search(self, query, platform=""):
        await asyncio.Event().wait()

    async def tavily_search(query, max_results=6):
        return [{"title": "Tavily", "url": "https://tavily.invalid/1", "content": "ready"}]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)

    result = await server.web_search("query", extra_sources=1)

    assert result["content_source"] == "tavily_search_fallback"
    assert "ready" in result["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.HTTPStatusError(
            "status",
            request=httpx.Request("POST", "https://grok.invalid"),
            response=httpx.Response(403),
        ),
        httpx.TransportError("transport"),
        httpx.ConnectError("connect"),
        httpx.ReadError("read"),
        httpx.RemoteProtocolError("protocol"),
    ],
)
async def test_grok_http_errors_use_tavily_fallback(monkeypatch, error):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def grok_search(self, query, platform=""):
        raise error

    async def tavily_search(query, max_results=6):
        return [{"title": "Tavily", "url": "https://tavily.invalid/1", "content": "fallback"}]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)

    result = await server.web_search("query", extra_sources=1)

    assert result["content_source"] == "tavily_search_fallback"


@pytest.mark.asyncio
async def test_unexpected_grok_return_type_propagates(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "false")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def grok_search(self, query, platform=""):
        return {"content": "not a string"}

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)

    with pytest.raises(TypeError, match="non-string"):
        await server.web_search("query")


@pytest.mark.asyncio
@pytest.mark.parametrize("grok_result", ["", httpx.ConnectError("connect")])
async def test_unavailable_grok_without_usable_allocated_tavily_is_tool_error(
    monkeypatch, grok_result
):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def grok_search(self, query, platform=""):
        if isinstance(grok_result, BaseException):
            raise grok_result
        return grok_result

    async def tavily_search(query, max_results=6):
        return [
            {"title": "missing URL", "url": " ", "content": "excerpt"},
            {"title": " ", "url": "https://empty.invalid", "content": " "},
        ]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)

    with pytest.raises(ToolError, match="no Tavily fallback"):
        await server.web_search("query", extra_sources=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_slot", ["grok", "tavily", "firecrawl"])
async def test_web_search_propagates_child_cancellation(monkeypatch, cancelled_slot):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", str(cancelled_slot == "tavily").lower())
    if cancelled_slot == "tavily":
        monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    else:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    if cancelled_slot == "firecrawl":
        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key")
    else:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def grok_search(self, query, platform=""):
        if cancelled_slot == "grok":
            raise asyncio.CancelledError()
        return "Grok answer"

    async def tavily_search(query, max_results=6):
        raise asyncio.CancelledError()

    async def firecrawl_search(query, limit=14):
        raise asyncio.CancelledError()

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)
    monkeypatch.setattr(server, "_call_firecrawl_search", firecrawl_search)

    with pytest.raises(asyncio.CancelledError):
        await server.web_search("query", extra_sources=1)


@pytest.mark.asyncio
async def test_outer_cancellation_cancels_unfinished_children(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    grok_started = asyncio.Event()
    tavily_started = asyncio.Event()
    grok_cancelled = asyncio.Event()
    tavily_cancelled = asyncio.Event()

    async def grok_search(self, query, platform=""):
        grok_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            grok_cancelled.set()

    async def tavily_search(query, max_results=6):
        tavily_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            tavily_cancelled.set()

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)
    task = asyncio.create_task(server.web_search("query", extra_sources=1))
    await asyncio.wait_for(
        asyncio.gather(grok_started.wait(), tavily_started.wait()),
        timeout=0.5,
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert grok_cancelled.is_set()
    assert tavily_cancelled.is_set()


@pytest.mark.asyncio
async def test_all_firecrawl_allocation_keeps_standby_out_of_cached_sources(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://grok.invalid")
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key")
    monkeypatch.setattr(server, "new_session_id", lambda: "firecrawl-only-session")
    firecrawl_calls = []
    tavily_calls = []

    async def grok_search(self, query, platform=""):
        return ""

    async def tavily_search(query, max_results=6):
        tavily_calls.append((query, max_results))
        return [{"title": "Tavily", "url": "https://tavily.invalid/1", "content": "fallback"}]

    async def firecrawl_search(query, limit=14):
        firecrawl_calls.append((query, limit))
        return [{"title": "Firecrawl", "url": "https://firecrawl.invalid/1", "description": "must not become fallback"}]

    monkeypatch.setattr(GrokSearchProvider, "search", grok_search)
    monkeypatch.setattr(server, "_call_tavily_search", tavily_search)
    monkeypatch.setattr(server, "_call_firecrawl_search", firecrawl_search)

    result = await server.web_search("query", extra_sources=3)

    cached = await server.get_sources("firecrawl-only-session")
    assert result["content_source"] == "tavily_search_fallback"
    assert result["sources_count"] == 1
    assert "fallback" in result["content"]
    assert tavily_calls == [("query", 5)]
    assert firecrawl_calls == [("query", 3)]
    assert any(
        source.get("provider") == "firecrawl"
        and source.get("description") == "must not become fallback"
        for source in cached["sources"]
    )
    assert all(source.get("provider") != "tavily" for source in cached["sources"])


def test_tavily_fallback_formatter_filters_dedupes_and_caps_raw_content():
    results = [
        {"title": "ignored", "url": "   ", "content": "ignored"},
        {"title": " ", "url": "https://empty.invalid", "content": " "},
        {
            "title": " First ",
            "url": " https://same.invalid ",
            "content": " raw-content ",
            "description": "merged-firecrawl-description",
        },
        {"title": "duplicate", "url": "https://same.invalid", "content": "duplicate"},
        {"title": "Second", "url": "https://second.invalid", "content": "x" * 1500 + "TAIL"},
        *[
            {"title": f"Bulk {index}", "url": f"https://bulk.invalid/{index}", "content": "y" * 1500}
            for index in range(6)
        ],
    ]

    formatted = server._format_tavily_search_fallback(results)

    assert formatted is not None
    assert formatted.startswith(
        "Tavily search fallback\n\nUntrusted search excerpts, not a Grok-generated answer"
    )
    assert formatted.count("https://same.invalid") == 1
    assert "https://empty.invalid" not in formatted
    assert formatted.index("https://same.invalid") < formatted.index("https://second.invalid")
    assert "raw-content" in formatted
    assert "merged-firecrawl-description" not in formatted
    assert "TAIL" not in formatted
    assert len(formatted) == 8000


@pytest.mark.asyncio
async def test_tavily_search_and_extract_whole_deadlines_return_none(monkeypatch):
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(server, "_TAVILY_OPERATION_DEADLINE_SECONDS", 0.001)
    client_timeouts = []
    bodies = []

    class HangingAsyncClient:
        def __init__(self, *args, **kwargs):
            client_timeouts.append(kwargs["timeout"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            bodies.append(kwargs["json"])
            await asyncio.Event().wait()

    monkeypatch.setattr(httpx, "AsyncClient", HangingAsyncClient)

    assert await server._call_tavily_search("query", 2) is None
    assert await server._call_tavily_extract("https://example.invalid") is None
    assert client_timeouts == [0.001, 0.001]
    assert bodies[0]["max_results"] == 2
    assert bodies[1] == {"urls": ["https://example.invalid"], "format": "markdown"}


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(150, 30.0), (12, 12.0)])
async def test_tavily_map_clamps_body_client_and_whole_timeout(
    monkeypatch, requested, expected
):
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    client_timeouts = []
    bodies = []
    overall_timeouts = []
    real_wait_for = asyncio.wait_for

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"base_url": "https://example.invalid", "results": [], "response_time": 0}

    class RecordingAsyncClient:
        def __init__(self, *args, **kwargs):
            client_timeouts.append(kwargs["timeout"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            bodies.append(kwargs["json"])
            return Response()

    async def recording_wait_for(awaitable, timeout):
        overall_timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout=0.5)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    monkeypatch.setattr(server.asyncio, "wait_for", recording_wait_for)

    result = await server._call_tavily_map("https://example.invalid", timeout=requested)

    assert "https://example.invalid" in result
    assert bodies[0]["timeout"] == expected
    assert client_timeouts == [expected]
    assert overall_timeouts == [expected]


@pytest.mark.asyncio
async def test_tavily_map_timeout_reports_effective_timeout(monkeypatch):
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")

    async def timeout_wait_for(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(server.asyncio, "wait_for", timeout_wait_for)

    assert await server._call_tavily_map("https://example.invalid", timeout=150) == "映射超时: 请求超过30.0秒"


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
    web_search_tool = next(tool for tool in tools if tool.name == "web_search")
    assert set(web_search_tool.inputSchema["properties"]) == {
        "query",
        "platform",
        "model",
        "extra_sources",
    }
    assert web_search_tool.inputSchema["required"] == ["query"]
    assert web_search_tool.inputSchema["properties"]["platform"]["default"] == ""
    assert web_search_tool.inputSchema["properties"]["model"]["default"] == ""
    assert web_search_tool.inputSchema["properties"]["extra_sources"]["default"] == 0
