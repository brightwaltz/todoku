"""T-197：入口の紹介動画を入力の札の外の小さな札にし、押すとその場で広げて自前の操作で流す。
画面の動き（押す前・再生中・キーボード）は $OPS/tools/intro_t197.py で 390・1280 を測る。ここでは形と素材のつじつまを見る。"""
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")
CARD = re.search(r'<section id="intro" class="card intro"[^>]*>(.*?)</section>', HTML, re.S).group(1)


def _mp4_seconds(data: bytes) -> float:
    """moov の中の mvhd から長さ（秒）を読む。"""
    i = data.index(b"mvhd") - 4
    version = data[i + 8]
    if version == 1:
        scale, length = struct.unpack(">IQ", data[i + 28:i + 40])
    else:
        scale, length = struct.unpack(">II", data[i + 20:i + 28])
    return length / scale


def test_scenes_json_has_one_start_per_step_and_matches_the_video():
    scenes = json.loads((WEB / "intro-scenes.json").read_text(encoding="utf-8"))
    steps, duration = scenes["steps"], scenes["duration"]
    assert len(steps) == CARD.count("<li>") == 4
    assert steps[0] == 0 and steps == sorted(steps) and len(set(steps)) == len(steps)
    assert steps[-1] < duration
    src = re.search(r'<source src="/static/([^"]+)" type="video/mp4">', CARD).group(1)
    assert abs(_mp4_seconds((WEB / src).read_bytes()) - duration) < 0.5
    # 題の「21秒」と、操作の時間の初めの値は素材の長さ（四捨五入）と同じ
    assert f"{round(duration)}秒でわかる" in CARD and f"0:00 / 0:{round(duration):02d}" in CARD


def test_nothing_plays_or_loads_before_the_press():
    player = re.search(r'<div class="intro-player" id="intro-player"[^>]*>', CARD).group(0)
    assert " hidden" in player
    video = re.search(r"<video [^>]*>", CARD).group(0)
    assert 'preload="none"' in video and "autoplay" not in video and " controls" not in video
    img = re.search(r"<img [^>]*>", CARD).group(0)
    assert 'alt=""' in img and 'loading="lazy"' in img  # 名前は押す所（button）の aria-label が持つ
    # JSON は押した後で取る。play() は押した出来事の中で、JSON を待たずに呼ぶ
    handler = JS[JS.index('$("intro-open").addEventListener("click"'):]
    handler = handler[:handler.index("});")]
    assert handler.index("play();") < handler.index('fetch("/static/intro-scenes.json")')


def test_controls_have_names_and_are_reachable_by_keyboard():
    opener = re.search(r'<button type="button" class="intro-thumb" id="intro-open"[^>]*>', CARD).group(0)
    assert 'aria-controls="intro-player"' in opener and 'aria-expanded="false"' in opener and "音声なし" in opener
    assert re.search(r'<button type="button" class="intro-btn" id="intro-toggle" aria-label="[^"]+">', CARD)
    assert '<button type="button" class="intro-btn intro-again" id="intro-again">もう一度</button>' in CARD
    bar = re.search(r'<div class="intro-bar" id="intro-bar"[^>]*>', CARD).group(0)
    assert 'role="progressbar"' in bar and "aria-label=" in bar and "aria-valuetext=" in bar
    assert 'aria-current", "step"' in JS  # 再生中のいまの手順
    # 音の道が無いので音量の操作は置かない
    assert "volume" not in CARD and "muted" not in CARD


def test_card_looks_like_the_other_cards_and_keeps_the_old_rules():
    assert re.search(r"\.intro-player video \{[^}]*max-width: 560px", CSS)
    thumb = re.search(r"\.intro-thumb \{([^}]+)\}", CSS).group(1)
    assert "width: 160px" in thumb and "border: 1px solid var(--line)" in thumb
    assert re.search(r"@media \(max-width: 32\.5em\) \{[^@]*\.intro-thumb \{ width: 96px; \}", CSS)
    intro_rules = "\n".join(line for line in CSS.splitlines() if ".intro" in line)
    assert "#000" not in intro_rules and "black" not in intro_rules
    assert ".intro-steps li { transition: none; }" in CSS  # 動きを減らす設定
    assert any(".intro { display: none; }" in p for p in re.findall(r"@media print \{(.*?)\n\}", CSS, re.S))
    fc = re.search(r"@media \(forced-colors: active\) \{(.*?)\n\}", CSS, re.S).group(1)
    assert ".intro-play" in fc and ".intro-thumb" in fc
