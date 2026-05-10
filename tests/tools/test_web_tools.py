import json
import sys
from types import SimpleNamespace
from urllib import error as urlerror

from agent.tools.builtin import web_tools
from agent.tools.builtin.web_tools import SearchHit


class FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_web_search_uses_tavily_and_applies_domain_filters(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    captured = {}

    def fake_urlopen(req, timeout):
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(
            {
                "results": [
                    {
                        "title": "Python Docs",
                        "url": "https://docs.python.org/3/library/json.html",
                        "content": "JSON module documentation.",
                        "score": 0.9,
                    },
                    {
                        "title": "Blocked",
                        "url": "https://example.com/python",
                        "content": "Should be filtered out.",
                    },
                ]
            }
        )

    monkeypatch.setattr(web_tools.request, "urlopen", fake_urlopen)

    result = json.loads(
        web_tools.web_search(
            "python json",
            num_results=5,
            allowed_domains=["https://DOCS.python.org/"],
            blocked_domains=["example.com"],
        )
    )

    assert result["provider"] == "tavily"
    assert result["results"] == [
        {
            "title": "Python Docs",
            "url": "https://docs.python.org/3/library/json.html",
            "snippet": "JSON module documentation.",
            "source_host": "docs.python.org",
            "score": 0.9,
        }
    ]
    assert captured["body"]["include_domains"] == ["docs.python.org"]
    assert captured["body"]["exclude_domains"] == ["example.com"]


def test_web_search_falls_back_to_duckduckgo_when_tavily_fails(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    def fake_urlopen(req, timeout):
        raise urlerror.URLError("offline")

    class FakeDDGS:
        def text(self, query, max_results):
            return [
                {
                    "title": "Fallback Result",
                    "href": "https://fallback.example/search",
                    "body": "From DuckDuckGo.",
                }
            ]

    monkeypatch.setattr(web_tools.request, "urlopen", fake_urlopen)
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=lambda: FakeDDGS()))

    result = json.loads(web_tools.web_search("fallback query", num_results=5))

    assert result["provider"] == "duckduckgo"
    assert result["fallback_used"] is True
    assert result["provider_chain"] == ["tavily", "duckduckgo"]
    assert result["provider_errors"][0]["provider"] == "tavily"
    assert result["results"][0]["url"] == "https://fallback.example/search"


def test_web_search_uses_duckduckgo_directly_without_tavily_key(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    class FakeDDGS:
        def text(self, query, max_results):
            return [
                {
                    "title": "Local Fallback",
                    "href": "https://duck.example/result",
                    "body": "No Tavily key needed.",
                }
            ]

    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=lambda: FakeDDGS()))

    result = json.loads(web_tools.web_search("local search", num_results=5))

    assert result["provider"] == "duckduckgo"
    assert result["provider_chain"] == ["duckduckgo"]
    assert "provider_errors" not in result


def test_filter_hits_dedupes_and_limits_results() -> None:
    hits = [
        SearchHit("A", "https://docs.rs/one#intro"),
        SearchHit("A duplicate", "https://docs.rs/one#other"),
        SearchHit("B", "https://sub.docs.rs/two"),
        SearchHit("Blocked", "https://example.com/nope"),
    ]

    filtered = web_tools._filter_hits(
        hits,
        allowed_domains=["docs.rs"],
        blocked_domains=["example.com"],
        limit=1,
    )

    assert [hit.url for hit in filtered] == ["https://docs.rs/one#intro"]
