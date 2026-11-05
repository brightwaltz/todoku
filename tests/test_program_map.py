"""結果の画面の「制度の地図」の矢印（T-081）。規則の前提（via・prereq）から作り、地図に置く制度どうしだけを結ぶ。"""
import os
import re
from pathlib import Path

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku import rules as rules_mod  # noqa: E402
from todoku.app import app  # noqa: E402
from todoku.engine import SHOWN, prereq_edges  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module")
def rs():
    return rules_mod.load()


def test_chain_from_nintei_to_tax_deduction(rs):
    # 要介護認定 → 障害者控除対象者認定書 → 障害者控除（BACKLOG の例）
    shown = ["kaigo-nintei", "machida-shogaisha-nintei", "shogaisha-kojo"]
    assert prereq_edges(rs, shown) == [("kaigo-nintei", "machida-shogaisha-nintei"),
                                       ("machida-shogaisha-nintei", "shogaisha-kojo")]


def test_edge_is_dropped_when_prerequisite_is_off_the_map(rs):
    # 認定が済んでいる（地図に置かない）と、認定からの矢印は描かない
    assert prereq_edges(rs, ["machida-shogaisha-nintei", "shogaisha-kojo"]) == [
        ("machida-shogaisha-nintei", "shogaisha-kojo")]
    assert prereq_edges(rs, ["shogaisha-kojo", "kogaku-ryoyohi"]) == []


def test_edges_follow_rule_order_and_every_depends_on(rs):
    every = list(rs.programs)
    edges = prereq_edges(rs, reversed(every))  # 渡す順によらない
    assert edges == [(d, p) for p in rs.order for d in sorted(rs.programs[p].depends_on, key=rs.order.index)]
    pos = {pid: i for i, pid in enumerate(rs.order)}
    assert all(pos[a] < pos[b] for a, b in edges)  # 前提が先（矢印は前へ戻らない）


def test_step_returns_edges_between_programs_on_the_map():
    facts = {"care_level": "none", "age_band": "age_75_plus", "residence": "home", "lives_in_machida": True}
    res = client.post("/api/step", json={"facts": facts, "suggestions": {}, "subject": "母", "asked": 3,
                                         "municipality": "132098"})
    assert res.status_code == 200, res.text
    data = res.json()
    on_map = {r["id"] for r in data["results"] if r["status"] in SHOWN}
    assert data["edges"], "認定のない人の結果に、認定からの矢印がない"
    for a, b in data["edges"]:
        assert a in on_map and b in on_map
    assert ["kaigo-nintei", "machida-shogaisha-nintei"] in data["edges"]


# ---- T-132：地図は名前の札で描く（点・矢印・費用の縁をやめた）----
WEB = Path(__file__).resolve().parents[1] / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def test_every_program_has_a_short_chip_name(rs):
    # 地図の札の名前（web/app.js の MAP_SHORT）が全部の制度にあり、7 字まで
    block = JS[JS.index("const MAP_SHORT = {"):JS.index("};", JS.index("const MAP_SHORT = {"))]
    short = dict(re.findall(r'"([a-z0-9-]+)": "([^"]+)"', block))
    ids = set(rs.programs)
    assert ids <= set(short), sorted(ids - set(short))
    assert all(0 < len(v) <= 7 for v in short.values()), {k: v for k, v in short.items() if len(v) > 7}


def test_map_has_no_arrows_or_cost_rings():
    # 先に要る手続きは並び方で示し、費用は札の一覧にだけ出す。凡例は状態の 1 つだけ
    for gone in ("pmap-arrow", "cost-ring", "pmap-chain", "pmap-cost", "longestChain"):
        assert gone not in JS and gone not in HTML, gone
    assert "点の縁の色は費用" not in HTML
    fig = HTML[HTML.index('<figure class="pmap"'):HTML.index("</figure>", HTML.index('<figure class="pmap"'))]
    assert fig.count("<ul") == 1 and "<p class=\"note\"" not in fig.split("<figcaption>")[1]
    # 確かめる制度は 1 つの束「＋N」
    assert "`＋${unknown.length}`" in JS


def test_map_chips_slide_only_without_reduced_motion():
    # T-132b：答えて増える札は束の位置から滑り、前からある札は新しい位置へ滑る。reduced-motion と印刷では動かさない
    # （ブラウザでの確かめは ops の motion_t132b.py。ここは T-133 で頁を組み替えても仕組みが残ることだけを見る）
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.pmap \.pt \{ transition: transform \d+ms", css)
    calm = css[css.index("@media (prefers-reduced-motion: reduce) {\n  .pmap .pt"):]
    assert calm.index(".pmap .pt { transition: none; }") < calm.index("}")
    assert "@media print { .pmap .pt { transition: none; } }" in css
    body = JS[JS.index("function renderMap("):JS.index("function pickPoint(")]
    assert "const from = again && !calm() ? mapBundleAt : null;" in body
    assert body.index("translate(${from.x}px, ${from.y}px)") < body.index("translate(${p.x}px, ${p.y}px)")


# 見本 3 本の問いの順と数（T-132〜T-134 は web/ だけを直すので、ここは変わらない）。
# dev-merge は見本の読み直し（T-117 など）が入っているので、dev と順が違う。T-132 の前の dev-merge 449a8bf で数えた
SAMPLE_QUESTIONS = {
    "taiin-haha": ["care_level", "needs_daily_help", "high_medical_costs"],
    "kaigo4-sobo": ["care_level", "age_band", "high_medical_costs"],
    "yokohama-chichi": ["care_level", "needs_daily_help", "residence"],
}


@pytest.mark.parametrize("sample_id", SAMPLE_QUESTIONS)
def test_sample_questions_keep_their_order(sample_id):
    from test_e2e_demo import pick_demo, walk  # 画面と同じ送り方で結果までたどる
    start = client.post("/api/sample", json={"id": sample_id}).json()
    asked = []

    def choose(q):
        asked.append(q["fact"])
        return pick_demo(q)

    walk(start, choose)
    assert asked == SAMPLE_QUESTIONS[sample_id]
