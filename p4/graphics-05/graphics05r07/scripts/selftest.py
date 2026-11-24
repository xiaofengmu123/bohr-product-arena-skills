#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the graphical-abstract-robust skill (stdlib only).

Runs the orchestrator against four synthetic fixture families and verifies:
  * normal input      -> result.json exists, case_id present, SVG parses
  * empty input       -> honest failed skeleton, exit code 0, never raises
  * malformed JSON    -> degraded partial result, case_id derived from task.md
  * oversized file    -> skipped and recorded, pipeline still completes
  * missing args      -> argparse defaults point to /input, /app/output, /app/work

Each case must return without raising. Any failure sets a non-zero exit code so a
reviewer or the harness can gate on it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import main  # noqa: E402
import svg_builder  # noqa: E402


def _write(path: Path, text: str, mode="w", encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding=encoding) as fh:
        fh.write(text)


def _result(output_dir: Path):
    p = output_dir / "result.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _svg_parses(output_dir: Path):
    files = sorted(output_dir.glob("fig_*.svg"))
    if not files:
        return False, "no SVG"
    try:
        ET.parse(files[0])
        return True, files[0].name
    except ET.ParseError as exc:
        return False, str(exc)


def _run_case(name, input_dir, work_dir, output_dir):
    code = main.run(["--input", str(input_dir), "--output", str(output_dir),
                     "--work", str(work_dir)])
    result = _result(output_dir)
    ok = code == 0 and result is not None and result.get("case_id") and result.get("status")
    return {"name": name, "ok": bool(ok), "exit": code, "status": result.get("status") if result else None,
            "case_id": result.get("case_id") if result else None,
            "errors": result.get("errors") if result else None,
            "degradations": result.get("degradations") if result else None}


def main_selftest():
    base = Path(tempfile.mkdtemp(prefix="ga_selftest_"))
    results = []

    # 1) normal input with an explicit output_contract
    n_in, n_out, n_work = base / "normal_in", base / "normal_out", base / "normal_work"
    _write(n_in / "case.json", json.dumps({
        "case_id": "demo-normal-001",
        "title": "Demo study",
        "output_contract": {
            "graphical_abstract": None, "editable_source": None, "exports": [],
            "entities": [], "relations": [], "data_mapping": [], "legend": [],
        },
        "content": "研究对象：样本A、样本B。A→B 表示激活。结果为 42 mg，72 %。",
    }, ensure_ascii=False))
    _write(n_in / "task.md", "# 图形摘要\nchallenge_id: `graphical-abstract-demo`\n")
    _write(n_in / "measurements.csv", "group,value\ncontrol,10\ntreated,45\n")
    results.append(_run_case("normal", n_in, n_work, n_out))
    ok_svg, svg_info = _svg_parses(n_out)
    results[-1]["svg_ok"] = ok_svg
    results[-1]["svg"] = svg_info
    results[-1]["ok"] = bool(results[-1]["ok"] and ok_svg)

    # 2) empty input directory
    e_in, e_out, e_work = base / "empty_in", base / "empty_out", base / "empty_work"
    e_in.mkdir(parents=True, exist_ok=True)
    r = _run_case("empty", e_in, e_work, e_out)
    r["ok"] = bool(r["exit"] == 0 and r["status"] == "failed")
    results.append(r)

    # 3) malformed JSON, task.md still provides case_id
    b_in, b_out, b_work = base / "bad_in", base / "bad_out", base / "bad_work"
    _write(b_in / "case.json", '{"case_id": "x", "content": "unterminated\n  this is not json')
    _write(b_in / "task.md", "# 图形摘要\nchallenge_id: `graphical-abstract-badjson-1`\n")
    r = _run_case("bad_json", b_in, b_work, b_out)
    r["ok"] = bool(r["exit"] == 0 and r["status"] == "partial"
                   and r["case_id"] == "graphical-abstract-badjson-1")
    results.append(r)

    # 4) oversized file is skipped, normal material still processed
    o_in, o_out, o_work = base / "oversize_in", base / "oversize_out", base / "oversize_work"
    _write(o_in / "case.json", json.dumps({"case_id": "demo-oversize-001",
                                           "content": "研究对象：样本C。"}, ensure_ascii=False))
    _write(o_in / "task.md", "# 图形摘要\nchallenge_id: `graphical-abstract-oversize`\n")
    big = o_in / "huge.txt"
    big.write_bytes(b"x" * (main.MAX_TEXT_FILE_BYTES + 1024))
    r = _run_case("oversized", o_in, o_work, o_out)
    r["ok"] = bool(r["exit"] == 0 and r["status"] in ("partial", "completed")
                   and any("huge.txt" in str(d.get("item", "")) for d in (r["degradations"] or [])))
    results.append(r)

    # 5) missing args: argparse defaults only (no filesystem dependency)
    parser = main.build_parser()
    defaults = {a.dest: a.default for a in parser._actions}
    default_ok = (defaults.get("input") == "/input" and defaults.get("output") == "/app/output"
                  and defaults.get("work") == "/app/work")
    results.append({"name": "defaults", "ok": default_ok, "exit": None,
                    "status": "check", "case_id": None, "errors": None,
                    "degradations": None})

    # 6) --version and --help exit cleanly (--help raises SystemExit(0) via argparse)
    ver_code = main.run(["--version"])
    help_code = 0
    try:
        main.run(["--help"])
    except SystemExit as exc:
        help_code = exc.code if exc.code is not None else 0
    results.append({"name": "cli", "ok": ver_code == 0 and help_code == 0,
                    "exit": (ver_code, help_code), "status": "check", "case_id": None,
                    "errors": None, "degradations": None})

    # summary
    print("=" * 60)
    print("graphical-abstract-robust selftest")
    print("=" * 60)
    failed = 0
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['name']:<10} exit={r['exit']} status={r['status']} "
              f"case_id={r['case_id']} svg={r.get('svg', '')} svg_ok={r.get('svg_ok', '-')}")
        if not r["ok"]:
            failed += 1
            print("      errors:", r["errors"])
            print("      degradations:", r["degradations"])
    print("-" * 60)
    print(f"{len(results) - failed}/{len(results)} passed")
    print(f"fixtures kept at: {base}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_selftest())
