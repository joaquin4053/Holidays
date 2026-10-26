"""Read prices from live booking pages without an LLM.

Most sites: a Crawl4AI CSS schema (JsonCssExtractionStrategy) turns the result list into rows.
Trainline: regular expressions on the cleaned markdown (its facts are sentences, not a list).
`normalize` turns the raw strings into numbers, `summarize` builds the short text the LLM gets
instead of the page. Selectors were taken from the live pages on 2026-10-07; when a site changes
its layout the schema finds 0 rows and the page falls back to cleaned markdown.
"""
from __future__ import annotations

import re
import statistics
from urllib.parse import parse_qs, unquote_plus, urlparse

# site -> (Crawl4AI CSS schema, selector that exists once the results are there). Class names that look generated
# (css-1mq2vqm, Price__priceText___IOt9s) are avoided; data-* / aria / structure are more stable.
SCHEMAS: dict[str, tuple[dict, str]] = {
    "tripcom": ({
        "name": "Trip.com hotels", "baseSelector": "div.hotel-card",
        "fields": [
            {"name": "name", "selector": "a.hotelName", "type": "text"},
            {"name": "rating", "selector": "span.score", "type": "text"},
            {"name": "reviews", "selector": "span.comment-num", "type": "text"},
            {"name": "room", "selector": "div.room-name", "type": "text"},
            {"name": "price_night", "selector": "span.sale", "type": "text"},
            {"name": "price_total", "selector": "span.price-highlight", "type": "text"},
            {"name": "area", "selector": "span.position-desc", "type": "text"},
        ],
    }, "div.hotel-card"),
    "airbnb": ({
        "name": "Airbnb homes", "baseSelector": '[itemprop="itemListElement"]',
        "fields": [
            {"name": "kind", "selector": '[data-testid="listing-card-title"]', "type": "text"},
            {"name": "name", "selector": '[data-testid="listing-card-name"]', "type": "text"},
            {"name": "price_row", "selector": '[data-testid="price-availability-row"]', "type": "text"},
            {"name": "card", "selector": '[data-testid="card-container"]', "type": "text"},
        ],
    }, '[data-testid="price-availability-row"]'),
    # Google serves two layouts: one aria-label with everything ("From 105 US dollars. Nonstop flight with
    # easyJet. Leaves ...") or several small ones ("92 US dollars", "Nonstop flight.", "Leaves ...").
    # Taking every aria-label of the row covers both; the airline then comes from the row text.
    "google_flights": ({
        "name": "Google Flights", "baseSelector": 'li:has([aria-label*="Select flight"])',
        "fields": [
            {"name": "labels", "selector": "[aria-label]", "type": "list",
             "fields": [{"name": "l", "type": "attribute", "attribute": "aria-label"}]},
            {"name": "text", "selector": "div", "type": "text"},
        ],
    }, '[aria-label*="Select flight"]'),
    "flixbus": ({
        "name": "FlixBus rides", "baseSelector": 'li[data-e2e^="search-result-item"]',
        "fields": [
            {"name": "times", "selector": "time", "type": "list", "fields": [{"name": "t", "type": "text"}]},
            {"name": "duration", "selector": '[data-e2e="search-result-duration"]', "type": "text"},
            {"name": "price", "selector": '[data-e2e="search-result-prices"]', "type": "text"},
            {"name": "transfers", "selector": '[class*="transferTypeLabel"]', "type": "text"},
        ],
    }, '[data-e2e="search-result-prices"]'),
    "hostelworld": ({
        "name": "Hostelworld hostels", "baseSelector": "a.property-listing-card",
        "fields": [
            {"name": "name", "selector": ".property-name", "type": "text"},
            {"name": "rating", "selector": "span.score", "type": "text"},
            {"name": "reviews", "selector": "span.num-reviews", "type": "text"},
            {"name": "distance", "selector": ".property-distance", "type": "text"},
            {"name": "prices", "selector": ".property-accommodation-price", "type": "nested_list", "fields": [
                {"name": "label", "selector": ".accommodation-label", "type": "text"},
                {"name": "price", "selector": "strong", "type": "text"},
            ]},
        ],
    }, "a.property-listing-card"),
    "numbeo": ({
        "name": "Numbeo prices", "baseSelector": "table.data_wide_table tr",
        "fields": [
            {"name": "item", "selector": "td:first-child", "type": "text"},
            {"name": "price", "selector": "td.priceValue", "type": "text"},
            {"name": "range", "selector": "td.priceBarTd", "type": "text"},
        ],
    }, "td.priceValue"),
    "billiger_mietwagen": ({
        "name": "Car rental offers", "baseSelector": "article",
        "fields": [
            {"name": "category", "selector": "header", "type": "text"},
            {"name": "price", "selector": "data", "type": "text"},
        ],
    }, "article data"),
}
MARKDOWN_SITES = {"trainline"}  # parsed with regexes from the cleaned markdown
NUMBEO_KEEP = re.compile(r"meal|beer|cappuccino|water \(0\.33|one-way ticket|monthly pass|taxi", re.I)

MONEY = re.compile(r"(US\$|\$|CHF|Fr\.|€|£|¥)?\s?(\d[\d,'.]*)\s?(€|CHF)?")
SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY", "Fr.": "CHF", "CHF": "CHF"}


def money(text: str | None) -> float | None:
    """'$1,234' / '1.234,50 €' / 'Fr.24.44' / 'CHF 89' -> number; None when there is no amount."""
    m = MONEY.search(text or "")
    if not m:
        return None
    raw = m.group(2).rstrip(".,")
    if re.search(r",\d{2}$", raw):            # 1.234,50 (German)
        raw = raw.replace(".", "").replace("'", "").replace(",", ".")
    else:                                      # 1,234.50 / 1'234
        raw = raw.replace(",", "").replace("'", "")
    try:
        return float(raw)
    except ValueError:
        return None


def currency(text: str | None) -> str:
    m = MONEY.search(text or "")
    symbol = (m.group(1) or m.group(3) or "") if m else ""
    return SYMBOLS.get(symbol, "USD")


def hours(text: str | None) -> float | None:
    """'1 hr 35 min' / '14:40 hrs' / '6h 20m' / '45 min' -> hours."""
    t = text or ""
    if m := re.search(r"(\d+):(\d{2})\s*hrs?", t):
        return int(m.group(1)) + int(m.group(2)) / 60
    h = re.search(r"(\d+)\s*(?:hr|hrs|hour|hours|h)\b", t)
    mi = re.search(r"(\d+)\s*(?:min|mins|minutes|m)\b", t)
    if not (h or mi):
        return None
    return (int(h.group(1)) if h else 0) + (int(mi.group(1)) / 60 if mi else 0)


def url_params(url: str) -> dict:
    """Nights and travellers from the search URL (the page itself doesn't repeat them reliably)."""
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    out: dict = {}
    ci, co = q.get("checkin") or q.get("check_in"), q.get("checkout") or q.get("check_out")
    if ci and co:
        from datetime import date
        a, b = (date.fromisoformat(x.replace("/", "-")) for x in (ci, co))
        out["nights"] = (b - a).days
    if people := q.get("adults") or q.get("adult") or q.get("group_adults"):
        out["people"] = int(people)
    elif m := re.search(r"(\d+) adults", unquote_plus(q.get("q", ""))):
        out["people"] = int(m.group(1))
    return out


def _flight(label: str, people: int, text: str = "") -> dict | None:
    """One flight from the row's aria-labels (joined) and, for the airline, the row text."""
    label, text = re.sub(r"\s+", " ", label), re.sub(r"\s+", " ", text)  # Google uses no-break spaces
    price = re.search(r"(?:From )?([\d,]+) US dollars", label)
    if not price:
        return None
    total = float(price.group(1).replace(",", ""))
    stops = re.search(r"(Nonstop|\d+ stops?) flight", label)
    airline = (re.search(r"flight with (.+?)\.", label)
               or re.search(r"on \w{3}, \w{3} \d{1,2}(?:\+\d)?([^\d–]+?)\d+ hr", text))
    leave = re.search(r"Leaves (.+?) at (\d{1,2}:\d{2}\s?[AP]M)", label)
    arrive = re.search(r"arrives at (.+?) at (\d{1,2}:\d{2}\s?[AP]M)", label)
    duration = re.search(r"Total duration ([\d hrmin]+)", label)
    return {"airline": airline.group(1) if airline else "", "stops": stops.group(1) if stops else "",
            "from": leave.group(1) if leave else "", "departs": leave.group(2) if leave else "",
            "to": arrive.group(1) if arrive else "", "arrives": arrive.group(2) if arrive else "",
            "hours": hours(duration.group(1)) if duration else None,
            "price_total": total, "price_person": round(total / max(people, 1), 2), "currency": "USD"}


def normalize(site: str, rows: list[dict], url: str = "") -> list[dict]:
    """Clean CSS rows: numbers for prices, drop rows without a price (ads, placeholders), no duplicates."""
    p = url_params(url)
    out: list[dict] = []
    seen: set = set()
    for r in rows:
        try:
            item = _normalize_row(site, r, p)
        except (KeyError, ValueError, TypeError, AttributeError, IndexError):
            item = None  # one odd row (ad, placeholder) must not cost the whole page
        if item:
            key = tuple(sorted((k, str(v)) for k, v in item.items()))
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _normalize_row(site: str, r: dict, p: dict) -> dict | None:
    r = {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items() if v not in (None, "", [])}
    item: dict | None = None
    if site == "tripcom":
        night = money(r.get("price_night"))
        if night and r.get("name"):
            item = {"name": r["name"], "rating": r.get("rating"), "reviews": r.get("reviews"),
                    "room": r.get("room"), "area": r.get("area"), "price_night": night,
                    "price_total": money(r.get("price_total")), "currency": currency(r.get("price_night"))}
    elif site == "airbnb":
        total = re.search(r"([$€£]|CHF|Fr\.)\s?([\d,'.]+)\s*total", r.get("price_row", ""))
        if total and r.get("name"):
            amount = money(total.group(0))
            rating = re.search(r"(\d\.\d{1,2}) \((\d+)\)", r.get("card", ""))
            nights = p.get("nights") or 1
            item = {"name": r["name"], "kind": r.get("kind"), "rating": rating.group(1) if rating else None,
                    "reviews": rating.group(2) if rating else None, "price_total": amount,
                    "price_night": round(amount / nights, 2) if amount else None,
                    "currency": currency(total.group(0))}
    elif site == "google_flights":
        labels = " ".join(x.get("l", "") for x in r.get("labels", []) if isinstance(x, dict))
        item = _flight(labels or r.get("label", ""), p.get("people", 1), r.get("text", ""))
    elif site == "flixbus":
        price, times = money(r.get("price")), [t.get("t") for t in r.get("times", [])]
        if price:
            item = {"departs": times[0] if times else "", "arrives": times[1] if len(times) > 1 else "",
                    "hours": hours(r.get("duration")), "transfers": r.get("transfers") or "direct",
                    "price_person": price, "currency": currency(r.get("price"))}
    elif site == "hostelworld":
        prices = {x["label"].split()[0].lower(): x.get("price") for x in r.get("prices", []) if x.get("label")}
        privates, dorms = money(prices.get("privates")), money(prices.get("dorms"))
        if r.get("name") and (privates or dorms):
            item = {"name": r["name"], "rating": r.get("rating"), "reviews": (r.get("reviews") or "").strip("()"),
                    "distance": r.get("distance"), "privates_from": privates, "dorms_from": dorms,
                    "currency": currency(prices.get("privates") or prices.get("dorms"))}
    elif site == "numbeo":
        if r.get("item") and NUMBEO_KEEP.search(r["item"]) and money(r.get("price")):
            item = {"item": r["item"], "price": money(r["price"]), "range": r.get("range"),
                    "currency": currency(r["price"])}
    elif site == "billiger_mietwagen":
        if (price := money(r.get("price"))) and r.get("category"):
            item = {"category": r["category"], "price_day": price, "currency": currency(r.get("price"))}
    return item


def parse_markdown(site: str, md: str) -> list[dict]:
    """Facts written as sentences (Trainline route pages) -> one row."""
    if site != "trainline":
        return []
    text = re.sub(r"\s+", " ", md)
    avg = re.search(r"average of (\d+h \d+m)", text)
    fastest = re.search(r"Journey time \|\s*From (\d+h \d+m)", text) or re.search(r"fastest [^.]*?taking (?:just )?([\d hoursandminute]+?)(?:\.|,| -)", text)
    km = re.search(r"\((\d[\d,]*) km\)", text)
    per_day = re.search(r"(\d+) trains per day", text)
    price = re.search(r"start from ((?:CHF|£|€|\$|US\$)\s?[\d.,]+)", text) or re.search(r"Price \|\s*From ((?:CHF|£|€|\$)\s?[\d.,]+)", text)
    changes = re.search(r"Changes \|\s*([^|]+?)\s*\|", text)
    if not (price or avg):
        return []
    return [{"avg_hours": hours(avg.group(1)) if avg else None,
             "fastest_hours": hours(fastest.group(1)) if fastest else None,
             "distance_km": int(km.group(1).replace(",", "")) if km else None,
             "trains_per_day": int(per_day.group(1)) if per_day else None,
             "changes": changes.group(1) if changes else None,
             "price_from": money(price.group(1)) if price else None,
             "currency": currency(price.group(1)) if price else "USD"}]


def price_stats(values: list[float]) -> dict[str, float] | None:
    values = sorted(v for v in values if v)
    if not values:
        return None
    q = statistics.quantiles(values, n=4) if len(values) >= 2 else [values[0]] * 3
    return {"n": len(values), "min": values[0], "p25": q[0], "median": q[1], "p75": q[2], "max": values[-1]}


def _stats_line(label: str, values: list[float], cur: str) -> str:
    st = price_stats(values)
    if not st:
        return ""
    return (f"{label} ({cur}, n={st['n']}): min {st['min']:.0f}, p25 {st['p25']:.0f}, median {st['median']:.0f}, "
            f"p75 {st['p75']:.0f}, max {st['max']:.0f}")


def _h(x: float | None) -> str:
    return f"{x:.1f} h" if x else "?"


def summarize(site: str, items: list[dict], max_rows: int = 12) -> str:
    """Short text for the LLM: price statistics plus the first offers, one line each."""
    if not items:
        return ""
    cur = items[0].get("currency", "USD")
    rows: list[str] = []
    if site == "tripcom":
        head = _stats_line("Hotel price per room per night, taxes excl.", [i["price_night"] for i in items], cur)
        rows = [f"- {i['name']} | rating {i.get('rating') or '-'} | {i.get('room') or ''} | {i['price_night']:.0f}/night"
                f" | total {i['price_total'] or '-'} incl. taxes" for i in items[:max_rows]]
    elif site == "airbnb":
        head = _stats_line("Home price per night (whole stay total / nights)", [i["price_night"] for i in items], cur)
        rows = [f"- {i['kind']}: {i['name']} | rating {i.get('rating') or '-'} | {i['price_night']:.0f}/night"
                f" | total {i['price_total']:.0f}" for i in items[:max_rows]]
    elif site == "google_flights":
        head = _stats_line("Flight price per person one way", [i["price_person"] for i in items], cur)
        rows = [f"- {i['airline']} | {i['stops']} | {i['departs']} -> {i['arrives']} | {_h(i['hours'])} | "
                f"{i['price_person']:.0f}/person" for i in sorted(items, key=lambda i: i["price_person"])[:max_rows]]
    elif site == "flixbus":
        head = _stats_line("Bus price per person one way", [i["price_person"] for i in items], cur)
        rows = [f"- {i['departs']} -> {i['arrives']} | {_h(i['hours'])} | {i['transfers']} | {i['price_person']:.2f}/person"
                for i in items[:max_rows]]
    elif site == "hostelworld":
        head = "; ".join(x for x in (
            _stats_line("Hostel private room 'from' price per night", [i["privates_from"] for i in items if i["privates_from"]], cur),
            _stats_line("Dorm bed 'from' price per night", [i["dorms_from"] for i in items if i["dorms_from"]], cur)) if x)
        rows = [f"- {i['name']} | rating {i.get('rating') or '-'} | {i.get('distance') or ''} | privates from "
                f"{i['privates_from'] or '-'} | dorms from {i['dorms_from'] or '-'}" for i in items[:max_rows]]
    elif site == "numbeo":
        head = f"Typical local prices ({cur}):"
        rows = [f"- {i['item']}: {i['price']:.2f} (range {i.get('range') or '-'})" for i in items[:max_rows]]
    elif site == "billiger_mietwagen":
        head = _stats_line("Car rental 'from' price per day, recent searches", [i["price_day"] for i in items], cur)
        rows = [f"- {i['category']}: from {i['price_day']:.0f}/day" for i in items[:max_rows]]
    elif site == "trainline":
        i = items[0]
        head = (f"Train: average {_h(i['avg_hours'])}, fastest {_h(i['fastest_hours'])}, "
                f"{i['distance_km'] or '?'} km, {i['trains_per_day'] or '?'} trains/day, changes {i['changes'] or '?'}, "
                f"advance fares from {i['price_from'] or '?'} {i['currency']} per person")
    else:
        return ""
    return "\n".join([head, *rows])
