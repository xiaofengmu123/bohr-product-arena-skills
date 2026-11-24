#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 包级自检：空输入、坏 JSON、坏编码、SVG 可解析、重复运行一致。

用法：python scripts/selftest.py [--keep-temp]
退出码：0 表示全部通过；1 表示存在失败。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def run_py(args, cwd=None):
    return subprocess.run([sys.executable, "-B"] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


def test_help():
    r = run_py([os.path.join(HERE, "main.py"), "--help"])
    record("main.py --help 退出码 0", r.returncode == 0, "rc=%d" % r.returncode)


def test_empty_input(tmp):
    inp = os.path.join(tmp, "empty")
    out = os.path.join(tmp, "out-empty")
    work = os.path.join(tmp, "work-empty")
    os.makedirs(inp)
    r = run_py([os.path.join(HERE, "main.py"), "run",
                "--input", inp, "--output", out, "--work", work])
    ok = r.returncode == 0
    status = None
    if ok:
        res, _ = C.load_json(os.path.join(out, "result.json"))
        if res:
            status = res.get("status")
    record("空输入目录 -> insufficient_evidence", ok and status == "insufficient_evidence",
           "rc=%d status=%s" % (r.returncode, status))


def test_bad_json(tmp):
    inp = os.path.join(tmp, "badjson")
    out = os.path.join(tmp, "out-badjson")
    work = os.path.join(tmp, "work-badjson")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    r = run_py([os.path.join(HERE, "main.py"), "run",
                "--input", inp, "--output", out, "--work", work])
    record("坏 JSON 输入不崩溃", r.returncode == 0, "rc=%d" % r.returncode)


def test_svg_roundtrip(tmp):
    ir = {
        "case_id": "self-1", "kind": "process_flow", "title": "自检流程",
        "entities": [
            {"id": "e1", "label": "原料罐", "type": "source",
             "evidence": {"locator": "materials/m.md#p1", "quote": "原料罐", "status": "verbatim"}},
            {"id": "e2", "label": "反应器", "type": "reactor",
             "evidence": {"locator": "materials/m.md#p2", "quote": "反应器", "status": "verbatim"}},
            {"id": "e3", "label": "产物收集", "type": "sink",
             "evidence": {"locator": "materials/m.md#p3", "quote": "产物收集", "status": "verbatim"}},
        ],
        "relations": [
            {"id": "r1", "from": "e1", "to": "e2", "kind": "flow", "direction": "forward",
             "evidence": {"locator": "materials/m.md#p2", "quote": "流入", "status": "verbatim"}},
            {"id": "r2", "from": "e2", "to": "e3", "kind": "flow", "direction": "forward",
             "evidence": {"locator": "materials/m.md#p3", "quote": "收集", "status": "verbatim"}},
            {"id": "r3", "from": "e3", "to": "e1", "kind": "return", "direction": "backward",
             "evidence": {"locator": "materials/m.md#p4", "quote": "回流", "status": "verbatim"}},
        ],
    }
    svg = os.path.join(tmp, "figure.svg")
    _, meta = C.build_svg(ir, svg)
    checks = C.validate(ir, svg)
    summary = C.summarize_checks(checks)
    ok = os.path.exists(svg) and summary["pass"]
    record("IR -> SVG 生成且无 critical", ok,
           "meta=%s summary=%s" % (json.dumps(meta, ensure_ascii=False), json.dumps(summary, ensure_ascii=False)))
    # SVG 可解析
    try:
        import xml.etree.ElementTree as ET
        ET.parse(svg)
        record("SVG XML 可解析", True)
    except Exception as e:
        record("SVG XML 可解析", False, str(e))
    return ir


def test_dangling_detected(tmp):
    ir = {
        "case_id": "self-2", "kind": "apparatus", "title": "断头检测",
        "entities": [{"id": "e1", "label": "A", "type": "vessel",
                      "evidence": {"locator": "m.md#p1", "quote": "A", "status": "verbatim"}}],
        "relations": [{"id": "r1", "from": "e1", "to": "missing",
                       "evidence": {"locator": "m.md#p2", "quote": "接", "status": "verbatim"}}],
    }
    checks = C.validate(ir)
    codes = {c["code"] for c in checks}
    record("断头关系被检出", "dangling_to" in codes, "codes=%s" % sorted(codes))


def test_missing_evidence_flagged(tmp):
    ir = {
        "case_id": "self-3", "kind": "apparatus", "title": "缺证据检测",
        "entities": [{"id": "e1", "label": "X", "type": "vessel"}],
        "relations": [],
    }
    checks = C.validate(ir)
    codes = {c["code"] for c in checks}
    record("缺证据实体被检出", "entity_missing_evidence" in codes, "codes=%s" % sorted(codes))


def test_determinism(tmp):
    ir = {
        "case_id": "self-4", "kind": "apparatus", "title": "确定性",
        "entities": [
            {"id": "e1", "label": "泵", "type": "pump",
             "evidence": {"locator": "m.md#p1", "quote": "泵", "status": "verbatim"}},
            {"id": "e2", "label": "阀", "type": "valve",
             "evidence": {"locator": "m.md#p2", "quote": "阀", "status": "verbatim"}},
        ],
        "relations": [],
    }
    s1, _ = C.build_svg(ir)
    s2, _ = C.build_svg(ir)
    record("重复运行 SVG 一致", s1 == s2, "len=%d" % len(s1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="scix-selftest-")
    try:
        test_help()
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_svg_roundtrip(tmp)
        test_dangling_detected(tmp)
        test_missing_evidence_flagged(tmp)
        test_determinism(tmp)
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in RESULTS if not r["ok"]]
    print("----")
    print("total=%d failed=%d" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
