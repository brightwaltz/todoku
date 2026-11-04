"""合成住民（1 版目）：facts.yaml の事実の値の組だけで、65 歳以上の架空の住民を作る。LLM は使わない。

割合はすべて下の定数にまとめ、出どころを SOURCES に 1 行ずつ書く（README.md の表と同じ）。
出どころの区分：
  memory  統計の名前と値を覚えている範囲で置いたもの。統計の頁ではまだ照らしていない（T-077b で照らす）
  assumed 統計が見つかっていない仮定。結果の数を外に言うときは、この仮定に左右されると書く
どの割合も、照らし直したら定数だけを差し替える（作り方は変えない）。
"""

from __future__ import annotations

import random
from typing import Any

AGE_BANDS = {"age_65_69": 0.20, "age_70_74": 0.24, "age_75_plus": 0.56}
# 認定を受けている割合（年齢ごと）と、認定を受けた人の中の区分の割合
CERTIFIED_RATE = {"age_65_69": 0.03, "age_70_74": 0.06, "age_75_plus": 0.31}
CARE_LEVELS = {"support1": 0.14, "support2": 0.14, "care1": 0.21, "care2": 0.17,
               "care3": 0.13, "care4": 0.13, "care5": 0.08}
# 認定を受けていない人のうち、申請中・非該当だった人
NOT_CERTIFIED = {"applying": 0.02, "jiritsu": 0.01}
HOUSEHOLD = {"alone": 0.22, "elderly_only": 0.40, "alone_daytime": 0.19, "with_family": 0.19}
TAX = {"household_exempt": 0.30, "self_exempt": 0.20, "self_taxed": 0.50}
WELFARE_RATE = 0.03
DEMENTIA_RATE = {"age_65_69": 0.02, "age_70_74": 0.04, "age_75_plus": 0.18}
# 暮らしている所（認定の重さごと）：(入院中, 施設に入所中)。残りは自宅
RESIDENCE = {"heavy": (0.05, 0.35), "middle": (0.03, 0.08), "light": (0.01, 0.01)}

SOURCES = {
    "age_band": ("memory", "総務省 人口推計（2023 年 10 月）の 65 歳以上・75 歳以上の人数の比"),
    "care_level": ("memory", "厚生労働省 介護保険事業状況報告の年齢ごとの認定率と、区分ごとの認定者数の比"),
    "household": ("assumed", "国民生活基礎調査の「65 歳以上の者のいる世帯」の単独・夫婦のみの比を人の割合に読み替え、残りを半分ずつ"),
    "tax_status": ("assumed", "介護保険料の所得段階の分布の目安（第 1〜3 段階・第 4〜5 段階・第 6 段階以上）"),
    "on_welfare": ("memory", "被保護者調査の 65 歳以上の被保護者の人数 ÷ 65 歳以上の人口"),
    "dementia_diagnosis": ("assumed", "認知症の有病率の推計（年齢ごと）を、診断を受けた人の割合とみなした"),
    "residence": ("assumed", "認定の重さで入院・入所の割合を置いた"),
    "そのほかの事実": ("assumed", "認定の区分・認知症・暮らしている所から決まる割合を置いた（population.py の draw）"),
}

HEAVY = {"care3", "care4", "care5"}
MIDDLE = {"care1", "care2"}
CERTIFIED = set(CARE_LEVELS)


def _pick(rng: random.Random, table: dict[str, float]) -> str:
    x = rng.random() * sum(table.values())
    for value, p in table.items():
        x -= p
        if x < 0:
            return value
    return next(reversed(table))


def _yes(rng: random.Random, p: float) -> bool:
    return rng.random() < p


def draw(rng: random.Random) -> dict[str, Any]:
    """1 人分の事実（住所の事実は入れない。住所は run_sim が市区町村コードで決める）。

    dev と dev-merge（本番の規則）で facts.yaml の事実が少し違うので、両方の事実を引く。規則に無い事実は
    判定に使われず、入口の出来事を選ぶとき（run_sim.THEME_FITS）にだけ使う。
    認定の区分の certified_unknown（区分が分からない、という答え）は引かない。住民は自分の区分を知っているとみなす
    """
    f: dict[str, Any] = {}
    age = f["age_band"] = _pick(rng, AGE_BANDS)
    if _yes(rng, CERTIFIED_RATE[age]):
        care = _pick(rng, CARE_LEVELS)
    else:
        care = _pick(rng, {**NOT_CERTIFIED, "none": 1 - sum(NOT_CERTIFIED.values())})
    f["care_level"] = care
    certified = care in CERTIFIED
    weight = "heavy" if care in HEAVY else "middle" if care in MIDDLE else "light"
    hospital, facility = RESIDENCE[weight]
    f["residence"] = _pick(rng, {"hospital": hospital, "facility": facility, "home": 1 - hospital - facility})
    home = f["residence"] == "home"
    f["household"] = _pick(rng, HOUSEHOLD)
    f["on_welfare"] = _yes(rng, WELFARE_RATE)
    f["tax_status"] = "household_exempt" if f["on_welfare"] else _pick(rng, TAX)
    # 認定を受けていない人は、介護保険のサービスも施設も使えない（facts.yaml の false_when と同じ）
    f["uses_care_services"] = certified and (f["residence"] == "facility" or _yes(rng, 0.8 if home else 0.3))
    f["shortstay_or_facility"] = certified and (f["residence"] == "facility"
                                                or _yes(rng, 0.3 if care in HEAVY else 0.1))
    f["high_medical_costs"] = _yes(rng, 0.8 if f["residence"] == "hospital" else 0.1)
    f["caregiver_employee"] = _yes(rng, 0.45)
    f["caregiver_insured"] = f["caregiver_employee"] and _yes(rng, 0.85)  # dev-merge の事実。雇われていなければ False
    f["needs_daily_help"] = _yes(rng, 0.9 if certified else 0.1)  # dev-merge の事実
    f["constant_care_2w"] = _yes(rng, 0.8 if care in HEAVY else 0.4 if care in MIDDLE else 0.1 if certified else 0.02)
    f["mental_outpatient"] = _yes(rng, 0.03)  # dev-merge の事実
    dementia = f["dementia_diagnosis"] = _yes(rng, 0.5 if care in HEAVY else DEMENTIA_RATE[age])
    f["money_management_difficulty"] = _yes(rng, 0.5 if dementia else 0.03)
    f["wandering_risk"] = _yes(rng, 0.25 if dementia else 0.01)
    f["fire_risk"] = home and _yes(rng, 0.3 if dementia else 0.02)
    f["chronic_attack_risk"] = _yes(rng, 0.10)
    f["incontinence_bedding"] = _yes(rng, 0.2 if care in {"care4", "care5"} else 0.01)
    f["uses_diapers"] = _yes(rng, 0.6 if care in HEAVY else 0.25 if care in MIDDLE else 0.08 if certified else 0.02)
    f["bedridden_6m"] = _yes(rng, {"care5": 0.7, "care4": 0.4, "care3": 0.1}.get(care, 0.0))
    f["severe_constant_care"] = _yes(rng, {"care5": 0.8, "care4": 0.5, "care3": 0.15}.get(care, 0.0))
    f["hearing_difficulty"] = _yes(rng, 0.2 if age == "age_75_plus" else 0.08)
    f["hearing_certifiable"] = f["hearing_difficulty"] and _yes(rng, 0.15)
    f["plans_home_modification"] = home and _yes(rng, 0.15 if certified else 0.03)
    f["plans_fixture_replacement"] = home and _yes(rng, 0.05)
    f["plans_equipment_purchase"] = home and _yes(rng, 0.15 if certified else 0.02)
    return f


def generate(n: int, seed: int = 1) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [draw(rng) for _ in range(n)]
