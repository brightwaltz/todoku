"""T-152：字の間・行の間を広げても（WCAG 1.4.12）申請メモの値が札の右で切れない。370px 以下だけ表の列の幅を変える。

字の間を広げたときの切れ・はみ出しは ops の shot_t152.py でブラウザで測った（320・360・390・1280）。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def test_memo_table_columns_unchanged_on_wide_screens():
    m = re.search(r"^\.memo-sheet dl\.fill \{([^}]*)\}", CSS, re.M)
    assert m and "grid-template-columns: max-content 1fr" in m.group(1)  # 390 より広い画面は前のまま


def test_memo_table_value_column_can_shrink_on_narrow_screens():
    m = re.search(r"@media \(max-width: 370px\) \{\s*\.memo-sheet dl\.fill \{([^}]*)\}\s*\.memo-sheet dl\.fill dd \{([^}]*)\}\s*\}", CSS)
    assert m, "370px 以下の申請メモの表の規則"
    assert "grid-template-columns: fit-content(50%) minmax(0, 1fr)" in m.group(1)
    assert "overflow-wrap: anywhere" in m.group(2)
