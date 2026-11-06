"""読み A と読み B の突き合わせ（eval/adjudicate.py）と、読み B の入力（eval/blind_read.py）の決まり。API は呼ばない。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import adjudicate  # noqa: E402
import blind_read  # noqa: E402
import sources  # noqa: E402
from todoku import rules as rules_mod  # noqa: E402
from todoku.engine import evaluate  # noqa: E402


def test_norm_ignores_spacing_and_width():
    assert adjudicate.norm("６５歳 以上で\n市内に | 住所") == adjudicate.norm("65歳以上で市内に住所")


def test_quote_found_needs_verbatim_text(tmp_path, monkeypatch):
    monkeypatch.setattr(adjudicate, "CACHE", tmp_path)
    (tmp_path / "k1.txt").write_text("介護保険における要介護状態区分4または5の\n認定を受けている方", encoding="utf-8")
    srcs = [{"key": "k1"}]
    assert adjudicate.quote_found("要介護状態区分4または5の認定を受けている方", srcs)
    assert not adjudicate.quote_found("要介護3以上の認定を受けている方", srcs)  # 言い換えは通さない
    assert not adjudicate.quote_found("要介護", srcs)  # 短すぎる引用は通さない


def _case(cases, cid):
    return next(c for c in cases if c["id"] == cid)


def test_a_reading_marks_match_protocol():
    rs = rules_mod.load()
    cases = blind_read.load_cases()
    nintei = next(p for p in rs.programs.values() if p.number == 1)
    marks = {}
    for c in cases:
        res = evaluate(rs, dict(c["answers"]))[nintei.id]
        marks[c["answers"]["care_level"]] = adjudicate.a_reading(res)[0]
    assert marks.get("applying") == "申"     # 申請中は「申」で論点に回る
    assert marks.get("care1") == "×"         # 認定を受けている人の「認定の申請」は済んでいる＝×
    # 認定のない人：介助の事実（needs_daily_help、11/5 に事例へ書き足し）が「いいえ」なら ×、それ以外は ○ か ？
    for c in cases:
        if c["answers"]["care_level"] == "none":
            m = adjudicate.a_reading(evaluate(rs, dict(c["answers"]))[nintei.id])[0]
            assert m in (("×",) if c["answers"].get("needs_daily_help") is False else ("○", "？")), c["id"]


def test_local_types_only_for_people_outside_machida():
    cases = blind_read.load_cases()
    outside = [c["id"] for c in cases if c["answers"].get("lives_in_machida") is False]
    assert [c["id"] for c in blind_read.cases_in_scope(28, cases)] == outside
    assert len(blind_read.cases_in_scope(27, cases)) == len(cases)


def test_read_input_has_no_rules_text():
    """読み B に渡す文に、規則を書いた人の言葉（summary・条件の label・資料の title）が入らない。"""
    rs = rules_mod.load()
    index = sources.load_index()
    if not index:
        pytest.skip("公式資料が保存されていない（eval/sources.py fetch の前）")
    import re
    progs = sources.program_sources()
    checked = 0
    for p in rs.programs.values():
        if p.number not in blind_read.SCORED:   # 33 番以降は正解を作らないので、読み B にも渡さない
            continue
        try:
            text, _ = sources.program_text(p.number, progs, index)
        except RuntimeError:  # 読み B を流したあとに出典が替わった制度（例：22 は 11/3 夜に法令へ）は、保存した資料がない
            continue
        checked += 1
        assert p.summary not in text
        # 資料の見出し（【資料N】の行と URL の行）に、規則の title を書かない（本文に同じ語が出るのはかまわない）
        headers = re.findall(r"【資料\d+】[^\n]*\n[^\n]*", text)
        assert len(headers) == len(progs[p.number]["sources"])
        for s in progs[p.number]["sources"]:
            assert all(s.get("title", "") not in h for h in headers)
    assert checked >= 25, checked
    assert "label" not in blind_read.READ_SYSTEM and "規則" not in blind_read.READ_SYSTEM


def _fake_run(tmp_path, name, nums, flip=None, pending_as=None, bad_quote=None):
    """読み A と同じ印を返す偽の読み B。flip={(num, cid): state} で一部を変える。"""
    rs = rules_mod.load()
    by_number = {p.number: p for p in rs.programs.values()}
    cases = blind_read.load_cases()
    to_state = {"○": "当たりうる", "×": "当たらない", "？": "決められない", "申": "申請済み・結果待ち"}
    run = {"model": name, "label": "t", "usd": 0.0, "programs": {}}
    for num in nums:
        js = []
        for c in blind_read.cases_in_scope(num, cases):
            mark, _ = adjudicate.a_reading(evaluate(rs, dict(c["answers"]))[by_number[num].id])
            state = (flip or {}).get((num, c["id"]), to_state[mark])
            quote = "公式の条件の文をそのまま抜き出したもの"
            if bad_quote and (num, c["id"]) in bad_quote:
                quote = "原文にない言い換えの文です"
            js.append({"case": c["id"], "state": state, "deciding": "d", "quote": quote, "source": 1, "reason": "r"})
        run["programs"][str(num)] = {"name": by_number[num].name, "sources": [{"key": "k1"}], "judgments": js}
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_prepare_groups_disagreements_and_samples_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(adjudicate, "CACHE", tmp_path)
    (tmp_path / "k1.txt").write_text("公式の条件の文をそのまま抜き出したもの", encoding="utf-8")
    rs = rules_mod.load()
    cases = blind_read.load_cases()
    nintei = next(p for p in rs.programs.values() if p.number == 1)
    # A が × の事例を 2 件選び、B の片方だけ ○ にする → 同じ論点に 2 マス
    no_cases = [c["id"] for c in cases
                if c["answers"]["care_level"] not in ("applying", "jiritsu", "__dont_know__")
                and adjudicate.a_reading(evaluate(rs, dict(c["answers"]))[nintei.id])[0] == "×"][:2]
    flip = {(1, cid): "当たりうる" for cid in no_cases}
    r1 = _fake_run(tmp_path, "m1", [1, 11], flip=flip)
    r2 = _fake_run(tmp_path, "m2", [1, 11], bad_quote={(11, cases[0]["id"])})
    data = adjudicate.prepare([r1, r2])
    kinds = {(it["num"], it["kind"]): len(it["cells"]) for it in data["issues"]}
    # 申請中は、制度をまたぐ型「p-pending」に 1 つにまとまり、制度ごとの論点には残らない
    applying = sum(1 for c in cases if c["answers"]["care_level"] == "applying")
    pending = next(po for po in data["policies"] if po["id"] == "p-pending")
    assert sum(1 for c in pending["cells"] if c["num"] == 1) == applying
    assert (1, "申請済み・結果待ち") not in kinds
    # B が割れた 2 マスは「1 番・A は ×・同じ決め手」の論点に入る
    flipped = [it for it in data["issues"] if it["num"] == 1 and it["kind"] == "×"]
    assert sum(len(it["cells"]) for it in flipped) == 2
    # 印が全部そろっても、引用が原文にないマスは論点に回る
    assert kinds.get((11, "引用が原文に見つからない")) == 1
    s = data["stats"]
    # 制度 1 は全員、制度 11（町田市の制度）は 11/6 に足したほかの地域の事例（c31〜）を除く
    assert s["cells"] == len(cases) + len([c for c in cases if not blind_read.is_added(c)])
    assert s["agreed"] + s["issue_cells"] == s["cells"]
    # 抜き取りは一致したマスからだけ、同じ種なら同じ結果
    agreed_keys = {(c["case"], c["num"]) for c in data["audit"]}
    issue_keys = {(c["case"], c["num"]) for it in data["issues"] + data["policies"] for c in it["cells"]}
    assert not agreed_keys & issue_keys
    again = adjudicate.prepare([r1, r2])
    assert [(c["case"], c["num"]) for c in again["audit"]] == [(c["case"], c["num"]) for c in data["audit"]]


def test_policy_of_order():
    """制度をまたぐ型は、申請中 → 非該当 → 区分が分からない → 前の手続き待ち の順に当てはめる。"""
    from types import SimpleNamespace as NS
    from todoku.engine import P, U
    b = [{"mark": "×"}, {"mark": "×"}]
    leaf_p = NS(state=P, fact="care_level")
    leaf_u = NS(state=U, fact="care_level")
    case = lambda care: {"answers": {"care_level": care}}  # noqa: E731
    assert adjudicate.policy_of(case("applying"), NS(leaves=[leaf_p]), "○", b) == "p-pending"
    assert adjudicate.policy_of(case("care1"), NS(leaves=[]), "申", b) == "p-pending"
    assert adjudicate.policy_of(case("jiritsu"), NS(leaves=[leaf_p]), "○", b) == "p-jiritsu"
    assert adjudicate.policy_of(case("__dont_know__"), NS(leaves=[leaf_u]), "？", b) == "p-care-unknown"
    assert adjudicate.policy_of(case("none"), NS(leaves=[leaf_p]), "？", b) == "p-prereq"
    assert adjudicate.policy_of(case("care2"), NS(leaves=[]), "○", b) is None
    # 予定を「ある」にすると ○ になる × は「使う予定がない人」、ならないものは型に入れない
    assert adjudicate.policy_of(case("care1"), NS(leaves=[]), "×", b, NS(status="eligible", leaves=[])) == "p-plan"
    assert adjudicate.policy_of(case("care1"), NS(leaves=[]), "×", b, NS(status="ineligible", leaves=[])) is None


def _c(case, num, a, b1, b2):
    return {"case": case, "num": num, "a": {"mark": a, "why": ""}, "b": [{"mark": b1}, {"mark": b2}]}


def test_build_gold_follows_decisions():
    adj = {
        "agreed": [{"case": "c01", "num": 2, "mark": "×"}, {"case": "c02", "num": 2, "mark": "○"}],
        "policies": [{"id": "p-prereq", "cells": [_c("c01", 5, "○", "×", "×"), _c("c02", 5, "○", "×", "○")]},
                     {"id": "p-pending", "cells": [_c("c04", 1, "申", "申", "申")]}],
        "issues": [{"id": "i01", "cells": [_c("c03", 9, "×", "○", "○"), _c("c05", 9, "×", "○", "○")]}],
        "audit": [{"case": "c01", "num": 2, "a": {"mark": "×"}}, {"case": "c02", "num": 2, "a": {"mark": "○"}}],
    }
    decisions = {
        "p-prereq": {"choice": "r2", "resolved": "B", "overrides": {}},   # B に合わせる：割れた c02-5 は決まらない
        "p-pending": {"choice": "○"},
        "i01": {"mark": "○", "overrides": {"c05-9": "？"}},
        "a-c01-2": {"mark": "×"},
        "a-c02-2": {"mark": "×"},                                         # 抜き取りで誤りが 1 件
    }
    gold, rep = adjudicate.build_gold(adj, decisions)
    assert gold["c01"][5] == "×" and 5 not in gold["c02"]
    assert rep["undecided"] == ["c02-5"]
    assert gold["c04"][1] == "○"
    assert gold["c03"][9] == "○" and gold["c05"][9] == "？"
    assert gold["c02"][2] == "×"                                          # 柴田の印を正解にする
    assert rep["audit_n"] == 2 and len(rep["audit_wrong"]) == 1


def test_upper_bound_matches_rule_of_three():
    assert abs(adjudicate.upper_bound(0, 40) - 0.072) < 0.003
    assert adjudicate.upper_bound(2, 40) > adjudicate.upper_bound(1, 40) > adjudicate.upper_bound(0, 40)


def test_scored_programs_are_1_to_32():
    assert list(blind_read.SCORED) == list(range(1, 33))


def test_cleared_policy_choice_is_undecided():
    """「読み1 に合わせる」を押してから取り消した（choice が空、resolved が残った）決まりの問いは、決まっていない扱い。"""
    adj = {"agreed": [], "issues": [], "audit": [],
           "policies": [{"id": "p-plan", "cells": [_c("c01", 5, "×", "○", "○")]}]}
    for d in ({"choice": None, "resolved": "A"}, {"choice": None, "resolved": "B"}, {"resolved": "A"}):
        gold, rep = adjudicate.build_gold(adj, {"p-plan": d})
        assert 5 not in gold.get("c01", {}) and rep["undecided"] == ["c01-5"]
    gold, _ = adjudicate.build_gold(adj, {"p-plan": {"choice": "r2", "resolved": "B"}})
    assert gold["c01"][5] == "○"
    gold, _ = adjudicate.build_gold(adj, {"p-plan": {"choice": "×", "resolved": "×"}})
    assert gold["c01"][5] == "×"


def _recs():
    return {"items": {
        "p-plan": {"cells": {"c01-5": {"mark": "○", "sure": True}, "c02-5": {"mark": "×", "sure": False}}},
        "i01": {"cells": {"c03-9": {"mark": "○", "sure": True}}}},
        "extra": {"x-c16-6": {"case": "c16", "num": 6, "mark": "○", "sure": False}}}


def _adj_rec():
    return {"agreed": [{"case": "c16", "num": 6, "mark": "×"}], "audit": [],
            "policies": [{"id": "p-plan", "cells": [_c("c01", 5, "×", "○", "○"), _c("c02", 5, "×", "○", "×")]}],
            "issues": [{"id": "i01", "cells": [_c("c03", 9, "×", "○", "○")]}]}


def test_rec_choice_uses_sure_cells_and_leaves_unsure_to_teacher():
    """「推しどおり」は確かなマスだけに効く。迷うマスは 1 マスずつ押すまで決まらない。"""
    adj, recs = _adj_rec(), _recs()
    d = {"p-plan": {"choice": "rec", "rec": {"c01-5": "○"}}}
    gold, rep = adjudicate.build_gold(adj, d, recs)
    assert gold["c01"][5] == "○" and rep["undecided"] == ["c02-5", "c03-9"]
    assert rep["source"]["c01-5"] == "推しどおり"
    # 迷うマスを 1 マスずつ押す → 推しと違えば数える
    d["p-plan"]["overrides"] = {"c02-5": "？"}
    gold, rep = adjudicate.build_gold(adj, d, recs)
    assert gold["c02"][5] == "？" and rep["source"]["c02-5"] == "先生が 1 マスずつ"
    assert rep["rec_changed"] == [{"cell": "c02-5", "rec": "×", "final": "？"}]
    # 1 マスを「全体の答えに戻す」（overrides から消す）と推しに戻る
    d["p-plan"]["overrides"] = {}
    d["p-plan"]["rec"]["c01-5"] = "○"
    gold, _ = adjudicate.build_gold(adj, d, recs)
    assert gold["c01"][5] == "○"


def test_issue_mark_and_rec_switch_both_ways():
    adj, recs = _adj_rec(), _recs()
    gold, rep = adjudicate.build_gold(adj, {"i01": {"choice": "rec", "rec": {"c03-9": "○"}, "mark": None}}, recs)
    assert gold["c03"][9] == "○" and rep["source"]["c03-9"] == "推しどおり"
    gold, rep = adjudicate.build_gold(adj, {"i01": {"choice": None, "rec": {"c03-9": "○"}, "mark": "×"}}, recs)
    assert gold["c03"][9] == "×" and rep["source"]["c03-9"] == "先生が論点ごとに"
    assert rep["rec_changed"] == [{"cell": "c03-9", "rec": "○", "final": "×"}]


def test_rec_in_db_must_match_committed_file():
    adj, recs = _adj_rec(), _recs()
    with pytest.raises(SystemExit):
        adjudicate.build_gold(adj, {"p-plan": {"choice": "rec", "rec": {"c01-5": "×"}}}, recs)
    with pytest.raises(SystemExit):   # 迷うマスまで「推しどおり」に入っているのは古いページ
        adjudicate.build_gold(adj, {"p-plan": {"choice": "rec", "rec": {"c01-5": "○", "c02-5": "×"}}}, recs)
    with pytest.raises(SystemExit):   # 推しのファイルにない「別に見るマス」
        adjudicate.build_gold(adj, {"x-c09-6": {"mark": "○", "case": "c09", "num": 6}}, recs)


def test_extra_cell_overrides_agreed_and_is_blanked_in_no_policy(tmp_path):
    adj, recs = _adj_rec(), _recs()
    d = {"p-plan": {"choice": "○"}, "x-c16-6": {"mark": "○", "case": "c16", "num": 6}}
    gold, rep = adjudicate.build_gold(adj, d, recs)
    assert gold["c16"][6] == "○" and rep["source"]["c16-6"] == "別に見た"
    assert set(rep["policy_cells"]) == {"c01-5", "c02-5", "c16-6"}
    path = tmp_path / "g.csv"
    adjudicate.write_gold(gold, path, set(rep["policy_cells"]))
    import csv
    rows = {r["事例"]: r for r in csv.DictReader(path.open(encoding="utf-8-sig"))}
    col5 = next(k for k in rows["c01"] if k.startswith("5 "))
    col6 = next(k for k in rows["c16"] if k.startswith("6 "))
    assert rows["c01"][col5] == "" and rows["c16"][col6] == ""


def test_committed_recommendations_cover_every_disputed_cell():
    """コミットした推しは、裁定の材料のすべてのマスに 1 つずつ印を持ち、町田市などの文を引いていない。"""
    recs = json.loads((ROOT / "eval" / "recommendations.json").read_text(encoding="utf-8"))
    n = sum(len(x["cells"]) for x in recs["items"].values())
    assert n == 165 and set(recs["extra"]) == {"x-c16-6", "x-c17-6"}
    for x in recs["items"].values():
        for v in x["cells"].values():
            assert v["mark"] in ("○", "×", "？") and v["why"] and isinstance(v["sure"], bool)
    # 公式資料の本文（手元に取ってあるときだけ。.gitignore 済み）と 20 字以上続けて同じ文がない
    texts = [adjudicate.norm(f.read_text(encoding="utf-8")) for f in sources.CACHE.glob("*.txt")] if sources.CACHE.exists() else []
    if not texts:
        pytest.skip("公式資料の本文が手元にない")
    whys = [v["why"] for x in recs["items"].values() for v in x["cells"].values()] + [x["summary"] for x in recs["items"].values()]
    whys += [x["why"] for x in recs["extra"].values()]
    for w in whys:
        n = adjudicate.norm(w)
        hits = [n[i:i + 20] for i in range(max(0, len(n) - 19)) if any(n[i:i + 20] in t for t in texts)]
        assert not hits, (w, hits[:1])


def test_stale_policy_choice_stops_gold_when_version_given():
    """推しを付けたあとのページで押していない問い（前回の「全部 ？」のまま）は、書き出しの段で止める。"""
    adj, recs = _adj_rec(), _recs()
    d = {"p-plan": {"choice": "？", "resolved": "？"},
         "i01": {"choice": "rec", "rec": {"c03-9": "○"}, "rec_seen": "v1"},
         "x-c16-6": {"mark": "○", "case": "c16", "num": 6, "rec_seen": "v1"}}
    with pytest.raises(SystemExit):
        adjudicate.build_gold(adj, d, recs, "v1")
    d["p-plan"] = {"choice": "rec", "rec": {"c01-5": "○"}, "overrides": {"c02-5": "×"}, "rec_seen": "v1"}
    gold, rep = adjudicate.build_gold(adj, d, recs, "v1")
    assert rep["undecided"] == [] and rep["rec_changed"] == []
    assert adjudicate.rec_version() == __import__("hashlib").sha256((ROOT / "eval" / "recommendations.json").read_bytes()).hexdigest()[:12]


def test_chat_cells_are_reported_separately():
    """先生がチャットで一括して答え、Claude が db に書いたマスは、出所を分けて数える。"""
    adj, recs = _adj_rec(), _recs()
    d = {"p-plan": {"choice": "rec", "rec": {"c01-5": "○"}, "overrides": {"c02-5": "×"}, "chat_cells": ["c02-5"]},
         "i01": {"choice": "rec", "rec": {"c03-9": "○"}, "chat_cells": ["c03-9"]},
         "x-c16-6": {"mark": "○", "case": "c16", "num": 6, "chat_cells": ["c16-6"]}}
    gold, rep = adjudicate.build_gold(adj, d, recs)
    assert rep["source"] == {"c16-6": "別に見た（チャットで推しどおり）", "c01-5": "推しどおり",
                             "c02-5": "チャットで推しどおり", "c03-9": "チャットで推しどおり"}
    assert set(rep["decided_cells"]) == {"c01-5", "c02-5", "c03-9", "c16-6"} and rep["rec_changed"] == []


def test_merged_gold_combines_rounds_and_rejects_overlap(tmp_path, monkeypatch):
    """裁定の回（11/4 の 30 件と 11/6 の 12 件）を合わせる。同じマスが 2 回出たら止める。"""
    a1 = {"agreed": [{"case": "c01", "num": 2, "mark": "×"}], "audit": [], "issues": [],
          "policies": [{"id": "p-plan", "cells": [_c("c01", 5, "×", "○", "○")]}]}
    a2 = {"agreed": [{"case": "c31", "num": 2, "mark": "○"}], "audit": [], "policies": [],
          "issues": [{"id": "i01", "cells": [_c("c31", 3, "○", "×", "×")]}]}
    for name, obj in (("adj1.json", a1), ("adj2.json", a2), ("d1.json", {"p-plan": {"choice": "○"}}),
                      ("d2.json", {"i01": {"mark": "×"}})):
        (tmp_path / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adjudicate, "recs_for", lambda p: (None, None))
    gold, rep = adjudicate.merged_gold([(tmp_path / "adj1.json", tmp_path / "d1.json"), (tmp_path / "adj2.json", tmp_path / "d2.json")])
    assert gold["c01"] == {2: "×", 5: "○"} and gold["c31"] == {2: "○", 3: "×"}
    assert rep["agreed"] == {"×": 1, "○": 1} and set(rep["decided_cells"]) == {"c01-5", "c31-3"}
    with pytest.raises(SystemExit):
        adjudicate.merged_gold([(tmp_path / "adj1.json", tmp_path / "d1.json"), (tmp_path / "adj1.json", tmp_path / "d1.json")])
