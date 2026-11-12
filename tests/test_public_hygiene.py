"""公開リポジトリに個人情報・手元のパスが入っていないか（git が追跡するファイルを全部読む）。

文字列の照合なので、手間を増やすだけの守り。消した個人情報・手元のパスがまた入ったら気づくためのもの。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 型の文字列は分けて組み立てる（このファイル自身が当たらないように）
PATTERNS = [
    ("手元のホームのパス", re.compile("/" + "Users/[A-Za-z0-9._-]+/")),
    ("~/.config の鍵の置き場", re.compile(re.escape("~/." + "config/"))),
    ("個人のメールアドレス", re.compile(re.escape("@" + "gmail" + ".com"))),
]


def _tracked() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git が無い")
    r = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        pytest.skip("git のリポジトリでない")
    return [p for p in r.stdout.decode("utf-8", errors="replace").split("\0") if p]


def test_no_personal_info_or_local_paths():
    hits = []
    for rel in _tracked():
        path = ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8000]:
            continue
        for no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for name, pat in PATTERNS:
                if pat.search(line):
                    hits.append(f"{rel}:{no}（{name}）")
    assert not hits, "個人情報・手元のパスが入っている：\n" + "\n".join(hits)
