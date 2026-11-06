"""T-148：見出し・選択肢で、最後の行が 1〜2 字だけになる折れ残りを減らす（390 の Chrome・WebKit で測った。$OPS/tools/shot_t148.py）。

前は 390 で「制度の地図（どの手続きが先か、図で見 / る）」「申請メモを開く（窓口に持っていく下 / 書き）」
「受けていな / い」「申請したが「非該当（自立）」だっ / た」「行方不明のおそれがある人の探索・見守 / り」と折れていた。
ここでは CSS の文を読むだけ（実際の折れ目はブラウザで測った）。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def rule(selector):
    m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", CSS, re.M)
    assert m, selector
    return m.group(1)


def test_titles_and_options_break_between_words_and_balance():
    body = rule("#pmap-fold > summary h2, details.memo > summary, .option")
    assert "word-break: keep-all" in body
    assert "text-wrap: balance" in body
    # 収まらない長い語は枠の外へはみ出さず、枠の中で折れる
    assert "overflow-wrap: anywhere" in body


def test_two_column_options_fit_six_characters_at_390():
    # 390 で 2 列の札の中は 105px で、19px の 6 字（114px）が入らなかった。左右 14px で 117px
    assert "padding-inline: 14px" in rule("#ask-options.two .option")
    # 2 列の規則は丸い札の規則（#ask-options .option）より後で、より強い
    assert CSS.index("#ask-options.two .option {") > CSS.index("#ask-options .option {")


def test_city_program_names_break_at_phrases():
    body = rule(".city-item h3")
    assert "word-break: auto-phrase" in body and "text-wrap: balance" in body
