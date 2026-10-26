"""Measure one complete trip run: time per step, LLM requests and tokens. Saves a reference file.

Usage (from project root):
  backend/.venv/bin/python tools/perf_run.py --label baseline [--cold-tavily] [--warm-live] [--warm-llm] [request.json]
  backend/.venv/bin/python tools/perf_run.py --show .tmp/perf/baseline.json   # print a saved run again
Live pages and LLM answers are not read from cache by default (free; LLM uses quota). --cold-tavily
also skips the Tavily cache: costs ~1 credit per query. Output: .tmp/perf/<label>.json (trip, timings, usage).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".tmp" / "perf"


def show(run: dict) -> None:
    timings = run["usage"]["timings"]
    t0 = run["started"]
    print(f"\n== {run['label']}: {run['total_seconds']:.1f} s total, status {run['status']}"
          + (f" ({run['error']})" if run.get("error") else ""))
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for t in timings:
        by_kind[t["kind"]].append(t)
    print(f"\n{'step':8} {'count':>5} {'sum s':>8} {'wall span s':>12}  (span = first start -> last end)")
    for kind in ("tavily", "live", "prep", "llm"):
        ts = by_kind.get(kind, [])
        if ts:
            span = max(t["start"] + t["seconds"] for t in ts) - min(t["start"] for t in ts)
            print(f"{kind:8} {len(ts):>5} {sum(t['seconds'] for t in ts):>8.1f} {span:>12.1f}")
    tav = by_kind.get("tavily", [])
    if tav:
        print(f"\nTavily: {sum(not t['cache'] for t in tav)} live queries, {sum(t['cache'] for t in tav)} from cache")
    print(f"\nLive pages (start = seconds after run start):\n{'site':19} {'start':>6} {'queued':>7} {'load s':>7} "
          f"{'status':10} {'items':>5} cache")
    for t in sorted(by_kind.get("live", []), key=lambda t: t["start"]):
        print(f"{t['label']:19} {t['start'] - t0:>6.1f} {t.get('queued', 0):>7.1f} {t['seconds']:>7.1f} "
              f"{t.get('status', ''):10} {t.get('items', 0):>5} {t['cache']}")
    print(f"\nLLM requests:\n{'schema':18} {'provider':10} {'start':>6} {'budget wait':>11} {'answer s':>8} "
          f"{'est tok':>7} {'prompt':>7} {'output':>7}")
    for t in sorted(by_kind.get("llm", []), key=lambda t: t["start"]):
        print(f"{t['label']:18} {t['provider']:10} {t['start'] - t0:>6.1f} {t.get('budget_wait', 0):>11.1f} "
              f"{t['seconds']:>8.1f} {t.get('estimate', 0):>7} {t.get('prompt_tokens') or '-':>7} "
              f"{t.get('completion_tokens') or '-':>7}")
    u = run["usage"]
    print(f"\nTotals: {u['llm_calls']} LLM requests ({u['llm_by_provider']}), {u['input_tokens']:,} prompt + "
          f"{u['output_tokens']:,} output tokens, {u['search_credits']} Tavily credits")


async def run_trip(label: str, request_path: Path) -> dict:
    sys.path.insert(0, str(ROOT / "backend"))
    from app import trips
    from app.config import setup_logging
    from app.models import TripRequest

    setup_logging()
    req = TripRequest.model_validate(json.loads(request_path.read_text()))
    trip = trips.create(req)
    started = time.time()
    try:
        await trips.run(trip)
    except Exception as e:  # recorded in the run file; the comparison shows what is missing
        print(f"Run failed: {type(e).__name__}: {e}")
    return {
        "label": label, "request_file": str(request_path), "started": started,
        "total_seconds": round(time.time() - started, 2), "trip_id": trip.id, "status": trip.status,
        "error": trip.error, "env": {k: os.environ.get(k, "1") for k in ("LIVE_CACHE", "TAVILY_CACHE", "LLM_CACHE")},
        "usage": trip.usage.model_dump(),
        "research": trip.research.model_dump() if trip.research else None,
        "report": trip.report.model_dump() if trip.report else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="?", default=str(ROOT / "tools" / "examples" / "rome.json"))
    ap.add_argument("--label")
    ap.add_argument("--cold-tavily", action="store_true", help="skip the Tavily cache (costs credits)")
    ap.add_argument("--warm-live", action="store_true", help="read live pages from today's cache")
    ap.add_argument("--warm-llm", action="store_true", help="answer from the LLM cache when the prompt is unchanged")
    ap.add_argument("--show", help="print a saved run")
    args = ap.parse_args()
    if args.show:
        show(json.loads(Path(args.show).read_text()))
        return
    if not args.label:
        ap.error("--label is required for a run")
    # Must be set before the app is imported (config reads them once).
    os.environ["LIVE_CACHE"] = "1" if args.warm_live else "0"
    os.environ["TAVILY_CACHE"] = "0" if args.cold_tavily else "1"
    os.environ["LLM_CACHE"] = "1" if args.warm_llm else "0"  # measure real LLM requests by default
    result = asyncio.run(run_trip(args.label, Path(args.request)))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.label}.json"
    out.write_text(json.dumps(result, indent=1, default=str))
    show(result)
    print(f"\nSaved: {out.relative_to(ROOT)}  |  View: http://localhost:5173/trip/{result['trip_id']}")


if __name__ == "__main__":
    main()
