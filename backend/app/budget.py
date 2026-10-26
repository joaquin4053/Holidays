"""Deterministic budget engine.

Turns researched price ranges (USD) into per-tier cost breakdowns in the user's
currency, detects budget overruns and proposes things the user can give up.
No LLM involved - all math lives here so it is testable and reproducible.
"""
from __future__ import annotations

from .models import (
    TIERS,
    Adjustments,
    Breakdown,
    BudgetReport,
    CostLine,
    Leg,
    LegChoice,
    Money,
    PriceRange,
    SavingOption,
    Tier,
    TripRequest,
    TripResearch,
)

MEALS_PER_DAY = 3
DEFAULT_LEG_PICK: dict[Tier, str] = {"budget": "cheapest", "comfort": "best_value", "luxury": "fastest"}


def _mid(p: PriceRange) -> float:
    return (p.min + p.max) / 2


def _fmt(p: PriceRange, rate: float, suffix: str) -> str:
    lo, hi = round(p.min * rate), round(p.max * rate)
    return f"{lo}-{hi} {suffix}" if lo != hi else f"{lo} {suffix}"


def _money(lo: float, hi: float, rate: float) -> Money:
    return Money(min=round(lo * rate, 2), max=round(hi * rate, 2))


def classify_leg(leg: Leg) -> tuple[int, int, int]:
    """Return option indexes for (fastest, cheapest, best_value).

    Best value = lowest sum of price and duration, each normalised to the best
    option on that axis (1.0 = best).
    """
    opts = leg.options
    fastest = min(range(len(opts)), key=lambda i: opts[i].duration_hours)
    cheapest = min(range(len(opts)), key=lambda i: _mid(opts[i].price))
    min_price = max(_mid(opts[cheapest].price), 1.0)
    min_dur = max(opts[fastest].duration_hours, 0.1)
    best_value = min(
        range(len(opts)),
        key=lambda i: _mid(opts[i].price) / min_price + opts[i].duration_hours / min_dur,
    )
    return fastest, cheapest, best_value


def _nights_by_place(research: TripResearch, adj: Adjustments) -> dict[str, int]:
    nights = {n.place: n.nights for n in research.nights}
    nights.update(adj.nights)
    return nights


def _scheduled_attractions(research: TripResearch, adj: Adjustments) -> list[tuple[str, str]]:
    """(place, attraction_id) pairs the user does: the day plan plus added ones, minus skipped ones."""
    owner = {a.id: p.name for p in research.places for a in p.attractions}
    planned = [(day.place, aid) for day in research.days for aid in day.attraction_ids]
    added = [(owner[aid], aid) for aid in adj.added_attractions if aid in owner]
    seen: set[str] = set()
    out = []
    for place, aid in planned + added:
        if aid not in seen and aid not in adj.skipped_attractions:
            seen.add(aid)
            out.append((place, aid))
    return out


def compute_breakdown(
    research: TripResearch,
    req: TripRequest,
    tier: Tier,
    rate: float,
    adj: Adjustments | None = None,
) -> Breakdown:
    adj = adj or Adjustments()
    pax, rooms = req.travelers, req.rooms
    lines: list[CostLine] = []
    leg_choices: list[LegChoice] = []

    # Transport between places
    for i, leg in enumerate(research.legs):
        if not leg.options:
            continue
        fastest, cheapest, best_value = classify_leg(leg)
        default = {"fastest": fastest, "cheapest": cheapest, "best_value": best_value}[DEFAULT_LEG_PICK[tier]]
        chosen = adj.leg_choice.get(i, default)
        chosen = chosen if 0 <= chosen < len(leg.options) else default
        opt = leg.options[chosen]
        leg_choices.append(LegChoice(
            leg_index=i, from_place=leg.from_place, to_place=leg.to_place,
            fastest=fastest, cheapest=cheapest, best_value=best_value, chosen=chosen,
        ))
        lines.append(CostLine(
            category="transport",
            label=f"{leg.from_place} → {leg.to_place} ({opt.mode})",
            unit=_fmt(opt.price, rate, "/ person") + f", ~{opt.duration_hours:g} h",
            total=_money(opt.price.min * pax, opt.price.max * pax, rate),
        ))

    nights = _nights_by_place(research, adj)
    for place in research.places:
        n = nights.get(place.name, 0)
        if n <= 0:
            continue
        acc_tier = adj.accommodation_tier.get(place.name, tier)
        food_tier = adj.food_tier.get(place.name, tier)
        acc = getattr(place.accommodation_per_night, acc_tier)
        meal = getattr(place.meal_cost, food_tier)
        local = getattr(place.local_transport_per_day, tier)
        lines.append(CostLine(
            category="accommodation",
            label=f"{place.name}: {n} nights ({acc_tier}, {rooms} room{'s' if rooms > 1 else ''})",
            unit=_fmt(acc, rate, "/ night"),
            total=_money(acc.min * n * rooms, acc.max * n * rooms, rate),
            place=place.name,
        ))
        meals = n * MEALS_PER_DAY * pax
        lines.append(CostLine(
            category="food",
            label=f"{place.name}: food ({food_tier})",
            unit=_fmt(meal, rate, "/ meal"),
            total=_money(meal.min * meals, meal.max * meals, rate),
            place=place.name,
        ))
        lines.append(CostLine(
            category="local_transport",
            label=f"{place.name}: local transport",
            unit=_fmt(local, rate, "/ day"),
            total=_money(local.min * n * pax, local.max * n * pax, rate),
            place=place.name,
        ))

    attractions = {a.id: a for p in research.places for a in p.attractions}
    owner = {a.id: p.name for p in research.places for a in p.attractions}
    for _day_place, aid in _scheduled_attractions(research, adj):
        a = attractions.get(aid)
        place_name = owner.get(aid, _day_place)
        if not a or not a.price or a.price.max <= 0:
            continue
        lines.append(CostLine(
            category="activities",
            label=f"{place_name}: {a.name}",
            unit=_fmt(a.price, rate, "/ person"),
            total=_money(a.price.min * pax, a.price.max * pax, rate),
            place=place_name,
        ))

    by_cat: dict[str, Money] = {}
    by_place: dict[str, Money] = {}
    for line in lines:
        cur = by_cat.get(line.category, Money(min=0, max=0))
        by_cat[line.category] = Money(min=round(cur.min + line.total.min, 2), max=round(cur.max + line.total.max, 2))
        if line.place:
            cur = by_place.get(line.place, Money(min=0, max=0))
            by_place[line.place] = Money(min=round(cur.min + line.total.min, 2), max=round(cur.max + line.total.max, 2))
    total = Money(
        min=round(sum(m.min for m in by_cat.values()), 2),
        max=round(sum(m.max for m in by_cat.values()), 2),
    )
    return Breakdown(
        tier=tier, currency=req.currency, lines=lines,
        by_category=by_cat, total=total, leg_choices=leg_choices, by_place=by_place,
    )


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------

def _lower(tier: Tier) -> Tier | None:
    idx = TIERS.index(tier)
    return TIERS[idx - 1] if idx > 0 else None


def _candidate_adjustments(
    research: TripResearch, req: TripRequest, tier: Tier, adj: Adjustments, current: Breakdown
) -> list[tuple[str, str, Adjustments]]:
    """All single-step changes the user could make: (id, label, new adjustments)."""
    out: list[tuple[str, str, Adjustments]] = []

    for lc in current.leg_choices:
        leg = research.legs[lc.leg_index]
        for target, name in ((lc.cheapest, "cheapest"), (lc.best_value, "best value")):
            if target == lc.chosen:
                continue
            new = adj.model_copy(deep=True)
            new.leg_choice[lc.leg_index] = target
            opt = leg.options[target]
            out.append((
                f"leg:{lc.leg_index}:{target}",
                f"{leg.from_place} → {leg.to_place}: take {opt.mode} ({name}, ~{opt.duration_hours:g} h) instead",
                new,
            ))

    nights = _nights_by_place(research, adj)
    for place in research.places:
        n = nights.get(place.name, 0)
        if n <= 0:
            continue
        for field, kind in (("accommodation_tier", "Accommodation"), ("food_tier", "Food")):
            cur_tier = getattr(adj, field).get(place.name, tier)
            low = _lower(cur_tier)
            if low:
                new = adj.model_copy(deep=True)
                getattr(new, field)[place.name] = low
                out.append((f"{field}:{place.name}:{low}", f"{place.name}: {kind} {cur_tier} → {low}", new))
        if n > 1:
            new = adj.model_copy(deep=True)
            new.nights[place.name] = n - 1
            out.append((f"night:{place.name}", f"{place.name}: stay one night less ({n} → {n - 1})", new))

    attractions = {a.id: a for p in research.places for a in p.attractions}
    owner = {a.id: p.name for p in research.places for a in p.attractions}
    for _day_place, aid in _scheduled_attractions(research, adj):
        a = attractions.get(aid)
        place_name = owner.get(aid, _day_place)
        if a and a.price and a.price.max > 0:
            new = adj.model_copy(deep=True)
            new.skipped_attractions.append(aid)
            out.append((f"skip:{aid}", f"{place_name}: skip {a.name}", new))
    return out


def savings_options(
    research: TripResearch, req: TripRequest, tier: Tier, rate: float, adj: Adjustments
) -> list[SavingOption]:
    current = compute_breakdown(research, req, tier, rate, adj)
    opts = []
    for oid, label, new_adj in _candidate_adjustments(research, req, tier, adj, current):
        alt = compute_breakdown(research, req, tier, rate, new_adj)
        saves = Money(
            min=round(current.total.min - alt.total.min, 2),
            max=round(current.total.max - alt.total.max, 2),
        )
        if saves.mid > 0:
            opts.append(SavingOption(id=oid, label=label, saves=saves))
    return sorted(opts, key=lambda o: o.saves.mid, reverse=True)


def apply_saving(
    research: TripResearch, req: TripRequest, tier: Tier, rate: float, adj: Adjustments, option_id: str
) -> Adjustments:
    current = compute_breakdown(research, req, tier, rate, adj)
    for oid, _label, new_adj in _candidate_adjustments(research, req, tier, adj, current):
        if oid == option_id:
            return new_adj
    raise KeyError(option_id)


def build_report(
    research: TripResearch, req: TripRequest, rate: float, tier: Tier | None = None, adj: Adjustments | None = None
) -> BudgetReport:
    tier = tier or req.tier
    adj = adj or Adjustments()
    # The user's choices (connections, classes per stop, attractions) apply to every class shown.
    tiers = {t: compute_breakdown(research, req, t, rate, adj) for t in TIERS}
    selected = compute_breakdown(research, req, tier, rate, adj)
    over_by = round(selected.total.mid - req.budget, 2)
    over = over_by > 0
    return BudgetReport(
        tiers=tiers,
        selected_tier=tier,
        selected=selected,
        budget=req.budget,
        currency=req.currency,
        over_budget=over,
        over_by=max(over_by, 0),
        savings=savings_options(research, req, tier, rate, adj) if over else [],
        adjustments=adj,
    )
