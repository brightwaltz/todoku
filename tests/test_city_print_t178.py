"""T-178：/city/ の頁を紙に刷るとき、畳んだ「出典」の中身も刷り、制度 1 件を頁の境で割らない。

直す前は紙に「▶ 出典」の 1 行だけが制度の数だけ並んだ（町田市で 28 行）。結果の画面は JS（beforeprint）で畳んだ欄を開いて刷るが、
/city/ は script を持たない頁（tests/test_city.py が見る）なので、CSS の ::details-content で開く。
Chromium 153・WebKit 26.6 で、画面では 57 個の出典のリンクが 0 個見え、印刷の media では 57 個見えることを確かめた（ops の確かめ）。
"""
import re

from fastapi.testclient import TestClient

from todoku import app as app_mod

client = TestClient(app_mod.app)
CSS = (app_mod.WEB_DIR / "style.css").read_text(encoding="utf-8")


def _print_block():
    m = re.search(r"@media print \{\n  \.city-item \{ break-inside: avoid; \}\n(.*?)\n\}\n", CSS, re.S)
    assert m, "T-178 の印刷の段が無い"
    return m.group(0)


def test_print_opens_source_details_and_hides_the_closed_marker():
    """印刷では出典の details の中身を見せ、閉じた印（▶）を出さない。画面の側（@media print の外）には足さない。"""
    block = _print_block()
    assert "details:has(> .city-src)::details-content { content-visibility: visible; display: block; }" in block
    assert "details:has(> .city-src) > summary { list-style: none; }" in block
    assert "details:has(> .city-src) > summary::-webkit-details-marker { display: none; }" in block
    assert CSS.count("::details-content {") == 1


def test_print_keeps_each_city_item_on_one_page():
    """印刷では .city-item を頁の境で割らず、見出しの後で頁を替えない。"""
    block = _print_block()
    assert ".city-item { break-inside: avoid; }" in block and ".city-item h3 { break-after: avoid; }" in block


def test_every_details_on_city_page_is_matched_by_the_print_rule():
    """/city/ の details は全部、summary の直後に ul.city-src を子に持つ（印刷の段の :has(> .city-src) に当たる）。"""
    for code in ("132098", "464040"):  # 町田市（国・都・市の出典）、十島村（見た県の頁もある）
        html = client.get(f"/city/{code}").text
        opens = re.findall(r"<details[^>]*>", html)
        matched = re.findall(r'<details[^>]*><summary>[^<]*</summary><ul class="city-src">', html)
        assert opens and len(opens) == len(matched), code
        assert "<script" not in html  # 印刷のために script は足さない
