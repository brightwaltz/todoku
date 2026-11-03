"""Dockerfile が root でない利用者でアプリを動かすか（11/4 T-036）。

11/3 の安全点検で「USER がないので root で動く」と指摘された。docker を使わずに Dockerfile の文だけを見る。
手元での確かめ（docker build・起動・見本 4 件を最後まで）は LOG に書いた。
"""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")


def _instructions() -> list[tuple[str, str]]:
    out = []
    for line in DOCKERFILE.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            word, _, rest = line.partition(" ")
            out.append((word.upper(), rest.strip()))
    return out


def test_app_runs_as_non_root_user():
    steps = _instructions()
    cmd = max(i for i, (word, _) in enumerate(steps) if word in ("CMD", "ENTRYPOINT"))
    users = [rest for word, rest in steps[:cmd] if word == "USER"]
    assert users, "CMD の前に USER がない（root で動く）"
    user = users[-1].split(":")[0]
    assert user not in ("root", "0"), users[-1]
