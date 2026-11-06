"""入口の紹介動画（T-140）を web/ に置く前の下ごしらえ。素材と置き場所は bwc-intro の仕様が決めるので、ここでは
仕様によらず要る 3 つだけを固める：(1) CSP が同じ源の動画・字幕・静止画を止めない (2) /static/ が Range に 206 で
答える（iOS の Safari は 206 が返らないと mp4 を再生しない） (3) nosniff の下で、Docker の素の Python でも
mp4・vtt・webp・jpg に正しい Content-Type が付く（3.12 の組み込みの表に無い webp は app.py が足す）。"""
import mimetypes
import os

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import CONTENT_SECURITY_POLICY, EXTRA_MIME_TYPES, app  # noqa: E402

client = TestClient(app)


def _directives() -> dict[str, list[str]]:
    out = {}
    for part in CONTENT_SECURITY_POLICY.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def test_csp_lets_same_origin_video_track_and_poster_load():
    d = _directives()
    # video と track は media-src、無ければ default-src に従う。poster は img-src
    media = d.get("media-src", d.get("default-src", []))
    assert "'self'" in media, media
    assert "'self'" in d["img-src"], d["img-src"]


def test_static_answers_range_requests_with_206():
    r = client.get("/static/style.css", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-99/")
    assert len(r.content) == 100
    assert client.get("/static/style.css").headers["accept-ranges"] == "bytes"


def test_video_files_get_the_right_content_type_without_system_mime_files():
    """Docker の素の Python と同じく、組み込みの表＋app.py が足した型だけで決まること。手元の macOS は
    /etc/apache2/mime.types などを読むので、グローバルの mimetypes.guess_type だけでは抜けに気づけない。"""
    builtin = mimetypes.MimeTypes()  # 組み込みの表だけ（端末の mime.types を読まない）
    for name, want in [("intro.mp4", "video/mp4"), ("intro.webm", "video/webm"), ("intro.vtt", "text/vtt"),
                       ("intro-poster.webp", "image/webp"), ("intro-poster.jpg", "image/jpeg")]:
        ext = name[name.rindex("."):]
        assert (builtin.guess_type(name)[0] or EXTRA_MIME_TYPES.get(ext)) == want, name
        assert mimetypes.guess_type(name)[0] == want, name  # StaticFiles（FileResponse）が使うのはこちら
