"""T-199：第 3 の層（市区町村の公式サイトから自動で集めた制度）を、確かな層と分けた枠に出す。
データの形は資料蓄積の municipal-shape-1107（step_view の municipal。無いときは null）。
この層は results に入らないので、制度の地図・見立ての帯・押した数の図・いま先にすることの数は動かない。"""
import re
from pathlib import Path

from todoku import city as city_mod
from todoku import rules as rules_mod

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
RULES = rules_mod.load()
TITLE = "市区町村の公式サイトから自動で集めた制度"
MARK = "Claude が公式サイトから集め、機械で確かめたもの（人は照らしていない。2026-11-07）"


def program(i: int, **kw) -> dict:
    base = {"id": f"muni-092011-{i:02d}", "category": "kaigo-yohin", "category_label": "紙おむつ・介護用品",
            "name": f"制度{i}", "name_on_page": f"頁の名前{i}", "summary": f"説明{i}", "status": "eligible",
            "status_label": "当てはまる見込み", "reasons": [], "at_application": [], "where": "高齢福祉課",
            "amount": [], "url": f"https://www.city.utsunomiya.lg.jp/p{i}.html", "confidence": "high"}
    return {**base, **kw}


def muni(*programs) -> dict:
    return {"code": "092011", "name": "宇都宮市", "site": "www.city.utsunomiya.lg.jp", "mark": MARK,
            "programs": list(programs)}


def _fn(name: str) -> str:
    """app.js の関数 1 つの本文（次の行頭の } まで）。"""
    return re.search(rf"^function {name}\(.*?^}}", JS, re.S | re.M).group(0)


def test_result_frame_is_folded_hidden_and_sits_between_region_and_ineligible():
    box = re.search(r'<details class="card soft fold muni" id="muni-box" hidden>(.*?)</details>', HTML, re.S).group(1)
    assert TITLE in box and 'id="muni-n"' in box and 'id="muni-mark"' in box and 'id="muni-list"' in box
    assert HTML.index('id="region-line"') < HTML.index('id="muni-box"') < HTML.index('id="ineligible-box"')


def test_result_frame_is_fed_only_from_data_municipal():
    assert "renderMunicipal(data.municipal);" in _fn("renderResult")
    # 確かな層の数・地図・帯・いま先にすること・申請メモは results だけを見る（第 3 の層を混ぜない）
    for name in ("tally", "renderMap", "renderBoard", "todoItems", "renderTodo", "renderEffort"):
        assert "municipal" not in _fn(name), name
    render = _fn("renderMunicipal")
    assert 'show("muni-box", programs.length > 0)' in render and "m.mark" in render
    card = _fn("muniCard")
    assert "\"card muni-item\"" in card and "option" not in card
    assert 'p.confidence === "low"' in card and "MUNI_LOW" in card and 'target: "_blank"' in card
    assert 'const MUNI_LOW = "一覧の説明だけから読んだもの";' in JS
    assert city_mod.MUNI_LOW == "一覧の説明だけから読んだもの" and city_mod.MUNI_TITLE == TITLE


def _page(municipal=None) -> str:
    return city_mod.city_page(RULES, "092011", "栃木県", "宇都宮市", "https://example.jp/center", municipal=municipal)


def test_city_page_has_no_frame_without_data():
    for m in (None, muni()):
        assert 'data-level="municipal"' not in _page(m) and TITLE not in _page(m)


def test_city_page_frame_lists_programs_without_status_and_keeps_the_totals():
    plain = _page()
    page = _page(muni(program(1, status="ineligible", status_label="当てはまらない", confidence="low"),
                      program(2, where="", url="https://www.city.utsunomiya.lg.jp/a?b=1&c=2")))
    section = re.search(r'<section class="card soft city-group" data-level="municipal">(.*?)</section>', page, re.S).group(1)
    assert f"{TITLE}<span class=\"city-n\">2 件</span>" in section and MARK in section
    assert section.count('class="city-item muni-item"') == 2 and section.count(city_mod.MUNI_LOW) == 1
    assert "当てはまらない" not in section  # この頁はご本人の状況を知らないので判定は出さない
    assert section.count("<b>申請先</b>") == 1  # 申請先の無いものは行を出さない
    assert 'href="https://www.city.utsunomiya.lg.jp/a?b=1&amp;c=2" target="_blank" rel="noopener">宇都宮市「頁の名前2」' in section
    # 合計と段の内訳（帯）は変わらない。段は確かな層の段のあと、ほかの都道府県の地図の前
    head = lambda html: re.search(r'<div class="city-summary".*?</div>', html, re.S).group(0)
    assert head(page) == head(plain)
    assert page.replace(re.search(r'<section class="card soft city-group" data-level="municipal">.*?</section>', page, re.S).group(0), "") == plain


def test_city_page_escapes_the_collected_text():
    page = _page(muni(program(1, name="<b>x</b>", summary="a&b", name_on_page='"q"')))
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page and "a&amp;b" in page and "&quot;q&quot;" in page
