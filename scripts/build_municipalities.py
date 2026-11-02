"""総務省「都道府県コード及び市区町村コード」の Excel から、画面で選ぶ市区町村の一覧（web/municipalities.json）を作る。

元の Excel はリポジトリに入れない（https://www.soumu.go.jp/denshijiti/code.html から取る）。
使うのは団体コードと名前だけ。使い方（openpyxl はこの台本のときだけ足す）：
  uv run --no-project --with openpyxl python -I scripts/build_municipalities.py <Excel のパス>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

OUT = Path(__file__).resolve().parents[1] / "web" / "municipalities.json"
SHEET = "R6.1.1現在の団体"
SOURCE = "総務省「都道府県コード及び市区町村コード」（令和6年1月1日更新）"


def main() -> None:
    wb = load_workbook(sys.argv[1], read_only=True, data_only=True)
    rows = list(wb[SHEET].iter_rows(values_only=True))
    prefs: dict[str, dict] = {}
    for code, pref, city, *_ in rows[1:]:
        code = str(code).zfill(6)
        if not re.fullmatch(r"\d{6}", code) or not pref:
            raise SystemExit(f"想定と違う行：{code} {pref} {city}")
        p = prefs.setdefault(code[:2], {"code": code[:2], "name": pref, "cities": []})
        if city:
            p["cities"].append([code, city])
    out = {"source": SOURCE, "prefectures": list(prefs.values())}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"都道府県 {len(prefs)}・市区町村 {sum(len(p['cities']) for p in prefs.values())} → {OUT}")


if __name__ == "__main__":
    main()
