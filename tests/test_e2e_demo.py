"""デモの通し：入口から問いに答えて結果とまとめが出るまでを、画面（web/app.js）と同じ送り方で API だけでたどる。

見本 4 件と「選ぶだけで下書き」の入口のどれかが途中で止まると、デモが通らない。
以後の変更でデモの道が壊れたら、ここが落ちる（鍵なし・試験モード）。
"""
import os

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import app  # noqa: E402
from todoku.engine import DONT_KNOW  # noqa: E402

client = TestClient(app)
# 回数制限の数え直しは tests/conftest.py の fresh_rate_limit（接続元ごとと全体）が毎回行う
NOT_THIS = "__not__"  # web/app.js の NOT_THIS と同じ
SUMMARIZED = {"eligible", "pending", "after_prereq"}  # まとめが扱う状態（app.py の SUMMARIZED と同じ）


DEMO_ANSWERS = {"age_band": "age_75_plus", "residence": "home"}  # 見本の人物（75 歳以上・自宅）に合う答え


def pick_demo(q: dict) -> dict:
    """デモで押す答え：書類の問いは「読んだ値のまま」、「合っていますか？」は「はい」、お住まいは町田市、
    年齢・住まいは見本の人物に合う値、ほかは先頭の選択肢。"""
    if q.get("keep"):
        return {"value": q["keep"]["value"]}
    if q.get("place"):
        return {"place": "132098"}  # 町田市
    if q["confirm"]:
        return {"value": q["options"][0]["value"]}  # はい
    return {"value": DEMO_ANSWERS.get(q["fact"], q["options"][0]["value"])}


def pick_dont_know(q: dict) -> dict:
    return {"value": DONT_KNOW}


def walk(start: dict, choose) -> tuple[dict, dict]:
    """web/app.js の answer()・answerPlace()・step() と同じ形で、問いがなくなるまで /api/step に送る。"""
    state = {"facts": dict(start["facts"]), "suggestions": dict(start["suggestions"]),
             "subject": start["subject"], "asked": 0,
             "municipality": start["place"]["code"] if start.get("place") else ""}
    data = start
    for _ in range(start["max_questions"] + 1):
        q = data["question"]
        if q is None:
            return state, data
        assert q["options"] and q["affects"], q
        answer = choose(q)
        facts, suggestions = dict(state["facts"]), dict(state["suggestions"])
        if "place" in answer:
            suggestions.pop("lives_in_machida", None)
            change = {"suggestions": suggestions, "municipality": answer["place"]}
        else:
            if not (q["confirm"] and answer["value"] == NOT_THIS):
                facts[q["fact"]] = answer["value"]
            suggestions.pop(q["fact"], None)
            change = {"facts": facts, "suggestions": suggestions}
        nxt = {**state, **change, "asked": state["asked"] + 1}
        res = client.post("/api/step", json={k: nxt[k] for k in ("facts", "suggestions", "subject", "asked", "municipality")})
        assert res.status_code == 200, res.text
        state, data = nxt, res.json()
    pytest.fail(f"聞き返しが {start['max_questions']} 問で終わらない")


def check_result(state: dict, data: dict, choose) -> None:
    assert data["question"] is None
    assert state["asked"] <= data["max_questions"]
    results = data["results"]
    assert results, "結果が空"
    shown = {r["id"] for r in results if r["status"] in SUMMARIZED}
    if choose is pick_demo:
        # 全部「わからない」で答えると、当てはまる制度が 0 件の見本がある（要介護4の祖母は 28 件とも「確かめる」）。
        # それは規則どおりなので、当てはまる制度が出ることはデモの答え方でだけ確かめる
        assert shown, "当てはまる制度が 1 件もない"
    for r in results:
        if r["status"] == "eligible":
            assert r["sources"], f"{r['id']} に出典がない"
    res = client.post("/api/summary", json={"facts": state["facts"], "subject": state["subject"],
                                            "municipality": state["municipality"]})
    assert res.status_code == 200, res.text
    for line in res.json()["lines"]:
        assert set(line["program_ids"]) <= shown, line


def test_entry_page_has_both_entrances():
    html = client.get("/").text
    assert 'id="sample-list"' in html and "見本で試す" in html
    for box in ("pick-who", "pick-age", "pref", "city", "pick-worry", "text", "send"):
        assert f'id="{box}"' in html, box


SAMPLES = [s["id"] for s in client.get("/api/samples").json()]


def test_four_samples_are_ready():
    listed = client.get("/api/samples").json()
    assert len(listed) == 5 and all(s["ready"] for s in listed)  # 11/7 に宇都宮市を足して 5 件


@pytest.mark.parametrize("choose", [pick_demo, pick_dont_know], ids=["demo", "dont_know"])
@pytest.mark.parametrize("sample_id", SAMPLES)
def test_sample_walks_to_result(sample_id, choose):
    res = client.post("/api/sample", json={"id": sample_id})
    assert res.status_code == 200, res.text
    start = res.json()
    assert start["engine"] == "sample" and start["extracted"]
    check_result(*walk(start, choose), choose)


@pytest.mark.parametrize("choose", [pick_demo, pick_dont_know], ids=["demo", "dont_know"])
@pytest.mark.parametrize("municipality", ["132098", ""], ids=["machida", "no_place"])
def test_tap_draft_walks_to_result(municipality, choose):
    # web/app.js の buildDraft() が「父・75歳以上・（東京都町田市）・物忘れ・道に迷う・仕事と介護」を選んだときに作る文
    place = "東京都町田市に住んでいます。" if municipality else ""
    draft = f"父（75歳以上）のことです。{place}物忘れが増えてきました。外出すると道に迷うことがあります。私は働きながら介護をしています。"
    res = client.post("/api/consult", json={"text": draft, "municipality": municipality})
    assert res.status_code == 200, res.text
    start = res.json()
    assert start["subject"] and start["extracted"]
    check_result(*walk(start, choose), choose)
