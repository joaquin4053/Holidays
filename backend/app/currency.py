"""Exchange rates from frankfurter.dev (ECB data, free, no API key)."""
from __future__ import annotations

import time

import httpx

API = "https://api.frankfurter.dev/v1"
_TTL = 6 * 3600
_rates: dict[str, tuple[float, float]] = {}
_currencies: dict[str, str] = {}


async def usd_rate(currency: str) -> float:
    """How many units of `currency` one USD buys."""
    currency = currency.upper()
    if currency == "USD":
        return 1.0
    cached = _rates.get(currency)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{API}/latest", params={"base": "USD", "symbols": currency})
        r.raise_for_status()
        rate = float(r.json()["rates"][currency])
    _rates[currency] = (rate, time.time())
    return rate


async def currencies() -> dict[str, str]:
    global _currencies
    if not _currencies:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API}/currencies")
            r.raise_for_status()
            _currencies = r.json()
    return _currencies
