"""T-123：47 都道府県の相談先（rules/contacts/prefectures.yaml）の読み込み・形の検査と、/city/ の頁の都道府県の段に出ること。"""
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from todoku import contacts
from todoku import rules as rules_mod
from todoku.city import CONTACTS_TITLE, city_page
from todoku.rules import RuleError

ROOT = Path(__file__).resolve().parents[1]
RULES = rules_mod.load()
DATA = json.loads((ROOT / "web" / "municipalities.json").read_text(encoding="utf-8"))
PREFS = {p["cities"][0][0][:2]: (p["name"], p["cities"][0]) for p in DATA["prefectures"]}
ALL = contacts.load(ROOT / "rules" / "contacts" / "prefectures.yaml")


def test_all_47_prefectures_have_a_contact():
    assert len(PREFS) == 47
    assert sorted(ALL) == sorted(PREFS) == list(contacts.PREF_CODES)
    assert all(ALL[code] for code in PREFS)


def test_urls_tels_and_dates_are_well_formed():
    urls = [c.url for cs in ALL.values() for c in cs]
    assert len(urls) == len(set(urls)), "同じ頁を 2 つの都道府県に置いていない"
    for code, cs in ALL.items():
        for c in cs:
            assert re.fullmatch(r"https://[a-z0-9.-]+/\S*", c.url), c.url
            assert c.tel == "" or contacts.TEL.fullmatch(c.tel), c.tel
            assert dt.date(2026, 11, 1) <= c.checked <= dt.date.today()
            assert c.name(PREFS[code][0]).startswith("認知症の人と家族の会")


def test_family_branch_is_on_the_association_site():
    for cs in ALL.values():
        for c in cs:
            if c.kind == "family_branch":
                assert c.url.startswith("https://alzheimer.or.jp/?page_id=")


@pytest.mark.parametrize("item, message", [
    ({"kind": "family_branch", "url": "http://example.jp/", "checked": "2026-11-05"}, "https"),
    ({"kind": "family_branch", "url": "https://example.jp/", "tel": "03 1234 5678", "checked": "2026-11-05"}, "電話"),
    ({"kind": "nope", "url": "https://example.jp/", "checked": "2026-11-05"}, "kind"),
    ({"kind": "family_branch", "url": "https://example.jp/"}, "checked"),
])
def test_bad_entries_stop_loading(tmp_path, item, message):
    import yaml
    path = tmp_path / "prefectures.yaml"
    path.write_text(yaml.safe_dump({"prefectures": {"01": [item]}}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleError, match=message):
        contacts.load(path)


def test_bad_prefecture_code_stops_loading(tmp_path):
    path = tmp_path / "prefectures.yaml"
    path.write_text('prefectures:\n  "48":\n    - {kind: family_branch, url: "https://example.jp/", checked: 2026-11-05}\n',
                    encoding="utf-8")
    with pytest.raises(RuleError, match="01〜47"):
        contacts.load(path)


def test_missing_file_is_empty(tmp_path):
    assert contacts.load(tmp_path / "none.yaml") == {}


@pytest.mark.parametrize("pref_code", sorted(PREFS))
def test_city_page_shows_the_contact_in_the_prefecture_group(pref_code):
    """どの都道府県の頁でも、都道府県の段（東京都は都の制度の段）の中に相談先が 1 件以上出る。"""
    pref, (code, name) = PREFS[pref_code]
    html = city_page(RULES, code, pref, name, "https://example.jp/center")
    level = "tokyo" if pref_code == "13" else "pref"
    section = re.search(rf'<section class="card city-group" data-level="{level}">.*?</section>', html, re.S).group(0)
    assert CONTACTS_TITLE.format(pref=pref) in section
    for c in ALL[pref_code]:
        assert f'href="{c.url}"' in section
        if c.tel:
            assert f'href="tel:{c.tel}"' in section
