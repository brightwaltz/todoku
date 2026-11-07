"""市区町村の公式サイトから自動で集めた制度（rules/municipal/<団体コード>.yaml）。第 3 の層。

- 集め方：Claude（サブエージェント）が市区町村の公式サイトを読み、決まった形（型・条件の値）で書いた。
  機械が、出典が公式サイトの中にあること・頁を取り直して制度の名前があることを確かめた（scripts/municipal_check.py）。
  人は照らしていない。このため、国・都・市の制度（確かな層）とも、2 回読んで照らした市の事業（rules/local）とも分けて出す。
- 判定：条件は今の事実の値（年齢・要介護度・暮らし方・住民税・在宅・紙おむつなど）に置き換えて、規則として判定する。
  置き換えられない条件は at_application（申請のときに確かめられること）に置く。Claude に判定させない。
- 評価を動かさない：この層は、次に聞く問いの選び方にも、採点する制度の番号（1〜32）にも入らない。
  住所で市区町村が決まったときだけ、その市区町村の分を判定して付ける。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import engine
from .rules import Program, RuleError, RuleSet, Source

CATEGORIES = {
    "kaigo-yohin": "紙おむつ・介護用品",
    "haishoku": "配食",
    "haikai": "徘徊の SOS・GPS・見守りシール",
    "iro": "家族介護慰労金・介護手当",
    "koken": "成年後見の費用の助成",
    "hochoki": "補聴器の購入費の助成",
    "kinkyu-tsuho": "緊急通報装置",
    "jutaku": "住宅改造費の助成",
    "shingu": "寝具の乾燥・消毒",
    "ribiyo": "訪問理美容",
    "gaishutsu": "外出支援（タクシー券・移送・バスのパス）",
    "mimamori": "見守り・安否確認",
    "nichijo-yogu": "日常生活用具（火災警報器など）",
    "chusha": "思いやり駐車場の利用証",
    "yutai": "シニアの優待・割引",
    "sonota": "そのほか",
}
# rules/local（2 回読んで照らした市の事業）の型と重なる型。rules/local にその市のファイルがあれば、そちらを使う
LOCAL_TEMPLATE = {"kaigo-yohin": "local-kaigo-yohin", "haishoku": "local-haishoku", "haikai": "local-haikai",
                  "iro": "local-iro", "koken": "local-koken"}
CARE = ("support1", "support2", "care1", "care2", "care3", "care4", "care5")
HOUSEHOLD = ("alone", "elderly_only", "alone_daytime", "with_family")
TAX = ("household_exempt", "self_exempt", "self_taxed")
FLAG_LABELS = {
    "uses_diapers": "紙おむつを使っている",
    "hearing_difficulty": "聞こえにくい",
    "dementia_diagnosis": "認知症の診断を受けている",
    "wandering_risk": "ひとりで出かけて道に迷うおそれがある",
    "bedridden_6m": "ねたきりの状態",
    "fire_risk": "火の不始末のおそれがある",
    "chronic_attack_risk": "急に倒れるおそれのある病気がある",
    "on_welfare": "生活保護を受けている",
}
AGE_BANDS = (("age_40_64", 40, 64), ("age_65_69", 65, 69), ("age_70_74", 70, 74), ("age_75_plus", 75, 200))
CARE_LABEL = {"support1": "要支援1", "support2": "要支援2", "care1": "要介護1", "care2": "要介護2",
              "care3": "要介護3", "care4": "要介護4", "care5": "要介護5"}
HOUSEHOLD_LABEL = {"alone": "ひとり暮らし", "elderly_only": "高齢者だけの世帯", "alone_daytime": "日中ひとり",
                   "with_family": "家族と同居"}
TAX_LABEL = {"household_exempt": "世帯の全員が住民税非課税", "self_exempt": "本人が住民税非課税（世帯に課税の人がいる）",
             "self_taxed": "本人が住民税課税"}
MARK = "Claude が公式サイトから集め、機械で確かめたもの（人は照らしていない。{date}）"


@dataclass(frozen=True)
class CityPrograms:
    code: str
    name: str
    site: str
    site_level: str  # lg.jp（ホスト名で公式と分かる）か title（トップの頁に市区町村の名前）
    date: dt.date
    read_by: str
    programs: tuple[Program, ...]
    not_found: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)  # program.id → {category, confidence, name_on_page}


def age_node(age_min: int) -> dict:
    """年齢の下限を、年齢の区分に置き換える。区分の途中に下限があるときは、その区分を unsure（分からない）にする。"""
    inside = [b for b, lo, hi in AGE_BANDS if lo >= age_min]
    unsure = [b for b, lo, hi in AGE_BANDS if lo < age_min <= hi]
    node = {"fact": "age_band", "in": inside, "label": f"{age_min}歳以上"}
    if unsure:
        node["unsure"] = unsure
    return node


def compile_who(who: Mapping[str, Any]) -> dict:
    """調べた条件（値）を、規則の条件の木にする。"""
    nodes: list[dict] = []
    if who.get("age_min") is not None:
        nodes.append(age_node(int(who["age_min"])))
    if who.get("care_levels"):
        levels = [c for c in CARE if c in who["care_levels"]]
        label = "・".join(CARE_LABEL[c] for c in levels)
        nodes.append({"fact": "care_level", "in": levels, "unsure": ["certified_unknown"], "via": "kaigo-nintei",
                      "label": f"認定が {label}"})
    if who.get("household"):
        nodes.append({"fact": "household", "in": list(who["household"]),
                      "label": "・".join(HOUSEHOLD_LABEL[h] for h in who["household"])})
    if who.get("tax"):
        nodes.append({"fact": "tax_status", "in": list(who["tax"]), "label": "か".join(TAX_LABEL[t] for t in who["tax"])})
    if who.get("at_home"):
        nodes.append({"fact": "residence", "in": ["home"], "label": "自宅で暮らしている"})
    for f in who.get("flags") or ():
        nodes.append({"fact": f, "is": True, "label": FLAG_LABELS[f]})
    # 人が照らしていない層なので、値にした条件がそろっても「当てはまる見込み」とは言わない。
    # 値にできなかった条件（「A または B」・「おおむね」など）は at_application にあり、申請先で確かめる。
    # このため、この層の状態は「確かめると使えるかもしれない」か、値にした条件で外れた「当てはまらない」だけになる
    nodes.append({"ask_at": "申請先", "label": "ほかの条件は申請先で確かめる（人が照らしていない記録のため）"})
    return {"all": nodes}


def _program(code: str, i: int, raw: dict, date: dt.date, where: str) -> Program:
    for k in ("category", "name", "name_on_page", "summary", "where", "url"):
        if not isinstance(raw.get(k), str) or not raw[k].strip():
            raise RuleError(f"{where}: programs[{i}] に {k} が無い")
    if raw["category"] not in CATEGORIES:
        raise RuleError(f"{where}: programs[{i}] の型 {raw['category']} を知らない")
    who = raw.get("who") or {}
    for k, allowed in (("care_levels", CARE), ("household", HOUSEHOLD), ("tax", TAX), ("flags", tuple(FLAG_LABELS))):
        if who.get(k) is not None and any(x not in allowed for x in who[k]):
            raise RuleError(f"{where}: programs[{i}] の who.{k} に決まった値でないもの")
    src = Source(url=raw["url"], publisher=where.split(":")[0], license="利用条件を確かめていない（引用しない）",
                 checked=date, title=raw["name_on_page"])
    return Program(
        id=f"muni-{code}-{i + 1:02d}", number=0, name=raw["name"], level="municipal", kind="review",
        summary=raw["summary"], weight=0, eligibility=compile_who(who), sources=[src],
        at_application=list(raw.get("at_application") or []),
        apply={"where": raw["where"]}, amount=[raw["amount"]] if raw.get("amount") else [],
    )


def load_city(path: Path, names: Mapping[str, str]) -> CityPrograms:
    doc = yaml.safe_load(path.read_text())
    code = str(doc.get("city") or doc.get("pref") or "")
    if code != path.stem or code not in names:
        raise RuleError(f"{path.name}: city が団体コードの一覧に無いか、ファイル名と違う")
    if doc.get("name") != names[code]:
        raise RuleError(f"{path.name}: name が一覧の名前（{names[code]}）と違う")
    review = doc.get("review") or {}
    date = review.get("date")
    date = date if isinstance(date, dt.date) else dt.date.fromisoformat(str(date))
    where = f"{doc['name']}: {path.name}"
    progs, extra = [], {}
    for i, raw in enumerate(doc.get("programs") or []):
        p = _program(code, i, raw, date, where)
        progs.append(p)
        extra[p.id] = {"category": raw["category"], "confidence": raw.get("confidence", "low"),
                       "name_on_page": raw["name_on_page"], "url": raw["url"]}
    return CityPrograms(code=code, name=doc["name"], site=doc.get("site", ""), site_level=doc.get("site_level", ""),
                        date=date, read_by=review.get("read_by", ""), programs=tuple(progs),
                        not_found=tuple(doc.get("not_found") or ()), extra=extra)


def load(directory: Path, names: Mapping[str, str]) -> dict[str, CityPrograms]:
    if not directory.exists():
        return {}
    return {p.stem: load_city(p, names) for p in sorted(directory.glob("*.yaml"))}


def evaluate_city(rules: RuleSet, city: CityPrograms, facts: Mapping[str, Any],
                  statuses: Mapping[str, str], skip_categories: set[str] = frozenset()) -> list[engine.Result]:
    """その市区町村の制度を判定する。statuses は確かな層の判定（前提の手続き kaigo-nintei を引く）。

    skip_categories の型（rules/local で 2 回読んで照らした型）は出さない。
    """
    implied = engine.with_implied(rules, facts)
    out = []
    for p in city.programs:
        if city.extra[p.id]["category"] in skip_categories:
            continue
        out.append(engine._result(p, implied, statuses))
    return out
