"""LLM calls (OpenAI-compatible APIs), JSON output validated with Pydantic.

Primary: OpenRouter with the free NVIDIA model, reasoning off. When OpenRouter reports a rate limit
the request goes to Groq (openai/gpt-oss-20b, free tier, reasoning_effort "low"). Groq allows only
8,000 tokens per minute, so every Groq request is budgeted before it is sent; one that can never fit
raises RequestTooLarge and the caller splits or shortens it instead. A call fails only when both
providers are limited - the error then says when each limit resets. No automatic SDK retries:
every retry would burn a request from the daily quota.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

import openai
from pydantic import BaseModel, ValidationError

from . import cache
from .config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    GROQ_REQUESTS_PER_MINUTE,
    GROQ_TOKENS_PER_MINUTE,
    LLM_CACHE,
    LLM_CONCURRENCY,
    OPENROUTER_API_KEY,
    OPENROUTER_BACKUP_MODELS,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    check_free_model,
)
from .models import Usage
from .timing import step

log = logging.getLogger("holydays.llm")
T = TypeVar("T", bound=BaseModel)

RATE_LIMIT_WAITS = (10, 30, 60)  # OpenRouter per-minute limit: pause before trying it again
MAX_WAIT = 65                    # wait for a per-minute limit to clear instead of failing, at most this long
OUTAGE_PAUSE = 600               # provider/model down (404, 5xx, no connection): skip it this long
EMPTY_PAUSE = 120                # answered without content (usually an upstream timeout): skip it this long
CHARS_PER_TOKEN = 3.0            # conservative (JSON, numbers, place names)
GROQ_MAX_OUTPUT = 3000           # max completion tokens per Groq request (a prompt + this must fit 8k)
GROQ_RESERVE_OUTPUT = 1200       # booked in the minute budget up front; real answers were 135-1,141 tokens
GROQ_BUDGET = int(GROQ_TOKENS_PER_MINUTE * 0.9)  # keep a margin for estimation errors
_sem = asyncio.Semaphore(LLM_CONCURRENCY)


class LLMError(RuntimeError):
    pass


class InvalidGeneration(Exception):
    """The provider checked the JSON itself and rejected it (Groq: HTTP 400 json_validate_failed)."""

    def __init__(self, text: str, error: str) -> None:
        super().__init__(error)
        self.text, self.error = text, error


class RequestTooLarge(LLMError):
    """Only Groq is available and the prompt can't fit its token budget: split or shorten it."""

    def __init__(self, max_chars: int) -> None:
        super().__init__(f"Request too large for Groq's {GROQ_TOKENS_PER_MINUTE} tokens/minute; "
                         f"user prompt must stay under {max_chars} characters")
        self.max_chars = max_chars


@dataclass
class Provider:
    name: str
    model: str
    base_url: str
    api_key: str
    blocked_until: float = 0.0  # epoch seconds
    reason: str = ""
    strikes: int = 0            # consecutive per-minute limits (longer pause each time)


# Order = preference: main free NVIDIA model, backup free NVIDIA models (same OpenRouter key), then Groq.
PROVIDERS = [
    Provider("openrouter", OPENROUTER_MODEL, OPENROUTER_BASE_URL, OPENROUTER_API_KEY),
    *(Provider("openrouter", m, OPENROUTER_BASE_URL, OPENROUTER_API_KEY) for m in OPENROUTER_BACKUP_MODELS),
    Provider("groq", GROQ_MODEL, GROQ_BASE_URL, GROQ_API_KEY),
]


def reset_limits() -> None:
    for p in PROVIDERS:
        p.blocked_until, p.reason, p.strikes = 0.0, "", 0


class MinuteBudget:
    """Sliding 60 s window: wait until a request fits the tokens/requests-per-minute limits.

    A request first reserves its worst case (prompt + max output); `settle` then books what it
    really used, so the next request doesn't wait for tokens that were never spent."""

    def __init__(self, tokens: int, requests: int) -> None:
        self.tokens, self.requests = tokens, requests
        self.events: deque[list] = deque()  # [time, tokens]

    async def acquire(self, tokens: int) -> list:
        while True:
            now = time.monotonic()
            while self.events and now - self.events[0][0] >= 60:
                self.events.popleft()
            used = sum(t for _, t in self.events)
            if used + tokens <= self.tokens and len(self.events) < self.requests:
                event = [now, tokens]
                self.events.append(event)
                return event
            wait = 60 - (now - self.events[0][0]) + 0.1
            log.info("Groq: waiting %.0f s for the per-minute token budget", wait)
            await asyncio.sleep(wait)

    @staticmethod
    def settle(event: list, actual_tokens: int) -> None:
        event[1] = actual_tokens


_groq_budget = MinuteBudget(GROQ_BUDGET, GROQ_REQUESTS_PER_MINUTE)


def client(provider: Provider) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=provider.api_key, base_url=provider.base_url, timeout=300, max_retries=0)


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema made strict: refs inlined, every property required, no extras."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def estimate_tokens(messages: list[dict], model: type[BaseModel]) -> int:
    chars = sum(len(m["content"]) for m in messages) + len(json.dumps(strict_schema(model)))
    return int(chars / CHARS_PER_TOKEN) + 20 * len(messages)


def _parse(text: str, model: type[T]) -> T:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    return model.model_validate(json.loads(text))


def _request(p: Provider, messages: list[dict], model: type[BaseModel], max_tokens: int = 16000) -> dict:
    response_format = {"type": "json_schema",
                       "json_schema": {"name": model.__name__, "strict": True, "schema": strict_schema(model)}}
    if p.name == "groq":
        return dict(model=GROQ_MODEL, messages=messages, response_format=response_format,
                    max_completion_tokens=min(max_tokens, GROQ_MAX_OUTPUT), reasoning_effort="low")
    return dict(model=p.model, messages=messages, response_format=response_format, max_tokens=max_tokens,
                extra_body={"provider": {"require_parameters": True}, "usage": {"include": True},
                            "reasoning": {"enabled": False}})


def _seconds(text: str) -> float:
    """'2m59.56s' / '7.66s' / '1h2m' -> seconds."""
    parts = dict((u, float(v)) for v, u in re.findall(r"([\d.]+)\s*([hms])", text))
    return parts.get("h", 0) * 3600 + parts.get("m", 0) * 60 + parts.get("s", 0)


def _limit_info(p: Provider, e: openai.RateLimitError) -> tuple[float, str]:
    """(seconds until the limit resets, what was hit) from a 429."""
    text = str(e)
    if p.name == "openrouter":
        if "per-day" in text:
            reset = re.search(r"X-RateLimit-Reset['\"]?\s*:\s*['\"]?(\d+)", text)
            secs = int(reset.group(1)) / 1000 - time.time() if reset else 6 * 3600
            return secs, "daily limit of free requests used up (50/day)"
        return RATE_LIMIT_WAITS[min(p.strikes, len(RATE_LIMIT_WAITS) - 1)], "per-minute limit (20 requests/min)"
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    wait = re.search(r"try again in ([\dhms.]+)", text)
    secs = _seconds(wait.group(1)) if wait else float(headers.get("retry-after") or 60)
    if re.search(r"per day|\(RPD\)|\(TPD\)", text, re.I):
        return secs, "daily limit (1,000 requests/day)"
    return secs, "per-minute limit (30 requests / 8,000 tokens per minute)"


def _short(e: Exception) -> str:
    """'HTTP 404: Function ...: Not found' from an OpenAI-SDK error, without the long JSON."""
    status = getattr(e, "status_code", None)
    detail = re.search(r'"detail":\s*"([^"]{0,120})', str(e)) or re.search(r"'message':\s*'([^']{0,120})", str(e))
    text = detail.group(1) if detail else type(e).__name__
    return f"HTTP {status}: {text}" if status else text


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%d.%m. %H:%M")


def _all_limited_message() -> str:
    parts = []
    for p in PROVIDERS:
        label = "Groq" if p.name == "groq" else f"OpenRouter ({p.model})"
        if not p.api_key:
            parts.append(f"{label}: not configured ({p.name.upper()}_API_KEY missing in .env)")
        else:
            parts.append(f"{label}: {p.reason}, resets {_when(p.blocked_until)}")
    return "No LLM provider is available right now. " + "; ".join(parts) + "."


def _record(p: Provider, resp, model: type[BaseModel], usage: Usage) -> None:
    u = resp.usage
    cost = float(getattr(u, "cost", 0) or 0) if u else 0.0
    usage.llm_calls += 1
    usage.llm_by_provider[p.name] = usage.llm_by_provider.get(p.name, 0) + 1
    if u:
        usage.input_tokens += u.prompt_tokens or 0
        usage.output_tokens += u.completion_tokens or 0
    usage.llm_cost_usd = round(usage.llm_cost_usd + cost, 6)
    log.info("LLM %s (%s) answered %s: %s in / %s out tokens", p.name, p.model, model.__name__,
             u.prompt_tokens if u else "?", u.completion_tokens if u else "?")
    if cost > 0:
        log.warning("%s charged %.6f USD for a call to %s", p.name, cost, p.model)


async def _create(messages: list[dict], model: type[BaseModel], usage: Usage, max_tokens: int = 16000):
    check_free_model(OPENROUTER_MODEL)  # never call a paid model, even if config changed at runtime
    estimate = estimate_tokens(messages, model)
    while True:
        now = time.time()
        too_large = False
        for p in PROVIDERS:
            if not p.api_key or p.blocked_until > now:
                continue
            if p.name == "openrouter":
                check_free_model(p.model)  # never call a paid model, even if config changed at runtime
            reservation = None
            waited = time.perf_counter()
            if p.name == "groq":
                if estimate + GROQ_MAX_OUTPUT > GROQ_BUDGET:
                    too_large = True
                    continue
                reservation = await _groq_budget.acquire(estimate + GROQ_RESERVE_OUTPUT)
            try:
                async with _sem:
                    with step("llm", model.__name__, provider=p.name, estimate=estimate,
                              budget_wait=round(time.perf_counter() - waited, 3)) as t:
                        resp = await client(p).chat.completions.create(**_request(p, messages, model, max_tokens))
                        if resp.usage:
                            t.update(prompt_tokens=resp.usage.prompt_tokens,
                                     completion_tokens=resp.usage.completion_tokens)
            except openai.RateLimitError as e:
                secs, p.reason = _limit_info(p, e)
                p.blocked_until, p.strikes = time.time() + max(secs, 1), p.strikes + 1
                log.warning("%s: %s, resets %s - switching provider", p.name, p.reason, _when(p.blocked_until))
                break  # start over: the next provider in line takes the request
            except openai.AuthenticationError as e:
                raise LLMError(f"{p.name} rejected the API key ({p.name.upper()}_API_KEY in .env)") from e
            except (openai.NotFoundError, openai.InternalServerError, openai.APIConnectionError) as e:
                # The model's host is down or gone (OpenRouter 404 "Provider returned error" from NVIDIA,
                # 2026-10-23): like a rate limit, the next provider takes the request.
                if reservation:
                    MinuteBudget.settle(reservation, 0)
                p.blocked_until, p.reason = time.time() + OUTAGE_PAUSE, f"model unavailable ({_short(e)})"
                log.warning("%s: %s - switching provider for %d min", p.name, p.reason, OUTAGE_PAUSE // 60)
                break
            except openai.BadRequestError as e:
                if e.code != "json_validate_failed":
                    raise
                if reservation:
                    MinuteBudget.settle(reservation, estimate)
                body = e.body if isinstance(e.body, dict) else {}
                log.warning("%s rejected its own JSON (schema check): %s", p.name, str(body.get("message", e))[:200])
                raise InvalidGeneration(str(body.get("failed_generation") or ""),
                                        str(body.get("message") or e)) from e
            if not resp.choices:
                # HTTP 200 without an answer: OpenRouter's way of passing on an upstream failure, often a timeout
                # after ~2 min on a very long request (Basel -> 4 Japanese stops, 56k tokens, 2026-10-23).
                extra = getattr(resp, "model_extra", None) or {}
                detail = str(extra.get("error") or extra or "no details")[:300]
                if reservation:
                    MinuteBudget.settle(reservation, 0)
                p.blocked_until, p.reason = time.time() + EMPTY_PAUSE, f"empty answer ({detail})"
                log.warning("%s (%s): %s - switching provider", p.name, p.model, p.reason)
                break
            p.strikes = 0
            if reservation and resp.usage:
                MinuteBudget.settle(reservation, (resp.usage.prompt_tokens or 0) + (resp.usage.completion_tokens or 0))
            _record(p, resp, model, usage)
            return resp.choices[0]
        else:  # nobody could take it
            blocked = [p.blocked_until for p in PROVIDERS if p.api_key and p.blocked_until > now]
            soonest = min(blocked) - now if blocked else None
            if soonest is not None and soonest <= MAX_WAIT:
                log.warning("LLM rate limit: waiting %.0f s", soonest)
                await asyncio.sleep(max(soonest, 0))
                continue
            if too_large:
                budget_chars = (GROQ_BUDGET - GROQ_MAX_OUTPUT - estimate_tokens(messages[:1], model)) * CHARS_PER_TOKEN
                raise RequestTooLarge(max(int(budget_chars * 0.9), 1000))
            raise LLMError(_all_limited_message())


async def complete_json(system: str, user: str, model: type[T], usage: Usage, repairs: int = 1,
                        max_tokens: int = 16000) -> T:
    """Ask for JSON matching `model`; on invalid output, send the error back and retry.

    Answers are cached by (system, prompt, schema): the same input never costs a second request."""
    key = cache.make_key("llm", system, user, model.__name__, strict_schema(model))
    if LLM_CACHE and (hit := cache.get(key)) is not None:
        with step("llm", model.__name__, provider="cache"):
            try:
                return model.model_validate_json(hit)
            except ValidationError:
                pass  # schema changed since: ask again
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(repairs + 1):
        try:
            choice = await _create(messages, model, usage, max_tokens)
            text, truncated = choice.message.content or "", choice.finish_reason == "length"
            try:
                result = _parse(text, model)
                cache.put(key, result.model_dump_json())
                return result
            except (json.JSONDecodeError, ValidationError) as e:
                error: Exception = e
        except InvalidGeneration as e:  # rejected by the provider's own schema check
            text, truncated, error = e.text, False, e
        if attempt == repairs:
            raise LLMError(f"{model.__name__}: invalid JSON from model after {repairs + 1} tries: {error}") from error
        if truncated:
            # Cut off at max_tokens: sending the half answer back doesn't help - ask again with more room.
            log.warning("%s cut off at %d tokens - asking again with %d", model.__name__, max_tokens,
                        min(max_tokens * 2, 16000))
            max_tokens = min(max_tokens * 2, 16000)
            continue
        hint = ""
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"That JSON is invalid{hint}: {str(error)[:1500]}\n"
                                        "Every field must have a value of the right type - never null. Leave out "
                                        "list entries you can't fill. Reply with the corrected JSON only."},
        ]
    raise AssertionError("unreachable")
