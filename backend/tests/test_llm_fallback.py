"""LLM fallback OpenRouter -> Groq, both-limited error, Groq token budget, request splitting."""
import json
import re
import time
from datetime import datetime
from types import SimpleNamespace as NS

import openai
import pytest

from app import llm
from app.agent import synthesize as syn
from app.config import GROQ_MODEL
from app.models import (
    DayDraft,
    DaysDraft,
    LegDraft,
    Place,
    PlaceDraft,
    PlaceInfoDraft,
    PlacePricesDraft,
    TripRequest,
    Usage,
)

# Daily-limit reset 6 h from now (a fixed date turns into the past and the limit looks expired).
RESET_MS = int((time.time() + 6 * 3600) * 1000)
RESET_DAY = datetime.fromtimestamp(RESET_MS / 1000).strftime("%d.%m.")
VALID_LEG = json.dumps({"options": [{"mode": "train", "description": "x", "duration_hours": 3,
                                     "price": {"min": 20, "max": 40}}]})
OR_DAILY = ("Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day', "
            "'metadata': {'headers': {'X-RateLimit-Reset': '" + str(RESET_MS) + "'}}}}")
OR_MINUTE = "Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-min'}}"
GROQ_DAILY = ("Error code: 429 - Rate limit reached for model `openai/gpt-oss-20b` on requests per day (RPD): "
              "Limit 1000, Used 1000, Requested 1. Please try again in 2h3m4.5s.")


def rate_limit(msg):
    return openai.RateLimitError(msg, response=NS(request=None, status_code=429, headers={}), body=None)


class Fake:
    """One fake chat endpoint per provider; replies are popped in order."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return NS(choices=[NS(message=NS(content=reply), finish_reason="stop")],
                  usage=NS(prompt_tokens=100, completion_tokens=50, cost=0))


@pytest.fixture
def providers(monkeypatch):
    """providers(openrouter_replies, groq_replies, groq_key=True) -> (openrouter fake, groq fake)."""
    llm.reset_limits()

    def install(or_replies, groq_replies, groq_key=True):
        fakes = {"openrouter": Fake(or_replies), "groq": Fake(groq_replies)}
        monkeypatch.setattr(llm, "PROVIDERS", [llm.PROVIDERS[0], llm.PROVIDERS[-1]])  # main model + Groq
        monkeypatch.setattr(llm.PROVIDERS[0], "api_key", "test-openrouter")
        monkeypatch.setattr(llm.PROVIDERS[1], "api_key", "test-groq" if groq_key else "")
        monkeypatch.setattr(llm, "client", lambda p: NS(chat=NS(completions=fakes[p.name])))
        monkeypatch.setattr(llm, "RATE_LIMIT_WAITS", (0, 0, 0))
        monkeypatch.setattr(llm, "_groq_budget", llm.MinuteBudget(llm.GROQ_BUDGET, 30))  # fresh minute window
        return fakes["openrouter"], fakes["groq"]
    yield install
    llm.reset_limits()


async def test_openrouter_first_with_reasoning_off(providers):
    orr, groq = providers([VALID_LEG], [])
    usage = Usage()
    await llm.complete_json("sys", "user", LegDraft, usage)
    assert orr.calls[0]["extra_body"]["reasoning"] == {"enabled": False}
    assert usage.llm_by_provider == {"openrouter": 1} and not groq.calls


async def test_rate_limit_switches_to_groq(providers):
    orr, groq = providers([rate_limit(OR_MINUTE)], [VALID_LEG])
    usage = Usage()
    out = await llm.complete_json("sys", "user", LegDraft, usage)
    assert out.options[0].mode == "train"
    call = groq.calls[0]
    assert call["model"] == GROQ_MODEL == "openai/gpt-oss-20b"
    assert call["reasoning_effort"] == "low" and call["max_completion_tokens"] == llm.GROQ_MAX_OUTPUT
    assert "extra_body" not in call  # no OpenRouter-only parameters
    assert usage.llm_by_provider == {"groq": 1}


async def test_daily_limit_stays_on_groq_for_later_requests(providers):
    orr, groq = providers([rate_limit(OR_DAILY)], [VALID_LEG, VALID_LEG])
    usage = Usage()
    await llm.complete_json("sys", "leg 1", LegDraft, usage)
    await llm.complete_json("sys", "leg 2", LegDraft, usage)
    assert len(orr.calls) == 1 and len(groq.calls) == 2  # OpenRouter not asked again until it resets


async def test_same_input_is_answered_from_the_cache(providers):
    orr, groq = providers([VALID_LEG, VALID_LEG], [])
    usage = Usage()
    first = await llm.complete_json("sys", "Zurich -> Rome", LegDraft, usage)
    again = await llm.complete_json("sys", "Zurich -> Rome", LegDraft, usage)
    other = await llm.complete_json("sys", "Rome -> Zurich", LegDraft, usage)
    assert first == again == other and len(orr.calls) == 2 and usage.llm_calls == 2


async def test_cache_can_be_switched_off_for_measurements(providers, monkeypatch):
    orr, _ = providers([VALID_LEG, VALID_LEG], [])
    monkeypatch.setattr(llm, "LLM_CACHE", False)
    await llm.complete_json("sys", "same", LegDraft, Usage())
    await llm.complete_json("sys", "same", LegDraft, Usage())
    assert len(orr.calls) == 2


async def test_max_tokens_reaches_the_provider(providers):
    orr, _ = providers([VALID_LEG], [])
    await llm.complete_json("sys", "short", LegDraft, Usage(), max_tokens=2500)
    assert orr.calls[0]["max_tokens"] == 2500


async def test_both_limited_fails_with_both_reset_times(providers):
    orr, groq = providers([rate_limit(OR_DAILY)], [rate_limit(GROQ_DAILY)])
    with pytest.raises(llm.LLMError) as e:
        await llm.complete_json("sys", "user", LegDraft, Usage())
    msg = str(e.value)
    assert "No LLM provider is available" in msg and "OpenRouter (nvidia/" in msg and "daily limit of free requests" in msg and f"resets {RESET_DAY}" in msg
    assert "Groq: daily limit (1,000 requests/day), resets" in msg
    assert len(orr.calls) == 1 and len(groq.calls) == 1


async def test_without_groq_key_the_message_says_so(providers):
    providers([rate_limit(OR_DAILY)], [], groq_key=False)
    with pytest.raises(llm.LLMError, match="Groq: not configured"):
        await llm.complete_json("sys", "user", LegDraft, Usage())


async def test_too_large_for_groq_is_not_sent(providers):
    orr, groq = providers([rate_limit(OR_DAILY)], [VALID_LEG])
    with pytest.raises(llm.RequestTooLarge) as e:
        await llm.complete_json("sys", "x" * 40_000, LegDraft, Usage())
    assert not groq.calls and 1000 <= e.value.max_chars < 20_000


def test_groq_limit_parsing():
    assert llm._seconds("2m59.56s") == pytest.approx(179.56)
    assert llm._seconds("1h2m") == 3720
    secs, reason = llm._limit_info(llm.Provider("groq", "", "", ""), rate_limit(GROQ_DAILY))
    assert reason.startswith("daily") and secs == pytest.approx(2 * 3600 + 3 * 60 + 4.5)
    tpm = rate_limit("Rate limit reached on tokens per minute (TPM): Limit 8000. Please try again in 7.66s.")
    secs, reason = llm._limit_info(llm.Provider("groq", "", "", ""), tpm)
    assert reason.startswith("per-minute") and secs == pytest.approx(7.66)


async def test_minute_budget_waits_until_tokens_free(monkeypatch):
    clock = {"t": 1000.0}
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        clock["t"] += s
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    budget = llm.MinuteBudget(tokens=7000, requests=30)
    await budget.acquire(5000)
    await budget.acquire(5000)  # doesn't fit: waits ~60 s for the first one to leave the window
    assert slept and 59 < slept[0] <= 61


# --- splitting in the planner ---------------------------------------------------

def req():
    return TripRequest(origin="Zurich", stops=[{"name": "Rome"}, {"name": "Florence"}], days=5, budget=2000)


P = {"min": 1, "max": 2}
TIERS = {"budget": P, "comfort": P, "luxury": P}
INFO = PlaceInfoDraft(name="Rome", country="Italy", known_for="x", summary="x", attractions=[], local_food=[])
PRICES = PlacePricesDraft(accommodation_per_night=TIERS, meal_cost=TIERS, local_transport_per_day=TIERS)


async def test_place_split_into_info_and_prices(monkeypatch):
    asked = []

    async def fake_complete(system, user, model, usage):
        asked.append((model, user))
        if model is PlaceDraft:
            raise llm.RequestTooLarge(6000)
        return INFO if model is PlaceInfoDraft else PRICES

    async def no_search(queries, usage):
        return {}
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    monkeypatch.setattr(syn.search, "search_many", no_search)
    place = await syn._place(req(), "Rome", Usage(), lambda m: None)
    assert [m for m, _ in asked] == [PlaceDraft, PlaceInfoDraft, PlacePricesDraft]
    assert "attractions" in asked[1][1] and "accommodation_per_night" not in asked[1][1]
    assert "accommodation_per_night" in asked[2][1] and "local_food" not in asked[2][1]
    assert place.name == "Rome" and place.meal_cost.comfort.max == 2


async def test_days_split_into_halves_without_repeating_attractions(monkeypatch):
    r = req()
    rome = Place(name="Rome", country="Italy", known_for="x", summary="x", local_food=[], sources=[],
                 accommodation_per_night=TIERS, meal_cost=TIERS, local_transport_per_day=TIERS,
                 attractions=[{"id": "rome-a", "name": "A", "kind": "sight", "description": "x", "price": None},
                              {"id": "rome-b", "name": "B", "kind": "sight", "description": "x", "price": None}])
    prompts = []

    async def fake_complete(system, user, model, usage):
        prompts.append(user)
        days = [int(n) for n in re.findall(r"^Day (\d+):", user, re.M)]
        if len(days) > 2:
            raise llm.RequestTooLarge(3000)
        return DaysDraft(days=[DayDraft(day=d, title=f"D{d}", attraction_ids=["rome-a", "rome-b"]) for d in days])
    monkeypatch.setattr(syn, "complete_json", fake_complete)
    skeleton = [(1, "Rome", "arrival"), (2, "Rome", "full"), (3, "Rome", "full"), (4, "Rome", "departure")]
    drafts = await syn._days_drafts(r, skeleton, [rome], set(), Usage())
    assert [d.day for d in drafts] == [1, 2, 3, 4]
    assert len(prompts) == 3  # 1 too large + 2 halves
    assert "rome-a" not in prompts[2]  # second half doesn't offer what the first half used


async def test_minute_budget_books_actual_usage(monkeypatch):
    clock = {"t": 1000.0}
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        clock["t"] += s
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    budget = llm.MinuteBudget(tokens=7000, requests=30)
    first = await budget.acquire(6000)        # worst case reserved ...
    llm.MinuteBudget.settle(first, 3500)      # ... but only 3,500 really used
    await budget.acquire(3000)                # fits now without waiting
    assert not slept


async def test_groq_schema_rejection_goes_into_the_repair_round(providers):
    failed = '{"options":[{"mode":"other","description":"none","duration_hours":null,"price":{"min":0,"max":0}}]}'
    rejected = openai.BadRequestError(
        "Error code: 400", response=NS(request=None, status_code=400, headers={}),
        body={"message": "jsonschema: '/options/0/duration_hours' expected number, but got null",
              "type": "invalid_request_error", "code": "json_validate_failed", "failed_generation": failed})
    orr, groq = providers([rate_limit(OR_DAILY)], [rejected, VALID_LEG])
    out = await llm.complete_json("sys", "user", LegDraft, Usage())
    assert out.options[0].mode == "train"
    repair = groq.calls[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": failed}
    assert "expected number, but got null" in repair[-1]["content"] and "never null" in repair[-1]["content"]


async def test_other_bad_requests_still_fail(providers):
    bad = openai.BadRequestError("Error code: 400", response=NS(request=None, status_code=400, headers={}),
                                 body={"message": "model not found", "code": "model_not_found"})
    providers([bad], [])
    with pytest.raises(openai.BadRequestError):
        await llm.complete_json("sys", "user", LegDraft, Usage())


async def test_cut_off_answer_is_asked_again_with_more_room(providers, monkeypatch):
    orr, _ = providers(["", VALID_LEG], [])

    async def cut_then_ok(**kwargs):
        orr.calls.append(kwargs)
        if len(orr.calls) == 1:
            return NS(choices=[NS(message=NS(content='{"options": [{"mode": "tr'), finish_reason="length")],
                      usage=NS(prompt_tokens=100, completion_tokens=4000, cost=0))
        return NS(choices=[NS(message=NS(content=VALID_LEG), finish_reason="stop")],
                  usage=NS(prompt_tokens=100, completion_tokens=50, cost=0))
    monkeypatch.setattr(orr, "create", cut_then_ok)
    out = await llm.complete_json("sys", "big trip", LegDraft, Usage(), max_tokens=4000)
    assert out.options[0].mode == "train"
    assert [c["max_tokens"] for c in orr.calls] == [4000, 8000]
    assert len(orr.calls[1]["messages"]) == 2  # same request, not the half answer sent back


async def test_model_outage_switches_to_groq_and_pauses_openrouter(providers):
    body = {"message": "Provider returned error", "code": 404, "metadata": {"raw": '{"status":404,"title":"Not Found",'
            '"detail":"Function \'ac74\': Not found for account \'x\'"}', "provider_name": "Nvidia"}}
    gone = openai.NotFoundError("Error code: 404 - " + str({"error": body}),
                                response=NS(request=None, status_code=404, headers={}), body=body)
    orr, groq = providers([gone], [VALID_LEG, VALID_LEG])
    usage = Usage()
    await llm.complete_json("sys", "first", LegDraft, usage)
    await llm.complete_json("sys", "second", LegDraft, usage)
    assert len(orr.calls) == 1 and usage.llm_by_provider == {"groq": 2}
    assert "model unavailable (HTTP 404" in llm.PROVIDERS[0].reason


async def test_backup_nvidia_model_is_tried_before_groq(monkeypatch):
    """Main model down -> the backup free NVIDIA model (same OpenRouter key) answers, Groq is not asked."""
    llm.reset_limits()
    main, backup, groq = (llm.Provider("openrouter", "nvidia/main:free", "", "k"),
                          llm.Provider("openrouter", "nvidia/backup:free", "", "k"), llm.Provider("groq", llm.GROQ_MODEL, "", "g"))
    gone = openai.NotFoundError("Error code: 404", response=NS(request=None, status_code=404, headers={}), body={})
    fakes = {"nvidia/main:free": Fake([gone]), "nvidia/backup:free": Fake([VALID_LEG]), llm.GROQ_MODEL: Fake([])}
    monkeypatch.setattr(llm, "PROVIDERS", [main, backup, groq])
    monkeypatch.setattr(llm, "client", lambda p: NS(chat=NS(completions=fakes[p.model])))
    usage = Usage()
    await llm.complete_json("sys", "x", LegDraft, usage)
    assert fakes["nvidia/backup:free"].calls[0]["model"] == "nvidia/backup:free" and not fakes[llm.GROQ_MODEL].calls
    assert usage.llm_by_provider == {"openrouter": 1}
    llm.reset_limits()


async def test_empty_answer_goes_to_the_next_provider(providers):
    """HTTP 200 without choices (upstream timeout) must not end the trip."""
    class Empty(Fake):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return NS(choices=[], usage=None, model_extra={"error": {"message": "Upstream timed out", "code": 504}})
    orr, groq = providers([], [VALID_LEG])
    empty = Empty([])
    llm.client = lambda p: NS(chat=NS(completions=empty if p.name == "openrouter" else groq))
    usage = Usage()
    out = await llm.complete_json("sys", "big", LegDraft, usage)
    assert out.options and len(empty.calls) == 1 and usage.llm_by_provider == {"groq": 1}
    assert "Upstream timed out" in llm.PROVIDERS[0].reason
