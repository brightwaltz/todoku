"""入口の「暮らしの出来事」のボタン（T-071）。

押すと、出来事の文を下書きにして、その文から言える事実だけを置き、最初の問いを返す。Claude は呼ばない
（合言葉も要らない）。事実は相談文の読み取りと同じ検査（claude.check_extraction）を通すので、根拠の言葉が
文にない事実や、取り違えやすい値は確信の低い読み取り（確かめる問い）になる。

置く事実は、文がそのまま言っていることだけにする。「物忘れが増えた」から認知症の診断は置かない。
「働きながら」は、雇われているか（介護休業給付の条件）まで言っていないので確かめる側（low）に置く。
出来事は家の場所 7 つの下に小項目で並べる（T-139）。気分をあおる仕掛け（連続の記録・期限で急かす・ごほうび）は入れない（先生 11/4）。
"""

from __future__ import annotations

from typing import Any

# 大項目は家の場所 7 つ（T-139。11/6 柴田「出来事の札は向きがばらばら。家の場所 → 小項目の 2 段に」）。画面に出すのは場所の名前だけ
PLACES: list[tuple[str, str]] = [
    ("bedroom", "寝室"), ("entrance", "階段と玄関"), ("bath", "浴室とトイレ"), ("kitchen", "台所"),
    ("living", "居間"), ("outside", "外"), ("family", "家族"),
]

# 小項目。並びは PLACES の順。icf は裏の分類で画面には出さない（WHO の国際生活機能分類 ICF を下敷きにした。
# 日本語版は厚生労働省。退院のような出来事は ICF の外なので「出来事」とする）。
# facts は (fact, value, confidence, evidence)。evidence は text の一部であること（tests/test_events.py が確かめる）。
# 文がそのまま言っていない事実は置かないか low（確かめる問い）にする：「一人で〜が難しい」は介助が要るとまでは
# 言っていないので needs_daily_help は low、「聞こえにくい」は補聴器を考えているかまで言っていないので low
EVENTS: list[dict[str, Any]] = [
    {
        "id": "discharged", "label": "退院した", "place": "bedroom", "icf": "出来事",
        "text": "病院から退院して、家に戻りました。",
        "facts": [("residence", "home", "high", "家に戻りました")],
    },
    {
        "id": "bedridden", "label": "寝たきりに近い", "place": "bedroom", "icf": "移動",
        "text": "寝たきりに近い状態です。",
        "facts": [],  # 6 か月以上か（bedridden_6m）、ほとんどに介護が要るか（severe_constant_care）は言っていない
    },
    {
        "id": "diapers", "label": "おむつが要るようになった", "place": "bedroom", "icf": "排泄",
        "text": "紙おむつを使うようになりました。",
        "facts": [("uses_diapers", True, "high", "紙おむつを使う")],
    },
    {
        "id": "falls", "label": "転びやすくなった", "place": "entrance", "icf": "移動",
        "text": "家の中で転びやすくなりました。",
        "facts": [("residence", "home", "high", "家の中で")],
    },
    {
        "id": "steps", "label": "段差がつらい", "place": "entrance", "icf": "移動",
        "text": "家の段差を上り下りするのがつらくなりました。",
        "facts": [],  # 介助が要るとまでは言っていない
    },
    {
        "id": "bathing", "label": "お風呂が一人では難しい", "place": "bath", "icf": "セルフケア",
        "text": "お風呂に一人で入るのが難しくなりました。",
        "facts": [("needs_daily_help", True, "low", "一人で入るのが難しく")],
    },
    {
        "id": "toilet", "label": "トイレが間に合わない", "place": "bath", "icf": "排泄",
        "text": "トイレに間に合わないことが増えました。",
        "facts": [],  # 紙おむつを使うか・布団の失禁かは言っていない
    },
    {
        "id": "fire", "label": "火の消し忘れ", "place": "kitchen", "icf": "家庭生活",
        "text": "コンロの火を消し忘れることがありました。",
        "facts": [("fire_risk", True, "high", "火を消し忘れる")],
    },
    {
        "id": "meals", "label": "食事の用意が難しい", "place": "kitchen", "icf": "家庭生活",
        "text": "食事の用意が一人では難しくなりました。",
        "facts": [("needs_daily_help", True, "low", "一人では難しく")],
    },
    {
        "id": "forgetful", "label": "物忘れが増えた", "place": "living", "icf": "精神機能",
        "text": "物忘れが増えてきました。",
        "facts": [],
    },
    {
        "id": "money", "label": "お金の管理が難しい", "place": "living", "icf": "経済生活",
        "text": "お金の管理が難しくなりました。",
        "facts": [("money_management_difficulty", True, "high", "お金の管理が難しく")],
    },
    {
        "id": "hearing", "label": "聞こえにくい", "place": "living", "icf": "感覚機能",
        "text": "耳が聞こえにくくなりました。",
        "facts": [("hearing_difficulty", True, "low", "聞こえにくく")],
    },
    {
        "id": "lost", "label": "道に迷った", "place": "outside", "icf": "精神機能",
        "text": "外に出て、帰り道が分からなくなったことがあります。",
        "facts": [("wandering_risk", True, "high", "帰り道が分からなくなった")],
    },
    {
        "id": "outing", "label": "一人で出かけられない", "place": "outside", "icf": "移動",
        "text": "一人で出かけるのが難しくなりました。",
        "facts": [("needs_daily_help", True, "low", "一人で出かけるのが難しく")],
    },
    {
        "id": "alone", "label": "一人暮らしになった", "place": "family", "icf": "環境（支え）",
        "text": "一人暮らしになりました。",
        "facts": [("household", "alone", "high", "一人暮らし")],
    },
    {
        "id": "work", "label": "仕事と介護が重なった", "place": "family", "icf": "環境（支え）",
        "text": "私は働きながら介護をしています。",
        "facts": [("caregiver_employee", True, "low", "働きながら")],
    },
]
MAX_EVENTS = len(EVENTS)  # 選べる数の上限は小項目の数（T-139。柴田の承認済み）
PLACE_LABEL = dict(PLACES)


def find(event_id: str) -> dict[str, Any] | None:
    return next((e for e in EVENTS if e["id"] == event_id), None)


def combine(events: list[dict[str, Any]]) -> dict[str, Any]:
    """絵で選んだいくつかの出来事（T-124）を 1 つの出来事にする。文は選んだ順につなぎ、同じ事実は最初のものだけ残す。

    根拠の言葉はどれも元の文の一部なので、つないだ文の一部でもある（check_extraction の検査はそのまま通る）。
    """
    facts, seen = [], set()
    for e in events:
        for f in e["facts"]:
            if f[0] not in seen:
                seen.add(f[0])
                facts.append(f)
    return {"id": "+".join(e["id"] for e in events), "label": "・".join(e["label"] for e in events),
            "text": "".join(e["text"] for e in events), "facts": facts}


def reading(event: dict[str, Any]) -> dict[str, Any]:
    """check_extraction に渡す形（Claude の読み取りと同じ形）にする。"""
    return {
        "subject": "ご本人",
        "facts": [{"fact": fid, "value": value, "confidence": confidence, "evidence": evidence}
                  for fid, value, confidence, evidence in event["facts"]],
    }
