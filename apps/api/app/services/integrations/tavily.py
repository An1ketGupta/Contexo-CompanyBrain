"""Tavily web-search adapter.

The Sales Agent's research stage runs KB search first. When KB returns no
usable signal on a prospect company we fall back to Tavily to fetch fresh
public information (company overview, headcount hints, tech mentions).

Tavily docs: https://docs.tavily.com/api/search

Why Tavily over Perplexity / Brave / Google:
  * AI-optimized snippets — every result has a clean `content` field
    we feed directly to the LLM without an extra scrape step.
  * Includes `answer` shortcut for the most common one-shot questions,
    saving an LLM round-trip for simple company briefs.
  * ~$0.005/search at low volume; the agent calls this at most once
    per deal at lead-entry time.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_DEFAULT_TIMEOUT = 25.0


class TavilyError(RuntimeError):
    """Raised on any non-2xx Tavily response or transport error."""


class TavilyUnavailable(RuntimeError):
    """Raised when TAVILY_API_KEY is empty — callers should treat this as
    'no web fallback configured' and proceed gracefully, not as a hard error."""


async def search(
    query: str,
    *,
    search_depth: Literal["basic", "advanced"] = "basic",
    max_results: int = 5,
    include_answer: bool = True,
    include_raw_content: bool = False,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run a Tavily web search.

    Args:
        query: The natural-language search query. Keep it focused — Tavily
            does best with the shape of a real user question.
        search_depth: "basic" (single search round) is fast & cheap; "advanced"
            does an extra synthesis pass and roughly doubles latency.
        max_results: 1-10. Returned snippets capped at this.
        include_answer: If True, Tavily synthesises a short answer over the
            top results. Useful for one-shot company-brief queries.
        include_raw_content: Include the full page bodies (slow + large).
            Defaults to False since we trust the snippets.

    Returns:
        Parsed Tavily response: {answer, results: [{title, url, content, score}]}

    Raises:
        TavilyUnavailable: TAVILY_API_KEY is empty.
        TavilyError: HTTP error or malformed response.
    """
    settings = get_settings()
    api_key = settings.tavily_api_key
    if not api_key:
        raise TavilyUnavailable("TAVILY_API_KEY is not configured")

    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "max_results": max(1, min(int(max_results), 10)),
        "include_answer": bool(include_answer),
        "include_raw_content": bool(include_raw_content),
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout or _DEFAULT_TIMEOUT)) as client:
            resp = await client.post(_TAVILY_URL, json=payload)
    except httpx.TimeoutException as exc:
        raise TavilyError(f"Tavily timed out after {timeout or _DEFAULT_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        raise TavilyError(f"Tavily transport error: {exc}") from exc

    if resp.status_code == 401:
        raise TavilyError("Tavily authentication failed — check TAVILY_API_KEY")
    if resp.status_code == 429:
        raise TavilyError("Tavily rate limit exceeded")
    if resp.status_code >= 400:
        raise TavilyError(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise TavilyError("Tavily returned non-JSON payload") from exc

    return data


async def company_brief(
    *,
    company_name: str,
    company_website: str | None = None,
) -> dict[str, Any]:
    """Convenience: fan out a small bundle of company-research queries in
    parallel and return a normalized summary.

    Used by the Sales Agent's research stage as the web fallback.
    """
    queries: list[tuple[str, str]] = [
        ("overview", f"{company_name} company overview industry size"),
        ("tech_stack", f"{company_name} engineering tech stack tools"),
    ]
    if company_website:
        # Constrain one query to their own site so we pick up the about page.
        queries.append(("about_page", f"{company_name} about us mission"))

    async def _run(label: str, q: str) -> tuple[str, dict[str, Any] | None]:
        try:
            res = await search(q, max_results=4, include_answer=True)
            return label, res
        except TavilyUnavailable:
            raise
        except TavilyError as exc:
            log.warning("tavily.query_failed label=%s err=%s", label, exc)
            return label, None

    try:
        results = await asyncio.gather(*[_run(label, q) for label, q in queries])
    except TavilyUnavailable:
        raise

    bundle: dict[str, Any] = {q[0]: None for q in queries}
    raw_sources: list[dict[str, Any]] = []
    answers: list[str] = []

    for label, res in results:
        if not res:
            continue
        bundle[label] = res
        ans = res.get("answer")
        if ans:
            answers.append(f"[{label}] {ans}")
        for r in (res.get("results") or [])[:3]:
            raw_sources.append(
                {
                    "type": "web",
                    "label": label,
                    "url": r.get("url"),
                    "title": r.get("title"),
                    "snippet": (r.get("content") or "")[:600],
                    "score": r.get("score"),
                }
            )

    return {
        "answers": answers,
        "raw_sources": raw_sources,
        "raw": bundle,
    }
