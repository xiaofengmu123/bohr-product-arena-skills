#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selfcheck.py —— 交付前自检（纯标准库）。

校验 result.json 契约回填、状态合法、产物存在与格式可解析、证据定位可指向文件。
用法：
    python3 -B scripts/selfcheck.py --input /input --output /app/output [--mindmap /app/work/mindmap.json]

退出码：默认始终 0（仅报告）；--strict 时遇到 critical 失败退出 1。
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
STANDARD_FIELDS = ["case_id", "status", "sources", "uncertainty", "change_log",
                   "errors", "degradations", "pending_human", "runtime", "artifacts"]


def read_text(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def discover_contract(path):
    """从 /input/case.json 读出 output_contract 键名列表；找不到返回 None。"""
    text = read_text(os.path.join(path, "case.json"))
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        for k in ("output_contract", "outputContract", "output_schema", "result_schema", "contract"):
            v = data.get(k)
            if isinstance(v, dict):
                return sorted(v.keys())
            if isinstance(v, list):
                names = []
                for item in v:
                    if isinstance(item, str):
                        names.append(item)
                    elif isinstance(item, dict) and "name" in item:
                        names.append(item["name"])
                return names
    return None


def check_svg(path):
    try:
        ET.parse(path)
        return True, "parse-ok"
    except Exception as e:
        return False, "svg-parse-fail: %s" % e


def check_pdf(path):
    with open(path, "rb") as f:
        head = f.read(5)
    return head == b"%PDF-", "header=" + head.decode("latin-1", "replace")


def check_mm(path):
    try:
        ET.parse(path)
        return True, "parse-ok"
    except Exception as e:
        return False, "mm-parse-fail: %s" % e


def check_json(path):
    try:
        json.load(open(path, encoding="utf-8-sig"))
        return True, "parse-ok"
    except Exception as e:
        return False, "json-parse-fail: %s" % e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--mindmap", default="/app/work/mindmap.json")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    checks = []  # (severity, item, detail)
    result = None
    result_path = os.path.join(args.output, "result.json")

    if not os.path.isfile(result_path):
        checks.append(("FAIL", "result.json", "missing"))
    else:
        result = read_text(result_path)
        try:
            r = json.loads(result)
        except json.JSONDecodeError as e:
            r = None
            checks.append(("FAIL", "result.json", "invalid json: %s" % e))
        if r is not None:
            cid = r.get("case_id")
            checks.append(("PASS" if cid else "FAIL", "case_id", "value=%r" % cid))
            status = r.get("status")
            checks.append(("PASS" if status in VALID_STATUS else "FAIL", "status",
                           "value=%r (allowed=%s)" % (status, sorted(VALID_STATUS))))

            contract_keys = discover_contract(args.input)
            missing = []
            if contract_keys:
                for k in contract_keys:
                    if k not in r:
                        missing.append(k)
                checks.append(("PASS" if not missing else "FAIL", "output_contract回填",
                               "缺失: %s" % (missing or "无")))
            else:
                checks.append(("WARN", "output_contract回填", "case.json 中未发现 contract，未校验"))

            for f in STANDARD_FIELDS:
                if f not in r:
                    checks.append(("WARN", "标准字段", "result.json 缺字段 %s" % f))

            # 证据可追溯性：sources/uncertainty 结构
            for key in ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human"):
                v = r.get(key)
                if v is None:
                    checks.append(("WARN", key, "值为 null，建议为 []"))
                elif not isinstance(v, list):
                    checks.append(("WARN", key, "非列表类型 %s" % type(v).__name__))

            # 产物存在性
            artifacts = r.get("artifacts") or {}
            if isinstance(artifacts, dict):
                for name in ("mindmap_svg", "mindmap_pdf", "mindmap_mm", "mindmap_md", "mindmap_json"):
                    p = artifacts.get(name)
                    if not p:
                        checks.append(("WARN", name, "未登记路径"))
                    elif not os.path.isfile(p):
                        checks.append(("FAIL", name, "文件不存在: %s" % p))

    # 直接校验输出目录里的产物
    expected = {
        "mindmap.svg": check_svg,
        "mindmap.pdf": check_pdf,
        "mindmap.mm": check_mm,
        "mindmap.json": check_json,
    }
    for fn, checker in expected.items():
        p = os.path.join(args.output, fn)
        if not os.path.isfile(p):
            checks.append(("FAIL" if fn in ("mindmap.svg", "mindmap.json") else "WARN",
                           fn, "missing"))
        else:
            ok, detail = checker(p)
            checks.append(("PASS" if ok else "FAIL", fn, detail))

    md_path = os.path.join(args.output, "mindmap.md")
    if os.path.isfile(md_path) and os.path.getsize(md_path) > 0:
        checks.append(("PASS", "mindmap.md", "non-empty"))
    elif os.path.isfile(md_path):
        checks.append(("WARN", "mindmap.md", "empty"))
    else:
        checks.append(("WARN", "mindmap.md", "missing"))

    # 证据 locator 指向文件（尽力而为）
    mm_path = os.path.join(args.output, "mindmap.json")
    if os.path.isfile(mm_path):
        try:
            mm = json.load(open(mm_path, encoding="utf-8-sig"))
        except Exception:
            mm = None
        if mm:
            locs = []
            def walk(n):
                for e in (n.get("evidence") or []):
                    locs.append(e.get("locator", ""))
                for c in n.get("children", []):
                    walk(c)
            walk(mm.get("root") or {})
            for r in (mm.get("relations") or []):
                for e in (r.get("evidence") or []):
                    locs.append(e.get("locator", ""))
            n_missing_file = 0
            for loc in locs:
                if not loc:
                    n_missing_file += 1
                    continue
                # locator 形如 materials/paper.md#p3 —— 取 # 前路径
                fp = loc.split("#", 1)[0]
                base = os.path.join(args.input, fp)
                if fp and not os.path.isfile(base):
                    n_missing_file += 1
            checks.append(("PASS" if n_missing_file == 0 else "WARN", "evidence locator",
                           "%d 条 locator 无法对应到 /input 下文件" % n_missing_file))

    n_pass = sum(1 for s, _, _ in checks if s == "PASS")
    n_warn = sum(1 for s, _, _ in checks if s == "WARN")
    n_fail = sum(1 for s, _, _ in checks if s == "FAIL")
    for sev, item, detail in checks:
        print("[%s] %-22s %s" % (sev, item, detail))
    print("SELFCHECK: %d pass / %d warn / %d fail" % (n_pass, n_warn, n_fail))
    return 1 if (args.strict and n_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
