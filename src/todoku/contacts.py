"""47 都道府県の相談先（rules/contacts/prefectures.yaml。T-123）：/city/ の頁の都道府県の段に出す。

判定には使わない。ファイルに書くのは URL・電話・確認日だけで、頁の文は写さない。名前と説明は種類（KINDS）から作る。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .rules import RuleError, rules_dir

# 種類 → (名前の型, 説明)。文言はここだけで差し替える
KINDS = {
    "family_branch": ("認知症の人と家族の会 {pref}支部",
                      "認知症の人を介護する家族の全国の会の支部です。電話相談の曜日と時間、相談専用の番号は支部の頁で確かめてください。"),
    # T-130：国保連（都道府県に 1 つ）の介護サービスの苦情相談
    "kokuho_complaint": ("{pref}国民健康保険団体連合会（介護サービスの苦情相談）",
                         "介護保険のサービスへの苦情を受ける、都道府県に 1 つの窓口です。市区町村の介護保険の窓口やケアマネジャーにも相談できます。"
                         "受付の曜日と時間、苦情相談の番号は頁で確かめてください。"),
}
PREF_CODES = tuple(f"{i:02d}" for i in range(1, 48))
TEL = re.compile(r"0\d{1,4}-\d{1,4}-\d{3,4}")


@dataclass(frozen=True)
class Contact:
    kind: str
    url: str
    tel: str
    checked: dt.date

    def name(self, pref: str) -> str:
        return KINDS[self.kind][0].format(pref=pref)

    @property
    def note(self) -> str:
        return KINDS[self.kind][1]


def load(path: Path) -> dict[str, list[Contact]]:
    """都道府県コード 2 桁 → 相談先。ファイルが無ければ空（規則の置き場を差し替えたテストなど）。"""
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, list[Contact]] = {}
    for code, items in (raw.get("prefectures") or {}).items():
        code = str(code)
        if code not in PREF_CODES:
            raise RuleError(f"{path.name}: 都道府県コード {code} は 01〜47 の 2 桁で書く")
        if not items:
            raise RuleError(f"{path.name}: {code} に相談先が無い")
        contacts = []
        for i, item in enumerate(items, 1):
            where = f"{path.name} の {code} の {i} 件目"
            if item.get("kind") not in KINDS:
                raise RuleError(f"{where}: kind は {'・'.join(KINDS)} のどれか")
            url = str(item.get("url") or "")
            if not url.startswith("https://"):
                raise RuleError(f"{where}: URL は https で始める")
            tel = str(item.get("tel") or "")
            if tel and not TEL.fullmatch(tel):
                raise RuleError(f"{where}: 電話は 0 で始まる半角の数字とハイフンで書く（{tel}）")
            checked = item.get("checked")
            if isinstance(checked, str):
                checked = dt.date.fromisoformat(checked)
            if not isinstance(checked, dt.date):
                raise RuleError(f"{where}: checked（確かめた日）がない")
            contacts.append(Contact(item["kind"], url, tel, checked))
        out[code] = contacts
    return out


@lru_cache(maxsize=1)
def _all() -> dict[str, list[Contact]]:
    return load(rules_dir() / "contacts" / "prefectures.yaml")


def for_place(code: str) -> tuple[Contact, ...]:
    """団体コード（6 桁）か都道府県コード（2 桁）の頭 2 桁で引く。"""
    return tuple(_all().get(code[:2], ()))
