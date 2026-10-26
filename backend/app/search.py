"""Web search via Tavily (https://docs.tavily.com). No LLM involved.

Every query belongs to a research function (sights, food, transport, ...). Preferred
websites per function come from backend/sources.json (editable by hand). Results are
cached in SQLite so repeated trips cost no credits.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import cache
from .config import SEARCH_RESULTS_PER_QUERY, TAVILY_API_KEY, TAVILY_CACHE
from .timing import step
from .models import TripRequest, Usage

API = "https://api.tavily.com/search"
SOURCES_FILE = Path(__file__).resolve().parents[1] / "sources.json"

Function = Literal["sights", "activities", "food", "accommodation", "daily_costs", "transport"]
FUNCTION_LABEL: dict[str, str] = {
    "sights": "Sights", "activities": "Activities & tours", "food": "Local food",
    "accommodation": "Accommodation prices", "daily_costs": "Daily costs (meals, local transport)",
    "transport": "Transport",
}


class SourceRule(BaseModel):
    mode: Literal["prefer", "restrict"] = "prefer"
    domains: list[str] = Field(default=[], max_length=300)


class SearchResult(BaseModel):
    title: str
    url: str
    content: str


class SearchError(RuntimeError):
    pass


def load_sources(path: Path = SOURCES_FILE) -> dict[str, SourceRule]:
    """Read sources.json fresh each time so hand edits apply without a restart."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        rules = {k: SourceRule.model_validate(v) for k, v in raw.items() if not k.startswith("_")}
    except (json.JSONDecodeError, ValidationError) as e:
        raise SearchError(f"backend/sources.json is invalid: {e}") from e
    unknown = set(rules) - set(FUNCTION_LABEL)
    if unknown:
        raise SearchError(f"backend/sources.json: unknown function(s) {sorted(unknown)}; "
                          f"allowed: {sorted(FUNCTION_LABEL)}")
    return rules


def place_queries(req: TripRequest, place: str) -> list[tuple[Function, str]]:
    year = (req.start_date or date.today()).year
    interests = " ".join(req.interests)
    return [
        ("sights", f"best sights in {place} for a {req.trip_type} trip {interests}".strip()),
        ("activities", f"{place} tours activities classes tickets prices"),
        ("food", f"{place} local food dishes you must try"),
        ("accommodation", f"{place} hotel prices per night hostel mid-range luxury {year}"),
        ("daily_costs", f"{place} travel costs {year} meal prices restaurant street food public transport taxi"),
    ]


def leg_queries(origin: str, destination: str) -> list[tuple[Function, str]]:
    return [
        ("transport", f"how to get from {origin} to {destination} train bus flight travel time"),
        ("transport", f"{origin} to {destination} ticket prices flight train bus"),
    ]


def tavily_params(query: str, rule: SourceRule | None) -> dict:
    params = {
        "query": query,
        "search_depth": "basic",
        "max_results": SEARCH_RESULTS_PER_QUERY,
        "chunks_per_source": 3,
        "include_answer": False,
        "include_raw_content": False,
    }
    if rule and rule.domains:
        params["include_domains"] = rule.domains
        params["include_domains_mode"] = rule.mode
    return params


async def search(
    client: httpx.AsyncClient, query: str, usage: Usage, rule: SourceRule | None = None
) -> list[SearchResult]:
    params = tavily_params(query, rule)
    key = cache.make_key("tavily", params, date.today().strftime("%Y-%m"))
    if TAVILY_CACHE and (hit := cache.get(key)) is not None:
        with step("tavily", query[:70], cache=True):
            return [SearchResult.model_validate(r) for r in json.loads(hit)]
    with step("tavily", query[:70], cache=False):
        r = await client.post(API, headers={"Authorization": f"Bearer {TAVILY_API_KEY}"}, json=params)
    if r.status_code in (401, 403):
        raise SearchError("Tavily rejected the API key (TAVILY_API_KEY in .env)")
    if r.status_code in (429, 432, 433):
        raise SearchError(f"Tavily limit reached (HTTP {r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    usage.search_credits += int((data.get("usage") or {}).get("credits", 1))
    results = [
        SearchResult(title=x.get("title", ""), url=x.get("url", ""), content=x.get("content", ""))
        for x in data.get("results", [])
    ]
    cache.put(key, json.dumps([x.model_dump() for x in results]))
    return results


async def search_many(queries: list[tuple[Function, str]], usage: Usage) -> dict[str, list[SearchResult]]:
    """Run queries in parallel; results grouped by function, de-duplicated by URL."""
    rules = load_sources()
    async with httpx.AsyncClient(timeout=30) as client:
        batches = await asyncio.gather(*(search(client, q, usage, rules.get(fn)) for fn, q in queries))
    seen: set[str] = set()
    grouped: dict[str, list[SearchResult]] = {}
    for (fn, _q), batch in zip(queries, batches):
        for res in batch:
            if res.url not in seen:
                seen.add(res.url)
                grouped.setdefault(fn, []).append(res)
    return grouped


def all_urls(grouped: dict[str, list[SearchResult]]) -> list[str]:
    return [r.url for results in grouped.values() for r in results]


def numbered_urls(grouped: dict[str, list[SearchResult]], functions: list[str] | None = None) -> list[str]:
    """URLs in the order `as_context` numbers them: entry n-1 = search result [n] (for the LLM's sources)."""
    return [r.url for fn, rs in grouped.items() if functions is None or fn in functions for r in rs]


def as_context(grouped: dict[str, list[SearchResult]], functions: list[str] | None = None,
               max_chars: int | None = None) -> str:
    """Compact text for the LLM prompt, one section per research function.

    `functions` limits the sections; `max_chars` cuts every snippet evenly so the whole text fits
    (used when a request has to be shrunk for Groq's tokens-per-minute limit)."""
    groups = {fn: rs for fn, rs in grouped.items() if functions is None or fn in functions}
    per_result = None
    if max_chars:
        count = sum(len(rs) for rs in groups.values()) or 1
        overhead = sum(len(r.title) + len(r.url) + 20 for rs in groups.values() for r in rs) + 40 * len(groups)
        per_result = max((max_chars - overhead) // count, 0)
    parts, n = [], 0
    for fn, results in groups.items():
        parts.append(f"## {FUNCTION_LABEL.get(fn, fn)} sources")
        for r in results:
            n += 1
            content = r.content if per_result is None else r.content[:per_result]
            parts.append(f"[{n}] {r.title}\nURL: {r.url}\n{content}")
    return "\n\n".join(parts)
