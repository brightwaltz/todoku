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
    assert data["facts"]["caregiver_employee"] is True
    assert data["facts"]["uses_care_services"] is False
    # 「介護保険は使ったことがない」から認定の有無までは読まない。いちばん値打ちのある認定を先に聞き返す
    assert "care_level" not in data["facts"]
    q = data["question"]
    assert q is not None and q["fact"] == "care_level"
    # 答えを先に選んでおかない：選択肢に印はなく、「わからない」は画面が足す
    assert all(set(o) == {"value", "label"} for o in q["options"])
    # 画面の見立てで札を光らせるための id。名前と対になり、どれも結果にある制度
    ids = {r["id"] for r in data["results"]}
    assert q["affects_ids"] and len(q["affects_ids"]) == len(q["affects"])
    assert set(q["affects_ids"]) <= ids


def test_fake_reads_the_draft_made_by_taps():
    # 選ぶだけで作った下書きの文を、試験モードでも読める（本物の Claude と同じ流れを鍵なしで試すため）
    draft = "母（75歳以上）のことです。東京都町田市に住んでいます。最近、病院から退院しました。私は働きながら介護をしています。"
    data = client.post("/api/consult", json={"text": draft, "municipality": "132098"}).json()
    assert data["facts"]["age_band"] == "age_75_plus"
    assert data["facts"]["residence"] == "home"
    assert data["facts"]["caregiver_employee"] is True
    assert data["place"]["name"] == "町田市"


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
    nintei = next(r for r in data["results"] if r["id"] == "kaigo-nintei")
    assert nintei["procedures"] and nintei["procedures_source"].startswith("デジタル庁")


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
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    h = client.get("/api/health").json()
    assert h["programs"] == 32


def test_rate_limit_counts_each_client_ip(monkeypatch):
    from todoku import app as app_mod

    monkeypatch.setattr(app_mod, "RATE_LIMIT", 1)
    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    send = lambda ip: client.post("/api/consult", json={"text": DEMO}, headers={"fly-client-ip": ip}).status_code
    assert send("203.0.113.1") == 200
    assert send("203.0.113.1") == 429
    assert send("203.0.113.2") == 200  # 中継が同じでも、別の人は別に数える


def test_claude_failure_becomes_a_message(monkeypatch):
    from todoku import app as app_mod
    from todoku.claude import ClaudeFailed

    def broken(rules, text):
        raise ClaudeFailed("max_tokens で切れた")

    monkeypatch.setattr(app_mod.CLAUDE, "extract", broken)
    res = client.post("/api/consult", json={"text": "母のことで相談です"})
    assert res.status_code == 503
    assert "もう一度" in res.json()["detail"]


def test_municipality_sets_lives_in_machida():
    from todoku import app as app_mod

    other = "011002"  # 札幌市（web/municipalities.json にある町田市以外のコード）
    assert other in app_mod.PLACES
    # 相談文に「町田」とあっても、画面で選んだ市区町村を優先する（読み取りの後に上書き）
    data = client.post("/api/consult", json={"text": DEMO, "municipality": "132098"}).json()
    assert data["facts"]["lives_in_machida"] is True
    assert data["place"] == {"code": "132098", "pref": "東京都", "name": "町田市"}
    data = client.post("/api/consult", json={"text": DEMO, "municipality": other}).json()
    assert data["facts"]["lives_in_machida"] is False
    assert "lives_in_machida" not in data["suggestions"]
    # 一覧にないコードは無視する
    data = client.post("/api/step", json={"facts": {}, "municipality": "999999"}).json()
    assert all(k["fact"] != "lives_in_machida" for k in data["known"])
    assert data["place"] is None
    data = client.post("/api/step", json={"facts": {"lives_in_machida": True}, "municipality": other}).json()
    assert next(k for k in data["known"] if k["fact"] == "lives_in_machida")["value"] is False


def test_passphrase_only_for_claude(monkeypatch):
    from todoku import app as app_mod

    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    assert client.post("/api/consult", json={"text": DEMO}).status_code == 401
    assert client.post("/api/step", json={"facts": {}}).status_code == 200
    assert client.get("/api/samples").status_code == 200


def test_samples_without_claude(monkeypatch):
    from todoku import app as app_mod

    listed = client.get("/api/samples").json()
    assert len(listed) == 4  # 町田市 3 件と、町田市以外（横浜市）1 件
    assert all(set(s) == {"id", "label", "text", "ready"} for s in listed)
    # 用意のない見本（reading が null）は 404
    blank = {"id": "blank", "label": "空", "text": "母のことです。", "municipality": "", "reading": None, "made": None}
    ready = {"id": "ready", "label": "用意済み", "text": DEMO, "municipality": "132098",
             "reading": {"subject": "お母さま", "facts": [
                 {"fact": "age_band", "value": "age_75_plus", "confidence": "high", "evidence": "82歳"},
                 {"fact": "uses_care_services", "value": "false", "confidence": "high", "evidence": "介護保険は使ったことがありません"},
                 {"fact": "household", "value": "alone", "confidence": "high", "evidence": "ひとり暮らし"},
             ]},
             "made": {"model": "claude-test", "date": "2026-11-03"}}
    monkeypatch.setattr(app_mod, "SAMPLES", [blank, ready])

    def no_claude(rules, text):
        raise AssertionError("見本で Claude を呼んだ")

    monkeypatch.setattr(app_mod.CLAUDE, "extract", no_claude)
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")  # 見本は合言葉なしで見られる
    assert client.post("/api/sample", json={"id": "blank"}).status_code == 404
    assert client.post("/api/sample", json={"id": "nope"}).status_code == 404
    res = client.post("/api/sample", json={"id": "ready"})
    assert res.status_code == 200
    data = res.json()
    assert data["engine"] == "sample" and data["sample"] is True
    assert data["made"]["model"] == "claude-test"
    assert data["facts"]["lives_in_machida"] is True
    assert data["facts"]["uses_care_services"] is False
    # 置いてある結果も検査し直す：根拠が相談文にない事実は確信を low に落とす
    assert data["suggestions"] == {"household": "alone"}
    assert data["question"] is not None


def test_other_cities_see_their_own_offices():
    facts = {"age_band": "age_75_plus", "residence": "home"}
    def where(code):
        data = client.post("/api/step", json={"facts": facts, "municipality": code}).json()
        return next(r for r in data["results"] if r["id"] == "kaigo-nintei")
    yokohama, machida = where("141003"), where("132098")
    assert "町田" not in yokohama["apply"]["where"] and "phone" not in yokohama["apply"]
    assert all("city.machida.tokyo.jp" not in s["url"] for s in yokohama["sources"])
    assert "町田市" in machida["apply"]["where"]
