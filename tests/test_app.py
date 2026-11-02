import os

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import app  # noqa: E402
from todoku.claude import check_extraction, check_summary  # noqa: E402
from todoku import rules as rules_mod  # noqa: E402
from todoku.engine import DONT_KNOW, evaluate  # noqa: E402

client = TestClient(app)
DEMO = "町田市に住む母（82歳）が先月退院してから、家の中で転びやすくなりました。介護保険は使ったことがありません。私は会社勤めで、日中は母ひとりです。"


def test_consult_reads_and_asks():
    data = client.post("/api/consult", json={"text": DEMO}).json()
    assert data["engine"] == "fake"
    assert data["subject"] == "お母さま"
    assert data["facts"]["lives_in_machida"] is True
    assert data["facts"]["age_band"] == "age_75_plus"
    assert data["facts"]["household"] == "alone_daytime"
    q = data["question"]
    assert data["facts"]["care_level"] == "none"
    assert q is not None and q["fact"] != "care_level"
    # 答えを先に選んでおかない：選択肢に印はなく、「わからない」は画面が足す
    assert all(set(o) == {"value", "label"} for o in q["options"])


def test_step_until_done_and_no_text_needed():
    facts = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home", "care_level": "none"}
    asked = 0
    while True:
        data = client.post("/api/step", json={"facts": facts, "subject": "お母さま", "asked": asked}).json()
        if data["question"] is None:
            break
        facts[data["question"]["fact"]] = DONT_KNOW
        asked += 1
    assert asked <= data["max_questions"]
    statuses = {r["id"]: r["status"] for r in data["results"]}
    assert statuses["kaigo-nintei"] == "eligible"
    chain = next(r for r in data["results"] if r["id"] == "shogaisha-kojo")
    assert chain["waiting_on"][0]["name"] == "障害者控除対象者認定書"


def test_step_drops_unknown_facts_and_values():
    data = client.post("/api/step", json={"facts": {"nope": True, "care_level": "care9", "on_welfare": 1}}).json()
    assert all(k["fact"] != "nope" for k in data["known"])
    assert all(k["fact"] != "care_level" for k in data["known"])
    assert all(k["fact"] != "on_welfare" for k in data["known"])


def test_summary_only_points_at_shown_programs():
    facts = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home", "care_level": "none",
             "plans_home_modification": True}
    data = client.post("/api/summary", json={"facts": facts, "subject": "お母さま"}).json()
    assert data["lines"]
    assert all("kaigo-nintei" in line["program_ids"] for line in data["lines"])


def test_check_extraction_downgrades_missing_evidence():
    rs = rules_mod.load()
    raw = {"subject": "お母さま", "facts": [
        {"fact": "household", "value": "alone", "confidence": "high", "evidence": "ひとり暮らし"},
        {"fact": "age_band", "value": "age_75_plus", "confidence": "high", "evidence": "90歳"},
        {"fact": "care_level", "value": "care9", "confidence": "high", "evidence": "要介護"},
        {"fact": "unknown_fact", "value": "true", "confidence": "high", "evidence": ""},
    ]}
    ex = check_extraction(rs, "母はひとり暮らしです。", raw)
    got = {e.fact: e.confidence for e in ex.facts}
    assert got == {"household": "high", "age_band": "low"}
    assert len(ex.dropped) == 2


def test_check_summary_rejects_numbers_and_foreign_ids():
    rs = rules_mod.load()
    shown = [r for r in evaluate(rs, {"caregiver_employee": True}).values() if r.status == "eligible"]
    raw = {"lines": [
        {"program_ids": ["kaigo-kyugyo-kyufu"], "text": "仕事を休むときは介護休業給付金があります。"},
        {"program_ids": ["kaigo-kyugyo-kyufu"], "text": "賃金の67%が出ます。"},
        {"program_ids": ["machida-gps"], "text": "GPS も使えます。"},
    ]}
    lines = check_summary(shown, raw)
    assert [l.text for l in lines] == ["仕事を休むときは介護休業給付金があります。"]


def test_index_and_health():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    h = client.get("/api/health").json()
    assert h["programs"] == 20
