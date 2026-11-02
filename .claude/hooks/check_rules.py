"""PostToolUse フック：rules/ の YAML を書き換えたら、規則の読み込みの検査を回す。

検査は src/todoku/rules.py の load()。形の誤り、町田市のページの原文の引用、前提の循環、
行政手続データの手続の形などを、書き換えた直後に Claude へ返す（終了コード 2 で stderr が Claude に届く）。
ネットにはつながない（引用が公式ページにあるかの照合は scripts/check_quotes.py を手で回す）。
Python 3.8 でも動くように書く。
"""

import json
import os
import shutil
import subprocess
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    rules = os.path.join(os.path.realpath(project), "rules") + os.sep
    if not path.endswith(".yaml") or not os.path.realpath(path).startswith(rules):
        return 0
    if shutil.which("uv") is None:
        return 0
    try:
        done = subprocess.run(
            ["uv", "run", "--quiet", "python", "-c", "from todoku.rules import load; load()"],
            cwd=project, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("規則の検査が 120 秒で終わらなかった。uv run pytest で確かめてください。\n")
        return 2
    if done.returncode == 0:
        return 0
    tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
    sys.stderr.write("規則ファイルの検査で止まった（src/todoku/rules.py の load）：\n" + "\n".join(tail) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
