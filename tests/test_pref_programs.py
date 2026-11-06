"""T-129：47 都道府県の制度を調べた記録（rules/pref_programs.yaml）の読み込み・形の検査と、/city/ の頁の都道府県の段に出ること。"""
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from todoku import pref_programs
from todoku import rules as rules_mod
from todoku.city import (PREF_CHECKED_NONE, PREF_FOUND_NOTE, SUMMARY_PREF_FOUND, SUMMARY_PREF_NONE, SUMMARY_PREF_UNSEEN,
                         city_groups, city_page, summary_parts)
from todoku.rules import RuleError

ROOT = Path(__file__).resolve().parents[1]
RULES = rules_mod.load()
DATA = json.loads((ROOT / "web" / "municipalities.json").read_text(encoding="utf-8"))
PREFS = {p["cities"][0][0][:2]: (p["name"], p["cities"][0]) for p in DATA["prefectures"]}
ALL = pref_programs.load(ROOT / "rules" / "pref_programs.yaml")
AOMORI = "02"


def write(tmp_path, body: str) -> Path:
    path = tmp_path / "pref_programs.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_record_loads_and_is_well_formed():
    assert set(ALL) <= set(PREFS)
    for code, c in ALL.items():
        assert dt.date(2026, 11, 1) <= c.checked <= dt.date.today(), code
        assert c.looked and len(c.looked) == len(set(c.looked)), code
        for u in list(c.looked) + [x.url for x in c.found]:
            assert re.fullmatch(r"https?://[a-z0-9.-]+/\S*", u), u
    assert "13" not in ALL and "14" not in ALL  # 東京都は規則（level tokyo）、神奈川県は PREF_NONE_FOUND にある


def test_every_prefecture_is_checked():
    """T-129 の完了条件：47 都道府県のどれも、都道府県の段が「まだ調べていない」にならない（東京都・神奈川県は記録の外で扱う）。"""
    assert set(ALL) | {"13", "14"} == set(PREFS)


@pytest.mark.parametrize("item, message", [
    ("{looked: [https://www.pref.example.jp/]}", "checked"),
    ("{checked: 2026-11-06}", "looked"),
    ("{checked: 2026-11-06, looked: [ftp://x]}", "http"),
    ("{checked: 2026-11-06, looked: [https://a.jp/], found: [{name: x, who: y}]}", "name・who・where"),
])
def test_bad_entries_stop_loading(tmp_path, item, message):
    with pytest.raises(RuleError, match=message):
        pref_programs.load(write(tmp_path, f'prefectures:\n  "02": {item}\n'))


def test_bad_code_and_missing_file(tmp_path):
    with pytest.raises(RuleError, match="01〜47"):
        pref_programs.load(write(tmp_path, 'prefectures:\n  "48": {checked: 2026-11-06, looked: [https://a.jp/]}\n'))
    assert pref_programs.load(tmp_path / "none.yaml") == {}


def page(monkeypatch, record: pref_programs.PrefCheck | None) -> tuple[str, list]:
    monkeypatch.setattr(pref_programs, "_all", lambda: {AOMORI: record} if record else {})
    pref, (code, name) = PREFS[AOMORI][0], PREFS[AOMORI][1][:2]
    groups = city_groups(RULES, code, pref, name)
    return city_page(RULES, code, pref, name, "https://example.jp/center"), summary_parts(groups)


def pref_section(html: str) -> str:
    return re.search(r'<section class="card[^"]*" data-level="pref">(.*?)</section>', html, re.S).group(1)


def test_unchecked_prefecture_still_says_not_seen(monkeypatch):
    html, parts = page(monkeypatch, None)
    pref = PREFS[AOMORI][0]
    assert (SUMMARY_PREF_UNSEEN.format(pref=pref), None) in parts
    assert "pref-looked" not in pref_section(html)


def test_checked_and_none_found_says_so_with_the_pages_looked(monkeypatch):
    rec = pref_programs.PrefCheck(dt.date(2026, 11, 6), ("https://www.pref.aomori.lg.jp/a.html",), ())
    html, parts = page(monkeypatch, rec)
    pref = PREFS[AOMORI][0]
    sec = pref_section(html)
    assert (SUMMARY_PREF_NONE.format(pref=pref), None) in parts
    assert PREF_CHECKED_NONE.format(pref=pref, date="2026-11-06") in sec
    assert "まだ規則にしていません" not in sec and 'href="https://www.pref.aomori.lg.jp/a.html"' in sec
    assert "認知症の人と家族の会" in sec  # 相談先（T-123）はそのまま


def test_found_candidates_are_listed_and_not_counted_as_rules(monkeypatch):
    cand = pref_programs.Candidate("県の例の事業", "65 歳以上の県民", "県の福祉事務所", "https://www.pref.aomori.lg.jp/b.html")
    rec = pref_programs.PrefCheck(dt.date(2026, 11, 6), ("https://www.pref.aomori.lg.jp/a.html",), (cand,))
    html, parts = page(monkeypatch, rec)
    pref = PREFS[AOMORI][0]
    sec = pref_section(html)
    assert (SUMMARY_PREF_FOUND.format(pref=pref, n=1), None) in parts
    assert PREF_FOUND_NOTE.format(pref=pref, date="2026-11-06", n=1) in sec
    assert sec.count('class="city-item pref-candidate"') == 1
    assert "県の例の事業" in sec and "65 歳以上の県民" in sec and "県の福祉事務所" in sec
    assert 'href="https://www.pref.aomori.lg.jp/b.html"' in sec
    assert 'data-id=' not in sec  # 規則の制度としては数えない（頁の先頭の件数にも入らない）


@pytest.mark.parametrize("code", ["11", "23"])
def test_checked_prefectures_in_the_record_show_on_their_city_pages(code):
    """埼玉県・愛知県（2026-11-06、見つからなかった）：県庁所在地の頁で「まだ規則にしていません」と出ない。"""
    pref, (city_code, name) = PREFS[code][0], PREFS[code][1][:2]
    html = city_page(RULES, city_code, pref, name, "https://example.jp/center")
    sec = pref_section(html)
    assert PREF_CHECKED_NONE.format(pref=pref, date=ALL[code].checked.isoformat()) in sec
    assert "まだ規則にしていません" not in sec and "まだ調べていません" not in html


@pytest.mark.parametrize("code", sorted(ALL))
def test_every_prefecture_in_the_record_shows_what_was_found_on_its_capital_page(code):
    """記録にある県はどれも、県庁所在地の頁の都道府県の段で「まだ」と出ず、見つかった数か「見つからなかった」が出る。"""
    pref, (city_code, name) = PREFS[code][0], PREFS[code][1][:2]
    rec = ALL[code]
    sec = pref_section(city_page(RULES, city_code, pref, name, "https://example.jp/center"))
    date = rec.checked.isoformat()
    if rec.found:
        assert PREF_FOUND_NOTE.format(pref=pref, date=date, n=len(rec.found)) in sec
        assert sec.count('class="city-item pref-candidate"') == len(rec.found)
    else:
        assert PREF_CHECKED_NONE.format(pref=pref, date=date) in sec
    assert "まだ規則にしていません" not in sec
    for u in rec.looked:
        assert f'href="{u}"' in sec
