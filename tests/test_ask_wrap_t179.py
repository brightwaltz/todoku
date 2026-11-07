"""T-179：問いの見出し（#ask-text）の最後の行が 1〜2 字だけになる折れ残りを減らす。

前は 430・521・600・1280 で「お父さまの年齢はどのくらいです / か？」「書類で確かめて選んでくだ / さい」と折れ、
見本 4 件の問いの見出し 84 個（7 幅）中 11 個が折れ残りだった（Chrome・WebKit とも。$OPS/tools/wrap_t179.py）。
ここでは CSS の文を読むだけ（実際の折れ目はブラウザで測った）。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def test_ask_heading_wraps_pretty_everywhere():
    m = re.search(r"^#ask-text \{([^}]*)\}", CSS, re.M)
    assert m and "text-wrap: pretty" in m.group(1)


def test_ask_heading_balances_at_phrases_only_where_supported():
    # Safari には auto-phrase が無く、balance だけだと語の途中で釣り合わせるので、@supports の中に置く
    m = re.search(r"^@supports \(word-break: auto-phrase\) \{ #ask-text \{([^}]*)\} \}", CSS, re.M)
    assert m, "@supports の中の #ask-text"
    body = m.group(1)
    assert "word-break: auto-phrase" in body and "text-wrap: balance" in body
    # 1 文節が 1 行に入らないとき（文字を大きくした 320 など）も枠の外へはみ出さない
    assert "overflow-wrap: anywhere" in body
