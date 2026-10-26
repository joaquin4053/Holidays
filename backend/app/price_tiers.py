"""Price tiers and transport options computed from live prices - in Python, no LLM.

Every function returns None when the live data is too thin; the LLM then estimates that value from the
search snippets as before. Amounts are converted to USD (rates = units of a currency per USD, from
app/currency.py). Rules, checked against the Rome baseline of 2026-10-08:
- Accommodation per double room per night, taxes included (Trip.com total / nights, Airbnb total /
  nights): budget = min..p10, comfort = p25..p75, luxury = p90..median of the top 10 %.
  Hostelworld is left out: its "Privates from" doesn't say whether it is per person or per room.
- Meal per person = average of breakfast, lunch and dinner built from Numbeo items; low/high from
  Numbeo's own ranges (the budget counts 3 meals a day, so a dinner price alone would overstate it).
- Local transport per person per day: Numbeo tickets and 5 km taxi rides shared by the travellers.
- Transport: flight (Google Flights) and bus (FlixBus) p25..p75 per person; door-to-door hours =
  median ride + access time. Train/car/ferry stay with the LLM (only "from" prices or no live data).
"""
from __future__ import annotations

import re
import statistics
from collections import Counter

from .live_extract import url_params
from .models import PriceRange, TierPrices, TransportOption

MIN_STAYS = 8         # offers needed for accommodation tiers
MIN_RIDES = 3         # offers needed for a transport option
TAXI_KM = 5           # typical city ride
ACCESS_HOURS = {"flight": 2.0, "bus": 0.5}  # to/from airport incl. check-in; to/from bus station
NUMBEO = {
    "inexpensive": "Meal at an Inexpensive Restaurant",
    "mid_for_two": "Meal for Two at a Mid-Range Restaurant",
    "fast_food": "Combo Meal",
    "coffee": "Cappuccino",
    "ticket": "One-Way Ticket",
    "taxi_start": "Taxi Start",
    "taxi_km": "Taxi 1 km",
}


def _usd(amount: float, cur: str, rates: dict[str, float]) -> float | None:
    rate = rates.get(cur or "USD", 1.0 if cur in ("", "USD", None) else None)
    return amount / rate if rate else None


def _range(lo: float, hi: float) -> PriceRange:
    lo, hi = sorted((lo, hi))
    return PriceRange(min=round(lo), max=round(max(hi, lo)))


def _ok(results: list, site: str) -> list:
    return [r for r in results if r.site == site and r.status == "ok" and r.items]


def accommodation(results: list, rates: dict[str, float]) -> TierPrices | None:
    nightly: list[float] = []
    for r in _ok(results, "tripcom") + _ok(results, "airbnb"):
        nights = url_params(r.url).get("nights") or 1
        for i in r.items:
            amount = i.get("price_total") / nights if i.get("price_total") else i.get("price_night")
            if amount and (usd := _usd(amount, i.get("currency", "USD"), rates)):
                nightly.append(usd)
    if len(nightly) < MIN_STAYS:
        return None
    nightly.sort()
    d = statistics.quantiles(nightly, n=20)  # d[1] = p10, d[4] = p25, d[14] = p75, d[17] = p90
    top = [v for v in nightly if v >= d[17]]
    return TierPrices(budget=_range(nightly[0], d[1]), comfort=_range(d[4], d[14]),
                      luxury=_range(d[17], statistics.median(top)))


def _numbeo(results: list, rates: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """key -> (low, typical, high) in USD."""
    out = {}
    for r in _ok(results, "numbeo"):
        for i in r.items:
            for key, name in NUMBEO.items():
                if key not in out and i["item"].startswith(name):
                    typical = _usd(i["price"], i.get("currency", "USD"), rates)
                    lo_hi = re.findall(r"[\d.]+", (i.get("range") or "").replace(",", ""))
                    lo, hi = (_usd(float(x), i.get("currency", "USD"), rates) for x in lo_hi[:2]) if len(lo_hi) >= 2 \
                        else (typical, typical)
                    if typical:
                        out[key] = (lo or typical, typical, hi or typical)
    return out


def meals(results: list, rates: dict[str, float]) -> TierPrices | None:
    n = _numbeo(results, rates)
    if not all(k in n for k in ("inexpensive", "mid_for_two", "fast_food", "coffee")):
        return None

    def day(which: int) -> dict[str, float]:  # 0 = low, 1 = typical, 2 = high
        coffee, fast, cheap, mid = (n[k][which] for k in ("coffee", "fast_food", "inexpensive", "mid_for_two"))
        return {"budget": (coffee * 1.5 + fast + cheap) / 3,             # coffee + pastry, fast food, simple dinner
                "comfort": (coffee * 3 + cheap + mid / 2) / 3,            # café breakfast, simple lunch, mid-range dinner
                "luxury": (cheap + mid / 2 + mid / 2 * 2.5) / 3}          # hotel breakfast, mid-range lunch, fine dining
    lo, hi = day(0), day(2)
    return TierPrices(**{t: _range(lo[t], hi[t]) for t in ("budget", "comfort", "luxury")})


def local_transport(results: list, rates: dict[str, float], people: int) -> TierPrices | None:
    n = _numbeo(results, rates)
    if not all(k in n for k in ("ticket", "taxi_start", "taxi_km")):
        return None
    people = max(people, 1)

    def tiers(w: int) -> dict[str, float]:
        ticket, taxi = n["ticket"][w], n["taxi_start"][w] + TAXI_KM * n["taxi_km"][w]
        return {"budget": ticket * (2 if w == 0 else 4),
                "comfort": 2 * ticket + taxi / people,
                "luxury": 3 * taxi / people}
    lo, hi = tiers(0), tiers(2)
    return TierPrices(**{t: _range(lo[t], hi[t]) for t in ("budget", "comfort", "luxury")})


def place_prices(results: list, rates: dict[str, float], people: int) -> dict[str, TierPrices]:
    """PlaceDraft price fields that live data covers; missing ones are left to the LLM."""
    found = {"accommodation_per_night": accommodation(results, rates), "meal_cost": meals(results, rates),
             "local_transport_per_day": local_transport(results, rates, people)}
    return {k: v for k, v in found.items() if v is not None}


def place_price_sources(results: list, fields: set[str]) -> dict[str, str]:
    """Live page behind each computed field (the search page with the same dates and travellers)."""
    pages = {site: r.url for site in ("tripcom", "airbnb", "numbeo") for r in _ok(results, site)[:1]}
    stay = pages.get("tripcom") or pages.get("airbnb")
    found = {"accommodation_per_night": stay, "meal_cost": pages.get("numbeo"),
             "local_transport_per_day": pages.get("numbeo")}
    return {k: v for k, v in found.items() if k in fields and v}


def _p25_p75(values: list[float]) -> tuple[float, float]:
    values = sorted(values)
    if len(values) < 4:
        return values[0], values[-1]
    q = statistics.quantiles(values, n=4)
    return q[0], q[2]


def leg_options(results: list, rates: dict[str, float]) -> dict[str, TransportOption]:
    """Flight and bus options from live data; other modes are left to the LLM."""
    out: dict[str, TransportOption] = {}
    flights = [i for r in _ok(results, "google_flights") for i in r.items]
    prices = [p for i in flights if (p := _usd(i["price_person"], i.get("currency", "USD"), rates))]
    if len(prices) >= MIN_RIDES:
        hrs = statistics.median(i["hours"] for i in flights if i.get("hours"))
        airlines = [a for a, _ in Counter(i["airline"] for i in flights if i.get("airline")).most_common(3)]
        stops = sorted({i["stops"] for i in flights if i.get("stops")}, key=lambda s: (s != "Nonstop", s))
        first = flights[0]
        route = f"{first['from']} -> {first['to']}" if first.get("from") and first.get("to") else ""
        out["flight"] = TransportOption(
            mode="flight", duration_hours=round(hrs + ACCESS_HOURS["flight"], 1), price=_range(*_p25_p75(prices)),
            description="; ".join(x for x in (", ".join(airlines), " / ".join(stops), route) if x)
                        + f" ({len(prices)} flights found, {hrs:.1f} h in the air)",
            source_url=_ok(results, "google_flights")[0].url)
    rides = [i for r in _ok(results, "flixbus") for i in r.items]
    prices = [p for i in rides if (p := _usd(i["price_person"], i.get("currency", "USD"), rates))]
    if len(prices) >= MIN_RIDES:
        hrs = statistics.median(i["hours"] for i in rides if i.get("hours"))
        transfers = sorted({i["transfers"] for i in rides if i.get("transfers")})
        out["bus"] = TransportOption(
            mode="bus", duration_hours=round(hrs + ACCESS_HOURS["bus"], 1), price=_range(*_p25_p75(prices)),
            description=f"FlixBus, {' / '.join(transfers) or 'direct'} ({len(prices)} rides found)",
            source_url=_ok(results, "flixbus")[0].url)
    return out


def currencies(results: list) -> set[str]:
    return {i.get("currency", "USD") for r in results if r.status == "ok" for i in r.items} - {"USD"}
