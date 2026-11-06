"""T-176：/city/ の都道府県の相談先の電話番号を、結果の画面の番号と同じ .tel にする。

直す前は素の a で、390px でも 1280px でも「03-」と「6238-0173」のように番号の途中で行が割れ、
押せる高さは 17px だった（結果の画面の .tel は太字・折らない・上下 14px まで押せる。T-029）。
"""
import json
import re
from pathlib import Path

from todoku import contacts
from todoku import rules as rules_mod
from todoku.city import city_page

ROOT = Path(__file__).resolve().parents[1]
RULES = rules_mod.load()
ALL = contacts.load(ROOT / "rules" / "contacts" / "prefectures.yaml")


def test_every_tel_link_on_city_pages_uses_the_tel_class():
    """相談先に電話がある都道府県の頁で、tel: のリンクは全部 class="tel"。"""
    data = json.loads((ROOT / "web" / "municipalities.json").read_text(encoding="utf-8"))
    seen = 0
    for p in data["prefectures"]:
        code, name = p["cities"][0]
        if not any(c.tel for c in ALL.get(code[:2], [])):
            continue
        html = city_page(RULES, code, p["name"], name, "https://example.jp/center")
        links = re.findall(r'<a [^>]*href="tel:[^"]*"[^>]*>', html)
        assert links, p["name"]
        for a in links:
            assert 'class="tel"' in a, (p["name"], a)
        seen += 1
    assert seen == 47  # 47 都道府県とも電話のある相談先がある


def test_tel_class_keeps_number_whole_and_widens_the_tap_area():
    """.tel は番号の途中で折らず、::after で上下に押せる範囲を広げる（印刷では広げない）。"""
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"^\.tel \{([^}]*)\}", css, re.M).group(1)
    assert "white-space: nowrap" in rule and "position: relative" in rule
    after = re.search(r"^\.tel::after \{([^}]*)\}", css, re.M).group(1)
    assert re.search(r"inset: -1[0-9]px 0", after)
