"""申請メモ（T-074）：札ごとに、窓口へ持っていく記入の下書きを作る。

数字と条件は規則（申請先・持ち物）と相談の答え（事実の値）からだけ入れ、Claude は使わない。
官製の様式の代わりではない。様式の欄の名前と書き方は窓口で確かめる。
"""
from __future__ import annotations

from typing import Any

from .engine import Result, known
from .rules import RuleSet

# メモを出す状態。当てはまらない・受け取り済み・結果待ちの制度は、窓口へ持っていく用がない
MEMO_STATUSES = ("eligible", "unknown", "after_prereq")

NOTE = "官製の様式の代わりではなく、記入の下書きです。様式と欄の名前は窓口で確かめてください。"

# 相談文での呼び方（敬称つき。claude.py の subject）を、窓口で自分の家族を言うときの言い方に直す。
# 一覧にない呼び方は付けない（「ご本人」「あなた」も付けない）
SPOKEN = {
    "お母さま": "母", "お父さま": "父", "奥さま": "妻", "ご主人": "夫",
    "おばあさま": "祖母", "おじいさま": "祖父", "お祖母さま": "祖母", "お祖父さま": "祖父",
    "義母さま": "義母", "義父さま": "義父", "お姉さま": "姉", "お兄さま": "兄",
}

# 事実の名前で書けない欄の名前（住所は事実ではなく、画面で選んだ市区町村から入れる）
PLACE_NAME = "住所の市区町村"


def fill_lines(r: Result, facts: dict[str, Any], rules: RuleSet, place: dict | None) -> list[dict]:
    """様式の欄のうち、相談の答えで埋まりそうなもの。はい・いいえの事実は欄にならないので入れない。"""
    lines: list[dict] = []
    if place:
        lines.append({"name": PLACE_NAME, "value": f"{place['pref']}{place['name']}"})
    # 年齢の区分は、条件にない制度の様式でもたいてい生年月日を書くので、分かっていれば入れる
    wanted = ["age_band"] + [leaf.fact for leaf in r.leaves if leaf.fact]
    seen: set[str] = set()
    for fid in wanted:
        if fid in seen or fid not in rules.facts:
            continue
        seen.add(fid)
        fact = rules.facts[fid]
        value = facts.get(fid)
        if fact.type == "bool" or fact.place or not known(value):
            continue
        lines.append({"name": fact.name, "value": fact.label_of(value)})
    return lines


def say_line(r: Result, subject: str, rules: RuleSet, pending: frozenset[str] = frozenset()) -> str:
    """窓口で最初に言う一言。制度の名前・分からない点・前提の制度は規則から入れる。"""
    who = SPOKEN.get(subject.strip(), "")
    name = r.program.name
    # 「要介護・要支援認定の申請」のように名前が申請で終わるときは「の申請」を重ねない
    what = name if name.endswith("申請") else f"{name}の申請"
    text = f"{who + 'の' if who else ''}{what}について相談に来ました。"
    if r.status == "unknown":
        names = [rules.facts[f].name for f in r.missing_facts if f in rules.facts]
        if names:
            text += f"{'・'.join(names)}が分からないので、当てはまるかを確かめたいです。"
    if r.status == "after_prereq":
        # 前提の制度を申請済み（結果待ち）なら、もう一度申請するように言わない
        sent = [rules.programs[w].name for w in r.waiting_on if w in pending]
        todo = [rules.programs[w].name for w in r.waiting_on if w not in pending]
        if sent:
            text += f"{'・'.join(sent)}は申請して、結果を待っています。"
        if todo:
            text += f"先に{'・'.join(todo)}が要るなら、その手続きも教えてください。"
    return text


def memo_view(r: Result, facts: dict[str, Any], subject: str, rules: RuleSet, place: dict | None,
              apply: dict, bring: list[str], pending: frozenset[str] = frozenset()) -> dict | None:
    """申請先・持ち物は札と同じもの（町田市の外なら elsewhere に置き換えた後）を受け取る。"""
    if r.status not in MEMO_STATUSES:
        return None
    return {
        "title": f"申請メモ：{r.program.name}",
        "where": apply.get("where", ""),
        "phone": apply.get("phone", ""),
        "bring": list(bring),
        "fill": fill_lines(r, facts, rules, place),
        "say": say_line(r, subject, rules, pending),
        "note": NOTE,
    }
