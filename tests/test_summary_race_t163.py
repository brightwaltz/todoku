"""結果の画面のまとめを待つ間に別の相談を始めたら、遅れて返った前の相談のまとめを出さないこと（T-163）。

Claude が書くまとめ（/api/summary）は数秒かかり、その間も「別の相談をする」と見本のボタンは押せる。前は、文の相談の結果から
すぐ見本へ移ると、見本の結果に前の相談（別の人）のまとめが出ていた。画面での動き（/api/summary を遅らせて見本を結果まで進める）は
ops の race_t163.py が playwright で確かめる。ここでは、その作り（送ったときの相談とまとめの鍵を覚え、受け取った後に同じかを見る。
読み取りの consultWith・聞き返しの step と同じ形）が消えていないことだけを見る。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")


def _body(head: str) -> str:
    m = re.search(r"\n" + re.escape(head) + r" \{\n(.*?)\n\}\n", JS, re.S)
    assert m, head
    return m.group(1)


def test_summary_is_dropped_when_the_consult_or_answers_changed():
    body = _body("async function summarize(key)")
    assert body.lstrip().startswith("const mine = state;"), body[:120]
    m = re.search(r"const data = await api\(\"/api/summary\".*?\n(?:\s*//.*\n)?\s*if \(mine !== state \|\| key !== state\.summaryKey\) return;", body, re.S)
    assert m, "受け取った直後に、まだ同じ相談・同じ答えかを見る"
    assert body.index('replaceChildren(') > m.end() and body.index('show("summary");') > m.end()


def test_render_passes_the_key_it_will_store():
    assert "else if (key !== state.summaryKey) summarize(key);\n  state.summaryKey = key;" in JS


def test_sample_summary_keeps_the_same_guard():
    """見本のまとめは前から答えの鍵で守っていた。別の相談を始めたときも出さないよう、相談も見る。"""
    body = _body("async function sampleSummary(key)")
    assert body.lstrip().startswith("const mine = state;"), body[:120]
    m = re.search(r"if \(mine !== state \|\| key !== state\.summaryKey\) return;", body)
    assert m and body.index("state.sampleSummary = data") > m.end()
