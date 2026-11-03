import os

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
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


def test_care_level_read_is_checked_against_the_card():
    # 認定の区分は、文に書いてあっても「合っていますか？」と聞かない（思い違いのままでも「はい」を押せるため。事例 c15）
    text = "町田市に住む母（82歳）は要介護2と言われています。私は会社勤めです。"
    data = client.post("/api/consult", json={"text": text}).json()
    assert "care_level" not in data["facts"] and data["suggestions"]["care_level"] == "care2"
    q = data["question"]
    assert q["fact"] == "care_level" and q["confirm"] is False
    # 読んだ値と、書類のどこを見るかを、選択肢の前の文で出す
    assert "「要介護2」" in q["hint"] and "被保険者証" in q["hint"]
    # 全部の選択肢から選ぶ。読んだ値に印は付けない
    assert [o["value"] for o in q["options"]] == rules_mod.load().facts["care_level"].values()
    assert all(set(o) == {"value", "label"} for o in q["options"])
    # 「わからない」の代わりに、読んだ値のまま進める
    assert q["keep"]["value"] == "care2" and "要介護2" in q["keep"]["label"]
    # 被保険者証で要支援2と分かったら、その値で判定する
    after = client.post("/api/step", json={"facts": {**data["facts"], "care_level": "support2"},
                                           "suggestions": {}, "asked": 1}).json()
    assert {"fact": "care_level", "value": "support2"}.items() <= next(
        k for k in after["known"] if k["fact"] == "care_level").items()
    assert after["question"] is None or after["question"]["fact"] != "care_level"


def test_other_low_reads_still_ask_yes_no():
    # 区分でない値（受けていない・申請中など）の確かめは今のまま「合っていますか？」（はい・いいえ）
    facts = {"lives_in_machida": True, "age_band": "age_75_plus"}
    q = client.post("/api/step", json={"facts": facts, "suggestions": {"care_level": "none"}}).json()["question"]
    assert q["fact"] == "care_level" and q["confirm"] is True
    assert [o["label"] for o in q["options"]] == ["はい", "いいえ"] and "keep" not in q


def test_fake_reads_the_draft_made_by_taps():
    # 選ぶだけで作った下書きの文を、試験モードでも読める（本物の Claude と同じ流れを鍵なしで試すため）
    draft = "母（75歳以上）のことです。東京都町田市に住んでいます。最近、病院から退院しました。私は働きながら介護をしています。"
    data = client.post("/api/consult", json={"text": draft, "municipality": "132098"}).json()
    assert data["facts"]["age_band"] == "age_75_plus"
    assert data["facts"]["residence"] == "home"
    assert data["facts"]["caregiver_employee"] is True
    assert data["place"]["name"] == "町田市"


def test_step_until_done_and_no_text_needed():
    # 介助の要る場面がある人（認定の申請が当てはまる。裁定 p-prereq、11/4）
    facts = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home", "care_level": "none",
             "needs_daily_help": True}
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
             "needs_daily_help": True}
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
    shown = [r for r in evaluate(rs, {"caregiver_employee": True, "caregiver_insured": True, "care_level": "care2"}).values()
             if r.status == "eligible"]
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
    assert h["programs"] == 40  # 33〜40 は東京都 1 件と横浜市 7 件（11/3 に足した）


def test_entry_text_names_every_place_with_rules():
    """最初の画面の lead と meta の description は、制度を判定する都・市を全部挙げる（「町田市だけ」と狭く書かない）。"""
    import re
    html = client.get("/").text
    lead = re.search(r'<p class="lead">(.*?)</p>', html).group(1)
    desc = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
    places = {rules_mod.LEVELS[p.level] for p in rules_mod.load().programs.values()
              if p.level not in ("national", "local")}
    assert places, "都・市の制度が規則にない"
    for where, text in (("lead", lead), ("description", desc)):
        missing = [place for place in sorted(places) if place not in text]
        assert not missing, f"{where} に {missing} がない"
        assert "だけ" not in text, f"{where} が範囲を狭く書いている：{text}"


def test_rate_limit_counts_each_client_ip(monkeypatch):
    from todoku import app as app_mod

    monkeypatch.setattr(app_mod, "RATE_LIMIT", 1)
    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    send = lambda ip: client.post("/api/consult", json={"text": DEMO}, headers={"fly-client-ip": ip}).status_code
    assert send("203.0.113.1") == 200
    assert send("203.0.113.1") == 429
    assert send("203.0.113.2") == 200  # 中継が同じでも、別の人は別に数える


def test_rate_limit_caps_the_whole_app(monkeypatch):
    from todoku import app as app_mod

    # Fly-Client-IP を変えながら送ると、接続元ごとの制限は外せる（T-012）。アプリ全体の上限で止める
    monkeypatch.setattr(app_mod, "RATE_LIMIT_ALL", 2)
    send = lambda ip, path="/api/consult", body={"text": DEMO}: client.post(
        path, json=body, headers={"fly-client-ip": ip}).status_code
    assert [send("203.0.113.1"), send("203.0.113.2"), send("203.0.113.3")] == [200, 200, 429]
    assert send("203.0.113.4", "/api/summary", {"facts": {}}) == 429  # まとめも Claude を呼ぶので同じ上限
    assert send("203.0.113.5", "/api/step", {"facts": {}}) == 200  # 規則だけの判定し直しは数えない
    assert len(app_mod._hits["203.0.113.3"]) == 0  # 全体で断った回は、接続元ごとの数を増やさない


def test_wrong_passphrase_does_not_use_up_the_whole_app(monkeypatch):
    from todoku import app as app_mod

    # 合言葉を間違えた回はアプリ全体に数えない。数えると、合言葉を知らない人が接続元を変えて送るだけで全員を止められる
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    monkeypatch.setattr(app_mod, "RATE_LIMIT_ALL", 1)
    send = lambda word, ip: client.post(
        "/api/consult", json={"text": DEMO}, headers={"fly-client-ip": ip, "x-aikotoba": word}).status_code
    assert [send("himitsy", "203.0.113.1"), send("", "203.0.113.2")] == [401, 401]
    assert send("himitsu", "203.0.113.3") == 200
    assert send("himitsu", "203.0.113.4") == 429


def test_wrong_passphrase_counts_toward_rate_limit(monkeypatch):
    from todoku import app as app_mod

    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    monkeypatch.setattr(app_mod, "RATE_LIMIT", 3)
    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    send = lambda word, ip="203.0.113.1": client.post(
        "/api/consult", json={"text": DEMO}, headers={"fly-client-ip": ip, "x-aikotoba": word}).status_code
    # 間違えた回も数える。数えないと、合言葉を何度でも試せる（回数制限は手間を増やすだけの対策）
    assert [send("himitsy"), send("himits"), send("")] == [401, 401, 401]
    assert send("himitsu") == 429  # 上限に届いたら、合っていても断る
    assert send("himitsu", ip="203.0.113.2") == 200  # 別の人は別に数える


def test_passphrase_compare_takes_any_bytes(monkeypatch):
    from todoku import app as app_mod

    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    # 中継は送られたバイトをそのまま渡し、Starlette は latin-1 で文字にする。ASCII でない文字でも 500 にしない
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    assert client.post("/api/consult", json={"text": DEMO}, headers={"x-aikotoba": "ひみつ".encode()}).status_code == 401
    assert client.post("/api/consult", json={"text": DEMO}, headers={"x-aikotoba": b"himitsu"}).status_code == 200
    monkeypatch.setattr(app_mod, "PASSPHRASE", "ひみつ")  # 合言葉が日本語でも、UTF-8 のバイトで送れば通る
    assert client.post("/api/consult", json={"text": DEMO}, headers={"x-aikotoba": "ひみつ".encode()}).status_code == 200
    assert client.post("/api/consult", json={"text": DEMO}, headers={"x-aikotoba": "ひみ".encode()}).status_code == 401


def test_screen_keeps_aikotoba_header_safe():
    # 画面（web/app.js）は合言葉をヘッダで送る。fetch は ISO-8859-1 にない文字をヘッダに入れられず、送る前に失敗する。
    # 日本語を入れると「通信できませんでした」になり、保存した値のせいで、そのタブでは見本も動かなくなっていた（T-015）
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    func = re.search(r"^function aikotobaWord\(raw\) \{\n.*?^\}\n", js, re.S | re.M).group(0)
    cases = ["todoku-test", "  todoku-test ", "ｔｏｄｏｋｕ－ｔｅｓｔ", "あいことば", "café", "todoku　test", "", "　"]
    run = f"{func}\nconsole.log(JSON.stringify({json.dumps(cases)}.map(aikotobaWord)))"
    got = json.loads(subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout)
    # 全角の英数字と全角の空白は半角に直して通す。日本語・ASCII でない文字は null（画面で断る）
    assert got == ["todoku-test", "todoku-test", "todoku-test", None, None, "todoku test", "", ""]
    # 送れない値を送らない：合言葉を入れる所（保存した値・欄）は aikotobaWord を通し、ヘッダはその値だけを付ける
    assert 'aikotobaWord(sessionStorage.getItem("aikotoba") || "")' in js
    assert 'aikotobaWord($("aikotoba").value)' in js
    assert '"X-Aikotoba": aikotoba' not in js
    assert len(re.findall(r"^\s+aikotoba = ", js, re.M)) == 2  # 宣言の後に入れる所は、上の 2 か所だけ


def test_board_does_not_count_settled_programs_as_not_eligible():
    # 見立ての帯：済んでいる・申請済み（結果待ち）の制度は「当てはまらない」の列にも、その増減にも数えない（T-022）。
    # 祖母の見本で「要介護4」と答えると要介護認定が済んでいるになり、「当てはまらない制度 +1」と出て、駄目になった制度があるように読めた
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    consts = re.search(r"^// ---- いまの見立て ----\n(.*?)^const calm = ", js, re.S | re.M).group(1)  # 帯の列と置き場（DOM を使わない）
    func = re.search(r"^function changeText\(moved, now\) \{\n.*?^\}\n", js, re.S | re.M).group(0)
    cases = [["ineligible", "done", "pending"], ["done"], ["pending"], ["ineligible", "ineligible"]]
    run = f"""{consts}
{func}
const keys = BOARD.map(([key]) => key);
const out = {json.dumps(cases)}.map((sts) => {{
  const moved = sts.map((status, i) => ({{ id: `p${{i}}`, name: `制度${{i}}`, status }}));
  const now = Object.fromEntries(moved.map((r) => [r.id, colOf(r.status)]));
  return changeText(moved, now);
}});
console.log(JSON.stringify({{ out, cols: ["ineligible", "done", "pending"].map((s) => keys.includes(colOf(s)) ? colOf(s) : null) }}))"""
    got = json.loads(subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout)
    # 当てはまらないの列に置くのは ineligible だけ。済んでいる・結果待ちは列に置かない（列の数に入らない）
    assert got["cols"] == ["no", None, None]
    mixed, done, pending, two_no = got["out"]
    assert mixed.count("当てはまらない制度") == 1 and "当てはまらない制度 +1" in mixed
    assert "済んでいる制度 +1" in mixed and "申請済み・結果待ちの制度 +1" in mixed
    assert done == "済んでいる制度 +1"
    assert pending == "申請済み・結果待ちの制度 +1"
    assert two_no == "当てはまらない制度 +2"


def test_reading_card_is_gone_before_scrolling_to_what_was_read():
    # 読み取った直後に、読み取ったことの欄へ送る（render → renderQuestion → revealThenAsk）。読み取り中の札を消すのがその後だと、
    # 送った後に札の高さ（約 118px）ぶん中身が上へずれ、「相談の文章から読み取ったこと」の見出しが画面の上に切れた（T-023）
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    func = re.search(r"^async function consultWith\(text, call\) \{\n.*?^\}\n", js, re.S | re.M).group(0)
    run = f"""
const hidden = {{}}, nodes = {{}}, seen = [];
const $ = (id) => (nodes[id] ||= {{ disabled: false, kids: [], replaceChildren(...k) {{ this.kids = k; }} }});
const show = (id, on = true) => {{ hidden[id] = !on; }};
const el = (tag, attrs, text) => text;
const fresh = () => ({{}});
const clearBoard = () => {{}};
class GateError extends Error {{}}
let state, resendAfterGate, renderFails = false;
const renderExtracted = () => {{ seen.push(["extracted", hidden.reading]); }};
const render = () => {{ seen.push(["render", hidden.reading]); if (renderFails) throw new Error("落ちた"); }};
{func}
(async () => {{
  const data = {{ subject: "母", facts: {{}}, suggestions: [], engine: "fake", place: null }};
  await consultWith("t", async () => data);
  const ok = {{ seen: [...seen], reading: hidden.reading }};
  await consultWith("t", async () => {{ throw new Error("通信できませんでした"); }});
  const failed = {{ reading: hidden.reading, msg: $("reading").kids }};
  renderFails = true;
  await consultWith("t", async () => data);
  const broke = {{ reading: hidden.reading, msg: $("reading").kids }};
  console.log(JSON.stringify({{ ok, failed, broke }}));
}})();
"""
    got = json.loads(subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout)
    # 読み取ったことの欄を出す時も、送る時（render）も、読み取り中の札はもう消えている
    assert got["ok"] == {"seen": [["extracted", True], ["render", True]], "reading": True}
    # 読み取りに失敗したときは、札に知らせを出して見せる（受け取った後に落ちたときも）
    assert got["failed"] == {"reading": False, "msg": ["通信できませんでした"]}
    assert got["broke"] == {"reading": False, "msg": ["落ちた"]}


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


def test_household_check_shows_no_machida_rule_elsewhere():
    # 「日中はひとり」の町田市の目安（週3日以上・1日6時間以上）は、町田市の救急通報システムの札にだけ書く。
    # 事実の確かめ方に書くと、横浜市の見守り機器補助の札や、横浜市の人への問いにも出てしまったため（T-005）
    facts = {"age_band": "age_75_plus", "residence": "home"}
    def step(code, **extra):
        return client.post("/api/step", json={"facts": facts, "municipality": code, "asked": 99, **extra}).json()
    yokohama = step("141003")
    mimamori = next(r for r in yokohama["results"] if r["id"] == "yokohama-mimamori")
    checks = [m["check"] for m in mimamori["missing"] if m["fact"] == "household"]
    assert checks and all("町田" not in c for c in checks)
    unconfirmed = step("141003", suggestions={"household": "alone"})["unconfirmed"]
    assert [u["fact"] for u in unconfirmed] == ["household"] and "町田" not in unconfirmed[0]["check"]
    # 町田市では、救急通報システムの札に目安が残る
    tsuho = next(r for r in step("132098")["results"] if r["id"] == "machida-kyukyu-tsuho")
    assert any("週に3日以上" in t and "6時間以上" in t for t in tsuho["at_application"])


def test_affects_name_only_programs_on_the_result_screen():
    # 「この答えで判定が決まる制度」には、結果の画面に出る制度だけを挙げる（T-007）。
    # 横浜市では型（配食・見守りなど）を判定はしたまま結果から外すので、問いの一覧に名前だけ出ると食い違う
    def visible(q: dict, shown: set[str]) -> None:
        assert set(q["affects_ids"]) <= shown, (q["fact"], set(q["affects_ids"]) - shown)
        assert len(q["affects"]) == len(q["affects_ids"])

    for start in ({}, {"age_band": "age_75_plus", "residence": "home"}):
        body = {"facts": dict(start), "suggestions": {"household": "alone"}, "municipality": "141003", "asked": 0}
        for _ in range(5):
            data = client.post("/api/step", json=body).json()
            shown = {r["id"] for r in data["results"]}
            if data["question"] is None:
                assert data["unconfirmed"]
                for u in data["unconfirmed"]:
                    visible(u, shown)
                break
            q = data["question"]
            visible(q, shown)
            body["facts"][q["fact"]] = DONT_KNOW
            body["suggestions"].pop(q["fact"], None)
            body["asked"] += 1
        else:
            pytest.fail("聞き返しが終わらない")


def test_yes_no_reasons_carry_no_value():
    # 横浜市では型を出さない（市の制度を一つずつ判定している）ので、型の札を見るのは大阪市で
    data = client.post("/api/step", json={"facts": {"age_band": "age_75_plus", "residence": "home", "wandering_risk": True},
                                          "municipality": "271004"}).json()
    card = next(r for r in data["results"] if r["id"] == "local-haikai")
    assert all(x["value"] is None for x in card["reasons"])  # 町田市以外・行方不明のおそれ（はい・いいえ）と、確かめる先


def test_center_search_points_at_the_prefecture():
    def step(code):
        return client.post("/api/step", json={"facts": {"age_band": "age_75_plus", "residence": "home"},
                                              "municipality": code}).json()
    yokohama, nowhere = step("141003"), step("")
    pref14 = "https://www.kaigokensaku.mhlw.go.jp/14/index.php?action_kouhyou_pref_seikatu_search_facility_search=true&SearchType=community"
    assert yokohama["center_search"] == pref14
    assert nowhere["center_search"] == "https://www.kaigokensaku.mhlw.go.jp/"  # 都道府県が分からなければ入口
    # 申請先に地域包括支援センターを挙げた制度にだけ付ける。町田市の申請先（高齢者支援センター）には付けない
    nintei = next(r for r in yokohama["results"] if r["id"] == "kaigo-nintei")
    assert nintei["apply"]["center_search"] == pref14
    machida = step("132098")
    assert "center_search" not in next(r for r in machida["results"] if r["id"] == "kaigo-nintei")["apply"]
    assert all("center_search" not in r["apply"] for r in yokohama["results"]
               if "地域包括支援センター" not in r["apply"]["where"] + r["apply"].get("how", ""))


def test_residence_is_asked_as_a_place():
    # お住まいが分からないときは「町田市にお住まいですか？」ではなく、市区町村を選ぶ問いにする（はい・いいえは控え）
    facts = {"age_band": "age_75_plus", "residence": "home", "care_level": "care2"}
    q = client.post("/api/step", json={"facts": facts, "subject": "お母さま"}).json()["question"]
    assert q["fact"] == "lives_in_machida"
    assert q["place"]["text"] == "お母さまはどちらにお住まいですか？"
    assert [o["value"] for o in q["options"]] == [True, False]
    # 選んだ市区町村を送れば、もう聞かない
    data = client.post("/api/step", json={"facts": facts, "asked": 1, "municipality": "141003"}).json()
    assert data["question"] is None or data["question"]["fact"] != "lives_in_machida"


def test_unconfirmed_reads_are_offered_on_the_result():
    # 3 問で確かめきれなかった読み取りは、結果の画面で同じ形の問いにする（「（あとで確かめます）」の約束を守る）
    facts = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home"}
    suggestions = {"household": "alone", "care_level": "care2"}
    asking = client.post("/api/step", json={"facts": facts, "suggestions": suggestions}).json()
    assert asking["question"] is not None and asking["unconfirmed"] == []
    done = client.post("/api/step", json={"facts": facts, "suggestions": suggestions, "subject": "お母さま",
                                          "asked": 99}).json()
    assert done["question"] is None
    by_fact = {q["fact"]: q for q in done["unconfirmed"]}
    assert set(by_fact) == {"household", "care_level"}
    assert by_fact["household"]["confirm"] is True
    assert by_fact["household"]["text"] == "お母さまは「ひとり暮らし」で合っていますか？"
    assert by_fact["care_level"]["keep"]["value"] == "care2"  # 区分は書類で選ぶ形のまま
    assert by_fact["care_level"]["affects_ids"]
    # 確かめた読み取りは、もう出さない
    again = client.post("/api/step", json={"facts": {**facts, "household": "alone"},
                                           "suggestions": {"care_level": "care2"}, "asked": 99}).json()
    assert [q["fact"] for q in again["unconfirmed"]] == ["care_level"]


def test_pending_certification_is_marked_on_dependents():
    facts = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home", "care_level": "applying"}
    data = client.post("/api/step", json={"facts": facts, "asked": 99}).json()
    nintei = next(r for r in data["results"] if r["id"] == "kaigo-nintei")
    assert nintei["status"] == "pending" and nintei["status_label"] == "申請済み・結果待ち"
    assert data["counts"]["pending"] == 1
    yogu = next(r for r in data["results"] if r["id"] == "fukushi-yogu-konyu")
    assert {"id": "kaigo-nintei", "name": "要介護・要支援認定の申請", "pending": True} in yogu["waiting_on"]
    # まとめにも渡す（試験モードの文は「結果が出ると」）
    lines = client.post("/api/summary", json={"facts": facts}).json()["lines"]
    firsts = [l["text"] for l in lines if l["program_ids"][0] == "kaigo-nintei"]
    assert firsts and all("結果が出ると" in t and "申請すると" not in t for t in firsts)


def test_large_bodies_are_refused():
    # 合言葉の要らない /api/step でも、大きな本文は読み込まずに断る（長さを書かない送り方でも数えて止める）
    big = {"facts": {"age_band": "age_75_plus"}, "subject": "x" * (70 * 1024)}
    assert client.post("/api/step", json=big).status_code == 413
    chunked = client.post("/api/step", content=iter([b'{"facts": {}, "subject": "', b"x" * (70 * 1024), b'"}']),
                          headers={"content-type": "application/json"})
    assert chunked.status_code == 413
    many = {"facts": {f"k{i}": True for i in range(65)}}
    assert client.post("/api/step", json=many).status_code == 422
    assert client.post("/api/step", json={"facts": {"age_band": "age_75_plus"}}).status_code == 200


SECRET = "ひみつの相談"
BROKEN = [  # 対になっていないサロゲート（JSON の \ud800 と、生のバイト）。subject は 12 字で切られる前に残る短さにする
    ("/api/consult", b'{"text": "' + SECRET.encode() + b'\\ud800"}'),
    ("/api/consult", b'{"text": "' + SECRET.encode() + b'\xed\xa0\x80"}'),
    ("/api/consult", b'{"text": "' + SECRET.encode() + b'", "municipality": "13\\ud800"}'),
    ("/api/step", b'{"subject": "' + SECRET.encode() + b'\\ud800", "facts": {"care_level": "care2"}, "asked": 1}'),
    ("/api/step", b'{"subject": "' + SECRET.encode() + b'", "municipality": "13\\ud800"}'),
    ("/api/step", b'{"subject": "' + SECRET.encode() + b'", "facts": {"care_level": "\\ud800"}}'),
    ("/api/step", b'{"subject": "' + SECRET.encode() + b'", "suggestions": {"\\ud800": true}}'),
    ("/api/summary", b'{"subject": "' + SECRET.encode() + b'\\ud800", "facts": {"care_level": "care2"}}'),
    ("/api/sample", b'{"id": "' + SECRET.encode() + b'\\ud800"}'),
]


@pytest.mark.parametrize("path,raw", BROKEN)
def test_broken_characters_are_refused_without_echo(path, raw):
    # 壊れた文字で 500 になると、uvicorn のトレースバックに相談文の全文が出て fly logs に残った（Managed Agent の指摘、T-002）。
    # トレースバックが出るのは例外がアプリの外に出たときだけ。client は例外をそのまま投げるので、ここで 4xx が返れば出ていない
    res = client.post(path, content=raw, headers={"content-type": "application/json"})
    assert res.status_code == 422
    assert SECRET not in res.text
    assert isinstance(res.json()["detail"], str)  # 画面（app.js）は detail をそのまま文として出す


def test_validation_errors_do_not_echo_the_text():
    # 長すぎる相談文も、送った文を返さない。detail は画面にそのまま出せる 1 文にする
    res = client.post("/api/consult", json={"text": SECRET * 400})
    assert res.status_code == 422 and SECRET not in res.text and isinstance(res.json()["detail"], str)
    # 壊れていない文字（絵文字・サロゲートの対）はふつうに通る
    ok = client.post("/api/step", json={"subject": "母😀", "facts": {"age_band": "age_75_plus"}})
    assert ok.status_code == 200


def test_machida_hides_the_types():
    # 町田市では市の制度を一つずつ判定するので、型は出さない。「介護用品（紙おむつなど）の支給」が当てはまらないに並ぶと、
    # 町田市の紙おむつ支給が「確かめる」にあるのに「紙おむつは駄目」と読めるため（Managed Agent の通し、11/3）
    facts = {"age_band": "age_75_plus", "residence": "home", "uses_diapers": True, "care_level": "care4"}
    machida = client.post("/api/step", json={"facts": facts, "municipality": "132098"}).json()
    osaka = client.post("/api/step", json={"facts": facts, "municipality": "271004"}).json()
    assert not [r for r in machida["results"] if r["id"].startswith("local-")]
    # 国 17・町田市 10・東京都 1（11/3 に東京都と横浜市の制度を足して 27 件から 28 件に）
    assert sum(machida["counts"].values()) == len(machida["results"]) == 28  # 札・数・一覧がそろう
    assert any(r["id"] == "local-kaigo-yohin" for r in osaka["results"])
    # 町田市・横浜市・東京都の外では、そこの制度を出さない：国 17・型 5（前は横浜市で見ていた。横浜市は型を出さなくなった）
    assert len(osaka["results"]) == 22


def test_other_cities_hide_machida_programs():
    # その市・都以外と分かったら、住所だけで外れる制度を「当てはまらない」に並べず、そこなら判定すると 1 行で伝える
    facts = {"age_band": "age_75_plus", "residence": "home", "uses_diapers": True, "care_level": "care4"}
    def step(code):
        return client.post("/api/step", json={"facts": facts, "municipality": code}).json()
    yokohama, machida, osaka, nowhere = step("141003"), step("132098"), step("271004"), step("")
    assert not [r for r in yokohama["results"] if r["id"].startswith(("machida-", "tokyo-"))]
    assert not [r for r in machida["results"] if r["id"].startswith("yokohama-")]
    assert sum(yokohama["counts"].values()) == len(yokohama["results"])
    assert osaka["hidden_places"] == [{"name": "町田市", "n": 10}, {"name": "横浜市", "n": 7}, {"name": "東京都", "n": 1}]
    # 町田市・横浜市に住む人には、ほかの市の件数を知らせない（自分の市の制度を一つずつ見ているため）
    assert machida["hidden_places"] == [] and any(r["id"].startswith("machida-") for r in machida["results"])
    assert yokohama["hidden_places"] == [] and any(r["id"].startswith("yokohama-") for r in yokohama["results"])
    # 住所が分からない間は今までどおり、市・都の制度も型も出す
    assert nowhere["hidden_places"] == [] and len(nowhere["results"]) == 40
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert "data.hidden_places" in script


def test_unknown_results_are_closest_first():
    # 確かめるは、分からない条件が少ない順、次に当てはまった条件が多い順（11/3 の判断 1）。祖母の相談の紙おむつが先頭に来る
    sample = client.post("/api/sample", json={"id": "kaigo4-sobo"}).json()  # 見本を 3 問に答えた後（要介護4・75歳以上・自宅）
    facts = {**sample["facts"], "care_level": "care4", "age_band": "age_75_plus", "residence": "home"}
    data = client.post("/api/step", json={"facts": facts, "municipality": sample["place"]["code"]}).json()
    unknown = [r for r in data["results"] if r["status"] == "unknown"]
    keys = [(sum(x["state"] == "U" for x in r["reasons"]), -sum(x["state"] == "T" for x in r["reasons"])) for r in unknown]
    assert keys == sorted(keys)
    assert unknown[0]["id"] == "machida-kami-omutsu"


def test_status_names_are_the_result_headings():
    # 札（status_label）と、結果の見出し・README の表の呼び名を 1 つにする（「情報が足りない」は書き方を責めるように読める）
    from pathlib import Path
    from todoku.app import STATUS_LABEL
    root = Path(__file__).resolve().parents[1]
    js, readme = (root / "web/app.js").read_text(encoding="utf-8"), (root / "README.md").read_text(encoding="utf-8")
    for status in ("eligible", "pending", "after_prereq", "unknown"):
        label = STATUS_LABEL[status]
        assert f'["{status}", "{label}",' in js
        assert f"| {label} |" in readme


def test_yokohama_shows_its_programs_and_one_type():
    # 横浜市では市の制度を一つずつ判定するので型は出さない。成年後見の利用支援だけは、横浜市の制度として書けていないので型のまま出す
    facts = {"age_band": "age_75_plus", "residence": "home", "dementia_diagnosis": True, "wandering_risk": True}
    data = client.post("/api/step", json={"facts": facts, "municipality": "141003"}).json()
    ids = [r["id"] for r in data["results"]]
    assert [i for i in ids if i.startswith("local-")] == ["local-koken"]
    assert {"yokohama-keiro-pass", "yokohama-sos"} <= {r["id"] for r in data["results"] if r["status"] == "eligible"}
    # 横浜市のページは横浜市の人にだけ出典に出す。国の制度の申請先は町田市の書き方にしない
    sos = next(r for r in data["results"] if r["id"] == "yokohama-sos")
    assert all("city.yokohama.lg.jp" in s["url"] for s in sos["sources"])
    assert "町田" not in next(r for r in data["results"] if r["id"] == "kaigo-nintei")["apply"]["where"]


def test_type_place_reason_matches_the_cities_listed_one_by_one():
    # 型の住所の条件の文は、市の制度を一つずつ示している市（RULES_CITY）を全部名指す。横浜市でも出す型は、出す訳も書く（指摘 R-6）
    from todoku.app import RULES, RULES_CITY
    from todoku.engine import evaluate
    names = [rules_mod.LEVELS[level] for level in RULES_CITY]
    for r in evaluate(RULES, {}).values():
        if r.program.level != "local":
            continue
        label = next(leaf.label for leaf in r.leaves if leaf.fact == "lives_in_machida")
        assert all(n in label for n in names), f"{r.program.id}: {label}"
        assert all(f"{rules_mod.LEVELS[c]}の" in label.split("。", 1)[-1] for c in r.program.shown_in), f"{r.program.id}: {label}"
    # 横浜市の人に出る成年後見の型の理由（画面に出る文）
    facts = {"age_band": "age_75_plus", "residence": "home", "dementia_diagnosis": True}
    data = client.post("/api/step", json={"facts": facts, "municipality": "141003"}).json()
    koken = next(r for r in data["results"] if r["id"] == "local-koken")
    place = next(x for x in koken["reasons"] if x["label"].startswith("町田市以外にお住まい"))
    assert place["state"] == "T" and "横浜市" in place["label"] and "この型で示します" in place["label"]


def test_place_question_counts_every_city():
    # 住所の問いは、規則が使う市・都ごとに 1 つと「そのほか」で値打ちを数える。分かっている住所と合わないものは外す
    from todoku.engine import statuses
    from todoku.questions import place_choices, score_fact
    rs = rules_mod.load()
    assert len(place_choices(rs, {})) == 4  # 町田市・横浜市・東京都のほかの市区町村・そのほか
    assert len(place_choices(rs, {"lives_in_machida": False})) == 3
    facts = {"age_band": "age_75_plus"}
    score, affects = score_fact(rs, facts, "lives_in_machida", statuses(rs, facts))
    assert score > 0 and {"yokohama-keiro-pass", "tokyo-silver-pass", "machida-gps"} <= set(affects)


def test_place_facts_are_not_read_by_claude():
    # 横浜市・東京都に住んでいるかは、選んだ市区町村から決める。Claude の読み取りの一覧と形には入れない（抽出の指示は変えない）
    from todoku.claude import _catalog, _extract_schema
    rs = rules_mod.load()
    catalog = _catalog(rs)
    assert "lives_in_machida" in catalog
    assert "lives_in_yokohama" not in catalog and "lives_in_tokyo" not in catalog
    enum = _extract_schema(rs)["properties"]["facts"]["items"]["properties"]["fact"]["enum"]
    assert "lives_in_yokohama" not in enum and "lives_in_machida" in enum
    out = check_extraction(rs, "横浜市に住む父", {"subject": "お父さま", "facts": [
        {"fact": "lives_in_yokohama", "value": "true", "confidence": "high", "evidence": "横浜市に住む"}]})
    assert not out.facts and out.dropped


def test_place_facts_come_only_from_the_place():
    # 横浜市・東京都に住んでいるかを本文で送っても使わない（町田市と横浜市の両方に住んでいることにできないように。3rd の点検）
    facts = {"lives_in_machida": True, "lives_in_yokohama": True, "age_band": "age_75_plus"}
    data = client.post("/api/step", json={"facts": facts, "municipality": ""}).json()
    assert not [r for r in data["results"] if r["id"].startswith("yokohama-")]
    assert any(r["id"] == "tokyo-silver-pass" and r["status"] == "eligible" for r in data["results"])


def test_place_is_asked_again_when_only_not_machida():
    # 町田市ではないとだけ分かった人にも、横浜市・東京都かを聞き直す（町田市の答えだけで住所の問いを済ませていた。3rd の点検）
    from todoku.questions import candidates, place_fact
    rs = rules_mod.load()
    assert place_fact(rs, {}) == "lives_in_machida"
    assert place_fact(rs, {"lives_in_machida": True}) is None  # 東京都・横浜市は町田市の答えから埋まる
    assert place_fact(rs, {"lives_in_machida": False}) == "lives_in_yokohama"
    names = candidates(rs, {"lives_in_machida": False})
    assert "lives_in_yokohama" in names and "lives_in_machida" not in names and "lives_in_tokyo" not in names
    # 住所の問いに「わからない」と答えたら、住所はもう聞かない
    facts = {"lives_in_machida": False, "lives_in_yokohama": DONT_KNOW}
    assert place_fact(rs, facts) is None and not set(candidates(rs, facts)) & set(rs.places)
    assert place_fact(rs, {"lives_in_machida": DONT_KNOW}) is None


def test_place_question_comes_back_for_not_machida():
    facts = {"lives_in_machida": False, "age_band": "age_75_plus", "residence": "home", "care_level": "care2"}
    places = set(rules_mod.load().places)
    asked, q = 0, None
    for _ in range(3):
        q = client.post("/api/step", json={"facts": facts, "subject": "お母さま", "asked": asked}).json()["question"]
        if q is None or q["fact"] in places:
            break
        facts[q["fact"]] = DONT_KNOW
        asked += 1
    assert q is not None and q["fact"] == "lives_in_yokohama"
    assert q["text"] == q["place"]["text"] == "お母さまはどちらにお住まいですか？"
    assert q["options"] == []  # 横浜市・東京都の事実の はい・いいえ は使わないので、控えの選択肢は出さない
    # 市区町村を選べば、もう聞かない。住所だけで外れる横浜市・東京都の制度は 1 行にまとめる
    data = client.post("/api/step", json={"facts": facts, "subject": "お母さま", "asked": asked + 1,
                                          "municipality": "271004"}).json()
    assert data["question"] is None or data["question"]["fact"] not in places
    assert not [r for r in data["results"] if r["id"].startswith("yokohama-") or r["id"] == "tokyo-silver-pass"]
    assert {"横浜市", "東京都"} <= {h["name"] for h in data["hidden_places"]}
    # 「わからない」と答えれば、もう聞かない。横浜市の制度は住所が分からないまま残す（要介護2で外れるものは、住所でなく区分で外れる）
    data = client.post("/api/step", json={"facts": {**facts, "lives_in_yokohama": DONT_KNOW}, "subject": "お母さま",
                                          "asked": asked + 1}).json()
    assert data["question"] is None or data["question"]["fact"] not in places
    assert next(k for k in data["known"] if k["fact"] == "lives_in_machida")["value"] is False
    yokohama = [r for r in data["results"] if r["id"].startswith("yokohama-")]
    assert yokohama and all(any(x["label"] == "横浜市に住んでいる" and x["state"] == "U" for x in r["reasons"]) for r in yokohama)
    assert next(r for r in yokohama if r["id"] == "yokohama-keiro-pass")["status"] == "unknown"


def test_place_facts_keep_only_dont_know():
    from todoku.app import clean_facts
    assert clean_facts({"lives_in_yokohama": True, "lives_in_tokyo": DONT_KNOW}) == {"lives_in_tokyo": DONT_KNOW}


def test_place_is_offered_on_the_result_when_not_asked():
    # 3 問を住所より値打ちの高い問いが埋めたときは、結果の画面の「まだ確かめていないこと」に市区町村を選ぶ欄を出す
    facts = {"lives_in_machida": False, "age_band": "age_40_64", "residence": "home", "care_level": "care2"}
    places = set(rules_mod.load().places)
    data = client.post("/api/step", json={"facts": facts, "subject": "お母さま", "asked": 99}).json()
    assert data["question"] is None
    first = data["unconfirmed"][0]
    assert first["fact"] == "lives_in_yokohama"
    assert first["text"] == "お母さまはどちらにお住まいですか？" and first["options"] == []
    # 市区町村を選ぶか「わからない」と答えたら、もう出さない
    data = client.post("/api/step", json={"facts": facts, "subject": "お母さま", "asked": 99, "municipality": "271004"}).json()
    assert not [u for u in data["unconfirmed"] if u["fact"] in places]
    data = client.post("/api/step", json={"facts": {**facts, "lives_in_yokohama": DONT_KNOW}, "subject": "お母さま",
                                          "asked": 99}).json()
    assert not [u for u in data["unconfirmed"] if u["fact"] in places]
def test_care_level_question_names_the_person():
    # 認定の問いにも主語を書き、相談文の呼び方に置き換える（指摘 P-6。見本「退院した母」では最初の問いになる）
    start = client.post("/api/sample", json={"id": "taiin-haha"}).json()
    q = start["question"]
    assert start["subject"] == "お母さま" and q["fact"] == "care_level" and q["confirm"] is False
    assert q["text"].startswith("お母さまは、") and "ご本人" not in q["text"]
    # 「わからない」と答えた後の「まだ確かめていないこと」の問いも同じ呼び方
    after = client.post("/api/step", json={"facts": {**start["facts"], "care_level": DONT_KNOW},
                                           "suggestions": start["suggestions"], "subject": start["subject"],
                                           "asked": 1}).json()
    missing = [m["question"] for r in after["results"] for m in r["missing"] if m["fact"] == "care_level"]
    assert missing and all(m.startswith("お母さまは、") for m in missing)


# ご本人のことを聞くのに主語が無かった問い（T-025）。介護をする家族に聞く問い（caregiver_employee・fire_risk）と、
# 家の工事や福祉用具を考えているかの問いは、聞く相手が家族なので主語を書かない
PERSON_FACTS = ("uses_care_services", "shortstay_or_facility", "high_medical_costs",
                "hearing_certifiable", "incontinence_bedding")


def test_person_questions_name_the_person():
    for fid in PERSON_FACTS:
        assert rules_mod.load().facts[fid].question.startswith("ご本人は、"), fid
    # 見本「退院した母」のデモの道：認定を「受けていない」と答えると、2 問目は医療費の問いで、相談文の呼び方で出る
    start = client.post("/api/sample", json={"id": "taiin-haha"}).json()
    assert start["subject"] == "お母さま" and start["question"]["fact"] == "care_level"
    second = client.post("/api/step", json={"facts": {**start["facts"], "care_level": "none"},
                                            "suggestions": start["suggestions"], "subject": start["subject"],
                                            "asked": 1}).json()["question"]
    assert second["fact"] == "high_medical_costs"
    assert second["text"].startswith("お母さまは、") and "ご本人" not in second["text"]
    # 「わからない」と答え続けた後の「まだ確かめていないこと」にも、5 問とも同じ呼び方で出る
    facts = {**start["facts"], "care_level": DONT_KNOW}
    data = client.post("/api/step", json={"facts": facts, "suggestions": start["suggestions"],
                                          "subject": start["subject"], "asked": 1}).json()
    asked = 1
    while data.get("question") and asked < 30:
        asked += 1
        facts = {**facts, data["question"]["fact"]: DONT_KNOW}
        data = client.post("/api/step", json={"facts": facts, "suggestions": start["suggestions"],
                                              "subject": start["subject"], "asked": asked}).json()
    assert not data.get("question")
    missing = {m["fact"]: m["question"] for r in data["results"] for m in r["missing"] if m["fact"] in PERSON_FACTS}
    assert set(missing) == set(PERSON_FACTS)
    assert all(t.startswith("お母さまは、") and "ご本人" not in t for t in missing.values()), missing
