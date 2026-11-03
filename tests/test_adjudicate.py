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
    assert marks.get("none") in ("○", "？")


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
    for p in rs.programs.values():
        text, _ = sources.program_text(p.number, progs, index)
        assert p.summary not in text
        # 資料の見出し（【資料N】の行と URL の行）に、規則の title を書かない（本文に同じ語が出るのはかまわない）
        headers = re.findall(r"【資料\d+】[^\n]*\n[^\n]*", text)
        assert len(headers) == len(progs[p.number]["sources"])
        for s in progs[p.number]["sources"]:
            assert all(s.get("title", "") not in h for h in headers)
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
                if adjudicate.a_reading(evaluate(rs, dict(c["answers"]))[nintei.id])[0] == "×"][:2]
    flip = {(1, cid): "当たりうる" for cid in no_cases}
    r1 = _fake_run(tmp_path, "m1", [1, 11], flip=flip)
    r2 = _fake_run(tmp_path, "m2", [1, 11], bad_quote={(11, cases[0]["id"])})
    data = adjudicate.prepare([r1, r2])
    kinds = {(it["num"], it["kind"]): len(it["cells"]) for it in data["issues"]}
    # 申請中は 1 つの論点にまとまる
    applying = sum(1 for c in cases if c["answers"]["care_level"] == "applying")
    assert kinds.get((1, "申請済み・結果待ち")) == applying
    # B が割れた 2 マスは「1 番・A は ×・同じ決め手」の論点に入る
    flipped = [it for it in data["issues"] if it["num"] == 1 and it["kind"] == "×"]
    assert sum(len(it["cells"]) for it in flipped) == 2
    # 印が全部そろっても、引用が原文にないマスは論点に回る
    assert kinds.get((11, "引用が原文に見つからない")) == 1
    s = data["stats"]
    assert s["cells"] == 2 * len(cases)
    assert s["agreed"] + s["issue_cells"] == s["cells"]
    # 抜き取りは一致したマスからだけ、同じ種なら同じ結果
    agreed_keys = {(c["case"], c["num"]) for c in data["audit"]}
    issue_keys = {(c["case"], c["num"]) for it in data["issues"] for c in it["cells"]}
    assert not agreed_keys & issue_keys
    again = adjudicate.prepare([r1, r2])
    assert [(c["case"], c["num"]) for c in again["audit"]] == [(c["case"], c["num"]) for c in data["audit"]]
