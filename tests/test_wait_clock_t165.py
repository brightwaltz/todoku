"""T-165：本物の Claude の読み取り（/api/consult）を待つ間、3 秒を過ぎたら読み取り中の札に経過の秒を足す。

静止した「読み取っています…」だけだと、十数秒の待ちで止まって見える。見本・出来事の道は Claude を呼ばず速いので付けない。
読み上げ（#status）は毎秒は読まない。札の文が替わったら（失敗の知らせ・別の相談）前の時計は書かない。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")


def _func(name: str) -> str:
    return re.search(rf"^(async )?function {name}\(.*?^\}}\n", JS, re.S | re.M).group(0)


def _run(scenario: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    run = f"""
let now = 0, ticks = [], cleared = [];
Date.now = () => now;
const setInterval = (fn) => {{ ticks.push(fn); return ticks.length; }};
const clearInterval = (id) => {{ cleared.push(id); }};
const mkP = (t) => ({{ textContent: t }});
const reading = {{ firstElementChild: mkP("読み取っています…") }};
const $ = (id) => reading;
const tick = (ms) => {{ now = ms; ticks[0](); }};
{_func("withClock")}
(async () => {{
{scenario}
}})();
"""
    out = subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_seconds_appear_after_three_seconds_and_stop_when_answered():
    got = _run("""
  let done;
  const p = withClock(new Promise((r) => { done = r; }));
  const first = reading.firstElementChild;
  const seen = [];
  for (const ms of [1000, 2000, 2999, 3000, 12400]) { tick(ms); seen.push(first.textContent); }
  done("答え");
  const value = await p;
  console.log(JSON.stringify({ seen, value, cleared }));
""")
    assert got["seen"] == [
        "読み取っています…", "読み取っています…", "読み取っています…",
        "読み取っています…（3 秒）", "読み取っています…（12 秒）",
    ]
    assert got["value"] == "答え"  # 包んでも答えはそのまま返す
    assert got["cleared"] == [1]   # 答えが返ったら時計を止める


def test_clock_stops_on_failure_and_passes_the_error_on():
    got = _run("""
  let fail;
  const p = withClock(new Promise((_, r) => { fail = r; }));
  fail(new Error("通信できませんでした"));
  let msg = "";
  try { await p; } catch (e) { msg = e.message; }
  console.log(JSON.stringify({ msg, cleared }));
""")
    assert got == {"msg": "通信できませんでした", "cleared": [1]}


def test_old_clock_does_not_write_over_the_next_card():
    # 待つ間に見本・別の相談を始めると consultWith が札の中身を替える。失敗の知らせに替えたときも同じ（T-162 の道）
    got = _run("""
  withClock(new Promise(() => {}));
  tick(4000);
  const next = mkP("読み取っています…");
  reading.firstElementChild = next;
  tick(9000);
  console.log(JSON.stringify({ next: next.textContent, cleared }));
""")
    assert got == {"next": "読み取っています…", "cleared": [1]}


def test_only_the_claude_reading_gets_the_clock_and_screen_readers_are_not_told_every_second():
    send = _func("send")
    assert 'withClock(api("/api/consult"' in send
    # 見本・出来事は Claude を呼ばず速い。時計は付けない
    assert 'consultWith(s.text, () => api("/api/sample"' in JS
    assert 'consultWith(text, () => api("/api/event"' in JS
    clock = _func("withClock")
    assert "say(" not in clock and "aria-live" not in clock
    # consultWith は今までどおり（読み上げの順は test_app.py の T-030 の検査が見る）
    assert 'say("読み取っています…")' in _func("consultWith")
