"""T-150：結果の画面で、制度の札を開くと見出しが h2 から h4 に飛んでいた（申請メモの項目「申請先」など）。

札の中の見出しは h3（判定の理由・申請先・出典）。申請メモの題（.memo-title）は p だったので、メモの中の h4 が
群の見出し（h2）のすぐ後に来ていた。題を h3 にして、メモの h4 がその下に入るようにした。見た目は前の p のまま
（.prog h3 の 16px・色を .memo-sheet .memo-title で打ち消す）。ブラウザでの見出しの順は $OPS/tools/a11y_t150.py で測った。
ここではファイルの文を読むだけ。
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
CSS = (WEB / "style.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")


def memo_view():
    m = re.search(r"^function memoView\(m\) \{(.*?)^\}", JS, re.M | re.S)
    assert m, "memoView"
    return m.group(1)


def test_memo_title_is_a_heading_above_its_items():
    body = memo_view()
    # T-169：題は頭書きと名前に分けて渡す（中身は m.title のまま）
    assert 'el("h3", { class: "memo-title" }, ...memoTitleParts(m.title))' in body
    assert body.index('class: "memo-title"') < body.index('el("h4"')
    # メモの中の項目は h4 のまま（題の h3 の下）
    assert body.count('el("h4"') == 4


def test_memo_title_keeps_the_look_of_the_old_paragraph():
    rule = re.search(r"^\.memo-sheet \.memo-title \{([^}]*)\}", CSS, re.M)
    assert rule
    for decl in ("font-weight: 700", "margin: 8px 0 2px", "font-size: 1em", "color: inherit"):
        assert decl in rule.group(1), decl
