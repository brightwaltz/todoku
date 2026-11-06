"""scripts/check_live.py（deploy した窓口を外から確かめる台本）を、手元に立てたサーバーで通す（T-059）。

本物の deploy の先には送らない。試験モード（TODOKU_FAKE_CLAUDE=1）と仮の合言葉で uvicorn を別のプロセスで立て、
台本をそのまま `python3 -I` で動かす。本番で台本を流すのは、deploy した人が自分の端末で行う。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_live.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(extra_env: dict[str, str], log=subprocess.DEVNULL):
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "TODOKU_PASSPHRASE", "FLY_APP_NAME", "TODOKU_OPEN", "TODOKU_DAILY_USD", "TODOKU_MACHINES")}
    env.update({"TODOKU_FAKE_CLAUDE": "1", **extra_env})
    # log を渡すと uvicorn の要求の記録（標準出力）をそこへ書く（送らなかったことを確かめるため。T-194）
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "todoku.app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1):
                return proc, base
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    proc.kill()
    pytest.fail("uvicorn が 30 秒で立たない")


@pytest.fixture(scope="module")
def server():
    proc, base = _serve({"TODOKU_PASSPHRASE": "dummy-for-test"})
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, timeout=120)


def test_all_checks_pass_on_local_server(server):
    res = _run(server, "--allow-fake")
    assert res.returncode == 0, res.stdout + res.stderr
    lines = res.stdout.splitlines()
    assert not [l for l in lines if l.startswith("NG")], res.stdout
    # 見本は全部を結果まで通す。合言葉なしの /api/consult は 401 で断られる
    samples = (ROOT / "web" / "samples.json").read_text(encoding="utf-8")
    assert sum(1 for l in lines if l.startswith("OK  見本「")) == samples.count('"reading"') >= 4, res.stdout
    assert any(l.startswith("OK  合言葉なしの /api/consult が 401") for l in lines), res.stdout
    assert any(l.startswith("OK  /api/sample を gzip で縮めて返す") for l in lines), res.stdout


def test_fake_engine_is_ng_without_allow_fake(server):
    # 本番の形（本物の Claude）を求める既定では、試験モードの窓口は NG になる（鍵の設定し忘れに気づける）
    res = _run(server)
    assert res.returncode == 1, res.stdout
    assert "NG  本物の Claude で動いている" in res.stdout, res.stdout


def test_unreachable_is_ng():
    res = _run(f"http://127.0.0.1:{_free_port()}")
    assert res.returncode == 1 and res.stdout.startswith("NG"), res.stdout


def test_expect_open_is_ng_where_passphrase_is_required(server):
    # --expect-open は、TODOKU_OPEN=1 の設定し忘れ（合言葉で止まる形のまま）を NG にする（T-194）
    res = _run(server, "--allow-fake", "--expect-open")
    assert res.returncode == 1, res.stdout
    assert "NG  合言葉なしで相談を送れる（TODOKU_OPEN=1）" in res.stdout, res.stdout
    # 合言葉が要る置き場なので、今までどおり合言葉なしの /api/consult が 401 かを確かめる
    assert "OK  合言葉なしの /api/consult が 401" in res.stdout, res.stdout


@pytest.mark.parametrize("extra, closed", [
    ({"TODOKU_PASSPHRASE": "dummy-for-test", "TODOKU_DAILY_USD": "30", "TODOKU_MACHINES": "2"}, False),
    # 上限 0 は、使った額 0 でも残り 0 なので受付を終えた日の形になる。合言葉も無し（open では管理者の外し方なので要らない）
    ({"TODOKU_DAILY_USD": "0"}, True),
])
def test_open_server_passes_without_sending_consult(tmp_path, extra, closed):
    # T-194：TODOKU_OPEN=1 の置き場に合言葉なしで /api/consult を送ると、本番では本物の Claude が読み取る
    # （費用がかかり、1 日の上限にも数えられる）。前の台本はそれを送り、200 なので NG と言っていた
    log = tmp_path / "access.log"
    with log.open("w") as f:
        proc, base = _serve({"TODOKU_OPEN": "1", **extra}, log=f)
        try:
            res = _run(base, "--allow-fake", "--expect-open")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    assert res.returncode == 0, res.stdout + res.stderr
    lines = res.stdout.splitlines()
    assert not [l for l in lines if l.startswith("NG")], res.stdout
    assert any(l.startswith("OK  合言葉なしで相談を送れる（TODOKU_OPEN=1）") for l in lines), res.stdout
    limit = [l for l in lines if l.startswith("OK  1 日の費用の上限が出ている")]
    assert len(limit) == 1, res.stdout
    # 受付を終えた日も NG にはしない（見本は動く）が、そうと分かるように書く。その日も /api/consult には送らない
    assert ("今日の受付を終えている" in limit[0]) is closed, limit[0]
    assert any(l.startswith("OK  合言葉なしの /api/consult（open の置き場では Claude が読み取るので送らない）") for l in lines), res.stdout
    assert not any("/api/consult が 401" in l for l in lines), res.stdout
    # 見本は今までどおり全部を結果まで通す
    samples = (ROOT / "web" / "samples.json").read_text(encoding="utf-8")
    assert sum(1 for l in lines if l.startswith("OK  見本「")) == samples.count('"reading"') >= 4, res.stdout
    # サーバの要求の記録に /api/consult が 1 つも無い（Claude を呼ぶ口には送っていない）
    access = log.read_text(encoding="utf-8")
    assert "/api/sample" in access, access[-2000:]
    assert "/api/consult" not in access and "/api/summary" not in access, access[-2000:]
