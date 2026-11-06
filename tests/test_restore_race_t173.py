"""残した結果のリンクを開き直す間の取り違えの守り（T-173）。

リンクを開くと /api/step で結果を作り直す。その返りを待つ間に見本・出来事・相談を始めた人には、
遅れて返った開き直しの答えを描かない（成功の側は前から守っていた）。失敗の側も同じにし、
後の見本の問いの上に「結果を作れませんでした」が出て画面が送られないようにする。
ブラウザでの確かめは ops の race_t173.py が playwright で行う。ここでは守りが消えていないことだけを見る。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")


def _restore() -> str:
    m = re.search(r"\nasync function restoreFrom\(code\) \{\n(.*?)\n\}\n", JS, re.S)
    assert m
    return m.group(1)


def test_restore_failure_is_dropped_when_another_consult_started():
    body = _restore()
    tail = body.split("const mine = state;", 1)
    assert len(tail) == 2, "開き直しの相談を覚える"
    c = re.search(r"\} catch \(e\) \{\n\s*if \(mine !== state\) return;", tail[1])
    assert c, "結果を作れなかったときも、待つ間に別の相談を始めていれば知らせを出さない"


def test_restore_success_is_still_guarded():
    body = _restore()
    assert re.search(r'await api\("/api/step".*?\n\s*if \(mine !== state\) return;', body, re.S)
