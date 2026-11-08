"""README の「ライセンス」に、紹介の動画（T-140）の利用条件が残っているか（T-196・T-198）。

RSI のレーンが同じ節の「町田市のページの文章は…」の行を削った（RSI-011）。紹介の動画の行がその真下にあると、
合流のときに README が衝突し、取り違えると字の利用条件（SIL Open Font License）の行が消える。
T-196 は動画の行を MIT の行の真下に移して衝突を避けたが、main 08c0479 が先に合流し、
「MIT → 外部の出典 → 紹介の動画」の 3 行（町田市の行なし）で解いた。T-198 でこの節を main と同じ形にした
（並びが違うと main 06a1044 と dev-merge の合流で同じ節が衝突する）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8") + "\n" + (ROOT / "DETAILS.md").read_text(encoding="utf-8")  # 要約版と詳細版を合わせて読む


def _license_items() -> list[str]:
    section = README.split("\n## ライセンス\n", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("- ")]


def test_intro_video_terms_stay_in_the_license_section():
    items = _license_items()
    intro = [i for i in items if "`web/intro.mp4`" in i]
    assert len(intro) == 1, items
    assert "SIL Open Font License" in intro[0]


def test_license_section_has_the_same_shape_as_main():
    items = _license_items()
    assert "MIT ライセンス" in items[0], items
    assert "外部の出典" in items[1], items
    assert "`web/intro.mp4`" in items[2], items
    assert not any("町田市のページの文章" in i for i in items), items
