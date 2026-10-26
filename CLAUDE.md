# Holydays – project notes for Claude

AI travel planner (FastAPI backend in `backend/`, React/Vite frontend in `frontend/`). The full
procedure is in `workflows/plan_trip.md`; this file only records the division of labour and the
live-price sites.

## Division of labour

| Part | Does | Does not |
|---|---|---|
| **Tavily** (`app/search.py`) | Search: find pages, URLs and IDs (5 queries per place, 2 per leg; Trip.com city ID, 1 credit per new city). Results cached per query + month in `.tmp/research_cache.sqlite`. | Load live pages, read prices for dates |
| **Crawl4AI** (open source, local; `app/live_prices.py`) | Load the live booking pages with place, dates and travellers, wait for the result list, read the prices **without LLM** (CSS schemas / regexes in `app/live_extract.py`), clean markdown as fallback. Pages cached 1 day (status `ok` only). | Search the web, use Crawl4AI Cloud, bypass bot protection |
| **Python** (`app/price_tiers.py`, `app/budget.py`) | All numbers: accommodation/meal/local-transport tiers and flight/bus options from live rows, totals, per person/day, currency. | Language |
| **LLM** (`app/llm.py`) | Language work only, **one request per trip** (`TripDraft`): place descriptions, attractions (+ ticket prices from snippets), dishes, transport modes without live data (train, car, ferry), day plan. Gets cleaned snippets/summaries, never HTML. | Read prices off pages, compute anything Python can |

## Performance rules (measured 2026-10-08, Rome: 73.9 s / 4 requests → ~45 s / 1 request)
- **Calculations always in Python, never in the LLM.** If live data covers a value, `price_tiers.py`
  computes it and the LLM doesn't even see that part of the task (shorter prompt and answer).
- **One LLM request per trip** (`synthesize._single_request`, `TripDraft`): data is collected first
  (Tavily + live pages in parallel), then one request writes places, legs and days; Python adds ids
  (attraction names → ids), live prices and validation. Split per place/leg only when Groq is the only
  free provider (`RequestTooLarge`), when the prompt exceeds `SINGLE_REQUEST_MAX_TOKENS` (30k; free models
  time out on longer ones) or with `LLM_SINGLE_REQUEST=0` (4 parallel requests, ~27 s).
- An answer without content (HTTP 200, no choices = upstream failure/timeout) skips that model for 2 min
  and goes to the next provider; the OpenRouter error text is logged.
- **LLM answers are cached** (`llm.complete_json`, key = system + prompt + schema): same input, no request.
- Answer time grows with **output tokens** (~100 tokens/s on the free model): keep schemas lean,
  `max_tokens` sized to the trip (`_expected_output_tokens`), no fields the LLM doesn't need to write.
- Live pages: one browser per trip, up to 6 pages at once, max 1 per site; the 3 s pause delays the
  *next* request to that site, never the result. No fixed waits: `wait_for` on the result element,
  scroll + settle only for `LOAD_MORE_SITES` (Trip.com). Google: decline cookies once per browser first.
- Resource blocking (images/fonts/trackers) was measured and dropped: no gain (7.7 vs 7.5 s), and
  Crawl4AI's `text_mode` also disables JavaScript.
- Measure before/after with `tools/perf_run.py --label <x>` and check quality with
  `tools/compare_trips.py .tmp/perf/baseline.json .tmp/perf/<x>.json` (baseline: `.tmp/perf/baseline.json`).

## Source links
- Every price shows where it comes from (`TransportOption.source_url`, `Place.price_sources`, "source ↗" in
  `Result.tsx`): live prices → the live search page (Google Flights / FlixBus / Trip.com / Airbnb / Numbeo with
  route, dates, travellers); LLM prices → the search result number [n] it names (`TransportOptionDraft.source`,
  numbering = `search.numbered_urls`, per prompt section), else the live page of that mode (Trainline, car
  rental). Invalid numbers give no link - never guess.

## LLM providers
- Primary: OpenRouter, free NVIDIA model (`OPENROUTER_MODEL`, must be `nvidia/...:free`), reasoning off.
- Backup: `OPENROUTER_BACKUP_MODELS` (default `nvidia/nemotron-3.5-super-vl:free`, same key), tried before Groq.
- A model that is down (404 "Provider returned error", 5xx, no connection) is skipped for 10 min like a
  limited one (2026-10-23: NVIDIA's endpoint for nemotron-3-super returned 404, status -5 on OpenRouter).
- On a rate limit: Groq `openai/gpt-oss-20b` (`GROQ_API_KEY`), `reasoning_effort="low"`, free tier:
  30 req/min, 1,000/day, **8,000 tokens/min**. Requests are budgeted in advance (`MinuteBudget`);
  one that can't fit raises `RequestTooLarge` and the planner splits it (place → info + prices,
  day plan → halves) or shortens the context.
- Fails only when both providers are limited; the error names each reset time. No SDK auto-retries.
- `Usage.llm_by_provider` + log line `LLM <provider> (<model>) answered <Draft>` show who answered what.
- Never switch to a paid model or paid API without asking the user.

## Live-price sites (Crawl4AI)

| Site | Used for | URL / IDs | Data read per page (no LLM) |
|---|---|---|---|
| Airbnb | place, dated | `/s/<place>/homes?checkin&checkout&adults` | name, type, rating, total → per night |
| Trip.com | place, dated | `hotels/list?city=<id>` (ID via Tavily) | hotel, rating, room, price/night, total incl. taxes |
| Hostelworld | place, **undated** | `/hostels/<continent>/<country>/<city>/` (GeoNames) | hostel, rating, distance, privates/dorms "from" |
| Numbeo | place | `/cost-of-living/in/<City>` | meals, beer, coffee, local transport, taxi |
| Google Flights | leg, dated | `travel/flights?q=Flights to … on <date> … adults` | airline, stops, times, duration, price → per person |
| FlixBus | leg, dated | `shop.flixbus.com/search` (city IDs: FlixBus autocomplete API) | times, duration, transfers, price per person |
| Trainline | leg, undated | `/en/train-times/<from>-to-<to>` | avg/fastest time, km, trains/day, changes, fares from (regex on markdown) |
| billiger-mietwagen.de | leg ≤ 800 km, undated | `/mietwagen-<German city>.html` (GeoNames) | category, "ab X €/Tag" |

- Not used (blocked for headless browsers or no prices, kept in `SITES` for retests): Booking,
  Skyscanner, Kayak, Sixt, Rentalcars, TripAdvisor, TheFork, GetYourGuide, Viator, Klook, Google Maps.
- Crawl4AI's `magic`, `simulate_user`, `override_navigator`, stealth and undetected (Patchright) modes
  stay **off** – bypassing bot protection is postponed until the user decides.
- A missing result list (`wait_for` timeout) → status `not_found` with a clear error; the trip continues
  with Tavily only. Pause of 3 s before the next request to the same site, max 6 pages at once.
- Layout changed? Run `tools/scrape_live_prices.py --site <site> …`, look at `items=0`, fix the schema in
  `live_extract.SCHEMAS` (prefer data-*/aria/structure selectors over generated class names).

## Frontend (design handoff `design_handoff_holydays/`, implemented 2026-10-21)
- `/` and `/plan` → `pages/Builder.tsx` = homepage 4a + trip builder 6a (map from `public/assets/countries.json`,
  destination cards from `public/assets/destinations.json`, 3-step planner, `POST /api/trips`).
  `/plan?trip=<id>` pre-fills the builder from `trip.request` ("Edit route").
- `/trip/:id` → `pages/TripPage.tsx` (loading overlay, SSE progress) + `pages/Result.tsx` (8c).
  The result page never recalculates prices: every click sends `POST /api/trips/{id}/adjust` and shows the
  returned report. Adjust fields: `tier`, `reset`, `apply_saving`, `leg_choice`, `accommodation_tier`,
  `food_tier`, `skipped_attractions`, `added_attractions`, `nights` (a field replaces its old value; a tier
  change keeps the choices, which apply to all three tiers). `PATCH /api/trips/{id}` saves the title.
  Per-stop cost = `Breakdown.by_place` (`CostLine.place`).
- `mapdata.ts` (projection, colours, start cities, asset loaders), `images.ts` (Wikipedia photo lookup for
  destinations without `img`, 2 at a time, cached in localStorage – should move to the server before launch).
- Styles are inline as in the design; hover states and animations in `index.css`.

## Tests
`cd backend && .venv/bin/python -m pytest -q` (no network). Crawl4AI parts: `tests/test_live_extract.py`,
fallback/splitting: `tests/test_llm_fallback.py`.
