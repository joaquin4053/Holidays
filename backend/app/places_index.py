"""Instant offline place search over GeoNames cities15000 (all places > 15,000 inhabitants).

Data: .tmp/geonames/, downloaded with tools/download_cities.py (CC-BY 4.0, geonames.org).
Search matches name prefixes in any language ("Rom", "Zürich", "Zurich", "Peking"),
ranked by match quality, then population. Reverse lookup picks the most important
city near a point.
"""
from __future__ import annotations

import bisect
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import ROOT

DATA_DIR = ROOT / ".tmp" / "geonames"
# Sections of a bigger city (districts) clutter results; whole cities come first anyway.
SKIP_FEATURE_CODES = {"PPLX", "PPLH", "PPLQ", "PPLW"}
DISTRICT_NAME = re.compile(r"\s\d{1,2}(\s|e\b|er\b|$)")  # "Paris 15 Vaugirard", "Marseille 01"


@dataclass(frozen=True)
class City:
    name: str
    country: str
    state: str
    lat: float
    lon: float
    population: int
    geoname_id: int = 0
    country_code: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "country": self.country, "state": self.state,
                "type": "city", "lat": self.lat, "lon": self.lon}


def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation: 'Zürich' -> 'zurich', 'São Paulo' -> 'sao paulo'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class PlacesIndex:
    def __init__(self, data_dir: Path = DATA_DIR):
        countries = self._read_countries(data_dir / "countryInfo.txt")
        admin1 = self._read_admin1(data_dir / "admin1CodesASCII.txt")
        self.cities: list[City] = []
        keys: list[tuple[str, int, int]] = []  # (normalized name, rank of name kind, city index)
        with open(data_dir / "cities15000.txt", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 15 or p[7] in SKIP_FEATURE_CODES or DISTRICT_NAME.search(p[1]):
                    continue
                idx = len(self.cities)
                self.cities.append(City(
                    name=p[1],
                    country=countries.get(p[8], p[8]),
                    state=admin1.get(f"{p[8]}.{p[10]}", ""),
                    lat=float(p[4]),
                    lon=float(p[5]),
                    population=int(p[14] or 0),
                    geoname_id=int(p[0]),
                    country_code=p[8],
                ))
                seen: set[str] = set()
                for kind, names in ((0, [p[1], p[2]]), (1, p[3].split(",") if p[3] else [])):
                    for n in names:
                        key = normalize(n)
                        if len(key) >= 2 and key not in seen:
                            seen.add(key)
                            keys.append((key, kind, idx))
        keys.sort()
        self._keys = [k for k, _, _ in keys]
        self._entries = [(kind, idx) for _, kind, idx in keys]
        self._countries_norm = {normalize(c) for c in countries.values()}

    @staticmethod
    def _read_countries(path: Path) -> dict[str, str]:
        out = {}
        for line in open(path, encoding="utf-8"):
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) > 4:
                out[p[0]] = p[4]
        return out

    @staticmethod
    def _read_admin1(path: Path) -> dict[str, str]:
        out = {}
        for line in open(path, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) > 1:
                out[p[0]] = p[1]
        return out

    # Bonus per match type, added to log10(population): an exact primary name wins, a
    # primary-name prefix beats a foreign-language alias unless the city is far bigger.
    BONUS = {(True, 0): 3.0, (False, 0): 2.0, (True, 1): 1.5, (False, 1): 0.0}

    def _prefix(self, prefix: str) -> dict[int, float]:
        """city index -> best score among its names starting with `prefix`."""
        best: dict[int, float] = {}
        i = bisect.bisect_left(self._keys, prefix)
        while i < len(self._keys) and self._keys[i].startswith(prefix):
            kind, idx = self._entries[i]
            score = math.log10(max(self.cities[idx].population, 1)) + self.BONUS[(self._keys[i] == prefix, kind)]
            if score > best.get(idx, -math.inf):
                best[idx] = score
            i += 1
        return best

    def search(self, query: str, limit: int = 8) -> list[City]:
        q = normalize(query)
        if len(q) < 2:
            return []
        matches = self._prefix(q)
        rest = ""
        if not matches and " " in q:
            # "rome italy" / "paris, france": match the first words, filter by country/state.
            words = q.split()
            for cut in range(len(words) - 1, 0, -1):
                matches = self._prefix(" ".join(words[:cut]))
                if matches:
                    rest = " ".join(words[cut:])
                    break
        ranked = sorted(matches.items(), key=lambda kv: -kv[1])
        out = []
        for idx, _ in ranked:
            c = self.cities[idx]
            if rest and not (normalize(c.country).startswith(rest) or normalize(c.state).startswith(rest)):
                continue
            out.append(c)
            if len(out) == limit:
                break
        return out

    def nearest(self, lat: float, lon: float, radius_km: float = 60) -> City | None:
        """Most important city near the point: population weighted by distance."""
        best, best_score = None, -math.inf
        cos_lat = math.cos(math.radians(lat))
        for c in self.cities:
            dlat = c.lat - lat
            if abs(dlat) > radius_km / 111:
                continue
            dlon = (c.lon - lon + 180) % 360 - 180
            dist = 111.2 * math.hypot(dlat, dlon * cos_lat)
            if dist > radius_km:
                continue
            score = math.log10(max(c.population, 1)) - dist / 15
            if score > best_score:
                best, best_score = c, score
        return best


@lru_cache(maxsize=1)
def get_index() -> PlacesIndex | None:
    """Loaded once; None if the data has not been downloaded yet (geo.py falls back to Photon)."""
    if not (DATA_DIR / "cities15000.txt").exists():
        return None
    return PlacesIndex()
