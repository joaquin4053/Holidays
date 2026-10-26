"""Planning pipeline: Tavily search + live booking pages (Crawl4AI) -> LLM extraction -> TripResearch.

Nights per place and the day -> place mapping are computed in Python; live prices are read
without an LLM (live_extract.py). The LLM turns snippets + price summaries into structured
places/legs and writes the day plan. When only Groq is available and a request is too big for
its tokens-per-minute limit, requests are split (place: info + prices; days: halves) or shortened.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from .. import currency, live_prices, price_tiers, search
from ..config import LLM_SINGLE_REQUEST
from ..llm import LLMError, RequestTooLarge, complete_json, estimate_tokens
from ..timing import step, track
from ..models import (
    Day,
    DayDraft,
    DaysDraft,
    Leg,
    LegDraft,
    NightsAt,
    Place,
    PlaceDraft,
    PlaceInfoDraft,
    PlacePricesDraft,
    TierPrices,
    TransportOption,
    TransportOptionDraft,
    TripDraft,
    TripDraftWithPrices,
    TripRequest,
    TripResearch,
    Usage,
)

Emit = Callable[[str], None]

EXTRACT_SYSTEM = """You turn web search snippets into structured travel data (JSON). \
All prices in USD (convert local currency). Price ranges must be TYPICAL - what most \
travellers pay when booking a few weeks ahead (about the 25th-75th percentile), never \
stretched to cover promo fares or last-minute peaks. If the snippets lack a number, give a \
realistic conservative estimate from your general knowledge. Keep descriptions short."""

DAYS_SYSTEM = """You write a realistic day-by-day travel itinerary as JSON, using only the \
attraction ids you are given. Keep titles short and notes practical."""


# ---------------------------------------------------------------------------
# Deterministic parts
# ---------------------------------------------------------------------------

def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def legs_for(req: TripRequest) -> list[tuple[str, str]]:
    route = [req.origin] + [s.name for s in req.stops]
    if req.return_to_origin:
        route.append(req.origin)
    return [(a, b) for a, b in zip(route, route[1:]) if a.strip().lower() != b.strip().lower()]


def distribute_nights(req: TripRequest) -> list[NightsAt]:
    """Fixed nights stay; the rest is split evenly (earlier stops get the remainder)."""
    free = [s for s in req.stops if not s.nights]
    left = req.total_nights - sum(s.nights or 0 for s in req.stops)
    share, extra = divmod(max(left, 0), len(free)) if free else (0, 0)
    out = []
    for s in req.stops:
        if s.nights:
            n = s.nights
        else:
            n = share + (1 if extra > 0 else 0)
            extra -= 1
        out.append(NightsAt(place=s.name, nights=n))
    return out


def stay_dates(req: TripRequest, nights: list[NightsAt]) -> dict[str, tuple[date, date]]:
    """Check-in/check-out per place (first visit); empty without a start date."""
    if not req.start_date:
        return {}
    out: dict[str, tuple[date, date]] = {}
    day = req.start_date
    for n in nights:
        end = day + timedelta(days=n.nights)
        out.setdefault(n.place, (day, end))
        day = end
    return out


def leg_date(req: TripRequest, stays: dict[str, tuple[date, date]], destination: str) -> date | None:
    """Travel day of a leg: check-in at the destination, or the last day when returning home."""
    if destination in stays:
        return stays[destination][0]
    return req.start_date and req.start_date + timedelta(days=req.total_nights)


def _live_section(results: list[live_prices.LiveResult], intro: str, max_chars: int | None = None) -> str:
    text = live_prices.as_context(results, max_chars)
    return f"\n\nLive prices from booking sites, read today. {intro}\n{text}" if text else ""


def _fit(task: str, results: dict, functions: list[str] | None, live: list[live_prices.LiveResult],
         intro: str, max_chars: int | None) -> str:
    """Task text + search snippets + live price summaries; within max_chars when given (Groq)."""
    with step("prep", task.splitlines()[0][:60]) as t:
        prompt = _fit_text(task, results, functions, live, intro, max_chars)
        t["chars"] = len(prompt)
    return prompt


def _fit_text(task: str, results: dict, functions: list[str] | None, live: list[live_prices.LiveResult],
              intro: str, max_chars: int | None) -> str:
    if max_chars is None:
        return f"{task}\n\nSearch results:\n{search.as_context(results, functions)}{_live_section(live, intro)}"
    room = max(max_chars - len(task) - 40, 500)
    live_text = _live_section(live, intro, room // 2)  # live first: short and date-specific
    snippets = search.as_context(results, functions, room - len(live_text))
    return f"{task}\n\nSearch results:\n{snippets}{live_text}"


async def _usd_rates(results: list[live_prices.LiveResult]) -> dict[str, float]:
    """Units per USD for the currencies on the live pages (CHF on Hostelworld/Trainline, EUR car rental)."""
    codes = sorted(price_tiers.currencies(results))
    rates = await asyncio.gather(*(currency.usd_rate(c) for c in codes), return_exceptions=True)
    return {c: r for c, r in zip(codes, rates) if isinstance(r, float)}


async def _ask(system: str, build: Callable[[int | None], str], model, usage: Usage):
    """One request; shortened to fit when only Groq is free and the full prompt is too big for it."""
    try:
        return await complete_json(system, build(None), model, usage)
    except RequestTooLarge as e:
        return await complete_json(system, build(e.max_chars), model, usage)


def day_skeleton(req: TripRequest, nights: list[NightsAt]) -> list[tuple[int, str, str]]:
    """(day, place, kind) for every day; kind is 'arrival', 'full' or 'departure'."""
    days: list[tuple[int, str, str]] = []
    for n in nights:
        for i in range(n.nights):
            days.append((len(days) + 1, n.place, "arrival" if i == 0 else "full"))
    last = nights[-1].place if nights else req.stops[-1].name
    while len(days) < req.total_days:
        days.append((len(days) + 1, last, "departure"))
    return days[: req.total_days]


def _finalize_place(draft: PlaceDraft, place_name: str, urls: list[str],
                    price_sources: dict[str, str] | None = None) -> Place:
    """Stable unique attraction ids, canonical place name, sources from search."""
    seen: set[str] = set()
    attractions = []
    for a in draft.attractions:
        aid = f"{slug(place_name)}-{slug(a.name)}"
        while aid in seen:
            aid += "-2"
        seen.add(aid)
        free = a.price.max <= 0
        attractions.append({**a.model_dump(), "id": aid, "price": None if free else a.price.model_dump()})
    data = draft.model_dump()
    data.update(name=place_name, attractions=attractions, sources=urls[:15], price_sources=price_sources or {})
    return Place.model_validate(data)


# ---------------------------------------------------------------------------
# LLM parts
# ---------------------------------------------------------------------------

PLACE_INFO_TASK = """- known_for (1-2 sentences), summary (2-3 sentences)
- attractions: 10-14 best sights (kind 'sight') and activities (kind 'activity') for this trip \
type and these interests; price = ticket/entry/tour price per person in USD. Most museums, \
monuments, tours and classes cost money - use 0-0 ONLY for things that are genuinely free \
(squares, fountains, parks, free churches, walking around a neighbourhood).
- local_food: 4-6 typical dishes or food experiences
"""
PLACE_PRICES_TASK = """- accommodation_per_night per double room: budget (hostel private room/guesthouse), \
comfort (3-4 star), luxury (5 star)
- meal_cost per person per meal: budget (street food), comfort (mid-range), luxury (upscale)
- local_transport_per_day per person: budget (public transport), comfort (mix with taxis), \
luxury (taxis/private driver)
"""


@dataclass
class PlaceData:
    """Everything collected for one destination before the LLM is asked."""
    name: str
    results: dict
    live: list[live_prices.LiveResult]
    checkin: date | None
    checkout: date | None
    fixed: dict[str, TierPrices]  # price tiers computed in Python from live pages

    @property
    def urls(self) -> list[str]:
        return live_prices.ok_urls(self.live) + search.all_urls(self.results)

    @property
    def price_sources(self) -> dict[str, str]:
        """Live search page for computed tiers, else the first Tavily result the LLM read for that kind of price."""
        tavily = {f: rs[0].url for f, fn in (("accommodation_per_night", "accommodation"), ("meal_cost", "daily_costs"),
                                              ("local_transport_per_day", "daily_costs"))
                  if (rs := self.results.get(fn))}
        return {**tavily, **price_tiers.place_price_sources(self.live, set(self.fixed))}


@dataclass
class LegData:
    a: str
    b: str
    day: date | None
    results: dict
    live: list[live_prices.LiveResult]
    fixed: dict[str, TransportOption]  # flight / bus computed in Python from live pages

    @property
    def urls(self) -> list[str]:
        return (live_prices.ok_urls(self.live) + search.all_urls(self.results))[:10]


async def _collect_place(req: TripRequest, name: str, usage: Usage, emit: Emit,
                         live: live_prices.LiveBrowser | None, stay: tuple[date, date] | None) -> PlaceData:
    emit(f"Searching the web{' and booking sites' if live else ''} for {name}…")
    checkin, checkout = stay or (None, None)
    results, live_results = await asyncio.gather(
        search.search_many(search.place_queries(req, name), usage),
        live_prices.place_prices(live, name, checkin, checkout, req.travelers, usage) if live else _nothing(),
    )
    fixed = price_tiers.place_prices(live_results, await _usd_rates(live_results), req.travelers) if live_results else {}
    return PlaceData(name, results, live_results, checkin, checkout, fixed)


async def _collect_leg(req: TripRequest, a: str, b: str, usage: Usage, emit: Emit,
                       live: live_prices.LiveBrowser | None, day: date | None) -> LegData:
    emit(f"Searching connections {a} → {b}…")
    results, live_results = await asyncio.gather(
        search.search_many(search.leg_queries(a, b), usage),
        live_prices.leg_prices(live, a, b, day, req.travelers) if live else _nothing(),
    )
    fixed = price_tiers.leg_options(live_results, await _usd_rates(live_results)) if live_results else {}
    return LegData(a, b, day, results, live_results, fixed)


def _place_head(req: TripRequest, name: str) -> str:
    return f"""Destination: {name}
Trip type: {req.trip_type}; interests: {", ".join(req.interests) or "none given"}; \
travel month: {req.start_date or 'unknown'}

Fill in:
"""


def _place_intro(req: TripRequest, d: PlaceData) -> str:
    return (f"{f'Stay {d.checkin} to {d.checkout}, ' if d.checkin else ''}{req.travelers} traveller(s). Prefer these "
            "over the search snippets for accommodation. Hotel/home prices are per room or home per night; "
            "hostel 'from' prices may be per bed. Convert to USD per double room per night.")


LEG_TASK = """List every realistic way to travel: flight, train (incl. high-speed/night trains), bus, \
ferry, car - skip options that do not exist. For each: mode, short description \
(operator/route/stations or airports), door-to-door duration in hours (incl. getting to the \
airport/station, check-in, transfers), typical price per person one way in USD, source = number [n] of \
the search result in that leg's section the price comes from (null if none). Never add an entry for a \
mode that doesn't exist, and never use 0 hours or 0 price."""


def _leg_known(d: LegData) -> str:
    if not d.fixed:
        return ""
    return (f"{' and '.join(d.fixed)} {'is' if len(d.fixed) == 1 else 'are'} already priced from live booking data "
            f"- do NOT list {'it' if len(d.fixed) == 1 else 'them'}.")


def _leg_intro(req: TripRequest, d: LegData) -> str:
    return (f"{f'Travel date {d.day}, ' if d.day else ''}{req.travelers} traveller(s). Prefer these prices over the "
            "search snippets; they are per person one way unless stated. Car rental prices are per day for the "
            "whole car (add fuel/tolls for the car option).")


def _leg_live(d: LegData) -> list[live_prices.LiveResult]:
    """Live pages the LLM still needs: not the ones whose mode Python already priced."""
    covered = {"google_flights": "flight", "flixbus": "bus"}
    return [r for r in d.live if covered.get(r.site) not in d.fixed]


# --- fallback: one request per place / leg (only Groq free and the trip doesn't fit its 8k tokens/min) -----

async def _place_llm(req: TripRequest, d: PlaceData, usage: Usage, emit: Emit) -> Place:
    emit(f"Summarising {d.name}…")
    head = _place_head(req, d.name)
    if len(d.fixed) == 3:  # all prices from live data: the LLM only writes the place itself
        info = await _ask(EXTRACT_SYSTEM, lambda m: _fit(head + PLACE_INFO_TASK, d.results,
                                                         ["sights", "activities", "food"], [], "", m),
                          PlaceInfoDraft, usage)
        draft = PlaceDraft(**info.model_dump(), **d.fixed)
    else:
        try:
            draft = await complete_json(EXTRACT_SYSTEM, _fit(head + PLACE_INFO_TASK + PLACE_PRICES_TASK, d.results,
                                                             None, d.live, _place_intro(req, d), None), PlaceDraft, usage)
        except RequestTooLarge:
            emit(f"Splitting the request for {d.name} (Groq token limit)…")
            info = await _ask(EXTRACT_SYSTEM, lambda m: _fit(head + PLACE_INFO_TASK, d.results,
                                                             ["sights", "activities", "food"], [], "", m),
                              PlaceInfoDraft, usage)
            prices = await _ask(EXTRACT_SYSTEM, lambda m: _fit(head + PLACE_PRICES_TASK, d.results,
                                                               ["accommodation", "daily_costs"], d.live,
                                                               _place_intro(req, d), m),
                                PlacePricesDraft, usage)
            draft = PlaceDraft(**info.model_dump(), **prices.model_dump())
        draft = draft.model_copy(update=d.fixed)
    emit(f"Done: {d.name}")
    return _finalize_place(draft, d.name, d.urls, d.price_sources)


async def _leg_llm(req: TripRequest, d: LegData, usage: Usage, emit: Emit) -> Leg:
    task = f"Leg: {d.a} → {d.b} (travel month: {req.start_date or 'unknown'})\n\n{LEG_TASK}\n{_leg_known(d)}"
    draft = await _ask(EXTRACT_SYSTEM, lambda m: _fit(task, d.results, None, _leg_live(d), _leg_intro(req, d), m),
                       LegDraft, usage)
    emit(f"Done: {d.a} → {d.b}")
    return _finish_leg(d, draft.options)


LIVE_PAGE_FOR_MODE = {"train": "trainline", "car": "billiger_mietwagen"}  # facts the LLM gets as live summaries


def _with_link(o: TransportOptionDraft, urls: list[str], live_pages: dict[str, str]) -> TransportOption:
    """Search-result number [n] -> URL; otherwise the live page for that mode (Trainline facts, car rental
    prices); a number outside the list gives no link (never guess)."""
    url = urls[o.source - 1] if o.source and 1 <= o.source <= len(urls) else None
    url = url or live_pages.get(LIVE_PAGE_FOR_MODE.get(o.mode, ""))
    return TransportOption(**o.model_dump(exclude={"source"}), source_url=url)


def _finish_leg(d: LegData, llm_options: list[TransportOptionDraft]) -> Leg:
    urls = search.numbered_urls(d.results)  # same numbering as the leg's search results in the prompt
    live_pages = {r.site: r.url for r in d.live if r.status == "ok"}
    options = [_with_link(o, urls, live_pages) for o in llm_options if o.mode not in d.fixed] + list(d.fixed.values())
    if not options:
        raise ValueError(f"No transport options found for {d.a} → {d.b}")
    return Leg(from_place=d.a, to_place=d.b, options=options, sources=d.urls)


async def _place(req: TripRequest, name: str, usage: Usage, emit: Emit,
                 live: live_prices.LiveBrowser | None = None, stay: tuple[date, date] | None = None) -> Place:
    return await _place_llm(req, await _collect_place(req, name, usage, emit, live, stay), usage, emit)


async def _leg(req: TripRequest, a: str, b: str, usage: Usage, emit: Emit,
               live: live_prices.LiveBrowser | None = None, day: date | None = None) -> Leg:
    return await _leg_llm(req, await _collect_leg(req, a, b, usage, emit, live, day), usage, emit)


# --- normal path: ONE request for the whole trip ---------------------------------------------------------

TRIP_SYSTEM = EXTRACT_SYSTEM + """ You also write a realistic day-by-day itinerary: short titles, \
practical notes."""


def _trip_prompt(req: TripRequest, places: list[PlaceData], legs: list[LegData],
                 skeleton: list[tuple[int, str, str]], with_prices: bool) -> str:
    plan = "\n".join(f"Day {d}: {place} ({kind})" for d, place, kind in skeleton)
    parts = [f"""Trip from {req.origin}{' and back' if req.return_to_origin else ''}; {req.travelers} traveller(s); \
trip type: {req.trip_type}; interests: {", ".join(req.interests) or "none given"}; \
travel month: {req.start_date or 'unknown'}.

Fill in three parts.
1. places - one entry per destination section below, same order:
{PLACE_INFO_TASK}{PLACE_PRICES_TASK if with_prices else ''}
2. legs - one entry per leg section below, from_place/to_place exactly as given. {LEG_TASK} Modes a leg \
marks as already priced must NOT be listed for that leg.
3. days - exactly one entry per day, same day numbers:
{plan}
"attractions" = names copied exactly from the attractions you listed for that day's place; 2-3 on full days, \
0-1 on arrival and departure days; never repeat an attraction; every day needs a short title; mention the \
transfer in notes on arrival and departure days."""]
    for d in places:
        snippets = search.as_context(d.results, None if with_prices and len(d.fixed) < 3
                                     else ["sights", "activities", "food"])
        live_text = _live_section(d.live, _place_intro(req, d)) if with_prices and len(d.fixed) < 3 else ""
        parts.append(f"# Destination: {d.name}\nSearch results:\n{snippets}{live_text}")
    for d in legs:
        parts.append(f"# Leg: {d.a} → {d.b}{f' (travel date {d.day})' if d.day else ''}. {_leg_known(d)}\n"
                     f"Search results:\n{search.as_context(d.results)}{_live_section(_leg_live(d), _leg_intro(req, d))}")
    return "\n\n".join(parts)


def _expected_output_tokens(places: int, legs: int, days: int, with_prices: bool) -> int:
    """Cap for the answer. Only a cap: the time depends on what the model writes, so be generous - a cut-off
    answer costs a whole new request. London -> Basel -> Zürich without dates (prices by the LLM) needed
    more than 7,584 tokens, 1.6 x the Rome-based estimate."""
    estimate = places * (1200 + (400 if with_prices else 0)) + legs * 250 + days * 70 + 300
    return min(max(int(estimate * 3), 4000), 16000)


def _match_attraction(name: str, place: Place) -> str | None:
    """Attraction id for a name the LLM wrote in the day plan (exact slug first, then containment)."""
    key = slug(name)
    by_slug = {slug(a.name): a.id for a in place.attractions}
    if key in by_slug:
        return by_slug[key]
    hits = [aid for s, aid in by_slug.items() if key and (key in s or s in key)]
    return hits[0] if len(hits) == 1 else None


# Above this the one-request plan goes to the parallel per-place/leg path: free models time out on very long
# requests (56k-token prompt for 4 stops without dates: empty answer after 120 s, 2026-10-23). Rome: ~17k.
SINGLE_REQUEST_MAX_TOKENS = 30_000


async def _single_request(req: TripRequest, places: list[PlaceData], legs: list[LegData],
                          nights: list[NightsAt], usage: Usage, emit: Emit) -> TripResearch:
    skeleton = day_skeleton(req, nights)
    with_prices = any(len(d.fixed) < 3 for d in places)
    model = TripDraftWithPrices if with_prices else TripDraft
    with step("prep", "trip prompt") as t:
        prompt = _trip_prompt(req, places, legs, skeleton, with_prices)
        t["chars"] = len(prompt)
    size = estimate_tokens([{"role": "system", "content": TRIP_SYSTEM}, {"role": "user", "content": prompt}], model)
    if size > SINGLE_REQUEST_MAX_TOKENS:
        raise RequestTooLarge(SINGLE_REQUEST_MAX_TOKENS)
    emit("Writing the trip plan…")
    draft = await complete_json(TRIP_SYSTEM, prompt, model, usage,
                                max_tokens=_expected_output_tokens(len(places), len(legs), len(skeleton), with_prices))
    if len(draft.places) < len(places) or len(draft.legs) < len(legs):
        raise LLMError(f"Trip plan incomplete: {len(draft.places)}/{len(places)} places, "
                       f"{len(draft.legs)}/{len(legs)} legs")
    place_objs = []
    for d, p in zip(places, draft.places):
        full = (PlaceDraft(**p.model_dump(), **d.fixed) if not with_prices
                else PlaceDraft.model_validate(p.model_dump()).model_copy(update=d.fixed))
        place_objs.append(_finalize_place(full, d.name, d.urls, d.price_sources))
    leg_objs = [_finish_leg(d, l.options) for d, l in zip(legs, draft.legs)]
    by_place = {p.name: p for p in place_objs}
    place_of_day = {day: place for day, place, _ in skeleton}
    day_drafts = [DayDraft(day=x.day, title=x.title, notes=x.notes,
                           attraction_ids=[aid for n in x.attractions
                                           if (aid := _match_attraction(n, by_place[place_of_day[x.day]]))]
                           if place_of_day.get(x.day) in by_place else [])
                  for x in draft.days]
    days = _days_from_drafts(skeleton, place_objs, {d.day: d for d in day_drafts})
    return TripResearch(places=place_objs, legs=leg_objs, nights=nights, days=days)


async def _nothing() -> list:
    return []


def _days_prompt(req: TripRequest, skeleton: list[tuple[int, str, str]], places: list[Place],
                 used: set[str]) -> str:
    catalog = "\n".join(
        f"{p.name}: " + "; ".join(
            f"{a.id} = {a.name} ({a.kind}{', free' if not a.price else ''})"
            for a in p.attractions if a.id not in used
        )
        for p in places
    )
    plan = "\n".join(f"Day {d}: {place} ({kind})" for d, place, kind in skeleton)
    example_id = next((a.id for p in places for a in p.attractions if a.id not in used), "rome-colosseum")
    return f"""Trip type: {req.trip_type}; interests: {', '.join(req.interests) or 'none'}; \
{req.travelers} traveller(s); from {req.origin}{' and back' if req.return_to_origin else ''}.

Days (fixed - write exactly one entry per day, same day numbers):
{plan}

Attractions you may use (id = name):
{catalog}

Answer with JSON exactly like:
{{"days": [{{"day": 1, "title": "Arrival and old town", "attraction_ids": ["{example_id}"], "notes": "Flight from {req.origin}, ..."}}]}}

Rules: every day needs a short title; only use ids of the place of that day; 2-3 attractions on full days, 0-1 on arrival \
and departure days; never repeat an attraction; mention the transfer in notes on arrival \
and departure days."""


async def _days_drafts(req: TripRequest, skeleton: list[tuple[int, str, str]], places: list[Place],
                       used: set[str], usage: Usage) -> list[DayDraft]:
    """One request for all days; split into halves when it is too big for Groq's token limit."""
    names = {place for _, place, _ in skeleton}
    own = [p for p in places if p.name in names]
    try:
        return (await complete_json(DAYS_SYSTEM, _days_prompt(req, skeleton, own, used), DaysDraft, usage)).days
    except RequestTooLarge:
        if len(skeleton) < 2:
            raise
        mid = len(skeleton) // 2
        first = await _days_drafts(req, skeleton[:mid], places, used, usage)
        taken = used | {i for d in first for i in d.attraction_ids}
        return first + await _days_drafts(req, skeleton[mid:], places, taken, usage)


def _days_from_drafts(skeleton: list[tuple[int, str, str]], places: list[Place],
                      drafts: dict[int, DayDraft]) -> list[Day]:
    """Validated days: only that day's place, no repeats, default titles for gaps."""
    by_place = {p.name: p for p in places}
    used: set[str] = set()
    days = []
    for day, place, kind in skeleton:
        d = drafts.get(day)
        valid = {a.id for a in by_place[place].attractions} if place in by_place else set()
        ids = [i for i in (d.attraction_ids if d else []) if i in valid and i not in used]
        used.update(ids)
        days.append(Day(
            day=day, place=place,
            title=(d.title if d and d.title.strip() else
                   {"arrival": f"Arrival in {place}", "departure": "Departure"}.get(kind, f"Explore {place}")),
            attraction_ids=ids,
            notes=d.notes if d else "",
        ))
    return days


async def _days(req: TripRequest, places: list[Place], nights: list[NightsAt], usage: Usage) -> list[Day]:
    skeleton = day_skeleton(req, nights)
    drafts = {d.day: d for d in await _days_drafts(req, skeleton, places, set(), usage)}
    return _days_from_drafts(skeleton, places, drafts)


async def _write_plan(req: TripRequest, places: list[PlaceData], legs: list[LegData], nights: list[NightsAt],
                      usage: Usage, emit: Emit) -> TripResearch:
    if LLM_SINGLE_REQUEST:
        try:
            return await _single_request(req, places, legs, nights, usage, emit)
        except RequestTooLarge:
            # Only Groq is free (8,000 tokens/minute): one request per place/leg + the day plan instead.
            emit("Large trip: planning it in several smaller requests…")
    done = await asyncio.gather(*(_place_llm(req, d, usage, emit) for d in places),
                                *(_leg_llm(req, d, usage, emit) for d in legs))
    place_objs, leg_objs = list(done[: len(places)]), list(done[len(places):])
    emit("Writing the day-by-day plan…")
    days = await _days(req, place_objs, nights, usage)
    return TripResearch(places=place_objs, legs=leg_objs, nights=nights, days=days)


async def plan_trip(req: TripRequest, usage: Usage, emit: Emit) -> TripResearch:
    track(usage)
    names = list(dict.fromkeys(s.name for s in req.stops))
    legs = legs_for(req)
    nights = distribute_nights(req)
    stays = stay_dates(req, nights)
    # Exchange rates for the live pages (CHF, EUR) load while the pages do, not after them.
    rates = asyncio.gather(*(currency.usd_rate(c) for c in ("CHF", "EUR")), return_exceptions=True)
    async with live_prices.session() as live:
        collected = await asyncio.gather(
            *(_collect_place(req, n, usage, emit, live, stays.get(n)) for n in names),
            *(_collect_leg(req, a, b, usage, emit, live, leg_date(req, stays, b)) for a, b in legs),
        )
        # Start the LLM before the browser closes (closing takes ~2-3 s).
        plan = asyncio.create_task(_write_plan(req, list(collected[: len(names)]), list(collected[len(names):]),
                                               nights, usage, emit))
    await rates
    return await plan
