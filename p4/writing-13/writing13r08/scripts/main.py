#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 基金申请书写作 Skill 入口（纯标准库，可重复运行）。

用法：
  python scripts/main.py run        [--input /input --output /app/output --work /app/work]
  python scripts/main.py scaffold   [--input /input --output /app/output --work /app/work]
  python scripts/main.py snapshot   [--input /input --work /app/work]
  python scripts/main.py verify     [--output /app/output]
  python scripts/main.py selftest

默认 --input /input --output /app/output --work /app/work；
Windows 本地调试时自动回退到 input/、work/、output/ 相对目录。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _resolve(default_path: str, fallback_rel: str) -> str:
    """优先用指定绝对路径；不存在时回退到工作目录下的相对目录。"""
    if os.path.isdir(default_path):
        return default_path
    cwd = os.getcwd()
    alt = os.path.join(cwd, fallback_rel)
    return alt


def _ensure_dirs(out: str, work: str) -> None:
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _find_case_json(inp: str):
    """优先 case.json，其次任意 *_case.json / *case*.json。"""
    direct = os.path.join(inp, "case.json")
    if os.path.isfile(direct):
        return direct
    if os.path.isdir(inp):
        for name in sorted(os.listdir(inp)):
            if name.endswith(".json") and "case" in name.lower():
                return os.path.join(inp, name)
    return None


def _append_change(result, action, reason, impact):
    result.setdefault("change_log", []).append(
        {"ts": C.now_iso(), "action": action, "reason": reason, "impact": impact}
    )


def cmd_run(a) -> int:
    inp = a.input if os.path.isdir(a.input) else _resolve(a.input, "input")
    out = a.output if os.path.isdir(a.output) else _resolve(a.output, "output")
    work = a.work if os.path.isdir(a.work) else _resolve(a.work, "work")
    _ensure_dirs(out, work)

    # 1) 来源快照
    snap = C.snapshot_sources(inp)
    _write_json(os.path.join(work, "source_manifest.json"), snap)

    # 2) 读取 case.json
    case_path = a.case or _find_case_json(inp)
    case_id, contract_hint, case_errs = C.load_case_meta(case_path) if case_path else (None, {}, ["case.json not found"])

    # 3) 读取 task.md（容错）
    task_path = os.path.join(inp, "task.md")
    task_text, task_errs = C.read_text_safe(task_path) if os.path.isfile(task_path) else (None, ["task.md missing"])

    # 4) 骨架（若已有 result.json 则增量更新，不推倒重写）
    result_path = os.path.join(out, "result.json")
    if os.path.isfile(result_path):
        existing, _ = C.read_json_safe(result_path)
        result = existing if isinstance(existing, dict) else {}
    else:
        result = {}
    if not result:
        if not case_id:
            case_id = os.path.basename(os.path.normpath(inp)) or "unknown_case"
        result = C.build_skeleton(case_id, contract_hint)
        result["status"] = "in_progress"
    else:
        # 保留已有字段，补齐契约键
        for k, v in contract_hint.items():
            result.setdefault(k, C._default_for_hint(v))
        result.setdefault("case_id", case_id)

    # 5) 事实台账合并
    ledger, ledger_errs = C.load_fact_ledger(work)
    if ledger:
        result["fact_source_map"] = ledger

    # 6) 来源清单并入
    sources = []
    for rel, meta in snap["files"].items():
        if meta.get("sha256"):
            sources.append({"path": rel, "sha256": meta["sha256"],
                            "size": meta["size"], "kind": _kind(rel)})
    result["sources"] = sources

    # 7) 主产物发现 + 四闸门检查
    draft_text = None
    for name in ("proposal_sections.md", "grant_sections.md", "draft.md",
                 "manuscript.md"):
        p = os.path.join(out, name)
        if os.path.isfile(p):
            result.setdefault("artifact", {})["path"] = p
            draft_text, _ = C.read_text_safe(p)
            break
    if draft_text:
        corr_path = os.path.join(work, "correspondence.json")
        corr, _ = C.read_json_safe(corr_path)
        gate = C.four_gate_report(draft_text, corr if isinstance(corr, dict) else None)
        result["quality_gates"] = gate

    # 8) 异常收集（每次运行重新计算，避免重复运行累积旧错误）
    result["errors"] = case_errs + task_errs + ledger_errs + snap["errors"]

    # 9) 校验 + 写盘
    issues = C.validate_result(result)
    result["selfcheck_issues"] = issues
    _append_change(result, "run", "main.py run 增量组装结果", "issues=%d" % len(issues))
    _write_json(result_path, result)

    summary = {
        "ok": True,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "sources": len(sources),
        "fact_claims": len(ledger),
        "selfcheck_issues": len(issues),
        "result_path": result_path,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _kind(rel: str) -> str:
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    table = {"pdf": "pdf", "txt": "text", "md": "markdown",
             "json": "json", "csv": "csv", "tsv": "tsv",
             "xlsx": "spreadsheet", "docx": "office", "pptx": "office"}
    return table.get(ext, "other")


def cmd_scaffold(a) -> int:
    """只落盘骨架 + 快照，供前三分钟先行保底。"""
    return cmd_run(a)


def cmd_snapshot(a) -> int:
    inp = a.input if os.path.isdir(a.input) else _resolve(a.input, "input")
    work = a.work if os.path.isdir(a.work) else _resolve(a.work, "work")
    os.makedirs(work, exist_ok=True)
    snap = C.snapshot_sources(inp)
    _write_json(os.path.join(work, "source_manifest.json"), snap)
    print(json.dumps({"ok": True, "files": len(snap["files"]),
                      "errors": len(snap["errors"])}, ensure_ascii=False))
    return 0


def cmd_verify(a) -> int:
    out = a.output if os.path.isdir(a.output) else _resolve(a.output, "output")
    result_path = os.path.join(out, "result.json")
    if not os.path.isfile(result_path):
        print("NO_RESULT: %s" % result_path)
        return 1
    result, errs = C.read_json_safe(result_path)
    if result is None:
        print("BAD_JSON: %s" % errs)
        return 1
    issues = C.validate_result(result)
    for i in issues:
        print("ISSUE:", i)
    print("STATUS:", result.get("status"), "ISSUES:", len(issues))
    return 1 if issues else 0


def cmd_selftest(a) -> int:
    p = os.path.join(HERE, "selftest.py")
    if not os.path.isfile(p):
        print("selftest.py missing")
        return 1
    r = subprocess.run([sys.executable, "-B", p], cwd=HERE)
    return r.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="main.py")
    ap.add_argument("cmd", nargs="?", default="run",
                    choices=["run", "scaffold", "snapshot", "verify", "selftest"])
    ap.add_argument("--input", default=C.DEFAULT_INPUT)
    ap.add_argument("--output", default=C.DEFAULT_OUTPUT)
    ap.add_argument("--work", default=C.DEFAULT_WORK)
    ap.add_argument("--case", default=None)
    a = ap.parse_args(argv)
    return {
        "run": cmd_run,
        "scaffold": cmd_scaffold,
        "snapshot": cmd_snapshot,
        "verify": cmd_verify,
        "selftest": cmd_selftest,
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
