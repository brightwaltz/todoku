"""T-169：申請メモの題が、390px で「申請メモ：障害者控除対象者認定 / 書」のように 1〜2 字だけ次の行へ落ちていた
（Chrome でも WebKit でも。$OPS/tools/shot_t147.py の折れ残り）。

題を「申請メモ：」と名前に分け、名前を 1 かたまり（inline-block）にして、行に入らなければ名前ごと次の行へ送る。
中身の文（サーバの memo.py が作る題）は変えない。ここではファイルの文を読むだけ。ブラウザでの折れ方は shot_t147.py で測った。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
MEMO_PY = (ROOT / "src" / "todoku" / "memo.py").read_text(encoding="utf-8")
HEAD = "申請メモ："


def test_server_title_starts_with_the_head_the_screen_splits_on():
    # 頭書きがずれると、画面は分けずに前と同じ 1 つの文で出す（壊れはしないが直らない）
    assert f'"title": f"{HEAD}{{' in MEMO_PY
    assert f'const head = "{HEAD}";' in JS


def test_name_is_its_own_span_inside_the_heading():
    fn = re.search(r"^function memoTitleParts\(title\) \{(.*?)^\}", JS, re.M | re.S)
    assert fn, "memoTitleParts"
    body = fn.group(1)
    assert 'el("span", { class: "memo-name" }, title.slice(head.length))' in body
    # 頭書きで始まらない題は、そのまま 1 つの文で出す
    assert ": [title]" in body


def test_name_moves_to_the_next_line_as_one_piece():
    rule = re.search(r"^\.memo-sheet \.memo-name \{([^}]*)\}", CSS, re.M)
    assert rule
    for decl in ("display: inline-block", "text-wrap: balance"):
        assert decl in rule.group(1), decl
