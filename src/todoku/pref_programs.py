"""都道府県の制度を調べた記録（rules/pref_programs.yaml。T-129）：/city/ の頁の都道府県の段に出す。

判定には使わない（規則にした都の制度〔東京都〕は rules/programs に level 付きで書く）。
ここに書くのは、県の公式ページを見て「個人で申し込める高齢者・介護の制度」が
見つかったか（確かめる候補）、見つからなかったか（見た頁）と、確かめた日だけ。頁の文は写さず、条件は言い換える。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .contacts import PREF_CODES
from .rules import RuleError, rules_dir


@dataclass(frozen=True)
class Candidate:
    name: str
    who: str  # 対象の主な条件の言い換え
    where: str  # 申請先
    url: str  # 出典


@dataclass(frozen=True)
class PrefCheck:
    checked: dt.date
    looked: tuple[str, ...]  # 見た頁の URL
    found: tuple[Candidate, ...]  # 空なら、調べて見つからなかった


def _url(value, where: str) -> str:
    url = str(value or "")
    if not url.startswith("https://") and not url.startswith("http://"):
        raise RuleError(f"{where}: URL は http(s) で始める（{url}）")
    return url


def load(path: Path) -> dict[str, PrefCheck]:
    """都道府県コード 2 桁 → 調べた記録。ファイルが無ければ空（規則の置き場を差し替えたテストなど）。"""
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, PrefCheck] = {}
    for code, item in (raw.get("prefectures") or {}).items():
        code = str(code)
        where = f"{path.name} の {code}"
        if code not in PREF_CODES:
            raise RuleError(f"{path.name}: 都道府県コード {code} は 01〜47 の 2 桁で書く")
        item = item or {}
        checked = item.get("checked")
        if isinstance(checked, str):
            checked = dt.date.fromisoformat(checked)
        if not isinstance(checked, dt.date):
            raise RuleError(f"{where}: checked（確かめた日）がない")
        looked = tuple(_url(u, where) for u in item.get("looked") or ())
        if not looked:
            raise RuleError(f"{where}: looked（見た頁）がない")
        found = []
        for i, c in enumerate(item.get("found") or (), 1):
            w = f"{where} の {i} 件目"
            if not all(str(c.get(k) or "").strip() for k in ("name", "who", "where")):
                raise RuleError(f"{w}: name・who・where を書く")
            found.append(Candidate(c["name"], c["who"], c["where"], _url(c.get("url"), w)))
        out[code] = PrefCheck(checked, looked, tuple(found))
    return out


@lru_cache(maxsize=1)
def _all() -> dict[str, PrefCheck]:
    return load(rules_dir() / "pref_programs.yaml")


def for_place(code: str) -> PrefCheck | None:
    """団体コード（6 桁）か都道府県コード（2 桁）の頭 2 桁で引く。調べていなければ None。"""
    return _all().get(code[:2])
