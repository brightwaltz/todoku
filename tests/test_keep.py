"""結果を残すリンク（T-072）。答えを URL の # より後ろに入れ、開き直すと /api/step だけで同じ結果になる。"""

import json
import os
import shutil
import subprocess

import pytest

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku import events as events_mod  # noqa: E402
from todoku.app import app  # noqa: E402

client = TestClient(app)
KEEP_JS = app_mod.WEB_DIR / "keep.js"


def answer_to_result(event_id: str, municipality: str = "") -> tuple[dict, dict]:
    """出来事から始め、どの問いも 1 つ目の選択肢で答えて結果まで行く。最後に送った本文と応答を返す。"""
    data = client.post("/api/event", json={"id": event_id, "municipality": municipality}).json()
    body = {"facts": data["facts"], "suggestions": data["suggestions"], "subject": data["subject"],
            "asked": 0, "municipality": municipality}
    while data["question"] is not None:
        q = data["question"]
        body["facts"][q["fact"]] = q["keep"]["value"] if q.get("keep") else q["options"][0]["value"]
        body["suggestions"].pop(q["fact"], None)
        body["asked"] += 1
        data = client.post("/api/step", json=body).json()
    return body, data


@pytest.mark.parametrize("municipality", ["", "132098"])
def test_reopening_the_link_gives_the_same_result(municipality):
    """リンクに入れるのは呼び方・事実・確かめていない読み取り・市区町村だけ。開き直すと asked=99 で同じ結果になる。"""
    for e in events_mod.EVENTS:
        body, shown = answer_to_result(e["id"], municipality)
        saved = {"s": body["subject"], "f": body["facts"], "g": body["suggestions"], "m": body["municipality"]}
        reopened = client.post("/api/step", json={"facts": saved["f"], "suggestions": saved["g"], "subject": saved["s"],
                                                  "asked": 99, "municipality": saved["m"]}).json()
        assert reopened["question"] is None
        for key in ["results", "unconfirmed", "unread", "known", "region", "place", "counts"]:
            assert reopened[key] == shown[key], (e["id"], key)


def test_after_nintei_the_care_level_can_be_answered_again():
    """認定の申請が当てはまる人がリンクから開き、「認定が出た」を押すと、認定の区分を結果の画面で答え直せる。"""
    # needs_daily_help は、規則が「いまの様子」も見る版（fix/gold-rules）のため。その事実の無い版では clean_facts が落とす
    facts = {"age_band": "age_75_plus", "care_level": "none", "residence": "home", "needs_daily_help": True}
    before = client.post("/api/step", json={"facts": facts, "asked": 99}).json()
    nintei = next(r for r in before["results"] if r["id"] == "kaigo-nintei")
    assert nintei["status"] == "eligible"
    assert any(r["status"] == "after_prereq" for r in before["results"])
    # 画面の reaskNintei と同じく、認定の区分を事実から外して送り直す
    rest = {k: v for k, v in facts.items() if k != "care_level"}
    again = client.post("/api/step", json={"facts": rest, "asked": 99}).json()
    assert again["unread"] and again["unread"][0]["fact"] == "care_level"
    # 答えると、先に手続きが要った制度が判定し直される
    after = client.post("/api/step", json={"facts": {**rest, "care_level": "care2"}, "asked": 99}).json()
    assert next(r for r in after["results"] if r["id"] == "kaigo-nintei")["status"] == "done"
    # 認定が前提だった制度（先に手続きをすると使える）は、もう前の手続き待ちではなく、それぞれの条件で判定される
    waited = {r["id"] for r in before["results"] if r["status"] == "after_prereq"}
    now = {r["id"]: r["status"] for r in after["results"]}
    assert waited and all(now.get(pid) != "after_prereq" for pid in waited)


NODE_CHECK = r"""
const { encodeKeep, decodeKeep, KEEP_MAX } = require(process.argv[1]);
(async () => {
  const saved = { s: "お母さま", f: { age_band: "age_75_plus", care_level: "none", uses_diapers: true },
                  g: { household: "alone" }, m: "132098" };
  const code = await encodeKeep(saved);
  const out = { code, back: await decodeKeep(code) };
  // 縮めずに作った版 0 も読める
  out.plain = await decodeKeep("0." + Buffer.from(JSON.stringify(saved)).toString("base64url"));
  out.bad = [];
  for (const c of ["", "1.", "2.abc", "1.!!!", "0." + Buffer.from("[1]").toString("base64url"),
                   "0." + Buffer.from('{"s":1,"f":{},"g":{},"m":""}').toString("base64url"), "0." + "A".repeat(KEEP_MAX)]) {
    try { await decodeKeep(c); out.bad.push(false); } catch (e) { out.bad.push(true); }
  }
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_keep_code_round_trips_and_rejects_broken_links():
    run = subprocess.run(["node", "-e", NODE_CHECK, str(KEEP_JS)], capture_output=True, text=True, timeout=30, check=True)
    out = json.loads(run.stdout)
    saved = {"s": "お母さま", "f": {"age_band": "age_75_plus", "care_level": "none", "uses_diapers": True},
             "g": {"household": "alone"}, "m": "132098"}
    assert out["back"] == saved and out["plain"] == saved
    assert out["code"].startswith("1.")
    assert set(out["code"][2:]) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")  # URL にそのまま置ける
    assert len(out["code"]) < 200  # 短い符号
    assert all(out["bad"]), out["bad"]


def test_page_has_keep_slot_and_does_not_send_the_link():
    html = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
    css = (app_mod.WEB_DIR / "style.css").read_text(encoding="utf-8")
    assert 'id="keep"' in html and 'id="keep-url"' in html and 'id="keep-copy"' in html
    assert "認定が出たら、このリンクを開くと次の手続きが出ます。" in html
    # keep.js は app.js より先に読む（encodeKeep・decodeKeep を app.js が使う）
    assert html.index('src="/static/keep.js"') < html.index('src="/static/app.js"')
    assert client.get("/static/keep.js").status_code == 200
    # 開き直すときは規則だけで判定する /api/step に送る。リンクから開いた結果も、合言葉なしならまとめを送らない
    assert 'restoreFrom' in js and '"#r="' in js
    assert "(state.event || state.restored) && needGate && !aikotoba" in js
    # 紙にはリンクの欄を刷らない
    assert ".print-result #keep" in css
