""".claude/hooks の 2 つのフックを、Claude Code と同じ形の入力（標準入力の JSON）で動かして確かめる。"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


def run(hook: str, payload: dict, python: str = sys.executable) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python, "-I", str(HOOKS / hook)],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
        env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"}, timeout=30,
    )


def call(tool: str, agent: str | None, **args) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args, "cwd": str(ROOT)}
    if agent:
        payload["agent_type"] = agent
    return payload


@pytest.mark.parametrize(
    "tool,args",
    [
        ("Read", {"file_path": str(ROOT / "rules/programs/jutaku-kaishu.yaml")}),
        ("Read", {"file_path": "src/todoku/engine.py"}),
        ("Read", {"file_path": str(ROOT / "eval/gold_sheet.csv")}),
        ("Read", {"file_path": str(ROOT / "eval/cases/../../rules/programs/kaigo-nintei.yaml")}),
        ("Grep", {"pattern": "care_level"}),
        ("Glob", {"pattern": "**/*.yaml"}),
        ("Bash", {"command": "cat rules/programs/kaigo-nintei.yaml"}),
        ("WebFetch", {"url": "https://example.com"}),
        # facts.yaml は読むだけ
        ("Write", {"file_path": str(ROOT / "rules/facts.yaml"), "content": "x"}),
        ("Edit", {"file_path": "rules/facts.yaml", "old_string": "a", "new_string": "b"}),
        # 場所を eval/cases に決めても、pattern・glob で外を指す検索
        ("Glob", {"pattern": "../../rules/programs/*.yaml", "path": "eval/cases"}),
        ("Glob", {"pattern": "/etc/*", "path": "eval/cases"}),
        ("Grep", {"pattern": "care_level", "path": "eval/cases", "glob": "../../rules/**"}),
    ],
)
def test_case_writer_is_blocked_outside_cases(tool, args):
    done = run("blind_case_writer.py", call(tool, "case-writer", **args))
    assert done.returncode == 2, (tool, args, done.stderr)
    assert "事例係" in done.stderr


@pytest.mark.parametrize(
    "tool,args",
    [
        ("Read", {"file_path": str(ROOT / "eval/cases/cases.yaml")}),
        ("Read", {"file_path": str(ROOT / "rules/facts.yaml")}),
        ("Write", {"file_path": str(ROOT / "eval/cases/new.yaml"), "content": "x"}),
        ("Grep", {"pattern": "age_band", "path": "eval/cases"}),
        ("Glob", {"pattern": "*.yaml", "path": "eval/cases"}),
        ("Edit", {"file_path": str(ROOT / "eval/cases/cases.yaml"), "old_string": "a", "new_string": "b"}),
    ],
)
def test_case_writer_may_use_cases_and_facts(tool, args):
    assert run("blind_case_writer.py", call(tool, "case-writer", **args)).returncode == 0


@pytest.mark.parametrize("agent", [None, "rule-reader", "Explore"])
def test_other_agents_are_untouched(agent):
    payload = call("Read", agent, file_path=str(ROOT / "rules/programs/jutaku-kaishu.yaml"))
    assert run("blind_case_writer.py", payload).returncode == 0


def test_unreadable_input_is_blocked():
    # 誰の呼び出しか分からない入力は通さない
    done = subprocess.run([sys.executable, "-I", str(HOOKS / "blind_case_writer.py")], input="{not json",
                          capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"}, timeout=30)
    assert done.returncode == 2 and "読めない" in done.stderr


def test_check_rules_ignores_other_files():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(ROOT / "README.md")}, "cwd": str(ROOT)}
    assert run("check_rules.py", payload).returncode == 0


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv がない")
def test_check_rules_passes_on_current_rules():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(ROOT / "rules/programs/kaigo-nintei.yaml")}}
    done = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "check_rules.py")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=150,
        env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": str(Path(shutil.which("uv")).parent) + ":/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="システムの python3 がない")
def test_hooks_run_on_system_python():
    # settings.json は python3 で呼ぶので、古い Python でも構文で落ちないことを確かめる
    payload = call("Read", "case-writer", file_path=str(ROOT / "rules/programs/jutaku-kaishu.yaml"))
    for python in ("/usr/bin/python3", shutil.which("python3") or "/usr/bin/python3"):
        assert run("blind_case_writer.py", payload, python=python).returncode == 2
        assert run("check_rules.py", {"tool_input": {"file_path": "README.md"}}, python=python).returncode == 0
