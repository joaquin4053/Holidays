"""POST /api/trips/{id}/adjust with every adjustment field, PATCH title, per-stop totals."""
import pytest
from fastapi.testclient import TestClient

from app import budget, trips
from app.main import app


@pytest.fixture
def client(research, request_, tmp_path, monkeypatch):
    monkeypatch.setattr(trips, "TRIPS_DIR", tmp_path / "trips")
    trip = trips.create(request_)
    trip.status, trip.research, trip.usd_rate = "done", research, 1.0
    trip.report = budget.build_report(research, request_, 1.0)
    return TestClient(app), trip


def post(c, trip, **body):
    r = c.post(f"/api/trips/{trip.id}/adjust", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_leg_choice_and_classes_per_stop(client):
    c, trip = client
    rep = post(c, trip, leg_choice={"1": 2}, accommodation_tier={"Shanghai": "luxury"}, food_tier={"Beijing": "budget"})
    lc = next(x for x in rep["selected"]["leg_choices"] if x["leg_index"] == 1)
    assert lc["chosen"] == 2
    labels = [l["label"] for l in rep["selected"]["lines"]]
    assert any(l.startswith("Shanghai:") and "(luxury" in l for l in labels)
    assert "Beijing: food (budget)" in labels
    # the choices also apply to the other classes shown
    assert next(x for x in rep["tiers"]["budget"]["leg_choices"] if x["leg_index"] == 1)["chosen"] == 2


def test_fields_left_out_are_kept_and_tier_change_keeps_choices(client):
    c, trip = client
    post(c, trip, food_tier={"Beijing": "budget"})
    rep = post(c, trip, tier="luxury")
    assert rep["selected_tier"] == "luxury" and rep["adjustments"]["food_tier"] == {"Beijing": "budget"}
    rep = post(c, trip, reset=True)
    assert rep["adjustments"]["food_tier"] == {}


def test_add_unscheduled_and_skip_scheduled_attractions(client):
    c, trip = client
    rep = post(c, trip, skipped_attractions=["sh-tower"], added_attractions=["bj-wall"])
    acts = [l["label"] for l in rep["selected"]["lines"] if l["category"] == "activities"]
    assert "Shanghai: Shanghai Tower" not in acts and "Beijing: Great Wall" in acts


def test_cost_per_stop(client):
    c, trip = client
    rep = post(c, trip, tier="comfort")
    by_place = rep["selected"]["by_place"]
    assert set(by_place) == {"Shanghai", "Beijing"}
    stop_lines = [l for l in rep["selected"]["lines"] if l["place"] == "Shanghai"]
    assert by_place["Shanghai"]["min"] == pytest.approx(sum(l["total"]["min"] for l in stop_lines))
    assert all(l["place"] is None for l in rep["selected"]["lines"] if l["category"] == "transport")


def test_nights_must_be_positive(client):
    c, trip = client
    assert c.post(f"/api/trips/{trip.id}/adjust", json={"nights": {"Shanghai": 0}}).status_code == 400


def test_title_patch_saved_and_cleared(client):
    c, trip = client
    r = c.patch(f"/api/trips/{trip.id}", json={"title": "  Our China trip  "})
    assert r.status_code == 200 and r.json()["title"] == "Our China trip"
    assert c.patch(f"/api/trips/{trip.id}", json={"title": ""}).json()["title"] is None
