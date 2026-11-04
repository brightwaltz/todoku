"""合成住民（1 版目）：facts.yaml の事実の値の組だけで、65 歳以上の架空の住民を作る。LLM は使わない。

割合はすべて下の定数にまとめ、出どころを SOURCES に 1 行ずつ書く（README.md の表と同じ）。
出どころの区分：
  stat    統計の公式の頁（表・CSV）の数から置いたもの。頁の URL と表の名前を SOURCES に書く（T-077b、2026-11-05）
  assumed 統計が見つかっていない仮定。結果の数を外に言うときは、この仮定に左右されると書く
どの割合も、照らし直したら定数だけを差し替える（作り方は変えない）。
"""

from __future__ import annotations

import random
from typing import Any

WP = "https://www8.cao.go.jp/kourei/whitepaper/w-2025/html/zenbun/"  # 内閣府 令和 7 年版 高齢社会白書

# 人口推計 参考表 1（2024 年 10 月 1 日、総人口）：65〜69 歳 7,268・70〜74 歳 8,198・75 歳以上 20,777 千人
AGE_BANDS = {"age_65_69": 0.20, "age_70_74": 0.23, "age_75_plus": 0.57}
# 認定を受けている割合（年齢ごと）。白書の表 1-2-2-7（2022 年度）：65〜74 歳 4.4%（要支援 1.4・要介護 3.0）、
# 75〜84 歳 17.6%・85 歳以上 58.5% の人数と割合から 75 歳以上は 31%。65〜74 歳の中の比（70〜74 歳が 2 倍）は仮定
CERTIFIED_RATE = {"age_65_69": 0.029, "age_70_74": 0.058, "age_75_plus": 0.31}
# 認定を受けた人の中の区分。白書の図 1-2-2-6 の CSV（2022 年度、千人）の数そのもの（_pick は合計で割る）
CARE_LEVELS = {"support1": 973, "support2": 940, "care1": 1425, "care2": 1134,
               "care3": 902, "care4": 870, "care5": 571}
# 認定を受けていない人のうち、申請中・非該当だった人
NOT_CERTIFIED = {"applying": 0.02, "jiritsu": 0.01}
# ひとり暮らしは白書の図 1-1-8（2023 年）の単独世帯 8,553 千 ÷ 65 歳以上 36,243 千。夫婦のみの世帯 8,635 千は
# 1 世帯の 65 歳以上が 1 人なら 24%、2 人なら 48% なので、その間に置いた（仮定）。残りを半分ずつ（仮定）
HOUSEHOLD = {"alone": 0.24, "elderly_only": 0.40, "alone_daytime": 0.18, "with_family": 0.18}
TAX = {"household_exempt": 0.30, "self_exempt": 0.20, "self_taxed": 0.50}
# 白書の図 1-2-1-18（2023 年）の保護率 65〜69 歳 2.34%・70 歳以上 3.03% を AGE_BANDS で重み付けた値
WELFARE_RATE = 0.029
# 白書の図 1-2-2-5：65 歳以上の認知症の有病率 12.3%（2022 年の推計）。下の割合と、重い人（要介護 3〜5）の 0.5 で
# 全体がおよそ 12.3% になるよう年齢ごとの値を縮めた。年齢の比と 0.5 は仮定。有病率を診断を受けた人の割合とみなした
DEMENTIA_RATE = {"age_65_69": 0.017, "age_70_74": 0.034, "age_75_plus": 0.15}
# 暮らしている所（認定の重さごと）：(入院中, 施設に入所中)。残りは自宅
RESIDENCE = {"heavy": (0.05, 0.35), "middle": (0.03, 0.08), "light": (0.01, 0.01)}

SOURCES = {
    "age_band": ("stat", "総務省 人口推計 2024 年 10 月 1 日 参考表 1（5 歳階級別、総人口）"
                 " https://www.stat.go.jp/data/jinsui/2024np/zuhyou/05k2024-4.xlsx"),
    "care_level": ("stat", "高齢社会白書 表 1-2-2-7 第 1 号被保険者の要介護等認定の状況（介護保険事業状況報告 2022 年度から算出）"
                   f"と図 1-2-2-6 要介護度別認定者数の推移 {WP}s1_2_2.html 。65〜74 歳の中の比と、申請中・非該当は仮定"),
    "on_welfare": ("stat", f"高齢社会白書 図 1-2-1-18 被保護人員の推移（2023 年の保護率） {WP}s1_2_1.html"),
    "household": ("assumed", f"ひとり暮らしの 24% だけ高齢社会白書 図 1-1-8 65 歳以上の者のいる世帯数及び構成割合（2023 年） {WP}s1_1_3.html"
                  " と人口推計から。夫婦のみ・日中ひとり・日中も誰かいるの割合は仮定"),
    "tax_status": ("assumed", "介護保険料の所得段階の分布の目安（第 1〜3 段階・第 4〜5 段階・第 6 段階以上）。統計の頁は見つけていない"),
    "dementia_diagnosis": ("assumed", f"全体の 12.3% だけ高齢社会白書 図 1-2-2-5 認知症及び MCI の高齢者数と有病率の将来推計 {WP}s1_2_2.html 。"
                           "年齢の比・重い人の割合は仮定で、有病率を診断を受けた人の割合とみなした（多めに数える向き）"),
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
