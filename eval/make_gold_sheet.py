"""正解を付けるための空の表（CSV）と、制度の公式ページの一覧を作る。

- eval/gold_sheet.csv：事例×制度の空欄。Numbers や Excel で開き、回線なしで記入できる（UTF-8、BOM 付き）。
- eval/programs_for_labeling.md：制度の名前と公式ページの URL だけ。窓口の規則（条件の書き方）は載せない。

正解は窓口の判定結果を見ずに付ける。この表にも判定結果は入れない。
使い方：uv run python eval/make_gold_sheet.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from todoku import rules as rules_mod  # noqa: E402

HERE = Path(__file__).resolve().parent
MARKS = "○＝案内すべき（今すぐ、または前の手続きを済ませれば対象になりうる）／×＝案内すべきでない／？＝この説明だけでは決められない"


def main() -> None:
    rs = rules_mod.load()
    cases = yaml.safe_load((HERE / "cases" / "cases.yaml").read_text(encoding="utf-8"))
    programs = sorted(rs.programs.values(), key=lambda p: p.number)
    header = ["事例", "相談文", "人物の説明"] + [f"{p.number} {p.name}" for p in programs]
    out = HERE / "gold_sheet.csv"
    if out.exists():
        sys.exit(f"{out} はもうあります。記入済みの表を上書きしないため止めました")
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for case in cases:
            w.writerow([case["id"], case["text"].strip(), case["profile"].strip()] + [""] * len(programs))
    lines = ["# 正解付けに使う制度の一覧", "", f"記号：{MARKS}", "",
             "公式ページだけを見て付けてください（窓口の規則ファイルは見ない）。", ""]
    for p in programs:
        lines.append(f"## {p.number} {p.name}（{rules_mod.LEVELS[p.level]}）")
        for s in p.sources:
            lines.append(f"- {s.publisher}：{s.url}")
        lines.append("")
    (HERE / "programs_for_labeling.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{out}（{len(cases)} 件 × {len(programs)} 制度）と programs_for_labeling.md を作りました")


if __name__ == "__main__":
    main()
