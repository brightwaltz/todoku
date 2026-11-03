"""README と docs/spec.md に書いた数が、規則と見本の今の数と合っているか。

制度を足すたびに文書の数がずれた（11/4 T-008 で「32 制度」「見本 3 件」などを直した）ので、
ずれたら落ちるようにしておく。
"""

from __future__ import annotations

import json
import re
import tomllib
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


def test_spec_fly_stop_mode_matches_fly_toml():
    """spec の「公開」の止め方が fly.toml の auto_stop_machines と合っているか（11/4 T-021）。

    fly.toml は "suspend"（一時停止）なのに、spec は「自動停止」と書いていた。
    """
    conf = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))
    mode = conf["http_service"]["auto_stop_machines"]
    words = {"suspend": "一時停止", "stop": "停止"}
    section = SPEC.split("## 公開", 1)[1].split("\n## ", 1)[0]
    assert f'`auto_stop_machines = "{mode}"`' in section
    assert words[mode] in section
    if mode == "suspend":
        assert "自動停止" not in section


def test_docs_and_page_do_not_say_rules_copy_official_pages():
    """規則が公式ページを「書き写した」と言わないか（11/4 T-026）。

    README の「出典と利用条件」は、町田市・横浜市・東京都福祉局のページの文章は写しておらず、
    条件は自分の言葉で書いた、としている。役割の表と画面の下の注が「書き写した」と言うと食い違う。
    """
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for name, doc in (("README.md", README), ("docs/spec.md", SPEC), ("web/index.html", page)):
        assert "書き写" not in doc, name
    row = next(line for line in README.splitlines() if line.startswith("| 申請先・持ち物・金額・出典 |"))
    assert "自分の言葉で書き" in row and "確認日" in row and "出典" in row.split("|")[2]
