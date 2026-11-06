"""頁が見つからないとき（T-158）：ブラウザで開くと入口へ戻れる短い頁を出し、/api/ と /static/ は今までどおり JSON のまま。"""

from fastapi.testclient import TestClient

from todoku.app import NOT_FOUND_CITY, NOT_FOUND_LINK, NOT_FOUND_OTHER, app

client = TestClient(app)
BROWSER = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def test_unknown_city_code_in_browser_is_a_page_back_to_the_top():
    r = client.get("/city/999999", headers=BROWSER)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert NOT_FOUND_CITY in r.text
    assert f'<a class="primary" href="/">{NOT_FOUND_LINK}</a>' in r.text
    assert '"detail"' not in r.text
    assert '<meta name="robots" content="noindex">' in r.text
    # 守りのヘッダは付いたまま（middleware は例外の応答にもかかる）
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_other_unknown_path_in_browser_says_the_address_may_be_cut():
    r = client.get("/no-such-page", headers=BROWSER)
    assert r.status_code == 404
    assert NOT_FOUND_OTHER in r.text and NOT_FOUND_CITY not in r.text


def test_head_from_a_browser_gets_the_same_status():
    # 一覧に無い団体コードの HEAD は tests/test_city_head_t159.py で確かめる（/city/ は T-159 まで HEAD が 405 だった）
    assert client.head("/no-such-page", headers=BROWSER).status_code == 404


def test_api_and_static_keep_json_detail():
    # app.js は /api/ の失敗で detail をそのまま画面に出すので、JSON のまま
    r = client.get("/api/no-such", headers=BROWSER)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = client.get("/static/no-such.png", headers=BROWSER)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = client.post("/api/sample", json={"id": "no-such-sample"})
    assert r.status_code == 404 and r.json()["detail"] == "この見本はまだ用意できていません"


def test_without_html_accept_city_404_stays_json():
    # curl や見張りの道具（Accept: */*）には今までどおり JSON の 1 文を返す
    r = client.get("/city/999999")
    assert r.status_code == 404 and r.json() == {"detail": "その団体コードの市区町村は一覧にありません"}


def test_known_city_page_is_unchanged():
    r = client.get("/city/132098", headers=BROWSER)
    assert r.status_code == 200 and "見つかりませんでした" not in r.text
