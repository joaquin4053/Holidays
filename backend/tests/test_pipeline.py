import json
import time
from datetime import datetime
from types import SimpleNamespace as NS

import openai
import pytest

from app import llm
from app.agent import synthesize as syn
from app.config import check_free_model
from app.models import LegDraft, PlaceDraft, TripRequest, Usage

# Daily-limit reset 6 h from now (a fixed date turns into the past and the limit looks expired).
RESET_MS = int((time.time() + 6 * 3600) * 1000)
RESET_DAY = datetime.fromtimestamp(RESET_MS / 1000).strftime("%d.%m.")


def req(**kw):
    base = dict(origin="Zurich", stops=[{"name": "Rome"}, {"name": "Florence"}, {"name": "Venice"}],
                days=10, budget=2000, currency="CHF")
    base.update(kw)
    return TripRequest(**base)


# --- free-model lock ---------------------------------------------------------

@pytest.mark.parametrize("model", ["nvidia/nemotron-3-super-120b-a12b", "openai/gpt-5:free", "anthropic/claude"])
def test_paid_or_foreign_models_are_rejected(model):
    with pytest.raises(RuntimeError):
        check_free_model(model)


def test_free_nvidia_model_is_allowed():
    check_free_model("nvidia/nemotron-3-super-120b-a12b:free")


# --- deterministic planning --------------------------------------------------

def test_distribute_nights_even_with_remainder():
    n = syn.distribute_nights(req())  # 10 days = 9 nights over 3 stops
    assert [x.nights for x in n] == [3, 3, 3]
    n = syn.distribute_nights(req(days=11))
    assert [x.nights for x in n] == [4, 3, 3]


def test_distribute_nights_respects_fixed():
    n = syn.distribute_nights(req(stops=[{"name": "Rome", "nights": 5}, {"name": "Florence"}, {"name": "Venice"}]))
    assert [x.nights for x in n] == [5, 2, 2]


def test_day_skeleton_covers_every_day():
    r = req()
    sk = syn.day_skeleton(r, syn.distribute_nights(r))
    assert [d for d, _, _ in sk] == list(range(1, 11))
    assert sk[0] == (1, "Rome", "arrival")
    assert sk[3] == (4, "Florence", "arrival")
    assert sk[-1] == (10, "Venice", "departure")


def test_legs_include_return_and_skip_same_place():
    assert syn.legs_for(req()) == [("Zurich", "Rome"), ("Rome", "Florence"), ("Florence", "Venice"), ("Venice", "Zurich")]
    assert syn.legs_for(req(return_to_origin=False))[-1] == ("Florence", "Venice")


def test_finalize_place_gives_unique_stable_ids():
    tp = {"min": 1, "max": 2}
    tiers = {"budget": tp, "comfort": tp, "luxury": tp}
    draft = PlaceDraft(
        name="rome", country="Italy", known_for="x", summary="x", local_food=[],
        accommodation_per_night=tiers, meal_cost=tiers, local_transport_per_day=tiers,
        attractions=[
            {"name": "Colosseum", "kind": "sight", "description": "", "price": tp},
            {"name": "Colosseum", "kind": "activity", "description": "", "price": tp},
            {"name": "Trevi Fountain", "kind": "sight", "description": "", "price": {"min": 0, "max": 0}},
        ],
    )
    place = syn._finalize_place(draft, "Rome", ["u1", "u2"])
    assert place.name == "Rome" and place.sources == ["u1", "u2"]
    assert [a.id for a in place.attractions] == ["rome-colosseum", "rome-colosseum-2", "rome-trevi-fountain"]
    assert place.attractions[0].price.max == 2 and place.attractions[2].price is None  # 0-0 -> free


def test_slug_handles_accents():
    assert syn.slug("São Paulo") == "sao-paulo"


# --- LLM JSON handling -------------------------------------------------------

def test_strict_schema_inlines_refs_and_requires_all():
    schema = llm.strict_schema(LegDraft)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    option = schema["properties"]["options"]["items"]
    assert option["additionalProperties"] is False
    assert set(option["required"]) == {"mode", "description", "duration_hours", "price", "source"}


def test_parse_accepts_code_fences():
    out = llm._parse('```json\n{"options": []}\n```', LegDraft)
    assert out.options == []


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return NS(
            choices=[NS(message=NS(content=reply), finish_reason="stop")],
            usage=NS(prompt_tokens=100, completion_tokens=50, cost=0),
        )


@pytest.fixture
def fake_llm(monkeypatch):
    """Fake OpenAI client for every provider; OpenRouter configured, Groq not (unless a test sets a key)."""
    llm.reset_limits()
    monkeypatch.setattr(llm, "PROVIDERS", [llm.PROVIDERS[0], llm.PROVIDERS[-1]])  # main model + Groq
    monkeypatch.setattr(llm.PROVIDERS[0], "api_key", "test-openrouter")
    monkeypatch.setattr(llm.PROVIDERS[1], "api_key", "")

    def install(replies):
        comp = FakeCompletions(replies)
        monkeypatch.setattr(llm, "client", lambda provider: NS(chat=NS(completions=comp)))
        monkeypatch.setattr(llm, "RATE_LIMIT_WAITS", (0, 0, 0))
        return comp
    yield install
    llm.reset_limits()


VALID_LEG = json.dumps({"options": [{"mode": "train", "description": "x", "duration_hours": 3,
                                     "price": {"min": 20, "max": 40}}]})


async def test_complete_json_repairs_invalid_output(fake_llm):
    comp = fake_llm(['{"options": [{"mode": "spaceship"}]}', VALID_LEG])
    usage = Usage()
    out = await llm.complete_json("sys", "user", LegDraft, usage)
    assert out.options[0].mode == "train"
    assert usage.llm_calls == 2 and usage.input_tokens == 200 and usage.llm_cost_usd == 0
    assert "invalid" in comp.calls[1]["messages"][-1]["content"]
    assert comp.calls[0]["model"].endswith(":free")
    assert comp.calls[0]["response_format"]["type"] == "json_schema"


async def test_complete_json_gives_up_after_repairs(fake_llm):
    fake_llm(["not json", "still not json"])
    with pytest.raises(llm.LLMError):
        await llm.complete_json("sys", "user", LegDraft, Usage())


async def test_rate_limit_retries_then_succeeds(fake_llm):
    err = openai.RateLimitError("429", response=NS(request=None, status_code=429, headers={}), body=None)
    fake_llm([err, VALID_LEG])
    out = await llm.complete_json("sys", "user", LegDraft, Usage())
    assert out.options


async def test_daily_limit_fails_fast_with_reset_time(fake_llm):
    msg = ("Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day', "
           "'metadata': {'headers': {'X-RateLimit-Reset': '" + str(RESET_MS) + "'}}}}")
    err = openai.RateLimitError(msg, response=NS(request=None, status_code=429, headers={}), body=None)
    comp = fake_llm([err, VALID_LEG])
    with pytest.raises(llm.LLMError, match=f"daily limit.*resets {RESET_DAY}"):
        await llm.complete_json("sys", "user", LegDraft, Usage())
    assert len(comp.calls) == 1  # no pointless retries
