"""T-190：README の「試す」の行を、合言葉なしで送れる形（T-180）に合わせる。

T-180 で、TODOKU_OPEN=1 なら自分の相談を合言葉なしで送れ、1 日の費用の上限に届いたら見本へ寄せるようにした。
README の 5 行目は「ご自分の相談を送るには合言葉が要ります」のままで、審査員が最初に読む行が画面と食い違っていた。
"""
from pathlib import Path

from todoku import app as todoku_app

ROOT = Path(__file__).resolve().parents[1]


def _try_line() -> str:
    lines = [l for l in (ROOT / "README.md").read_text(encoding="utf-8").splitlines() if l.startswith("**試す**")]
    assert len(lines) == 1
    return lines[0]


def test_readme_try_line_says_own_consult_needs_no_passphrase():
    line = _try_line()
    assert "https://todoku.fly.dev/" in line
    assert "合言葉が要ります" not in line
    assert "ご自分の相談を合言葉なしで送れます" in line


def test_readme_try_line_says_samples_remain_after_daily_cap():
    line = _try_line()
    assert "見本で試す" in line
    assert "1 日の上限" in line and "見本だけ" in line
    # 上限に届いた日の断り（画面と 429 の本文）も見本へ寄せる
    assert "見本" in todoku_app.CLOSED_FOR_TODAY
