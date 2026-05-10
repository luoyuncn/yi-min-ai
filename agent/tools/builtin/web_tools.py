"""Web search tool with Tavily primary search and DuckDuckGo fallback."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

logger = logging.getLogger(__name__)

DEFAULT_NUM_RESULTS = 5
MAX_RESULTS = 8
DEFAULT_TIMEOUT_SECONDS = 20


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    source_host: str = ""
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_host": self.source_host or _host_from_url(self.url),
        }
        if self.score is not None:
            payload["score"] = self.score
        return payload


class SearchProviderError(RuntimeError):
    """Raised when a search provider cannot return usable results."""


def web_search(
    query: str,
    num_results: int = DEFAULT_NUM_RESULTS,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    topic: str | None = None,
    time_range: str | None = None,
) -> str:
    """Search the web.

    Tavily is used first when ``TAVILY_API_KEY`` is configured. DuckDuckGo is
    retained as the no-key fallback so local development continues to work.
    """

    started = time.perf_counter()
    provider_chain: list[str] = []
    provider_errors: list[dict[str, str]] = []

    try:
        clean_query = _validate_query(query)
        limit = _coerce_num_results(num_results)
        allow_list = _normalize_domain_filters(allowed_domains)
        block_list = _normalize_domain_filters(blocked_domains)
        timeout = _timeout_seconds()

        hits: list[SearchHit] = []
        provider = ""
        for provider_name in _provider_order():
            provider_chain.append(provider_name)
            try:
                if provider_name == "tavily":
                    hits = _search_tavily(
                        clean_query,
                        limit=limit,
                        allowed_domains=allow_list,
                        blocked_domains=block_list,
                        topic=topic,
                        time_range=time_range,
                        timeout=timeout,
                    )
                elif provider_name == "duckduckgo":
                    hits = _search_duckduckgo(clean_query, limit=limit)
                else:
                    continue
            except SearchProviderError as exc:
                logger.warning("web_search provider failed provider=%s error=%s", provider_name, exc)
                provider_errors.append({"provider": provider_name, "message": str(exc)})
                continue

            hits = _filter_hits(
                hits,
                allowed_domains=allow_list,
                blocked_domains=block_list,
                limit=limit,
            )
            if hits:
                provider = provider_name
                break
            provider_errors.append({"provider": provider_name, "message": "provider returned no usable results"})

        elapsed = round(time.perf_counter() - started, 3)
        payload = {
            "query": clean_query,
            "provider": provider or None,
            "provider_chain": provider_chain,
            "fallback_used": len(provider_chain) > 1 and provider == "duckduckgo",
            "results": [hit.to_dict() for hit in hits],
            "duration_seconds": elapsed,
            "commentary": _build_commentary(clean_query, hits),
        }
        if provider_errors:
            payload["provider_errors"] = provider_errors
        if not hits and provider_errors:
            payload["error"] = {
                "type": "search_unavailable",
                "message": "All configured web search providers failed or returned no usable results.",
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("web_search failed query=%r error=%s", query, exc, exc_info=True)
        payload = {
            "query": query,
            "provider": None,
            "provider_chain": provider_chain,
            "results": [],
            "duration_seconds": round(time.perf_counter() - started, 3),
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "commentary": "Web search failed. Do not claim this answer was verified online.",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _search_tavily(
    query: str,
    *,
    limit: int,
    allowed_domains: list[str],
    blocked_domains: list[str],
    topic: str | None,
    time_range: str | None,
    timeout: float,
) -> list[SearchHit]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise SearchProviderError("TAVILY_API_KEY is not configured")

    endpoint = os.getenv("TAVILY_SEARCH_URL", "https://api.tavily.com/search").strip()
    body: dict[str, Any] = {
        "query": query,
        "max_results": limit,
        "search_depth": os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    if topic:
        body["topic"] = topic
    if time_range:
        body["time_range"] = time_range
    if allowed_domains:
        body["include_domains"] = allowed_domains
    if blocked_domains:
        body["exclude_domains"] = blocked_domains

    req = request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "yi-min-ai-web-search/0.1",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SearchProviderError(f"Tavily HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise SearchProviderError(f"Tavily network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SearchProviderError("Tavily request timed out") from exc

    if status_code < 200 or status_code >= 300:
        raise SearchProviderError(f"Tavily HTTP {status_code}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SearchProviderError("Tavily returned invalid JSON") from exc

    hits: list[SearchHit] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not _is_http_url(url):
            continue
        title = str(item.get("title") or url).strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        score = item.get("score")
        hits.append(
            SearchHit(
                title=title,
                url=url,
                snippet=snippet,
                source_host=_host_from_url(url),
                score=score if isinstance(score, (int, float)) else None,
            )
        )
    return hits


def _search_duckduckgo(query: str, *, limit: int) -> list[SearchHit]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError as exc:
            raise SearchProviderError("ddgs is not installed") from exc

    try:
        raw_results = list(DDGS().text(query, max_results=limit))
    except Exception as exc:  # pragma: no cover - provider library owns the details.
        raise SearchProviderError(f"DuckDuckGo search failed: {exc}") from exc

    hits: list[SearchHit] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("href") or item.get("url") or "").strip()
        if not _is_http_url(url):
            continue
        title = str(item.get("title") or url).strip()
        snippet = str(item.get("body") or item.get("snippet") or "").strip()
        hits.append(SearchHit(title=title, url=url, snippet=snippet, source_host=_host_from_url(url)))
    return hits


def _provider_order() -> list[str]:
    requested = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
    tavily_configured = bool(os.getenv("TAVILY_API_KEY", "").strip())
    if requested == "duckduckgo":
        return ["duckduckgo"]
    if requested in {"tavily", "auto", ""}:
        return ["tavily", "duckduckgo"] if tavily_configured else ["duckduckgo"]
    return [requested, "duckduckgo"]


def _validate_query(query: str) -> str:
    clean_query = str(query or "").strip()
    if len(clean_query) < 2:
        raise ValueError("web_search query must contain at least 2 characters")
    return clean_query


def _coerce_num_results(num_results: int | str | None) -> int:
    try:
        value = int(num_results) if num_results is not None else DEFAULT_NUM_RESULTS
    except (TypeError, ValueError):
        value = DEFAULT_NUM_RESULTS
    return max(1, min(value, MAX_RESULTS))


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _filter_hits(
    hits: list[SearchHit],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    limit: int,
) -> list[SearchHit]:
    filtered = hits
    if allowed_domains:
        filtered = [hit for hit in filtered if _host_matches_list(hit.url, allowed_domains)]
    if blocked_domains:
        filtered = [hit for hit in filtered if not _host_matches_list(hit.url, blocked_domains)]

    deduped: list[SearchHit] = []
    seen_urls: set[str] = set()
    for hit in filtered:
        key = _dedupe_url_key(hit.url)
        if not key or key in seen_urls:
            continue
        seen_urls.add(key)
        deduped.append(hit)
        if len(deduped) >= limit:
            break
    return deduped


def _normalize_domain_filters(domains: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for domain in domains or []:
        clean = _normalize_domain_filter(domain)
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


def _normalize_domain_filter(domain: str) -> str:
    text = str(domain or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        host = parse.urlparse(text).hostname or ""
    else:
        host = parse.urlparse(f"//{text}").hostname or text.split("/")[0].split(":")[0]
    return host.strip().strip(".").removeprefix("*.").removeprefix(".")


def _host_matches_list(url: str, domains: list[str]) -> bool:
    host = _host_from_url(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _host_from_url(url: str) -> str:
    try:
        return (parse.urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def _dedupe_url_key(url: str) -> str:
    try:
        parsed = parse.urlparse(url)
    except ValueError:
        return ""
    return parse.urlunparse(parsed._replace(fragment=""))


def _is_http_url(url: str) -> bool:
    try:
        return parse.urlparse(url).scheme in {"http", "https"}
    except ValueError:
        return False


def _build_commentary(query: str, hits: list[SearchHit]) -> str:
    if not hits:
        return f"No usable web search results were found for {query!r}."
    return (
        f"Search results for {query!r}. Use the result URLs as sources and include a "
        "Sources section in the final answer when the answer relies on these results."
    )
