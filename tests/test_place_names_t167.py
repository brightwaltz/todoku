"""T-167：お住まいの選択肢で、同じ都道府県に同じ名前の市区町村が 2 つ並ばない。

総務省のコード表では北海道に泊村が 2 つある（古宇郡 014036 と、北方領土の国後郡 016969）。
名前だけでは選択肢も /city/ の見出しも同じになり、古宇郡の泊村の人がどちらを選べばよいか分からなかったので、
web/municipalities.json の 2 つにだけ郡の名前を添えた。判定・問い・読み取りは変えない（名前は画面に出すだけ）。
"""
import collections
import json
import re

from fastapi.testclient import TestClient

from todoku import app as app_mod

client = TestClient(app_mod.app)
DATA = json.loads((app_mod.WEB_DIR / "municipalities.json").read_text(encoding="utf-8"))


def test_names_are_unique_within_each_prefecture():
    for pref in DATA["prefectures"]:
        names = collections.Counter(name for _, name in pref["cities"])
        assert [n for n, k in names.items() if k > 1] == [], pref["name"]


def test_both_tomari_keep_their_codes_and_name():
    places = app_mod.PLACES
    assert places["014036"] == ("北海道", "泊村（古宇郡）")
    assert places["016969"] == ("北海道", "泊村（国後郡）")
    assert sum(len(p["cities"]) for p in DATA["prefectures"]) == 1747  # コードは足しも消しもしない


def test_city_pages_of_the_two_tomari_differ():
    heads = {code: re.search(r"<h1[^>]*>(.*?)</h1>", client.get(f"/city/{code}").text, re.S).group(1)
             for code in ("014036", "016969")}
    assert "古宇郡" in heads["014036"] and "国後郡" in heads["016969"], heads
