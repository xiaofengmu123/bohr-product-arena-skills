#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 本地冒烟与回归自检（纯标准库）。

在临时目录构造最小但"真实形状"的输入（同哈希副本、带版本标记、无标记冲突、
显式别名声明、检索问题），按主路径跑 init→check→build→selfcheck，再覆盖四个兜底场景，
并验证同输入两次 build 除 runtime 外逐字节一致。退出码 0 表示全部通过。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import main  # noqa: E402
import core  # noqa: E402

PASS = 0
FAIL = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}: {detail}")


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_demo_input(root: Path) -> None:
    inp = root / "input"
    (inp / "materials").mkdir(parents=True, exist_ok=True)
    case = {
        "case_id": "selftest-case-01",
        "title": "最小自检案例",
        "output_contract": {
            "file_manifest": [],
            "duplicates": [],
            "version_relations": [],
            "search_answers": [],
        },
        "queries": ["检索'复现'相关的材料"],
    }
    write(inp / "case.json", json.dumps(case, ensure_ascii=False, indent=2))
    write(inp / "task.md", "# 任务\n盘点 materials 目录，回答检索问题。\n")

    body = "这是一份关于实验复现的科研笔记。\n结论：固定随机种子可以保证结果可复现。\n"
    (inp / "materials" / "note.md").write_text(body, encoding="utf-8")
    (inp / "materials" / "note_copy.md").write_text(body, encoding="utf-8")          # 同哈希副本
    (inp / "materials" / "protocol_v1.txt").write_text("protocol version 1\n", encoding="utf-8")
    (inp / "materials" / "protocol_v2.txt").write_text("protocol version 2 changed\n", encoding="utf-8")
    (inp / "materials" / "data_a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (inp / "materials" / "data_b.csv").write_text("x,y\n3,4\n", encoding="utf-8")
    # 无版本标记、同 stem、不同内容 → 冲突候选（进 pending_human）。
    (inp / "materials" / "analysis.txt").write_text("analysis alpha\n", encoding="utf-8")
    (inp / "materials" / "analysis(1).txt").write_text("analysis beta\n", encoding="utf-8")
    write(inp / "materials" / "admin_notes.md",
          "行政备注：note_copy.md 是 note.md 的别名/副本。\n版本备注：protocol_v2 取代 protocol_v1。\n")
    # 一个无文本层二进制（模拟 PDF），只应做字节盘点，不虚构内容。
    (inp / "materials" / "paper.pdf").write_bytes(b"%PDF-1.4\n% fake for selftest\n")


def run_pipeline(input_dir: Path, work_dir: Path, output_dir: Path) -> dict:
    base = ["--input", str(input_dir), "--work", str(work_dir), "--output", str(output_dir)]
    main.cli(["init"] + base)
    main.cli(["show"] + base)
    # 写一个最小 draft：回答查询 + 一条元数据 note。
    lib = core.load_json(work_dir / "library.json")
    qid = lib["queries"][0]["query_id"] if lib["queries"] else "Q1"
    note_id = next((f["id"] for f in lib["files"] if f["rel_path"] == "materials/note.md"), "F001")
    draft = {
        "case_id": lib["case_id"],
        "notes": [
            {"target": "case", "kind": "metadata",
             "text": "行政备注确认 note_copy.md 为 note.md 别名。",
             "basis": "显式备注", "confidence": "explicit",
             "evidence": [{"source_id": "case", "locator": "case.json",
                           "quote": "\u590d\u73b0"}]},
        ],
        "queries": [
            {"query_id": qid, "answer": "note.md / note_copy.md 含'复现'相关结论。",
             "status": "answered",
             "evidence": [{"source_id": note_id, "locator": "materials/note.md",
                           "quote": "\u53ef\u590d\u73b0"}]},
        ],
        "contract_values": {},
    }
    core.to_json(draft, work_dir / "draft.json")
    main.cli(["check"] + base)
    main.cli(["build"] + base)
    main.cli(["selfcheck"] + base)
    return core.load_json(output_dir / "result.json")


def main_test() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="libselftest_"))
    try:
        build_demo_input(tmp)
        inp, work, out = tmp / "input", tmp / "work", tmp / "output"

        # 1) 主路径
        result = run_pipeline(inp, work, out)
        report("main pipeline produces result.json", result.get("case_id") == "selftest-case-01")
        report("contract keys backfilled", all(k in result for k in
              ("file_manifest", "duplicates", "version_relations", "search_answers")))
        report("envelope complete", all(k in result for k in
              ("status", "sources", "uncertainty", "change_log", "limitations", "pending_human")))
        for name in ("library_index.json", "evidence_map.json", "inventory.md", "selfcheck_report.json"):
            report(f"artifact {name}", (out / name).exists() and (out / name).stat().st_size > 0)
        emap = core.load_json(out / "evidence_map.json")
        report("evidence_map no unverified quotes", emap.get("unverified") == [])

        # 2) 确定性：再 build 一次，除 runtime 外一致。
        out2 = tmp / "output2"
        result2 = run_pipeline(inp, tmp / "work2", out2)
        r1 = {k: v for k, v in result.items() if k != "runtime"}
        r2 = {k: v for k, v in result2.items() if k != "runtime"}
        report("two runs deterministic (excl runtime)", r1 == r2)
        lib1 = (out / "library_index.json").read_bytes()
        lib2 = (out2 / "library_index.json").read_bytes()
        report("library_index byte-identical", lib1 == lib2)

        # 3) 兜底场景
        empty = tmp / "empty_in"
        empty.mkdir(exist_ok=True)
        eout = tmp / "empty_out"
        main.cli(["init", "--input", str(empty), "--work", str(tmp / "ew"),
                  "--output", str(eout)])
        r = core.load_json(eout / "result.json")
        report("empty input -> insufficient_evidence", r.get("status") == "insufficient_evidence")

        bad = tmp / "bad_in"
        bad.mkdir(exist_ok=True)
        write(bad / "case.json", "{not valid json")
        write(bad / "task.md", "x\n")
        bout = tmp / "bad_out"
        main.cli(["init", "--input", str(bad), "--work", str(tmp / "bw"),
                  "--output", str(bout)])
        r = core.load_json(bout / "result.json")
        report("bad json -> input_error", r.get("status") == "input_error")

        nodraft = tmp / "nodraft_in"
        nodraft.mkdir(exist_ok=True)
        write(nodraft / "case.json", json.dumps({"case_id": "x", "output_contract": {}}))
        write(nodraft / "task.md", "x\n")
        (nodraft / "f.txt").write_text("hello\n", encoding="utf-8")
        nout = tmp / "nodraft_out"
        main.cli(["init", "--input", str(nodraft), "--work", str(tmp / "nw"),
                  "--output", str(nout)])
        main.cli(["build", "--input", str(nodraft), "--work", str(tmp / "nw"),
                  "--output", str(nout)])
        r = core.load_json(nout / "result.json")
        report("no draft -> partial", r.get("status") == "partial")

        # 4) 解析器可达（--help 会触发 SystemExit，故只验证 parse_args 不抛异常）
        try:
            main.build_parser().parse_args(["init"])
            report("parser reachable", True)
        except Exception as exc:  # noqa: BLE001
            report("parser reachable", False, str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main_test()
    print(f"\nselftest: {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
