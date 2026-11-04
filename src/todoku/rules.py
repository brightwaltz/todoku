"""規則ファイル（rules/facts.yaml と rules/programs/*.yaml）を読み込み、形を検査する。"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_RULES_DIR = Path(__file__).resolve().parents[2] / "rules"

LEVELS = {"national": "国", "machida": "町田市", "yokohama": "横浜市", "tokyo": "東京都", "local": "市区町村（型）"}
# 住所で決まる制度の段：その住所の事実と、出典に要るサイト（その市・都・県の公式ページの出典を 1 つは持つ）
# 神奈川県は、個人で申し込める県の制度が見つからなかったので段を置いていない（11/3 の下調べ）
LEVEL_PLACE = {"machida": "lives_in_machida", "yokohama": "lives_in_yokohama", "tokyo": "lives_in_tokyo"}
LEVEL_SITE = {"machida": "city.machida.tokyo.jp", "yokohama": "city.yokohama.lg.jp", "tokyo": "metro.tokyo.lg.jp"}
KINDS = {"review": "審査", "condition": "条件"}
# 原文の引用を画面に出してよい利用条件。町田市は独自の規約で無断転載が禁じられているので入らない
QUOTABLE_LICENSES = {"PDL1.0"}
BOOL_OPTIONS = ((True, "はい"), (False, "いいえ"))
# 費用の負担（札に 1 行で出す）。出典で言えないものは ask（窓口で確かめる）にする
SELF_PAY = {
    "none": "かからない",
    "partial": "一部を負担",
    "loan": "借りる（返す）",
    "varies": "人や勤め先・事業所で変わる",
    "receive": "お金を受け取る制度",
    "tax": "税が軽くなる制度",
    "ask": "窓口で確かめる",
}
APPLICANTS = ("本人", "家族", "親族", "代理人", "法定代理人")
COST_SHARE_MAX = 40  # 札の 1 行に収める。出典の文は写さず、自分の言葉で短く書く


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
    # 住所の事実：{"city": 団体コード} か {"pref": 都道府県の番号}。値は選んだ市区町村から決まる
    place: dict | None = None
    # False なら Claude に相談文から読ませない
    read: bool = True
    # 選択肢の means：「このどれか（どれかは分からない）」を表す値と、その中身（例：認定は受けているが区分は分からない）
    means: tuple[tuple[Any, tuple[Any, ...]], ...] = ()

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


# 手続の出どころ。国の制度の根拠法令・条項は、このデータを MCP サーバーで引いて写した
PROCEDURES_DATASET = "デジタル庁「行政手続等の棚卸調査結果（令和7年度）」（調査時点 2025-11-01）"
PROCEDURE_MATCHES = {
    "direct": "この制度の申請の手続",
    "general": "この制度を受けるときに使う一般の手続（データにこの制度の記載はない）",
}


@dataclass(frozen=True)
class Procedure:
    id: str
    name: str
    law: str
    article: str
    attachments: str = ""
    match: str = "direct"
    note: str = ""


@dataclass(frozen=True)
class Cost:
    self_pay: str = "ask"
    # 割合・額などの一言（自分の言葉で）
    share: str = ""
    who_can_apply: tuple[str, ...] = ()
    # どの出典で言えるか（その制度の sources の URL）
    source: str = ""


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
    # 申請して結果を待っている（例：認定を申請中）。前提にしている制度は「先に手続きをすると使える」のまま
    pending_when: dict | None = None
    at_application: list[str] = field(default_factory=list)
    apply: dict = field(default_factory=dict)
    bring: list[str] = field(default_factory=list)
    amount: list[str] = field(default_factory=list)
    timing: list[str] = field(default_factory=list)
    # 行政手続データの手続（根拠法令・条項）。国の制度だけ
    procedures: list[Procedure] = field(default_factory=list)
    # 行政手続データに手続がないときの理由（探したことが分かるように、空にせず理由を書く）
    procedures_none: str = ""
    # 町田市以外にお住まいの人に出す申請先・持ち物・時期（apply・bring・timing を置き換える）
    elsewhere: dict = field(default_factory=dict)
    # 前提の手続き（via と prereq で参照している制度）。読み込みのときに埋める
    depends_on: set[str] = field(default_factory=set)
    # 市区町村の型だけ：市の制度を一つずつ判定している市（段の名前）でも、型のまま出す市
    shown_in: tuple[str, ...] = ()
    cost: Cost = field(default_factory=Cost)


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

    @property
    def places(self) -> dict[str, dict]:
        """住所の事実 → place（facts.yaml の順）。住所が分からないときは最初のものが、市区町村を選ぶ問いを受け持つ（町田市ではないとだけ分かったときは、まだ分からない次のもの。questions.place_fact）。"""
        return {f.id: f.place for f in self.facts.values() if f.place}

    @property
    def place_question(self) -> str | None:
        return next(iter(self.places), None)

    def place_values(self, code: str) -> dict[str, bool]:
        """選んだ市区町村（団体コード）から、住所の事実をすべて決める。都道府県は団体コードの上 2 桁。"""
        return {fid: code == p["city"] if "city" in p else code[:2] == p["pref"] for fid, p in self.places.items()}


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
            means = tuple((o["value"], tuple(o["means"])) for o in spec["options"] if "means" in o)
            for value, inner in means:
                if not inner or any(v not in {x for x, _ in options} or v == value for v in inner):
                    raise RuleError(f"facts.yaml: {fid} の {value} の means は、ほかの選択肢を並べて書く")
        else:
            raise RuleError(f"facts.yaml: {fid} の type が bool でも enum でもない")
        for key in ("name", "question", "check"):
            if not spec.get(key):
                raise RuleError(f"facts.yaml: {fid} に {key} がない")
        false_when = spec.get("false_when")
        if false_when is not None and ftype != "bool":
            raise RuleError(f"facts.yaml: false_when は bool の事実だけ（{fid}）")
        place = spec.get("place")
        if place is not None:
            ok = ftype == "bool" and isinstance(place, dict) and len(place) == 1 and (
                str(place.get("city", "")).isdigit() and len(str(place.get("city"))) == 6
                or str(place.get("pref", "")).isdigit() and len(str(place.get("pref"))) == 2)
            if not ok:
                raise RuleError(f"facts.yaml: {fid} の place は bool の事実に {{city: 6 桁}} か {{pref: 2 桁}} で書く")
            place = {k: str(v) for k, v in place.items()}
        facts[fid] = Fact(fid, ftype, spec["question"], spec["check"], options, order, spec["name"], false_when,
                          place, bool(spec.get("read", True)), means if ftype == "enum" else ())
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
    pending_when = raw.get("pending_when")
    if pending_when is not None:
        _check_node(pending_when, facts, set(), where, inside_not=False)

    sources = [_load_source(s, where) for s in need("sources")]
    site = LEVEL_SITE.get(level)
    if site and not any(site in s.url for s in sources):
        raise RuleError(f"{where}: {LEVELS[level]}の制度に{LEVELS[level]}のページの出典がない")
    if level == "local" and not _has_key(eligibility, "ask_at"):
        # 型は「お住まいの市区町村にあるか」を規則では決めない。必ず確かめる先（ask_at）を書く
        raise RuleError(f"{where}: 市区町村の型には、実施しているかを確かめる ask_at の条件が要る")

    procedures = [_load_procedure(p, where) for p in raw.get("procedures") or []]
    procedures_none = (raw.get("procedures_none") or "").strip()
    if procedures_none and procedures:
        raise RuleError(f"{where}: procedures と procedures_none は両方書かない")
    if procedures_none and level != "national":
        raise RuleError(f"{where}: procedures_none は国の制度だけ")
    if procedures and level != "national":
        raise RuleError(f"{where}: procedures は国の制度だけ")

    # 国の制度で、申請先などが町田市の書き方のとき、町田市以外にお住まいの人に出す書き方
    elsewhere = dict(raw.get("elsewhere") or {})
    if elsewhere and level != "national":
        raise RuleError(f"{where}: elsewhere は国の制度だけ")
    if set(elsewhere) - {"apply", "bring", "timing"}:
        raise RuleError(f"{where}: elsewhere に書けるのは apply・bring・timing だけ")
    if "apply" in elsewhere and not (elsewhere["apply"] or {}).get("where"):
        raise RuleError(f"{where}: elsewhere の apply に where がない")

    shown_in = tuple(raw.get("shown_in") or ())
    if shown_in and (level != "local" or set(shown_in) - set(LEVEL_PLACE)):
        raise RuleError(f"{where}: shown_in は市区町村の型だけに、段の名前（{'・'.join(LEVEL_PLACE)}）で書く")

    cost = _load_cost(raw.get("cost"), sources, where)

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
        pending_when=pending_when,
        at_application=list(raw.get("at_application") or []),
        apply=dict(need("apply")),
        bring=list(raw.get("bring") or []),
        amount=_as_list(raw.get("amount")),
        timing=_as_list(raw.get("timing")),
        procedures=procedures,
        procedures_none=procedures_none,
        elsewhere=elsewhere,
        depends_on=depends_on,
        shown_in=shown_in,
        cost=cost,
    )


def _load_cost(raw: Any, sources: list[Source], where: str) -> Cost:
    if raw is None:
        return Cost()
    if not isinstance(raw, dict):
        raise RuleError(f"{where}: cost は辞書で書く")
    if set(raw) - {"self_pay", "share", "who_can_apply", "source"}:
        raise RuleError(f"{where}: cost に書けるのは self_pay・share・who_can_apply・source だけ")
    self_pay = raw.get("self_pay", "ask")
    if self_pay not in SELF_PAY:
        raise RuleError(f"{where}: cost の self_pay は {sorted(SELF_PAY)} のどれか")
    share = str(raw.get("share") or "").strip()
    who = tuple(raw.get("who_can_apply") or ())
    source = str(raw.get("source") or "").strip()
    if set(who) - set(APPLICANTS):
        raise RuleError(f"{where}: cost の who_can_apply は {'・'.join(APPLICANTS)} から選ぶ")
    if self_pay == "ask" and share:
        raise RuleError(f"{where}: 窓口で確かめる（ask）ときは share を書かない")
    if len(share) > COST_SHARE_MAX:
        raise RuleError(f"{where}: cost の share は {COST_SHARE_MAX} 字まで（札の 1 行に収める）")
    if (self_pay != "ask" or who) and not source:
        raise RuleError(f"{where}: cost を埋めるときは、どの出典で言えるかを source（URL）に書く")
    if source and source not in {s.url for s in sources}:
        raise RuleError(f"{where}: cost の source が sources の URL のどれでもない: {source}")
    return Cost(self_pay=self_pay, share=share, who_can_apply=who, source=source)


def _load_procedure(raw: dict, where: str) -> Procedure:
    if not isinstance(raw, dict):
        raise RuleError(f"{where}: procedures の中は辞書で書く: {raw!r}")
    for key in ("id", "name", "law", "article"):
        if not raw.get(key):
            raise RuleError(f"{where}: 手続に {key} がない")
    if not isinstance(raw["id"], str):
        raise RuleError(f"{where}: 手続ID は文字列で書く（先頭の 0 や英字を失わないように）: {raw['id']!r}")
    match = raw.get("match", "direct")
    if match not in PROCEDURE_MATCHES:
        raise RuleError(f"{where}: 手続の match は {sorted(PROCEDURE_MATCHES)} のどれか")
    note = (raw.get("note") or "").strip()
    if match == "general" and not note:
        raise RuleError(f"{where}: 一般の手続（{raw['id']}）には、この制度との関係を note に書く")
    return Procedure(
        id=raw["id"],
        name=raw["name"],
        law=raw["law"],
        article=raw["article"],
        attachments=raw.get("attachments", ""),
        match=match,
        note=note,
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


def _has_key(node: dict, key: str) -> bool:
    if key in node:
        return True
    children = node.get("all") or node.get("any") or ([node["not"]] if "not" in node else [])
    return any(_has_key(c, key) for c in children)


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
    if "ask_at" in node:
        if inside_not:
            raise RuleError(f"{where}: not の中で ask_at は使えない")
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
    if op == "in":
        # means の値（例：区分の分からない認定）は、中身が全部 in にあれば満たし、一部だけなら「わからない」（unsure）にする
        for value, inner in fact.means:
            if value in node["in"]:
                continue
            hits = [v for v in inner if v in node["in"]]
            if len(hits) == len(inner):
                node["in"] = [*node["in"], value]
            elif hits and value not in node.get("unsure", []):
                node["unsure"] = [*node.get("unsure", []), value]
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
