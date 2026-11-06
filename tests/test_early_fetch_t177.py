"""T-177：最初の画面のデータを app.js の読み込みを待たずに取り始める（web/early.js）。

遅い回線（Fast 3G 相当）では、app.js を待ってから取ると Google Fonts のファイルと回線を取り合い、
都道府県の選択肢が入るまで 4.9 秒かかっていた。early.js が先に取り、app.js は earlyFetch() で受け取る。
二重に取らないこと（WebKit で preload が二重になったので使わない）を、URL の組が同じであることで確かめる。
"""

import os
import re

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402

client = TestClient(app_mod.app)
EARLY = (app_mod.WEB_DIR / "early.js").read_text(encoding="utf-8")
APP = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")


def _early_urls() -> set[str]:
    m = re.search(r"for \(const url of \[([^\]]*)\]\)", EARLY)
    assert m, "early.js の URL の並びが見つからない"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_index_loads_early_js_async_in_head_before_app_js():
    html = client.get("/").text
    head = html[: html.index("</head>")]
    assert re.search(r'<script src="/static/early\.js" async[^>]*></script>', head)
    assert html.index('src="/static/early.js"') < html.index('src="/static/app.js"')
    r = client.get("/static/early.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


def test_app_js_takes_every_early_fetch_and_does_not_fetch_them_again():
    taken = set(re.findall(r'earlyFetch\("([^"]+)"\)', APP))
    assert taken == _early_urls()
    for url in taken:
        assert f'fetch("{url}")' not in APP.replace(f'earlyFetch("{url}")', ""), f"{url} を app.js が二重に取る"


def test_late_early_js_does_not_fetch_twice():
    # early.js は todokuEarly が既にあれば何もしない。app.js は無ければ {} を置いてから取る
    assert "if (!window.todokuEarly)" in EARLY
    assert "window.todokuEarly || (window.todokuEarly = {})" in APP


def test_every_early_url_answers():
    for url in _early_urls():
        assert client.get(url).status_code == 200, url
