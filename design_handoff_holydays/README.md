# Handoff: Holydays – Homepage, Trip Builder and Trip Results

## Overview
Holydays plans multi-stop trips. The user picks places on a world map, builds a route, sets basics and preferences, and sends the request to the existing FastAPI backend (`Holydays/backend`). The backend researches places, connections and prices, and the results page shows the plan with costs that the user can adjust.

This handoff covers three screens and connects them to the existing React/Vite frontend (`Holydays/frontend`) and backend:

| Design | Target in `frontend/src` |
|---|---|
| 4a Homepage (`Holydays Homepage.dc.html`) | `pages/Start.tsx` |
| 6a Trip Builder (`Holydays Trip Builder.dc.html`) | `pages/Wizard.tsx` (replace the current wizard) |
| 8c Trip Results (`Holydays Trip Results.dc.html`, section "Turn 8", frame **8c**) | `pages/Result.tsx` / `pages/TripPage.tsx` |

Ignore turn 7 (7a–7c) in the results file and `Holydays Multi-Stop.dc.html`. Those are earlier explorations.

## About the design files
The `.dc.html` files are **design references built in HTML**. They are prototypes that show the intended look and behaviour, not production code. Rebuild them in the existing frontend: React and TypeScript, with the types in `api.ts`, which mirror `backend/app/models.py`. Reuse `api.ts` for all requests. The logic class at the bottom of each `.dc.html` (`class Component extends DCLogic`) contains working reference code for data mapping, map projection, price recalculation and backend calls. Port it, don't copy it.

Open a file in a browser to try it. With `dev.sh` running, the files call `http://localhost:8000`. The results page falls back to `assets/demo-trip.json` when the backend is offline.

## Fidelity
**High fidelity.** Recreate colours, typography, spacing and interactions as specified. All styles are inline in the HTML, so exact values can be read directly from the markup.

## Flow
1. **Homepage (4a)**: map with country hover and continent labels, search bar at the bottom. This screen is approved, keep it as is.
2. **Trip Builder (6a)**: search, add places, plan in 3 steps, then `POST /api/trips`.
3. Once the POST returns `{id}`, navigate to the results page with `?trip=<id>`. The results page shows the loading screen and polls until `status === 'done'`.
4. **Results (8c)**: "Edit route" goes back to the builder with `?trip=<id>`. The builder loads `GET /api/trips/{id}` and pre-fills everything from `trip.request`.

## Screens

### 6a Trip Builder
- **Map**: full-bleed world map (`assets/world-map.svg`, countries from `assets/countries.json`), coloured by continent. In plan mode the map transforms to fit **only the stops**, not the origin: padding 130 px, target area x 470 / y 90 / w 780 / h 690 inside a 1280 px frame, zoom limited to 0.55–2.6. Route arcs are dotted (`stroke-dasharray 1 6`, 2 px, `#24231f`), and pins are numbered 26 px circles with a name pill.
- **Search** (closed state from 4a): results are destination cards from `assets/destinations.json` (1005 places, see Assets). "+ Add" puts a place on the route, "Plan route" opens the planner.
- **Planner panel**: `aside`, absolute, left 24, top 84, bottom 24, width 420, bg `#fffdf8`, radius 22, shadow `0 16px 48px rgba(40,30,20,.16)`, flex column.
  - Header: "Step N of 3" (12 px, 700, `#6b675e`), title 26 px / 700 / −0.03em (Your route / Basics / Preferences). Top right shows "Browse ideas" in step 1 and "← Edit route" / "← Basics" in later steps. Below the header is a progress bar with three 3 px segments, gap 4: done `#24231f`, open `#e4dfd4`.
  - **Step 1 – Your route**: origin input ("From"), then numbered stops with − nights + (shown as "x nights"), drag handle ⋮⋮ (pointer-drag reorder) and remove ×. Dotted connectors run between the stops. Below them is a dashed input "+ Add a stop, or click the map" with suggestions from `/api/geo/search`, and the toggle "Return to {origin}". Footer button: "Continue" plus "3 stops · 12 nights →", 54 px high, radius 14. It is disabled (`#cfc9bd`) without stops.
  - **Step 2 – Basics**: a route line that goes back to step 1 when clicked, then a plain list. Rows are at least 64 px high with a 1 px divider `#ece6da`:
    - When: "Exact dates" link (opens the date picker) and on the right − 14 days +, or the date range if dates are set.
    - Travellers: − 2 +.
    - Budget: "For the whole group", with an underlined input "CHF 6000" on the right.
    - Class: three radio rows (18 px ring, selected `5.5px solid #24231f`, idle `1.5px solid #b9b2a4`) with hints "Hostels, local food" / "Mid-range hotels" / "Top hotels, fine dining". These hints are design copy, not backend data.
    - Footer: "Continue · Preferences →".
  - **Step 3 – Preferences**: "What kind of trip? · Pick one" is a 2-column grid of radio rows for `trip_type`: city, culture, beach, adventure, backpacking, roadtrip, family. "What would you like to see? · Pick as many as you like" is a 2-column grid of checkbox rows for `interests`, the 12 values from `Wizard.tsx`. Rows are at least 44 px high, radius 12, idle bg `#f7f4ee`; selected rows get bg `#fffdf8` and `inset 0 0 0 2px #24231f`. Long labels wrap. Footer: "Plan my trip" plus "13 nights total →".
- **Request** sent by "Plan my trip": `{origin, stops:[{name, nights?}], return_to_origin, start_date+end_date | days, travelers, budget, currency:'CHF', tier, trip_type, interests}`.

### 8c Trip Results
- **Loading overlay**: route, "Planning your trip", progress bar (8 px, radius 999) with the latest backend event message. Use `GET /api/trips/{id}/events` (SSE) or poll `GET /api/trips/{id}` every 1.2 s. Fade out over 0.6 s.
- **Photo header** (400 px): one photo panel per stop, side by side with 3 px gaps, each with a pill "1 · Shanghai". Gradient overlay `linear-gradient(rgba(20,18,15,.45), 0 26%, .4 52%, .88)`. Bottom left: an **editable title** input (48 px / 700, white; transparent until hover or focus). The title is generated automatically: 1–3 countries → "China trip" / "China & Japan trip"; more countries on one continent → "Asia trip"; otherwise "Multi-country trip". "Reset" appears when the title was edited. Below the title: the route (18 px / 600) and meta (dates · nights · travellers · type). Bottom right: "Estimated total · {tier}", the mid total at 60 px, and the price per person.
- **Body**: grid `minmax(0,1fr) 340px`, gap 32, padding 32 / 40.
  - **Your stops**: the route alternates legs and stops.
    - **Leg row**: short dotted vertical line (20 px), then the transport options in equal columns (never wrapping, max-width 620, centred). No "from → to" text. Each option shows a mode icon (flight, train, bus, ferry, car), duration and price, plus tags Fastest (`#e6e3ef` / `#4b4d78`), Cheapest (`#e3eadb` / `#3e5a2c`) and Best value (`#f3e6d6` / `#8a5a1c`). The selected option is shown by a `2px #24231f` ring and a filled check circle top right, not by fill colour. Clicking an option recalculates all prices on the client.
    - **Stop card**: grid `180px 1fr 210px`, radius 22. Photo on the left with a number badge. In the middle: name 26 px / 700 and "China · 3 nights", plus a small list with one word and one average value per line ("Stay CHF 75", "Meal CHF 12", "Local CHF 9"). On the right: "Total" and the stop cost at 32 px. The whole card is clickable and opens the stop modal.
  - **Side column** (sticky): the cost panel (range total, per person, status pill over/under budget, Budget / Comfort / Luxury segmented control, category bars, "Budget CHF x", "Save trip"), with "Ways to save" below it.
- **Stop modal** (one page, no tabs): 1000 px wide, radius 28, backdrop `rgba(20,18,15,.5)` with a 3 px blur. Header photo 200 px with "Stop 1 · China · 3 nights", the name at 46 px and "Cost at this stop". Below it, `known_for` and `summary` when present. Then two columns:
  - Left (1.45fr): **Things to do** with filters All / Sights / Activities and "2 of 3 in your plan · CHF 120 for 2 travellers". Each row has a See/Do badge, name, a day tag (Day 2, Added or Not in plan), description, price and Add/Remove. Below that, the **Day plan**: day label, title, attractions and notes.
  - Right (1fr): **Your stay** card with Hotel and Food class pickers per stop (Budget/Comfort/Luxury with avg prices) and local transport per day. Then **What to eat** (`local_food`) and **Sources** chips (from `sources`, showing hostnames).
  - Footer: "← {previous stop}" and "{next stop} →". Esc or a click on the backdrop closes the modal.

## Client-side price recalculation (port from `resultVals()` → `adj()`)
For every tier, start from `report.tiers[k].lines` and apply:
- **Leg choice**: replace that leg's transport line with `option.price × travellers × usd_rate`.
- **Per-stop hotel class**: replace the accommodation line with `accommodation_per_night[tier] × nights × ceil(travellers/2) × usd_rate`.
- **Per-stop food class**: replace the food line with `meal_cost[tier] × nights × 3 × travellers × usd_rate`.
- **Attraction toggles**: remove or add the line `"{Place}: {Attraction}"` with `price × travellers × usd_rate`.

Then rebuild `by_category` and `total` from the lines. The rules match `backend/app/budget.py` (MEALS_PER_DAY = 3, rooms = (travellers+1)//2).

## Backend work needed
1. **Extend `POST /api/trips/{id}/adjust`** to accept the `Adjustments` fields that already exist in `models.py`: `leg_choice`, `accommodation_tier`, `food_tier`, `skipped_attractions` and `nights`. Today the endpoint only takes `tier`, `apply_saving` and `reset`. Then the frontend sends every change and uses the returned `BudgetReport` instead of recalculating locally.
2. **Trip title**: add an optional `title` to the trip and save it, either via adjust or a new `PATCH`. The prototype stores it in `localStorage` under `holydays-trip-titles`.
3. **Adding unscheduled attractions**: the budget only counts attractions that appear in `days`. Either add an `added_attractions` adjustment or let `skipped_attractions` work both ways.
4. **Per-stop cost**: the prototype sums the report lines whose label starts with `"{Place}:"`. A `by_place` total in `Breakdown` would make this reliable.

## State (results page)
`trip`, `tier`, `legPick{legIndex: optIndex}`, `placeAcc{place: tier}`, `placeFood{place: tier}`, `attOn{attractionId: bool}`, `openStop`, `attFilter`, `titles{tripId: string}`.

## Design tokens
- **Ink and text**: `#24231f` (ink), `#4a4740` (secondary), `#6b675e` (muted), `#9a958a` (faint).
- **Surfaces**: page `#f7f5f0`; cards `#fffdf8`; soft fills `#f7f4ee`, `#f4efe6`, `#ebe6dc`, `#f1ece2`, `#ebe5da`.
- **Lines**: `#ece6da`, `#e4dfd4`, `#e2dccf`, `#d8d3c8`, `#cfc9bd`, `#b9b2a4` (dotted connectors).
- **Accent**: `#bc7654` (terracotta), `#9c5536` (dark), `#e0a17f` (on dark).
- **Status**: under budget `#e3eadb` / `#3e5a2c`; over budget `#f6e1d8` / `#8f3b22`; error `#a8432a`.
- **Categories**: transport `#24231f`, stays `#bc7654`, food `#c8af83`, activities `#8a8cb2`, local transport `#979e75`.
- **Map continents**: N. America `#a1a899`, S. America `#c8af83`, Europe `#8a8cb2`, Africa `#c49a8e`, Asia `#979e75`, Oceania `#bc7654`. Non-route countries are drawn at 55 % toward `#f7f5f0`.
- **Fonts**: Bricolage Grotesque (500/700/800) for all UI in the frames, with the logo "Holydays" at 800 / 28 px / −0.03em. DM Sans for the body. Google Fonts.
- **Radii**: 999 (pills), 22–28 (panels and modal), 18–20 (cards), 12–16 (tiles, rows and buttons).
- **Shadows**: panel `0 16px 48px rgba(40,30,20,.16)`; card `0 1px 3px rgba(40,30,20,.06)`; modal `0 30px 80px rgba(20,18,15,.35)`.

## Assets
- `assets/destinations.json` has 1005 places: `{id, c (continent), mood City|Nature|Beach, name, country, season, img, src, file, lat, lon, cn, wiki?, landmark?}`.
  - 548 places have a fixed Wikimedia `img`.
  - The other 457 cities only have `wiki` (the Wikipedia title of the city's landmark). The prototypes resolve the photo at runtime through `https://en.wikipedia.org/api/rest_v1/page/summary/{title}`, with at most 2 parallel requests, backoff on 429 and a cache in `localStorage['holydays-img-cache-v2']`.
  - **Before launch**: resolve all images once on the server, write `img` into the JSON, and add **photo credits** (author and licence from Wikimedia). 9 older entries still have no photo.
  - Coordinates and the `season` of the new cities were set by hand (the season is copied from the country) and should be checked.
- `assets/countries.json`: country paths `{n, c, d, x, y, s}` for the SVG map (viewBox 0 0 1376 768). Projection: `x = 712.8 + 3.037·lon·plen(lat)`, `y = 385.3 − 3.534·lat` (see `proj` / `plen` in the logic).
- `assets/world-map.svg`: the vector map.
- `assets/demo-trip.json`: an example `GET /api/trips/{id}` response (Zürich → Shanghai → Beijing).
- Transport icons are Material-style inline SVG paths (`ICON` in the results logic).

## Files
- `Holydays Homepage.dc.html` – 4a
- `Holydays Trip Builder.dc.html` – 6a
- `Holydays Trip Results.dc.html` – 8c (turn 8), plus older 7a–7c
- `support.js` – runtime needed to open the `.dc.html` files in a browser
- `assets/` – data files listed above
