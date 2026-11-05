"""結果の画面の先頭の「いま先にすること」（T-121）。判定の結果だけから 1〜3 件を選び、長い図と一覧は下に閉じて置く。"""

import json
import os
import re
import shutil
import subprocess

import pytest

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku.app import app  # noqa: E402

client = TestClient(app)
JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (app_mod.WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_result_starts_with_todo_and_folds_long_parts():
    """結果の画面の並び（T-133 で T-121 の並びから替えた）：いま先にすること（1 行）→ 当てはまる見込みの札 → 制度の地図（開）
    → まだ確かめていないこと → まとめ → ほかの札の一覧 → 閉じた欄（有料と民間）。押した数の図は結果の頁の外の別の頁。"""
    result = HTML.split('<section id="result" hidden>', 1)[1].split("</section>", 1)[0]
    at = {k: result.index(f'id="{k}"') for k in ("todo", "groups-top", "pmap-fold", "later", "summary", "groups", "options-box", "keep")}
    assert list(at) == sorted(at, key=at.get), at
    tag = re.search(r'<(\w+)[^>]*id="pmap-fold"[^>]*>', result)
    assert tag.group(1) == "details" and " open" in tag.group(0), tag.group(0)
    tag = re.search(r'<(\w+)[^>]*id="options-box"[^>]*>', result)
    assert tag.group(1) == "details" and " open" not in tag.group(0), tag.group(0)
    assert result.index('id="pmap-fold"') < result.index('id="pmap"') < result.index('id="later"')
    # 押した数の図は別の頁。結果の頁には入口のボタンだけ
    assert 'id="effort"' not in result and 'id="effort-open"' in result
    page = HTML.split('<section id="effort-page"', 1)[1].split("</section>", 1)[0]
    assert 'id="effort"' in page and 'id="effort-back"' in page
    assert 'show("effort-open"' in JS and 'show("pmap-fold"' in JS
    # 先頭に出すのは 1 行だけ。確かめるの札の一覧は畳んだ欄。件数は札の一覧の見出しだけ（地図の凡例・結果の頁の帯には出さない）
    assert "const TODO_SHOWN = 1;" in JS and "todoItems(data).slice(0, TODO_SHOWN)" in JS
    assert 'id: "unknown-fold"' in JS
    legend = JS.split('$("pmap-legend").replaceChildren(', 1)[1].split("\n", 1)[0]
    assert "件" not in legend, legend
    assert 'show("board", false);  // T-133' in JS


def run_todo(data: dict) -> list:
    if not shutil.which("node"):
        pytest.skip("node が無い")
    src = re.search(r"const TODO_MAX = \d+;\nfunction todoItems\(data\) \{.*?\n\}\n", JS, re.S).group(0)
    code = src + f"\nconsole.log(JSON.stringify(todoItems({json.dumps(data, ensure_ascii=False)}).map((t) => t.later ? ['later', t.why] : [t.r.id, t.why])));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def r(id_, status, waiting=(), reasons=(), missing=()):
    return {"id": id_, "name": id_, "status": status, "waiting_on": [{"id": w, "name": w, "pending": False} for w in waiting],
            "reasons": [{"state": "T", "label": x} for x in reasons], "missing": [{"name": m} for m in missing]}


def test_todo_puts_the_gate_first_then_eligible_up_to_three():
    data = {"unconfirmed": [], "results": [
        r("a", "eligible", reasons=["70歳以上"]), r("nintei", "eligible"), r("b", "eligible"), r("c", "eligible"),
        r("x", "after_prereq", waiting=["nintei"]), r("y", "after_prereq", waiting=["nintei"]), r("u", "unknown")]}
    got = run_todo(data)
    assert [g[0] for g in got] == ["nintei", "a", "b"]
    assert "2 件" in got[0][1] and got[1][1] == "当てはまる理由：70歳以上"


def test_todo_points_to_the_confirm_question_and_falls_back_to_unknown():
    got = run_todo({"unconfirmed": [{"fact": "care_level"}], "results": [r("a", "eligible")]})
    assert [g[0] for g in got] == ["a", "later"]
    got = run_todo({"unconfirmed": [], "results": [r("u", "unknown", missing=["年齢"]), r("n", "ineligible")]})
    assert got == [["u", "分からない点：年齢"]]


@pytest.mark.parametrize("sample", ["taiin-haha", "mayou-chichi", "kaigo4-sobo", "yokohama-chichi"])
def test_todo_on_samples_is_one_to_three_programs_on_the_screen(sample):
    data = client.post("/api/sample", json={"id": sample}).json()
    got = run_todo(data)
    ids = {x["id"]: x["status"] for x in data["results"]}
    assert 1 <= len(got) <= 3
    assert all(g[0] == "later" or ids[g[0]] in ("eligible", "unknown") for g in got)
