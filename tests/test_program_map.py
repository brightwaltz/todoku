"""結果の画面の「制度の地図」の矢印（T-081）。規則の前提（via・prereq）から作り、地図に置く制度どうしだけを結ぶ。"""
import os

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
