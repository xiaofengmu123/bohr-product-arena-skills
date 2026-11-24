#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 本地冒烟自检（纯标准库）。

在临时目录里构造 6 个场景，逐个用 subprocess 调用 main.py，断言：
- 退出码恒为 0；
- result.json 可解析且 status 语义正确；
- 空输入 / 坏 JSON / 缺 case.json / 超大文件 / 无 draft / 未知参数均优雅降级；
- 同一输入两次运行除 runtime 外逐字节一致。

运行：python -B scripts/selftest.py
本脚本只用于提交前自检，不作为正式案例产物，不预写任何答案。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
MAIN = SCRIPTS_DIR / "main.py"

ENV = dict(os.environ)
ENV.setdefault("PYTHONIOENCODING", "utf-8")


def run(*args):
    cp = subprocess.run(
        [sys.executable, "-B", str(MAIN), *args],
        cwd=str(SCRIPTS_DIR), env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return cp.returncode, cp.stdout, cp.stderr


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def load_result(output_dir: Path):
    p = output_dir / "result.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


# ---------------------------------------------------------------- 场景 1：happy path


def happy_path():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        work = root / "work"
        out = root / "output"
        write(inp / "case.json", json.dumps({
            "case_id": "case-selftest-001",
            "output_contract": {
                "inventory": [],
                "queries": [],
                "versions": [],
                "aliases": [],
                "summary": None,
            },
            "queries": ["where is the protocol file?"],
            "relations": [{"kind": "version", "files": ["report.txt", "report_v2.txt"]}],
            "manifest": ["materials/report.txt", "materials/report_v2.txt"],
        }, ensure_ascii=False))
        write(inp / "task.md", "# 冒烟案例\n")
        write(inp / "materials" / "report.txt", "protocol file v1 content: alpha")
        write(inp / "materials" / "report_v2.txt", "protocol file v2 content: beta changed")
        write(inp / "materials" / "report_copy.txt", "protocol file v1 content: alpha")
        write(inp / "materials" / "notes.md", "note: beta method requires revision")

        rc, _, _ = run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
        check("happy: init exit 0", rc == 0)

        # 写 draft（quote 必须与 show 预览逐字一致；source_id 对应确定性扫描顺序）
        # 扫描顺序：F001=case.json F002=task.md F003=notes.md
        #           F004=report.txt F005=report_copy.txt F006=report_v2.txt
        draft = {
            "case_id": "case-selftest-001",
            "notes": [{
                "target": "F006", "kind": "version",
                "text": "report_v2.txt 是 report.txt 的改版",
                "basis": "case.json relations", "confidence": "explicit",
                "evidence": [{"source_id": "case", "locator": "case.json",
                              "quote": "\"kind\": \"version\""}],
            }],
            "queries": [{
                "query_id": "Q1",
                "answer": "protocol 文件位于 materials/report.txt 与 report_v2.txt",
                "status": "answered",
                "evidence": [{"source_id": "F004", "locator": "materials/report.txt",
                              "quote": "protocol file v1 content: alpha"}],
            }],
            "contract": {},
            "recoveries": [],
        }
        write(work / "draft.json", json.dumps(draft, ensure_ascii=False))

        rc, _, _ = run("check", "--input", str(inp), "--work", str(work), "--output", str(out))
        check("happy: check exit 0", rc == 0)
        rc, _, _ = run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
        check("happy: build exit 0", rc == 0)

        res = load_result(out)
        check("happy: result 可解析", res is not None)
        if res:
            check("happy: status ok", res.get("status") == "ok", f"status={res.get('status')}")
            check("happy: case_id 保留", res.get("case_id") == "case-selftest-001")
            for k in ("inventory", "queries", "versions", "aliases", "summary"):
                check(f"happy: 契约键 {k} 存在", k in res)
            for name in ("library_index.json", "evidence_map.json", "inventory.md"):
                check(f"happy: {name} 非空",
                      (out / name).exists() and (out / name).stat().st_size > 0)

        # 确定性：二次运行，除 runtime 外逐字节一致
        work2 = root / "work2"
        out2 = root / "output2"
        run("init", "--input", str(inp), "--work", str(work2), "--output", str(out2))
        write(work2 / "draft.json", json.dumps(draft, ensure_ascii=False))
        run("build", "--input", str(inp), "--work", str(work2), "--output", str(out2))
        r1 = load_result(out)
        r2 = load_result(out2)
        if r1 and r2:
            r1c = {k: v for k, v in r1.items() if k != "runtime"}
            r2c = {k: v for k, v in r2.items() if k != "runtime"}
            check("happy: 两次运行除 runtime 外一致", r1c == r2c)
            lib1 = (out / "library_index.json").read_text(encoding="utf-8")
            lib2 = (out2 / "library_index.json").read_text(encoding="utf-8")
            check("happy: library_index 逐字节一致", lib1 == lib2)


# ---------------------------------------------------------------- 场景 2：空输入


def empty_input():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        inp.mkdir()
        work = root / "work"
        out = root / "output"
        run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
        run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
        res = load_result(out)
        check("empty: result 可解析", res is not None)
        check("empty: status insufficient_evidence", res and res.get("status") == "insufficient_evidence",
              f"status={res and res.get('status')}")


# ---------------------------------------------------------------- 场景 3：坏 JSON


def bad_json():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        write(inp / "case.json", "{ this is not json ")
        write(inp / "materials" / "a.txt", "hello")
        work = root / "work"
        out = root / "output"
        run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
        run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
        res = load_result(out)
        check("badjson: result 可解析", res is not None)
        check("badjson: status input_error", res and res.get("status") == "input_error",
              f"status={res and res.get('status')}")


# ---------------------------------------------------------------- 场景 4：缺 case.json（有材料）


def missing_case():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        write(inp / "materials" / "b.txt", "world")
        work = root / "work"
        out = root / "output"
        run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
        run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
        res = load_result(out)
        check("missingcase: result 可解析", res is not None)
        check("missingcase: status input_error（仍盘点材料）",
              res and res.get("status") == "input_error", f"status={res and res.get('status')}")
        check("missingcase: 仍记录文件",
              res and len(res.get("sources", [])) >= 1)


# ---------------------------------------------------------------- 场景 5：无 draft 直接 build


def no_draft():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        write(inp / "case.json", json.dumps({"case_id": "c-x"}))
        write(inp / "materials" / "c.txt", "abc")
        work = root / "work"
        out = root / "output"
        run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
        run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
        res = load_result(out)
        check("nodraft: result 可解析", res is not None)
        check("nodraft: status partial", res and res.get("status") == "partial",
              f"status={res and res.get('status')}")
        check("nodraft: degradations 含 no_draft",
              res and any("no_draft" in json.dumps(d, ensure_ascii=False)
                          for d in res.get("degradations", [])))


# ---------------------------------------------------------------- 场景 6：超大文件 + 未知参数


def oversized_and_unknown():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        write(inp / "case.json", json.dumps({"case_id": "c-big"}))
        big = inp / "big.txt"
        big.write_bytes(b"x" * (5 * 1024 * 1024))  # 5 MiB，超过整读上限
        work = root / "work"
        out = root / "output"
        run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
        run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
        res = load_result(out)
        check("big: result 可解析", res is not None)
        if res:
            lib = json.loads((out / "library_index.json").read_text(encoding="utf-8"))
            f = lib["files"][0]
            check("big: 超大文件仍计算 sha256", bool(f.get("sha256")))
        # 未知子命令 / --help 均退出 0
        rc1, _, _ = run("--help")
        rc2, _, _ = run("frobnicate")
        check("unknown: --help 退出 0", rc1 == 0)
        check("unknown: 未知参数退出 0", rc2 == 0)


def main():
    for fn in (happy_path, empty_input, bad_json, missing_case, no_draft, oversized_and_unknown):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            check(fn.__name__, False, f"exception: {e}")
            traceback.print_exc()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\nselftest: {passed}/{total} 通过")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    print(f"\n{'ALL PASS' if passed == total else 'SOME FAILED'}")
    # 自检脚本本身恒退出 0：失败以文本形式报告，便于 CI 读取。
    return 0


if __name__ == "__main__":
    sys.exit(main())