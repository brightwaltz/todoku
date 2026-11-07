"""質問だけで始めた道の「まだ確かめていないこと」の言い方（T-205）。

main の T-204（/api/begin）で、文も出来事もなく質問だけで始められるようになった。その道の結果の画面で
「まだ確かめていないこと」の注が「相談の文からは、はっきり読み取れなかったこと・書かれていなかったことです。」、
ボタンが「読み取れなかったことにも答える（n 件）」のままで、何も書いていない人に合わなかった。
画面だけの直し：その道（state.event が真で state.text が空）のときだけ、注の 1 文目とボタンの名前を替える。
文の道・出来事の道・見本の言い方は変えない。判定・問い・読み取りは変えない。
"""

import os
import re

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from todoku import app as app_mod  # noqa: E402

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")


def lead_note() -> str:
    m = re.search(r'<div class="card ask" id="later" hidden>.*?<p class="note lead-note">(.*?)</p>', HTML, re.S)
    assert m, "#later の注が見つからない"
    return m.group(1)


def render_later() -> str:
    return re.search(r"\nfunction renderLater\(.*?\n\}\n", JS, re.S).group(0)


def test_blank_lead_replaces_only_the_first_sentence():
    lead = lead_note()
    # 文の道の注はそのまま（1 文目が「相談の文から」で、句点で切れる）
    assert lead.startswith("相談の文からは、はっきり読み取れなかったこと・書かれていなかったことです。")
    # app.js の置き換えと同じ式で、質問だけの道の注を作る
    pat = re.search(r'LATER_LEAD\.replace\(/(.*?)/, "(.*?)"\)', JS)
    assert pat, "BLANK_LEAD の置き換えが見つからない"
    assert pat.group(1) == "^[^。]*。"
    blank = re.sub(pat.group(1), pat.group(2), lead, count=1)
    assert blank.startswith("まだ答えていない質問です。答えると、規則のプログラムが判定し直します")
    assert "相談の文" not in blank and "読み取" not in blank
    # 2 文目以降（AI は使いません）は同じ
    assert blank.split("。", 1)[1] == lead.split("。", 1)[1]


def test_render_later_switches_only_on_the_blank_path():
    src = render_later()
    assert "const blank = state.event && !state.text;" in src
    assert '.lead-note").textContent = blank ? BLANK_LEAD : LATER_LEAD;' in src
    assert 'keepTail(blank ? "ほかの質問にも答える" : "読み取れなかったことにも答える"' in src
    # 出来事の道は相談の文に代わる文を持つ（state.text が空でない）ので、質問だけの道と取り違えない
    assert re.search(r"await consultWith\(text, \(\) => api\(\"/api/event\"", JS)
    # 見本・結果を残したリンクから戻した結果は state.event が偽のまま（fresh の既定）
    fresh = re.search(r"\nfunction fresh\(\) \{\n(.*?)\n\}\n", JS, re.S).group(1)
    assert 'text: ""' in fresh and "event: false" in fresh
