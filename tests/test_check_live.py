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


def _serve(extra_env: dict[str, str]):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "TODOKU_PASSPHRASE", "FLY_APP_NAME")}
    env.update({"TODOKU_FAKE_CLAUDE": "1", **extra_env})
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "todoku.app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
