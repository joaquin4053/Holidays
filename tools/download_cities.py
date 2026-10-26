"""Download the GeoNames city list used for instant place search (free, CC-BY 4.0).

Usage (from project root):  backend/.venv/bin/python tools/download_cities.py
Writes .tmp/geonames/{cities15000.txt, countryInfo.txt, admin1CodesASCII.txt}.
"""
import io
import sys
import zipfile
from pathlib import Path

import httpx

BASE = "https://download.geonames.org/export/dump"
OUT = Path(__file__).resolve().parents[1] / ".tmp" / "geonames"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        r = client.get(f"{BASE}/cities15000.zip")
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            (OUT / "cities15000.txt").write_bytes(z.read("cities15000.txt"))
        for name in ("countryInfo.txt", "admin1CodesASCII.txt"):
            r = client.get(f"{BASE}/{name}")
            r.raise_for_status()
            (OUT / name).write_bytes(r.content)
    lines = sum(1 for _ in open(OUT / "cities15000.txt", encoding="utf-8"))
    print(f"Saved {lines} cities to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
