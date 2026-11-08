"""README が rules/local/（市ごとの事業）を説明していて、その説明が今のデータと画面の印に合っているか（T-166）。

rules/local/ は 36 市分あり、/city/ と結果の札で型の代わりにその市の事業の名前・申請先・出典を出しているのに、
README の「全国対応」と「2 つの層」の節には書かれていなかった。市の数はデータのレーンが増やすので、README には書かず、
名前を挙げた市にその市の事業が実際にあることだけを確かめる。
"""

from __future__ import annotations

import re
from pathlib import Path

from todoku import local_rules
from todoku.app import LOCAL, PLACES

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8") + "\n" + (ROOT / "DETAILS.md").read_text(encoding="utf-8")  # 要約版と詳細版を合わせて読む


def _local_line() -> str:
    lines = [line for line in README.splitlines() if "`rules/local/`" in line]
    assert len(lines) == 1, "README に rules/local/ の説明が無い（か 2 か所ある）"
    return lines[0]


def test_readme_points_to_rules_local():
    assert "`rules/local/`" in README
    assert (ROOT / "rules" / "local" / "README.md").exists()


def test_cities_named_in_readme_have_their_own_programs():
    head = _local_line().split("など一部の市では", 1)[0].rsplit("。", 1)[-1]
    names = head.split("・")
    assert names and all(re.fullmatch(r"\S+市", n) for n in names), f"README の例の市の名前が読めない：{head}"
    by_name = {PLACES[code][1]: code for code in LOCAL}
    for name in names:
        assert name in by_name, f"{name} は rules/local/ に無い"
        assert LOCAL[by_name[name]].found(), f"{name} にその市の事業が 1 件も無い"


def test_readme_matches_the_mark_on_screen():
    # 画面の印は「Claude が公式頁から書き、照らし直したもの（日付）」。README は同じことを言う
    assert local_rules.MARK.startswith("Claude が公式頁から書き、照らし直したもの")
    text = _local_line()
    assert "Claude が公式ページから書き" in text and "照らし直した" in text and "日付" in text


def test_readme_says_conditions_stay_those_of_the_template():
    layer = README.split("## 2 つの層", 1)[1].split("\n## ", 1)[0]
    assert "判定の条件は型のまま" in layer
