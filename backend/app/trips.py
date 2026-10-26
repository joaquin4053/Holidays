"""Trip jobs: state, persistence (.tmp/trips/*.json) and the planning pipeline."""
from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel

from . import budget, currency
from .agent.synthesize import plan_trip
from .config import TRIPS_DIR
from .models import Adjustments, BudgetReport, Tier, TripRequest, TripResearch, Usage

Status = Literal["queued", "researching", "synthesizing", "done", "error"]


class Event(BaseModel):
    seq: int
    ts: float
    message: str


class Trip(BaseModel):
    id: str
    status: Status = "queued"
    request: TripRequest
    events: list[Event] = []
    research: TripResearch | None = None
    usd_rate: float | None = None
    report: BudgetReport | None = None
    error: str | None = None
    usage: Usage = Usage()
    title: str | None = None  # set by the user on the results page; None = automatic title


_trips: dict[str, Trip] = {}


def _save(trip: Trip) -> None:
    TRIPS_DIR.mkdir(parents=True, exist_ok=True)
    (TRIPS_DIR / f"{trip.id}.json").write_text(trip.model_dump_json(indent=2))


def get(trip_id: str) -> Trip | None:
    if trip_id in _trips:
        return _trips[trip_id]
    path = TRIPS_DIR / f"{trip_id}.json"
    if trip_id.isalnum() and path.exists():
        trip = Trip.model_validate_json(path.read_text())
        _trips[trip_id] = trip
        return trip
    return None


def create(req: TripRequest) -> Trip:
    trip = Trip(id=uuid.uuid4().hex[:12], request=req)
    _trips[trip.id] = trip
    _save(trip)
    return trip


def _emit(trip: Trip, message: str) -> None:
    trip.events.append(Event(seq=len(trip.events), ts=time.time(), message=message))


async def run(trip: Trip) -> None:
    req = trip.request
    try:
        trip.status = "researching"
        _emit(trip, "Starting web research")
        trip.research = await plan_trip(req, trip.usage, lambda m: _emit(trip, m))

        _emit(trip, f"Converting prices to {req.currency}")
        trip.usd_rate = await currency.usd_rate(req.currency)
        trip.report = budget.build_report(trip.research, req, trip.usd_rate)
        trip.status = "done"
        u = trip.usage
        _emit(trip, f"Done ({u.search_credits} search credits, {u.llm_calls} LLM calls, "
                    f"LLM cost {u.llm_cost_usd:.2f} USD)")
    except Exception as e:  # surfaced to the UI; full trace in the server log
        trip.status = "error"
        trip.error = f"{type(e).__name__}: {e}"
        _emit(trip, f"Error: {trip.error}")
        raise
    finally:
        _save(trip)


def set_title(trip: Trip, title: str | None) -> None:
    trip.title = title.strip()[:120] if title and title.strip() else None
    _save(trip)


def recompute(trip: Trip, tier: Tier, adj: Adjustments) -> BudgetReport:
    assert trip.research and trip.usd_rate
    trip.report = budget.build_report(trip.research, trip.request, trip.usd_rate, tier, adj)
    _save(trip)
    return trip.report


def apply_saving(trip: Trip, option_id: str) -> BudgetReport:
    assert trip.research and trip.usd_rate and trip.report
    tier = trip.report.selected_tier
    adj = budget.apply_saving(
        trip.research, trip.request, tier, trip.usd_rate, trip.report.adjustments, option_id
    )
    return recompute(trip, tier, adj)
