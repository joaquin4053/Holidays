import pytest

from app import cache
from app.models import TripRequest, TripResearch


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Tests never read or write the real .tmp/research_cache.sqlite (LLM answers, live pages, Tavily)."""
    monkeypatch.setattr(cache, "DB_PATH", tmp_path / "cache.sqlite")

P = lambda lo, hi: {"min": lo, "max": hi}
TIERS3 = lambda b, c, l: {"budget": P(*b), "comfort": P(*c), "luxury": P(*l)}


def place(name, attractions):
    return {
        "name": name, "country": "China", "known_for": "x", "summary": "x",
        "attractions": attractions,
        "local_food": [{"name": "dumplings", "description": "x"}],
        "accommodation_per_night": TIERS3((30, 40), (80, 100), (250, 300)),
        "meal_cost": TIERS3((3, 5), (10, 15), (40, 60)),
        "local_transport_per_day": TIERS3((2, 4), (8, 12), (40, 60)),
        "sources": [],
    }


def option(mode, hours, lo, hi):
    return {"mode": mode, "description": mode, "duration_hours": hours, "price": P(lo, hi)}


@pytest.fixture
def research() -> TripResearch:
    return TripResearch.model_validate({
        "places": [
            place("Shanghai", [
                {"id": "sh-bund", "name": "The Bund", "kind": "sight", "description": "x", "price": None},
                {"id": "sh-tower", "name": "Shanghai Tower", "kind": "sight", "description": "x", "price": P(25, 30)},
            ]),
            place("Beijing", [
                {"id": "bj-wall", "name": "Great Wall", "kind": "activity", "description": "x", "price": P(40, 60)},
            ]),
        ],
        "legs": [
            {"from_place": "Zurich", "to_place": "Shanghai", "sources": [], "options": [
                option("flight", 14, 600, 900)]},
            {"from_place": "Shanghai", "to_place": "Beijing", "sources": [], "options": [
                option("flight", 5, 80, 150),   # fastest
                option("train", 6, 80, 90),     # best value
                option("bus", 14, 30, 40),      # cheapest
            ]},
            {"from_place": "Beijing", "to_place": "Zurich", "sources": [], "options": [
                option("flight", 13, 600, 900)]},
        ],
        "nights": [{"place": "Shanghai", "nights": 3}, {"place": "Beijing", "nights": 3}],
        "days": [
            {"day": 1, "place": "Shanghai", "title": "x", "attraction_ids": ["sh-bund", "sh-tower"], "notes": ""},
            {"day": 4, "place": "Beijing", "title": "x", "attraction_ids": ["bj-wall", "unknown-id"], "notes": ""},
        ],
    })


@pytest.fixture
def request_() -> TripRequest:
    return TripRequest(
        origin="Zurich", stops=[{"name": "Shanghai"}, {"name": "Beijing"}],
        days=7, travelers=2, budget=4000, currency="CHF", trip_type="backpacking", tier="comfort",
    )
