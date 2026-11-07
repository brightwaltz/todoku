"""T-185：今日の受付が終わった日の断り（#closed の「今日の受付は終わりました。」）が、ブラウザの文字を特大（24）にした
320・360・390 で語の途中で折れていた（「今日の受付は終わ / りました。」「今日の受付は終わりまし / た。」）。
問いの見出し（#ask-text）と同じく、auto-phrase のあるブラウザ（Chrome）でだけ文節で折る。auto-phrase の無い Safari で
balance にすると語の途中で折れるので、@supports の中に置く。ふつうの文字の Chrome 320・360・390・1280 と WebKit では
前も後も 1 行のままであることを $OPS/tools/open_t180b.py（T180_SIZES・T180_FONT）と差し込みの測りで確かめた。ここではファイルの文を読むだけ。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def _rule():
    rule = re.search(r"^@supports \(word-break: auto-phrase\) \{ \.closed span \{([^}]*)\} \}", CSS, re.M)
    assert rule, "断りの文の折り方は @supports (word-break: auto-phrase) の中に置く"
    return rule.group(1)


def test_closed_notice_breaks_by_phrase_in_chrome():
    for decl in ("word-break: auto-phrase", "text-wrap: balance", "overflow-wrap: anywhere"):
        assert decl in _rule(), decl


def test_closed_notice_is_not_balanced_outside_supports():
    # Safari（auto-phrase なし）で balance にすると語の途中で折れるので、@supports の外には書かない
    outside = re.sub(r"^@supports \(word-break: auto-phrase\) \{[^\n]*\}\s*$", "", CSS, flags=re.M)
    for m in re.finditer(r"^\.closed span \{([^}]*)\}", outside, re.M):
        assert "balance" not in m.group(1)
