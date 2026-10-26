"""Price tiers and transport options computed in Python from live rows (no LLM)."""
import pytest

from app import price_tiers as pt
from app.live_prices import LiveResult

TRIP_URL = "https://www.trip.com/hotels/list?city=343&checkin=2027/03/12&checkout=2027/03/15&adult=2"
AIRBNB_URL = "https://www.airbnb.com/s/Rome/homes?checkin=2027-03-12&checkout=2027-03-15&adults=2"


def page(site, items, url="u", status="ok"):
    return LiveResult(site=site, category="x", url=url, status=status, items=items)


def hotels(totals):
    return page("tripcom", [{"name": f"H{i}", "price_night": t / 3 * 0.9, "price_total": t, "currency": "USD"}
                            for i, t in enumerate(totals)], TRIP_URL)


NUMBEO = page("numbeo", [
    {"item": "Meal at an Inexpensive Restaurant", "price": 16.8, "range": "13.44-22.39", "currency": "USD"},
    {"item": "Meal for Two at a Mid-Range Restaurant (Three Courses)", "price": 67.18, "range": "55.99-89.58",
     "currency": "USD"},
    {"item": "Combo Meal at McDonald's (or Equivalent Fast-Food Meal)", "price": 11.2, "range": "10.08-15.68",
     "currency": "USD"},
    {"item": "Cappuccino (Regular Size)", "price": 2.07, "range": "1.34-3.36", "currency": "USD"},
    {"item": "One-Way Ticket (Local Transport)", "price": 1.68, "range": "1.68-1.68", "currency": "USD"},
    {"item": "Taxi Start (Standard Tariff)", "price": 6.72, "range": "4.48-10.08", "currency": "USD"},
    {"item": "Taxi 1 km (Standard Tariff)", "price": 1.68, "range": "1.46-2.80", "currency": "USD"},
])


def test_accommodation_tiers_per_night_with_taxes():
    totals = [300 + 30 * i for i in range(20)]  # 100..290 per night
    t = pt.accommodation([hotels(totals)], {})
    assert t.budget.min == 100 and t.budget.max < t.comfort.min < t.comfort.max < t.luxury.min <= t.luxury.max
    assert 140 <= t.comfort.min <= 150 and 240 <= t.comfort.max <= 250


def test_accommodation_needs_enough_offers_and_converts_currency():
    assert pt.accommodation([hotels([300, 330, 360])], {}) is None
    eur = page("airbnb", [{"name": f"A{i}", "price_total": 270 + 9 * i, "price_night": 90 + 3 * i, "currency": "EUR"}
                          for i in range(10)], AIRBNB_URL)
    t = pt.accommodation([eur], {"EUR": 0.9})  # 1 USD = 0.9 EUR
    assert t.budget.min == 100  # 90 EUR / 0.9


def test_meals_are_a_day_average_not_a_dinner_price():
    m = pt.meals([NUMBEO], {})
    assert (m.budget.min, m.budget.max) == (9, 14)  # (1.34*1.5 + 10.08 + 13.44) / 3 = 8.5
    assert (m.comfort.min, m.comfort.max) == (15, 26)
    assert (m.luxury.min, m.luxury.max) == (37, 60)


def test_local_transport_shares_taxis_between_travellers():
    two, one = pt.local_transport([NUMBEO], {}, 2), pt.local_transport([NUMBEO], {}, 1)
    assert two.budget.min == 3 and two.comfort.max < one.comfort.max and two.luxury.max < one.luxury.max


def test_place_prices_only_what_live_data_covers():
    assert set(pt.place_prices([NUMBEO], {}, 2)) == {"meal_cost", "local_transport_per_day"}
    assert pt.place_prices([page("numbeo", [], status="blocked")], {}, 2) == {}


def test_leg_options_from_flights_and_buses():
    flights = page("google_flights", [
        {"airline": a, "stops": s, "from": "Zurich Airport", "to": "Rome FCO", "hours": h, "price_person": p,
         "currency": "USD"}
        for a, s, h, p in [("easyJet", "Nonstop", 1.6, 52), ("ITA", "Nonstop", 1.5, 200), ("easyJet", "Nonstop", 1.6, 107),
                           ("Air Serbia", "1 stop", 4.5, 76)]])
    buses = page("flixbus", [{"hours": 14.5, "transfers": "1 transfer", "price_person": p, "currency": "CHF"}
                             for p in (40, 45, 50)])
    opts = pt.leg_options([flights, buses], {"CHF": 0.8})
    f, b = opts["flight"], opts["bus"]
    assert f.mode == "flight" and f.duration_hours == pytest.approx(1.6 + 2.0) and f.price.min < f.price.max
    assert f.description.startswith("easyJet") and "Nonstop / 1 stop" in f.description
    assert (b.price.min, b.price.max) == (50, 62) and b.duration_hours == 15.0  # CHF -> USD, + 0.5 h


def test_too_few_rides_stay_with_the_llm():
    one = page("google_flights", [{"airline": "X", "stops": "Nonstop", "hours": 1, "price_person": 50,
                                   "currency": "USD"}])
    assert pt.leg_options([one], {}) == {}
