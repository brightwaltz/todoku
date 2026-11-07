"""合言葉なしで相談を送れる置き場（TODOKU_OPEN=1）の画面と、今日の受付が終わった日の画面（T-180b）。

サーバの側（T-180a）は tests/test_daily_budget_t180.py。ここでは画面（web/app.js・index.html）が
- /api/health の open が真なら、合言葉の欄も「合言葉をお持ちの方」の入口も出さない（管理者は #admin で欄を開く）
- /api/consult の 429（x-todoku-closed: 1）と、health の today.closed で、合言葉の欄ではなく断りと見本への案内を出す
- 受付が終わった日は、まとめ（Claude を呼ぶ口）を送らない
作りが消えていないことを見る。画面での動き（390・1280 の写真）は ops の open_t180b.py が playwright で確かめる。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")


def _body(head: str) -> str:
    m = re.search(r"\n" + re.escape(head) + r" \{\n(.*?)\n\}\n", JS, re.S)
    assert m, head
    return m.group(1)


def test_closed_notice_sits_under_the_send_button_hidden_at_first():
    m = re.search(r'<button class="primary" id="send">相談する</button>\s*(?:<!--.*?-->\s*)?<p class="closed" id="closed" hidden[^>]*>(.*?)</p>', HTML, re.S)
    assert m, "断りは送るボタンのすぐ下に、最初は隠して置く"
    inner = m.group(1)
    assert "今日の受付は終わりました。" in inner and 'id="closed-go"' in inner and "見本で試す" in inner
    assert "合言葉" not in inner, "受付が終わった日に、合言葉の要る形には戻さない"


def test_open_site_never_asks_for_the_passphrase():
    assert 'needGate = Boolean(h.passphrase) && !openSite;' in JS
    m = re.search(r"if \(openSite\) \{(.*?)\n  \} else if \(h\.passphrase && !aikotoba && !keptAtLoad\) show\(\"gate-open\"\);", JS, re.S)
    assert m, "open の置き場では、小さな入口（gate-open）を出す枝に入らない"
    assert 'show("gate-open")' not in m.group(1).replace('show("gate-open", false)', "")
    # 管理者だけが #admin で欄を開く
    assert 'location.hash === ADMIN_HASH) openAdminGate();' in m.group(1)
    assert 'const ADMIN_HASH = "#admin";' in JS
    assert re.search(r'addEventListener\("hashchange", \(\) => \{ if \(openSite && location\.hash === ADMIN_HASH\) openAdminGate\(\); \}\)', JS)


def test_closed_response_shows_the_notice_not_the_gate():
    body = _body("async function api(path, body)")
    m = re.search(r'if \(res\.status === 429 && res\.headers\.get\("x-todoku-closed"\) === "1"\) \{(.*?)\n  \}', body, re.S)
    assert m, "429 と印のヘッダで受付終了を見分ける"
    assert "showClosed();" in m.group(1) and "throw Object.assign(new Error(msg), { closed: true });" in m.group(1)
    assert "askGate" not in m.group(1)
    # 受付終了の見分けは、ふつうの失敗（!res.ok）より先
    assert body.index('x-todoku-closed') < body.index("if (!res.ok)")
    closed = _body("function showClosed()")
    assert 'show("gate", false); show("gate-open", false);' in closed and 'show("closed");' in closed


def test_closed_at_load_from_health():
    assert "if (h.today && h.today.closed && !aikotoba) showClosed();" in JS


def test_consult_failure_for_closed_points_to_samples():
    body = _body("async function consultWith(text, call)")
    assert 'if (e.closed) show("reading", false); else show("reading");' in body
    assert 'if (e.closed) focusOn($("closed-go")); else backTo(pressed);' in body
    go = re.search(r'\$\("closed-go"\)\.addEventListener\("click", \(\) => \{(.*?)\n\}\);', JS, re.S)
    assert go and '$("samples").scrollIntoView(' in go.group(1) and "focusOn(first);" in go.group(1)


def test_no_summary_on_a_closed_day():
    assert '} else if (closedToday && !aikotoba) {\n    // 今日の受付が終わった日は、まとめ（Claude を呼ぶ口）も断られるので送らない（T-180b）\n    show("summary", false);\n  } else if (key !== state.summaryKey) summarize(key);' in JS


def test_admin_passphrase_lifts_the_notice():
    m = re.search(r'\$\("gate-ok"\)\.addEventListener\("click", \(\) => \{(.*?)\n\}\);', JS, re.S)
    assert m and 'show("closed", false);' in m.group(1)
