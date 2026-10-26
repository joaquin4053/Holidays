"""Compare two perf runs (tools/perf_run.py) for completeness and plausibility of the trip plan.

Usage (from project root):
  backend/.venv/bin/python tools/compare_trips.py .tmp/perf/baseline.json .tmp/perf/<label>.json
Prints a side-by-side table; lines marked "!" got worse (fewer items, missing data, or a price
that moved more than 35 %). Read both plans as well - the numbers can't judge wording.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MOVE = 0.35  # price change that is flagged for a closer look


def _range(p: dict | None) -> str:
    return f"{p['min']:.0f}-{p['max']:.0f}" if p else "-"


def _mid(p: dict | None) -> float | None:
    return (p["min"] + p["max"]) / 2 if p else None


def metrics(run: dict) -> dict[str, tuple[str, float | None, str]]:
    """name -> (shown value, number to compare, 'more'|'price')"""
    out: dict[str, tuple[str, float | None, str]] = {}
    r = run.get("research")
    if not r:
        return {"plan": ("missing", 0, "more")}
    for p in r["places"]:
        n = p["name"]
        priced = sum(1 for a in p["attractions"] if a.get("price"))
        out[f"{n}: attractions"] = (str(len(p["attractions"])), len(p["attractions"]), "more")
        out[f"{n}: attractions with price"] = (str(priced), priced, "more")
        out[f"{n}: local dishes"] = (str(len(p["local_food"])), len(p["local_food"]), "more")
        out[f"{n}: sources"] = (str(len(p.get("sources", []))), len(p.get("sources", [])), "more")
        for field in ("accommodation_per_night", "meal_cost", "local_transport_per_day"):
            for tier in ("budget", "comfort", "luxury"):
                v = p[field][tier]
                out[f"{n}: {field} {tier}"] = (_range(v), _mid(v), "price")
    for leg in r["legs"]:
        key = f"{leg['from_place']}->{leg['to_place']}"
        modes = sorted(o["mode"] for o in leg["options"])
        out[f"{key}: options"] = (",".join(modes), len(modes), "more")
        for o in leg["options"]:
            out[f"{key}: {o['mode']} price/person"] = (_range(o["price"]), _mid(o["price"]), "price")
            out[f"{key}: {o['mode']} hours"] = (f"{o['duration_hours']:.1f}", o["duration_hours"], "price")
    days = r["days"]
    out["days"] = (str(len(days)), len(days), "more")
    out["days with own title"] = (str(sum(1 for d in days if d["title"] and not d["title"].startswith(("Explore ",)))),
                                  sum(1 for d in days if d["title"]), "more")
    out["attractions scheduled"] = (str(sum(len(d["attraction_ids"]) for d in days)),
                                    sum(len(d["attraction_ids"]) for d in days), "more")
    out["days with notes"] = (str(sum(1 for d in days if d["notes"])), sum(1 for d in days if d["notes"]), "more")
    rep = run.get("report")
    if rep:
        for tier, b in rep["tiers"].items():
            out[f"total {tier} ({rep['currency']})"] = (_range(b["total"]), _mid(b["total"]), "price")
    return out


def main(a_path: str, b_path: str) -> None:
    a, b = (json.loads(Path(p).read_text()) for p in (a_path, b_path))
    ma, mb = metrics(a), metrics(b)
    print(f"{'':44} {a['label']:>22} {b['label']:>22}")
    print(f"{'total seconds':44} {a['total_seconds']:>22.1f} {b['total_seconds']:>22.1f}")
    ua, ub = a["usage"], b["usage"]
    print(f"{'LLM requests':44} {ua['llm_calls']:>22} {ub['llm_calls']:>22}")
    print(f"{'LLM tokens (prompt+output)':44} {ua['input_tokens'] + ua['output_tokens']:>22,} "
          f"{ub['input_tokens'] + ub['output_tokens']:>22,}")
    worse = 0
    for key in list(dict.fromkeys([*ma, *mb])):
        va, vb = ma.get(key), mb.get(key)
        flag = " "
        if va and not vb:
            flag = "!"
        elif va and vb and va[1] is not None and vb[1] is not None:
            if va[2] == "more" and vb[1] < va[1]:
                flag = "!"
            elif va[2] == "price" and va[1] and abs(vb[1] - va[1]) / va[1] > MOVE:
                flag = "!"
        worse += flag == "!"
        print(f"{flag} {key[:42]:42} {(va[0] if va else '-'):>22} {(vb[0] if vb else '-'):>22}")
    print(f"\n{worse} line(s) flagged with '!' - check them (fewer items, missing, or price moved > {MOVE:.0%}).")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
