"""Live prices: load booking and comparison sites with Crawl4AI (open source, local headless browser).

Free. Every site gets a search URL with place, dates and travellers. Prices are read without an LLM
(CSS schemas / regexes in live_extract.py); the LLM only gets a short summary per site, or cleaned
markdown when a site has no schema or its layout changed. No bot-protection bypass (Crawl4AI's
magic/stealth/undetected modes stay off): a block page is reported as "blocked" and the planner
relies on Tavily for that site.

Site IDs: FlixBus via its public city autocomplete, Hostelworld paths and German city names
(billiger-mietwagen.de) via the offline GeoNames files, Trip.com city IDs via one Tavily search
per city. All IDs are cached forever; pages are cached per day.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import re
import time
import unicodedata
import zipfile
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import AsyncIterator, Callable
from urllib.parse import quote, quote_plus

import httpx
from pydantic import BaseModel

from . import cache, live_extract, search
from .config import LIVE_CACHE, LIVE_PRICES, ROOT
from .models import Usage
from .places_index import City, get_index
from .timing import step

log = logging.getLogger("holydays.live")

GEONAMES_DIR = ROOT / ".tmp" / "geonames"
ALT_NAMES_URL = "https://download.geonames.org/export/dump/alternatenames/{cc}.zip"
HEADERS = {"User-Agent": "holydays-travel-planner/0.1 (local dev)"}
MAX_TEXT = 20_000        # stored per page
CONTEXT_CHARS = 6_000    # per page in the LLM prompt
CONCURRENCY = 6          # pages open at the same time (one per site at most)
LOAD_MORE_SITES = {"tripcom"}
# Google answers the first request of a fresh browser with its cookie-consent page; the result wait then
# times out (25 s, flights sometimes missing). Declining cookies once per browser (same context) fixes it:
# 3 s warm-up + 3 s search instead of 27 s (measured 2026-10-08).
CONSENT_WARMUP = {"google_flights": "https://www.google.com/travel/flights?hl=en"}
DECLINE_COOKIES_JS = """
const b = [...document.querySelectorAll('button, input[type=submit]')].find(x =>
  /reject all|alle ablehnen/i.test(x.innerText || x.value || x.getAttribute('aria-label') || ''));
if (b) b.click();
"""  # add results while scrolling: scroll + settle time (others: measured, not needed)
SITE_PAUSE = 3.0         # seconds between two requests to the same site
CAR_RENTAL_MAX_KM = 800  # car rental only for legs you could plausibly drive

BLOCK_MARKERS = [
    "captcha", "are you a robot", "are you human", "verify you are human", "access denied",
    "unusual traffic", "press & hold", "press and hold", "request blocked", "pardon our interruption",
    "bot or not", "please verify", "blocked by", "something went wrong while loading",
    "person or a robot", 'thinks you are a "bot"', "what is a bot?", "access blocked",
]
PRICE = re.compile(r"(?:US\$|\$|CHF|€|£|¥)\s?\d|\d[\d.,]*\s?(?:€|CHF)")
CONTINENTS = {"EU": "europe", "AS": "asia", "AF": "africa", "NA": "north-america",
              "SA": "south-america", "OC": "oceania", "AN": "antarctica"}


def slug(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


def german_ascii(text: str) -> str:
    """German URL spelling: München -> Muenchen, Zürich -> Zuerich."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return text


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

@dataclass
class Query:
    place: str                                 # stay location / leg destination
    checkin: date | None = None                # leg: travel date
    checkout: date | None = None
    people: int = 1
    origin: str | None = None                  # legs only
    tripcom: tuple[str, str] | None = None     # (city id, city slug from trip.com)
    flixbus: tuple[str, str] | None = None     # (origin city id, destination city id)
    hostelworld: str | None = None             # "europe/italy/rome"
    car_city_de: str | None = None             # German name of the pick-up city
    iata: tuple[str, str] | None = None        # tool only (Skyscanner)
    tourism_url: str | None = None             # tool only

    @property
    def rooms(self) -> int:
        return math.ceil(self.people / 2)

    @property
    def dated(self) -> bool:
        return bool(self.checkin and self.checkout)


@dataclass(frozen=True)
class Site:
    category: str  # accommodation | daily_costs | transport | car_rental | food | activities | sights
    url: Callable[[Query], str | None]
    in_app: bool = False  # used by the trip planner; the rest is blocked or not useful (see workflow)


def _airbnb(q: Query) -> str | None:
    return q.dated and (f"https://www.airbnb.com/s/{quote(q.place)}/homes?checkin={q.checkin}"
                        f"&checkout={q.checkout}&adults={q.people}&currency=USD")


def _tripcom(q: Query) -> str | None:
    return q.dated and q.tripcom and (
        f"https://www.trip.com/hotels/list?city={q.tripcom[0]}&checkin={q.checkin:%Y/%m/%d}"
        f"&checkout={q.checkout:%Y/%m/%d}&adult={q.people}&crn={q.rooms}&curr=USD&locale=en-US")


def _google_flights(q: Query) -> str | None:
    return q.checkin and q.origin and ("https://www.google.com/travel/flights?hl=en&curr=USD&q=" + quote_plus(
        f"Flights to {q.place} from {q.origin} on {q.checkin} one way {q.people} adults"))


def _flixbus(q: Query) -> str | None:
    return q.checkin and q.flixbus and (
        f"https://shop.flixbus.com/search?departureCity={q.flixbus[0]}&arrivalCity={q.flixbus[1]}"
        f"&rideDate={q.checkin:%d.%m.%Y}&adult={q.people}&_locale=en&currency=USD")


SITES: dict[str, Site] = {
    "airbnb": Site("accommodation", _airbnb, in_app=True),
    "tripcom": Site("accommodation", _tripcom, in_app=True),
    # City page with "from" prices per bed; the dated search needs internal IDs.
    "hostelworld": Site("accommodation", lambda q: q.hostelworld and (
        f"https://www.hostelworld.com/hostels/{q.hostelworld}/"), in_app=True),
    "numbeo": Site("daily_costs", lambda q: (
        f"https://www.numbeo.com/cost-of-living/in/{quote(q.place.replace(' ', '-'))}?displayCurrency=USD"),
        in_app=True),
    "google_flights": Site("transport", _google_flights, in_app=True),
    "flixbus": Site("transport", _flixbus, in_app=True),
    # Route page with a "from" price, no dates.
    "trainline": Site("transport", lambda q: q.origin and (
        f"https://www.thetrainline.com/en/train-times/{slug(q.origin)}-to-{slug(q.place)}"), in_app=True),
    # City page with "ab X € / Tag" from recent searches, no dates.
    "billiger_mietwagen": Site("car_rental", lambda q: q.car_city_de and (
        f"https://www.billiger-mietwagen.de/mietwagen-{slug(german_ascii(q.car_city_de))}.html"), in_app=True),
    # Tool only: blocked for headless browsers or without prices (test 2026-10-07).
    "booking": Site("accommodation", lambda q: q.dated and (
        f"https://www.booking.com/searchresults.html?ss={quote_plus(q.place)}&checkin={q.checkin}"
        f"&checkout={q.checkout}&group_adults={q.people}&no_rooms={q.rooms}&group_children=0"
        f"&selected_currency=USD&lang=en-us")),
    "skyscanner": Site("transport", lambda q: q.checkin and q.iata and (
        f"https://www.skyscanner.net/transport/flights/{q.iata[0].lower()}/{q.iata[1].lower()}/"
        f"{q.checkin:%y%m%d}/?adultsv2={q.people}&currency=USD")),
    "sixt": Site("car_rental", lambda q: q.hostelworld and (
        f"https://www.sixt.com/car-rental/{q.hostelworld.split('/', 1)[1]}/")),
    "kayak_cars": Site("car_rental", lambda q: q.dated and (
        f"https://www.kayak.com/cars/{quote(q.place)}/{q.checkin}/{q.checkout}")),
    "tripadvisor": Site("food", lambda q: (
        f"https://www.tripadvisor.com/Search?q={quote_plus('restaurants ' + q.place)}")),
    "google_maps": Site("food", lambda q: (
        f"https://www.google.com/maps/search/{quote_plus('restaurants in ' + q.place)}?hl=en")),
    "thefork": Site("food", lambda q: f"https://www.thefork.com/search?queryText={quote_plus(q.place)}"),
    "getyourguide": Site("activities", lambda q: (
        f"https://www.getyourguide.com/s/?q={quote_plus(q.place)}&currency=USD&locale=en-US")),
    "viator": Site("activities", lambda q: f"https://www.viator.com/searchResults/all?text={quote_plus(q.place)}"),
    "klook": Site("activities", lambda q: f"https://www.klook.com/en-US/search/result/?query={quote_plus(q.place)}"),
    "tourism": Site("sights", lambda q: q.tourism_url),
}
PLACE_SITES = [s for s, x in SITES.items() if x.in_app and x.category in ("accommodation", "daily_costs")]
LEG_SITES = [s for s, x in SITES.items() if x.in_app and x.category in ("transport", "car_rental")]


# ---------------------------------------------------------------------------
# Site IDs (cached forever)
# ---------------------------------------------------------------------------

def _recall(kind: str, name: str) -> str | None:
    return cache.get(cache.make_key("live_id", kind, name))


def _remember(kind: str, name: str, value: str) -> None:
    cache.put(cache.make_key("live_id", kind, name), value)


def city_of(place: str) -> City | None:
    index = get_index()
    found = index.search(place, 1) if index else []
    return found[0] if found else None


@lru_cache
def _continents() -> dict[str, str]:
    out = {}
    for line in open(GEONAMES_DIR / "countryInfo.txt", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if not line.startswith("#") and len(p) > 8:
            out[p[0]] = CONTINENTS.get(p[8], "")
    return out


def hostelworld_path(city: City) -> str | None:
    continent = _continents().get(city.country_code)
    return continent and f"{continent}/{slug(city.country)}/{slug(city.name)}"


def tripcom_from_urls(urls: list[str], place: str) -> tuple[str, str] | None:
    """(city id, city slug) from trip.com URLs like .../hotels/rome-hotels-list-343/; the place's own city first."""
    found = [m.groups() for u in urls if (m := re.search(r"/hotels/([a-z0-9-]+)-hotels-list-(\d+)", u))]
    found.sort(key=lambda f: f[0] != slug(place))
    return (found[0][1], found[0][0]) if found else None


async def tripcom_city(place: str, usage: Usage) -> tuple[str, str] | None:
    """One Tavily search per new city (1 credit), cached forever; '' = not found, don't pay again."""
    if (hit := _recall("tripcom", place)) is not None:
        return tuple(hit.split("|", 1)) if hit else None  # type: ignore[return-value]
    rule = search.SourceRule(mode="restrict", domains=["trip.com"])
    async with httpx.AsyncClient(timeout=30) as client:
        results = await search.search(client, f"trip.com hotels in {place}", usage, rule)
    found = tripcom_from_urls([r.url for r in results], place)
    _remember("tripcom", place, "|".join(found) if found else "")
    return found


async def flixbus_city_id(client: httpx.AsyncClient, name: str) -> str | None:
    if (hit := _recall("flixbus", name)) is not None:
        return hit or None
    r = await client.get("https://global.api.flixbus.com/search/autocomplete/cities",
                         params={"q": name, "lang": "en", "flixbus_cities_only": "false"})
    r.raise_for_status()
    cities = r.json()
    city_id = cities[0]["id"] if cities else ""
    _remember("flixbus", name, city_id)
    return city_id or None


def german_name_from_zip(data: bytes, geoname_id: int, country_code: str) -> str | None:
    """Preferred, non-historic German name from a GeoNames alternatenames/<CC>.zip."""
    best: tuple[int, str] | None = None
    with zipfile.ZipFile(io.BytesIO(data)) as z, z.open(f"{country_code}.txt") as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or p[1] != str(geoname_id) or p[2] != "de":
                continue
            if len(p) > 7 and p[7] == "1":  # historic
                continue
            rank = 0 if len(p) > 4 and p[4] == "1" else 1
            if best is None or rank < best[0]:
                best = (rank, p[3])
    return best[1] if best else None


async def german_name(city: City) -> str:
    key = str(city.geoname_id)
    if (hit := _recall("german_name", key)) is not None:
        return hit or city.name
    path = GEONAMES_DIR / "alternatenames" / f"{city.country_code}.zip"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120, headers=HEADERS) as client:
            r = await client.get(ALT_NAMES_URL.format(cc=city.country_code))
            r.raise_for_status()
        path.write_bytes(r.content)
    name = await asyncio.to_thread(german_name_from_zip, path.read_bytes(), city.geoname_id, city.country_code)
    _remember("german_name", key, name or "")
    return name or city.name


def distance_km(a: City, b: City) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

class LiveResult(BaseModel):
    site: str
    category: str
    url: str
    final_url: str = ""
    status: str  # ok | no_prices | blocked | empty | error | wrong_city | not_found
    http_status: int | None = None
    title: str = ""
    text: str = ""            # cleaned markdown
    items: list[dict] = []    # prices read without LLM (live_extract); empty when the site has no parser
    error: str | None = None


def classify(http_status: int | None, text: str) -> str:
    low = text.lower()
    if http_status in (401, 403, 429) or any(m in low[:3000] for m in BLOCK_MARKERS):
        return "blocked"
    if http_status and http_status >= 400:
        return "error"
    if len(text) < 500:
        return "empty"
    if not PRICE.search(text):
        return "no_prices"  # page loaded without results (e.g. Booking drops the search for bots)
    return "ok"


def _crawl_config(site: str):
    from crawl4ai import CacheMode, CrawlerRunConfig, JsonCssExtractionStrategy
    from crawl4ai import PruningContentFilterLXML
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    schema, ready = live_extract.SCHEMAS.get(site, (None, None))
    # "Exists" instead of css: (= visible): some result rows are hidden aria labels (Google Flights).
    wait_for = ready and f"js:() => document.querySelector({json.dumps(ready)}) !== null"
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,  # our own day cache in research_cache.sqlite
        locale="en-US", timezone_id="Europe/Zurich",
        wait_for=wait_for, wait_for_timeout=25_000, page_timeout=60_000,
        # Scrolling and a settle delay only where results load on scroll; elsewhere they cost 4-6 s per page
        # for the same rows (measured 2026-10-08). The wait above already waits for the result list.
        delay_before_return_html=1.5 if site in LOAD_MORE_SITES else 0.2,
        scan_full_page=site in LOAD_MORE_SITES, scroll_delay=0.4, max_scroll_steps=8,
        remove_consent_popups=True, remove_overlay_elements=True,
        excluded_tags=["script", "style", "noscript", "svg", "nav", "footer"],
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilterLXML(threshold=0.4, threshold_type="dynamic"),
            options={"ignore_links": True, "ignore_images": True}),
        extraction_strategy=JsonCssExtractionStrategy(schema) if schema else None,
        verbose=False,
    )


async def crawl_page(crawler, site: str, url: str) -> LiveResult:
    """Load one page with Crawl4AI: CSS rows (no LLM) + cleaned markdown for the rest."""
    base = dict(site=site, category=SITES[site].category, url=url)
    try:
        r = await crawler.arun(url, config=_crawl_config(site))
    except Exception as e:
        return LiveResult(**base, status="error", error=f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
    md = r.markdown
    raw = re.sub(r"\n\s*\n+", "\n", (md.raw_markdown if md else "") or "").strip()
    fit = (md.fit_markdown if md else "") or ""
    title = ((r.metadata or {}).get("title") or "") if hasattr(r, "metadata") else ""
    if not r.success:
        full = r.error_message or "unknown error"
        detail = next((l.strip() for l in full.splitlines() if l.strip().startswith("Error:")), full.splitlines()[0])
        # wait_for timed out: the result list never appeared (blocked, no results or layout changed)
        status = "blocked" if classify(r.status_code, raw) == "blocked" else (
            "not_found" if "wait condition failed" in full.lower() else "error")
        error = (f"{site}: result list not found within 25 s (blocked, no results, or the page layout changed)"
                 if status == "not_found" else f"{site}: {detail[:200]}")
        return LiveResult(**base, status=status, http_status=r.status_code, title=title, text=raw[:MAX_TEXT],
                          error=error)
    rows = []
    if r.extracted_content:
        try:
            rows = live_extract.normalize(site, json.loads(r.extracted_content), url)
        except (ValueError, TypeError) as e:
            log.warning("CSS extraction for %s unreadable: %r", site, e)
    elif site in live_extract.MARKDOWN_SITES:
        rows = live_extract.parse_markdown(site, raw)
    status = "ok" if rows else classify(r.status_code, raw)
    return LiveResult(**base, final_url=r.redirected_url or url, http_status=r.status_code, status=status,
                      title=title, text=(fit if len(fit) > 300 else raw)[:MAX_TEXT], items=rows)


class LiveBrowser:
    """One Crawl4AI browser per trip; at most one page per site and CONCURRENCY pages in total."""

    def __init__(self, crawler) -> None:
        self.crawler = crawler    # crawl4ai.AsyncWebCrawler
        self._warmed: set[str] = set()
        self._next_ok: dict[str, float] = {}  # site -> earliest monotonic time for its next request
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def fetch(self, site: str, url: str, use_cache: bool = LIVE_CACHE) -> LiveResult:
        key = cache.make_key("live", site, url, date.today())
        if use_cache and (hit := cache.get(key)):
            with step("live", site, cache=True) as t:
                result = LiveResult.model_validate_json(hit)
                t.update(status=result.status, items=len(result.items))
            return result
        queued = time.perf_counter()
        async with self._locks[site]:
            # Pause between two requests to the same site - before the next one, so a result is never held back.
            if (wait := self._next_ok.get(site, 0) - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            async with self._sem:
                result = await self._load(site, url, queued)
            self._next_ok[site] = time.monotonic() + SITE_PAUSE
        if result.status == "ok":
            cache.put(key, result.model_dump_json())
        return result

    async def _load(self, site: str, url: str, queued: float) -> LiveResult:
        if site in CONSENT_WARMUP and site not in self._warmed:
            await self._decline_cookies(site)
        with step("live", site, cache=False, queued=round(time.perf_counter() - queued, 3)) as t:
            result = await crawl_page(self.crawler, site, url)
            t.update(status=result.status, items=len(result.items))
        if result.status != "ok":
            log.warning("Live page %s: %s %s", site, result.status, result.error or "")
        return result

    async def _decline_cookies(self, site: str) -> None:
        """Open the site once and decline cookies, in the same browser context as the searches."""
        self._warmed.add(site)
        config = _crawl_config(site).clone(wait_for=None, extraction_strategy=None, js_code=DECLINE_COOKIES_JS,
                                           delay_before_return_html=2.0, scan_full_page=False)
        with step("live", f"{site} (cookie consent)", cache=False):
            try:
                await self.crawler.arun(CONSENT_WARMUP[site], config=config)
            except Exception as e:  # the search still runs; worst case it is slow like before
                log.warning("Cookie consent for %s failed: %r", site, e)


@asynccontextmanager
async def session(enabled: bool = LIVE_PRICES) -> AsyncIterator[LiveBrowser | None]:
    """Yields None when live prices are off or the browser cannot start (planner then uses Tavily only)."""
    if not enabled:
        yield None
        return
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
        crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
        await crawler.start()
    except Exception as e:
        log.warning("Live prices disabled, browser did not start: %r", e)
        yield None
        return
    try:
        yield LiveBrowser(crawler)
    finally:
        await crawler.close()


# ---------------------------------------------------------------------------
# Planner entry points
# ---------------------------------------------------------------------------

async def _run(live: LiveBrowser, q: Query, sites: list[str]) -> list[LiveResult]:
    urls = {s: url for s in sites if (url := SITES[s].url(q))}
    results = await asyncio.gather(*(live.fetch(s, u) for s, u in urls.items()))
    for r in results:
        # A wrong ID shows another city; the slug comes from trip.com's own URL for that ID.
        if r.site == "tripcom" and q.tripcom and q.tripcom[1].replace("-", " ") not in r.title.lower():
            r.status = "wrong_city"
    return list(results)


async def place_prices(live: LiveBrowser, place: str, checkin: date | None, checkout: date | None,
                       people: int, usage: Usage) -> list[LiveResult]:
    if checkin and checkout and checkout <= checkin:
        checkin = checkout = None
    q = Query(place=place, checkin=checkin, checkout=checkout, people=people)
    try:
        if city := city_of(place):
            q.hostelworld = hostelworld_path(city)
        if q.dated:
            q.tripcom = await tripcom_city(place, usage)
    except Exception as e:  # lookups are optional; the sites without IDs still run
        log.warning("Live price lookups for %s failed: %r", place, e)
    return await _run(live, q, PLACE_SITES)


async def leg_prices(live: LiveBrowser, origin: str, destination: str, day: date | None,
                     people: int) -> list[LiveResult]:
    q = Query(place=destination, origin=origin, checkin=day, checkout=day and day + timedelta(days=1),
              people=people)
    try:
        if day:
            async with httpx.AsyncClient(timeout=15) as client:
                ids = await flixbus_city_id(client, origin), await flixbus_city_id(client, destination)
            q.flixbus = ids if all(ids) else None  # type: ignore[assignment]
        a, b = city_of(origin), city_of(destination)
        if a and b and distance_km(a, b) <= CAR_RENTAL_MAX_KM:
            q.car_city_de = await german_name(a)
    except Exception as e:
        log.warning("Live price lookups for %s -> %s failed: %r", origin, destination, e)
    return await _run(live, q, LEG_SITES)


def as_context(results: list[LiveResult], max_chars: int | None = None) -> str:
    """Prompt text: usable pages only. CSS rows become a short summary; pages without rows are cut
    to the part with the prices. `max_chars` shares a budget evenly (requests shrunk for Groq)."""
    usable = [r for r in results if r.status == "ok"]
    share = max_chars // len(usable) if max_chars and usable else None
    parts = []
    for r in usable:
        head = f"### {r.site} ({r.category}): {r.title}\nURL: {r.url}\n"
        if r.items:  # a few hundred characters instead of the page
            body = live_extract.summarize(r.site, r.items, max_rows=12 if share is None else 5)
        else:
            m = PRICE.search(r.text)
            start = max(0, (m.start() if m else 0) - 1500)
            body = r.text[start:start + CONTEXT_CHARS]
        parts.append((head + body)[:share] if share else head + body)
    return "\n\n".join(parts)


def ok_urls(results: list[LiveResult]) -> list[str]:
    return [r.url for r in results if r.status == "ok"]
