# Workflow: Plan a trip

## Objective
Turn a user's route, dates, travellers, budget and trip style into a researched trip plan:
3 budget classes with cost ranges per category, transport comparison per leg,
day-by-day plan, place overviews, and savings options when over budget.

## Inputs (`TripRequest`, backend/app/models.py)
origin, stops[] (ordered, optional fixed nights), return_to_origin, start_date+end_date or days,
travelers, budget (total for the group), currency (ISO code, frankfurter.dev list), trip_type,
interests[], tier (budget | comfort | luxury, the class checked against the budget).

## Steps (backend/app/agent/synthesize.py → `plan_trip`)
1. **Collect** (no LLM, all places and legs in parallel): Tavily search (`app/search.py`, 5 queries per
   place, 2 per leg, 1 credit each, cached per query + month) and live pages (`app/live_prices.py`,
   Crawl4AI, prices read by CSS, cached per day).
2. **Calculate** (`app/price_tiers.py`, Python): accommodation / meal / local-transport tiers per place and
   flight / bus options per leg from the live rows, in USD. Values without enough live data stay open.
3. **One LLM request** (`_single_request`, `TripDraft`, OpenRouter): place descriptions, attractions,
   dishes, open price tiers, remaining transport modes (train, car, ferry) and the day plan with
   attraction names. Answers cached by prompt. Groq-only (`RequestTooLarge`) or `LLM_SINGLE_REQUEST=0`:
   one request per place/leg in parallel + the day plan (fallback path).
4. **Assemble** in Python: attraction ids (names → slugs), live prices override LLM values, days
   validated (only that day's place, no repeats, default titles), nights per place (`distribute_nights`).
5. **Budget** (`app/budget.py`): deterministic totals per tier, fastest/cheapest/best value per leg,
   currency conversion, savings proposals. `POST /api/trips/{id}/adjust` recomputes without new research.

## Performance (2026-10-08, `tools/perf_run.py`, Zürich → Rome, 3 nights, 2 people)
| run | total | live pages (span) | LLM requests | LLM tokens |
|---|---|---|---|---|
| baseline | 73.9 s | 50.4 s | 4 | 28,016 |
| after scraping fixes | 28.4 s* | 14.2 s | 4 | 25,483 |
| + Python price tiers | 27.4 s | 14.2 s | 4 | 18,634 |
| one request per trip (default) | 43–49 s | 13.7 s | **1** | ~17,500 |
| `LLM_SINGLE_REQUEST=0` | 26.3 s | 12.4 s | 4 | 16,976 |
\* the free model's answer time varies a lot (Rome place request 10.6–60.6 s for the same prompt).
- What made scraping slow: queueing (3 pages at once), fixed scroll/settle waits on every page (4–6 s),
  Google's cookie page on the first request of a browser (27 s, sometimes no flights), the per-site
  pause held back each result for 3 s. Google Flights also has a second layout (split aria-labels) that
  found 0 return flights.
- One request is slower than 4 parallel ones: answer time grows with output tokens (~100 tokens/s) and
  one long answer can't be written in parallel. Default = 1 request (user goal: fewest requests).
- Quality check: `tools/compare_trips.py .tmp/perf/baseline.json .tmp/perf/<run>.json` (flags fewer
  items or prices moving > 35 %). Asking 10-14 attractions (was 8-12) keeps the single request at least
  as complete as the baseline (18 vs 16 attractions, 10 vs 9 with price, 9 vs 8 scheduled).

## Sources per function (`backend/sources.json`)
- Preferred websites per function, editable by hand, read fresh on every search (no restart).
- `mode: "prefer"` (user's choice, 2026-10-07): Tavily favours these domains but still uses the
  whole web; `"restrict"` = only these domains (risk: empty results for small places).
  Empty `domains` = whole web. Max 300 domains per function. Unknown function names or a
  wrong mode → clear error (`SearchError`).
- Starter list: Wikivoyage/Lonely Planet/Tripadvisor/Atlas Obscura (sights), GetYourGuide/Viator
  (activities, tour prices), TasteAtlas/Eater/Michelin (food), Booking/Hostelworld/Hotels/Kayak
  (accommodation), Numbeo/BudgetYourTrip (daily costs), Rome2Rio/Seat61/Skyscanner/Trainline/
  FlixBus/12Go (transport).

## Place search (autocomplete, map click)
- Offline GeoNames `cities15000` index (`app/places_index.py`, ~32k cities, names in all
  languages, ~1 ms per query, loaded once at startup in ~1.5 s, ~50 MB RAM).
  Download/refresh: `backend/.venv/bin/python tools/download_cities.py`.
- Ranking: log10(population) + bonus (exact name 3, name prefix 2, exact alias 1.5).
  City districts (PPLX, "Paris 15 …") are hidden.
- Photon is only asked when the index has no match at all, and its fuzzy results are filtered
  to names containing the first typed word. Photon errors return [] (never HTTP 500).
- Before (2026-10-07): every keystroke went to Photon, 1–2 s per request, 33 of 75 timed out.

## Tools
- `tools/perf_run.py --label <name> [--cold-tavily]`: one measured run (time per Tavily query, live page,
  LLM request; tokens) → `.tmp/perf/<name>.json`; `--show <file>` prints a saved run.
- `tools/compare_trips.py <a.json> <b.json>`: completeness/price comparison of two runs.
- `tools/plan_trip.py <request.json>`: full run from the CLI (uses Tavily credits + free LLM quota).
- `tools/seed_demo_trip.py`: free demo trip at /trip/demo for UI work.
- `tools/download_cities.py`: download the offline city list (needed once).
- `./dev.sh`: start backend + frontend.

## Live prices (`app/live_prices.py` + `app/live_extract.py`, Crawl4AI since 2026-10-07)
- Division of labour: Tavily finds pages/IDs, Crawl4AI (open source, local headless browser, not
  the cloud) loads the live pages, CSS schemas/regexes read the prices **without LLM**, the LLM only gets
  a short summary per site (stats + up to 12 offers). Rome test: 41,440 → 7,927 characters in the prompt.
  Site table and per-site data: `CLAUDE.md`.
- Runs in parallel with the Tavily search of each place/leg; max 3 pages at once, one per site, 3 s pause
  between requests to the same site. `LIVE_PRICES=0` in `.env` turns it off; if the browser can't start,
  the planner silently uses Tavily only.
- Crawl4AI config (`_crawl_config`): cache BYPASS (own day cache), `wait_for` = JS check that the result
  list *exists* (`css:` waits for *visible*, which never happens for Google Flights' hidden aria labels),
  25 s; `scan_full_page`, consent/overlay removal, markdown via `PruningContentFilterLXML`.
  magic/simulate_user/override_navigator/stealth/undetected stay off (no bot-protection bypass).
- Statuses: `ok` (rows or prices found), `not_found` (result list never appeared: blocked, no results
  or layout changed - clear message, trip continues), `blocked`, `no_prices`, `empty`, `error`,
  `wrong_city` (Trip.com ID check via page title).
- Dates per stop: `stay_dates` (check-in/out from `distribute_nights`, first visit of a place);
  leg date = check-in at the destination, way home = last day. Without a start date only the
  undated pages run.
- Site IDs, cached forever in `.tmp/research_cache.sqlite`: FlixBus city IDs via
  `global.api.flixbus.com/search/autocomplete/cities`; Hostelworld path continent/country/city from
  GeoNames; German city names (billiger-mietwagen URLs, "mietwagen-muenchen.html") from GeoNames
  `alternatenames/<CC>.zip` (downloaded once per country to `.tmp/geonames/alternatenames/`, 3-23 MB);
  Trip.com city ID via one Tavily search per new city (**1 credit**, URL `…/rome-hotels-list-343/`).
- Pages are cached per site + URL + day (only status `ok`).
- `tools/scrape_live_prices.py --site app|all|<site> …`: test sites from the CLI (no page cache);
  prints `items=` (rows read without LLM) per site. `items=0` on an `ok` page = schema outdated.
- Test 2026-10-07 (Rome, Mar 2027, 2 people): all 8 planner sites `ok` via Crawl4AI with rows
  (Airbnb 24, Trip.com 22, Hostelworld 30, Numbeo 13, Google Flights 9, FlixBus 9, Trainline 1, car rental 24).
  Not usable without bypassing bot protection (kept in `SITES` for retests): Booking, Skyscanner, Kayak,
  Sixt, Rentalcars, TripAdvisor, TheFork, GetYourGuide, Viator, Klook. Google Maps: no prices.
  Tourism sites stay with Tavily.
- Dead ends: Trip.com keyword API is blocked ("whaleguard"); DuckDuckGo HTML search and Wikidata rate-limit
  after a few queries; billiger-mietwagen city pages need German names (English slugs 404).
  Dated car-rental quotes not pursued (would need the site's private quote API).
  Hostelworld dated search (`/pwa/s?…&id=36&from&to&guests`, city id is on the city page) takes the dates
  but renders the result cards empty for the headless browser → city page with "from" prices for now.
- `crawl4ai-setup` also installs Patchright (undetected Playwright); it is only used when the undetected
  adapter is chosen explicitly - we don't.

## Keys (.env)
- `TAVILY_API_KEY` (app.tavily.com), `OPENROUTER_API_KEY` (openrouter.ai/keys),
  `GROQ_API_KEY` (console.groq.com/keys, free tier; fallback only).
- `OPENROUTER_MODEL`: must be a free NVIDIA model (`nvidia/...:free`). `config.check_free_model`
  refuses anything else at startup and before every call (user requirement: never pay for the LLM).
  Default `nvidia/nemotron-3-super-120b-a12b:free`, the only free NVIDIA model on OpenRouter
  with JSON-schema support (checked 2026-10-07 via /api/v1/models).

## Costs and limits
- Per trip: Tavily credits = 5 × places + 2 × legs (Rome round trip: 9), + 1 per city never planned
  before (Trip.com city ID, live prices). Live pages: 0 USD, ~1-3 min per trip when not cached,
  ~1-4k characters per place/leg in the LLM prompt (summaries). LLM: 0 USD.
- OpenRouter free models: 20 requests/min, 50/day (1000/day after buying ≥ 10 USD credits once).
  A trip needs places + legs + 1 calls (+ repairs). Reasoning is switched off (`reasoning.enabled=false`).
- Fallback (`app/llm.py`): any OpenRouter 429 → the request goes to Groq `openai/gpt-oss-20b`
  (`reasoning_effort="low"`, max 3,000 output tokens). Groq free tier: 30 req/min, 1,000/day,
  **8,000 tokens/min** → every Groq request is budgeted (estimate = chars / 3 + output) in a 60 s window
  (`MinuteBudget`, 90 % of the limit). It books prompt + 1,200 output tokens (`GROQ_RESERVE_OUTPUT`;
  booking the full 3,000 let only one request run at a time) and replaces that with the real usage after
  the answer; if Groq still says "try again in …" the provider is paused that long (first
  run reserved 3,000 output tokens but used 270-690 → only 1 request/minute, 4 min for Rome). A request
  that can never fit raises `RequestTooLarge`; the
  planner splits it (place → `PlaceInfoDraft` + `PlacePricesDraft`; day plan → halves without repeating
  attractions) or shortens the snippets (`search.as_context(max_chars=…)`).
- A limited provider is skipped until its reset (daily: from the 429 headers; per minute: 10/30/60 s for
  OpenRouter, "try again in …" for Groq). Per-minute limits ≤ 65 s are waited out. Only when both are
  limited does the trip fail, naming both reset times. SDK auto-retries are off (`max_retries=0`):
  they burned daily quota on every 429.
- Log line per answer: `LLM <provider> (<model>) answered <Draft>: … tokens`; totals per provider in
  `Usage.llm_by_provider` and in the `tools/plan_trip.py` summary.
- History: the first version used Claude Opus + server-side web search: 3.35 USD for a 3-stop
  trip (512k input tokens from search results). Replaced on 2026-10-07.

## Defaults
- Default transport pick per class: budget → cheapest, comfort → best value, luxury → fastest.
  Best value = lowest (price / cheapest price + duration / fastest duration).
- Rooms = ceil(travellers / 2); accommodation prices are per double room per night.
- Food = 3 meals × nights × travellers.

## Known quirks
- The LLM sometimes lists modes that don't exist ("no train", ferry Basel → Bangkok) with 0 h / 0 USD; the
  budget then ranked them fastest + cheapest (bug found by the user 2026-10-08). `Leg` now drops options
  with duration or max price <= 0 (also when old trips are loaded); the prompt forbids them. 5 saved
  trips were recomputed (manual leg choices reset).
- One request per trip can get long: London → Basel → Zürich, 7 days, no dates (no live prices, the LLM
  writes all tiers) needed > 7,584 output tokens and was cut off twice (2026-10-08). Now: `max_tokens` =
  3 x estimate (min 4,000, max 16,000; it is only a cap), and a cut-off answer is asked again with double
  room instead of sending the half answer back.
- Groq checks the JSON against the schema itself and answers HTTP 400 `json_validate_failed`
  (e.g. `duration_hours: null` for a "no train exists" option, Osorno → San Pedro de Atacama,
  2026-10-07). `llm.py` turns that into a normal repair round (`InvalidGeneration`, the rejected
  JSON + error go back to the model) instead of failing the whole trip.
- Price ranges: prompts ask for typical (25th–75th percentile) prices; otherwise models report
  promo-to-peak ranges that make totals useless.
- Nominatim blocked us; Photon is slow and times out often → only used as fallback.
- Homebrew on this Mac is the Intel build without Rosetta; Node is installed at `~/.local/node`.
- "Stay one night less" lowers costs but does not rewrite the day plan.
