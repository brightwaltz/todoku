"""T-201：第 3 の層に都道府県の分も 1 つの並びで来る（資料蓄積の municipal-shape-1107、11/7 10:45 の変更）。

- 各行の by（申し込む先の自治体の名前）ごとに、結果の画面の枠の中を小見出しで分ける。型の名前（category_label）を出す。
  型は 2 つ増えた：chusha（思いやり駐車場の利用証）・yutai（シニアの優待・割引）。名前はサーバの category_label をそのまま出す。
- 都道府県の制度は、結果の画面では第 3 の層だけに出る（rules/pref_programs.yaml は /city/ の頁の都道府県の段だけ）。
- /city/ の頁の都道府県の段は rules/pref_programs.yaml（T-129）のまま。第 3 の層の段には、その市区町村の分だけを出す
  （両方にある制度が 2 回出ないように）。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from todoku import city as city_mod
from todoku import rules as rules_mod

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
RULES = rules_mod.load()
TITLE = "市区町村の公式サイトから自動で集めた制度"
MARK = "Claude が公式サイトから集め、機械で確かめたもの（人は照らしていない。2026-11-07）"


def program(pid: str, by: str | None, category: str, label: str, **kw) -> dict:
    base = {"id": pid, "category": category, "category_label": label, "name": f"制度 {pid}",
            "name_on_page": f"頁の名前 {pid}", "summary": f"説明 {pid}", "status": "unknown",
            "status_label": "確かめると使えるかもしれない", "reasons": [], "at_application": [], "where": "高齢福祉課",
            "amount": [], "url": f"https://www.example.lg.jp/{pid}.html", "confidence": "high"}
    if by is not None:
        base["by"] = by
    return {**base, **kw}


# サーバの並び（unknown が先、分からない条件の少ない順）では、県と市の行が混ざって来る
MIXED = [
    program("muni-092011-01", "宇都宮市", "kaigo-yohin", "紙おむつ・介護用品"),
    program("muni-09-01", "栃木県", "chusha", "思いやり駐車場の利用証"),
    program("muni-092011-02", "宇都宮市", "ribiyo", "訪問理美容"),
    program("muni-09-02", "栃木県", "yutai", "シニアの優待・割引", status="ineligible", status_label="当てはまらない"),
]


def _fn(name: str) -> str:
    return re.search(rf"^function {name}\(.*?^}}", JS, re.S | re.M).group(0)


def groups(programs: list[dict], fallback: str) -> list[dict]:
    if not shutil.which("node"):
        pytest.skip("node が無い")
    code = (_fn("muniGroups") + f"\nconsole.log(JSON.stringify(muniGroups({json.dumps(programs, ensure_ascii=False)}, "
            f"{json.dumps(fallback, ensure_ascii=False)})));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    return [{"by": g["by"], "ids": [p["id"] for p in g["programs"]]} for g in json.loads(out)]


def test_groups_by_applicant_in_first_appearance_order_and_keep_server_order_inside():
    assert groups(MIXED, "宇都宮市") == [
        {"by": "宇都宮市", "ids": ["muni-092011-01", "muni-092011-02"]},
        {"by": "栃木県", "ids": ["muni-09-01", "muni-09-02"]},
    ]
    # 県の行が先に来れば、県の組が先
    assert [g["by"] for g in groups(MIXED[1:], "宇都宮市")] == ["栃木県", "宇都宮市"]


def test_only_prefecture_rows_still_get_a_heading_and_rows_without_by_go_to_the_city():
    # その市区町村のファイルが無く、県の分だけのとき（例：茨城県の市で 08.yaml だけ）。小見出しは 1 つでも出す
    assert groups([MIXED[1]], "水戸市") == [{"by": "栃木県", "ids": ["muni-09-01"]}]
    # by の無い行（T-199 の形）は住所の市区町村の組
    assert groups([program("a", None, "sonota", "そのほか"), program("b", "栃木県", "chusha", "思いやり駐車場の利用証")],
                  "宇都宮市") == [{"by": "宇都宮市", "ids": ["a"]}, {"by": "栃木県", "ids": ["b"]}]
    assert groups([], "宇都宮市") == []


def test_result_frame_renders_one_heading_per_applicant_and_the_category_name():
    render = _fn("renderMunicipal")
    assert "muniGroups(programs, m ? m.name : \"\")" in render
    assert 'el("h3", {}, `${g.by}の制度`' in render and "g.programs.map(muniCard)" in render
    # 枠の見出しの件数は全部の数（県＋市）。出す・出さないは T-199 のまま
    assert "`${programs.length} 件`" in render and 'show("muni-box", programs.length > 0)' in render
    card = _fn("muniCard")
    assert "p.category_label" in card and '"muni-cat"' in card
    assert ".muni-by > h3" in (ROOT / "web" / "style.css").read_text(encoding="utf-8")


def test_result_screen_shows_prefecture_programs_only_in_the_third_layer():
    # 結果の画面は rules/pref_programs.yaml（/city/ の頁の都道府県の段）を読まない。県の制度は municipal の by の組だけに出る
    code = "\n".join(line for line in JS.splitlines() if not line.lstrip().startswith("//"))
    html = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    for text in (code, html):
        assert "pref_programs" not in text and "pref-looked" not in text


def _page(municipal=None) -> str:
    return city_mod.city_page(RULES, "092011", "栃木県", "宇都宮市", "https://example.jp/center", municipal=municipal)


def muni(programs: list[dict]) -> dict:
    return {"code": "092011", "name": "宇都宮市", "site": "www.city.utsunomiya.lg.jp", "mark": MARK, "programs": programs}


def _section(page: str) -> str:
    return re.search(r'<section class="card soft city-group" data-level="municipal">(.*?)</section>', page, re.S).group(1)


def test_city_page_lists_only_the_city_rows_with_the_category_name():
    section = _section(_page(muni(MIXED)))
    assert f'{TITLE}<span class="city-n">2 件</span>' in section
    assert re.findall(r'data-id="([^"]+)"', section) == ["muni-092011-01", "muni-092011-02"]
    assert "栃木県" not in section and "思いやり駐車場の利用証" not in section
    assert '<h3>制度 muni-092011-02</h3><p class="muni-cat">訪問理美容</p><p>説明 muni-092011-02</p>' in section


def test_city_page_has_no_frame_when_only_prefecture_rows_and_keeps_the_prefecture_section():
    plain = _page()
    assert _page(muni([p for p in MIXED if p["by"] == "栃木県"])) == plain
    # 都道府県の段（T-129 の調べた記録）は municipal を渡しても同じ
    head = lambda html: re.search(r'<section class="[^"]*" data-level="pref">.*?</section>', html, re.S)
    assert head(plain) and head(_page(muni(MIXED))).group(0) == head(plain).group(0)
