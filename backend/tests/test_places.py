import httpx
import pytest

from app import geo
from app.places_index import PlacesIndex, normalize


def row(gid, name, alt, lat, lon, cc, admin1, pop, code="PPLA"):
    cols = [str(gid), name, normalize(name), alt, str(lat), str(lon), "P", code, cc, "", admin1,
            "", "", "", str(pop), "", "", "Europe/Zurich", "2024-01-01"]
    return "\t".join(cols)


@pytest.fixture
def index(tmp_path):
    (tmp_path / "countryInfo.txt").write_text(
        "#ISO\tISO3\tISO-Numeric\tfips\tCountry\n"
        "IT\tITA\t380\tIT\tItaly\nCH\tCHE\t756\tSZ\tSwitzerland\nCN\tCHN\t156\tCH\tChina\n"
        "FR\tFRA\t250\tFR\tFrance\nTG\tTGO\t768\tTO\tTogo\nPT\tPRT\t620\tPO\tPortugal\n")
    (tmp_path / "admin1CodesASCII.txt").write_text("CH.ZH\tZurich\tZurich\t1\nCN.22\tBeijing\tBeijing\t2\n")
    (tmp_path / "cities15000.txt").write_text("\n".join([
        row(1, "Rome", "Rom,Roma,Rzym", 41.89, 12.48, "IT", "07", 2_318_895, "PPLC"),
        row(2, "Zürich", "Zurich,Zuerich,Zurigo", 47.37, 8.55, "CH", "ZH", 341_730),
        row(3, "Beijing", "Peking,Pekin,Beijing Shi", 39.91, 116.40, "CN", "22", 18_960_744, "PPLC"),
        row(4, "Faro", "Ro", 37.02, -7.93, "PT", "", 41_355),
        row(5, "Lomé", "Lome", 6.13, 1.22, "TG", "", 749_700, "PPLC"),
        row(6, "Paris", "Parigi", 48.85, 2.35, "FR", "", 2_138_551, "PPLC"),
        row(7, "Paris 15 Vaugirard", "", 48.84, 2.29, "FR", "", 230_000),
        row(8, "Altstadt", "", 47.37, 8.54, "CH", "ZH", 20_000, "PPLX"),
    ]) + "\n")
    return PlacesIndex(tmp_path)


def names(cities):
    return [c.name for c in cities]


def test_prefix_and_population_ranking(index):
    assert names(index.search("ro"))[0] == "Rome"          # big primary-name city beats alias 'Ro'
    assert names(index.search("rom")) == ["Rome"]


@pytest.mark.parametrize("q", ["zür", "zur", "Zuerich", "ZÜRICH"])
def test_accents_and_aliases(index, q):
    assert names(index.search(q)) == ["Zürich"]


def test_foreign_alias(index):
    assert names(index.search("peking")) == ["Beijing"]


def test_districts_are_hidden(index):
    assert names(index.search("paris")) == ["Paris"]
    assert names(index.search("alt")) == []                # PPLX = part of a city


def test_city_with_country_filter(index):
    assert names(index.search("rome italy")) == ["Rome"]
    assert names(index.search("paris, france")) == ["Paris"]
    assert names(index.search("rome france")) == []


def test_result_fields(index):
    d = index.search("zurich")[0].as_dict()
    assert (d["name"], d["country"], d["state"]) == ("Zürich", "Switzerland", "Zurich")


def test_nearest_prefers_big_city(index):
    assert index.nearest(47.36, 8.53).name == "Zürich"     # not the district
    assert index.nearest(41.9, 12.5).name == "Rome"
    assert index.nearest(0.0, -30.0) is None                # middle of the Atlantic


async def test_photon_failure_returns_empty_instead_of_error(monkeypatch):
    class Boom:
        async def get(self, *a, **k):
            raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(geo, "_http", lambda: Boom())
    monkeypatch.setattr(geo, "get_index", lambda: None)
    assert await geo.search("some tiny village") == []
    assert await geo.reverse(1.0, 2.0) is None


async def test_photon_only_when_nothing_local_and_filtered(monkeypatch, index):
    calls = []

    class FakePhoton:
        async def get(self, url, params):
            calls.append(params["q"])
            feats = [
                {"properties": {"name": "Pan de Azúcar", "type": "village", "country": "Uruguay"},
                 "geometry": {"coordinates": [-55.2, -34.8]}},
                {"properties": {"name": "Concepcion", "type": "city", "country": "Chile"},
                 "geometry": {"coordinates": [-73.0, -36.8]}},
            ]
            return httpx.Response(200, json={"features": feats}, request=httpx.Request("GET", url))

    monkeypatch.setattr(geo, "_http", lambda: FakePhoton())
    monkeypatch.setattr(geo, "get_index", lambda: index)
    assert [p["name"] for p in await geo.search("zürich")] == ["Zürich"]
    assert calls == []                                       # local hit -> no Photon call
    assert [p["name"] for p in await geo.search("pan de azucar")] == ["Pan de Azúcar"]
    assert calls == ["pan de azucar"]
