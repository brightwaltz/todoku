"""T-074：申請メモ。申請先・持ち物・相談の答えで埋まる欄・窓口で言う一言を、規則と事実からだけ作る。"""
from pathlib import Path

from todoku import app as app_mod
from todoku import memo as memo_mod

MACHIDA = "132098"
BASE = {"care_level": "care2", "residence": "home", "plans_equipment_purchase": True, "age_band": "age_75_plus"}
# 認定をまだ受けていない人。dev-merge の規則では要介護認定に「日々の暮らしで介助が要る」も要るので、その事実があれば答えておく
AFTER = {**BASE, "care_level": "none",
         **({"needs_daily_help": True} if "needs_daily_help" in app_mod.RULES.facts else {})}


def card(view: dict, pid: str) -> dict:
    return next(r for r in view["results"] if r["id"] == pid)


def step(facts: dict, subject: str = "お母さま", municipality: str = MACHIDA) -> dict:
    facts = dict(facts)
    if municipality:
        app_mod.apply_place(facts, {}, municipality)
    return app_mod.step_view(facts, {}, subject, asked=3, municipality=municipality)


def test_memo_for_eligible_card_uses_rule_and_answers():
    r = card(step(BASE), "fukushi-yogu-konyu")
    assert r["status"] == "eligible"
    m = r["memo"]
    # 申請先・電話・持ち物は札と同じ（規則のまま）
    assert m["where"] == r["apply"]["where"] and m["phone"] == r["apply"]["phone"] and m["bring"] == r["bring"]
    fill = {f["name"]: f["value"] for f in m["fill"]}
    assert fill[memo_mod.PLACE_NAME] == "東京都町田市"
    assert fill["年齢"] == "75歳以上"
    assert "要介護2" in fill.values()
    assert fill["いま暮らしている所"] == "自宅"
    # はい・いいえの事実（福祉用具を買う予定など）は欄にしない
    assert len(fill) == 4
    assert m["say"] == "母の福祉用具購入費の支給の申請について相談に来ました。"
    assert "官製の様式の代わりではなく、記入の下書き" in m["note"]


def test_unknown_card_says_what_is_not_known():
    facts = {k: v for k, v in BASE.items() if k != "residence"}
    r = card(step(facts, subject="あなた"), "fukushi-yogu-konyu")
    assert r["status"] == "unknown"
    assert r["memo"]["say"].startswith("福祉用具購入費の支給の申請について")  # 「あなた」は付けない
    assert "が分からないので、当てはまるかを確かめたいです。" in r["memo"]["say"]


def test_no_memo_for_ineligible_cards():
    view = step(BASE)
    for r in view["results"]:
        assert (r["memo"] is None) == (r["status"] not in memo_mod.MEMO_STATUSES)
    assert any(r["memo"] is None for r in view["results"])


def test_outside_machida_uses_elsewhere_and_no_place_line():
    r = card(step(BASE, municipality=""), "fukushi-yogu-konyu")
    assert "町田市" not in r["memo"]["where"] and r["memo"]["where"] == r["apply"]["where"]
    assert memo_mod.PLACE_NAME not in [f["name"] for f in r["memo"]["fill"]]


def test_after_prereq_names_the_prereq():
    r = card(step(AFTER), "fukushi-yogu-konyu")
    assert r["status"] == "after_prereq"
    assert r["memo"]["say"].endswith("先に要介護・要支援認定の申請が要るなら、その手続きも教えてください。")


def test_after_prereq_pending_does_not_ask_to_apply_again():
    r = card(step({**BASE, "care_level": "applying"}), "fukushi-yogu-konyu")
    assert r["status"] == "after_prereq"
    assert "は申請して、結果を待っています。" in r["memo"]["say"] and "先に" not in r["memo"]["say"]


def test_page_has_memo_and_print_one_sheet():
    js = (Path(app_mod.__file__).parents[2] / "web" / "app.js").read_text(encoding="utf-8")
    css = (Path(app_mod.__file__).parents[2] / "web" / "style.css").read_text(encoding="utf-8")
    assert "function memoView" in js and "このメモだけ印刷する" in js
    assert "html.print-memo body > :not(#print-sheet)" in css


def test_name_ending_with_shinsei_is_not_doubled():
    r = card(step(AFTER), "kaigo-nintei")
    assert r["memo"]["say"] == "母の要介護・要支援認定の申請について相談に来ました。"
