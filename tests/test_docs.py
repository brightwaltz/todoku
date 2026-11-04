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
    # 11/5 T-083a：結果の「押した数」の図の注（app.js）と /city/ の頁の注（city.py）も「書き写した」と言っていた
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    city = (ROOT / "src" / "todoku" / "city.py").read_text(encoding="utf-8")
    for name, doc in (("README.md", README), ("docs/spec.md", SPEC), ("web/index.html", page),
                      ("web/app.js", script), ("src/todoku/city.py", city)):
        assert "書き写" not in doc, name
    row = next(line for line in README.splitlines() if line.startswith("| 申請先・持ち物・金額・出典 |"))
    assert "自分の言葉で書き" in row and "確認日" in row and "出典" in row.split("|")[2]


def test_pyproject_has_no_uv_init_leftovers():
    """pyproject.toml に uv init の雛形が残っていないか（11/4 T-058）。

    説明が "Add your description here" のままで、`uv run todoku` は
    "Hello from todoku!" を出すだけだった。説明は README の書き出しと同じ文にする。
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["description"] != "Add your description here"
    lead = README.split("\n\n", 2)[1].replace("**", "")
    assert project["description"] in lead
    for target in project.get("scripts", {}).values():
        module, _, func = target.partition(":")
        assert callable(getattr(__import__(module, fromlist=[func]), func))


def test_readme_lists_claude_api_with_the_other_claude_uses():
    """「Claude の使い方」の Claude API の行が、Claude Code・MCP と同じ箇条書きにあるか（11/4 T-060）。

    11/3 に「Claude Code の設定（`.claude/`）」の小節を MCP と Claude API の行の間に挿し込んだため、
    Claude API の行が小節の表の下に取り残され、Claude Code の設定の一部に見えていた。
    """
    section = README.split("\n## Claude の使い方\n", 1)[1].split("\n## ", 1)[0]
    head = section.split("\n### ", 1)[0]
    assert "- **Claude Code**" in head
    assert "- **MCP**" in head
    assert "- **Claude API" in head


def test_readme_after_hackathon_names_the_operating_partners():
    """「ハッカソンの後」に、行政と組んで専門のスタッフが確かめる実運用の道があるか（11/4 T-054、D-0014 の答え）。

    説明の文（T-083）でも同じ言い方にそろえるので、言い回しを変えたらここも直す。
    """
    section = README.split("\n## ハッカソンの後\n", 1)[1].split("\n## ", 1)[0]
    items = [line for line in section.splitlines() if line.startswith("- ")]
    assert 3 <= len(items) <= 5
    assert "行政（市の窓口・地域包括支援センター）と組み" in section
    assert "専門のスタッフが規則と判定を確かめる" in section


def test_readme_two_layers_and_effort_figure():
    """「2 つの層」の数が規則と合い、「手間と、分かること」の図がリポジトリにあるか（11/5 T-083a）。

    型は一般の条件では判定するので、「判定しない」とは書かず「お住まいの市区町村がしているか」は判定しない、と書く。
    """
    rs = rules.load()
    n = Counter(p.level for p in rs.programs.values())
    layers = README.split("\n## 2 つの層", 1)[1].split("\n## ", 1)[0]
    assert f"国・都・市の制度 {len(rs.programs) - n['local']} 件" in layers
    assert f"市区町村の事業の型 {n['local']} 件" in layers
    assert "判定していません" in layers and "判定しない" not in layers
    effort = README.split("\n## 手間と、分かること\n", 1)[1].split("\n## ", 1)[0]
    images = re.findall(r"!\[[^\]]+\]\(([^)]+)\)", effort)
    assert images
    for img in images:
        assert (ROOT / img).is_file(), img
        assert (ROOT / img).stat().st_size <= 300_000, img


def _walk_taiin_haha_first_options():
    """見本「退院した母」を、どの問いにも先頭の選択肢で答えて進め、0 回と最後の数と押した数を返す。"""
    from fastapi.testclient import TestClient

    from todoku.app import app

    client = TestClient(app)
    d = client.post("/api/sample", json={"id": "taiin-haha"}).json()
    first = dict(d["counts"])
    s = {"facts": dict(d["facts"]), "suggestions": dict(d["suggestions"]), "subject": d["subject"],
         "asked": 0, "municipality": d["place"]["code"] if d.get("place") else ""}
    while d["question"]:
        q = d["question"]
        s["facts"][q["fact"]] = q["options"][0]["value"]
        s["suggestions"].pop(q["fact"], None)
        s["asked"] += 1
        d = client.post("/api/step", json=s).json()
    return first, dict(d["counts"]), s["asked"]


def test_readme_effort_numbers_match_the_sample_walk():
    """「手間と、分かること」に書いた数が、見本「退院した母」を先頭の選択肢で通した今の数と合うか（11/5 T-083c）。

    規則を直すと見本の道が変わり、README の数だけが古いまま残った（dev-merge で 3・23 → 6・5・15 のまま）。
    """
    effort = README.split("\n## 手間と、分かること\n", 1)[1].split("\n## ", 1)[0]
    m = re.search(r"0 回では(.+?)、(\d+) 回押した後は(.+?)でした", effort)
    assert m, "0 回と、押した後の数の文が見つからない"
    first, last, asked = _walk_taiin_haha_first_options()
    assert int(m.group(2)) == asked
    keys = {"当てはまる": "eligible", "先に手続き": "after_prereq", "確かめる": "unknown", "当てはまらない": "ineligible"}
    for text, counts in ((m.group(1), first), (m.group(3), last)):
        written = dict(re.findall(r"(当てはまる|先に手続き|確かめる|当てはまらない) (\d+) 件", text))
        assert "当てはまる" in written and "確かめる" in written, text
        for label, n in written.items():
            assert int(n) == counts.get(keys[label], 0), (label, n, counts)


def test_readme_claude_uses_say_agent_sdk_is_not_used():
    """使った Claude の製品の欄に Managed Agents があり、使っていない Agent SDK を使っていないと書くか（11/5 T-083a）。"""
    section = README.split("\n## Claude の使い方\n", 1)[1].split("\n## ", 1)[0]
    head = section.split("\n### ", 1)[0]
    assert "- **Claude Managed Agents**" in head
    assert "Claude Agent SDK は使っていません" in head
