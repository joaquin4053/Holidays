"""Data models shared by the API, the research agent and the budget engine.

All prices coming from research are in USD; budget.py converts to the user's currency.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Tier = Literal["budget", "comfort", "luxury"]
TIERS: tuple[Tier, ...] = ("budget", "comfort", "luxury")

TripType = Literal[
    "backpacking", "city", "beach", "adventure", "culture", "roadtrip", "family"
]
TransportMode = Literal["flight", "train", "bus", "ferry", "car", "other"]


# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------

class Stop(BaseModel):
    name: str = Field(min_length=1)
    nights: int | None = Field(default=None, ge=1, description="Optional fixed number of nights")


class TripRequest(BaseModel):
    origin: str = Field(min_length=1)
    stops: list[Stop] = Field(min_length=1)
    return_to_origin: bool = True
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=90)
    travelers: int = Field(default=1, ge=1, le=20)
    budget: float = Field(gt=0, description="Total budget for the whole group")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    trip_type: TripType = "city"
    interests: list[str] = []
    tier: Tier = "comfort"

    @model_validator(mode="after")
    def _check_duration(self) -> "TripRequest":
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValueError("end_date must be after start_date")
        elif not self.days:
            raise ValueError("Provide start_date and end_date, or days")
        self.currency = self.currency.upper()
        fixed = sum(s.nights or 0 for s in self.stops)
        if fixed > self.total_nights:
            raise ValueError("Fixed nights per stop exceed the trip length")
        return self

    @property
    def total_days(self) -> int:
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return self.days or 1

    @property
    def total_nights(self) -> int:
        return max(self.total_days - 1, 1)

    @property
    def rooms(self) -> int:
        return (self.travelers + 1) // 2


# ---------------------------------------------------------------------------
# Research result (produced by the synthesis call via structured output)
# ---------------------------------------------------------------------------

class PriceRange(BaseModel):
    min: float = Field(description="Lower estimate in USD")
    max: float = Field(description="Upper estimate in USD")


class TierPrices(BaseModel):
    budget: PriceRange
    comfort: PriceRange
    luxury: PriceRange


class Attraction(BaseModel):
    id: str = Field(description="Unique slug, e.g. 'shanghai-the-bund'")
    name: str
    kind: Literal["sight", "activity"]
    description: str
    price: PriceRange | None = Field(description="Entry/participation cost per person in USD, null if free")


class AttractionDraft(BaseModel):
    """LLM version: price is mandatory so models can't lazily mark everything as free."""
    name: str
    kind: Literal["sight", "activity"]
    description: str
    price: PriceRange = Field(description="Ticket/tour price per person in USD; 0-0 only if genuinely free")


class Dish(BaseModel):
    name: str
    description: str


class PlaceInfoDraft(BaseModel):
    """Language part of a place (used alone when a request must be split for Groq's token limit)."""
    name: str
    country: str
    known_for: str = Field(description="One or two sentences: what the place is important/famous for")
    summary: str
    attractions: list[AttractionDraft]
    local_food: list[Dish]


class PlacePricesDraft(BaseModel):
    """Price part of a place (used alone when a request must be split for Groq's token limit)."""
    accommodation_per_night: TierPrices = Field(description="Per double room per night in USD")
    meal_cost: TierPrices = Field(description="Per person per meal in USD")
    local_transport_per_day: TierPrices = Field(description="Per person per day in USD")


class PlaceDraft(PlaceInfoDraft):
    """What the LLM fills in for a place; sources are added from the search results."""
    accommodation_per_night: TierPrices = Field(description="Per double room per night in USD")
    meal_cost: TierPrices = Field(description="Per person per meal in USD")
    local_transport_per_day: TierPrices = Field(description="Per person per day in USD")


class Place(PlaceDraft):
    attractions: list[Attraction]
    sources: list[str] = Field(description="URLs used")
    price_sources: dict[str, str] = {}  # accommodation_per_night / meal_cost / local_transport_per_day -> URL


class _TransportBase(BaseModel):
    mode: TransportMode
    description: str = Field(description="e.g. 'High-speed train G-series, Shanghai Hongqiao -> Beijing South'")
    duration_hours: float = Field(description="Door-to-door estimate incl. typical transfers")
    price: PriceRange = Field(description="Per person, one way, USD")


class TransportOptionDraft(_TransportBase):
    """LLM version: names the search result it relies on; Python turns the number into a link."""
    source: int | None = Field(default=None, description="Number [n] of the search result the price/duration "
                                                         "come from, null if none")


class TransportOption(_TransportBase):
    source_url: str | None = None  # page the price comes from (live search page or search result)


class LegDraft(BaseModel):
    """What the LLM fills in for a leg."""
    options: list[TransportOptionDraft]


class Leg(LegDraft):
    from_place: str
    to_place: str
    options: list[TransportOption]
    sources: list[str]

    @field_validator("options")
    @classmethod
    def _only_real_options(cls, options: list[TransportOption]) -> list[TransportOption]:
        """Drop placeholders like "no train exists" with 0 h / 0 USD: the budget would rank them fastest and
        cheapest. Also cleans trips saved before this check."""
        return [o for o in options if o.duration_hours > 0 and o.price.max > 0]


class NightsAt(BaseModel):
    place: str
    nights: int


class Day(BaseModel):
    day: int
    place: str
    title: str
    attraction_ids: list[str] = Field(description="Ids of attractions scheduled this day")
    notes: str


class DayDraft(BaseModel):
    """Lenient on purpose: free models sometimes drop fields; Python fills the gaps."""
    day: int
    title: str = ""
    attraction_ids: list[str] = Field(default=[], description="Ids of attractions scheduled this day")
    notes: str = ""


class DaysDraft(BaseModel):
    days: list[DayDraft]


# One request for the whole trip (synthesize._single_request); Python adds ids, live prices, validation.

class LegOptionsDraft(BaseModel):
    from_place: str
    to_place: str
    options: list[TransportOptionDraft]


class DayPlanDraft(BaseModel):
    day: int
    title: str = ""
    attractions: list[str] = Field(default=[], description="Attraction names exactly as listed for that day's place")
    notes: str = ""


class TripDraft(BaseModel):
    places: list[PlaceInfoDraft]
    legs: list[LegOptionsDraft]
    days: list[DayPlanDraft]


class TripDraftWithPrices(TripDraft):
    """Used when live data didn't cover every place's price tiers."""
    places: list[PlaceDraft]


class TripResearch(BaseModel):
    places: list[Place]
    legs: list[Leg] = Field(description="In travel order, including outbound and return legs")
    nights: list[NightsAt]
    days: list[Day]


# ---------------------------------------------------------------------------
# API usage tracking
# ---------------------------------------------------------------------------

class Usage(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_cost_usd: float = 0.0
    llm_by_provider: dict[str, int] = {}  # answered requests per provider (openrouter / groq)
    timings: list[dict] = []  # app/timing.py: one entry per Tavily query, live page, LLM request ...
    search_credits: int = 0


# ---------------------------------------------------------------------------
# Budget engine output
# ---------------------------------------------------------------------------

class Money(BaseModel):
    min: float
    max: float

    @property
    def mid(self) -> float:
        return (self.min + self.max) / 2


class CostLine(BaseModel):
    category: Literal["transport", "accommodation", "food", "activities", "local_transport"]
    label: str
    unit: str = Field(description="Human readable unit price, e.g. '50-60 / night'")
    total: Money
    place: str | None = None  # stop the line belongs to (None = travel between stops)


class LegChoice(BaseModel):
    leg_index: int
    from_place: str
    to_place: str
    fastest: int
    cheapest: int
    best_value: int
    chosen: int


class Breakdown(BaseModel):
    tier: Tier
    currency: str
    lines: list[CostLine]
    by_category: dict[str, Money]
    total: Money
    leg_choices: list[LegChoice]
    by_place: dict[str, Money] = {}  # cost at each stop (stay, food, local transport, activities)


class SavingOption(BaseModel):
    id: str
    label: str
    saves: Money


class Adjustments(BaseModel):
    """User choices applied on top of a tier, without new research."""
    leg_choice: dict[int, int] = {}
    accommodation_tier: dict[str, Tier] = {}
    food_tier: dict[str, Tier] = {}
    skipped_attractions: list[str] = []
    added_attractions: list[str] = []  # attractions the user adds although they are not in the day plan
    nights: dict[str, int] = {}


class BudgetReport(BaseModel):
    tiers: dict[str, Breakdown]
    selected_tier: Tier
    selected: Breakdown
    budget: float
    currency: str
    over_budget: bool
    over_by: float
    savings: list[SavingOption]
    adjustments: Adjustments
