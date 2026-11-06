"""T-149：320・360 で、数と単位・見出しの最後の語・短い括弧書きが 1〜2 字だけ次の行に落ちる折れ残りを減らす
（Chrome・WebKit で測った。$OPS/tools/shot_t148.py）。

前は「確かめると使えるかもしれない25 / 件」「読み取れなかったことにも答える（15 / 件）」「申請のときに確かめられるこ / と」
「相談の答えから書けるこ / と」「相談の文章から読み取った / こと」「次の確定申告の時期（2〜3 / 月）」「介護保険の住宅改修費の支 / 給」と折れていた。
末尾は app.js の keepTail で .nowrap の 1 かたまりにする。ここではファイルの文を読むだけ（実際の折れ目はブラウザで測った）。
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
CSS = (WEB / "style.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def rule(selector):
    # 同じ選択子の規則が 2 つ以上あるとき（.todo-list a は T-029 の大きさと T-149 の折り方）は、全部をつないで見る
    found = re.findall(r"^" + re.escape(selector) + r" \{([^}]*)\}", CSS, re.M)
    assert found, selector
    return " ".join(found)


def test_counts_and_tails_do_not_wrap_inside():
    assert "white-space: nowrap" in rule(".nowrap, .group-title .n, .fold > summary h2 .n")


def test_tails_are_kept_together_in_app_js():
    assert 'const keepTail = (head, tail) => [head, el("span", { class: "nowrap" }, tail)];' in JS
    for head, tail in [("申請のときに", "確かめられること"), ("相談の答えから", "書けること"),
                       ("相談の文章から", "読み取ったこと"), ("選んだ出来事から", "分かったこと")]:
        assert f'keepTail("{head}", "{tail}")' in JS
    assert 'keepTail("読み取れなかったことにも答える", `（${unread.length} 件）`)' in JS
    assert 'keepTail("当てはまらない・済んでいる制度", `（${no.length} 件）`)' in JS
    # 文は前と同じ（見出しを 2 つに分けただけ）
    assert '"申請のときに確かめられること"' not in JS and '"相談の答えから書けること"' not in JS


def test_ahead_heading_keeps_short_parenthetical():
    # 「次の確定申告の時期（2〜3月）」の「（2〜3月）」を離さない。文の定数は変えていない
    assert 'const AHEAD_TAX = "次の確定申告の時期（2〜3月）";' in JS
    assert 'el("h3", {}, aheadWhen(s.when))' in JS
    m = re.search(r"const aheadWhen = .*?match\((/.+?/)\)", JS)
    assert m
    pat = m.group(1).strip("/").replace("(?:", "(")
    g = re.match(pat, "次の確定申告の時期（2〜3月）")
    assert g and g.group(1) == "次の確定申告の時期" and g.group(2) == "（2〜3月）"
    assert re.match(pat, "要介護・要支援認定の申請の結果が出たら") is None


def test_static_reading_heading_matches_script():
    # 最初に描く見出しと、app.js が描き直す見出しが同じ形（どちらも末尾を .nowrap に）
    assert '<h2>相談の文章から<span class="nowrap">読み取ったこと</span></h2>' in HTML


def test_todo_names_break_at_phrases():
    body = rule(".todo-list a")
    assert "word-break: auto-phrase" in body and "text-wrap: balance" in body
