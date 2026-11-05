"""T-076：/city/ の頁の「先に別の手続きが要る」の印（尋ねられにくい制度の候補。決め方は判断待ちで、既定は規則の前提の手続き）。"""

import os
import re

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import RULES, app  # noqa: E402
from todoku.city import FIRST_MARK, FIRST_NOTE  # noqa: E402

client = TestClient(app)
MACHIDA, YOKOHAMA, VILLAGE = "132098", "141003", "023043"


def marks(code: str) -> dict[str, str]:
    """印の付いた制度の id → 先に済ませる手続きの文。"""
    html = client.get(f"/city/{code}").text
    items = re.findall(r'<li class="city-item[^"]*" data-id="([^"]+)" data-first="1">.*?'
                       r'<p class="city-first"><b>[^<]+</b>([^<]+)</p>', html, re.S)
    return dict(items)


def test_programs_after_the_care_certification_are_marked_and_the_certification_itself_is_not():
    got = marks(VILLAGE)
    for pid in ("kogaku-kaigo-service", "kogaku-gassan", "jutaku-kaishu", "fukushi-yogu-konyu", "futan-gendogaku"):
        assert got[pid] == "要介護・要支援認定の申請"
    assert got["hosougu-hochoki"] == "身体障害者手帳（聴覚障害）"
    for pid in ("kaigo-nintei", "kogaku-ryoyohi", "kaigo-kyuka", "kaigo-kyugyo-kyufu"):
        assert pid not in got


def test_marks_come_only_from_prerequisites_or_steps_in_the_rules():
    for code in (MACHIDA, YOKOHAMA, VILLAGE):
        for pid in marks(code):
            p = RULES.programs[pid]
            assert p.depends_on or "'step': True" in repr(p.eligibility), (code, pid)


def test_outside_machida_the_tax_deduction_names_the_local_step_not_the_machida_certificate():
    village, machida = marks(VILLAGE), marks(MACHIDA)
    assert village["shogaisha-kojo"].startswith("お住まいの市区町村で")
    assert "町田市" not in "".join(village.values())
    assert machida["shogaisha-kojo"] == RULES.programs["machida-shogaisha-nintei"].name
    # 町田市では確認書か医師の証明書のどちらかでよいので「または」でつなぐ。町田市の外は医師の証明書だけ
    assert " または " in machida["iryohi-kojo-omutsu"]
    assert village["iryohi-kojo-omutsu"] == "医師に「おむつ使用証明書」を書いてもらう"


def test_city_programs_that_need_the_certification_are_marked_in_their_city():
    assert marks(MACHIDA)["machida-kami-omutsu"] == "要介護・要支援認定の申請"
    assert marks(YOKOHAMA)["yokohama-kami-omutsu"] == "要介護・要支援認定の申請"


def test_the_note_counts_the_marks_and_says_it_is_not_checked():
    for code in (MACHIDA, VILLAGE):
        html = client.get(f"/city/{code}").text
        n = len(marks(code))
        assert n == html.count('data-first="1"') == html.count(f"<b>{FIRST_MARK}</b>")
        assert FIRST_NOTE.format(mark=FIRST_MARK, n=n) in html
        assert "確かめていません" in FIRST_NOTE
