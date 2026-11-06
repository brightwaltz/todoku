"""結果の画面の地域のリンク（「地域の支えの一覧を見る」→ /city/）は新しいタブで開く（T-174）。

同じタブで移ると、戻る・進むのキャッシュが効かない端末では、戻ったときに残した結果のリンク（#r=）から
結果を作り直すことになり、まとめの文・押した数の図の入口・開いた札・画面の位置が変わっていた。
入口の一覧のリンク（city-link）は前から新しいタブで開くので、それにそろえる。
ブラウザでの確かめは ops の back_t174.py が playwright で行う。ここではリンクの形だけを見る。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_region_link_opens_in_new_tab():
    m = re.search(r'el\("a", \{(.*)\}, REGION_LINK\)\);', JS)  # 1 行で書いてある（href の ${} を含む）
    assert m, "地域の 1 行のリンクがある"
    attrs = m.group(1)
    assert "href: `/city/${rg.code}`" in attrs
    assert 'target: "_blank"' in attrs, "新しいタブで開き、結果の画面を残す"
    assert 'rel: "noopener"' in attrs, "開いた頁から元の頁を触らせない"


def test_entry_city_link_still_opens_in_new_tab():
    m = re.search(r'<a id="city-link"[^>]*>', HTML)
    assert m and 'target="_blank"' in m.group(0) and 'rel="noopener"' in m.group(0)
