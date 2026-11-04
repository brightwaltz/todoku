"""T-080b：/city/<団体コード> の頁。件数が規則と合うか、住んでいない市・都の制度を出さないか。"""

import os
import re
from pathlib import Path

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import PLACES, RULES, app  # noqa: E402
from todoku.city import city_groups  # noqa: E402

client = TestClient(app)
WEB = Path(__file__).resolve().parents[1] / "web"

MACHIDA, OSAKA, YOKOHAMA = "132098", "271004", "141003"
VILLAGE = "023043"  # 青森県 蓬田村


def ids_of(level: str, keep=lambda p: True) -> set[str]:
    return {p.id for p in RULES.programs.values() if p.level == level and keep(p)}


def page_ids(html: str) -> dict[str, set[str]]:
    """頁の段（data-level）ごとに、並んだ制度の id を拾う。"""
    out = {}
    for level, section in re.findall(r'<section class="card[^"]*" data-level="([^"]+)">(.*?)</section>', html, re.S):
        out[level] = set(re.findall(r'class="city-item" data-id="([^"]+)"', section))
    return out


def test_codes_used_here_are_the_places_named():
    assert PLACES[MACHIDA] == ("東京都", "町田市")
    assert PLACES[OSAKA] == ("大阪府", "大阪市")
    assert PLACES[VILLAGE] == ("青森県", "蓬田村")


def test_machida_lists_national_tokyo_machida_and_only_shown_in_types():
    r = client.get(f"/city/{MACHIDA}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    got = page_ids(r.text)
    assert got["national"] == ids_of("national")
    assert got["tokyo"] == ids_of("tokyo")
    assert got["machida"] == ids_of("machida")
    assert got["local"] == ids_of("local", lambda p: "machida" in p.shown_in)
    assert "yokohama" not in got
    assert f'{len(ids_of("machida"))} 件' in r.text


@pytest.mark.parametrize("code", [OSAKA, VILLAGE])
def test_places_without_their_own_rules_get_national_and_all_five_types(code):
    r = client.get(f"/city/{code}")
    assert r.status_code == 200
    got = page_ids(r.text)
    assert got["national"] == ids_of("national")
    assert got["local"] == ids_of("local") and len(got["local"]) == 5
    assert got["pref"] == set() and got["city"] == set()  # 都道府県と市の制度はまだ無い、と 1 行で出す
    assert not {"tokyo", "machida", "yokohama"} & set(got)
    pref, name = PLACES[code]
    assert f"{pref}の制度は、まだ規則にしていません" in r.text
    assert f"{pref}{name}で使える制度" in r.text


def test_yokohama_has_city_programs_but_no_kanagawa_programs():
    got = page_ids(client.get(f"/city/{YOKOHAMA}").text)
    assert got["yokohama"] == ids_of("yokohama")
    assert got["pref"] == set()
    assert got["local"] == ids_of("local", lambda p: "yokohama" in p.shown_in)


def test_every_program_shows_where_to_apply_and_a_source():
    html = client.get(f"/city/{OSAKA}").text
    items = re.findall(r'<li class="city-item".*?</details></li>', html, re.S)
    assert len(items) == len(ids_of("national")) + 5
    for item in items:
        assert "申請先" in item and 'href="https://' in item, item[:200]


def test_outside_machida_no_machida_window_or_page():
    """国の制度の申請先は町田市で書いてあるので、大阪市の頁には町田市の窓口も町田市のページも出さない。"""
    html = client.get(f"/city/{OSAKA}").text
    assert "町田市役所" not in html and "city.machida.tokyo.jp" not in html


def test_center_search_uses_the_prefecture_code():
    html = client.get(f"/city/{OSAKA}").text
    assert "kaigokensaku.mhlw.go.jp/27/index.php" in html


def test_every_code_opens_and_unknown_code_is_404():
    for code in PLACES:
        groups = city_groups(RULES, code, *PLACES[code])
        assert sum(len(g.programs) for g in groups) >= len(ids_of("national")) + 1
    assert client.get("/city/999999").status_code == 404
    assert client.get("/city/<script>").status_code == 404


def test_page_does_not_call_claude_and_has_no_inline_script():
    html = client.get(f"/city/{MACHIDA}").text
    assert "<script" not in html and "style=" not in html  # CSP（script-src・style-src は 'self'）に触れない


def test_entry_has_link_to_city_page():
    index = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="city-link"' in index and 'id="city-link-box"' in index
    assert "`/city/${encodeURIComponent(city[0])}`" in js


@pytest.mark.parametrize("code", [MACHIDA, VILLAGE])
def test_center_card_says_how_staff_can_use_it(code):
    """相談先の枠に、包括の職員が相談を受けながら使うときの 1 文があるか（T-089）。文言は city.STAFF_NOTE だけで差し替える。"""
    from html import escape

    from todoku.city import STAFF_NOTE

    html = client.get(f"/city/{code}").text
    center = re.search(r'data-level="center">(.*?)</section>', html, re.S).group(1)
    assert f'<p class="note city-staff">{escape(STAFF_NOTE)}</p>' in center
    assert html.count("city-staff") == 1
