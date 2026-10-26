import pytest
from pydantic import ValidationError

from app import budget
from app.models import Adjustments, TripRequest


def test_classify_leg(research):
    assert budget.classify_leg(research.legs[1]) == (0, 2, 1)  # fastest, cheapest, best value


def test_default_leg_pick_per_tier(research, request_):
    chosen = {t: budget.compute_breakdown(research, request_, t, 1.0).leg_choices[1].chosen
              for t in ("budget", "comfort", "luxury")}
    assert chosen == {"budget": 2, "comfort": 1, "luxury": 0}


def test_comfort_breakdown_usd(research, request_):
    b = budget.compute_breakdown(research, request_, "comfort", 1.0)
    cat = b.by_category
    # 2 travelers: 600-900 + 80-90 + 600-900 per person
    assert (cat["transport"].min, cat["transport"].max) == (2560, 3780)
    # 1 room, 6 nights, 80-100
    assert (cat["accommodation"].min, cat["accommodation"].max) == (480, 600)
    # 6 days * 3 meals * 2 pax * 10-15
    assert (cat["food"].min, cat["food"].max) == (360, 540)
    # free Bund ignored, unknown id ignored: tower 25-30 + wall 40-60, x2
    assert (cat["activities"].min, cat["activities"].max) == (130, 180)
    assert b.total.min == sum(m.min for m in cat.values())


def test_currency_rate_applied(research, request_):
    usd = budget.compute_breakdown(research, request_, "comfort", 1.0).total
    chf = budget.compute_breakdown(research, request_, "comfort", 0.8).total
    assert chf.min == pytest.approx(usd.min * 0.8, abs=0.1)


def test_tiers_are_ordered(research, request_):
    totals = [budget.compute_breakdown(research, request_, t, 1.0).total.mid for t in ("budget", "comfort", "luxury")]
    assert totals == sorted(totals)


def test_over_budget_offers_savings_and_applying_one_lowers_total(research, request_):
    report = budget.build_report(research, request_, 1.0)
    assert report.over_budget and report.over_by > 0
    ids = [s.id for s in report.savings]
    assert "leg:1:2" in ids                      # bus instead of train
    assert "skip:bj-wall" in ids
    assert "night:Shanghai" in ids
    assert "accommodation_tier:Beijing:budget" in ids
    assert all(s.saves.mid > 0 for s in report.savings)
    assert [s.saves.mid for s in report.savings] == sorted((s.saves.mid for s in report.savings), reverse=True)

    adj = budget.apply_saving(research, request_, "comfort", 1.0, report.adjustments, "night:Shanghai")
    after = budget.build_report(research, request_, 1.0, "comfort", adj)
    assert after.selected.total.mid < report.selected.total.mid
    assert adj.nights == {"Shanghai": 2}


def test_under_budget_has_no_savings(research, request_):
    rich = request_.model_copy(update={"budget": 100_000})
    report = budget.build_report(research, rich, 1.0)
    assert not report.over_budget and report.savings == []


def test_unknown_saving_raises(research, request_):
    with pytest.raises(KeyError):
        budget.apply_saving(research, request_, "comfort", 1.0, Adjustments(), "nope")


def test_request_validation():
    with pytest.raises(ValidationError):
        TripRequest(origin="A", stops=[{"name": "B"}], budget=100)  # no duration
    with pytest.raises(ValidationError):
        TripRequest(origin="A", stops=[{"name": "B", "nights": 10}], days=5, budget=100)
    r = TripRequest(origin="A", stops=[{"name": "B"}], start_date="2026-11-01", end_date="2026-11-10",
                    budget=100, travelers=3, currency="chf")
    assert (r.total_days, r.total_nights, r.rooms, r.currency) == (10, 9, 2, "CHF")


def test_attraction_labelled_by_its_own_place(research, request_):
    # Schedule a Shanghai sight on a day whose place is Beijing (e.g. a travel day).
    research.days[1].attraction_ids.append("sh-tower")
    research.days[0].attraction_ids.remove("sh-tower")
    labels = [l.label for l in budget.compute_breakdown(research, request_, "comfort", 1.0).lines
              if l.category == "activities"]
    assert "Shanghai: Shanghai Tower" in labels


def test_placeholder_options_are_dropped_and_never_ranked():
    """Bug 2026-10-08: "no train exists" with 0 h / 0 CHF was picked as fastest and cheapest."""
    from app.models import Leg
    real = {"mode": "flight", "description": "x", "duration_hours": 3, "price": {"min": 80, "max": 120}}
    bus = {"mode": "bus", "description": "x", "duration_hours": 12, "price": {"min": 40, "max": 60}}
    none = {"mode": "train", "description": "No train exists", "duration_hours": 0, "price": {"min": 0, "max": 0}}
    leg = Leg(from_place="A", to_place="B", sources=[], options=[real, none, bus])
    assert [o.mode for o in leg.options] == ["flight", "bus"]
    fastest, cheapest, _ = budget.classify_leg(leg)
    assert leg.options[fastest].mode == "flight" and leg.options[cheapest].mode == "bus"
