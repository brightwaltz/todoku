"""README の「ライセンス」に、紹介の動画（T-140）の利用条件が残っているか（T-196）。

RSI のレーンが同じ節の「町田市のページの文章は…」の行を削った（RSI-011）。紹介の動画の行がその真下にあると、
合流のときに README が衝突し、取り違えると字の利用条件（SIL Open Font License）の行が消える。
動画の行を MIT の行の真下に置き、間に変わらない行を挟んで、衝突しないようにした。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _license_items() -> list[str]:
    section = README.split("\n## ライセンス\n", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("- ")]


def test_intro_video_terms_stay_in_the_license_section():
    items = _license_items()
    intro = [i for i in items if "`web/intro.mp4`" in i]
    assert len(intro) == 1, items
    assert "SIL Open Font License" in intro[0]


def test_intro_video_terms_sit_right_under_the_mit_line():
    items = _license_items()
    assert "MIT ライセンス" in items[0], items
    assert "`web/intro.mp4`" in items[1], items
