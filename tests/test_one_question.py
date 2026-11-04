"""1 問だけ答える（T-072b）。最初の問いで選ぶと、答えるたびに止まり、その時点の札と「もう 1 問答える／ここでやめる」を出す。"""

import json
import os
import re
import shutil
import subprocess

import pytest

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku import events as events_mod  # noqa: E402
from todoku.app import app  # noqa: E402

client = TestClient(app)
JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")
CSS = (app_mod.WEB_DIR / "style.css").read_text(encoding="utf-8")


def first_value(q: dict):
    return q["keep"]["value"] if q.get("keep") else q["options"][0]["value"]


@pytest.mark.parametrize("event", [e["id"] for e in events_mod.EVENTS])
def test_stopping_after_one_answer_gives_the_same_judgement(event):
    """1 問答えて「ここでやめる」は asked=99 の /api/step（聞き返しをやめたのと同じ）。止まったときの札と結果の判定は同じ。"""
    data = client.post("/api/event", json={"id": event, "municipality": ""}).json()
    q = data["question"]
    assert q is not None, event
    facts = {**data["facts"], q["fact"]: first_value(q)}
    suggestions = {k: v for k, v in data["suggestions"].items() if k != q["fact"]}
    body = {"facts": facts, "suggestions": suggestions, "subject": data["subject"], "municipality": ""}
    paused = client.post("/api/step", json={**body, "asked": 1}).json()
    stopped = client.post("/api/step", json={**body, "asked": 99}).json()
    assert stopped["question"] is None
    # 止まったときの札（その時点の判定）と、やめた後の結果の判定は同じ（やめても判定は変わらない）
    assert [(r["id"], r["status"]) for r in paused["results"]] == [(r["id"], r["status"]) for r in stopped["results"]]
    # 開き直しは、リンクに入れるもの（呼び方・事実・確かめていない読み取り・市区町村）を asked=99 で送るだけで、
    # 上の stopped と同じ本文になる（開き直して同じ結果になることは tests/test_keep.py で見ている）
    assert set(body) == {"facts", "suggestions", "subject", "municipality"}


def grab(pattern: str) -> str:
    m = re.search(pattern, JS, re.S | re.M)
    assert m, pattern
    return m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_render_pauses_only_after_an_answer_in_one_question_mode():
    """render の分かれ方：1 問だけの道で 1 問以上答えたら止まる。最初の問いと、ふだんの道は問いを出す。結果は結果。"""
    funcs = "\n".join(grab(rf"^function {name}\(.*?^\}}\n") for name in ["render", "syncOne", "toggleOne", "renderPause", "pauseMore"])
    groups = grab(r"^const GROUPS = \[\n.*?^\];\n")
    names = grab(r"^const affectNames = .*?;\n")
    run = f"""
const hidden = {{}}, nodes = {{}}, seen = [];
const $ = (id) => (nodes[id] ||= {{ id, textContent: "", attrs: {{}}, setAttribute(k, v) {{ this.attrs[k] = v; }}, scrollIntoView() {{}} }});
const show = (id, on = true) => {{ hidden[id] = !on; }};
const renderBoard = () => {{}};
const renderQuestion = (q, max) => {{ seen.push(["question", q.fact, max]); }};
const renderResult = () => {{ seen.push(["result"]); }};
let focused = null;
const focusOn = (node) => {{ focused = node.id; }};
const calm = () => true;
let busy = false;
let state = {{ asked: 0, oneOnly: false, held: null }};
{groups}{names}{funcs}
const q = {{ fact: "care_level", affects: ["介護保険のサービス", "おむつの支給", "配食", "見守り"] }};
const results = [{{ status: "eligible" }}, {{ status: "eligible" }}, {{ status: "after_prereq" }}, {{ status: "unknown" }}, {{ status: "ineligible" }}];
const withQ = {{ question: q, max_questions: 3, results, center_search: "" }};
const out = {{}};
// ふだんの道：答えた後も次の問いを出す
state.asked = 1; render(withQ); out.normal = {{ seen: seen.splice(0), pause: hidden.pause, ask: hidden.ask }};
// 最初の問いでは、1 問だけを選んでいても問いを出す（まだ答えていない）
state.asked = 0; toggleOne(); out.pressed = $("one").attrs["aria-pressed"]; out.note = $("one-note").textContent;
render(withQ); out.first = {{ seen: seen.splice(0), pause: hidden.pause }};
// 1 問答えたら止まる
state.asked = 1; render(withQ);
out.paused = {{ seen: seen.splice(0), pause: hidden.pause, ask: hidden.ask, result: hidden.result, held: state.held === withQ,
  title: $("pause-title").textContent, counts: $("pause-counts").textContent, more: $("pause-more").textContent, focused }};
// 送っている間は「もう 1 問答える」を受け付けない
busy = true; pauseMore(); out.busy = {{ seen: seen.splice(0), held: state.held === withQ }}; busy = false;
// もう 1 問答える：止めておいた問いを出す（送り直さない）
pauseMore(); out.more = {{ seen: seen.splice(0), pause: hidden.pause, ask: hidden.ask, held: state.held }};
// 結果（問いがない）なら、1 問だけの道でも結果を出し、止まる欄は隠す
state.asked = 2; render({{ question: null, results, center_search: "" }}); out.result = {{ seen: seen.splice(0), pause: hidden.pause }};
// もう一度押すと、ふだんの道に戻る
toggleOne(); out.off = {{ pressed: $("one").attrs["aria-pressed"], note: $("one-note").textContent }};
console.log(JSON.stringify(out));
"""
    got = json.loads(subprocess.run(["node", "-e", run], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert got["normal"] == {"seen": [["question", "care_level", 3]], "pause": True, "ask": False}
    assert got["pressed"] == "true" and "止まります" in got["note"]
    assert got["first"] == {"seen": [["question", "care_level", 3]], "pause": True}
    paused = got["paused"]
    assert paused["seen"] == [] and paused["pause"] is False and paused["ask"] is True and paused["result"] is True
    assert paused["held"] is True and paused["focused"] == "pause-title"
    assert paused["title"] == "1 問答えました。ここまでの見立てです"
    # 数は結果の画面の見出しと同じ名前で、0 件の状態は出さない。当てはまらない制度は数えない
    assert paused["counts"] == ("当てはまる見込み 2 件・先に手続きをすると使える 1 件・確かめると使えるかもしれない 1 件。"
                                "次の問いで判定が決まる制度：介護保険のサービス、おむつの支給、配食 ほか 1 件")
    assert paused["more"] == "もう 1 問答える（あと 2 問まで）"
    assert got["busy"] == {"seen": [], "held": True}
    assert got["more"] == {"seen": [["question", "care_level", 3]], "pause": True, "ask": False, "held": None}
    assert got["result"] == {"seen": [["result"]], "pause": True}
    assert got["off"] == {"pressed": "false", "note": ""}


def test_page_has_one_question_path():
    # 「1 問だけ答える」は押すと切り替わるボタン（aria-pressed）。最初の問いでだけ出す
    assert 'id="one" aria-pressed="false"' in HTML and 'id="one-box" hidden' in HTML
    assert 'show("one-box", state.asked === 0)' in JS
    # 止まる欄：その時点の数と「もう 1 問答える」「ここでやめて、結果を見る」
    assert HTML.index('id="ask"') < HTML.index('id="pause"') < HTML.index('id="result"')
    for part in ['id="pause-title" tabindex="-1"', 'id="pause-counts"', 'id="pause-more"', 'id="pause-stop"', 'id="pause-msg"']:
        assert part in HTML
    # 「ここでやめる」は聞き返しをやめたのと同じ step({}, true)。結果の画面（renderResult）が残すリンク（showKeep）を出す
    assert '$("pause-stop").addEventListener("click", () => step({}, true));' in JS
    assert "showKeep(data);" in grab(r"^function renderResult\(data\) \{\n.*?^\}\n")
    # 送っている間は止まる欄のボタンも止める（2 度押しで結果を 2 回出さない）
    assert "#pause button" in grab(r"^function setBusy\(on\) \{\n.*?^\}\n")
    # 新しい相談・別の相談をするでは止まる欄を隠す。紙には刷らない
    assert JS.count('show("pause", false)') >= 4
    assert ".print-result #pause" in CSS
