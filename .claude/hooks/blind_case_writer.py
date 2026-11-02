"""PreToolUse フック：事例係（サブエージェント case-writer）に、規則と判定器を開かせない。

評価の事例が「自分の規則をなぞるだけ」にならないように、事例係が触れてよい場所を
eval/cases/ と rules/facts.yaml（事実の言葉の定義）だけに絞る。ほかの場所の読み書きと、
場所を決めない検索（リポジトリ全体を読む）は止める（終了コード 2）。
メインの会話やほかのサブエージェントには何もしない。agent_type はフックの入力に入る
（2026-11-03 に Claude Code 2.1.316 で確かめた）。Python 3.8 でも動くように書く。
"""

import json
import os
import sys

AGENT = "case-writer"
ALLOWED_DIRS = ("eval/cases",)
ALLOWED_FILES = ("rules/facts.yaml",)


def allowed(path: str, project: str) -> bool:
    if not path:
        return False
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(project, path))
    root = os.path.realpath(project)
    for d in ALLOWED_DIRS:
        base = os.path.join(root, d)
        if full == base or full.startswith(base + os.sep):
            return True
    return any(full == os.path.join(root, f) for f in ALLOWED_FILES)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("agent_type") != AGENT:
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    tool = data.get("tool_name") or ""
    args = data.get("tool_input") or {}
    if tool in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = args.get("file_path") or args.get("notebook_path") or ""
    elif tool in ("Grep", "Glob"):
        target = args.get("path") or ""
    else:
        target = ""
    if allowed(target, project):
        return 0
    sys.stderr.write(
        "事例係は規則ファイル（rules/programs/）・判定器（src/）・正解表を開かない約束です。"
        "使えるのは eval/cases/ と rules/facts.yaml だけです（検索は path を eval/cases に決めてください）。"
        "止めた呼び出し：{} {}\n".format(tool, target or "（場所の指定なし）")
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
