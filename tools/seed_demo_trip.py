"""Create a finished demo trip from test fixture data - no API calls, no cost.

Usage (from project root):  backend/.venv/bin/python tools/seed_demo_trip.py
Then open http://localhost:5173/trip/demo
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import budget, trips  # noqa: E402
from app.models import TripRequest, TripResearch  # noqa: E402
from tests.conftest import option, place  # noqa: E402

P = lambda lo, hi: {"min": lo, "max": hi}  # noqa: E731

research = TripResearch.model_validate({
    "places": [
        place("Shanghai", [
            {"id": "sh-bund", "name": "The Bund", "kind": "sight", "description": "Colonial waterfront promenade", "price": None},
            {"id": "sh-tower", "name": "Shanghai Tower", "kind": "sight", "description": "Observation deck, 118th floor", "price": P(25, 30)},
            {"id": "sh-cooking", "name": "Dumpling cooking class", "kind": "activity", "description": "Make xiaolongbao", "price": P(45, 60)},
        ]),
        place("Beijing", [
            {"id": "bj-wall", "name": "Great Wall at Mutianyu", "kind": "activity", "description": "Hike + cable car", "price": P(40, 60)},
            {"id": "bj-city", "name": "Forbidden City", "kind": "sight", "description": "Imperial palace", "price": P(8, 10)},
        ]),
    ],
    "legs": [
        {"from_place": "Zurich", "to_place": "Shanghai", "sources": [], "options": [
            option("flight", 14, 600, 900), option("flight", 18, 450, 600)]},
        {"from_place": "Shanghai", "to_place": "Beijing", "sources": [], "options": [
            option("flight", 5, 80, 150), option("train", 6, 80, 90), option("bus", 14, 30, 40)]},
        {"from_place": "Beijing", "to_place": "Zurich", "sources": [], "options": [
            option("flight", 13, 600, 900), option("flight", 17, 450, 600)]},
    ],
    "nights": [{"place": "Shanghai", "nights": 3}, {"place": "Beijing", "nights": 3}],
    "days": [
        {"day": 1, "place": "Shanghai", "title": "Arrival", "attraction_ids": ["sh-bund"], "notes": "Flight from Zurich"},
        {"day": 2, "place": "Shanghai", "title": "Skyline & food", "attraction_ids": ["sh-tower", "sh-cooking"], "notes": ""},
        {"day": 4, "place": "Beijing", "title": "Train north", "attraction_ids": ["bj-city"], "notes": "High-speed train"},
        {"day": 5, "place": "Beijing", "title": "Great Wall", "attraction_ids": ["bj-wall"], "notes": ""},
    ],
})
req = TripRequest(origin="Zurich", stops=[{"name": "Shanghai"}, {"name": "Beijing"}], days=7,
                  travelers=2, budget=2500, currency="CHF", trip_type="culture", tier="comfort")
rate = 0.83
trip = trips.Trip(id="demo", status="done", request=req, research=research, usd_rate=rate,
                  report=budget.build_report(research, req, rate))
trips._save(trip)
print("Demo trip saved -> http://localhost:5173/trip/demo")
