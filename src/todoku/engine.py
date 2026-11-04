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
from typing import Any, Iterable, Mapping

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
    so_far: dict[str, str] = {}  # ここまでに判定した制度の状態（前提の手続きの葉が引く。rules.order は前提が先）
    for pid in rules.order:
        program = rules.programs[pid]
        results[pid] = _result(program, facts, so_far)
        so_far[pid] = results[pid].status
    return results


def _result(program: Program, facts: Mapping[str, Any], so_far: Mapping[str, str]) -> Result:
    if program.done_when is not None and _state(program.done_when, facts, so_far) == T:
        return Result(program, "done", [])
    if program.pending_when is not None:
        leaves: list[Leaf] = []
        if _eval(program.pending_when, facts, so_far, leaves) == T:
            return Result(program, "pending", leaves)
    leaves = []
    state = _eval(program.eligibility, facts, so_far, leaves)
    return Result(program, STATUS_OF_STATE[state], leaves)


def statuses(rules: RuleSet, facts: Mapping[str, Any]) -> dict[str, str]:
    """evaluate と同じ状態を、理由（葉）を作らずに返す。

    次に聞く事実を選ぶときは、事実ごと・答えごとに全制度を判定し直す（/api/step 1 回で 40 回ほど）。
    状態だけでよいので、葉を作らず、all・any は決まったところで残りを見ない（T-047）。
    evaluate と同じになることは tests/test_engine.py で確かめる。
    """
    facts = with_implied(rules, facts)
    out: dict[str, str] = {}
    for pid in rules.order:
        program = rules.programs[pid]
        if program.done_when is not None and _state(program.done_when, facts, out) == T:
            out[pid] = "done"
        elif program.pending_when is not None and _state(program.pending_when, facts, out) == T:
            out[pid] = "pending"
        else:
            out[pid] = STATUS_OF_STATE[_state(program.eligibility, facts, out)]
    return out


def _state(node: dict, facts: Mapping[str, Any], statuses: Mapping[str, str]) -> str:
    """_eval と同じ状態を返す（葉は作らない）。"""
    if "all" in node:
        worst = T  # F > U > P > T
        for c in node["all"]:
            s = _state(c, facts, statuses)
            if s == F:
                return F
            if s == U or (s == P and worst == T):
                worst = s
        return worst
    if "any" in node:
        best = F  # T > P > U > F
        for c in node["any"]:
            s = _state(c, facts, statuses)
            if s == T:
                return T
            if s == P or (s == U and best == F):
                best = s
        return best
    if "not" in node:
        s = _state(node["not"], facts, statuses)
        return {T: F, F: T}.get(s, s)
    if "step" in node:
        return P
    if "ask_at" in node:
        return U
    if "prereq" in node:
        return STATE_OF_PREREQ[statuses[node["prereq"]]]
    value = facts.get(node["fact"])
    if not known(value):
        return U
    if "in" in node:
        # unsure は区分の分からない認定のような値（_eval と同じ。T-070 で fix/gold-rules の直しに合わせた）
        s = T if value in node["in"] else U if value in node.get("unsure", ()) else F
    elif "eq" in node:
        s = T if value == node["eq"] else F
    else:
        s = T if value is node["is"] else F
    if s == F and "via" in node:
        return STATE_OF_VIA[statuses[node["via"]]]
    return s


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
        # unsure は、区分の分からない認定のように「どれかは分からない」値（rules.py が means から書く）
        s = T if value in node["in"] else U if value in node.get("unsure", ()) else F
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


def prereq_edges(rules: RuleSet, shown: Iterable[str]) -> list[tuple[str, str]]:
    """制度の地図の矢印（前提の制度 → それを前提にする制度）。両方が shown にあるものだけを、rules.order の順で返す。

    規則の via と prereq（Program.depends_on）から作る。判定の状態は見ない（矢印は「先に済ませる手続き」のつながりを示すだけ）。
    前提の制度が地図にない（済んでいる・当てはまらない・住所で外した）ときは、その矢印を描かない（T-081）
    """
    on_map = set(shown)
    return [(dep, pid) for pid in rules.order if pid in on_map
            for dep in sorted(rules.programs[pid].depends_on, key=rules.order.index) if dep in on_map]
