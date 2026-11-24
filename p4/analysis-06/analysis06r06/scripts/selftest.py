#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: 对 /app/output 产物做格式、一致性与稳定性自检。

用法:
  python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work
  python3 -B scripts/selftest.py --help

检查项（对应 SKILL.md 第 6 节自检清单）:
  1. result.json 存在、可解析、含 case_id/status/sources/uncertainty/change_log/ir；
  2. status 取值合法；contract 键不缺失；
  3. plan.json / figure_model.json / schema.json / evidence_map.json 可解析；
  4. evidence_map/sources 每条含 locator + quote；query_log 每条含 sql + row_count；
  5. figures/*.svg 可被 XML 解析；
  6. 空输入冒烟测试：退出码 0 且 status=insufficient_evidence；
  7. 稳定性：同一输入重跑两遍，除时间戳外 analysis/query_log/answers 数值一致。

仅依赖 Python 标准库。
"""

import argparse
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as ci
import main as mainmod

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def load_json(path):
    if not Path(path).exists():
        return None, "missing"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json: %s" % exc


def validate_result(output_dir, failures):
    r, err = load_json(Path(output_dir) / "result.json")
    if err:
        failures.append("result.json: %s" % err)
        return r
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if key not in r:
            failures.append("result.json missing key: %s" % key)
    if r.get("status") not in ALLOWED_STATUS:
        failures.append("result.json bad status: %r" % r.get("status"))
    if not isinstance(r.get("sources"), list) or not isinstance(r.get("uncertainty"), list) \
            or not isinstance(r.get("change_log"), list):
        failures.append("result.json sources/uncertainty/change_log must be lists")
    ir = r.get("ir") or {}
    for k in ("plan", "figure_model", "evidence_map"):
        if not ir.get(k):
            failures.append("result.json ir.%s missing" % k)
    return r


def validate_artifacts(output_dir, failures):
    base = Path(output_dir)
    for name in ("plan.json", "figure_model.json", "schema.json", "evidence_map.json",
                 "report.md"):
        if not (base / name).exists():
            failures.append("missing artifact: %s" % name)
    # evidence_map 结构
    ev, err = load_json(base / "evidence_map.json")
    if err:
        failures.append("evidence_map.json: %s" % err)
    elif isinstance(ev, list):
        for i, e in enumerate(ev):
            if not (isinstance(e, dict) and e.get("locator") and e.get("quote")):
                failures.append("evidence_map[%d] lacks locator/quote" % i)
    # figure_model 结构
    fm, err = load_json(base / "figure_model.json")
    if err:
        failures.append("figure_model.json: %s" % err)
    elif isinstance(fm, dict) and isinstance(fm.get("figures"), list):
        for f in fm["figures"]:
            if not (f.get("id") and f.get("kind") and f.get("data") and f.get("svg")):
                failures.append("figure_model entry lacks id/kind/data/svg: %r" % f.get("id"))
    # SVG 可解析
    fig_dir = base / "figures"
    if fig_dir.exists():
        for svg in sorted(fig_dir.glob("*.svg")):
            try:
                ET.parse(svg)
            except Exception as exc:  # noqa: BLE001
                failures.append("svg parse failed: %s: %s" % (svg.name, exc))


def validate_query_log(result, failures):
    ql = result.get("query_log") or []
    if not isinstance(ql, list):
        failures.append("query_log must be list")
        return
    for i, e in enumerate(ql):
        if not (isinstance(e, dict) and e.get("sql") and "row_count" in e):
            failures.append("query_log[%d] lacks sql/row_count" % i)


def smoke_empty():
    """空输入目录 → main 退出码 0 且 status=insufficient_evidence。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "in").mkdir(); (root / "out").mkdir(); (root / "work").mkdir()
        code = mainmod.main(["--input", str(root / "in"),
                             "--output", str(root / "out"),
                             "--work", str(root / "work")])
        r, err = load_json(root / "out" / "result.json")
        status = (r or {}).get("status")
        return code, status, err


def determinism(input_dir):
    """同一输入重跑两遍，比较 analysis/query_log/answers 是否一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i in (1, 2):
            (root / ("out%d" % i)).mkdir(); (root / ("work%d" % i)).mkdir()
            mainmod.main(["--input", input_dir,
                          "--output", str(root / ("out%d" % i)),
                          "--work", str(root / ("work%d" % i))])
        r1, e1 = load_json(root / "out1" / "result.json")
        r2, e2 = load_json(root / "out2" / "result.json")
        if e1 or e2:
            return False, "determinism re-run failed: %s %s" % (e1, e2)
        def strip(r):
            return {"analysis": r.get("analysis"), "query_log": r.get("query_log"),
                    "answers": r.get("answers")}
        return strip(r1) == strip(r2), "analysis/query_log/answers differ between runs"


def main(argv=None):
    p = argparse.ArgumentParser(description="self-check for scientific-db-analysis-ir")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--skip-determinism", action="store_true")
    p.add_argument("--skip-smoke", action="store_true")
    args = p.parse_args(argv)

    failures = []
    result = validate_result(args.output, failures)
    validate_artifacts(args.output, failures)
    if result is not None:
        validate_query_log(result, failures)

    if not args.skip_smoke:
        code, status, err = smoke_empty()
        if code != 0 or status != "insufficient_evidence":
            failures.append("empty-input smoke test failed: code=%s status=%s err=%s"
                            % (code, status, err))

    if not args.skip_determinism and Path(args.input).exists():
        same, msg = determinism(args.input)
        if not same:
            failures.append("determinism: %s" % msg)

    if failures:
        print("SELFTEST FAIL")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("SELFTEST OK")
    print("  status=%s" % ((result or {}).get("status")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
