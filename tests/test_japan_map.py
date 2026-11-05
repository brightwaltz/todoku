"""T-080c：全国の見取り図。47 都道府県のタイルが重ならず、深さの色が規則の段と合い、入口と /city/ に出るか。"""

import os
import re
from pathlib import Path

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from todoku import rules as rules_mod  # noqa: E402
from todoku.app import PREFS, RULES, app  # noqa: E402
from todoku.japan_map import TILES, coverage, japan_map, short_name  # noqa: E402

client = TestClient(app)
WEB = Path(__file__).resolve().parents[1] / "web"


def tiers(html: str) -> dict[str, str]:
    return dict(re.findall(r'data-pref="(\d\d)" data-tier="(\d)"', html))


def test_tiles_are_47_prefectures_without_overlap():
    assert sorted(TILES) == sorted(PREFS) == [f"{i:02d}" for i in range(1, 48)]
    assert len(set(TILES.values())) == 47
    assert max(x for x, _ in TILES.values()) <= 12  # 13 列。390px でもタイルが 25px ほどに収まる幅


def test_tiers_follow_the_place_facts_of_the_rules():
    cov = {c.code: c for c in coverage(RULES, PREFS)}
    # 東京都は都の制度と町田市の制度、神奈川県は横浜市の制度。ほかは国の制度と型だけ
    assert cov["13"].levels == ["tokyo", "machida"] and cov["13"].tier == 2
    assert cov["14"].levels == ["yokohama"] and cov["14"].tier == 1
    assert all(c.tier == 0 and c.levels == [] for code, c in cov.items() if code not in ("13", "14"))
    # 段の数は rules.places から数え直したものと同じ
    for code, c in cov.items():
        want = sum(1 for fid in rules_mod.LEVEL_PLACE.values()
                   if (RULES.places[fid].get("city") or "")[:2] == code or RULES.places[fid].get("pref") == code)
        assert len(c.levels) == want


def test_map_html_has_a_link_and_label_for_every_tile():
    html = japan_map(RULES, PREFS, "14")
    assert len(re.findall(r'<a href="/\?pref=\d\d"', html)) == 47
    assert tiers(html)["13"] == "2" and tiers(html)["14"] == "1" and tiers(html)["01"] == "0"
    assert html.count('aria-current="true"') == 1 and 'data-pref="14" data-tier="1" aria-label="神奈川県：' in html
    assert "横浜市の制度 7 件" in html and "東京都の制度 1 件・町田市の制度 10 件" in html
    national = sum(1 for p in RULES.programs.values() if p.level == "national")
    assert f"国の制度 {national} 件" in html and "47 都道府県のどこでも" in html
    assert "<script" not in html and "style=" not in html  # CSP（script-src・style-src 'self'）に触れない


def test_short_names_fit_the_tiles():
    names = {short_name(n) for n in PREFS.values()}
    assert {"北海道", "東京", "京都", "大阪", "神奈川", "鹿児島"} <= names
    assert all(2 <= len(n) <= 3 for n in names)


def test_entrance_loads_the_map_part():
    r = client.get("/japan-map")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert len(tiers(r.text)) == 47 and 'aria-current' not in r.text
    # T-134b で入口から外し、別の頁（web/japan.html と japan.js）が読み込む。タイルの /?pref= は入口の app.js が受ける
    page = (WEB / "japan.html").read_text(encoding="utf-8")
    assert 'id="japan"' in page and 'id="japan-box"' in page
    assert 'fetch("/japan-map")' in (WEB / "japan.js").read_text(encoding="utf-8")
    assert 'get("pref")' in (WEB / "app.js").read_text(encoding="utf-8")


def test_city_page_shows_the_map_with_its_prefecture_marked():
    html = client.get("/city/271004").text  # 大阪市
    assert 'data-level="map"' in html and len(tiers(html)) == 47
    assert re.search(r'data-pref="27" data-tier="0" aria-label="[^"]+" aria-current="true"', html)
    assert html.count('aria-current="true"') == 1
