"""One LLM request per trip: mapping of the TripDraft answer, fallback to per-item requests."""
from contextlib import asynccontextmanager
from datetime import date

from app import llm
from app.agent import synthesize as syn
from app.models import (
    DaysDraft,
    LegDraft,
    PlaceDraft,
    TransportOption,
    TransportOptionDraft,
    TripDraft,
    TripDraftWithPrices,
    TripRequest,
    Usage,
)

P = {"min": 1, "max": 2}
TIERS = {"budget": P, "comfort": P, "luxury": P}
FIXED = {"accommodation_per_night": TIERS, "meal_cost": TIERS, "local_transport_per_day": TIERS}


def req():
    return TripRequest(origin="Zurich", stops=[{"name": "Rome"}], start_date=date(2027, 3, 12),
                       end_date=date(2027, 3, 15), travelers=2, budget=1500)


def option(mode, lo, source=None):
    """What the LLM answers (with the number of its search result)."""
    return TransportOptionDraft(mode=mode, description=mode, duration_hours=2, price={"min": lo, "max": lo + 10},
                                source=source)


def live_option(mode, lo):
    """What Python computes from a live page."""
    return TransportOption(mode=mode, description=mode, duration_hours=2, price={"min": lo, "max": lo + 10},
                           source_url=f"https://live/{mode}")


def trip_answer():
    return TripDraft.model_validate({
        "places": [{"name": "Roma", "country": "Italy", "known_for": "x", "summary": "x", "local_food": [],
                    "attractions": [{"name": "Colosseum", "kind": "sight", "description": "x", "price": P},
                                    {"name": "Trevi Fountain", "kind": "sight", "description": "x",
                                     "price": {"min": 0, "max": 0}}]}],
        "legs": [{"from_place": "Zurich", "to_place": "Rome", "options": [option("flight", 999), option("train", 70)]},
                 {"from_place": "Rome", "to_place": "Zurich", "options": [option("train", 60)]}],
        "days": [{"day": 1, "title": "Arrival", "attractions": ["the Colosseum"], "notes": "Flight"},
                 {"day": 2, "title": "Old town", "attractions": ["Trevi fountain", "Colosseum", "Pantheon"], "notes": ""},
                 {"day": 4, "title": "Home", "attractions": [], "notes": "Train"}],
    })


def data():
    places = [syn.PlaceData("Rome", {}, [], date(2027, 3, 12), date(2027, 3, 15), FIXED)]
    legs = [syn.LegData("Zurich", "Rome", date(2027, 3, 12), {}, [], {"flight": live_option("flight", 90)}),
            syn.LegData("Rome", "Zurich", date(2027, 3, 15), {}, [], {})]
    return places, legs


async def test_one_request_builds_places_legs_and_days(monkeypatch):
    asked = []

    async def fake_complete(system, user, model, usage, max_tokens=16000):
        asked.append((model, user, max_tokens))
        return trip_answer()
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    places, legs = data()
    research = await syn._single_request(req(), places, legs, syn.distribute_nights(req()), Usage(), lambda m: None)

    [(model, prompt, max_tokens)] = asked
    assert model is TripDraft  # all prices known: no price fields asked
    assert "accommodation_per_night" not in prompt and "flight is already priced" in prompt
    assert 2000 <= max_tokens <= 16000
    rome = research.places[0]
    assert rome.name == "Rome" and rome.meal_cost.comfort.max == 2  # canonical name, prices from Python
    assert [a.id for a in rome.attractions] == ["rome-colosseum", "rome-trevi-fountain"]
    out = research.legs[0]
    assert sorted(o.mode for o in out.options) == ["flight", "train"]
    assert next(o for o in out.options if o.mode == "flight").price.min == 90  # live price wins over LLM 999
    days = {d.day: d for d in research.days}
    assert days[1].attraction_ids == ["rome-colosseum"]          # "the Colosseum" -> matched
    assert days[2].attraction_ids == ["rome-trevi-fountain"]     # no repeat, unknown "Pantheon" dropped
    assert days[3].title == "Explore Rome" and len(research.days) == 4  # missing day filled by Python


async def test_missing_live_prices_ask_for_them_in_the_same_request(monkeypatch):
    asked = []

    async def fake_complete(system, user, model, usage, max_tokens=16000):
        asked.append(model)
        a = trip_answer().model_dump()
        for p in a["places"]:
            p.update(FIXED)
        return TripDraftWithPrices.model_validate(a)
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    places, legs = data()
    places[0].fixed = {}
    research = await syn._single_request(req(), places, legs, syn.distribute_nights(req()), Usage(), lambda m: None)
    assert asked == [TripDraftWithPrices] and research.places[0].accommodation_per_night.budget.max == 2


async def test_groq_only_falls_back_to_several_requests(monkeypatch):
    asked = []

    async def fake_complete(system, user, model, usage, max_tokens=16000):
        asked.append(model.__name__)
        if model is TripDraft or model is TripDraftWithPrices:
            raise llm.RequestTooLarge(6000)
        if model is PlaceDraft:
            return PlaceDraft(name="Rome", country="Italy", known_for="x", summary="x", attractions=[],
                              local_food=[], **FIXED)
        if model is LegDraft:
            return LegDraft(options=[option("train", 70)])
        return DaysDraft(days=[])

    @asynccontextmanager
    async def no_browser():
        yield None

    async def no_search(queries, usage):
        return {}
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    monkeypatch.setattr(syn.live_prices, "session", no_browser)
    monkeypatch.setattr(syn.search, "search_many", no_search)
    research = await syn.plan_trip(req(), Usage(), lambda m: None)
    assert asked[0] == "TripDraftWithPrices" and sorted(asked[1:]) == ["DaysDraft", "LegDraft", "LegDraft", "PlaceDraft"]
    assert len(research.legs) == 2 and len(research.days) == 4


def test_output_budget_grows_with_the_trip():
    small = syn._expected_output_tokens(1, 2, 4, False)
    big = syn._expected_output_tokens(5, 6, 20, True)
    assert 2000 <= small < big <= 16000


async def test_parallel_mode_skips_the_single_request(monkeypatch):
    asked = []

    async def fake_complete(system, user, model, usage, max_tokens=16000):
        asked.append(model.__name__)
        if model is LegDraft:
            return LegDraft(options=[option("train", 70)])
        if model is PlaceDraft:
            return PlaceDraft(name="Rome", country="Italy", known_for="x", summary="x", attractions=[],
                              local_food=[], **FIXED)
        return DaysDraft(days=[])
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    monkeypatch.setattr(syn, "LLM_SINGLE_REQUEST", False)
    places, legs = data()
    places[0].fixed = {}
    await syn._write_plan(req(), places, legs, syn.distribute_nights(req()), Usage(), lambda m: None)
    assert "TripDraft" not in asked and sorted(asked) == ["DaysDraft", "LegDraft", "LegDraft", "PlaceDraft"]


def test_sources_become_links():
    """LLM option source [n] -> URL of that search result; live prices link to the live search page."""
    from app.search import SearchResult
    results = {"transport": [SearchResult(title="a", url="https://seat61.com/rome", content="x"),
                             SearchResult(title="b", url="https://trainline.com/zr", content="x")]}
    d = syn.LegData("Zurich", "Rome", None, results, [], {"flight": live_option("flight", 90)})
    leg = syn._finish_leg(d, [option("train", 70, source=2), option("car", 150, source=9), option("bus", 40)])
    links = {o.mode: o.source_url for o in leg.options}
    assert links == {"train": "https://trainline.com/zr", "car": None, "bus": None, "flight": "https://live/flight"}


def test_options_without_source_link_to_the_live_page_of_their_mode():
    from app.live_prices import LiveResult
    live = [LiveResult(site="trainline", category="transport", url="https://thetrainline.com/zurich-to-rome",
                       status="ok", items=[{"x": 1}])]
    d = syn.LegData("Zurich", "Rome", None, {}, live, {})
    leg = syn._finish_leg(d, [option("train", 70), option("ferry", 50)])
    assert {o.mode: o.source_url for o in leg.options} == {"train": "https://thetrainline.com/zurich-to-rome",
                                                          "ferry": None}


def test_place_price_sources_prefer_live_pages():
    from app.live_prices import LiveResult
    from app.search import SearchResult
    live = [LiveResult(site="tripcom", category="accommodation", url="https://trip.com/list", status="ok",
                       items=[{"x": 1}]),
            LiveResult(site="numbeo", category="daily_costs", url="https://numbeo.com/rome", status="ok",
                       items=[{"x": 1}])]
    results = {"accommodation": [SearchResult(title="h", url="https://hotels.example", content="x")],
               "daily_costs": [SearchResult(title="c", url="https://costs.example", content="x")]}
    d = syn.PlaceData("Rome", results, live, None, None, {"accommodation_per_night": TIERS})
    assert d.price_sources == {"accommodation_per_night": "https://trip.com/list",
                               "meal_cost": "https://costs.example",          # computed by the LLM -> Tavily page
                               "local_transport_per_day": "https://costs.example"}


def test_old_trips_without_links_still_load():
    from app.models import Leg, Place
    leg = Leg.model_validate({"from_place": "A", "to_place": "B", "sources": [], "options": [
        {"mode": "bus", "description": "x", "duration_hours": 5, "price": {"min": 10, "max": 20}}]})
    assert leg.options[0].source_url is None
    place = Place.model_validate({"name": "Rome", "country": "IT", "known_for": "x", "summary": "x",
                                  "attractions": [], "local_food": [], "sources": [], **FIXED})
    assert place.price_sources == {}


async def test_very_large_trip_uses_the_parallel_path(monkeypatch):
    asked = []

    async def fake_complete(system, user, model, usage, max_tokens=16000):
        asked.append(model.__name__)
        if model is LegDraft:
            return LegDraft(options=[option("train", 70)])
        if model is PlaceDraft:
            return PlaceDraft(name="Rome", country="Italy", known_for="x", summary="x", attractions=[], local_food=[], **FIXED)
        return DaysDraft(days=[])
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    monkeypatch.setattr(syn, "SINGLE_REQUEST_MAX_TOKENS", 10)  # any real prompt is bigger
    places, legs = data()
    places[0].fixed = {}
    research = await syn._write_plan(req(), places, legs, syn.distribute_nights(req()), Usage(), lambda m: None)
    assert "TripDraftWithPrices" not in asked and sorted(asked) == ["DaysDraft", "LegDraft", "LegDraft", "PlaceDraft"]
    assert len(research.days) == 4
