"""T-203：書くのも難しく、当てはまる絵もない人の入口（11/7 柴田「絵の中に当てはまる場面がなく、文章で書くことも難しい場合が
考慮・配慮されていない」）。「選んだ出来事で始める」は絵を選ぶまで押せないので、いつでも押せる「当てはまる絵がないときは、
質問に答えて始める」を足し、/api/begin が何も置かずに最初の問いを返す。Claude を呼ばず、合言葉も要らない。
判定・問い・読み取りは変えない（/api/event の 0 件は今までどおり 404）。
"""
import os
import re
from pathlib import Path

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from test_e2e_demo import pick_demo, walk  # noqa: E402
from todoku import app as app_mod  # noqa: E402
from todoku.app import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def no_claude(*args, **kwargs):
    raise AssertionError("質問だけの入口で Claude を呼んだ")


def test_begin_asks_the_place_first_without_claude_or_passphrase(monkeypatch):
    monkeypatch.setattr(app_mod.CLAUDE, "extract", no_claude)
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")  # 合言葉なしで動く
    res = client.post("/api/begin", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["blank"] is True and data["text"] == "" and "event" not in data
    assert data["facts"] == {} and data["extracted"] == []
    # 住所が分からないので、最初は 3 問の枠の外のお住まいの問い（P1）
    assert data["question"]["place"]


def test_begin_with_a_city_skips_to_the_rules_questions(monkeypatch):
    monkeypatch.setattr(app_mod.CLAUDE, "extract", no_claude)
    data = client.post("/api/begin", json={"municipality": "141003"}).json()  # 横浜市
    assert data["place"]["code"] == "141003"
    assert not data["question"].get("place")


def test_begin_walks_to_results(monkeypatch):
    monkeypatch.setattr(app_mod.CLAUDE, "extract", no_claude)
    start = client.post("/api/begin", json={}).json()
    assert start["question"]["place"]
    # お住まいは web/app.js の answerPlace と同じく数えずに答える（walk は答えをどれも 1 問に数えるので、ここだけ自分で送る）
    after = client.post("/api/step", json={"facts": {}, "suggestions": start["suggestions"], "subject": start["subject"],
                                            "asked": 0, "municipality": "132098"}).json()
    assert after["question"] and not after["question"].get("place")
    # /api/step は事実を返さない（画面が持つ）ので、画面と同じく手元の事実を添えて walk に渡す
    state, end = walk({**after, "facts": {}, "suggestions": start["suggestions"], "subject": start["subject"]}, pick_demo)
    assert end["question"] is None and end["results"]
    assert state["municipality"] == "132098" and len(state["facts"]) == after["max_questions"]  # お住まい＋3 問


def test_begin_rejects_bad_input_and_event_keeps_its_404():
    assert client.post("/api/begin", json={"municipality": "1234567"}).status_code == 422
    assert client.post("/api/event", json={"ids": []}).status_code == 404  # 出来事 0 件の扱いは変えない


def test_entrance_has_the_blank_start_under_event_start():
    html = client.get("/").text
    events = html[html.index('<div id="events"'):]
    button = '<button type="button" class="link" id="blank-start">当てはまる絵がないときは、質問に答えて始める</button>'
    assert button in events
    assert events.index('id="event-start"') < events.index('id="blank-start"') < events.index("</div>\n    <svg id=\"art-defs\"")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'consultWith("", () => api("/api/begin", { municipality: pick.city }))' in js
    assert '$("blank-start").addEventListener("click", openBlank);' in js
    # 文が無いので「あなた」の吹き出しを出さず、Claude のまとめ（合言葉の要る口）も送らない
    extracted = re.search(r"^function renderExtracted\(.*?^\}", js, re.S | re.M).group(0)
    assert 'show("said", !data.sample && !data.blank);' in extracted
    assert "state.event = Boolean(data.event || data.blank);" in js  # まとめを送らない条件は出来事の道と同じ（test_events）
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert "#blank-start {" in css
