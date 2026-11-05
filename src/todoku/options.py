"""有料・民間の支え（行政の制度ではない候補。T-084）。

判定はしない。関係しそうな事実（世帯・認定の区分など）が当てはまる数で並べ替えるだけ。
会社の名前は出さず、種類だけを出す。出典は全国の公的な団体か国の資料だけで、費用の目安は出典にある数字だけ。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .rules import Fact, RuleError, Source, _load_source

TAG = "有料・民間・行政の制度ではない候補"


@dataclass(frozen=True)
class Hint:
    fact: str
    values: tuple[Any, ...]
    label: str

    def matches(self, facts: dict[str, Any]) -> bool:
        return self.fact in facts and facts[self.fact] in self.values


@dataclass(frozen=True)
class Option:
    id: str
    name: str
    summary: str
    where: str
    hints: tuple[Hint, ...]
    sources: tuple[Source, ...]
    # 出典にある数字だけ（自分の言葉で短く）。無ければ空で、画面は「申し込み先で確かめる」
    cost: str = ""
    cost_source: str = ""


def load(path: Path, facts: dict[str, Fact]) -> list[Option]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    options: list[Option] = []
    for i, item in enumerate(raw.get("options") or []):
        where = f"{path.name}[{i}]"
        for key in ("id", "name", "summary", "where", "sources"):
            if not item.get(key):
                raise RuleError(f"{where}: {key} がない")
        if any(o.id == item["id"] for o in options):
            raise RuleError(f"{where}: id {item['id']} が重複している")
        sources = tuple(_load_source(s, f"{where}.sources") for s in item["sources"])
        cost = (item.get("cost") or "").strip()
        cost_source = item.get("cost_source", "")
        if cost and cost_source not in {s.url for s in sources}:
            raise RuleError(f"{where}: 費用の目安は、sources のどれで言えるかを cost_source に書く")
        options.append(Option(
            id=item["id"], name=item["name"], summary=item["summary"], where=item["where"],
            hints=tuple(_load_hint(h, facts, where) for h in item.get("hints") or []),
            sources=sources, cost=cost, cost_source=cost_source if cost else "",
        ))
    return options


def _load_hint(raw: dict, facts: dict[str, Fact], where: str) -> Hint:
    fid = raw.get("fact")
    if fid not in facts:
        raise RuleError(f"{where}: 事実 {fid} がない")
    values = tuple(raw["in"]) if "in" in raw else (raw.get("is"),)
    allowed = facts[fid].values()
    for v in values:
        if v not in allowed:
            raise RuleError(f"{where}: 事実 {fid} に値 {v!r} がない")
    if not raw.get("label"):
        raise RuleError(f"{where}: 手がかり {fid} に label がない")
    return Hint(fact=fid, values=values, label=raw["label"])


def ranked(options: list[Option], facts: dict[str, Any]) -> list[dict]:
    """当てはまる手がかりが多い順（同じ数なら規則の順）。手がかりが当てはまらなくても外さない。"""
    views = []
    for o in options:
        why = [h.label for h in o.hints if h.matches(facts)]
        views.append({
            "id": o.id, "name": o.name, "tag": TAG, "summary": o.summary, "where": o.where,
            "cost": o.cost, "why": why,
            "sources": [{"url": s.url, "publisher": s.publisher, "title": s.title, "checked": s.checked.isoformat()}
                        for s in o.sources],
        })
    return sorted(views, key=lambda v: -len(v["why"]))
