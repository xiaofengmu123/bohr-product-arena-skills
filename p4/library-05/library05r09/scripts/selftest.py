#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 本地冒烟自检：用程序临时生成的夹具跑完整主路径与降级场景。

注意：夹具在运行时写入系统临时目录，不在包内提交任何"预制答案/正式测试产物"。
退出码 0 表示全部通过；失败打印具体断言并返回非零（仅作者本地使用，不影响运行时）。
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import main  # noqa: E402

CASES = 0
FAILS: list = []


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main.main(argv)
    return code, buf.getvalue()


def check(cond, label):
    global CASES
    CASES += 1
    if not cond:
        FAILS.append(label)
        print(f"  FAIL: {label}")


def make_fixture(root: Path, kind: str) -> Path:
    inp = root / kind / "input"
    wrk = root / kind / "work"
    out = root / kind / "output"
    inp.mkdir(parents=True, exist_ok=True)
    wrk.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    if kind == "full":
        (inp / "case.json").write_text(json.dumps({
            "case_id": "demo-001",
            "output_contract": {
                "inventory": "file list",
                "duplicates": "identical copies",
                "versions": "candidate versions",
                "queries": "retrieval answers",
            },
            "queries": ["版本 v2", "metadata"],
        }, ensure_ascii=False), encoding="utf-8")
        (inp / "task.md").write_text(
            "# 案例\n请整理文件库，识别重复与版本关系，并回答检索问题。\n", encoding="utf-8")
        mat = inp / "materials"
        mat.mkdir(exist_ok=True)
        (mat / "paper.md").write_text("intro\nmethod\nconclusion\n", encoding="utf-8")
        (mat / "paper_v1.md").write_text("intro\nmethod v1\nconclusion\n", encoding="utf-8")
        (mat / "paper_v2.md").write_text("intro\nmethod v2\nconclusion\n", encoding="utf-8")
        (mat / "metadata.json").write_text(json.dumps(
            {"title": "demo paper", "version": "v2"}, ensure_ascii=False), encoding="utf-8")
        # 一个重复副本（与 paper.md 相同内容）
        (mat / "paper copy.md").write_text("intro\nmethod\nconclusion\n", encoding="utf-8")
    elif kind == "empty":
        pass
    elif kind == "badjson":
        (inp / "case.json").write_text("{not valid json", encoding="utf-8")
        (inp / "task.md").write_text("# task\n", encoding="utf-8")
    return inp, wrk, out


def write_draft(work: Path):
    """写一个 source_id / locator / quote 完全一致的语义 draft，用于验证 check 通过。"""
    draft = {
        "case_id": "demo-001",
        "notes": [
            {"target": "F006", "kind": "version", "text": "行政版本备注：paper_v2 为当前版本",
             "basis": "case.json administrative note", "confidence": "explicit",
             "evidence": [{"source_id": "F006", "locator": "materials/paper_v2.md",
                           "quote": "method v2"}]}
        ],
        "relations": [
            {"type": "version", "members": ["F004", "F005", "F006"],
             "canonical": "F006", "reason": "paper_v2 为当前版本"}
        ],
        "queries": [
            {"query_id": "Q1", "answer": "materials/paper_v2.md",
             "status": "answered",
             "evidence": [{"source_id": "F006", "locator": "materials/paper_v2.md",
                           "quote": "method v2"}]}
        ],
    }
    (work / "draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")


def full_flow(root: Path, kind: str):
    inp, wrk, out = make_fixture(root, kind)
    args = ["--input", str(inp), "--work", str(wrk), "--output", str(out)]
    run(["init"] + args)
    run(["show"] + args)
    if kind == "full":
        write_draft(wrk)
    run(["check"] + args)
    run(["build"] + args)
    run(["selfcheck"] + args)
    return inp, wrk, out


def check_report_ok(work: Path) -> bool:
    try:
        rep = json.loads((work / "check_report.json").read_text(encoding="utf-8"))
        return bool(rep.get("ok"))
    except Exception:
        return False


def main_test() -> int:
    global CASES, FAILS
    tmp = Path(tempfile.mkdtemp(prefix="library-skill-selftest-"))
    try:
        # 1) 完整主路径
        inp, wrk, out = full_flow(tmp, "full")
        check(check_report_ok(wrk), "full: check 通过（source/quote 逐字命中）")
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check(res.get("case_id") == "demo-001", "full: case_id 原样带回")
        check(res.get("status") in ("ok", "partial"), f"full: status 合法 ({res.get('status')})")
        check(all(k in res for k in ("sources", "uncertainty", "change_log")),
              "full: 信封字段齐备")
        check(res.get("inventory") is not None, "full: 契约键 inventory 回填")
        for name in ("library_index.json", "evidence_map.json", "inventory.md"):
            check((out / name).exists() and (out / name).stat().st_size > 0,
                  f"full: 产物 {name} 非空")

        # 2) 确定性：同输入重跑 build，忽略 runtime 后逐字节一致
        out2 = tmp / "full" / "output2"
        out2.mkdir()
        run(["build", "--input", str(inp), "--work", str(wrk), "--output", str(out2)])
        r1 = json.loads((out / "result.json").read_text(encoding="utf-8"))
        r2 = json.loads((out2 / "result.json").read_text(encoding="utf-8"))
        r1.pop("runtime", None)
        r2.pop("runtime", None)
        check(json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True),
              "确定性: 两次 build 除 runtime 外一致")

        # 3) 空输入目录
        _i, _w, o3 = full_flow(tmp, "empty")
        r3 = json.loads((o3 / "result.json").read_text(encoding="utf-8"))
        check(r3.get("status") in ("insufficient_evidence", "input_error"),
              f"空输入: 诚实降级 ({r3.get('status')})")

        # 4) 坏 JSON case.json
        _i, _w, o4 = full_flow(tmp, "badjson")
        r4 = json.loads((o4 / "result.json").read_text(encoding="utf-8"))
        check(r4.get("status") == "input_error", f"坏 JSON: input_error ({r4.get('status')})")
        check(any(e.get("code") for e in r4.get("errors", [])), "坏 JSON: errors 记录")

        # 5) 无 draft 直接 build（确定性基线）
        _i, w5, o5 = make_fixture(tmp, "full")
        run(["init", "--input", str(inp), "--work", str(w5), "--output", str(o5)])
        run(["build", "--input", str(inp), "--work", str(w5), "--output", str(o5)])
        r5 = json.loads((o5 / "result.json").read_text(encoding="utf-8"))
        check(r5.get("status") == "partial", f"无 draft: partial ({r5.get('status')})")
        check(any(d.get("code") == "no_draft" for d in r5.get("degradations", [])),
              "无 draft: degradations 标 no_draft")

        # 6) --help 退出码 0
        code = 0
        try:
            run(["--help"])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
        check(code == 0, "--help 退出码 0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nselftest: {CASES - len(FAILS)}/{CASES} 通过")
    if FAILS:
        print("失败的检查：")
        for f in FAILS:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_test())