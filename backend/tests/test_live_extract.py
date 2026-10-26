"""Crawl4AI parts: CSS schemas on small copies of the real page structure, parsing, crawl errors."""
import json
from types import SimpleNamespace as NS

import pytest
from crawl4ai import JsonCssExtractionStrategy

from app import live_extract as lx
from app import live_prices as lp


def extract(site: str, html: str, url: str = "") -> list[dict]:
    rows = JsonCssExtractionStrategy(lx.SCHEMAS[site][0]).extract(url, html)
    return lx.normalize(site, rows, url)


# --- numbers --------------------------------------------------------------------

@pytest.mark.parametrize("text, amount, cur", [
    ("$1,234", 1234, "USD"), ("1.234,50 €", 1234.5, "EUR"), ("Fr.24.44", 24.44, "CHF"),
    ("CHF 89", 89, "CHF"), ("ab5,00 €/Tag", 5, "EUR"), ("£61.26", 61.26, "GBP"),
])
def test_money_and_currency(text, amount, cur):
    assert lx.money(text) == amount and lx.currency(text) == cur


def test_hours_and_url_params():
    assert lx.hours("1 hr 35 min") == pytest.approx(1 + 35 / 60)
    assert lx.hours("14:40 hrs") == pytest.approx(14 + 40 / 60)
    assert lx.hours("6h 20m") == pytest.approx(6 + 20 / 60)
    assert lx.url_params("https://x/list?city=343&checkin=2027/03/12&checkout=2027/03/15&adult=2") == {"nights": 3, "people": 2}
    gf = "https://www.google.com/travel/flights?q=Flights+to+Rome+from+Zurich+on+2027-03-12+one+way+2+adults"
    assert lx.url_params(gf) == {"people": 2}


# --- CSS schemas (structure copied from the live pages, 2026-10-07) ---------------

def test_tripcom_rows():
    html = """<div class="hotel-card"><a class="hotelName">Hotel Corot</a><span class="score">8.3</span>
      <div class="room-name">Double Room</div><span class="sale">$112</span><span class="price-highlight">$410</span></div>
      <div class="hotel-card"><a class="hotelName">Ad without price</a></div>"""
    items = extract("tripcom", html)
    assert items == [{"name": "Hotel Corot", "rating": "8.3", "reviews": None, "room": "Double Room", "area": None,
                      "price_night": 112.0, "price_total": 410.0, "currency": "USD"}]


def test_airbnb_price_per_night_from_total_and_url():
    html = """<div itemprop="itemListElement"><div data-testid="card-container">
      <div data-testid="listing-card-title">Room in Rome</div><span>4.96 (207)</span>
      <span data-testid="listing-card-name">GiGi on Via Giulia</span>
      <div data-testid="price-availability-row"><span>$723</span><span>$536 total</span></div></div></div>"""
    url = "https://www.airbnb.com/s/Rome/homes?checkin=2027-03-12&checkout=2027-03-15&adults=2"
    [item] = extract("airbnb", html, url)
    assert item["price_total"] == 536 and item["price_night"] == pytest.approx(178.67, abs=0.01)
    assert item["rating"] == "4.96" and item["reviews"] == "207"


def test_google_flights_layout_with_one_label_deduplicated():
    label = ("From 105 US dollars. Nonstop flight with easyJet. Leaves Zurich Airport at 9:55 PM on Friday, "
             "March 12 and arrives at Leonardo da Vinci International Airport at 11:30 PM on Friday, March 12. "
             "Total duration 1 hr 35 min.   Select flight")
    nbsp_label = label.replace("flight with", "flight\u00a0with")  # Google uses no-break spaces
    row = (f'<li><div aria-label="{nbsp_label}">'
           '<div>9:55 PM\u00a0on\u00a0Fri, Mar 12easyJet1 hr 35 min</div></div></li>')
    url = "https://www.google.com/travel/flights?q=Flights+to+Rome+from+Zurich+on+2027-03-12+one+way+2+adults"
    [f] = extract("google_flights", row + row, url)
    assert f["airline"] == "easyJet" and f["stops"] == "Nonstop" and f["departs"] == "9:55 PM"
    assert f["price_total"] == 105 and f["price_person"] == 52.5 and f["hours"] == pytest.approx(1 + 35 / 60)


def test_google_flights_layout_with_split_labels():
    """Return leg Rome -> Zurich (2026-10-08): price, stops and times in separate aria-labels."""
    row = """<li><div><div aria-label="Leaves Leonardo da Vinci International Airport at 6:00 AM on Monday, March 15
      and arrives at Zurich Airport at 7:35 AM on Monday, March 15."></div><div aria-label="Total duration 1 hr 35 min.">
      </div><div aria-label="Nonstop flight."></div><span aria-label="92 US dollars"></span>
      <button aria-label="Select flight"></button><div>6:00 AM on Mon, Mar 15–7:35 AM on Mon, Mar 15easyJet1 hr 35 min</div>
      </div></li>"""
    url = "https://www.google.com/travel/flights?q=Flights+to+Zurich+from+Rome+on+2027-03-15+one+way+2+adults"
    [f] = extract("google_flights", row, url)
    assert (f["airline"], f["stops"], f["departs"], f["arrives"]) == ("easyJet", "Nonstop", "6:00 AM", "7:35 AM")
    assert f["price_person"] == 46 and f["hours"] == pytest.approx(1 + 35 / 60)


def test_flixbus_rows():
    html = """<li data-e2e="search-result-item abc"><time>12:40 AM</time><time>03:20 PM</time>
      <span data-e2e="search-result-duration">14:40 hrs</span><span data-e2e="search-result-prices">$60<sup>.48</sup></span>
      <span class="TransferPill__transferTypeLabel___R7OUb">1 transfer</span></li>"""
    [r] = extract("flixbus", html)
    assert r == {"departs": "12:40 AM", "arrives": "03:20 PM", "hours": pytest.approx(14 + 40 / 60),
                 "transfers": "1 transfer", "price_person": 60.48, "currency": "USD"}


def test_hostelworld_rows_and_missing_label():
    html = """<a class="property-listing-card"><span class="property-name">YellowSquare Rome</span>
      <span class="score">9.1</span><span class="num-reviews">(24940)</span>
      <div class="property-accommodation-price"><div class="accommodation-label">Privates From</div><strong>Fr.24.44</strong></div>
      <div class="property-accommodation-price"><strong>Fr.1.00</strong></div>
      <div class="property-accommodation-price"><div class="accommodation-label">Dorms From</div><strong>Fr.17.11</strong></div></a>"""
    [h] = extract("hostelworld", html)
    assert h["privates_from"] == 24.44 and h["dorms_from"] == 17.11 and h["currency"] == "CHF"
    assert h["reviews"] == "24940"


def test_numbeo_keeps_travel_relevant_rows():
    html = """<table class="data_wide_table">
      <tr><td>Meal at an Inexpensive Restaurant</td><td class="priceValue">$16.89</td><td class="priceBarTd">13.51-22.52</td></tr>
      <tr><td>Milk (Regular, 1 Liter)</td><td class="priceValue">$1.72</td><td class="priceBarTd">1-2</td></tr>
      <tr><td>One-Way Ticket (Local Transport)</td><td class="priceValue">$1.69</td><td class="priceBarTd">1.69-1.69</td></tr></table>"""
    assert [i["item"] for i in extract("numbeo", html)] == ["Meal at an Inexpensive Restaurant",
                                                           "One-Way Ticket (Local Transport)"]


def test_car_rental_rows():
    html = "<article><header>Kleinwagen</header><data>ab<span>5,00 €</span><span>/Tag</span></data></article>"
    assert extract("billiger_mietwagen", html) == [{"category": "Kleinwagen", "price_day": 5.0, "currency": "EUR"}]


def test_trainline_facts_from_markdown():
    md = ("It takes an average of 7h 40m to travel from Zürich to Rome by train, over a distance of around "
          "426 miles (685 km). There are normally 26 trains per day travelling from Zürich to Rome and tickets "
          "for this journey start from CHF 67.50 when you book in advance.\n| Journey time  | From 6h 20m  |   |\n"
          "| Changes  | 1 change  |   |")
    [t] = lx.parse_markdown("trainline", md)
    assert t["avg_hours"] == pytest.approx(7 + 40 / 60) and t["fastest_hours"] == pytest.approx(6 + 20 / 60)
    assert (t["distance_km"], t["trains_per_day"], t["price_from"], t["currency"]) == (685, 26, 67.5, "CHF")
    assert "26 trains/day" in lx.summarize("trainline", [t])


def test_summary_is_short_with_statistics():
    items = [{"name": f"H{i}", "rating": "8", "room": "Double", "price_night": float(p), "price_total": p * 3,
              "currency": "USD"} for i, p in enumerate([80, 100, 120, 140, 400])]
    text = lx.summarize("tripcom", items, max_rows=2)
    assert "n=5" in text and "median 120" in text and text.count("\n- ") == 2


# --- crawl_page: result handling without a browser ---------------------------------

class FakeCrawler:
    def __init__(self, result):
        self.result, self.configs = result, []

    async def arun(self, url, config):
        self.configs.append(config)
        return self.result


def page(**kw):
    base = dict(success=True, status_code=200, error_message=None, extracted_content=None, redirected_url=None,
                metadata={"title": "Rome Hotels | Trip.com"}, markdown=NS(raw_markdown="", fit_markdown=""))
    base.update(kw)
    return NS(**base)


async def test_crawl_page_uses_css_rows():
    rows = [{"name": "Hotel Corot", "price_night": "$112", "price_total": "$410"}]
    crawler = FakeCrawler(page(extracted_content=json.dumps(rows), markdown=NS(raw_markdown="x" * 600, fit_markdown="")))
    r = await lp.crawl_page(crawler, "tripcom", "https://www.trip.com/hotels/list?city=343")
    assert r.status == "ok" and r.items[0]["price_night"] == 112 and r.title.startswith("Rome Hotels")
    cfg = crawler.configs[0]
    assert cfg.wait_for.startswith("js:") and "div.hotel-card" in cfg.wait_for
    assert not (cfg.magic or cfg.simulate_user or cfg.override_navigator)  # no bot-protection bypass


async def test_crawl_page_missing_result_list_is_a_clear_error():
    err = "Unexpected error in _crawl_web\nError: Wait condition failed: Timeout after 25000ms waiting for selector"
    r = await lp.crawl_page(FakeCrawler(page(success=False, error_message=err)), "airbnb", "https://www.airbnb.com/s/x")
    assert r.status == "not_found" and "result list not found" in r.error


async def test_crawl_page_block_and_crash_do_not_raise():
    blocked = page(success=False, status_code=403, error_message="Error: Wait condition failed",
                   markdown=NS(raw_markdown="Are you a person or a robot?", fit_markdown=""))
    assert (await lp.crawl_page(FakeCrawler(blocked), "airbnb", "u")).status == "blocked"

    class Boom:
        async def arun(self, url, config):
            raise RuntimeError("browser died")
    r = await lp.crawl_page(Boom(), "airbnb", "u")
    assert r.status == "error" and "browser died" in r.error


async def test_markdown_site_without_schema():
    md = "It takes an average of 7h 40m ... start from CHF 67.50 when you book" + " filler" * 100
    r = await lp.crawl_page(FakeCrawler(page(markdown=NS(raw_markdown=md, fit_markdown=""))), "trainline", "u")
    assert r.status == "ok" and r.items[0]["price_from"] == 67.5
    assert FakeCrawler(page()).result and r.items  # no wait_for for markdown sites


def test_context_budget_for_groq():
    big = lp.LiveResult(site="numbeo", category="daily_costs", url="u", status="ok", text="$5 " * 5000)
    rows = lp.LiveResult(site="tripcom", category="accommodation", url="u2", status="ok", title="T",
                         items=[{"name": "H", "rating": "8", "room": "R", "price_night": 100.0, "price_total": 300.0,
                                 "currency": "USD"}])
    assert len(lp.as_context([big, rows], max_chars=1000)) <= 1000 + 10
    assert "median 100" in lp.as_context([rows])


async def test_pages_run_in_parallel_but_one_at_a_time_per_site(monkeypatch):
    """Different sites overlap; the same site never runs twice at once and pauses between requests."""
    import asyncio
    import time
    running: dict[str, int] = {}
    peak = {"total": 0, "same_site": 0}
    log = []

    async def fake_crawl(crawler, site, url):
        running[site] = running.get(site, 0) + 1
        peak["same_site"] = max(peak["same_site"], running[site])
        peak["total"] = max(peak["total"], sum(running.values()))
        log.append((site, time.perf_counter()))
        await asyncio.sleep(0.02)
        running[site] -= 1
        return lp.LiveResult(site=site, category="x", url=url, status="error")
    monkeypatch.setattr(lp, "crawl_page", fake_crawl)
    monkeypatch.setattr(lp, "SITE_PAUSE", 0.05)
    live = lp.LiveBrowser(crawler=None)
    sites = ["airbnb", "tripcom", "numbeo", "hostelworld", "airbnb", "tripcom"]
    await asyncio.gather(*(live.fetch(s, f"u{i}", use_cache=False) for i, s in enumerate(sites)))
    assert peak["same_site"] == 1 and peak["total"] >= 4
    airbnb = [t for s, t in log if s == "airbnb"]
    assert airbnb[1] - airbnb[0] >= 0.02 + 0.05 - 0.005  # load + pause before the next airbnb page


async def test_pause_does_not_hold_back_the_result(monkeypatch):
    import asyncio
    import time

    async def fake_crawl(crawler, site, url):
        return lp.LiveResult(site=site, category="x", url=url, status="error")
    monkeypatch.setattr(lp, "crawl_page", fake_crawl)
    monkeypatch.setattr(lp, "SITE_PAUSE", 0.5)
    live = lp.LiveBrowser(crawler=None)
    t = time.perf_counter()
    await live.fetch("numbeo", "u", use_cache=False)
    assert time.perf_counter() - t < 0.2  # the pause only delays the NEXT numbeo request


async def test_google_cookie_consent_declined_once_per_browser(monkeypatch):
    calls = []

    class Crawler:
        async def arun(self, url, config):
            calls.append((url, config.js_code))
            return page()
    monkeypatch.setattr(lp, "SITE_PAUSE", 0)
    live = lp.LiveBrowser(Crawler())
    await live.fetch("google_flights", "https://www.google.com/travel/flights?q=a", use_cache=False)
    await live.fetch("google_flights", "https://www.google.com/travel/flights?q=b", use_cache=False)
    await live.fetch("airbnb", "https://www.airbnb.com/s/x", use_cache=False)
    assert [u for u, _ in calls] == [lp.CONSENT_WARMUP["google_flights"], "https://www.google.com/travel/flights?q=a",
                                     "https://www.google.com/travel/flights?q=b", "https://www.airbnb.com/s/x"]
    assert "reject all" in calls[0][1]
