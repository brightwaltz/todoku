"""読み A（規則）と読み B（目隠しの Claude）を突き合わせ、先生が裁く論点と抜き取りを作る。

手順は eval/PROTOCOL.md。API は呼ばない。
  uv run python eval/adjudicate.py prepare eval/out/blind/read-A.json eval/out/blind/read-B.json
  uv run python eval/adjudicate.py gold eval/out/adjudication-….json eval/out/decisions-….json   （裁定ページの db を書き出したもの）
gold は eval/gold.csv と、決まりの問いのマス（と同じ型で別に見たマス）を空欄にした eval/gold_no_policy.csv を書く。
裁くときに見せた Claude の推しは eval/recommendations.json（裁定の前にコミット）。db の「推しどおり」は、このファイルと照合する。
結果は eval/out/adjudication-<日時>.json（町田市の引用を含むので eval/out/ に置く。リポジトリに入れない）。

マスの印：○＝当たりうる、×＝当たらない（済んでいるを含む）、？＝決められない、申＝申請済み・結果待ち。
- 読み A：eligible・after_prereq→○、ineligible・done→×、unknown→？、pending→申
- 読み B：当たりうる→○、当たらない・済んでいる→×、決められない→？、申請済み・結果待ち→申
一致：A と B の全部の回が同じ印で、申を含まず、B の ○・× の引用がどれも原文に見つかる。
それ以外は、まず制度をまたぐ型（POLICIES：申請中・非該当だった人・区分が分からない人・使う予定がない人・前の手続き待ち）に入れ、
残りを論点に入れる。論点は「制度×A の印×A の決め手」でまとめる。
抜き取り：一致した ○ と × から、乱数の種 20261103 で ○ 20・× 20（足りなければ全部）。
"""

from __future__ import annotations

import json
import random
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
from todoku import rules as rules_mod  # noqa: E402
from todoku.engine import DONT_KNOW, F, P, U, evaluate  # noqa: E402

from blind_read import SCORED, cases_in_scope, load_cases  # noqa: E402
from sources import CACHE  # noqa: E402

OUT = ROOT / "eval" / "out"
RECS = ROOT / "eval" / "recommendations.json"
MARKS = ("○", "×", "？")
SEED = 20261103
AUDIT_YES = 20
AUDIT_NO = 20

A_MARK = {"eligible": "○", "after_prereq": "○", "ineligible": "×", "done": "×", "unknown": "？", "pending": "申"}
B_MARK = {"当たりうる": "○", "当たらない": "×", "済んでいる": "×", "決められない": "？", "申請済み・結果待ち": "申"}

# 制度をまたいで同じ型の食い違い（試しの結果を見て、本番の結果を見る前に足した。PROTOCOL.md の変更の記録）。
# 柴田は、型ごとに 1 回「どちらの読みに合わせるか／？にするか／制度ごとに見るか」を選ぶ。上から順に当てはめる。
POLICIES = {
    "p-pending": ("申請して結果を待っている人",
                  "要介護認定を申請して、結果がまだ出ていない人。認定の申請そのものや、認定を前提にした制度に、どの印を付けますか。"),
    "p-jiritsu": ("前の認定で「非該当（自立）」だった人",
                  "前に申請して非該当だった人。その後の様子の変わり方をどう読むか（申請し直せるか、認定を前提にした制度をどうするか）。"),
    "p-care-unknown": ("認定は受けているが、区分を相談した人が知らない人",
                       "人物の説明では認定を受けているが、要支援か要介護か、いくつかは書かれていない人。"),
    "p-plan": ("使う予定がない人",
               "工事・福祉用具の購入・浴槽などの取り替え・ショートステイや施設を、今は考えていない人。公式の条件には当たりうるが、"
               "使う予定がないことだけで印が割れているマスです。予定がない人にも ○ を付けるか（正解付けページの定義は「本人が望むか、"
               "役に立ちそうかは問わない」）、予定がないので × にするか。"),
    "p-prereq": ("前の手続きを済ませれば当たりうる人",
                 "今はまだ前の手続き（要介護認定など）を済ませていない人。「済ませれば当たる」を、今の様子からは当たりそうにない人にも当てはめるか。"),
}


def norm(text: str) -> str:
    """引用の照合用：NFKC にして、空白・改行・表の区切りを除く。"""
    t = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s|｜]+", "", t)


def quote_found(quote: str, sources: list[dict]) -> bool:
    """引用がその制度の公式資料のどれかに、そのまま入っているか（資料の番号の書き違いは問わない）。"""
    q = norm(quote)
    if len(q) < 8:
        return False
    for s in sources:
        path = CACHE / f"{s['key']}.txt"
        if path.exists() and q in norm(path.read_text(encoding="utf-8")):
            return True
    return False


def a_reading(result) -> tuple[str, str]:
    """読み A の印と、決め手（条件の label）。"""
    st = result.status
    if st == "done":
        return "×", "済んでいる"
    if st == "pending":
        return "申", "申請済み・結果待ち"
    if st == "ineligible":
        labels = [lf.label for lf in result.leaves if lf.state == F]
        return "×", "満たさない：" + "／".join(dict.fromkeys(labels))
    if st == "unknown":
        labels = [lf.label for lf in result.leaves if lf.state == U]
        return "？", "分からない：" + "／".join(dict.fromkeys(labels))
    pre = [lf.label for lf in result.leaves if lf.prereq]
    return "○", ("先に手続き：" + "／".join(dict.fromkeys(pre))) if st == "after_prereq" else "条件を満たす"


# 「使う予定」の事実（本番の結果を見て足した型 p-plan の決め手。PROTOCOL.md の変更の記録）
PLAN_FACTS = ("plans_home_modification", "plans_equipment_purchase", "plans_fixture_replacement", "shortstay_or_facility")


def policy_of(case: dict, res, a_mark: str, bs: list[dict], res_if_planned=None) -> str | None:
    """食い違ったマスが、制度をまたぐ型（POLICIES）のどれに当たるか。"""
    care = case["answers"].get("care_level")
    marks = {a_mark, *[b["mark"] for b in bs]}
    has_p = any(lf.state == P for lf in res.leaves)
    if "申" in marks or (care == "applying" and has_p):
        return "p-pending"
    if care == "jiritsu":
        return "p-jiritsu"
    if care == DONT_KNOW and any(lf.state == U and lf.fact == "care_level" for lf in res.leaves):
        return "p-care-unknown"
    # 予定を「ある」にして判定し直すと ○ になる＝使う予定だけが決め手
    if a_mark == "×" and res_if_planned is not None and A_MARK.get(res_if_planned.status) == "○":
        return "p-plan"
    if has_p:
        return "p-prereq"
    return None


def load_runs(paths: list[str]) -> list[dict]:
    runs = []
    for p in paths:
        r = json.loads(Path(p).read_text(encoding="utf-8"))
        r["_path"] = p
        runs.append(r)
    return runs


def b_cell(run: dict, num: int, cid: str) -> dict | None:
    prog = run["programs"].get(str(num))
    if not prog or "judgments" not in prog:
        return None
    for j in prog["judgments"]:
        if j["case"] == cid:
            mark = B_MARK[j["state"]]
            ok = True if mark in ("？", "申") else quote_found(j["quote"], prog["sources"])
            return {"model": run["model"], "label": run["label"], "mark": mark, "state": j["state"],
                    "deciding": j["deciding"], "reason": j["reason"], "quote": j["quote"],
                    "source": j["source"], "quote_ok": ok}
    return None


def prepare(paths: list[str]) -> dict:
    rs = rules_mod.load()
    by_number = {p.number: p for p in rs.programs.values()}
    cases = load_cases()
    runs = load_runs(paths)
    nums = sorted({int(n) for r in runs for n, p in r["programs"].items() if "judgments" in p})
    cells, agreed, issues, policies = [], [], defaultdict(list), defaultdict(list)
    for case in cases:
        results = evaluate(rs, dict(case["answers"]))
        planned = evaluate(rs, {**case["answers"], **{f: True for f in PLAN_FACTS}})
        for num in nums:
            if case not in cases_in_scope(num, cases):
                continue
            prog = by_number[num]
            res = results[prog.id]
            a_mark, a_why = a_reading(res)
            bs = [b_cell(r, num, case["id"]) for r in runs]
            if any(b is None for b in bs):
                key = (num, "B の答えがない", "")
            else:
                marks = {a_mark, *[b["mark"] for b in bs]}
                quotes_ok = all(b["quote_ok"] for b in bs)
                if len(marks) == 1 and "申" not in marks and quotes_ok:
                    key = None
                elif "申" in marks:
                    key = (num, "申請済み・結果待ち", "")
                elif len(marks) == 1:
                    key = (num, "引用が原文に見つからない", "")
                else:
                    key = (num, a_mark, a_why)
            cell = {"case": case["id"], "num": num, "program": prog.name, "a": {"mark": a_mark, "why": a_why},
                    "b": bs}
            cells.append(cell)
            if key is None:
                agreed.append(cell)
                continue
            pol = policy_of(case, res, a_mark, bs, planned[prog.id]) if key[1] not in ("B の答えがない", "引用が原文に見つからない") else None
            if pol:
                cell["policy"] = pol
                policies[pol].append(cell)
            else:
                issues[key].append(cell)
    rng = random.Random(SEED)
    yes = [c for c in agreed if c["a"]["mark"] == "○"]
    no = [c for c in agreed if c["a"]["mark"] == "×"]
    audit = rng.sample(yes, min(AUDIT_YES, len(yes))) + rng.sample(no, min(AUDIT_NO, len(no)))
    issue_list = []
    for i, (key, cs) in enumerate(sorted(issues.items(), key=lambda kv: (kv[0][0], -len(kv[1]))), 1):
        num, kind, why = key
        issue_list.append({"id": f"i{i:02d}", "num": num, "program": by_number[num].name, "kind": kind,
                           "a_why": why, "cells": cs})
    policy_list = [{"id": pid, "title": POLICIES[pid][0], "question": POLICIES[pid][1], "cells": policies[pid]}
                   for pid in POLICIES if policies[pid]]
    stats = summarize(cells, agreed, issue_list, runs)
    stats["policies"] = {p["id"]: len(p["cells"]) for p in policy_list}
    return {"made": datetime.now().isoformat(timespec="seconds"), "seed": SEED, "runs": [r["_path"] for r in runs],
            "models": [r["model"] for r in runs], "programs": nums, "stats": stats,
            "agreed": [{"case": c["case"], "num": c["num"], "mark": c["a"]["mark"]} for c in agreed],
            "policies": policy_list, "issues": issue_list, "audit": audit}


def summarize(cells, agreed, issues, runs) -> dict:
    n = len(cells)
    agree_ab = [sum(1 for c in cells if c["b"][k] and c["b"][k]["mark"] == c["a"]["mark"]) for k in range(len(runs))]
    bb = None
    if len(runs) >= 2:
        both = [c for c in cells if c["b"][0] and c["b"][1]]
        bb = sum(1 for c in both if c["b"][0]["mark"] == c["b"][1]["mark"]) / max(1, len(both))
    qfail = []
    for k in range(len(runs)):
        judged = [c["b"][k] for c in cells if c["b"][k] and c["b"][k]["mark"] in ("○", "×")]
        qfail.append({"model": runs[k]["model"], "ox": len(judged), "quote_missing": sum(not b["quote_ok"] for b in judged)})
    marks = defaultdict(int)
    for c in agreed:
        marks[c["a"]["mark"]] += 1
    return {"cells": n, "agreed": len(agreed), "agreed_by_mark": dict(marks),
            "issue_cells": n - len(agreed), "issues": len(issues),
            "a_b_agreement": [round(x / max(1, n), 3) for x in agree_ab],
            "b_b_agreement": None if bb is None else round(bb, 3), "quotes": qfail,
            "usd": [round(r.get("usd", 0), 3) for r in runs]}


# ---- 裁いた結果から正解表を作る -------------------------------------------------

def b_mark_of(cell: dict) -> str | None:
    """B の 2 回がそろって ○・×・？ なら、その印。割れた・申・答えなしは None。"""
    ms = {b["mark"] for b in cell["b"] if b}
    if len(ms) == 1 and len(cell["b"]) == len([b for b in cell["b"] if b]):
        m = ms.pop()
        return m if m in ("○", "×", "？") else None
    return None


def decided_mark(item: dict, cell: dict, kind: str, decisions: dict) -> str | None:
    d = decisions.get(item["id"], {})
    key = f"{cell['case']}-{cell['num']}"
    if (d.get("overrides") or {}).get(key):
        return d["overrides"][key]
    # 推しどおり（2026-11-04）：押したときの推しの印を db の文書に入れてある（確かなマスだけ。迷うマスは overrides で 1 マスずつ）
    if d.get("choice") == "rec":
        m = (d.get("rec") or {}).get(key)
        return m if m in MARKS else None
    if kind == "policy":
        # 取り消した選択（choice が空）に resolved が残っていても使わない
        if d.get("choice") not in ("r1", "r2", "○", "×", "？"):
            return None
        if d.get("choice") in ("r1", "r2") and d.get("resolved") == "A":
            return cell["a"]["mark"] if cell["a"]["mark"] in ("○", "×", "？") else None
        if d.get("choice") in ("r1", "r2") and d.get("resolved") == "B":
            return b_mark_of(cell)
        if d.get("choice") in ("r1", "r2"):
            return None
        return d.get("choice") if d.get("choice") in ("○", "×", "？") else None
    return d.get("mark") if d.get("mark") in ("○", "×", "？") else None


def upper_bound(k: int, n: int, alpha: float = 0.05) -> float:
    """n 件中 k 件が誤りのとき、誤り率の片側 95% の上限（Clopper-Pearson、二分法）。"""
    from math import comb
    if n == 0:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        cdf = sum(comb(n, i) * mid ** i * (1 - mid) ** (n - i) for i in range(k + 1))
        lo, hi = (mid, hi) if cdf > alpha else (lo, mid)
    return hi


def check_recs(adj: dict, decisions: dict, recs: dict) -> None:
    """db の「推しどおり」に入っている印が、コミットした推し（確かなマス）と同じか。違えば止める。"""
    for it in adj["policies"] + adj["issues"]:
        d = decisions.get(it["id"], {})
        if d.get("choice") != "rec":
            continue
        want = {k: v["mark"] for k, v in recs["items"][it["id"]]["cells"].items() if v["sure"]}
        if (d.get("rec") or {}) != want:
            sys.exit(f"{it['id']} の「推しどおり」の印が eval/recommendations.json と合いません（ページの版が古い？）")
    for k, d in decisions.items():
        if k.startswith("x-") and d.get("mark") in MARKS:
            x = recs.get("extra", {}).get(k)
            if not x or (x["case"], x["num"]) != (d.get("case"), d.get("num")):
                sys.exit(f"{k} は eval/recommendations.json の「別に見るマス」にありません")


def source_of(it: dict, cell: dict, kind: str, decisions: dict) -> str:
    """マスの正解がどこから来たか（報告用）。"""
    d = decisions.get(it["id"], {})
    if (d.get("overrides") or {}).get(f"{cell['case']}-{cell['num']}"):
        return "先生が 1 マスずつ"
    return "推しどおり" if d.get("choice") == "rec" else f"先生が{'型' if kind == 'policy' else '論点'}ごとに"


def build_gold(adj: dict, decisions: dict, recs: dict | None = None) -> tuple[dict, dict]:
    """（事例→{制度番号: 印}、報告）を返す。決まっていないマスは入れない（空欄）。"""
    if recs:
        check_recs(adj, decisions, recs)
    rec_mark = {}
    if recs:
        rec_mark = {k: v["mark"] for x in recs["items"].values() for k, v in x["cells"].items()}
        rec_mark.update({f"{x['case']}-{x['num']}": x["mark"] for x in recs.get("extra", {}).values()})
    gold: dict[str, dict[int, str]] = defaultdict(dict)
    report = {"agreed": defaultdict(int), "policy": defaultdict(int), "issue": defaultdict(int),
              "undecided": [], "audit_n": 0, "audit_wrong": [], "audit_unmarked": 0,
              "source": {}, "policy_cells": [], "extra": {}, "rec_changed": []}
    for c in adj["agreed"]:
        gold[c["case"]][c["num"]] = c["mark"]
        report["agreed"][c["mark"]] += 1
        report["source"][f"{c['case']}-{c['num']}"] = "一致"
    for kind, items in (("policy", adj["policies"]), ("issue", adj["issues"])):
        for it in items:
            for cell in it["cells"]:
                key = f"{cell['case']}-{cell['num']}"
                if kind == "policy":
                    report["policy_cells"].append(key)
                m = decided_mark(it, cell, kind, decisions)
                if m is None:
                    report["undecided"].append(key)
                    continue
                gold[cell["case"]][cell["num"]] = m
                report[kind][m] += 1
                report["source"][key] = source_of(it, cell, kind, decisions)
                if key in rec_mark and rec_mark[key] != m:
                    report["rec_changed"].append({"cell": key, "rec": rec_mark[key], "final": m})
    # 一致したが、決まりの問いと同じ型で読みの理由が外れていたマス（別に見る。結果を見てから足した）
    for k, d in sorted(decisions.items()):
        if k.startswith("x-") and d.get("mark") in MARKS:
            key = f"{d['case']}-{d['num']}"
            gold[d["case"]][int(d["num"])] = d["mark"]
            report["extra"][key] = d["mark"]
            report["source"][key] = "別に見た"
            report["policy_cells"].append(key)
            if key in rec_mark and rec_mark[key] != d["mark"]:
                report["rec_changed"].append({"cell": key, "rec": rec_mark[key], "final": d["mark"]})
    for a in adj["audit"]:
        d = decisions.get(f"a-{a['case']}-{a['num']}", {})
        if d.get("mark") not in ("○", "×", "？"):
            report["audit_unmarked"] += 1
            continue
        report["audit_n"] += 1
        if d["mark"] != a["a"]["mark"]:
            report["audit_wrong"].append({"case": a["case"], "num": a["num"], "agreed": a["a"]["mark"], "teacher": d["mark"]})
            gold[a["case"]][a["num"]] = d["mark"]   # 柴田の印を正解にする
            report["source"][f"{a['case']}-{a['num']}"] = "抜き取りで直した"
    k, n = len(report["audit_wrong"]), report["audit_n"]
    report["audit_upper"] = round(upper_bound(k, n), 3) if n else None
    return gold, report


def write_gold(gold: dict, path: Path, blank: set[str] = frozenset()) -> None:
    """正解表を書く。blank に入れたマス（"c01-5" の形）は空欄にする。"""
    import csv
    rs = rules_mod.load()
    progs = sorted((p for p in rs.programs.values() if p.number in SCORED), key=lambda p: p.number)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["事例"] + [f"{p.number} {p.name}" for p in progs])
        for case in load_cases():
            w.writerow([case["id"]] + ["" if f"{case['id']}-{p.number}" in blank else gold.get(case["id"], {}).get(p.number, "")
                                       for p in progs])


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "gold":
        adj = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        decisions = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
        recs = json.loads(RECS.read_text(encoding="utf-8")) if RECS.exists() else None
        gold, rep = build_gold(adj, decisions, recs)
        write_gold(gold, ROOT / "eval" / "gold.csv")
        write_gold(gold, ROOT / "eval" / "gold_no_policy.csv", set(rep["policy_cells"]))
        path = OUT / f"gold-report-{datetime.now():%Y%m%d-%H%M}.json"
        path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"一致 {dict(rep['agreed'])}、決まりの問い {dict(rep['policy'])}、論点 {dict(rep['issue'])}、未決 {len(rep['undecided'])} マス")
        print(f"別に見たマス {rep['extra']}、推しと違う印になったマス {len(rep['rec_changed'])}")
        print(f"抜き取り {rep['audit_n']} 件中 {len(rep['audit_wrong'])} 件が誤り（上限 {rep['audit_upper']}）、未記入 {rep['audit_unmarked']}"
              f" → eval/gold.csv、決まりの問いの {len(rep['policy_cells'])} マスを空欄にした eval/gold_no_policy.csv")
        return
    if len(sys.argv) < 3 or sys.argv[1] != "prepare":
        sys.exit(__doc__)
    data = prepare(sys.argv[2:])
    path = OUT / f"adjudication-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    s = data["stats"]
    print(f"マス {s['cells']}：一致 {s['agreed']}（{dict(s['agreed_by_mark'])}）、論点に入れた {s['issue_cells']} マス → 論点 {s['issues']} 件")
    print(f"A と B の一致率 {s['a_b_agreement']}、B どうし {s['b_b_agreement']}、引用 {s['quotes']}、費用 {s['usd']}")
    print(f"抜き取り {len(data['audit'])} 件 → {path.relative_to(ROOT)}")
    for po in data["policies"]:
        progs = sorted({c["num"] for c in po["cells"]})
        print(f"  {po['id']} {po['title']}｜{len(po['cells'])} マス（制度 {','.join(map(str, progs))}）")
    for it in data["issues"]:
        print(f"  {it['id']} {it['num']:>2} {it['program']}｜{it['kind']}｜{it['a_why'][:50]}｜{len(it['cells'])} マス")


if __name__ == "__main__":
    main()
