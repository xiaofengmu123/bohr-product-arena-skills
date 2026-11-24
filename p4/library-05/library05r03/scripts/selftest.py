#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 本地自检脚本（纯标准库，不依赖真实案例）。

构造临时最小夹具，验证：
  1. init/show/check/build/selfcheck 五命令可运行且退出码 0；
  2. build 产出 result.json / library_index.json / evidence_map.json / inventory.md；
  3. 同输入两次 build 除 result.json.runtime 外逐字节一致（确定性）；
  4. 四个降级场景：空目录 / 坏 JSON / 缺 case.json（有材料）/ 无 draft，均不崩溃。

用法：
    python -B scripts/selftest.py
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


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _wb(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_fixture(root: Path) -> Path:
    inp = root / "input"
    (inp / "materials").mkdir(parents=True, exist_ok=True)
    _w(inp / "case.json", json.dumps({
        "case_id": "fixture-001",
        "output_contract": ["inventory", "relations", "search_results"],
        "aliases": [{"source": "F001", "target": "F002", "relation": "duplicate"}],
        "versions": [{"note": "paper-v2 is the administrative revision of paper-v1"}],
        "queries": ["where is the method chapter"],
    }, ensure_ascii=False))
    _w(inp / "task.md", "# 文档库修复（自检夹具，非正式案例）\n")
    _w(inp / "materials" / "notes.md",
       "alpha.txt and beta.txt are the same file (duplicate).\n"
       "paper-v2 is the administrative revision of paper-v1 (version note).\n")
    _w(inp / "materials" / "alpha.txt", "hello world\nmethod: run experiment now\n")
    _w(inp / "materials" / "beta.txt", "hello world\nmethod: run experiment now\n")
    _w(inp / "materials" / "paper-v1.txt", "draft one\n")
    _w(inp / "materials" / "paper-v2.txt", "draft two revised\n")
    _wb(inp / "materials" / "scan.pdf", b"%PDF-1.4\n\x00\x01binary-no-text-layer\n")
    return inp


def _run(cmd, inp, work, out):
    code = main.main([cmd, "--input", str(inp), "--work", str(work), "--output", str(out)])
    return code


def _load(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _norm_result(path: Path):
    r = _load(path)
    if isinstance(r, dict):
        r.pop("runtime", None)
    return r


def test_main_path(tmp: Path) -> tuple:
    inp = make_fixture(tmp)
    work = tmp / "work"
    out = tmp / "output"
    codes = []
    codes.append(("init", _run("init", inp, work, out)))
    codes.append(("show", _run("show", inp, work, out)))
    codes.append(("check", _run("check", inp, work, out)))
    codes.append(("build", _run("build", inp, work, out)))
    codes.append(("selfcheck", _run("selfcheck", inp, work, out)))
    result = _load(out / "result.json")
    ok = result is not None and result.get("case_id") == "fixture-001"
    ok = ok and all((out / n).exists() and (out / n).stat().st_size > 0
                    for n in ("result.json", "library_index.json", "evidence_map.json", "inventory.md"))
    for name, code in codes:
        if code != 0:
            ok = False
    return ok, {"codes": codes, "status": (result or {}).get("status")}


def test_determinism(tmp: Path) -> tuple:
    inp = make_fixture(tmp / "det")
    work1 = tmp / "w1"
    work2 = tmp / "w2"
    out1 = tmp / "o1"
    out2 = tmp / "o2"
    _run("init", inp, work1, out1)
    _run("init", inp, work2, out2)
    _run("build", inp, work1, out1)
    _run("build", inp, work2, out2)

    names = ["library_index.json", "evidence_map.json", "inventory.md"]
    ok = True
    for n in names:
        b1 = (out1 / n).read_bytes()
        b2 = (out2 / n).read_bytes()
        if b1 != b2:
            ok = False
    r1 = _norm_result(out1 / "result.json")
    r2 = _norm_result(out2 / "result.json")
    if json.dumps(r1, ensure_ascii=False, sort_keys=True) != json.dumps(r2, ensure_ascii=False, sort_keys=True):
        ok = False
    return ok, {"byte_identical": ok}


def test_degration_empty(tmp: Path) -> tuple:
    inp = tmp / "empty"
    inp.mkdir(parents=True, exist_ok=True)
    out = tmp / "out_empty"
    code = _run("build", inp, tmp / "we", out)
    r = _load(out / "result.json")
    return (code == 0 and isinstance(r, dict) and r.get("status") == "insufficient_evidence"), r


def test_degration_badjson(tmp: Path) -> tuple:
    inp = tmp / "badjson"
    inp.mkdir(parents=True, exist_ok=True)
    _w(inp / "case.json", "not valid json {{")
    _w(inp / "materials" / "a.txt", "data\n")
    out = tmp / "out_badjson"
    code = _run("build", inp, tmp / "wb", out)
    r = _load(out / "result.json")
    return (code == 0 and isinstance(r, dict) and r.get("status") == "input_error"), r


def test_degration_nocase(tmp: Path) -> tuple:
    inp = tmp / "nocase"
    (inp / "materials").mkdir(parents=True, exist_ok=True)
    _w(inp / "materials" / "a.txt", "data\n")
    out = tmp / "out_nocase"
    code = _run("build", inp, tmp / "wn", out)
    r = _load(out / "result.json")
    return (code == 0 and isinstance(r, dict) and r.get("status") == "input_error"), r


def test_degration_nodraft(tmp: Path) -> tuple:
    inp = make_fixture(tmp / "nodraft")
    work = tmp / "wnodraft"
    out = tmp / "onodraft"
    _run("init", inp, work, out)
    # 删除 draft，直接 build
    draft = work / "draft.json"
    if draft.exists():
        draft.unlink()
    code = _run("build", inp, work, out)
    r = _load(out / "result.json")
    has_degrade = any(d.get("kind") == "no_draft" for d in (r or {}).get("degradations", []))
    return (code == 0 and isinstance(r, dict) and r.get("status") == "partial" and has_degrade), r


def main_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="library05_selftest_"))
    results = []
    try:
        for name, fn in (
            ("main_path", test_main_path),
            ("determinism", test_determinism),
            ("empty_dir", test_degration_empty),
            ("bad_json", test_degration_badjson),
            ("no_case_json", test_degration_nocase),
            ("no_draft", test_degration_nodraft),
        ):
            try:
                ok, extra = fn(tmp / name)
                results.append((name, ok))
                print(f"[{'PASS' if ok else 'FAIL'}] {name}")
                if not ok:
                    print("   detail:", json.dumps(extra, ensure_ascii=False)[:800])
            except Exception as e:  # noqa: BLE001
                results.append((name, False))
                print(f"[FAIL] {name} raised: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok in results if not ok]
    print("-" * 60)
    print(f"selftest: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("failed:", ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main_test())