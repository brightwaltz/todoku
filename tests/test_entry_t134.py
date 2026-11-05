"""T-134a：入口をさらに軽く。最初は書く欄と出来事の札だけで、どなた・年齢・お住まいを選ぶ欄は出来事を 1 つ押した後に開く。
直しは web/ だけ（判定・問い・読み取りは変えない）。見本 3 本（退院した母・要介護4の祖母・横浜市の父）の問いの順と数が
変わらないことを、tests/fixtures/t134_sample_questions.json（lane-c の元 449a8bf で /api/sample → /api/step を歩いて取った）と見比べる。
規則が変わって問いが変わったときは、この表を作り直す（UPDATE_T134=1 で書き直す）。
"""
import json
import os
import re
from pathlib import Path

from test_e2e_demo import client, pick_demo, walk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "t134_sample_questions.json"
LABELS = ("退院した母", "要介護4の祖母", "横浜市の父（認知症）")


def sample_questions() -> dict:
    """見本ごとに、デモの答え方（pick_demo）で出る問いの順（事実の名前。お住まいの問いは place）を返す。"""
    samples = {s["label"]: s["id"] for s in client.get("/api/samples").json()}
    seen = {}
    for label in LABELS:
        start = client.post("/api/sample", json={"id": samples[label]}).json()
        asked = []

        def record(q, asked=asked):
            asked.append("place" if q.get("place") else q["fact"])
            return pick_demo(q)

        walk(start, record)
        seen[label] = asked
    return seen


def test_three_samples_ask_the_same_questions_in_the_same_order():
    seen = sample_questions()
    if os.environ.get("UPDATE_T134") == "1":
        FIXTURE.write_text(json.dumps(seen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    want = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for label in LABELS:
        assert seen[label] == want[label], f"{label} の問いの順・数が変わった（規則を直したなら UPDATE_T134=1 で表を作り直す）"
    # 見本は id だけを送る。出来事の後に開く選ぶ欄の中身（どなた・お住まい）を混ぜない
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'api("/api/sample", { id: s.id })' in js


def test_pick_fields_open_only_after_an_event():
    html = client.get("/").text
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 最初は出さない。出来事の枠の中で、「選んだ出来事で始める」の上に置く
    assert '<details class="pick-more" id="pick-more" hidden>' in html
    events = html[html.index('<div id="events"'):]
    assert events.index('id="pick-more"') < events.index('id="event-start"')
    toggle = re.search(r"^function toggleEvent\(.*?^\}", js, re.S | re.M).group(0)
    assert 'show("pick-more"); $("pick-more").open = true;' in toggle and "redraft();" in toggle
    # どなた・お住まいを選んでも、出来事の文を消さない（下書きは選んだ項目の文 ＋ 出来事の文）
    draft = re.search(r"^function buildDraft\(.*?^\}", js, re.S | re.M).group(0)
    assert "eventPicked.has(e.id)" in draft and 'return parts.join("") + events;' in draft
    # /city/ の頁から ?pref= で来たときは、隠してある欄を出してから開く
    from_map = re.search(r"^function pickFromMap\(.*?^\}", js, re.S | re.M).group(0)
    assert from_map.index('show("pick-more")') < from_map.index('$("pick-more").open = true;')


def test_japan_map_moved_off_the_entrance_to_its_own_page():
    """T-134b：47 都道府県のタイル地図は入口（「このサイトについて」）から外し、別の頁へ。結果の節の最後に 1 行のリンクだけ。"""
    html = client.get("/").text
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="japan"' not in html and "japan-box" not in html and 'fetch("/japan-map")' not in js
    # 結果の節の最後の 1 行。文は前に入口にあった見出しと同じで、言葉は増やさない
    result = re.search(r'<section id="result" hidden>(.*?)</section>\s*</main>', html, re.S).group(1)
    last = result.strip().splitlines()[-1].strip()
    assert last == '<p class="map-link"><a href="/static/japan.html">全国どこでも使えます</a></p>'
    assert result.count("japan.html") == 1
    # 別の頁はサーバの /static/ が配る。CSP（script-src 'self'）に合うよう、スクリプトはファイルで読む
    page = client.get("/static/japan.html")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert '<script src="/static/japan.js"></script>' in page.text and "<script>" not in page.text
    assert '<h2 id="japan-title">全国どこでも使えます</h2>' in page.text
    assert client.get("/static/japan.js").status_code == 200
    # タイルは /?pref=NN へ戻り、入口の pickFromMap が選ぶ欄を開く（T-134a）
    assert 'href="/?pref=14"' in client.get("/japan-map").text
    assert "pickFromMap(asked, false);" in js
