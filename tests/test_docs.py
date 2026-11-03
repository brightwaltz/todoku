"""README と docs/spec.md に書いた数が、規則と見本の今の数と合っているか。

制度を足すたびに文書の数がずれた（11/4 T-008 で「32 制度」「見本 3 件」などを直した）ので、
ずれたら落ちるようにしておく。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from todoku import rules

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SPEC = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")


def _program_table() -> list[tuple[int, str, str]]:
    section = README.split("## 扱う制度", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| (\d+) \| (.+?) \| (.+?) \|$", section, flags=re.M)
    return [(int(n), name, level) for n, name, level in rows]


def test_readme_program_table_matches_rules():
    rs = rules.load()
    expected = [(p.number, p.name, rules.LEVELS[p.level])
                for p in sorted(rs.programs.values(), key=lambda p: p.number)]
    assert _program_table() == expected


def test_counts_in_readme_and_spec_match_rules():
    rs = rules.load()
    n = Counter(p.level for p in rs.programs.values())
    assert f"{len(rs.programs)} 制度のそれぞれに" in README
    for doc in (README, SPEC):
        assert f"国の制度 {n['national']} 件" in doc
        assert f"東京都の {n['tokyo']} 件" in doc
        assert f"町田市の {n['machida']} 件" in doc
        assert re.search(rf"横浜市の {n['yokohama']} 件", doc)
        assert re.search(rf"型」? ?{n['local']} 件", doc)


def test_sample_count_in_readme_and_spec():
    samples = json.loads((ROOT / "web" / "samples.json").read_text(encoding="utf-8"))
    for doc in (README, SPEC):
        found = re.findall(r"見本 (\d+) 件", doc)
        assert found and all(int(k) == len(samples) for k in found), found
