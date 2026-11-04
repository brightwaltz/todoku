"""入口の「暮らしの出来事」のボタン（T-071）。

押すと、出来事の文を下書きにして、その文から言える事実だけを置き、最初の問いを返す。Claude は呼ばない
（合言葉も要らない）。事実は相談文の読み取りと同じ検査（claude.check_extraction）を通すので、根拠の言葉が
文にない事実や、取り違えやすい値は確信の低い読み取り（確かめる問い）になる。

置く事実は、文がそのまま言っていることだけにする。「物忘れが増えた」から認知症の診断は置かない。
「働きながら」は、雇われているか（介護休業給付の条件）まで言っていないので確かめる側（low）に置く。
出来事の数は 6 つまで。気分をあおる仕掛け（連続の記録・期限で急かす・ごほうび）は入れない（先生 11/4）。
"""

from __future__ import annotations

from typing import Any

# (fact, value, confidence, evidence)。evidence は text の一部であること（tests/test_events.py が確かめる）
EVENTS: list[dict[str, Any]] = [
    {
        "id": "discharged", "label": "退院した",
        "text": "病院から退院して、家に戻りました。",
        "facts": [("residence", "home", "high", "家に戻りました")],
    },
    {
        "id": "forgetful", "label": "物忘れが増えた",
        "text": "物忘れが増えてきました。",
        "facts": [],
    },
    {
        "id": "alone", "label": "一人暮らしになった",
        "text": "一人暮らしになりました。",
        "facts": [("household", "alone", "high", "一人暮らし")],
    },
    {
        "id": "falls", "label": "転びやすくなった",
        "text": "家の中で転びやすくなりました。",
        "facts": [("residence", "home", "high", "家の中で")],
    },
    {
        "id": "work", "label": "仕事と介護が重なった",
        "text": "私は働きながら介護をしています。",
        "facts": [("caregiver_employee", True, "low", "働きながら")],
    },
    {
        "id": "diapers", "label": "おむつが要るようになった",
        "text": "紙おむつを使うようになりました。",
        "facts": [("uses_diapers", True, "high", "紙おむつを使う")],
    },
]
MAX_EVENTS = 6


def find(event_id: str) -> dict[str, Any] | None:
    return next((e for e in EVENTS if e["id"] == event_id), None)


def reading(event: dict[str, Any]) -> dict[str, Any]:
    """check_extraction に渡す形（Claude の読み取りと同じ形）にする。"""
    return {
        "subject": "ご本人",
        "facts": [{"fact": fid, "value": value, "confidence": confidence, "evidence": evidence}
                  for fid, value, confidence, evidence in event["facts"]],
    }
