"""読み A（規則）と読み B（目隠しの Claude）を突き合わせ、先生が裁く論点と抜き取りを作る。

手順は eval/PROTOCOL.md。API は呼ばない。
  uv run python eval/adjudicate.py prepare eval/out/blind/read-A.json eval/out/blind/read-B.json
結果は eval/out/adjudication-<日時>.json（町田市の引用を含むので eval/out/ に置く。リポジトリに入れない）。

マスの印：○＝当たりうる、×＝当たらない（済んでいるを含む）、？＝決められない、申＝申請済み・結果待ち。
- 読み A：eligible・after_prereq→○、ineligible・done→×、unknown→？、pending→申
- 読み B：当たりうる→○、当たらない・済んでいる→×、決められない→？、申請済み・結果待ち→申
一致：A と B の全部の回が同じ印で、申を含まず、B の ○・× の引用がどれも原文に見つかる。
それ以外は論点に入れる。論点は「制度×A の印×A の決め手」でまとめる。
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
from todoku.engine import F, U, evaluate  # noqa: E402

from blind_read import LOCAL_TYPES, cases_in_scope, load_cases  # noqa: E402
from sources import CACHE  # noqa: E402

OUT = ROOT / "eval" / "out"
SEED = 20261103
AUDIT_YES = 20
AUDIT_NO = 20

A_MARK = {"eligible": "○", "after_prereq": "○", "ineligible": "×", "done": "×", "unknown": "？", "pending": "申"}
B_MARK = {"当たりうる": "○", "当たらない": "×", "済んでいる": "×", "決められない": "？", "申請済み・結果待ち": "申"}


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
    cells, agreed, issues = [], [], defaultdict(list)
    for case in cases:
        results = evaluate(rs, dict(case["answers"]))
        for num in nums:
            if case not in cases_in_scope(num, cases):
                continue
            prog = by_number[num]
            a_mark, a_why = a_reading(results[prog.id])
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
    stats = summarize(cells, agreed, issue_list, runs)
    return {"made": datetime.now().isoformat(timespec="seconds"), "seed": SEED, "runs": [r["_path"] for r in runs],
            "models": [r["model"] for r in runs], "programs": nums, "stats": stats,
            "issues": issue_list, "audit": audit}


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


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "prepare":
        sys.exit(__doc__)
    data = prepare(sys.argv[2:])
    path = OUT / f"adjudication-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    s = data["stats"]
    print(f"マス {s['cells']}：一致 {s['agreed']}（{dict(s['agreed_by_mark'])}）、論点に入れた {s['issue_cells']} マス → 論点 {s['issues']} 件")
    print(f"A と B の一致率 {s['a_b_agreement']}、B どうし {s['b_b_agreement']}、引用 {s['quotes']}、費用 {s['usd']}")
    print(f"抜き取り {len(data['audit'])} 件 → {path.relative_to(ROOT)}")
    for it in data["issues"]:
        print(f"  {it['id']} {it['num']:>2} {it['program']}｜{it['kind']}｜{it['a_why'][:50]}｜{len(it['cells'])} マス")


if __name__ == "__main__":
    main()
