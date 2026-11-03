"""PreToolUse フック：事例係（サブエージェント case-writer）に、規則と判定器を開かせない。

評価の事例が「自分の規則をなぞるだけ」にならないように、事例係が触れてよい場所を
eval/cases/ と rules/facts.yaml（事実の言葉の定義。読むだけ）だけに絞る。ほかの場所の読み書き、
facts.yaml への書き込み、場所を決めない検索（リポジトリ全体を読む）、入力が読めない呼び出しは止める（終了コード 2）。
メインの会話やほかのサブエージェントには何もしない。agent_type はフックの入力に入る
（2026-11-03 に Claude Code 2.1.316 で確かめた）。Python 3.8 でも動くように書く。
"""

import json
import os
import sys

AGENT = "case-writer"
ALLOWED_DIRS = ("eval/cases",)
ALLOWED_FILES = ("rules/facts.yaml",)


def allowed(path: str, project: str, files=ALLOWED_FILES) -> bool:
    if not path:
        return False
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(project, path))
    root = os.path.realpath(project)
    for d in ALLOWED_DIRS:
        base = os.path.join(root, d)
        if full == base or full.startswith(base + os.sep):
            return True
    return any(full == os.path.join(root, f) for f in files)


def escapes(pattern: str) -> bool:
    """Glob の pattern・Grep の glob が、決めた場所の外を指していないか。
    文字列の照合なので、手間を増やすだけの守り（パスを解決して確かめるわけではない）。
    事例係の道具は Read・Write・Edit・Glob・Grep だけ（agents/case-writer.md の tools）で、
    Bash・Task・mcp などは定義の時点で使えない。こちらが能力を取り除く方の守り。"""
    return bool(pattern) and (".." in pattern or pattern.startswith(("/", "~")))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        # 入力が読めないときは止める（誰の呼び出しか分からないので、通すと目隠しが外れうる）
        sys.stderr.write("blind_case_writer.py：フックの入力（JSON）が読めないので止めました\n")
        return 2
    if data.get("agent_type") != AGENT:
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    tool = data.get("tool_name") or ""
    args = data.get("tool_input") or {}
    writing = tool in ("Write", "Edit", "MultiEdit", "NotebookEdit")
    if tool == "Read" or writing:
        target = args.get("file_path") or args.get("notebook_path") or ""
    elif tool in ("Grep", "Glob"):
        target = args.get("path") or ""
        if escapes(args.get("pattern", "") if tool == "Glob" else args.get("glob", "")):
            target = ""
    else:
        target = ""
    # rules/facts.yaml は読むだけ（事実の言葉は窓口の側が決める。事例係が書き換えると、事例に合わせて定義が動く）
    if writing and target and not allowed(target, project, files=()):
        target = ""
    if allowed(target, project):
        return 0
    sys.stderr.write(
        "事例係は規則ファイル（rules/programs/）・判定器（src/）・正解表を開かない約束です。"
        "使えるのは eval/cases/ と、読むだけの rules/facts.yaml です（検索は path を eval/cases に決め、.. を使わないでください）。"
        "止めた呼び出し：{} {}\n".format(tool, target or "（場所の指定なし）")
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
