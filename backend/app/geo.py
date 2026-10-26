"""Place search and reverse geocoding.

Primary: the offline GeoNames index (places_index.py) - instant, no rate limits.
Fallback: Photon (photon.komoot.io) for places the index doesn't know (small villages,
landmarks). Photon is slow (1-2 s) and often times out, so failures return nothing
instead of an error.
"""
from __future__ import annotations

import logging

import httpx

from .places_index import get_index, normalize

API = "https://photon.komoot.io"
HEADERS = {"User-Agent": "holydays-travel-planner/0.1 (local dev)"}
log = logging.getLogger("holydays.geo")
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=4, headers=HEADERS)
    return _client


def _to_place(feature: dict) -> dict:
    p = feature["properties"]
    lon, lat = feature["geometry"]["coordinates"]
    if p.get("type") in ("city", "town", "village", "country", "state"):
        name = p.get("name")
    else:  # district, street, house ... -> use the surrounding city
        name = p.get("city") or p.get("county") or p.get("state") or p.get("name")
    return {
        "name": name or p.get("name") or "",
        "country": p.get("country", ""),
        "state": p.get("state", ""),
        "type": p.get("type", ""),
        "lat": lat,
        "lon": lon,
    }


async def _photon(path: str, params: dict) -> list[dict]:
    try:
        r = await _http().get(f"{API}{path}", params=params)
        r.raise_for_status()
        return r.json().get("features", [])
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Photon %s failed: %r", path, e)
        return []


async def search(q: str, limit: int = 6) -> list[dict]:
    index = get_index()
    results = [c.as_dict() for c in index.search(q, limit)] if index else []
    if results or len(q.strip()) < 3:
        return results
    # Nothing local (small village, landmark): ask Photon. It matches fuzzily, so keep
    # only places whose name contains the first word typed.
    first = normalize(q).split()[0]
    seen: set[tuple[str, str]] = set()
    for f in await _photon("/api/", {"q": q, "limit": limit * 2, "lang": "en"}):
        place = _to_place(f)
        key = (place["name"], place["country"])
        if place["name"] and key not in seen and first in normalize(place["name"]):
            seen.add(key)
            results.append(place)
    return results[:limit]


async def reverse(lat: float, lon: float) -> dict | None:
    index = get_index()
    city = index.nearest(lat, lon) if index else None
    if city:
        return city.as_dict()
    feats = await _photon("/reverse", {"lat": lat, "lon": lon, "lang": "en"})
    return _to_place(feats[0]) if feats else None
