"""手間と分かること（T-082）：押した数ごとに、その人の道で返った results を数えてためる関数（web/app.js の tally）。

図そのものはブラウザで撮って見る（ops の shot_t082.py）。ここでは数え方と、画面がその数を使っていることを確かめる。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def run_tally(steps: list[tuple[int, list[dict]]]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    consts = re.search(r"^// ---- いまの見立て ----\n(.*?)^const calm = ", JS, re.S | re.M).group(1)  # BOARD・colOf
    func = re.search(r"^const TYPE_LEVEL = .*?^function tally\(trail, presses, results\) \{\n.*?^\}\n", JS, re.S | re.M).group(0)
    run = f"""{consts}
{func}
let trail = [];
for (const [presses, results] of {json.dumps(steps, ensure_ascii=False)}) trail = tally(trail, presses, results);
console.log(JSON.stringify(trail));"""
    return json.loads(subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout)


def r(status: str, level: str = "国") -> dict:
    return {"id": f"{status}-{level}", "status": status, "level": level}


def test_counts_each_press_by_board_column():
    first = [r("eligible"), r("unknown"), r("unknown"), r("unknown", "市区町村（型）"), r("ineligible")]
    second = [r("eligible"), r("eligible"), r("unknown"), r("ineligible", "市区町村（型）"), r("ineligible")]
    trail = run_tally([(0, first), (1, second)])
    assert [t["presses"] for t in trail] == [0, 1]
    assert trail[0]["n"] == {"eligible": 1, "after_prereq": 0, "unknown": 3, "no": 1}
    assert trail[1]["n"] == {"eligible": 2, "after_prereq": 0, "unknown": 1, "no": 2}
    # 層は当てはまらないを除いて数える：型はプラスアルファの層、ほかは確かな層
    assert (trail[0]["sure"], trail[0]["extra"]) == (3, 1)
    assert (trail[1]["sure"], trail[1]["extra"]) == (3, 0)


def test_settled_programs_are_not_counted():
    # 済んでいる・申請済み（結果待ち）は、見立ての帯と同じく数えない（T-022）
    trail = run_tally([(0, [r("done"), r("pending"), r("after_prereq")])])
    assert trail[0]["n"] == {"eligible": 0, "after_prereq": 1, "unknown": 0, "no": 0}
    assert (trail[0]["sure"], trail[0]["extra"]) == (1, 0)


def test_same_press_count_replaces_the_last_bar():
    # 聞き返しをやめた・「ここでやめる」は押した数が増えないので、同じ棒を最後の応答で置き換える
    trail = run_tally([(0, [r("unknown")]), (1, [r("unknown")]), (1, [r("eligible")]), (2, [])])
    assert [t["presses"] for t in trail] == [0, 1, 2]
    assert trail[1]["n"]["eligible"] == 1 and trail[1]["n"]["unknown"] == 0
    assert sum(trail[2]["n"].values()) == 0


def test_screen_records_every_response_and_draws_after_the_result():
    render = re.search(r"^function render\(data, first = false\) \{\n.*?^\}\n", JS, re.S | re.M).group(0)
    # 帯を描く前に、その応答の results をためる（最初の読み取りも、問いへの答えも、結果の画面で答えたときも render を通る）
    assert render.index("state.trail = tally(state.trail, state.presses, data.results)") < render.index("renderBoard(data)")
    result = re.search(r"^function renderResult\(data\) \{\n.*?^\}\n", JS, re.S | re.M).group(0)
    assert "renderEffort();" in result
    assert "trail: []" in re.search(r"^function fresh\(\) \{\n.*?^\}\n", JS, re.S | re.M).group(0)  # 相談ごとに作り直す
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for id_ in ("effort", "effort-bars", "effort-legend", "effort-layers"):
        assert f'id="{id_}"' in html
    # 型の段の名前は app.py の LEVELS["local"] と同じ
    from todoku import rules as rules_mod
    assert f'const TYPE_LEVEL = "{rules_mod.LEVELS["local"]}";' in JS
