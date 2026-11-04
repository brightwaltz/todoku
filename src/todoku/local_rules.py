"""市ごとの事業（rules/local/<団体コード>.yaml）：市区町村の型を、その市の事業に置き換えるためのデータ（T-075）。

型（rules/programs/local-*.yaml）は「多くの市区町村がしている事業」の目安で、その市にあるかは窓口で確かめる。
このファイルには、その市の公式ページから書いた事業の名前・申請先・主な条件の言い換え・出典と、
誰がどう読んで照らし直したか（調べ方）を置く。判定の条件（eligibility）は型のものをそのまま使い、ここでは変えない。

見つからなかった型は status: not_found で残す（「その市に無い」とは言わない。型のまま出す）。
照らし直しで原文と合わなかったものは置かない（読み込みで止める）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

from .rules import RuleError, RuleSet, Source, _load_source

STATUSES = {"found": "その市の事業が見つかった", "not_found": "見つからなかった（型のまま出す）"}
# 照らし直し（別の読み手が出典の原文の行と見比べた結果）。合わなかったもの（mismatch）は置かない
RECHECKS = {"match": "原文の行と合っていた", "fixed": "照らし直しで直した"}
CONDITION_MAX = 60  # 札の 1 行に収める。出典の文は写さず、自分の言葉で短く書く
CONDITIONS_MAX = 5


@dataclass(frozen=True)
class Review:
    read_by: str  # 読んだもの（例：Managed Agent〔モデル名〕）
    recheck: str  # RECHECKS のどれか
    date: dt.date
    note: str = ""


@dataclass(frozen=True)
class CityProgram:
    template: str  # 置き換える型の id（local-*）
    status: str
    review: Review
    name: str = ""
    where: str = ""
    conditions: tuple[str, ...] = ()
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True)
class CityRules:
    code: str
    name: str
    site: str
    programs: dict[str, CityProgram]  # 型の id → その市の事業

    def found(self) -> dict[str, CityProgram]:
        """置き換えに使えるもの（その市の事業が見つかったものだけ）。"""
        return {t: p for t, p in self.programs.items() if p.status == "found"}


def load(directory: Path, rules: RuleSet, places: dict[str, tuple[str, str]]) -> dict[str, CityRules]:
    """directory（rules/local）の *.yaml を読む。無ければ空。places は団体コード → (都道府県名, 市区町村名)。"""
    if not directory.is_dir():
        return {}
    own = {p["city"] for p in rules.places.values() if "city" in p}
    out: dict[str, CityRules] = {}
    for path in sorted(directory.glob("*.yaml")):
        city = _load_city(path, rules, places, own)
        out[city.code] = city
    return out


def _load_city(path: Path, rules: RuleSet, places: dict[str, tuple[str, str]], own: set[str]) -> CityRules:
    where = f"local/{path.name}"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    code = str(raw.get("city") or "")
    if code != path.stem:
        raise RuleError(f"{where}: city（{code!r}）をファイル名の団体コードと同じにする（文字列で、先頭の 0 も書く）")
    if code not in places:
        raise RuleError(f"{where}: 団体コード {code} が市区町村の一覧（web/municipalities.json）にない")
    if code in own:
        raise RuleError(f"{where}: {places[code][1]}は市の制度を一つずつ判定しているので、型を置き換えない")
    name = raw.get("name") or ""
    if name != places[code][1]:
        raise RuleError(f"{where}: name（{name!r}）が一覧の名前（{places[code][1]}）と違う")
    site = str(raw.get("site") or "").strip()
    if not site or "/" in site:
        raise RuleError(f"{where}: site にその市の公式ページのホスト名（例 city.sapporo.jp）を書く")

    programs: dict[str, CityProgram] = {}
    for i, item in enumerate(raw.get("programs") or []):
        at = f"{where}.programs[{i}]"
        program = _load_program(item, rules, site, at)
        if program.template in programs:
            raise RuleError(f"{at}: 型 {program.template} が重複している")
        programs[program.template] = program
    if not programs:
        raise RuleError(f"{where}: programs が空")
    return CityRules(code=code, name=name, site=site, programs=programs)


def _load_program(raw: dict, rules: RuleSet, site: str, at: str) -> CityProgram:
    if not isinstance(raw, dict):
        raise RuleError(f"{at}: 辞書で書く")
    template = raw.get("template")
    tpl = rules.programs.get(template) if isinstance(template, str) else None
    if tpl is None or tpl.level != "local":
        raise RuleError(f"{at}: template（{template!r}）は市区町村の型（level: local）の id")
    status = raw.get("status")
    if status not in STATUSES:
        raise RuleError(f"{at}: status は {sorted(STATUSES)} のどれか")
    review = _load_review(raw.get("review"), at)
    sources = tuple(_load_source(s, at) for s in raw.get("sources") or [])

    if status == "not_found":
        if set(raw) & {"name", "where", "conditions"}:
            raise RuleError(f"{at}: 見つからなかった型には name・where・conditions を書かない")
        return CityProgram(template=template, status=status, review=review, sources=sources)

    for key in ("name", "where", "conditions", "sources"):
        if not raw.get(key):
            raise RuleError(f"{at}: その市の事業（found）に {key} がない")
    if not any(site in s.url for s in sources):
        raise RuleError(f"{at}: その市の公式ページ（{site}）の出典がない")
    conditions = tuple(str(c).strip() for c in raw["conditions"])
    if len(conditions) > CONDITIONS_MAX:
        raise RuleError(f"{at}: conditions は {CONDITIONS_MAX} 行まで（主な条件の言い換え）")
    for c in conditions:
        if not c or len(c) > CONDITION_MAX:
            raise RuleError(f"{at}: conditions の 1 行は 1〜{CONDITION_MAX} 字（自分の言葉で短く）: {c!r}")
    return CityProgram(template=template, status=status, review=review, name=str(raw["name"]).strip(),
                       where=str(raw["where"]).strip(), conditions=conditions, sources=sources)


def _load_review(raw: object, at: str) -> Review:
    if not isinstance(raw, dict):
        raise RuleError(f"{at}: review（調べ方：read_by・recheck・date）がない")
    for key in ("read_by", "recheck", "date"):
        if not raw.get(key):
            raise RuleError(f"{at}: review に {key} がない")
    if raw["recheck"] not in RECHECKS:
        raise RuleError(f"{at}: review の recheck は {sorted(RECHECKS)} のどれか（原文と合わなかったものは置かない）")
    note = str(raw.get("note") or "").strip()
    if raw["recheck"] == "fixed" and not note:
        raise RuleError(f"{at}: 照らし直しで直したとき（fixed）は、何を直したかを note に書く")
    date = raw["date"]
    if isinstance(date, str):
        date = dt.date.fromisoformat(date)
    return Review(read_by=str(raw["read_by"]).strip(), recheck=raw["recheck"], date=date, note=note)
