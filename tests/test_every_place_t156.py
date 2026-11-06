"""T-156：全国のどこを選んでも、結果の画面の地域の 1 行と /city/ の頁が食い違わない。

全 1,747 の団体コードを通す確かめ（手元で 27 秒）は一度だけ流し、NG は 0 だった。ここでは毎回流せるように、
各都道府県の団体コードの小さい方から 1 つと、市の公式頁から事業が書けた市区町村（lane-b のデータ）と、
都・市の制度を一つずつ書いた市を通す。データが増えたときに、帯の数・住所の絞り込み・文の埋め残しの崩れに気づくため。
判定・問い・読み取りは変えない（画面に返す形を見るだけ）。
"""
import re

import pytest
from fastapi.testclient import TestClient

from todoku import app as app_mod
from todoku import rules as rules_mod

client = TestClient(app_mod.app)
RULES, PLACES = app_mod.RULES, app_mod.PLACES

FACTS = {"age_band": "age_75_plus", "residence": "home", "uses_diapers": True, "care_level": "care4"}
LEFT = re.compile(r"\{(pref|name|n|date|what|mark|code|subject)\}|\bNone\b|\bundefined\b|\bNaN\b")
BAND_LI = re.compile(r'<li(?: class="city-break-types")?>.*? <b>(\d+)</b>')


def _codes() -> list[str]:
    first = {}
    for code in sorted(PLACES):
        first.setdefault(code[:2], code)
    found = [c for c in PLACES if any(app_mod.local_mod.found_in(app_mod.LOCAL, c, p.id)
                                      for p in RULES.programs.values() if p.level == "local")]
    detailed = [c for c in PLACES if any(v for f, v in RULES.place_values(c).items()
                                         if f in rules_mod.LEVEL_PLACE.values() and "city" in RULES.places[f])]
    return sorted(set(first.values()) | set(found) | set(detailed))


CODES = _codes()


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def test_codes_cover_every_prefecture():
    assert len({c[:2] for c in CODES}) == 47
    assert "132098" in CODES and "141003" in CODES  # 町田市・横浜市


@pytest.mark.parametrize("code", CODES)
def test_result_region_matches_city_page(code):
    pref, name = PLACES[code]
    d = client.post("/api/step", json={"facts": FACTS, "municipality": code, "asked": 99}).json()
    assert d["place"] == {"code": code, "pref": pref, "name": name}
    rg = d["region"]
    assert rg["code"] == code and rg["total"] > 0
    assert all(x["n"] > 0 for x in rg["parts"])  # 0 件の段は出さない
    # 住んでいない都・市の制度は、結果に並べない
    values = RULES.place_values(code)
    away = [r["id"] for r in d["results"]
            if rules_mod.LEVEL_PLACE.get(RULES.programs[r["id"]].level) and
            values[rules_mod.LEVEL_PLACE[RULES.programs[r["id"]].level]] is not True]
    assert away == []
    # 文に {pref} などの埋め残しや None が出ない
    assert [s for s in _strings(d) if LEFT.search(s)] == []
    # /city/ の頁の帯と、内訳の数も合計も同じ
    html = client.get(f"/city/{code}").text
    band = html.split('class="city-break"', 1)[1].split("</ul>", 1)[0]
    assert [int(n) for n in BAND_LI.findall(band)] == [x["n"] for x in rg["parts"]]
    assert f'<p class="city-total"><b>{rg["total"]}</b> 件' in html
    assert not re.search(r"(?<![\d,.])0 件|<b>0</b>", band)


@pytest.mark.parametrize("code", CODES)
def test_place_is_not_asked_again_when_chosen(code):
    # お住まいを選んで出来事から始めたら、住所の問いを聞き返さない（P1：お住まいは 3 問の枠の外で最初に聞く）
    d = client.post("/api/event", json={"ids": ["discharged"], "municipality": code}).json()
    assert d["region"]["code"] == code
    assert d["question"] is None or d["question"]["fact"] != RULES.place_question
