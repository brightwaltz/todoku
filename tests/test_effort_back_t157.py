"""押した数の図（別の頁、T-133）で、ブラウザの戻るがサイトの外へ出ず結果へ戻ること（T-157）。

画面での動き（戻る・進む・頁の「戻る」・読み直し）は ops の back_t157.py が playwright で確かめる。ここでは、その作りが
消えていないこと（開くときに同じ URL の履歴を 1 つ積む・popstate で閉じる・頁の「戻る」は積んだ 1 つを戻す）だけを見る。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")


def _handler(event_target: str) -> str:
    """$("<id>").addEventListener("click", () => { … }); の中身。"""
    m = re.search(r'\$\("' + re.escape(event_target) + r'"\)\.addEventListener\("click", \(\) => \{(.*?)\n\}\);', JS, re.S)
    assert m, event_target
    return m.group(1)


def test_opening_effort_page_pushes_same_url():
    body = _handler("effort-open")
    assert "history.pushState(" in body and "location.href" in body, body
    assert "openEffort()" in body


def test_page_back_button_pops_what_it_pushed():
    body = _handler("effort-back")
    assert "history.back()" in body, body
    assert "closeEffort()" in body, body  # 積んだ履歴が無いとき（読み直した後など）は、その場で閉じる


def test_popstate_closes_effort_page():
    m = re.search(r'window\.addEventListener\("popstate", \(ev\) => \{(.*?)\n\}\);', JS, re.S)
    assert m
    assert "closeEffort()" in m.group(1) and "openEffort()" in m.group(1)


def test_no_other_history_entries():
    """履歴を積むのは図の頁だけ。ほかは replaceState のまま（残した結果の # を書き換えても、戻るの回数は増えない）。"""
    assert JS.count("history.pushState(") == 1
