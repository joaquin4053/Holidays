import io
import zipfile
from datetime import date

from app import live_prices as lp
from app.agent import synthesize as syn
from app.models import TripRequest


def req(**kw):
    base = dict(origin="Zurich", stops=[{"name": "Rome", "nights": 3}, {"name": "Florence"}],
                start_date=date(2026, 11, 10), end_date=date(2026, 11, 16), budget=2000, travelers=2)
    base.update(kw)
    return TripRequest(**base)


# --- dates per stop and leg ---------------------------------------------------

def test_stay_dates_follow_nights():
    r = req()
    stays = syn.stay_dates(r, syn.distribute_nights(r))  # 6 nights: Rome 3, Florence 3
    assert stays == {"Rome": (date(2026, 11, 10), date(2026, 11, 13)),
                     "Florence": (date(2026, 11, 13), date(2026, 11, 16))}
    assert syn.leg_date(r, stays, "Florence") == date(2026, 11, 13)
    assert syn.leg_date(r, stays, "Zurich") == date(2026, 11, 16)  # way home on the last day


def test_no_dates_without_start_date():
    r = req(start_date=None, end_date=None, days=7)
    stays = syn.stay_dates(r, syn.distribute_nights(r))
    assert stays == {} and syn.leg_date(r, stays, "Rome") is None


# --- URLs ---------------------------------------------------------------------

def test_dated_sites_need_dates_and_ids():
    q = lp.Query(place="Rome", people=2)
    assert not lp.SITES["airbnb"].url(q) and not lp.SITES["tripcom"].url(q)
    assert lp.SITES["numbeo"].url(q).startswith("https://www.numbeo.com/cost-of-living/in/Rome")
    q.checkin, q.checkout = date(2026, 11, 10), date(2026, 11, 13)
    assert "checkin=2026-11-10" in lp.SITES["airbnb"].url(q)
    assert not lp.SITES["tripcom"].url(q)  # still no city ID
    q.tripcom = ("343", "rome")
    assert "city=343&checkin=2026/11/10&checkout=2026/11/13&adult=2&crn=1" in lp.SITES["tripcom"].url(q)


def test_leg_urls():
    q = lp.Query(place="Rome", origin="Zürich", checkin=date(2026, 11, 10), people=2,
                 flixbus=("a", "b"), car_city_de="München")
    assert "rideDate=10.11.2026&adult=2" in lp.SITES["flixbus"].url(q)
    assert lp.SITES["trainline"].url(q).endswith("/zurich-to-rome")
    assert lp.SITES["billiger_mietwagen"].url(q).endswith("/mietwagen-muenchen.html")


def test_app_uses_only_working_sites():
    assert set(lp.PLACE_SITES) == {"airbnb", "tripcom", "hostelworld", "numbeo"}
    assert set(lp.LEG_SITES) == {"google_flights", "flixbus", "trainline", "billiger_mietwagen"}


# --- ID lookups ---------------------------------------------------------------

def test_tripcom_id_prefers_the_place_itself():
    urls = ["https://www.trip.com/hotels/lazio-hotels-list-999/",
            "https://www.trip.com/hotels/rome-hotels-list-343/zone112213595/"]
    assert lp.tripcom_from_urls(urls, "Rome") == ("343", "rome")
    assert lp.tripcom_from_urls(["https://www.trip.com/travel-guide/rome/"], "Rome") is None


def test_german_name_from_geonames_zip():
    rows = ["1\t3176959\tde\tFlorentia\t\t\t\t1\t\t",   # historic
            "2\t3176959\tde\tFlorenz\t1\t\t\t\t\t",
            "3\t3176959\tit\tFirenze\t1\t\t\t\t\t",
            "4\t9999999\tde\tAnders\t1\t\t\t\t\t"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("IT.txt", "\n".join(rows) + "\n")
    assert lp.german_name_from_zip(buf.getvalue(), 3176959, "IT") == "Florenz"
    assert lp.german_name_from_zip(buf.getvalue(), 1234, "IT") is None


# --- page handling ------------------------------------------------------------

def test_classify_pages():
    prices = "Hotel Corot $308 total " * 40
    assert lp.classify(200, prices) == "ok"
    assert lp.classify(403, "") == "blocked"
    assert lp.classify(200, "Are you a person or a robot? " + prices) == "blocked"
    assert lp.classify(200, "0 properties found " * 50) == "no_prices"
    assert lp.classify(404, prices) == "error"


def test_context_only_usable_pages_cut_to_prices():
    ok = lp.LiveResult(site="airbnb", category="accommodation", url="u1", status="ok", title="Airbnb",
                       text="menu " * 2000 + "Apartment $496 total")
    blocked = lp.LiveResult(site="booking", category="accommodation", url="u2", status="blocked", text="x")
    text = lp.as_context([ok, blocked])
    assert "$496 total" in text and "booking" not in text
    assert len(text) < lp.CONTEXT_CHARS + 200
    assert lp.ok_urls([ok, blocked]) == ["u1"]
