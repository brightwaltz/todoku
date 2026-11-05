"""結果の画面の「これからの流れ」（T-120、先回りの提案の最小版）。

判定の結果（状態）と規則の「先に要る手続き」（waiting_on）・費用の形（cost.self_pay）・時期（timing）だけから、
いま → 〈手続き〉の結果が出たら → 次の確定申告の時期 の順に並べる。判定はせず、Claude も呼ばない。
"""

import json
import os
import re
import shutil
import subprocess

import pytest

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from todoku import app as app_mod  # noqa: E402

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")


def run_ahead(data: dict) -> list:
    if not shutil.which("node"):
        pytest.skip("node が無い")
    src = re.search(r'const AHEAD_NOW = .*?\nfunction aheadSteps\(data\) \{.*?\n\}\n', JS, re.S).group(0)
    code = src + (f"\nconsole.log(JSON.stringify(aheadSteps({json.dumps(data, ensure_ascii=False)})"
                  ".map((s) => [s.when, s.note, s.items.map((t) => [t.r.id, t.note])])));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def r(id_, status, waiting=(), self_pay="none", timing=(), pending=False):
    return {"id": id_, "name": f"{id_}の名前", "status": status, "cost": {"self_pay": self_pay}, "timing": list(timing),
            "waiting_on": [{"id": w, "name": f"{w}の名前", "pending": pending} for w in waiting]}


def test_steps_follow_prerequisites_then_tax_time():
    data = {"results": [
        r("nintei", "eligible", timing=["結果は原則として申請から30日以内に届く"]),
        r("yogu", "after_prereq", waiting=["nintei"], self_pay="partial", timing=["買ったあとに申請する"]),
        r("kaishu", "after_prereq", waiting=["nintei"], self_pay="partial"),
        r("iryohi", "eligible", self_pay="tax", timing=["その年の分は、翌年の1月1日以降に申請する"]),
        r("kojo", "after_prereq", waiting=["nintei"], self_pay="tax"),
        r("maybe", "unknown"),
        r("no", "ineligible"),
    ]}
    assert run_ahead(data) == [
        ["いま", "", [["nintei", ""]]],
        ["ninteiの名前の結果が出たら", "結果は原則として申請から30日以内に届く", [["yogu", "買ったあとに申請する"], ["kaishu", ""]]],
        ["次の確定申告の時期（2〜3月）", "", [["iryohi", "その年の分は、翌年の1月1日以降に申請する"],
                                    ["kojo", "先にninteiの名前が要ります"]]],
    ]


def test_no_flow_when_only_now_and_unknown_is_never_listed():
    # 区切りが「いま」だけなら出さない。確かめる・当てはまらない制度は並べない（判定の結果を言い換えない）
    assert run_ahead({"results": [r("a", "eligible"), r("b", "unknown"), r("c", "ineligible")]}) == []
    got = run_ahead({"results": [r("x", "unknown", self_pay="tax"), r("y", "after_prereq", waiting=["p"])]})
    assert got == [["pの名前の結果が出たら", "", [["y", ""]]]]  # 前提が結果に無いときは目安を出さない


def test_program_waiting_on_two_is_listed_once():
    got = run_ahead({"results": [r("p", "eligible"), r("q", "eligible"), r("z", "after_prereq", waiting=["p", "q"])]})
    assert [s[0] for s in got] == ["いま", "pの名前の結果が出たら"]
    assert sum(len(s[2]) for s in got) == 3


def test_ahead_sits_after_map_and_before_later_and_is_rendered_from_result():
    result = HTML.split('<section id="result" hidden>', 1)[1].split("</section>", 1)[0]
    assert result.index('id="pmap-fold"') < result.index('id="ahead"') < result.index('id="later"')
    assert re.search(r'<div class="card ahead" id="ahead" hidden>', result)
    body = JS.split("function renderResult(data) {", 1)[1].split("\n}\n", 1)[0]
    assert "renderAhead(data);" in body
    # 先回りの提案は外へ送らない：aheadSteps と renderAhead は api() も fetch も使わない
    part = JS.split("const AHEAD_NOW", 1)[1].split("function renderResult(data)", 1)[0]
    assert "api(" not in part and "fetch(" not in part and "localStorage" not in part
