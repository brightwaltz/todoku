"""費用負担の札（T-073）。規則の cost を検査し、札に 1 行で出す。出典で言えないものは「窓口で確かめる」。"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku import rules as rules_mod  # noqa: E402
from todoku.app import RULES, app  # noqa: E402

client = TestClient(app)
PROGRAMS = Path(rules_mod.DEFAULT_RULES_DIR) / "programs"
FIXTURE = Path(__file__).parent / "fixtures" / "rules"
JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_every_program_file_says_its_cost():
    """40 制度とも cost を書く（言えないものも ask と書き、見たことが分かるようにする）。埋めたものは出典の URL を指す。"""
    files = sorted(PROGRAMS.glob("*.yaml"))
    assert len(files) == len(RULES.programs)
    for f in files:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "cost" in raw, f.name
    filled = [p for p in RULES.programs.values() if p.cost.self_pay != "ask"]
    asked = [p for p in RULES.programs.values() if p.cost.self_pay == "ask"]
    assert filled and asked
    for p in filled:
        assert p.cost.source in {s.url for s in p.sources}, p.id
    # 割合で言える国の制度（介護保険の住宅改修・福祉用具）と、無料の申請
    assert RULES.programs["jutaku-kaishu"].cost.self_pay == "partial"
    assert RULES.programs["kaigo-nintei"].cost.self_pay == "none"
    assert RULES.programs["seikatsu-fukushi-shikin"].cost.self_pay == "loan"


def test_share_is_short_and_not_a_copy_of_the_rule_text():
    """share は札の 1 行に収まる長さ。町田市・横浜市・東京都の頁の文は写さないので、引用（quote）を持つのは PDL の出典だけのまま。"""
    for p in RULES.programs.values():
        assert len(p.cost.share) <= rules_mod.COST_SHARE_MAX, p.id
        assert "「" not in p.cost.share, p.id


def _load_with_cost(tmp_path, block: str):
    shutil.copytree(FIXTURE, tmp_path / "rules")
    path = tmp_path / "rules" / "programs" / "city_alone.yaml"
    path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
    return rules_mod.load(tmp_path / "rules")


URL = "https://www.city.machida.tokyo.jp/x.html"


@pytest.mark.parametrize("block, message", [
    ("cost: {self_pay: free}\n", "self_pay"),
    ("cost: {self_pay: ask, share: 1割}\n", "share を書かない"),
    ("cost: {self_pay: partial, share: 1割}\n", "source"),
    ("cost: {self_pay: partial, share: 1割, source: 'https://example.jp/'}\n", "sources の URL"),
    (f"cost: {{self_pay: ask, who_can_apply: [友人], source: '{URL}'}}\n", "who_can_apply"),
    ("cost: {self_pay: ask, who_can_apply: [本人]}\n", "source"),
    (f"cost: {{self_pay: partial, share: '{'あ' * 41}', source: '{URL}'}}\n", "40 字"),
    (f"cost: {{self_pay: partial, price: 1, source: '{URL}'}}\n", "書けるのは"),
])
def test_cost_is_checked_when_loading(tmp_path, block, message):
    with pytest.raises(rules_mod.RuleError, match=message):
        _load_with_cost(tmp_path, block)


def test_cost_defaults_to_ask_and_loads_when_filled(tmp_path):
    rs = rules_mod.load(FIXTURE)
    assert rs.programs["city_alone"].cost == rules_mod.Cost()
    rs = _load_with_cost(tmp_path, f"cost: {{self_pay: partial, share: 1割, who_can_apply: [本人, 家族], source: '{URL}'}}\n")
    assert rs.programs["city_alone"].cost == rules_mod.Cost("partial", "1割", ("本人", "家族"), URL)


def test_card_view_carries_cost_with_label():
    data = client.post("/api/step", json={"facts": {}, "asked": 99}).json()
    cards = {r["id"]: r for r in data["results"]}
    assert cards, "札が無い"
    for pid, card in cards.items():
        c = card["cost"]
        assert c["label"] == rules_mod.SELF_PAY[c["self_pay"]]
        assert c["self_pay"] == RULES.programs[pid].cost.self_pay
        assert c["share"] == RULES.programs[pid].cost.share
    # 使った数え方で、ask の札も「窓口で確かめる」と出る
    assert any(c["cost"]["self_pay"] == "ask" for c in cards.values())


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_cost_line_text_and_place_on_the_card():
    fn = re.search(r"^function costText\(.*?^\}\n", JS, re.S | re.M).group(0)
    run = fn + """
console.log(JSON.stringify([
  costText({ label: "一部を負担", share: "年12,000円。住民税が非課税なら1,000円", who_can_apply: ["本人", "代理人"] }),
  costText({ label: "窓口で確かめる", share: "", who_can_apply: [] }),
]));
"""
    got = json.loads(subprocess.run(["node", "-e", run], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert got == ["費用：一部を負担（年12,000円。住民税が非課税なら1,000円）。申請できる人：本人・代理人", "費用：窓口で確かめる"]
    card = re.search(r"^function programCard\(.*?^\}\n", JS, re.S | re.M).group(0)
    # 札を閉じたままでも見える所（summary の中）に出す
    assert card.index('class: "cost"') < card.index('const body = el("div"')


# T-073b（2026-11-05）：埋めた cost を、source の頁を開いて照らし直した。照らした頁の URL を制度ごとに残す。
# 30 件中 25 件はそのまま言え、3 件は出典を言える頁に替え（kaigo-nintei・jiritsu-shien-iryo・kogaku-ryoyohi）、
# 2 件は share を頁に合わせた（yokohama-keiro-pass・tokyo-silver-pass）。ask にしたものはない。
COST_RECHECKED_20261105 = {
    "fukushi-yogu-konyu": "https://www.mhlw.go.jp/content/12300000/001668164.pdf",
    "futan-gendogaku": "https://www.kaigokensaku.mhlw.go.jp/commentary/fee.html",
    "hosougu-hochoki": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/hukushi_kaigo/shougaishahukushi/yogu/hosouguhisikyuuseido.html",
    "iryohi-kojo-omutsu": "https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/1122.htm",
    "jiritsu-shien-iryo": "https://www.mhlw.go.jp/bunya/shougaihoken/jiritsu/dl/03.pdf",
    "jutaku-kaishu": "https://www.mhlw.go.jp/content/12300000/001668164.pdf",
    "kaigo-kyugyo-kyufu": "https://www.hellowork.mhlw.go.jp/insurance/insurance_continue.html",
    "kaigo-kyuka": "https://www.mhlw.go.jp/seisakunitsuite/bunya/koyou_roudou/koyoukintou/ryouritsu/kaigo/shorttime-leave/",
    "kaigo-nintei": "https://www.kaigokensaku.mhlw.go.jp/commentary/flow.html",
    "kogaku-gassan": "https://www.kyoukaikenpo.or.jp/benefit/high_cost_medical_expenses/003/index.html",
    "kogaku-kaigo-service": "https://www.kaigokensaku.mhlw.go.jp/commentary/fee.html",
    "kogaku-ryoyohi": "https://www.city.machida.tokyo.jp/iryo/old_iryo/koukikoureisyairyou/koukikoureikyuuhu.html",
    "local-iro": "https://www.pref.saitama.lg.jp/documents/149759/739.pdf",
    "machida-gps": "https://www.city.machida.tokyo.jp/iryo/old/shiminnokatae/seikatsukurashi/moshimo/gps.html",
    "machida-haishoku": "https://www.city.machida.tokyo.jp/iryo/old/shiminnokatae/seikatsukurashi/zaitaku/jiritsushienhaishoku.html",
    "machida-hochoki": "https://www.city.machida.tokyo.jp/iryo/old/shiminnokatae/seikatsukurashi/hocyouki.html",
    "machida-jutaku-setsubi": "https://www.city.machida.tokyo.jp/iryo/kaigo/business/jutakukaisyu/jukai.html",
    "machida-kasai-yobo": "https://www.city.machida.tokyo.jp/iryo/old/shiminnokatae/seikatsukurashi/moshimo/kasaianzenshisutemu.html",
    "machida-kyukyu-tsuho": "https://www.city.machida.tokyo.jp/iryo/old/shiminnokatae/seikatsukurashi/moshimo/kinkyutuhoshisutemu.html",
    "machida-shingu": "https://www.city.machida.tokyo.jp/iryo/old/shiminnokatae/seikatsukurashi/zaitaku/shingushodoku.html",
    "nichijo-jiritsu-shien": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/hukushi_kaigo/seikatsuhogo/chiiki-fukusi-yougo",
    "seikatsu-fukushi-shikin": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/hukushi_kaigo/seikatsuhogo/seikatsu-fukushi-shikin1/index.html",
    "shogaisha-kojo": "https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/1160.htm",
    "tokubetsu-shogaisha-teate": "https://www.mhlw.go.jp/bunya/shougaihoken/jidou/tokubetsu.html",
    "tokyo-silver-pass": "https://www.silver-pass.tokyo/",
    "yokohama-hochoki": "https://www.city.yokohama.lg.jp/kenko-iryo-fukushi/kenko-iryo/iryo/seisaku/hochokijosei.html",
    "yokohama-kami-omutsu": "https://www.city.yokohama.lg.jp/kenko-iryo-fukushi/fukushi-kaigo/koreisha-kaigo/kaigohoken-igai-service/zaitaku-yoengo-shien/kourei-omutsu.html",
    "yokohama-keiro-pass": "https://www.city.yokohama.lg.jp/kenko-iryo-fukushi/fukushi-kaigo/koreisha-kaigo/kaigoyobo-kenkoudukuri-ikigai/ikigai-shakaisanka/keirou.html",
    "yokohama-mimamori": "https://www.city.yokohama.lg.jp/kenko-iryo-fukushi/fukushi-kaigo/koreisha-kaigo/kaigohoken-igai-service/zaitaku-yoengo-shien/mimamori.html",
    "yokohama-shika-kenshin": "https://www.city.yokohama.lg.jp/kenko-iryo-fukushi/fukushi-kaigo/koreisha-kaigo/kaigohoken-igai-service/zaitaku-yoengo-shien/houmonsikakensin.html",
}


def test_filled_cost_was_rechecked_on_its_source_page():
    """埋めた cost は、照らし直した頁を指したまま。source を替えたり新しく埋めたりしたら、頁を開いて照らしてからここに足す。"""
    filled = {p.id: p.cost.source for p in RULES.programs.values() if p.cost.self_pay != "ask"}
    assert filled == COST_RECHECKED_20261105
