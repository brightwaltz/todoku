"""Dockerfile が root でない利用者でアプリを動かすか（11/4 T-036）、元のイメージを固定しているか（T-038）。

11/3 の安全点検で「USER がないので root で動く」「元のイメージが版で固定されていない」と指摘された。
docker を使わずに Dockerfile の文だけを見る。手元での確かめ（docker build・起動・見本）は LOG に書いた。
"""

from __future__ import annotations

import re
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


def test_base_image_is_pinned_by_version_and_digest():
    froms = [rest for word, rest in _instructions() if word == "FROM"]
    assert froms, "FROM がない"
    for image in froms:
        assert re.fullmatch(r"python:\d+\.\d+\.\d+-slim(-\w+)?@sha256:[0-9a-f]{64}", image), image
