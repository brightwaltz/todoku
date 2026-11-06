"""T-146：T-029 の後に入った押す所も 44px 以上にする（高齢の方がスマホで押し違えないよう。WCAG 2.5.5・Apple HIG の数）。

T-029 の test_tap_targets_are_at_least_44px は、そのときにあった部品（.chip・.link・details summary など）だけを見る。
その後に入った部品は、それぞれの規則が summary の余白や高さを上書きしていて、ブラウザで測ると 32〜38px だった
（$OPS/tools/a11y_t146.py・shot_t146.py、390・1280）。ここでは CSS の文を読むだけ（実際の大きさはブラウザで測った）。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def rule(selector):
    m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", CSS, re.M)
    assert m, selector
    return m.group(1)


def min_height(selector):
    h = re.search(r"min-height: (\d+)px", rule(selector))
    return int(h.group(1)) if h else 0


def test_todo_names_are_44px_and_centered():
    # 「いま先にすること」の名前（押すとカードが開く）。T-121a では 32px だった
    assert min_height(".todo-list a") >= 44
    assert "align-items: center" in rule(".todo-list a")


def test_folded_section_headings_are_44px():
    # 「制度の地図」「確かめると使えるかもしれない」「有料・民間の支え」などの畳んだ欄の見出し。
    # 見出しの文字 17px の行（27px）＋ 上下 9px で 45px。T-121a で 4px にしたので 35px だった
    assert "padding: 9px 0" in rule(".fold > summary")


def test_memo_print_button_is_44px():
    # 申請メモの「このメモだけ印刷する」。ブラウザの既定のボタンのままで 36〜38px だった
    assert min_height(".memo > button") >= 44


def test_place_tabs_are_44px_both_ways():
    # 入口の家の場所の札（T-139）。40px で、1 文字の「外」は幅も 41px だった
    assert min_height(".place-tab") >= 44
    assert "min-width: 44px" in rule(".place-tab")
