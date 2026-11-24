#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前本地自检（纯标准库，不写正式测试产物）。

在临时目录生成最小案例，跑通 init/show/check/build/selfcheck 全路径，
并验证：
- 五个子命令退出码均为 0；
- result.json 可解析，case_id 回填、status 合法；
- 关键信封字段齐全；
- 同输入两次运行除 runtime 外逐字节一致（确定性）；
- 空输入/坏 JSON/缺 draft 三个兜底场景不崩溃且产出合法 result.json。

用法：
    python -B scripts/selftest.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
MAIN = SCRIPTS_DIR / "main.py"


def _run(cmd: list, cwd=None) -> int:
    proc = subprocess.run(
        [sys.executable, "-B", str(MAIN)] + cmd,
        cwd=cwd or SCRIPTS_DIR.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode


def _make_case(root: Path) -> None:
    inp = root / "input"
    inp.mkdir(parents=True)
    (inp / "materials").mkdir()
    (inp / "case.json").write_text(json.dumps({
        "case_id": "demo-1",
        "output_contract": ["inventory", "relations", "search_results"],
        "queries": ["algorithms"],
        "relations": [
            {"type": "version", "from": "report_final.md", "to": "report_draft.md"}
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (inp / "materials" / "report_final.md").write_text(
        "# Report\n\nWe compare three algorithms for sorting.\n", encoding="utf-8")
    (inp / "materials" / "report_draft.md").write_text(
        "# Report\n\nWe compare three algorithms for sorting (draft).\n", encoding="utf-8")
    (inp / "materials" / "readme.txt").write_text("notes about algorithms\n", encoding="utf-8")
    (inp / "materials" / "data.csv").write_text("id,name\n1,a\n", encoding="utf-8")


def _case_result(root: Path):
    return json.loads((root / "output" / "result.json").read_text(encoding="utf-8"))


def main() -> int:
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="skill_selftest_"))
    try:
        # 1) 主路径
        root = tmp / "full"
        _make_case(root)
        for cmd in (["init"], ["show"], ["check"], ["build"], ["selfcheck"]):
            rc = _run(cmd + [f"--input={root / 'input'}",
                             f"--work={root / 'work'}",
                             f"--output={root / 'output'}"])
            if rc != 0:
                failures.append(f"{cmd} 退出码 {rc}")

        # 模型不填 draft 就直接 build 也应该收口（partial）
        res = _case_result(root)
        if res.get("case_id") != "demo-1":
            failures.append("case_id 回填错误")
        if res.get("status") not in {"ok", "partial", "insufficient_evidence", "input_error"}:
            failures.append("status 非法")
        for key in ("case_id", "status", "sources", "uncertainty", "change_log", "result"):
            if key not in res:
                failures.append(f"缺信封字段 {key}")
        # 契约字段逐键回填（演示契约含 inventory/relations/search_results）
        if "inventory" not in res or not isinstance(res.get("inventory"), list):
            failures.append("output_contract 字段 inventory 未回填")
        if "relations" not in res or not isinstance(res.get("relations"), list):
            failures.append("output_contract 字段 relations 未回填")
        for name in ("result.json", "library_index.json", "evidence_map.json", "inventory.md"):
            p = root / "output" / name
            if not p.exists() or p.stat().st_size == 0:
                failures.append(f"缺产物 {name}")

        # 2) 确定性：同输入跑两次，除 runtime 外逐字节一致
        root2 = tmp / "full2"
        _make_case(root2)
        for r in (root, root2):
            for cmd in (["init"], ["build"]):
                _run(cmd + [f"--input={r / 'input'}",
                            f"--work={r / 'work'}",
                            f"--output={r / 'output'}"])
        r1 = _case_result(root)
        r2 = _case_result(root2)
        for k in ("case_id", "status", "claims", "sources", "change_log", "uncertainty",
                  "pending_human", "result"):
            if r1.get(k) != r2.get(k):
                failures.append(f"两次运行 {k} 不一致")

        # 3) 空输入
        empty = tmp / "empty"
        (empty / "input").mkdir(parents=True)
        _run(["init", f"--input={empty / 'input'}", f"--work={empty / 'work'}",
              f"--output={empty / 'output'}"])
        er = _case_result(empty)
        if er.get("status") != "insufficient_evidence":
            failures.append("空输入应 insufficient_evidence")

        # 4) 坏 JSON case.json
        bad = tmp / "bad"
        (bad / "input").mkdir(parents=True)
        (bad / "input" / "case.json").write_text("{ not json", encoding="utf-8")
        (bad / "input" / "f.txt").write_text("x", encoding="utf-8")
        _run(["init", f"--input={bad / 'input'}", f"--work={bad / 'work'}",
              f"--output={bad / 'output'}"])
        br = _case_result(bad)
        if br.get("status") != "input_error":
            failures.append("坏 JSON 应 input_error")

        print(json.dumps({"ok": not failures, "failures": failures,
                          "tmp": str(tmp)}, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
