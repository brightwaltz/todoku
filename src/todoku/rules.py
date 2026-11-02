"""規則ファイル（rules/facts.yaml と rules/programs/*.yaml）を読み込み、形を検査する。"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_RULES_DIR = Path(__file__).resolve().parents[2] / "rules"

LEVELS = {"national": "国", "machida": "町田市"}
KINDS = {"review": "審査", "condition": "条件"}
# 原文の引用を画面に出してよい利用条件。町田市は独自の規約で無断転載が禁じられているので入らない
QUOTABLE_LICENSES = {"PDL1.0"}
BOOL_OPTIONS = ((True, "はい"), (False, "いいえ"))


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    id: str
    type: str
    question: str
    check: str
    options: tuple[tuple[Any, str], ...]
    order: int
    name: str = ""
    # ほかの事実から「いいえ」と決まる場合（例：認定がなければ介護サービスは使っていない）
    false_when: dict | None = None

    def values(self) -> list[Any]:
        return [v for v, _ in self.options]

    def label_of(self, value: Any) -> str:
        for v, label in self.options:
            if v == value:
                return label
        return str(value)


@dataclass(frozen=True)
class Source:
    url: str
    publisher: str
    license: str
    checked: dt.date
    title: str = ""
    quote: str = ""


@dataclass
class Program:
    id: str
    number: int
    name: str
    level: str
    kind: str
    summary: str
    weight: int
    eligibility: dict
    sources: list[Source]
    done_when: dict | None = None
    at_application: list[str] = field(default_factory=list)
    apply: dict = field(default_factory=dict)
    bring: list[str] = field(default_factory=list)
    amount: list[str] = field(default_factory=list)
    timing: list[str] = field(default_factory=list)
    # 前提の手続き（via と prereq で参照している制度）。読み込みのときに埋める
    depends_on: set[str] = field(default_factory=set)


@dataclass
class RuleSet:
    facts: dict[str, Fact]
    programs: dict[str, Program]
    # 前提の手続きが先に来る順
    order: list[str]
    # その制度を前提にしている制度の数
    dependents: dict[str, int]

    def weight(self, program_id: str) -> int:
        return self.programs[program_id].weight + self.dependents.get(program_id, 0)


def rules_dir() -> Path:
    return Path(os.environ.get("TODOKU_RULES_DIR", DEFAULT_RULES_DIR))


def load(directory: Path | None = None) -> RuleSet:
    directory = directory or rules_dir()
    facts = _load_facts(directory / "facts.yaml")
    programs: dict[str, Program] = {}
    for path in sorted((directory / "programs").glob("*.yaml")):
        program = _load_program(path, facts)
        if program.id in programs:
            raise RuleError(f"{path.name}: id {program.id} が重複している")
        programs[program.id] = program
    for program in programs.values():
        for ref in program.depends_on:
            if ref not in programs:
                raise RuleError(f"{program.id}: 前提の制度 {ref} がない")
    order = _topological_order(programs)
    dependents = {pid: 0 for pid in programs}
    for program in programs.values():
        for ref in program.depends_on:
            dependents[ref] += 1
    return RuleSet(facts=facts, programs=programs, order=order, dependents=dependents)


def _load_facts(path: Path) -> dict[str, Fact]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    facts = {}
    for order, (fid, spec) in enumerate(raw.items()):
        ftype = spec.get("type")
        if ftype == "bool":
            options = BOOL_OPTIONS
        elif ftype == "enum":
            options = tuple((o["value"], o["label"]) for o in spec["options"])
            if len({v for v, _ in options}) != len(options):
                raise RuleError(f"facts.yaml: {fid} の選択肢が重複している")
        else:
            raise RuleError(f"facts.yaml: {fid} の type が bool でも enum でもない")
        for key in ("name", "question", "check"):
            if not spec.get(key):
                raise RuleError(f"facts.yaml: {fid} に {key} がない")
        false_when = spec.get("false_when")
        if false_when is not None and ftype != "bool":
            raise RuleError(f"facts.yaml: false_when は bool の事実だけ（{fid}）")
        facts[fid] = Fact(fid, ftype, spec["question"], spec["check"], options, order, spec["name"], false_when)
    for fact in facts.values():
        if fact.false_when is not None:
            ref = facts.get(fact.false_when.get("fact"))
            if ref is None or any(v not in ref.values() for v in fact.false_when.get("in", [])):
                raise RuleError(f"facts.yaml: {fact.id} の false_when が事実と合わない")
    return facts


def _load_program(path: Path, facts: dict[str, Fact]) -> Program:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    where = path.name

    def need(key: str) -> Any:
        if key not in raw or raw[key] in (None, ""):
            raise RuleError(f"{where}: {key} がない")
        return raw[key]

    pid = need("id")
    if path.stem != pid:
        raise RuleError(f"{where}: ファイル名と id（{pid}）が違う")
    level, kind = need("level"), need("kind")
    if level not in LEVELS:
        raise RuleError(f"{where}: level は {sorted(LEVELS)} のどれか")
    if kind not in KINDS:
        raise RuleError(f"{where}: kind は {sorted(KINDS)} のどれか")

    depends_on: set[str] = set()
    eligibility = need("eligibility")
    _check_node(eligibility, facts, depends_on, where, inside_not=False)
    done_when = raw.get("done_when")
    if done_when is not None:
        _check_node(done_when, facts, set(), where, inside_not=False)

    sources = [_load_source(s, where) for s in need("sources")]
    if level == "machida" and not any("city.machida.tokyo.jp" in s.url for s in sources):
        raise RuleError(f"{where}: 町田市の制度に町田市のページの出典がない")

    return Program(
        id=pid,
        number=int(need("number")),
        name=need("name"),
        level=level,
        kind=kind,
        summary=need("summary").strip(),
        weight=int(raw.get("weight", 1)),
        eligibility=eligibility,
        sources=sources,
        done_when=done_when,
        at_application=list(raw.get("at_application") or []),
        apply=dict(need("apply")),
        bring=list(raw.get("bring") or []),
        amount=_as_list(raw.get("amount")),
        timing=_as_list(raw.get("timing")),
        depends_on=depends_on,
    )


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _load_source(raw: dict, where: str) -> Source:
    for key in ("url", "publisher", "license", "checked"):
        if not raw.get(key):
            raise RuleError(f"{where}: 出典に {key} がない")
    if not str(raw["url"]).startswith("https://"):
        raise RuleError(f"{where}: 出典の URL は https で始める")
    quote = (raw.get("quote") or "").strip()
    if quote and raw["license"] not in QUOTABLE_LICENSES:
        raise RuleError(f"{where}: 利用条件 {raw['license']} のページの原文は引用しない（{raw['url']}）")
    checked = raw["checked"]
    if isinstance(checked, str):
        checked = dt.date.fromisoformat(checked)
    return Source(
        url=raw["url"],
        publisher=raw["publisher"],
        license=raw["license"],
        checked=checked,
        title=raw.get("title", ""),
        quote=quote,
    )


def _check_node(node: Any, facts: dict[str, Fact], depends_on: set[str], where: str, inside_not: bool) -> None:
    if not isinstance(node, dict):
        raise RuleError(f"{where}: 条件は辞書で書く: {node!r}")
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise RuleError(f"{where}: {key} の中が空")
        for child in children:
            _check_node(child, facts, depends_on, where, inside_not)
        return
    if "not" in node:
        _check_node(node["not"], facts, depends_on, where, inside_not=True)
        return
    if not node.get("label"):
        raise RuleError(f"{where}: 条件に label がない: {node!r}")
    if "step" in node:
        if inside_not:
            raise RuleError(f"{where}: not の中で step は使えない")
        return
    if "prereq" in node:
        if inside_not:
            raise RuleError(f"{where}: not の中で prereq は使えない")
        depends_on.add(node["prereq"])
        return
    fid = node.get("fact")
    if fid not in facts:
        raise RuleError(f"{where}: 事実 {fid!r} が facts.yaml にない")
    fact = facts[fid]
    ops = [op for op in ("in", "eq", "is") if op in node]
    if len(ops) != 1:
        raise RuleError(f"{where}: {fid} の条件は in・eq・is のどれか 1 つで書く")
    op = ops[0]
    values = node[op] if op == "in" else [node[op]]
    if op == "is" and fact.type != "bool":
        raise RuleError(f"{where}: is は bool の事実だけ（{fid}）")
    for v in values:
        if v not in fact.values():
            raise RuleError(f"{where}: {fid} に値 {v!r} はない")
    if "via" in node:
        if inside_not:
            raise RuleError(f"{where}: not の中で via は使えない")
        depends_on.add(node["via"])


def _topological_order(programs: dict[str, Program]) -> list[str]:
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(pid: str, path: list[str]) -> None:
        if state.get(pid) == 2:
            return
        if state.get(pid) == 1:
            raise RuleError("前提の手続きが循環している: " + " → ".join(path + [pid]))
        state[pid] = 1
        for ref in sorted(programs[pid].depends_on):
            visit(ref, path + [pid])
        state[pid] = 2
        order.append(pid)

    for pid in sorted(programs, key=lambda p: programs[p].number):
        visit(pid, [])
    return order
