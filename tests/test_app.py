import json
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
    lines = check_summary(rs, shown, raw)
    assert [l.text for l in lines] == ["仕事を休むときは介護休業給付金があります。"]


def test_index_and_health():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    h = client.get("/api/health").json()
    assert h["programs"] == 40  # 33〜40 は東京都 1 件と横浜市 7 件（11/3 に足した）


def test_index_and_health_answer_head_like_get():
    """「/」と /api/health は HEAD に 200 で答え、本文は送らず、ヘッダは GET と同じ（T-056）。@app.get だけだと 405 だった。"""
    for path in ["/", "/api/health"]:
        g, h = client.get(path), client.head(path)
        assert h.status_code == 200, path
        assert h.content == b"", path
        for k in ["content-type", "content-length", "content-security-policy", "x-frame-options"]:
            assert h.headers.get(k) == g.headers.get(k), (path, k)
    assert client.head("/").headers["cache-control"] == "no-cache"


def test_large_responses_are_gzipped_but_index_is_not():
    """/api/sample・/api/step と画面のファイルは、Accept-Encoding: gzip なら縮めて返す（T-061。1 回 約 84KB を縮めずに送っていた）。
    「/」は HEAD と GET のヘッダを同じに保つため縮めない（T-056）。小さい /api/health も縮めない。"""
    gz, plain = {"accept-encoding": "gzip"}, {"accept-encoding": "identity"}
    a = client.post("/api/sample", json={"id": "taiin-haha"}, headers=gz)
    b = client.post("/api/sample", json={"id": "taiin-haha"}, headers=plain)
    assert a.headers.get("content-encoding") == "gzip" and "content-encoding" not in b.headers
    assert int(a.headers["content-length"]) * 3 < int(b.headers["content-length"])
    assert a.json() == b.json()  # httpx が解いた中身は、縮めないときと同じ
    assert "content-security-policy" in a.headers and a.headers.get("x-frame-options") == "DENY"
    body = {k: a.json()[k] for k in ("facts", "suggestions", "subject")} | {"asked": 0, "municipality": ""}
    assert client.post("/api/step", json=body, headers=gz).headers.get("content-encoding") == "gzip"
    for path in ["/static/app.js", "/static/municipalities.json"]:
        r = client.get(path, headers=gz)
        assert r.headers.get("content-encoding") == "gzip" and r.headers["cache-control"] == "no-cache", path
    for path in ["/", "/api/health"]:
        assert "content-encoding" not in client.get(path, headers=gz).headers, path


def test_security_headers_on_every_response():
    """全部の応答に CSP・nosniff・Referrer-Policy・枠に入れさせないヘッダが付く（T-033）。CSP は 'unsafe-inline' を許さず、
    画面の el() は style 属性を setAttribute でなく CSSOM で付ける（属性のままだと CSP で止まり、段階の動きが消える）。"""
    import re
    from pathlib import Path
    for path in ["/", "/static/app.js", "/static/style.css", "/api/samples", "/api/health"]:
        h = client.get(path).headers
        csp = h["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp, path
        assert h["x-content-type-options"] == "nosniff", path
        assert h["referrer-policy"] == "no-referrer", path
        assert h["x-frame-options"] == "DENY", path
        assert "strict-transport-security" not in h, path  # deploy 後に戻せないので付けない
    html = client.get("/").text
    assert "<style" not in html and " style=" not in html and "<script>" not in html  # インラインは CSP で止まる
    for host in re.findall(r'<link [^>]*href="https://([^/"]+)', html):
        assert host in ("fonts.googleapis.com", "fonts.gstatic.com"), host  # 読み込むのは CSP の style-src・font-src に入れた所だけ（<a> の行き先は CSP の外）
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert 'k === "style") node.style.cssText = v' in js
    assert not re.search(r'setAttribute\(\s*["\']style', js)


def test_page_names_its_icon_so_browsers_do_not_ask_for_favicon_ico():
    """画面がアイコンを名指しする（T-048）。名指しがないとブラウザが /favicon.ico を取りに行き、最初の読み込みで
    404 が console.error に出ていた（T-043 の気づき 7）。アイコンは CSP の img-src 'self' で読める自前の SVG で、
    直に開かれても動くもの（スクリプト・イベントの属性）とよその参照を持たない。"""
    import re
    html = client.get("/").text
    links = re.findall(r'<link rel="icon" href="([^"]+)" type="image/svg\+xml">', html)
    assert links == ["/static/favicon.svg"]
    r = client.get(links[0])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-content-type-options"] == "nosniff"
    svg = r.text
    assert svg.lstrip().startswith("<svg") and "<script" not in svg.lower()
    assert not re.search(r"\son\w+\s*=", svg, re.I)  # onload= など
    assert not re.search(r"(href|src)\s*=", svg, re.I) and "url(" not in svg  # よその出どころを引かない
    assert "#0d6b5b" in svg  # 画面の上の帯（style.css の --accent）と同じ色


def test_entry_text_does_not_single_out_places():
    """最初の画面の lead と meta の description は、制度を一つずつ書けている都・市の名前を挙げない（T-122）。

    前は全部挙げる決まりだった（「町田市だけ」と狭く書かないため）。それでも「東京都・横浜市・町田市にお住まいなら…」は、
    ほかの地域の人に「自分は対象外」と読まれた（11/5 の柴田の感想）。範囲を狭く書かない決まり（「だけ」）は残す。
    """
    import re
    html = client.get("/").text
    lead = re.search(r'<p class="lead">(.*?)</p>', html).group(1)
    desc = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
    places = {rules_mod.LEVELS[p.level] for p in rules_mod.load().programs.values()
              if p.level not in ("national", "local")}
    assert places, "都・市の制度が規則にない"
    for where, text in (("lead", lead), ("description", desc)):
        named = [place for place in sorted(places) if place in text]
        assert not named, f"{where} が {named} を名指している：{text}"
        assert "全国" in text and "お住まいなら" not in text, text
        assert "だけ" not in text, f"{where} が範囲を狭く書いている：{text}"


def test_entry_text_counts_match_rules_and_municipalities():
    """入口・README の冒頭の「全国の市区町村（1,741）」「市区町村の事業 5 種類」が、
    web/municipalities.json と規則から数えた数と合うか（11/4 T-080a）。

    総務省のコード表には北方領土の 6 村（01695〜01700）も載っているので、それを除いて数える。
    """
    import re
    from pathlib import Path
    from todoku.app import WEB_DIR

    data = json.loads((WEB_DIR / "municipalities.json").read_text(encoding="utf-8"))
    northern = {"016951", "016969", "016977", "016985", "016993", "017001"}
    codes = [code for pref in data["prefectures"] for code, _ in pref["cities"]]
    assert northern <= set(codes)
    n_cities = len(set(codes) - northern)
    n_types = sum(1 for p in rules_mod.load().programs.values() if p.level == "local")
    html = client.get("/").text
    lead = re.search(r'<p class="lead">(.*?)</p>', html).group(1)
    desc = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
    readme_first = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8").split("\n\n", 2)[1]
    for where, text in (("lead", lead), ("description", desc), ("README", readme_first)):
        assert f"全国の市区町村（{n_cities:,}）" in text, where
        assert f"市区町村の事業 {n_types} 種類" in text, where
        assert "全国の実施率" in text, where



def test_entry_shows_little_text_and_folds_the_rest():
    """最初の画面に見せるのは、見出し・相談の欄・絵から選ぶ・見本・合言葉の小さな入口だけ（T-126。11/5 の柴田の気づき
    「最初の画面が文字ばかりだと『難しそう』と読まずに閉じる」）。説明・全国の見取り図は「このサイトについて」、
    どなた・お住まいを選ぶ欄は畳んだ details に入れる。数え方と前後の数は $OPS/submission/t126-critique.md。
    """
    import re
    from pathlib import Path
    html = client.get("/").text
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    header = re.search(r'<header class="top">(.*?)</header>', html, re.S).group(1)
    assert "<h1>" in header and "<p" not in header  # 上の帯は名前だけ。説明の文を置かない
    about = re.search(r'<details id="about"[^>]*>(.*?)</details>', html, re.S).group(1)
    assert "<summary>このサイトについて</summary>" in about and " open" not in html[html.index('id="about"') - 9:][:40]
    for inner in ('<p class="lead">', 'id="japan"', 'こちらには残しません', 'マイクの印'):
        assert inner in about, inner
    picks = re.search(r'<details class="pick-more" id="pick-more">(.*?)</details>', html, re.S).group(1)
    for box in ("pick-who", "pick-age", "pref", "city", "pick-worry"):
        assert f'id="{box}"' in picks, box
    # 合言葉の欄は最初は出さず、小さな入口だけ（押すか、合言葉なしで送ると欄が出る）
    assert re.search(r'<button[^>]*id="gate-open"[^>]*hidden', html)
    assert 'show("gate-open");' in js and 'if (h.passphrase && !aikotoba && !keptAtLoad) show("gate");' not in js
    # 見取り図や /city/ の頁から都道府県を選んだときは、畳んだ欄を開いてから市区町村の欄へ移る
    pick_from_map = re.search(r"^function pickFromMap\(.*?^\}", js, re.S | re.M).group(0)
    assert pick_from_map.index('$("pick-more").open = true;') < pick_from_map.index('$("city").scrollIntoView')


def test_flow_after_sending_reads_as_a_thread_of_bubbles():
    """送った後の流れ（読み取り → 問い → 答え）を、吹き出しのやりとりで見せる（T-126b。11/5 の柴田の気づき「タイムライン式」）。
    あなたの文と答え＝右、とどくが読んだこと・問い＝左。答えた問いと押した答えを、今の問いの上に積む。
    問いの中身・順・判定は変えない（積むのは画面だけで、/api/step には送らない）。見本 4 件の通しは $OPS/tools/shot_t126b.py。
    """
    import re
    from pathlib import Path
    html = client.get("/").text
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert 'class="said bubble you" id="said"' in html
    assert '<div class="bubble bot"><ul id="extracted-list"' in html
    ask = re.search(r'<section id="ask"[^>]*>(.*?)</section>', html, re.S).group(1)
    # 積んだやりとりは今の問いより上。押せる選択肢（#ask-options）とは別の欄にする（台本の通しは #ask-options の中を押す）
    assert ask.index('id="thread"') < ask.index('id="ask-text"') < ask.index('id="ask-options"')
    assert re.search(r'<ol id="thread"[^>]*hidden', ask)
    assert "thread: []" in re.search(r"^function fresh\(\).*?^\}", js, re.S | re.M).group(0)  # 相談ごとに空から
    # 積むのは聞き返しの問いだけ（結果の画面で後から答えた分は積まない）。押した答えの文字は選択肢の値から引く
    assert "(later ? state.thread : [...state.thread, { q: askText(q), a }])" in js
    assert re.search(r"thread: withSaid\(q, answerLabel\(q, value\), later\)", js)
    assert "answerPlace(code, false, q)" in js and "answerPlace(code, true)" in js
    step = re.search(r"^async function step\(.*?^\}", js, re.S | re.M).group(0)
    assert "thread" not in step.split("api(\"/api/step\"")[1].split("});")[0]  # サーバへは送らない
    # 読み上げでは、吹き出しの左右の代わりに「質問：」「あなたの答え：」を添える
    render = re.search(r"^function renderThread\(\).*?^\}", js, re.S | re.M).group(0)
    assert '"質問："' in render and '"あなたの答え："' in render


@pytest.mark.usefixtures("on_fly")
def test_rate_limit_counts_each_client_ip(monkeypatch):
    from todoku import app as app_mod

    monkeypatch.setattr(app_mod, "RATE_LIMIT", 1)
    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    send = lambda ip: client.post("/api/consult", json={"text": DEMO}, headers={"fly-client-ip": ip}).status_code
    assert send("203.0.113.1") == 200
    assert send("203.0.113.1") == 429
    assert send("203.0.113.2") == 200  # 中継が同じでも、別の人は別に数える


@pytest.mark.usefixtures("on_fly")
def test_rate_limit_counts_ipv6_per_64(monkeypatch):
    from todoku import app as app_mod

    # IPv6 は 1 人で /64 を持てるので、アドレスを変えながら送ると接続元ごとの上限を外せていた（T-041）。/64 ごとに数える
    monkeypatch.setattr(app_mod, "RATE_LIMIT", 1)
    send = lambda ip: client.post("/api/consult", json={"text": DEMO}, headers={"fly-client-ip": ip}).status_code
    assert send("2001:db8:1:2::1") == 200
    assert send("2001:db8:1:2:ffff::9") == 429  # 同じ /64 の別のアドレス
    assert send("2001:db8:1:3::1") == 200  # 隣の /64 は別に数える
    assert send("::ffff:203.0.113.1") == 200  # IPv4 を IPv6 の形で書いたものは IPv4 として数え、::/64 にまとめない
    assert send("::ffff:203.0.113.2") == 200
    assert send("203.0.113.1") == 429  # 同じ IPv4 を普通の形で書いても同じ接続元
    assert send("fe80::1%eth0") == 200  # 範囲の印つきでも 500 にならない
    assert sorted(app_mod._hits) == ["2001:db8:1:2::/64", "2001:db8:1:3::/64", "203.0.113.1", "203.0.113.2", "fe80::/64"]


def test_rate_limit_ignores_fly_client_ip_off_fly(monkeypatch):
    from todoku import app as app_mod

    # Fly の外（FLY_APP_NAME がない）では、Fly-Client-IP を付けて送るだけで接続元ごとの上限を外せていた（T-042）。
    # ヘッダを見ず、接続そのものの相手（TestClient では testclient）で数える
    monkeypatch.setattr(app_mod, "ON_FLY", False)  # Fly の外の形。FLY_APP_NAME がある所で流しても同じに試す（Fly の上の形は on_fly の fixture）
    monkeypatch.setattr(app_mod, "RATE_LIMIT", 1)
    send = lambda headers: client.post("/api/consult", json={"text": DEMO}, headers=headers).status_code
    assert send({"fly-client-ip": "203.0.113.1"}) == 200
    assert send({"fly-client-ip": "203.0.113.2"}) == 429  # ヘッダを変えても同じ接続元
    assert send({"fly-client-ip": "2001:db8:1:2::1"}) == 429
    assert send({}) == 429
    assert sorted(app_mod._hits) == ["testclient"]


@pytest.mark.usefixtures("on_fly")
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


@pytest.mark.usefixtures("on_fly")
def test_rate_limit_forgets_idle_client_ips():
    import time

    from todoku import app as app_mod

    # 接続元を変えながら送ると、来なくなった接続元の行が _hits に残り続けていた（T-037）。窓から外れて空になった行は消す
    old = time.time() - app_mod.WINDOW - 1
    for i in range(50):
        app_mod._hits[f"198.51.100.{i}"].append(old)
    app_mod._hits["203.0.113.8"].append(time.time())  # 窓の中に記録がある接続元は残す
    assert client.post("/api/consult", json={"text": DEMO}, headers={"fly-client-ip": "203.0.113.9"}).status_code == 200
    assert sorted(app_mod._hits) == ["203.0.113.8", "203.0.113.9"]
    assert len(app_mod._hits["203.0.113.8"]) == 1  # 消さなかった行の数は変えない


@pytest.mark.usefixtures("on_fly")
def test_step_limit_counts_each_client_ip_apart_from_claude(monkeypatch):
    from todoku import app as app_mod

    # 聞き返し（/api/step）は合言葉なしで CPU を使うので、送り続けるとほかの人の画面が遅くなっていた（11/3 の安全点検の 7、T-051）。
    # 接続元ごとのゆるい上限を掛ける。Claude を呼ぶ口の回数とは別に数え、互いに減らさない
    monkeypatch.setattr(app_mod, "STEP_LIMIT", 2)
    step = lambda ip: client.post("/api/step", json={"facts": {}}, headers={"fly-client-ip": ip}).status_code
    assert [step("203.0.113.1"), step("203.0.113.1"), step("203.0.113.1")] == [200, 200, 429]
    r = client.post("/api/step", json={"facts": {}}, headers={"fly-client-ip": "203.0.113.1"})
    assert r.json()["detail"] == "しばらく時間をおいてから試してください"  # 画面は detail を問いの欄に出す
    assert step("2001:db8:1:2::1") == 200  # 別の人は別に数える。IPv6 は /64 ごと（Claude を呼ぶ口と同じ決め方）
    assert step("2001:db8:1:2::ffff") == 200
    assert step("2001:db8:1:2::2") == 429
    assert len(app_mod._hits) == 0 and len(app_mod._hits_all) == 0  # Claude を呼ぶ口の回数は減らさない
    assert client.post("/api/consult", json={"text": DEMO}, headers={"fly-client-ip": "203.0.113.1"}).status_code == 200
    assert len(app_mod._step_hits["203.0.113.1"]) == 2  # 読み取りは聞き返しの数に入れない（断った回も積まない）


def test_step_limit_ignores_fly_client_ip_off_fly(monkeypatch):
    from todoku import app as app_mod

    # Fly の外ではヘッダを見ない（T-042 と同じ決め方）。付けて送るだけで上限を外せないこと
    monkeypatch.setattr(app_mod, "ON_FLY", False)
    monkeypatch.setattr(app_mod, "STEP_LIMIT", 1)
    step = lambda ip: client.post("/api/step", json={"facts": {}}, headers={"fly-client-ip": ip}).status_code
    assert [step("203.0.113.1"), step("203.0.113.2")] == [200, 429]
    assert sorted(app_mod._step_hits) == ["testclient"]


@pytest.mark.usefixtures("on_fly")
def test_step_limit_window_and_idle_rows(monkeypatch):
    import time

    from todoku import app as app_mod

    # 窓（STEP_WINDOW 秒）より前の回は数えない。来なくなった接続元の行は、窓ごとの見回しで消す
    monkeypatch.setattr(app_mod, "STEP_LIMIT", 1)
    old = time.time() - app_mod.STEP_WINDOW - 1
    app_mod._step_hits["203.0.113.1"].append(old)
    for i in range(50):
        app_mod._step_hits[f"198.51.100.{i}"].append(old)
    app_mod._step_hits["203.0.113.8"].append(time.time())  # 窓の中に記録がある接続元は残す
    step = lambda ip: client.post("/api/step", json={"facts": {}}, headers={"fly-client-ip": ip}).status_code
    assert step("203.0.113.1") == 200  # 窓の外の 1 回は数えないので、上限 1 でも通る
    assert sorted(app_mod._step_hits) == ["203.0.113.1", "203.0.113.8"]
    assert list(app_mod._step_hits["203.0.113.1"]) != [old] and len(app_mod._step_hits["203.0.113.1"]) == 1
    assert step("203.0.113.8") == 429  # 窓の中の 1 回は数える
    # 見回しのすぐ後（見回さない間）でも、窓の外の回は数えない（Claude を呼ぶ口の 10 分の窓で数えない）
    monkeypatch.setattr(app_mod, "_step_swept", time.time())
    app_mod._step_hits["203.0.113.2"].append(old)
    assert step("203.0.113.2") == 200


@pytest.mark.usefixtures("on_fly")
def test_rate_limit_survives_concurrent_requests(monkeypatch):
    import threading
    import time

    from starlette.requests import Request

    from todoku import app as app_mod

    # 口は同期の関数でスレッドで動く。2 つが同時に recent() に入ると、両方が同じ古い記録を見て popleft し、
    # 2 つ目が IndexError（500）になるか、1 つ目が積んだ記録を消して数え漏らしていた（T-039）
    class SlowDeque(app_mod.deque):
        def __getitem__(self, i):
            v = super().__getitem__(i)
            time.sleep(0.05)  # 読んだ後に止まり、別のスレッドを先に進ませる（読む前に止まると、鍵がなくても当たらない）
            return v

    monkeypatch.setattr(app_mod, "_hits_all", SlowDeque([time.time() - app_mod.WINDOW - 1]))
    barrier = threading.Barrier(2)
    errors = []

    def send(ip):
        req = Request({"type": "http", "method": "POST", "path": "/api/consult",
                       "headers": [(b"fly-client-ip", ip.encode())], "client": ("127.0.0.1", 1)})
        barrier.wait()
        try:
            app_mod.guard(req, costly=True)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=send, args=(f"203.0.113.{n}",)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert errors == []
    assert len(app_mod._hits_all) == 2  # 古い記録は消え、通った 2 回はどちらも残る


@pytest.mark.usefixtures("on_fly")
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


@pytest.mark.usefixtures("on_fly")
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
    func = re.search(r"^function changeText\([^)]*\) \{\n.*?^\}\n", js, re.S | re.M).group(0)
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
const said = [], say = (t) => {{ said.push(t); }};  // いつもある読み上げの領域 #status（T-030）
const el = (tag, attrs, text) => text;
const fresh = () => ({{}});
const clearBoard = () => {{}};
const forgetKeep = () => {{}};  // 結果を残すリンクをアドレス欄から外す（T-072）。動きは test_keep とブラウザで見る
const document = {{ activeElement: null, body: {{}} }};  // 失敗したら押したボタンへ戻す（T-062）。動きは test_focus_returns_… とブラウザで見る
const backTo = () => {{}};
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
  console.log(JSON.stringify({{ ok, failed, broke, said }}));
}})();
"""
    got = json.loads(subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout)
    # 読み取ったことの欄を出す時も、送る時（render）も、読み取り中の札はもう消えている
    assert got["ok"] == {"seen": [["extracted", True], ["render", True]], "reading": True}
    # 読み取りに失敗したときは、札に知らせを出して見せる（受け取った後に落ちたときも）
    assert got["failed"] == {"reading": False, "msg": ["通信できませんでした"]}
    assert got["broke"] == {"reading": False, "msg": ["落ちた"]}
    # 読み上げには #status で、読み取りの間は「読み取っています…」、読み取れたら空、失敗したらその知らせを伝える（T-030）
    assert got["said"] == ["読み取っています…", "", "読み取っています…", "通信できませんでした", "読み取っています…", "", "落ちた"]


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


def test_real_claude_without_passphrase_closes_claude_routes(monkeypatch):
    # 鍵があるのに合言葉がない（設定し忘れ）ときは、Claude を呼ぶ口を 503 で閉じ、見本と判定し直しは動かす
    from todoku import app as app_mod

    class RealLike:
        name = "claude"

        def extract(self, rules, text):
            raise AssertionError("合言葉なしで Claude を呼んだ")

        def summarize(self, rules, subject, shown):
            raise AssertionError("合言葉なしで Claude を呼んだ")

    monkeypatch.setattr(app_mod, "CLAUDE", RealLike())
    monkeypatch.setattr(app_mod, "PASSPHRASE", "")
    res = client.post("/api/consult", json={"text": DEMO})
    assert res.status_code == 503 and "合言葉" in res.json()["detail"]
    assert client.post("/api/summary", json={"facts": {}}).status_code == 503
    assert len(app_mod._hits_all) == 0 and not any(app_mod._hits.values())  # 断った回は数えない
    assert client.post("/api/step", json={"facts": {}}).status_code == 200
    sample_id = client.get("/api/samples").json()[0]["id"]
    assert client.post("/api/sample", json={"id": sample_id}).status_code == 200
    # 合言葉を設定すれば、いつもどおり合言葉を求める
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    assert client.post("/api/consult", json={"text": DEMO}).status_code == 401


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
    # その市・都以外と分かったら、住所だけで外れる制度を「当てはまらない」に並べない。
    # T-122：外した地域の名前と数は返さず（「町田市にお住まいなら…も判定します」をやめた）、自分の地域で確かめられる支えの数を返す
    facts = {"age_band": "age_75_plus", "residence": "home", "uses_diapers": True, "care_level": "care4"}
    def step(code):
        return client.post("/api/step", json={"facts": facts, "municipality": code}).json()
    yokohama, machida, osaka, nowhere = step("141003"), step("132098"), step("271004"), step("")
    village = step("013048")  # 北海道新篠津村
    assert not [r for r in yokohama["results"] if r["id"].startswith(("machida-", "tokyo-"))]
    assert not [r for r in machida["results"] if r["id"].startswith("yokohama-")]
    assert sum(yokohama["counts"].values()) == len(yokohama["results"])
    for d in (yokohama, machida, osaka, nowhere, village):
        assert "hidden_places" not in d
    # 数は /city/ の頁の帯と同じ数え方で、結果の札の数とそろう
    for d in (yokohama, machida, osaka, village):
        assert d["region"]["total"] == len(d["results"]) == sum(x["n"] for x in d["region"]["parts"])
    assert osaka["region"]["parts"] == [{"label": "国の制度", "n": 17, "unit": "件"},
                                        {"label": "市区町村の事業", "n": 5, "unit": "種類"}]
    # その市・村の公式頁から型が書けたら（lane-b の T-131 など）、自分の「市の事業」「村の事業」だけは入ってよい
    assert osaka["region"]["detailed"] in ([], ["市の事業"]) and village["region"]["detailed"] in ([], ["村の事業"])
    # 住んでいない地域の名前は、どの欄にも出さない（0 件の段も出さない）
    assert not any(w in str(osaka["region"]) for w in ("町田", "横浜", "東京", "0 件"))
    assert machida["region"]["detailed"] == ["都の制度", "市の制度"] and any(r["id"].startswith("machida-") for r in machida["results"])
    assert yokohama["region"]["detailed"] == ["市の制度"] and any(r["id"].startswith("yokohama-") for r in yokohama["results"])
    # 住所が分からない間は今までどおり、市・都の制度も型も出す（地域の数は出さない）
    assert nowhere["region"] is None and len(nowhere["results"]) == 40
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "web"
    script = (root / "app.js").read_text(encoding="utf-8")
    page = (root / "index.html").read_text(encoding="utf-8")
    assert "data.region" in script and "hidden_places" not in script and "お住まいなら" not in script
    # 最初の画面にも、特定の地域なら詳しいと読める 1 行を出さない
    assert "お住まいなら" not in page and "東京都・横浜市・町田市" not in page


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
    # 市区町村を選べば、もう聞かない。住所だけで外れる横浜市・東京都の制度は出さず、名前も数も返さない（T-122）
    data = client.post("/api/step", json={"facts": facts, "subject": "お母さま", "asked": asked + 1,
                                          "municipality": "271004"}).json()
    assert data["question"] is None or data["question"]["fact"] not in places
    assert not [r for r in data["results"] if r["id"].startswith("yokohama-") or r["id"] == "tokyo-silver-pass"]
    assert "hidden_places" not in data and not any(w in str(data["region"]) for w in ("横浜", "東京", "町田"))
    assert data["region"]["total"] == len(data["results"])
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
    # 認定の問いにも主語を書き、相談文の呼び方に置き換える（指摘 P-6。見本「道に迷う父」では最初の問いになる）
    # T-117：見本の読み直し（0533d97）で「退院した母」は care_level=none を確かさ低で読むようになり、
    # 最初の問いが確かめの問いに変わったので、選ぶ問いを見る見本を「道に迷う父」に替えた
    start = client.post("/api/sample", json={"id": "mayou-chichi"}).json()
    q = start["question"]
    assert start["subject"] == "お父さま" and q["fact"] == "care_level" and q["confirm"] is False
    assert q["text"].startswith("お父さまは、") and "ご本人" not in q["text"]
    # 「わからない」と答えた後の「まだ確かめていないこと」の問いも同じ呼び方
    after = client.post("/api/step", json={"facts": {**start["facts"], "care_level": DONT_KNOW},
                                           "suggestions": start["suggestions"], "subject": start["subject"],
                                           "asked": 1}).json()
    missing = [m["question"] for r in after["results"] for m in r["missing"] if m["fact"] == "care_level"]
    assert missing and all(m.startswith("お父さまは、") for m in missing)
    # 確かめの問いも相談文の呼び方で出る（「退院した母」の最初の問い。「はい」で受けていない、「いいえ」で選び直す）
    haha = client.post("/api/sample", json={"id": "taiin-haha"}).json()
    hq = haha["question"]
    assert haha["subject"] == "お母さま" and hq["fact"] == "care_level" and hq["confirm"] is True
    assert hq["text"].startswith("お母さまは") and "ご本人" not in hq["text"]
    assert [o["value"] for o in hq["options"]] == ["none", "__not__"]


# ご本人のことを聞くのに主語が無かった問い（T-025）。介護をする家族に聞く問い（caregiver_employee・fire_risk）と、
# 家の工事や福祉用具を考えているかの問いは、聞く相手が家族なので主語を書かない
# shortstay_or_facility は fix/gold-rules が「予定の事実」として外したので、T-070 で一覧から外した
PERSON_FACTS = ("uses_care_services", "high_medical_costs",
                "hearing_certifiable", "incontinence_bedding")


def test_person_questions_name_the_person():
    for fid in PERSON_FACTS:
        assert rules_mod.load().facts[fid].question.startswith("ご本人は、"), fid
    # 見本「退院した母」のデモの道：認定を「受けていない」と答えると、2 問目は介助の要る場面の問い（fix/gold-rules の裁定 p-prereq の後。
    # T-070 までは医療費の問いだった）で、相談文の呼び方で出る
    start = client.post("/api/sample", json={"id": "taiin-haha"}).json()
    assert start["subject"] == "お母さま" and start["question"]["fact"] == "care_level"
    second = client.post("/api/step", json={"facts": {**start["facts"], "care_level": "none"},
                                            "suggestions": start["suggestions"], "subject": start["subject"],
                                            "asked": 1}).json()["question"]
    assert second["fact"] == "needs_daily_help"
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


def test_read_facts_do_not_carry_unpersonalized_questions():
    # 読み取ったこと（extracted）と分かっていること（known）は名前と値だけを返す。問いの文を持たせると、
    # 相談文の呼び方に置き換えないまま「ご本人は、…」が API に出ていたため（T-027。画面はこの欄を使っていなかった）
    for s in client.get("/api/samples").json():
        data = client.post("/api/sample", json={"id": s["id"]}).json()
        assert data["subject"] != "ご本人" and data["extracted"] and data["known"], s["id"]
        for f in data["extracted"]:
            assert set(f) == {"fact", "name", "value", "label", "confidence", "evidence"}, (s["id"], f)
        for f in data["known"]:
            assert set(f) == {"fact", "name", "value", "label"}, (s["id"], f)
        assert "ご本人" not in json.dumps(data["extracted"] + data["known"], ensure_ascii=False), s["id"]


def test_unread_facts_can_be_answered_on_the_result():
    # 相談の文から読み取れなかった事実（生活保護など）にも、結果の画面で答えられる（T-014）
    facts = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home", "care_level": "care4",
             "uses_diapers": DONT_KNOW}
    suggestions = {"household": "alone"}
    asking = client.post("/api/step", json={"facts": facts, "suggestions": suggestions}).json()
    assert asking["question"] is not None and asking["unread"] == []  # 聞き返しの間は出さない
    body = {"facts": facts, "suggestions": suggestions, "subject": "おばあさま", "asked": 99}
    done = client.post("/api/step", json=body).json()
    assert done["question"] is None
    unread = [q["fact"] for q in done["unread"]]
    assert "on_welfare" in unread
    # 分かっている事実・「わからない」と答えた事実・確信の低い読み取り（unconfirmed で聞く）は入れない
    assert not set(unread) & (set(facts) | set(suggestions)) and len(unread) == len(set(unread))
    assert [q["fact"] for q in done["unconfirmed"]] == ["household"]
    shown = {r["id"] for r in done["results"]}
    for q in done["unread"]:
        assert q["affects_ids"] and set(q["affects_ids"]) <= shown
        assert "ご本人" not in q["text"]
    welfare = next(q for q in done["unread"] if q["fact"] == "on_welfare")
    assert welfare["text"] == "おばあさまは生活保護を受けていますか？"
    # 答えても聞き返しの数は増えず、答えた事実は一覧から消える
    again = client.post("/api/step", json={**body, "facts": {**facts, "on_welfare": False}}).json()
    assert again["question"] is None
    assert "on_welfare" not in [q["fact"] for q in again["unread"]]


def test_tap_targets_are_at_least_44px():
    # 高齢の方がスマホで押し違えないよう、押す所は高さ 44px 以上にする（T-029。実際の大きさはブラウザで測った）。
    # 選ぶ札・文字のボタン・畳む見出し・ページ下のリンクは min-height か上下の余白で、文の中の電話番号は
    # 行の高さを変えずに ::after で押せる範囲だけを上下に広げる。大きいボタン（.primary など）は元から 56px。
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")

    def rule(selector):
        m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", css, re.M)
        assert m, selector
        return m.group(1)

    for sel in (".chip", ".link", ".foot a", ".primary, .secondary, .option", "select"):
        h = re.search(r"min-height: (\d+)px", rule(sel))
        assert h and int(h.group(1)) >= 44, sel
    # 「確かめ方」などの畳む見出し：文字 15px × 行の高さ 1.75 ＋ 上下の余白 9px で 44px
    assert "padding: 9px 0" in rule("details summary")
    assert "position: relative" in rule(".tel")
    assert "inset: -14px 0" in rule(".tel::after")


def test_select_draws_its_own_look_so_safari_keeps_48px():
    # 上の min-height の検査は CSS の文を読むだけ。Safari（WebKit）は既定の見た目の select に min-height を効かせず、
    # 高さ 23px で描いていた（T-064。Playwright の WebKit で測った）。appearance: none で自分で描き、
    # 都道府県を選ぶまで押せない #city（disabled）でも矢印が消えないよう、背景は background-color で塗る。
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")
    body = re.search(r"^select \{([^}]*)\}", css, re.M).group(1)
    assert "-webkit-appearance: none" in body and " appearance: none" in body
    assert "background-image: linear-gradient" in body
    disabled = re.search(r"^select:disabled \{([^}]*)\}", css, re.M).group(1)
    assert "background:" not in disabled and "background-color:" in disabled


def test_small_text_is_at_least_14px():
    # 帯の注と結果の札（pill・tag）は 14px 以上（T-031。ブラウザで測ると、結果のカードの高さは 390 で 8 枚の合計 +6px、
    # 1280 で 0px）。帯の列の名前は D-0012 で聞いている（B が 14px。A なら下の .col-head の 1 行を 11 に戻す）。
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web"
    css = (root / "style.css").read_text(encoding="utf-8")

    def size(selector):
        m = re.search(r"^" + re.escape(selector) + r" \{[^}]*font-size: (\d+)px", css, re.M)
        assert m, selector
        return int(m.group(1))

    for sel in (".board-card .note", ".tag", ".pill"):
        assert size(sel) >= 14, sel
    assert size(".col-head") >= 14
    # 14px では 390 の 4 列で 4 字までしか並ばないので、列の名前は言葉の切れ目でだけ折り返す
    assert "display: inline-block" in re.search(r"^\.col-head \.ph \{([^}]*)\}", css, re.M).group(1)
    js = (root / "app.js").read_text(encoding="utf-8")
    board = re.search(r"const BOARD = \[(.*?)\n\];", js, re.S).group(1)
    rows = re.findall(r'\["(\w+)", "([^"]+)", "[^"]+", \[([^\]]*)\]\]', board)
    assert len(rows) == 4
    for key, label, parts in rows:
        assert "".join(re.findall(r'"([^"]+)"', parts)) == label, key
    assert 'parts.map((t) => el("span", { class: "ph" }, t))' in js


def test_screen_reader_status_and_focus_after_buttons_vanish():
    # 読み上げとキーボード（T-030。動きはブラウザで確かめた。VoiceOver の実機では確かめていない）。
    # (1) 押したボタンが消える所（結果の画面が出たとき・「まだ確かめていないこと」に答えたとき・「ほか N 件」）で
    #     フォーカスを body に落とさず、次に読む所へ移す。
    # (2) 問いが変わるたびに選択肢まで読み上げないよう、#ask は aria-live にせず、問いの文へのフォーカスの移動で伝える。
    # (3) 「読み取っています…」は、hidden を外すのと同時に出す領域ではなく、いつもある role=status の領域で伝える。
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")

    def tag(id_):
        m = re.search(r'<[a-z]+ id="' + id_ + r'"[^>]*>', html)
        assert m, id_
        return m.group(0)

    assert "aria-live" not in tag("ask") and "aria-live" not in tag("reading")
    status = tag("status")
    assert 'role="status"' in status and "hidden" not in status
    # どの section の中でもなく main の直下に置く（section ごと hidden になると、読み上げの領域として働かない）
    assert re.search(r'<main class="wrap">\s*(<!--[^>]*-->\s*)?<p id="status"', html)
    sr = re.search(r"^\.sr-only \{([^}]*)\}", css, re.M)
    assert sr and "display: none" not in sr.group(1) and "visibility" not in sr.group(1)
    assert 'say("読み取っています…")' in js and "say(e.message)" in js
    # 結果の画面が出たら先頭の見出しへ、答えたら同じ位置に来た問いへ、「ほか N 件」は開いた中身へ
    assert "focusOn(resultHeading())" in js
    assert "state.laterAt = laterSpots().indexOf(" in js
    assert 'focusOn(rest.querySelector("h3"))' in js and 'focusOn(rest.querySelector("summary"))' in js
    assert "more.remove(); }" not in js  # 消すだけで、フォーカスを移さないボタンを残さない


def test_focus_returns_to_pressed_button_after_failed_send():
    # 送る間は押したボタンを止めるので、Chrome ではフォーカスが body に落ちる。成功すれば次の問い・結果の見出しへ移すが、
    # 失敗してボタンを戻したときは body のままで、キーボード・読み上げの人は先頭から辿り直していた（T-062。動きはブラウザで確かめた）。
    # 失敗したら、止めたボタンを戻した後で押したボタンへ戻す。合言葉の欄へ移したときなど、body でなければ動かさない。
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")

    def body(name):
        m = re.search(r"^(?:async )?function " + name + r"\(.*?^\}", js, re.M | re.S)
        assert m, name
        return m.group(0)

    back = body("backTo")
    assert "document.activeElement === document.body" in back and "node !== document.body" in back
    # 問いの欄・結果の画面（step）：押したボタンを覚えてから止め、失敗なら止めたボタンを戻した後で戻す
    step = body("step")
    assert step.index("const pressed = document.activeElement") < step.index("setBusy(true)")
    fin = step[step.index("} finally {"):]
    assert fin.index("setBusy(false)") < fin.index("backTo(pressed)")
    # 相談を送るボタン（consultWith）
    consult = body("consultWith")
    assert consult.index("const pressed = document.activeElement") < consult.index('$("send").disabled = true')
    err = consult[consult.index("} catch (e) {"):]
    assert err.index('$("send").disabled = false') < err.index("backTo(pressed)") < err.index("return;")


def test_focus_moves_on_after_aikotoba_is_used():
    # 「合言葉を使う」（Enter も同じ）は合言葉の欄ごと隠すので、押したボタン・欄からフォーカスが body に落ちていた（T-063。動きはブラウザで確かめた）。
    # 送り直すなら送るボタンへ移してから送る（consultWith がそれを押したボタンとして覚え、失敗したらそこへ戻す）。書く前なら相談の欄へ。
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    handler = re.search(r'^\$\("gate-ok"\)\.addEventListener\("click", \(\) => \{\n.*?^\}\);', js, re.M | re.S).group(0)
    after = handler[handler.index('show("gate", false);'):]
    resend = after.index("if (resendAfterGate)")
    assert resend < after.index('$("send").focus();') < after.index("send();")
    assert after.index("send();") < after.index('else $("text").focus();')
    # 合言葉の欄の Enter は既定の動きを止めてから押す。止めないと、keydown の中で移した相談の欄に改行が 1 つ入っていた
    enter = re.search(r'^\$\("aikotoba"\)\.addEventListener\("keydown", .*$', js, re.M).group(0)
    assert enter.index("ev.preventDefault();") < enter.index('$("gate-ok").click();')


def test_one_phone_number_per_paragraph():
    # 電話番号の押せる範囲は ::after で上下 14px 広げている（T-029）。1 つの段落で続く行に番号が並ぶと、行の間隔（約 30px）
    # より範囲が広く、上の番号の少し下を押すと下の番号に電話がかかった（390px の高額療養費・高額介護合算療養費。T-032）。
    # 画面は「／」で区切って 1 件ずつ段落に分けるので、規則の phone は「／」で区切った 1 件に番号が 1 つまでにする。
    import re
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    pattern = re.search(r"text\.matchAll\(/(.+?)/g\)", js).group(1)  # telLinks が番号と見なす形
    phones = {}
    for f in sorted((root / "rules" / "programs").glob("*.yaml")):
        phone = (yaml.safe_load(f.read_text(encoding="utf-8")).get("apply") or {}).get("phone")
        if phone:
            phones[f.stem] = phone
    split = {k: v.split("／") for k, v in phones.items()}
    assert all(len(re.findall(pattern, part)) <= 1 for parts in split.values() for part in parts), split
    assert len(split["kogaku-gassan"]) == 3 and len(split["kogaku-ryoyohi"]) == 2  # 分けた後も 1 件ずつに番号が残る
    assert all(re.search(pattern, part) for k in ("kogaku-gassan", "kogaku-ryoyohi") for part in split[k])
    # カードは phoneViews で「／」ごとに段落を作り、番号の並ぶ文字列をそのまま 1 つの段落に入れない
    assert re.search(r'^function phoneViews\(phone\) \{\n\s+return phone\.split\("／"\)\.map\(\(part\) => el\("p", \{\}, "電話：", telLinks\(part\)\)\);', js, re.M)
    assert "...phoneViews(r.apply.phone)" in js
    assert "telLinks(r.apply.phone)" not in js


def test_gate_sits_below_samples_and_is_not_the_loudest_button():
    # 最初の画面で、合言葉の欄を見本より上の、いちばん目立つ所に出さない（T-044）。
    # 審査などで合言葉を持たない人は見本しか通れないので、見本のボタンを合言葉の欄より上に置き、
    # 塗りの大きいボタン（.primary）を見本より上に置かない。欄は相談の文の前に残す（書き始める前に要ると分かるように）。
    # 位置は 1280・390 の画面でも測った（$OPS/shots/T-044-*）。
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")

    # T-124（11/5 21 時半の柴田の気づき「相談の文をいちばん上に置く」）で、相談の欄と「相談する」を見本より上に移した。
    # 見本より上の塗りのボタンは「相談する」の 1 つだけ。合言葉の欄は見本より下に残す（最初の画面でいちばん目立たせない）
    at = {k: html.index(f'id="{k}"') for k in ("input", "text", "send", "events", "samples", "sample-list", "gate")}
    assert at["input"] < at["text"] < at["send"] < at["events"] < at["samples"] < at["sample-list"] < at["gate"]
    gate_ok = re.search(r'<button[^>]*id="gate-ok"[^>]*>', html).group(0)
    assert "primary" not in gate_ok
    assert html[: at["samples"]].count('class="primary"') == 1
    # 見本は合言葉の欄より上にあるので、「下の見本」と書かない
    assert "下の見本" not in html and "下の見本" not in js


def test_print_puts_cards_on_paper_and_leaves_controls_out():
    # 結果の画面を印刷すると、畳んだ札（details）の中身が紙に出ず、入力の欄・見本・合言葉の欄が 4 ページ刷られていた
    # （T-066。Chrome の page.pdf で、申請先・持ち物・出典の見出しが 0 件）。beforeprint で札を開き、afterprint で
    # 開く前に戻す。入力・問い・帯・ボタンは結果の画面のときだけ（html.print-result）紙から外す。
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web"
    css = (root / "style.css").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    m = re.search(r"^@media print \{\n(.*?)^\}", css, re.M | re.S)
    assert m, "@media print"
    block = m.group(1)
    for sel in ("#input", "#board", "#ask", "#later", "#again"):
        assert f".print-result {sel}" in block, sel
    assert ".print-result #result button:not(.link) { display: none; }" in block
    assert ".prog > summary { break-inside: avoid; }" in block
    before = re.search(r'addEventListener\("beforeprint", \(\) => \{(.*?)\n\}\);', js, re.S).group(1)
    after = re.search(r'addEventListener\("afterprint", \(\) => \{(.*?)\n\}\);', js, re.S).group(1)
    assert 'classList.toggle("print-result"' in before and 'querySelectorAll("details:not([open])")' in before
    assert "d.open = true" in before
    assert "d.open = false" in after and 'classList.remove("print-result")' in after


def test_board_while_answering_is_one_row_of_counts_and_one_line_note():
    # T-136：問いに答えている間（.live）の帯は、4 つの数の 1 行＋注 1 行。点は出さない（DOM には残す）。
    # 390 で 209px（画面の 25%）を占め、前の問いを隠していた。高さ・行数・点の見え方はブラウザで確かめた（ops の shot_t136.py）
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web"
    css = (root / "style.css").read_text(encoding="utf-8")

    def rule(selector):
        m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", css, re.M)
        assert m, selector
        return m.group(1)

    assert "display: none" in rule(".board-card.live .tiles")
    assert "display: flex" in rule(".board-card.live .board")
    assert "flex-wrap: wrap" not in rule(".board-card.live .board")  # 折り返すのは 370px より狭い画面だけ
    assert "white-space: nowrap" in rule(".board-card.live .col-head .ph")  # 名前は言葉の切れ目でだけ折る
    one_line = rule(".board-card.live .change, .board-card.live .note")
    for decl in ("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis"):
        assert decl in one_line, decl
    html = (root / "index.html").read_text(encoding="utf-8")
    note = re.search(r'<p class="note"[^>]*>([^<]*)</p>\n  </section>\n\n  <section id="ask"', html).group(1)
    assert note == "答えるたびに、規則のプログラムが判定し直します（AI は使いません）。"  # 点を出さないので「点は制度 1 つずつ」は消した
    js = (root / "app.js").read_text(encoding="utf-8")
    assert '$("board-change").title = $("board-change").textContent;' in js  # 切った全文は title にも
