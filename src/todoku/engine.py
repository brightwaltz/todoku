"""制度ごとに、当てはまるかを三値（＋前提の手続き待ち）で判定する。

条件の葉の状態：
  T 満たす / F 満たさない / U 分からない / P 前提の手続きを済ませれば満たせる
all は F > U > P > T、any は T > P > U > F の順に強い（any は、前提の手続きで届く道があれば
「先に手続きをすると使える」と言える）。

制度の状態：
  done          もう済んでいる（done_when が T）
  pending       申請して結果を待っている（pending_when が T）。前提にしている制度は P のまま
  eligible      当てはまる見込み（残りは申請で確かめる）
  after_prereq  先に手続きをすると使える
  unknown       確かめると使えるかもしれない
  ineligible    当てはまらない
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .rules import Program, RuleSet

T, F, U, P = "T", "F", "U", "P"
DONT_KNOW = "__dont_know__"

STATUS_OF_STATE = {T: "eligible", P: "after_prereq", U: "unknown", F: "ineligible"}
# 前提の制度の状態を、それを参照する葉の状態に直す
STATE_OF_PREREQ = {"done": T, "pending": P, "eligible": P, "after_prereq": P, "unknown": U, "ineligible": F}
# via：葉が F のとき、前提の制度を済ませれば満たせるか
STATE_OF_VIA = {"done": F, "pending": P, "eligible": P, "after_prereq": P, "unknown": U, "ineligible": F}
# 画面に出す状態（ineligible と done はたたむ）
SHOWN = ("eligible", "after_prereq", "unknown")


@dataclass
class Leaf:
    label: str
    state: str
    fact: str | None = None
    value: Any = None
    prereq: str | None = None


@dataclass
class Result:
    program: Program
    status: str
    leaves: list[Leaf] = field(default_factory=list)

    @property
    def missing_facts(self) -> list[str]:
        """状態が U の葉の事実（「わからない」と答えたものも含む）。"""
        seen: list[str] = []
        for leaf in self.leaves:
            if leaf.state == U and leaf.fact and leaf.fact not in seen:
                seen.append(leaf.fact)
        return seen

    @property
    def waiting_on(self) -> list[str]:
        seen: list[str] = []
        for leaf in self.leaves:
            if leaf.state == P and leaf.prereq and leaf.prereq not in seen:
                seen.append(leaf.prereq)
        return seen


def known(value: Any) -> bool:
    return value is not None and value != DONT_KNOW


def with_implied(rules: RuleSet, facts: Mapping[str, Any]) -> dict[str, Any]:
    """facts.yaml の false_when で「いいえ」と決まる事実を埋める。"""
    out = dict(facts)
    for fact in rules.facts.values():
        cond = fact.false_when
        if cond is None or known(out.get(fact.id)):
            continue
        if out.get(cond["fact"]) in cond["in"]:
            out[fact.id] = False
    _imply_place(rules, out)
    return out


def _imply_place(rules: RuleSet, out: dict[str, Any]) -> None:
    """住所の事実を、分かっている住所から埋める（町田市に住んでいれば東京都に住んでいて、横浜市・神奈川県ではない）。

    読み取りで「町田市に住んでいる」だけが分かったときも、画面と評価で同じ判定になるように、evaluate の前にここで埋める。
    """
    places = rules.places
    city = next((p["city"] for fid, p in places.items() if "city" in p and out.get(fid) is True), None)
    pref = city[:2] if city else next((p["pref"] for fid, p in places.items() if "pref" in p and out.get(fid) is True), None)
    not_prefs = {p["pref"] for fid, p in places.items() if "pref" in p and out.get(fid) is False}
    for fid, p in places.items():
        if known(out.get(fid)):
            continue
        if "city" in p:
            if city or (pref and p["city"][:2] != pref) or p["city"][:2] in not_prefs:
                out[fid] = False
        elif pref:
            out[fid] = p["pref"] == pref


def evaluate(rules: RuleSet, facts: Mapping[str, Any]) -> dict[str, Result]:
    """事実（分からないものは入れないか DONT_KNOW）から、全制度の判定を返す。"""
    facts = with_implied(rules, facts)
    results: dict[str, Result] = {}
    for pid in rules.order:
        program = rules.programs[pid]
        statuses = {k: r.status for k, r in results.items()}
        if program.done_when is not None and _eval(program.done_when, facts, statuses, []) == T:
            results[pid] = Result(program, "done", [])
            continue
        if program.pending_when is not None:
            leaves = []
            if _eval(program.pending_when, facts, statuses, leaves) == T:
                results[pid] = Result(program, "pending", leaves)
                continue
        leaves: list[Leaf] = []
        state = _eval(program.eligibility, facts, statuses, leaves)
        results[pid] = Result(program, STATUS_OF_STATE[state], leaves)
    return results


def statuses(rules: RuleSet, facts: Mapping[str, Any]) -> dict[str, str]:
    return {pid: r.status for pid, r in evaluate(rules, facts).items()}


def _eval(node: dict, facts: Mapping[str, Any], statuses: Mapping[str, str], leaves: list[Leaf]) -> str:
    if "all" in node:
        states = [_eval(c, facts, statuses, leaves) for c in node["all"]]
        for s in (F, U, P):
            if s in states:
                return s
        return T
    if "any" in node:
        branches = []
        for c in node["any"]:
            inner: list[Leaf] = []
            branches.append((_eval(c, facts, statuses, inner), inner))
        states = [b for b, _ in branches]
        result = next((s for s in (T, P, U) if s in states), F)
        # 通った枝があれば、その枝の理由だけを残す（当てはまるのに、別の道の × が並ばないように）
        for state, inner in branches:
            if result not in (T, P) or state == result:
                leaves.extend(inner)
        return result
    if "not" in node:
        inner: list[Leaf] = []
        s = _eval(node["not"], facts, statuses, inner)
        flipped = {T: F, F: T}.get(s, s)
        for leaf in inner:
            leaf.label = "（ではない）" + leaf.label
            leaf.state = {T: F, F: T}.get(leaf.state, leaf.state)
        leaves.extend(inner)
        return flipped
    if "step" in node:
        # 本人が先に済ませればよいこと（例：医師に証明書を書いてもらう）
        leaves.append(Leaf(node["label"], P))
        return P
    if "ask_at" in node:
        # 規則では決めず、問いにもしない条件（例：お住まいの市区町村がその事業をしているか）。確かめる先は ask_at に書く
        leaves.append(Leaf(node["label"], U))
        return U
    if "prereq" in node:
        s = STATE_OF_PREREQ[statuses[node["prereq"]]]
        leaves.append(Leaf(node["label"], s, prereq=node["prereq"]))
        return s

    fid = node["fact"]
    value = facts.get(fid)
    if not known(value):
        s = U
    elif "in" in node:
        s = T if value in node["in"] else F
    elif "eq" in node:
        s = T if value == node["eq"] else F
    else:
        s = T if value is node["is"] else F
    prereq = None
    if s == F and "via" in node:
        s = STATE_OF_VIA[statuses[node["via"]]]
        prereq = node["via"]
    leaves.append(Leaf(node["label"], s, fact=fid, value=value, prereq=prereq))
    return s
