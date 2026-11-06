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
    assert len(listed) == len(events_mod.EVENTS) == events_mod.MAX_EVENTS  # 小項目は全部出す（T-139。上限は小項目の数）
    assert all(set(e) == {"id", "label", "text", "place", "place_label"} for e in listed)  # 裏の分類（icf）は渡さない
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
    assert "(state.event || state.restored) && needGate && !aikotoba" in js


def test_picked_events_are_combined_without_claude(monkeypatch):
    # 絵で選んだいくつかの出来事（T-124）。文は札の順につなぎ、同じ事実（residence）は 1 つにし、Claude は呼ばない
    def no_claude(*args, **kwargs):
        raise AssertionError("出来事の絵で Claude を呼んだ")

    monkeypatch.setattr(app_mod.CLAUDE, "extract", no_claude)
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    data = client.post("/api/event", json={"ids": ["discharged", "falls", "alone", "falls"]}).json()
    assert data["text"] == "病院から退院して、家に戻りました。家の中で転びやすくなりました。一人暮らしになりました。"
    assert data["facts"] == {"residence": "home", "household": "alone"}
    assert [x["evidence"] for x in data["extracted"]] == ["家に戻りました", "一人暮らし"]
    assert data["question"] is not None and data["results"]
    # 1 件だけなら、id で送ったときと同じ答え
    one = client.post("/api/event", json={"ids": ["alone"]}).json()
    assert one == client.post("/api/event", json={"id": "alone"}).json()
    # 知らない出来事が 1 つでも混じれば断る。何も選んでいなければ断る。数の上限を超えれば 422
    assert client.post("/api/event", json={"ids": ["alone", "nope"]}).status_code == 404
    assert client.post("/api/event", json={"ids": []}).status_code == 404
    assert client.post("/api/event", json={"ids": ["alone"] * (events_mod.MAX_EVENTS + 1)}).status_code == 422


def test_every_event_has_a_picture_and_a_place_in_the_scene():
    # 札の絵と家の絵は同じ symbol（art-<id>）を使う（T-124）。絵には文字のラベル（札の名前）が付き、
    # 札はボタン（キーボードで選べる・aria-pressed）で、家の絵は読み上げから外し、選んだ名前は文字の行で知らせる
    import re

    html = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
    css = (app_mod.WEB_DIR / "style.css").read_text(encoding="utf-8")
    ids = [e["id"] for e in client.get("/api/events").json()]
    for i in ids:
        assert f'<symbol id="art-{i}"' in html, i
    # 家の絵には、出来事の全部を 1 回ずつ置く（T-139b で 16 個に戻した）
    placed = re.findall(r'data-ev="(\w+)"><use href="#art-(\w+)"', html)
    assert all(a == b for a, b in placed), placed
    assert sorted(a for a, _ in placed) == sorted(ids)
    assert not re.search(r'<(image|img)\b', html.split('id="events"')[1].split("</svg>\n    <svg")[0])  # 外の画像は使わない
    assert '<figure class="scene" aria-hidden="true">' in html and 'aria-live="polite"' in html
    assert 'el("button", { type: "button", class: "ev-card", "aria-pressed": "false"' in js
    assert "prefers-reduced-motion: reduce) { .scene-item { transition: none" in css
    # 相談の欄が最初で、絵はその下（11/5 21 時半の柴田の気づき）
    assert html.index('id="text"') < html.index('id="send"') < html.index('id="events"') < html.index('id="samples"')


def _drawn_bounds(symbol_markup):
    """symbol の中の図形（rect・circle・ellipse・line・path の絶対座標）が描く範囲。viewBox 0 0 100 100 の座標"""
    import re

    xs, ys = [], []
    for tag, attrs in re.findall(r"<(rect|circle|ellipse|line|path)\b([^>]*)/>", symbol_markup):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        f = {k: float(v) for k, v in a.items() if re.fullmatch(r"[\d.]+", v)}
        if tag == "rect":
            xs += [f["x"], f["x"] + f["width"]]; ys += [f["y"], f["y"] + f["height"]]
        elif tag == "circle":
            xs += [f["cx"] - f["r"], f["cx"] + f["r"]]; ys += [f["cy"] - f["r"], f["cy"] + f["r"]]
        elif tag == "ellipse":
            xs += [f["cx"] - f["rx"], f["cx"] + f["rx"]]; ys += [f["cy"] - f["ry"], f["cy"] + f["ry"]]
        elif tag == "line":
            xs += [f["x1"], f["x2"]]; ys += [f["y1"], f["y2"]]
        else:  # path は大文字（絶対座標）の命令の点だけを見る。小文字の短い線は範囲をほとんど変えない
            for cmd, nums in re.findall(r"([MLQ])\s*([\d.\s]+)", a["d"]):
                v = [float(n) for n in nums.split()]
                xs += v[0::2]; ys += v[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def test_scene_items_fit_in_the_house_without_overlapping():
    # 家の絵は場所 7 つの区画に分かれ（T-139b）、出来事の絵はどれも自分の場所の区画に収まり、床に置く物は区画の床
    # （区画の下の辺）に立ち、描いた範囲が互いに重ならない（T-124b）。壁に掛ける物（暦と時計・耳）だけは床に立たない。
    # ブラウザでの写真と動きの検査は $OPS/tools/shot_t139b.py
    import re

    html = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")
    symbols = dict(re.findall(r'<symbol id="art-(\w+)" viewBox="0 0 100 100">(.*?)</symbol>', html))
    rooms = {p: tuple(float(v) for v in xywh) for p, *xywh in re.findall(
        r'<rect class="room" data-place="(\w+)" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/>', html)}
    assert sorted(rooms) == sorted(p for p, _ in events_mod.PLACES)
    place_of = {e["id"]: e["place"] for e in client.get("/api/events").json()}
    boxes = {}
    for ev, x, y, w, h in re.findall(
            r'data-ev="(\w+)"><use href="#art-\w+" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', html):
        x, y, k = float(x), float(y), float(w) / 100
        assert float(w) == float(h), ev
        x0, y0, x1, y1 = _drawn_bounds(symbols[ev])
        boxes[ev] = (x + x0 * k, y + y0 * k, x + x1 * k, y + y1 * k)
    assert set(boxes) == set(place_of)  # 全部を置く
    for ev, (x0, y0, x1, y1) in boxes.items():
        rx, ry, rw, rh = rooms[place_of[ev]]
        assert rx <= x0 and x1 <= rx + rw and ry <= y0 and y1 <= ry + rh + 1, (ev, place_of[ev], boxes[ev])
        if ev not in ("forgetful", "hearing"):
            assert ry + rh - 4 <= y1, (ev, y1)  # 床に立つ
    items = list(boxes.items())
    for i, (a, p) in enumerate(items):
        for b, q in items[i + 1:]:
            ox = min(p[2], q[2]) - max(p[0], q[0])
            oy = min(p[3], q[3]) - max(p[1], q[1])
            assert not (ox > 0 and oy > 0), (a, b, round(ox, 1), round(oy, 1))


def test_events_are_grouped_by_the_seven_places_of_the_house():
    # 大項目は家の場所 7 つ、小項目は場所ごとに 2〜4 個（T-139）。並びは場所の順。画面に出すのは場所と小項目の名前だけで、
    # 裏の分類（ICF を下敷き）は全部に付ける。事実は facts.yaml にあるものだけで、値は選択肢にある
    import yaml

    facts = yaml.safe_load((app_mod.WEB_DIR.parent / "rules" / "facts.yaml").read_text(encoding="utf-8"))
    facts = facts.get("facts", facts)
    assert [p for p, _ in events_mod.PLACES] == ["bedroom", "entrance", "bath", "kitchen", "living", "outside", "family"]
    assert [l for _, l in events_mod.PLACES] == ["寝室", "階段と玄関", "浴室とトイレ", "台所", "居間", "外", "家族"]
    order = [p for p, _ in events_mod.PLACES]
    seen = [e["place"] for e in events_mod.EVENTS]
    assert seen == sorted(seen, key=order.index)  # 場所の順に並ぶ
    for p in order:
        assert 2 <= seen.count(p) <= 4, p
    for e in events_mod.EVENTS:
        assert e["icf"] and e["label"] and len(e["label"]) <= 12, e["id"]
        for fid, value, confidence, evidence in e["facts"]:
            assert fid in facts, (e["id"], fid)
            spec = facts[fid]
            if spec["type"] == "bool":
                assert isinstance(value, bool), (e["id"], fid)
            else:
                assert value in [o["value"] for o in spec["options"]], (e["id"], fid, value)
            assert confidence in ("high", "low") and evidence in e["text"], (e["id"], fid)
    listed = client.get("/api/events").json()
    assert [(e["place"], e["place_label"]) for e in listed] == [(e["place"], events_mod.PLACE_LABEL[e["place"]]) for e in events_mod.EVENTS]
    js = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
    html = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="event-places" role="group" aria-label="家の場所" hidden' in html
    assert "showPlace(places[0][0]);" in js  # 最初は 1 つ目の場所（寝室。動画の台本が押す「退院した」がある）
