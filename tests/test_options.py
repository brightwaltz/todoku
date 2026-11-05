"""T-084：有料・民間の支え（行政の制度ではない候補）の読み込みと並べ替え。判定はしない。"""
from pathlib import Path

import pytest

from todoku import options as options_mod
from todoku import rules as rules_mod

RULES = rules_mod.load()
SRC = [{"url": "https://www.mhlw.go.jp/example", "publisher": "厚生労働省", "license": "未確認", "checked": "2026-11-05"}]


def write(tmp_path: Path, items: list) -> Path:
    import yaml
    path = tmp_path / "options.yaml"
    path.write_text(yaml.safe_dump({"options": items}, allow_unicode=True), encoding="utf-8")
    return path


def item(**kw):
    base = {"id": "a", "name": "家事の手伝い", "summary": "掃除や買い物", "where": "地域の団体", "sources": SRC}
    return {**base, **kw}


def test_no_file_means_no_options(tmp_path):
    assert options_mod.load(tmp_path / "options.yaml", RULES.facts) == []


def test_rank_by_matching_hints_without_dropping(tmp_path):
    hint = {"fact": "household", "in": ["alone"], "label": "ひとり暮らし"}
    opts = options_mod.load(write(tmp_path, [item(id="a"), item(id="b", hints=[hint])]), RULES.facts)
    assert [v["id"] for v in options_mod.ranked(opts, {"household": "alone"})] == ["b", "a"]
    views = options_mod.ranked(opts, {"household": "with_family"})
    assert [v["id"] for v in views] == ["a", "b"] and all(v["tag"] == options_mod.TAG for v in views)
    assert views[1]["why"] == []


@pytest.mark.parametrize("bad", [
    item(hints=[{"fact": "no_such", "is": True, "label": "x"}]),
    item(hints=[{"fact": "household", "in": ["nobody"], "label": "x"}]),
    item(cost="1時間 800円"),  # 費用の数字は出典を指す
    item(where=""),
])
def test_bad_options_are_rejected(tmp_path, bad):
    with pytest.raises(rules_mod.RuleError):
        options_mod.load(write(tmp_path, [bad]), RULES.facts)


def test_cost_needs_its_source(tmp_path):
    opts = options_mod.load(write(tmp_path, [item(cost="1時間 800円", cost_source=SRC[0]["url"])]), RULES.facts)
    assert options_mod.ranked(opts, {})[0]["cost"] == "1時間 800円"


def test_real_options_file_loads_with_national_sources_only():
    path = Path(__file__).resolve().parents[1] / "rules" / "options.yaml"
    opts = options_mod.load(path, RULES.facts)
    assert len(opts) >= 2
    for o in opts:
        assert o.sources and all(s.url.startswith(("https://www.mhlw.go.jp/", "https://www.mlit.go.jp/")) for s in o.sources)
        assert not o.cost  # 出典に数字が無いので書かない
