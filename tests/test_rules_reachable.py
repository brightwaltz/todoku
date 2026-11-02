"""どの制度も、出てくる道と出てこない道の両方があることを確かめる。

条件の木から、満たす事実の組み合わせを自動で組み立てる。書き間違いで二度と出てこない制度や、
誰にでも出てしまう制度を見つけるため。
"""

import pytest

from todoku import rules as rules_mod
from todoku.engine import statuses

RS = rules_mod.load()


def satisfy(node: dict, facts: dict) -> bool:
    """node を満たすように facts に値を足す。矛盾したら False。"""
    if "all" in node:
        return all(satisfy(c, facts) for c in node["all"])
    if "any" in node:
        for child in node["any"]:
            trial = dict(facts)
            if satisfy(child, trial):
                facts.update(trial)
                return True
        return False
    if "step" in node or "ask_at" in node:
        return True
    if "prereq" in node:
        return satisfy(RS.programs[node["prereq"]].eligibility, facts)
    want = node["in"] if "in" in node else [node.get("eq", node.get("is"))]
    current = facts.get(node["fact"])
    if current is not None:
        return current in want
    facts[node["fact"]] = want[0]
    return True


@pytest.mark.parametrize("pid", sorted(RS.programs))
def test_program_can_be_shown(pid):
    facts: dict = {}
    assert satisfy(RS.programs[pid].eligibility, facts), pid
    # 市区町村の型は、あるかどうかを窓口で確かめる（ask_at）ので、いちばん良くても「確かめる」
    best = ("unknown",) if RS.programs[pid].level == "local" else ("eligible", "after_prereq")
    assert statuses(RS, facts)[pid] in best, (pid, facts)


@pytest.mark.parametrize("pid", sorted(RS.programs))
def test_program_can_be_ruled_out(pid):
    # 当てはまる組み合わせから、事実を 1 つずつ別の値に変えて、当てはまらなくなる道があるか
    facts: dict = {}
    satisfy(RS.programs[pid].eligibility, facts)
    for fid in facts:
        for v in RS.facts[fid].values():
            if v != facts[fid] and statuses(RS, {**facts, fid: v})[pid] == "ineligible":
                return
    # 「どれか 1 つ」（any）の条件は、1 つ変えるだけでは外れない。条件の木から外れる組み合わせを組み立てて確かめる
    facts = {}
    if refuse(RS.programs[pid].eligibility, facts) and statuses(RS, facts)[pid] == "ineligible":
        return
    pytest.fail(f"{pid} はどの事実を変えても当てはまらなくならない")


def refuse(node: dict, facts: dict) -> bool:
    """node を満たさないように facts に値を足す（satisfy の逆）。できなければ False。"""
    if "all" in node:
        for child in node["all"]:
            trial = dict(facts)
            if refuse(child, trial):
                facts.update(trial)
                return True
        return False
    if "any" in node:
        return all(refuse(c, facts) for c in node["any"])
    if "not" in node:
        return satisfy(node["not"], facts)
    if "step" in node or "ask_at" in node:
        return False
    if "prereq" in node:
        return refuse(RS.programs[node["prereq"]].eligibility, facts)
    want = node["in"] if "in" in node else [node.get("eq", node.get("is"))]
    current = facts.get(node["fact"])
    if current is not None:
        return current not in want
    others = [v for v in RS.facts[node["fact"]].values() if v not in want]
    if not others:
        return False
    facts[node["fact"]] = others[0]
    return True
