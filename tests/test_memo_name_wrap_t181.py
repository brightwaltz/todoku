"""T-181：ブラウザの文字を特大（24）にした 320px で、申請メモの名前（.memo-sheet .memo-name）が札の枠で切れていた
（「障害者控除対象者認定書」の右端 346・枠 285。$OPS/tools/shot_t147.py の T147_FONT=24 で 7 件）。

名前は T-169 で 1 かたまり（inline-block）にし、auto-phrase で文節の中では折らない。inline-block の幅は中身の最小の幅より
縮まないので、1 文節が 1 行に入らないと枠を越える。T-164 の .todo-list a と同じく overflow-wrap: anywhere を足すと、
最小の幅が字 1 つ分になり、入らないときだけ文節の中で折る。ふつうの文字の 320・360・390・1280（Chrome）と 320・390（WebKit）では、
名前の折れ目が前と 1 つも変わらないことを $OPS/tools/memo_name_t181.py で確かめた。ここではファイルの文を読むだけ。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def _rule():
    rule = re.search(r"^\.memo-sheet \.memo-name \{([^}]*)\}", CSS, re.M)
    assert rule
    return rule.group(1)


def test_name_can_break_inside_a_phrase_when_it_does_not_fit():
    # break-word は最小の幅に効かないので、inline-block の中では枠を越えたままになる
    assert "overflow-wrap: anywhere" in _rule()
    assert "break-word" not in _rule()


def test_name_still_moves_as_one_piece_first():
    # 折れ目を前と変えない：まず名前ごと次の行へ送り、文節で折る（T-169）
    for decl in ("display: inline-block", "word-break: auto-phrase", "text-wrap: balance"):
        assert decl in _rule(), decl
