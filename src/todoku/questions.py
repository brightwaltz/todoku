"""次に聞く事実を選ぶ。

問いの値打ち＝その事実の答え次第で状態が変わる制度の重みの和（選択肢ごとの平均）。
答えるたびに数え直す。どの答えでも全制度の状態が同じになる事実は聞かない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .engine import DONT_KNOW, evaluate, known, statuses
from .rules import RuleSet

MAX_QUESTIONS = 3


@dataclass
class Question:
    fact: str
    score: float
    # 確信の低い事実を確かめる問いのとき、Claude が読み取った値
    suggested: Any = None
    # この答えで状態が変わりうる制度
    affects: tuple[str, ...] = ()


def referenced_facts(rules: RuleSet) -> set[str]:
    found: set[str] = set()

    def walk(node: dict) -> None:
        for key in ("all", "any"):
            if key in node:
                for child in node[key]:
                    walk(child)
                return
        if "not" in node:
            walk(node["not"])
        elif "fact" in node:
            found.add(node["fact"])

    for program in rules.programs.values():
        walk(program.eligibility)
        if program.done_when:
            walk(program.done_when)
        if program.pending_when:
            walk(program.pending_when)
    return found


def score_fact(rules: RuleSet, facts: Mapping[str, Any], fid: str, base: Mapping[str, str]) -> tuple[float, tuple[str, ...]]:
    values = rules.facts[fid].values()
    outcomes = [statuses(rules, {**facts, fid: v}) for v in values]
    if all(o == outcomes[0] for o in outcomes[1:]):
        return 0.0, ()
    total = 0.0
    affected = []
    for pid, now in base.items():
        changed = sum(1 for o in outcomes if o[pid] != now)
        if changed:
            total += rules.weight(pid) * changed / len(values)
            affected.append(pid)
    return total, tuple(affected)


def next_question(
    rules: RuleSet,
    facts: Mapping[str, Any],
    suggestions: Mapping[str, Any] | None = None,
    asked: int = 0,
    max_questions: int = MAX_QUESTIONS,
) -> Question | None:
    """聞く事実を 1 つ返す。聞く必要がなければ None。

    facts に DONT_KNOW が入っている事実（「わからない」と答えたもの）は 2 度聞かない。
    suggestions は Claude が確信の低いまま読み取った値で、判定には使わず、確かめる問いにする。
    """
    if asked >= max_questions:
        return None
    suggestions = suggestions or {}
    base = statuses(rules, facts)
    best: Question | None = None
    for fid in sorted(referenced_facts(rules), key=lambda f: rules.facts[f].order):
        if known(facts.get(fid)) or facts.get(fid) == DONT_KNOW:
            continue
        score, affects = score_fact(rules, facts, fid, base)
        if score <= 0:
            continue
        if best is None or score > best.score:
            best = Question(fid, score, suggestions.get(fid), affects)
    return best


def plan(rules: RuleSet, facts: Mapping[str, Any]) -> dict[str, Any]:
    """画面とテスト向け：今の判定と、聞く候補の値打ちの一覧。"""
    base = statuses(rules, facts)
    ranked = []
    for fid in referenced_facts(rules):
        if known(facts.get(fid)) or facts.get(fid) == DONT_KNOW:
            continue
        score, affects = score_fact(rules, facts, fid, base)
        if score > 0:
            ranked.append((score, -rules.facts[fid].order, fid, affects))
    ranked.sort(reverse=True)
    return {
        "results": evaluate(rules, facts),
        "ranked": [(fid, score, affects) for score, _, fid, affects in ranked],
    }
