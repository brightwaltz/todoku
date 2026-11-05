"""次に聞く事実を選ぶ。

問いの値打ち＝その事実の答え次第で状態が変わる制度の重みの和（選択肢ごとの平均）。
答えるたびに数え直す。どの答えでも全制度の状態が同じになる事実は聞かない。

住所の事実（facts.yaml の place）は 1 つの問い（市区町村を選ぶ欄）で聞く。問いは、まだ分からない最初の住所の事実が受け持つ
（ふつうは町田市。町田市ではないとだけ分かったときは横浜市・東京都の事実が受け持ち、同じ欄をもう一度出す）。
住所の問いに「わからない」と答えたら、住所はもう聞かない。
選択肢の代わりに「規則が使う市・都道府県ごとに 1 つと、そのほか」で値打ちを数える。

住所は、3 問の枠の外で最初に聞く（11/6、柴田「町田市前提はやめてほしい。ユーザは全国どの人の可能性もある」）。
使う人は全国のどこにも住みうるので、住所が分からないまま値打ちで選ぶと、3 問のうち 1 問が住所に使われていた
（11/6 07:36 の採点で 30 件中 24 件）。住所の問いは数えない（Question.counted が False）。place_first=False は前の流れで、評価で並べるために残す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .engine import DONT_KNOW, evaluate, known, statuses, with_implied
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
    # 3 問の枠に数えるか。住所の問い（place_first で最初に聞くもの）は数えない
    counted: bool = True


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
    if fid in rules.places:
        return _score(rules, base, [statuses(rules, {**facts, **a}) for a in place_choices(rules, facts)])
    return _score(rules, base, [statuses(rules, {**facts, fid: v}) for v in rules.facts[fid].values()])


def place_choices(rules: RuleSet, facts: Mapping[str, Any]) -> list[dict[str, bool]]:
    """住所の問いの答えの代わり：規則が使う市ごと・都道府県（その中のほかの市）ごとに 1 つと、そのほか。

    分かっている住所と合わないものは外す。規則が町田市しか使わなければ「町田市／そのほか」の 2 つで、はい・いいえと同じに数える。
    """
    used = referenced_facts(rules)
    places = {fid: p for fid, p in rules.places.items() if fid in used}
    codes = [p["city"] for p in places.values() if "city" in p]
    codes += [p["pref"] + "0000" for p in places.values() if "pref" in p]  # その都道府県の、規則にない市区町村
    codes.append("000000")  # そのほか
    now = with_implied(rules, facts)
    choices = []
    for code in codes:
        values = {fid: v for fid, v in rules.place_values(code).items() if fid in used}
        if all(not known(now.get(fid)) or now[fid] == v for fid, v in values.items()):
            choices.append(values)
    return choices


def _score(rules: RuleSet, base: Mapping[str, str], outcomes: list[dict[str, str]]) -> tuple[float, tuple[str, ...]]:
    if len(outcomes) < 2 or all(o == outcomes[0] for o in outcomes[1:]):
        return 0.0, ()
    total = 0.0
    affected = []
    for pid, now in base.items():
        changed = sum(1 for o in outcomes if o[pid] != now)
        if changed:
            total += rules.weight(pid) * changed / len(outcomes)
            affected.append(pid)
    return total, tuple(affected)


def place_fact(rules: RuleSet, facts: Mapping[str, Any]) -> str | None:
    """住所の問い（市区町村を選ぶ欄）を受け持つ事実：規則が使う住所の事実のうち、分かっている住所から埋めてもまだ分からない最初のもの。

    町田市ではないとだけ分かった人にも、横浜市・東京都かを聞き直すため（町田市の答えだけで住所の問いを済ませていた）。
    住所の事実のどれかに「わからない」と答えていれば、住所はもう聞かない。
    """
    if any(facts.get(fid) == DONT_KNOW for fid in rules.places):
        return None
    used = referenced_facts(rules)
    now = with_implied(rules, facts)
    return next((fid for fid in rules.places if fid in used and not known(now.get(fid))), None)


def candidates(rules: RuleSet, facts: Mapping[str, Any]) -> list[str]:
    """聞いてよい事実。住所の事実は、住所の問いを受け持つもの（place_fact）だけを候補にする。"""
    place = place_fact(rules, facts)
    out = []
    for fid in sorted(referenced_facts(rules) | ({place} if place else set()),
                      key=lambda f: rules.facts[f].order):
        if fid in rules.places and fid != place:
            continue
        if known(facts.get(fid)) or facts.get(fid) == DONT_KNOW:
            continue
        out.append(fid)
    return out


def next_question(
    rules: RuleSet,
    facts: Mapping[str, Any],
    suggestions: Mapping[str, Any] | None = None,
    asked: int = 0,
    max_questions: int = MAX_QUESTIONS,
    place_first: bool = True,
) -> Question | None:
    """聞く事実を 1 つ返す。聞く必要がなければ None。

    facts に DONT_KNOW が入っている事実（「わからない」と答えたもの）は 2 度聞かない。
    suggestions は Claude が確信の低いまま読み取った値で、判定には使わず、確かめる問いにする。
    place_first なら、住所が分からない間は住所の問いを先に返す（枠に数えない。asked は増やさずに次を呼ぶ）。
    asked が枠に届いていれば（聞き返しをやめたときも）住所も聞かない。住所は結果の画面の欄で選べる（app.unconfirmed_view）。
    """
    if asked >= max_questions:
        return None
    suggestions = suggestions or {}
    base = statuses(rules, facts)
    if place_first:
        place = place_fact(rules, facts)
        if place is not None:
            score, affects = score_fact(rules, facts, place, base)
            return Question(place, score, suggestions.get(place), affects, counted=False)
    best: Question | None = None
    for fid in candidates(rules, facts):
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
    for fid in candidates(rules, facts):
        score, affects = score_fact(rules, facts, fid, base)
        if score > 0:
            ranked.append((score, -rules.facts[fid].order, fid, affects))
    ranked.sort(reverse=True)
    return {
        "results": evaluate(rules, facts),
        "ranked": [(fid, score, affects) for score, _, fid, affects in ranked],
    }
