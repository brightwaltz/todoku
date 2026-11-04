"""合成住民での試算（1 版目）：入口の出来事から始めて、気づかなかった制度が 1 人あたりいくつ出るか。

  uv run python eval/sim/run_sim.py [--n 2000] [--seed 1] [--city 132098 --city 023043 …] [--rule facts|none] [--json out.json]

1 人ずつ次のように通す（API は呼ばない）：
  1. 住民の事実から、その人に当てはまる入口の出来事（THEME_FITS）を 1 つ選ぶ。どれも当てはまらない人は数えない
  2. 画面で市区町村を選び、出来事のボタンを押したのと同じ事実を置く（events.py と claude.check_extraction）
  3. 問いに住民の値で最大 3 問答える（eval/run_eval.py の simulate と同じ）
  4. その市で画面に出る制度のうち、「当てはまる見込み」と「先に手続きをすると使える」を数える。
     そのうち相談の主題に直接つながる制度（--rule）を除いた数を「主題の外」とする
主題の決め方（--rule）は判断待ち（integration-1105.md の下書き）。既定の facts は、出来事ごとの事実（THEME_FACTS）を
判定の条件に使う制度を「直接つながる」とみなす。none は除かない。
割合の出どころは population.py の SOURCES と README.md。memory・assumed の割合で出した数なので、外に言う前に照らす。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import population  # noqa: E402
import run_eval  # noqa: E402  （src を sys.path に入れるのもここ）
from todoku import events as events_mod  # noqa: E402
from todoku import rules as rules_mod  # noqa: E402
from todoku.claude import check_extraction  # noqa: E402
from todoku.questions import MAX_QUESTIONS  # noqa: E402

RECOMMEND = run_eval.RECOMMEND  # 当てはまる見込み・先に手続きをすると使える
# 町田市（市の制度を一つずつ判定）・横浜市（同じ）・青森県蓬田村（市区町村の型だけ）
CITIES = ("132098", "141003", "023043")

# その出来事のボタンを押しそうな住民か
THEME_FITS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "discharged": lambda f: f["residence"] == "home" and f["high_medical_costs"],
    "forgetful": lambda f: any(f[k] for k in ("dementia_diagnosis", "wandering_risk", "fire_risk",
                                              "money_management_difficulty")),
    "alone": lambda f: f["household"] == "alone",
    "falls": lambda f: f["residence"] == "home" and any(
        f[k] for k in ("plans_home_modification", "plans_fixture_replacement", "plans_equipment_purchase")),
    "work": lambda f: f["caregiver_employee"],
    "diapers": lambda f: f["uses_diapers"],
}
# 出来事の主題そのものの事実。これを判定の条件に使う制度は「主題に直接つながる」（--rule facts）
THEME_FACTS: dict[str, tuple[str, ...]] = {
    "discharged": ("residence", "high_medical_costs"),
    "forgetful": ("dementia_diagnosis", "wandering_risk", "fire_risk", "money_management_difficulty"),
    "alone": ("household",),
    "falls": ("plans_home_modification", "plans_fixture_replacement", "plans_equipment_purchase"),
    "work": ("caregiver_employee",),
    "diapers": ("uses_diapers", "incontinence_bedding"),
}
RULES = ("facts", "none")


def eligibility_facts(node: dict) -> set[str]:
    """判定の条件（eligibility）が使う事実。前提の制度（prereq・via）の中の事実は数えない。"""
    found: set[str] = set()
    for key in ("all", "any"):
        for child in node.get(key, ()):
            found |= eligibility_facts(child)
    if "not" in node:
        found |= eligibility_facts(node["not"])
    if "fact" in node:
        found.add(node["fact"])
    return found


def direct_programs(rs: rules_mod.RuleSet, event_id: str, rule: str) -> set[str]:
    if rule == "none":
        return set()
    theme = set(THEME_FACTS[event_id])
    return {pid for pid, p in rs.programs.items() if eligibility_facts(p.eligibility) & theme}


def visible(rs: rules_mod.RuleSet, code: str) -> set[str]:
    """その市区町村を選んだ人の結果の画面に出る制度（app.step_view の 2 つの絞り込みと同じ。テストで照らす）。"""
    place = rs.place_values(code)
    city = next((lv for lv, fid in rules_mod.LEVEL_PLACE.items()
                 if place.get(fid) is True and "city" in (rs.places.get(fid) or {})), None)
    out = set()
    for pid, p in rs.programs.items():
        if city and p.level == "local" and city not in p.shown_in:
            continue
        if p.level in rules_mod.LEVEL_PLACE and place.get(rules_mod.LEVEL_PLACE[p.level]) is False:
            continue
        out.add(pid)
    return out


def themes_of(facts: dict[str, Any]) -> list[str]:
    return [e["id"] for e in events_mod.EVENTS[:events_mod.MAX_EVENTS] if THEME_FITS[e["id"]](facts)]


def run_one(rs: rules_mod.RuleSet, facts: dict[str, Any], event_id: str, code: str) -> dict:
    """出来事のボタンから始めて最大 3 問答えた後の、全制度の状態と聞いた事実。"""
    event = events_mod.find(event_id)
    reading = check_extraction(rs, event["text"], events_mod.reading(event))
    case = {"answers": facts, "municipality": code}
    known, suggestions = reading.confirmed(), reading.suggestions()
    run_eval.apply_place(rs, case, known, suggestions, rs.place_question or "lives_in_machida")
    return run_eval.simulate(rs, case, known, suggestions, MAX_QUESTIONS, "value")


def simulate_city(rs: rules_mod.RuleSet, residents: list[dict[str, Any]], code: str, rule: str,
                  seed: int) -> dict:
    import random

    rng = random.Random(seed)
    shown = visible(rs, code)
    rows = []
    for facts in residents:
        fits = themes_of(facts)
        if not fits:
            continue
        theme = rng.choice(fits)
        out = run_one(rs, facts, theme, code)
        found = {pid for pid in shown if out["statuses"][pid] in RECOMMEND}
        direct = direct_programs(rs, theme, rule)
        outside = found - direct
        rows.append({"theme": theme, "asked": out["asked"], "found": sorted(found), "outside": sorted(outside),
                     # 主題の外のうち「当てはまる見込み」。残りは「先に手続きをすると使える」（認定の申請が先、など）
                     "outside_eligible": sorted(p for p in outside if out["statuses"][p] == "eligible")})
    return summarize(rows, len(residents))


def summarize(rows: list[dict], n: int) -> dict:
    outside = [len(r["outside"]) for r in rows]
    found = [len(r["found"]) for r in rows]
    by_theme: dict[str, list[int]] = {}
    for r in rows:
        by_theme.setdefault(r["theme"], []).append(len(r["outside"]))
    programs = Counter(pid for r in rows for pid in r["outside"])
    return {
        "residents": n,
        "counted": len(rows),
        "mean_found": round(statistics.fmean(found), 2) if rows else None,
        "mean_outside": round(statistics.fmean(outside), 2) if rows else None,
        "mean_outside_eligible": round(statistics.fmean(len(r["outside_eligible"]) for r in rows), 2) if rows else None,
        "share_outside_1plus": round(sum(1 for x in outside if x >= 1) / len(rows), 3) if rows else None,
        "outside_histogram": dict(sorted(Counter(min(x, 5) for x in outside).items())),
        "by_theme": {t: {"n": len(v), "mean_outside": round(statistics.fmean(v), 2)}
                     for t, v in sorted(by_theme.items())},
        "top_outside_programs": programs.most_common(10),
    }


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--city", action="append", help="団体コード（6 桁）。既定は町田市・横浜市・蓬田村")
    ap.add_argument("--rule", choices=RULES, default="facts")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    rs = rules_mod.load()
    residents = population.generate(args.n, args.seed)
    report = {"n": args.n, "seed": args.seed, "rule": args.rule, "max_questions": MAX_QUESTIONS,
              "sources": population.SOURCES,
              "cities": {code: simulate_city(rs, residents, code, args.rule, args.seed)
                         for code in (args.city or CITIES)}}
    for code, s in report["cities"].items():
        print(f"{code}: 数えた {s['counted']}/{s['residents']} 人、案内できた {s['mean_found']} 件／人、"
              f"主題の外 {s['mean_outside']} 件／人（うち当てはまる見込み {s['mean_outside_eligible']}、"
              f"1 件以上の人 {s['share_outside_1plus']:.0%}）")
        print("  主題ごと: " + "、".join(f"{t} {v['mean_outside']}（{v['n']} 人）" for t, v in s["by_theme"].items()))
        print("  主題の外の件数: " + "、".join(f"{k}{'+' if k == 5 else ''} 件 {v} 人"
                                          for k, v in s["outside_histogram"].items()))
        names = "、".join(f"{rs.programs[p].name} {c}" for p, c in s["top_outside_programs"][:5])
        print(f"  主題の外で多い制度: {names}")
    print("割合は memory・assumed（population.py の SOURCES）。統計の頁で照らす前の数なので、外には出さない")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
