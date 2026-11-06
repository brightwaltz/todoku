"""相談の読み取りを待つ間に見本・出来事・別の相談を始めたら、遅れて返った前の答えを描かないこと（T-162）。

本物の Claude の読み取りは数秒かかり、その間も見本と出来事のボタンは押せる。前は、後から押した見本の問いに答えている途中で
前の読み取りが返り、画面が前の相談の読み取ったことと問いに替わっていた。失敗で返ったときは、見本の上に失敗の知らせが出ていた。
画面での動き（/api/consult を遅らせて見本を押す）は ops の race_t162.py が playwright で確かめる。ここでは、その作り
（始めた相談を覚え、受け取った後と失敗した後に、まだ同じ相談かを見る。聞き返しの step と同じ形）が消えていないことだけを見る。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")


def _consult_with() -> str:
    m = re.search(r"\nasync function consultWith\(text, call\) \{\n(.*?)\n\}\n", JS, re.S)
    assert m
    return m.group(1)


def test_remembers_which_consult_it_started():
    body = _consult_with()
    assert re.search(r"state = fresh\(\);\n\s*const mine = state;", body), body[:200]


def test_late_answer_is_dropped_before_drawing():
    body = _consult_with()
    m = re.search(r"const data = await call\(\);\n\s*if \(mine !== state\) return;", body)
    assert m, "受け取った直後に、まだ同じ相談かを見る"
    assert body.index("renderExtracted(") > m.end() and body.index("render(data") > m.end()


def test_late_failure_is_not_shown_over_the_next_consult():
    body = _consult_with()
    m = re.search(r"\} catch \(e\) \{\n\s*if \(mine === state\) \{(.*?)\n    \}\n    return;\n  \}", body, re.S)
    assert m, "失敗の知らせを出す前に、まだ同じ相談かを見る"
    inside = m.group(1)
    for part in ('$("reading").replaceChildren(el("p", { class: "error" }', "say(e.message)", 'show("reading")', "backTo(pressed)"):
        assert part in inside, part


def test_step_keeps_the_same_guard():
    """聞き返し（step）は前から同じ形で守っている。片方だけ消えないように。"""
    m = re.search(r"\nasync function step\(change = \{\}, stop = false\) \{\n(.*?)\n\}\n", JS, re.S)
    assert m
    assert "const mine = state;" in m.group(1) and "if (mine !== state) return;" in m.group(1)
