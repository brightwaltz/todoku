"""T-192：docs/spec.md の「公開」の段を、合言葉なしで開ける形（T-180）に合わせる。

T-180 で TODOKU_OPEN=1 なら Claude を呼ぶ口を合言葉なしで開き、1 日の費用の上限（TODOKU_DAILY_USD）に届いたら
見本へ寄せるようにした。spec は「合言葉は Claude を呼ぶ口だけに掛け」のままで、README（T-190）とも食い違っていた。
"""
from pathlib import Path

from todoku import app as todoku_app

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
APP_SRC = (ROOT / "src" / "todoku" / "app.py").read_text(encoding="utf-8")


def _publish_section() -> str:
    after = SPEC.split("\n## 公開\n", 1)[1]
    return after.split("\n## ", 1)[0]


def test_spec_publish_names_open_switch_and_daily_cap():
    section = _publish_section()
    for name in ("TODOKU_OPEN", "TODOKU_DAILY_USD", "TODOKU_MACHINES"):
        assert name in section
        # spec に書いた名前を、アプリが本当に読んでいる
        assert f'"{name}"' in APP_SRC
    assert "429" in section and "見本へ寄せる" in section
    # 上限に届いた日の断りも見本へ寄せる
    assert "見本" in todoku_app.CLOSED_FOR_TODAY


def test_spec_publish_no_longer_says_passphrase_always_required():
    section = _publish_section()
    assert "合言葉は Claude を呼ぶ口（読み取り・まとめ）だけに掛け" not in section
    # 既定は今のまま合言葉が要る
    assert 'os.environ.get("TODOKU_OPEN", "0")' in APP_SRC
