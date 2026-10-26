"""Test live-price sites from the command line (uses backend/app/live_prices.py, no cache).

Opens each site in a local headless browser and saves the visible page text, so you can check which
sites still deliver prices. Free, except the Trip.com city-ID lookup: 1 Tavily credit per new city
(cached forever; pass --tripcom-city-id to skip it).

Usage (from project root):
  backend/.venv/bin/python tools/scrape_live_prices.py --site all --place Rome --origin Zurich \
      --checkin 2026-11-10 --checkout 2026-11-13 --people 2 [--origin-iata ZRH --dest-iata FCO] \
      [--tripcom-city-id 343] [--tourism-url https://www.turismoroma.it/en]
--site app = only the sites the trip planner uses. Saves .tmp/live_prices/<site>_<place>_<checkin>.json
and prints one status line per site.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import live_prices as lp  # noqa: E402
from app.config import setup_logging  # noqa: E402
from app.models import Usage  # noqa: E402

OUT_DIR = ROOT / ".tmp" / "live_prices"


async def build_query(args: argparse.Namespace, sites: list[str], usage: Usage) -> lp.Query:
    q = lp.Query(place=args.place, checkin=date.fromisoformat(args.checkin), checkout=date.fromisoformat(args.checkout),
                 people=args.people, origin=args.origin, tourism_url=args.tourism_url,
                 iata=(args.origin_iata, args.dest_iata) if args.origin_iata and args.dest_iata else None)
    city, origin_city = lp.city_of(args.place), lp.city_of(args.origin)
    if city:
        q.hostelworld = lp.hostelworld_path(city)
    if "tripcom" in sites:
        q.tripcom = ((str(args.tripcom_city_id), lp.slug(args.place)) if args.tripcom_city_id
                     else await lp.tripcom_city(args.place, usage))
    if "flixbus" in sites:
        async with httpx.AsyncClient(timeout=15) as client:
            ids = await lp.flixbus_city_id(client, args.origin), await lp.flixbus_city_id(client, args.place)
        q.flixbus = ids if all(ids) else None
    if "billiger_mietwagen" in sites and origin_city:
        q.car_city_de = await lp.german_name(origin_city)
    return q


async def main(args: argparse.Namespace) -> None:
    setup_logging()
    sites = {"all": list(lp.SITES), "app": lp.PLACE_SITES + lp.LEG_SITES}.get(args.site[0], args.site)
    usage = Usage()
    q = await build_query(args, sites, usage)
    if usage.search_credits:
        print(f"Trip.com city lookup used {usage.search_credits} Tavily credit(s): {q.tripcom}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    async with lp.session(enabled=True) as live:
        if live is None:
            sys.exit("Browser did not start: run `backend/.venv/bin/crawl4ai-setup`")
        for site in sites:
            url = lp.SITES[site].url(q)
            if not url:
                print(f"{site:19} skipped  (missing input or ID)")
                continue
            r = await live.fetch(site, url, use_cache=False)
            if site == "tripcom" and q.tripcom and q.tripcom[1].replace("-", " ") not in r.title.lower():
                r.status = "wrong_city"
            out = OUT_DIR / f"{site}_{lp.slug(q.place)}_{q.checkin}.json"
            out.write_text(r.model_dump_json(indent=2))
            print(f"{site:19} {r.status:10} http={r.http_status} items={len(r.items):>3} chars={len(r.text):>6} title={r.title[:45]!r}"
                  + (f" {r.error}" if r.error else ""), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", nargs="+", default=["app"], choices=["all", "app", *lp.SITES])
    ap.add_argument("--place", required=True)
    ap.add_argument("--origin", default="Zurich")
    ap.add_argument("--checkin", required=True)
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--people", type=int, default=2)
    ap.add_argument("--origin-iata")
    ap.add_argument("--dest-iata")
    ap.add_argument("--tripcom-city-id", type=int)
    ap.add_argument("--tourism-url")
    asyncio.run(main(ap.parse_args()))
