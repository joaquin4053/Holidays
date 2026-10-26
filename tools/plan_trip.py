"""Run the full planning pipeline from the command line (costs API credits).

Usage (from project root):
  backend/.venv/bin/python tools/plan_trip.py tools/examples/china.json
Prints progress, saves the trip to .tmp/trips/<id>.json and prints the URL to view it.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import trips  # noqa: E402
from app.config import setup_logging  # noqa: E402
from app.models import TripRequest  # noqa: E402


async def main(path: str) -> None:
    setup_logging()
    req = TripRequest.model_validate(json.loads(Path(path).read_text()))
    trip = trips.create(req)
    printed = 0

    async def progress() -> None:
        nonlocal printed
        while trip.status not in ("done", "error"):
            for ev in trip.events[printed:]:
                print(f"[{trip.status}] {ev.message}", flush=True)
            printed = len(trip.events)
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(progress())
    try:
        await trips.run(trip)
    finally:
        await asyncio.sleep(0.6)
        watcher.cancel()
    r = trip.report
    print(f"\nTotal ({r.selected_tier}): {r.selected.total.min:.0f}-{r.selected.total.max:.0f} {r.currency}"
          f" | budget {r.budget:.0f} | over: {r.over_budget}")
    u = trip.usage
    by = ", ".join(f"{k} {v}" for k, v in u.llm_by_provider.items()) or "none"
    print(f"Usage: {u.search_credits} Tavily credits, {u.llm_calls} LLM calls ({by}; "
          f"{u.input_tokens:,} in / {u.output_tokens:,} out tokens), LLM cost {u.llm_cost_usd:.2f} USD")
    print(f"View: http://localhost:5173/trip/{trip.id}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
