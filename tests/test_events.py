"""入口の出来事のボタン（T-071）。押すと下書きの文と最初の問いが出て、文字を打たず合言葉なしで結果まで行ける。"""

import os

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku import events as events_mod  # noqa: E402
from todoku.app import RULES, app  # noqa: E402
from todoku.claude import check_extraction  # noqa: E402

client = TestClient(app)


def test_events_are_few_and_their_facts_are_in_the_text():
    listed = client.get("/api/events").json()
    assert 1 <= len(listed) <= 6
    assert all(set(e) == {"id", "label", "text"} for e in listed)
    assert len({e["id"] for e in listed}) == len(listed)
    # 先生の挙げた出来事（11/4）は入っている
    labels = {e["label"] for e in listed}
    assert {"退院した", "物忘れが増えた", "一人暮らしになった", "転びやすくなった", "仕事と介護が重なった"} <= labels
    for e in events_mod.EVENTS:
        ex = check_extraction(RULES, e["text"], events_mod.reading(e))
        # 置いた事実はどれも検査で落ちず、確信も根拠も表のとおり残る（根拠の言葉が文にない・値が違うなら、ここで分かる）
        assert not ex.dropped, e["id"]
        assert [(f.fact, f.value, f.confidence, f.evidence) for f in ex.facts] == list(e["facts"]), e["id"]


def test_event_gives_draft_and_first_question_without_claude(monkeypatch):
    def no_claude(*args, **kwargs):
        raise AssertionError("出来事のボタンで Claude を呼んだ")

    monkeypatch.setattr(app_mod.CLAUDE, "extract", no_claude)
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")  # 合言葉なしで動く
    res = client.post("/api/event", json={"id": "alone"})
    assert res.status_code == 200
    data = res.json()
    assert data["event"] is True and data["engine"] == "event"
    assert data["text"] == "一人暮らしになりました。"
    assert data["facts"] == {"household": "alone"}
    assert data["extracted"][0]["evidence"] == "一人暮らし"
    assert data["question"] is not None and data["question"]["options"]
    assert data["results"]  # 見立ての帯に置く札がある


def test_unsure_event_fact_is_asked_not_assumed():
    # 「働きながら」は雇われているかまで言っていない。判定には使わず、確かめる読み取りにする
    data = client.post("/api/event", json={"id": "work"}).json()
    assert "caregiver_employee" not in data["facts"]
    assert data["suggestions"] == {"caregiver_employee": True}


def test_event_with_place_and_unknown_id():
    data = client.post("/api/event", json={"id": "discharged", "municipality": "132098"}).json()
    assert data["facts"]["residence"] == "home"
    assert data["place"] and data["place"]["code"] == "132098"
    assert client.post("/api/event", json={"id": "nope"}).status_code == 404


def test_event_reaches_result_by_answering_only(monkeypatch):
    """文字を打たずに、出来事のボタンと選択肢だけで結果（問いが None）まで行ける。"""
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    for e in events_mod.EVENTS:
        data = client.post("/api/event", json={"id": e["id"]}).json()
        facts, suggestions, asked = data["facts"], data["suggestions"], 0
        while data["question"] is not None:
            q = data["question"]
            assert asked <= app_mod.MAX_QUESTIONS, e["id"]
            if q.get("keep"):
                facts[q["fact"]] = q["keep"]["value"]
            else:
                facts[q["fact"]] = q["options"][0]["value"]
            asked += 1
            res = client.post("/api/step", json={"facts": facts, "suggestions": suggestions, "asked": asked})
            assert res.status_code == 200
            data = res.json()
        assert data["results"], e["id"]


def test_page_has_event_buttons_slot():
    html = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert 'id="events"' in html and 'id="event-list"' in html
    assert '"/api/events"' in js and '"/api/event"' in js
    # 出来事の道は合言葉なしで結果まで来るので、合言葉が要る置き場ではまとめ（/api/summary）を送らない
    assert "state.event && needGate && !aikotoba" in js
