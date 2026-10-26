"""FastAPI app. Run: uvicorn app.main:app --reload --port 8000 (from backend/)."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import config, currency, geo, places_index, trips
from .models import Adjustments, BudgetReport, Tier, TripRequest

config.setup_logging()
log = logging.getLogger("holydays")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the offline city list once (~1.5 s, in a thread so the event loop stays free).
    if await asyncio.to_thread(places_index.get_index) is None:
        log.warning("City list missing - run tools/download_cities.py (falling back to Photon)")
    yield


app = FastAPI(title="Holydays travel planner", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    # Claude Design previews run on *.claudeusercontent.com and call this local backend directly.
    allow_origin_regex=r"https://.*\.claudeusercontent\.com",
    # Chrome's Private Network Access: a public page may only call localhost if the preflight allows it.
    allow_private_network=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _trip_or_404(trip_id: str) -> trips.Trip:
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    return trip


# --- lookups ---------------------------------------------------------------

@app.get("/api/currencies")
async def get_currencies() -> dict[str, str]:
    return {"USD": "United States Dollar", **await currency.currencies()}


@app.get("/api/geo/search")
async def geo_search(q: str) -> list[dict]:
    return await geo.search(q) if len(q.strip()) >= 2 else []


@app.get("/api/geo/reverse")
async def geo_reverse(lat: float, lon: float) -> dict:
    place = await geo.reverse(lat, lon)
    if not place:
        raise HTTPException(404, "No place found here")
    return place


# --- trips -----------------------------------------------------------------

@app.post("/api/trips")
async def create_trip(req: TripRequest, background: BackgroundTasks) -> dict:
    missing = [k for k in ("TAVILY_API_KEY", "OPENROUTER_API_KEY") if not getattr(config, k)]
    if missing:
        raise HTTPException(503, f"Missing in .env: {', '.join(missing)}")
    trip = trips.create(req)

    async def job() -> None:
        try:
            await trips.run(trip)
        except Exception:
            log.exception("Trip %s failed", trip.id)

    background.add_task(job)
    return {"id": trip.id}


@app.get("/api/trips/{trip_id}")
async def get_trip(trip_id: str) -> trips.Trip:
    return _trip_or_404(trip_id)


class TripPatch(BaseModel):
    title: str | None = None  # empty or null = back to the automatic title


@app.patch("/api/trips/{trip_id}")
async def patch_trip(trip_id: str, body: TripPatch) -> trips.Trip:
    trip = _trip_or_404(trip_id)
    trips.set_title(trip, body.title)
    return trip


@app.get("/api/trips/{trip_id}/events")
async def trip_events(trip_id: str) -> EventSourceResponse:
    trip = _trip_or_404(trip_id)

    async def stream():
        sent = 0
        while True:
            for ev in trip.events[sent:]:
                yield {"event": "progress", "data": ev.model_dump_json()}
            sent = len(trip.events)
            if trip.status in ("done", "error"):
                yield {"event": "status", "data": json.dumps({"status": trip.status, "error": trip.error})}
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(stream())


class AdjustBody(BaseModel):
    """Every field is optional. Adjustment fields replace the current value of that field (send the whole
    dict/list); fields left out stay as they are. Changing the tier keeps the user's choices."""
    tier: Tier | None = None
    apply_saving: str | None = None
    reset: bool = False
    leg_choice: dict[int, int] | None = None
    accommodation_tier: dict[str, Tier] | None = None
    food_tier: dict[str, Tier] | None = None
    skipped_attractions: list[str] | None = None
    added_attractions: list[str] | None = None
    nights: dict[str, int] | None = None


ADJUSTMENT_FIELDS = ("leg_choice", "accommodation_tier", "food_tier", "skipped_attractions",
                     "added_attractions", "nights")


@app.post("/api/trips/{trip_id}/adjust")
async def adjust(trip_id: str, body: AdjustBody) -> BudgetReport:
    trip = _trip_or_404(trip_id)
    if trip.status != "done" or not trip.report:
        raise HTTPException(409, "Trip is not finished yet")
    changes = {f: getattr(body, f) for f in ADJUSTMENT_FIELDS if getattr(body, f) is not None}
    if body.reset or body.tier or changes:
        tier = body.tier or trip.report.selected_tier
        adj = Adjustments() if body.reset else trip.report.adjustments
        if body.nights and any(n < 1 for n in body.nights.values()):
            raise HTTPException(400, "Nights must be at least 1")
        trips.recompute(trip, tier, adj.model_copy(update=changes))
    if body.apply_saving:
        try:
            trips.apply_saving(trip, body.apply_saving)
        except KeyError:
            raise HTTPException(400, "Unknown saving option")
    return trip.report
