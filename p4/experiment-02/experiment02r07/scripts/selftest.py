#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 结果结构校验 + 故障注入自检。

用法：
    python3 scripts/selftest.py --input /input --output /app/output [--work /app/work]
        校验 result.json 的契约、枚举、证据、七维覆盖与引文可溯源。
    python3 scripts/selftest.py --faults
        在临时目录中运行故障注入（空输入、坏 JSON、UTF-16、超大文件、二进制、
        不可写输出、符号链接、确定性复跑），逐项 PASS/FAIL。
    python3 scripts/selftest.py --all --input DIR --output DIR --work DIR
        两者都做。--strict 将"引文无法在原文定位"从 WARN 升为 FAIL。

退出码：存在 FAIL 时返回 1；仅 WARN 返回 0。仅依赖标准库。
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SKILL_VERSION = "2.1.0"
DIMS = ["assumptions_variables", "controls", "equipment_materials", "time_cost",
        "parameter_ranges", "risks_exceptions", "ethics_safety"]
VERDICTS = {"ok", "conditional", "blocked", "unconfirmed", "missing", "conflict"}
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
REQUIRED_KEYS = ["case_id", "status", "research_question", "experiment_plan",
                 "review", "plan_comparison", "evidence_and_risks", "confirmation",
                 "sources", "uncertainty", "pending_human", "change_log",
                 "artifacts"]
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml",
            ".tex", ".rst", ".log", ".ini", ".cfg"}


def _fix_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _ingest_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ingest.py")


def _load_ingest():
    spec = importlib.util.spec_from_file_location("ingest_mod", _ingest_path())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 结果校验
# --------------------------------------------------------------------------- #

def normalize(s):
    return re.sub(r"\s+", "", s or "")


def build_corpus(input_dir, work_dir):
    """收集输入目录 + work/pdf 提取文本，去空白后用于引文定位校验。"""
    chunks = []
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            p = os.path.join(root, name)
            try:
                if os.path.getsize(p) > 2 * 1024 * 1024:
                    continue
            except OSError:
                continue
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if not data:
                continue
            if data[:8192].count(0) / max(1, len(data[:8192])) > 0.02:
                continue
            for enc in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    chunks.append(data.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
    pdf_dir = os.path.join(work_dir, "pdf")
    if os.path.isdir(pdf_dir):
        for name in os.listdir(pdf_dir):
            if name.endswith(".txt"):
                try:
                    with open(os.path.join(pdf_dir, name), "r", encoding="utf-8",
                              errors="replace") as fh:
                        chunks.append(fh.read())
                except OSError:
                    pass
    return normalize("".join(chunks))


def _get_contract_keys(input_dir):
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            if name.lower() not in ("case.json",):
                continue
            p = os.path.join(root, name)
            try:
                with open(p, "r", encoding="utf-8-sig") as fh:
                    obj = json.load(fh)
            except Exception:  # noqa: BLE001
                return []
            spec = obj.get("output_contract")
            if isinstance(spec, dict):
                if "fields" in spec and isinstance(spec["fields"], dict):
                    spec = spec["fields"]
                elif "properties" in spec and isinstance(spec["properties"], dict):
                    spec = spec["properties"]
                return list(spec.keys())
    return []


def _all_evidence(result):
    out = []
    review = result.get("review") or {}
    for d in DIMS:
        for it in review.get(d) or []:
            for e in (it.get("evidence") or []) if isinstance(it, dict) else []:
                if isinstance(e, dict) and e.get("quote"):
                    out.append(e)
    for e in (result.get("evidence_and_risks") or {}).get("evidence") or []:
        if isinstance(e, dict) and e.get("quote"):
            out.append(e)
    return out


def validate_result(result, input_dir, work_dir, strict):
    checks = []
    if not isinstance(result, dict):
        return [("result_type", "fail", "result.json 不是对象")]

    def add(cid, level, msg):
        checks.append((cid, level, msg))

    for k in REQUIRED_KEYS:
        if k not in result:
            add("required:" + k, "fail", f"缺少必需键 {k}")
    if result.get("status") not in STATUSES:
        add("status", "fail", f"status 非法: {result.get('status')}")
    cid = result.get("case_id")
    if result.get("status") != "input_error" and (not cid or cid == "unknown"):
        add("case_id", "warn", "case_id 缺失或为 unknown")

    contract_keys = _get_contract_keys(input_dir)
    for k in contract_keys:
        if k not in result:
            add("contract:" + k, "fail", f"output_contract 字段 {k} 未回填到顶层")

    review = result.get("review") or {}
    for d in DIMS:
        items = review.get(d)
        if not isinstance(items, list):
            add("dim:" + d, "fail", f"review.{d} 不是数组")
            continue
        for it in items:
            if not isinstance(it, dict):
                add("dim:" + d, "fail", "审查项不是对象")
                continue
            vid = it.get("id", "?")
            if not it.get("id"):
                add(f"item:{vid}", "fail", "审查项缺少 id")
            if it.get("verdict") not in VERDICTS:
                add(f"item:{vid}", "fail", f"verdict 非法: {it.get('verdict')}")
            if it.get("verdict") in ("ok", "conditional", "blocked", "conflict"):
                ev = it.get("evidence") or []
                if not ev or not any(isinstance(e, dict) and e.get("locator") and
                                     e.get("quote") for e in ev):
                    add(f"item:{vid}", "fail",
                        "ok/conditional/blocked/conflict 必须有 locator+quote 证据")
            if it.get("verdict") == "conditional" and not it.get("conditions"):
                add(f"item:{vid}", "fail", "conditional 未写 conditions")
            if it.get("verdict") == "blocked" and not (it.get("blocks") or it.get("conditions")):
                add(f"item:{vid}", "warn", "blocked 未写清缺什么")

    if result.get("status") == "ok":
        empty_dims = [d for d in DIMS if not (review.get(d) or [])]
        if empty_dims:
            add("status:ok", "fail", f"status=ok 但以下维度为空: {empty_dims}")
        if result.get("errors"):
            add("status:ok", "fail", "status=ok 但 errors 非空")

    pc = result.get("plan_comparison") or {}
    if pc.get("ranking") and pc.get("recommended") and \
            pc["recommended"] not in pc["ranking"]:
        add("plan_comparison", "warn", "recommended 不在 ranking 中")

    if not result.get("change_log"):
        add("change_log", "fail", "change_log 为空")

    text = json.dumps(result, ensure_ascii=False)
    for marker in ("TBD", "TODO", "待补", "待填", "FIXME"):
        if marker in text:
            add("placeholder", "warn", f"疑似占位符未清理: {marker}")

    corpus = build_corpus(input_dir, work_dir)
    for e in _all_evidence(result):
        q = normalize(e.get("quote"))
        loc = e.get("locator", "?")
        if len(q) >= 8 and corpus and q not in corpus:
            add("quote:" + loc, "fail" if strict else "warn",
                f"引文无法在原文定位（可能因 PDF 排版差异）: {loc}")

    return checks


def _report_path(output_dir):
    return os.path.join(output_dir, "review_report.md")


# --------------------------------------------------------------------------- #
# 故障注入
# --------------------------------------------------------------------------- #

def _run_ingest(in_dir, out_dir, work_dir):
    cmd = [sys.executable, _ingest_path(), "--input", in_dir,
           "--output", out_dir, "--work", work_dir]
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return None


def _read_result(out_dir):
    p = os.path.join(out_dir, "result.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _ft(name, func):
    try:
        res = func()
        if res is None:
            return name, None, "skipped（平台不支持）"
        ok, detail = res
        return name, bool(ok), detail
    except Exception as exc:  # noqa: BLE001
        return name, False, type(exc).__name__ + ":" + str(exc)[:160]


def _tmpdirs(parent, label):
    base = tempfile.mkdtemp(prefix=label + "_", dir=parent)
    return (os.path.join(base, "in"), os.path.join(base, "out"),
            os.path.join(base, "work"), base)


GOOD_CASE = {
    "case_id": "selftest-001", "title": "自检案例",
    "research_question": "比较三个候选方案在温控与样本量约束下的可开工性",
    "output_contract": {"recommendation": "string", "confidence": "number",
                        "checklist": "array", "details": "object", "flag": "boolean"},
    "plans": [
        {"id": "P1", "title": "方案甲", "steps": [{"name": "预热", "duration": "30 min"},
                                            {"name": "合成", "duration": "2 h"}]},
        {"id": "P2", "title": "方案乙", "steps": [{"name": "预热", "duration": "45 min"}]},
        {"id": "P3", "title": "方案丙", "steps": [{"name": "检测", "duration": "1 h"}]},
    ],
    "contracts": {"price_list": {"离心机": 1200, "试剂盒": 480}},
    "parameter_domains": {"温度": {"min": 20, "max": 60, "step": 1}},
    "samples": {"n_available": 24},
    "ethics": {"involves_human": False},
}


def ft_empty():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_empty")
    os.makedirs(os.path.join(base, "in"), exist_ok=True)
    p = _run_ingest(os.path.join(base, "in"), out, work)
    if p is None:
        return False, "timeout"
    r = _read_result(out)
    return (p.returncode == 0 and r is not None and
            r.get("status") == "insufficient_evidence"), \
        f"rc={p.returncode} status={r.get('status') if r else None}"


def ft_missing_dir():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_missing")
    p = _run_ingest(os.path.join(base, "nope"), out, work)
    r = _read_result(out)
    return (p is not None and p.returncode == 0 and r is not None and
            r.get("status") == "input_error"), \
        f"rc={p.returncode if p else 'timeout'} status={r.get('status') if r else None}"


def ft_good():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_good")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(GOOD_CASE, fh, ensure_ascii=False)
    with open(os.path.join(in_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    ok = (p is not None and p.returncode == 0 and r is not None and
          r.get("case_id") == "selftest-001" and r.get("status") == "partial" and
          isinstance(r.get("recommendation"), str) and
          r.get("confidence") is None and isinstance(r.get("checklist"), list))
    return ok, f"rc={p.returncode if p else 'timeout'} status={r.get('status') if r else None}"


def ft_bad_json():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_badjson")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write('{"case_id": "bad-001", "plans": [{"id": "P1", "steps": [{"name": "a"')
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    ok = (p is not None and p.returncode == 0 and r is not None and
          r.get("case_id") == "bad-001" and r.get("degradations"))
    return ok, f"rc={p.returncode if p else 'timeout'} case_id={r.get('case_id') if r else None} deg={len(r.get('degradations', [])) if r else 0}"


def ft_utf16():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_utf16")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "wb") as fh:
        fh.write(json.dumps({"case_id": "utf16-ok", "plans": []},
                            ensure_ascii=False).encode("utf-16"))
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    ok = (p is not None and p.returncode == 0 and r is not None and
          r.get("case_id") == "utf16-ok")
    return ok, f"rc={p.returncode if p else 'timeout'} case_id={r.get('case_id') if r else None}"


def ft_oversize():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_oversize")
    in_dir = os.path.join(base, "in")
    os.makedirs(os.path.join(in_dir, "materials"), exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(GOOD_CASE, fh, ensure_ascii=False)
    big = os.path.join(in_dir, "materials", "notes.md")
    with open(big, "wb") as fh:
        fh.write(b"# head\n" + b"x" * (3 * 1024 * 1024))
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    inv_p = os.path.join(work, "input_inventory.json")
    truncated = False
    if os.path.isfile(inv_p):
        with open(inv_p, "r", encoding="utf-8") as fh:
            inv = json.load(fh)
        truncated = any(f.get("truncated") for f in inv.get("files", []))
    ok = (p is not None and p.returncode == 0 and r is not None and
          r.get("status") == "partial" and truncated)
    return ok, f"rc={p.returncode if p else 'timeout'} truncated={truncated}"


def ft_binary():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_binary")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(GOOD_CASE, fh, ensure_ascii=False)
    with open(os.path.join(in_dir, "garbage.bin"), "wb") as fh:
        fh.write(bytes(range(256)) * 40)
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    ok = (p is not None and p.returncode == 0 and r is not None and
          r.get("status") == "partial")
    return ok, f"rc={p.returncode if p else 'timeout'}"


def ft_unwritable():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_unwritable")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(GOOD_CASE, fh, ensure_ascii=False)
    out_file = os.path.join(base, "out")   # 故意是文件
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write("occupied")
    p = _run_ingest(in_dir, out_file, work)
    ok = (p is not None and p.returncode == 0 and "WRITE_FALLBACK" in
          (p.stdout or ""))
    return ok, f"rc={p.returncode if p else 'timeout'} fallback={'WRITE_FALLBACK' in (p.stdout or '')}"


def ft_determinism():
    parent = tempfile.mkdtemp(prefix="efr_det_", dir=tempfile.gettempdir())
    r1 = r2 = None
    for label in ("a", "b"):
        in_dir = os.path.join(parent, label, "in")
        out = os.path.join(parent, label, "out")
        work = os.path.join(parent, label, "work")
        os.makedirs(in_dir, exist_ok=True)
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(GOOD_CASE, fh, ensure_ascii=False)
        p = _run_ingest(in_dir, out, work)
        if p is None or p.returncode != 0:
            return False, "运行失败"
        rr = _read_result(out)
        rr.pop("input_brief", None)
        if label == "a":
            r1 = rr
        else:
            r2 = rr
    same = (r1.get("run_id") == r2.get("run_id") and
            r1.get("case_fingerprint") == r2.get("case_fingerprint") and
            json.dumps(r1, sort_keys=True, ensure_ascii=False) ==
            json.dumps(r2, sort_keys=True, ensure_ascii=False))
    return same, f"run_id={r1.get('run_id')} vs {r2.get('run_id')}"


def ft_symlink():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_symlink")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(GOOD_CASE, fh, ensure_ascii=False)
    target = os.path.join(in_dir, "case.json")
    link = os.path.join(in_dir, "case-link.json")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return None  # 平台不支持 → 跳过
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    ok = (p is not None and p.returncode == 0 and r is not None)
    return ok, f"rc={p.returncode if p else 'timeout'}"


def ft_gb18030():
    _, out, work, base = _tmpdirs(tempfile.gettempdir(), "efr_gb")
    in_dir = os.path.join(base, "in")
    os.makedirs(in_dir, exist_ok=True)
    with open(os.path.join(in_dir, "case.json"), "wb") as fh:
        fh.write(json.dumps({"case_id": "gb-ok", "plans": []},
                            ensure_ascii=False).encode("gb18030"))
    p = _run_ingest(in_dir, out, work)
    r = _read_result(out)
    ok = (p is not None and p.returncode == 0 and r is not None and
          r.get("case_id") == "gb-ok")
    return ok, f"rc={p.returncode if p else 'timeout'} case_id={r.get('case_id') if r else None}"


FAULTS = [
    ("empty_input", ft_empty),
    ("missing_dir", ft_missing_dir),
    ("good_min", ft_good),
    ("bad_json_truncated", ft_bad_json),
    ("utf16_bom", ft_utf16),
    ("gb18030", ft_gb18030),
    ("oversize_material", ft_oversize),
    ("binary_file", ft_binary),
    ("unwritable_output", ft_unwritable),
    ("determinism", ft_determinism),
    ("symlink", ft_symlink),
]


def run_faults():
    rows = []
    for name, func in FAULTS:
        res = _ft(name, func)
        rows.append(res)
    return rows


# --------------------------------------------------------------------------- #

def main(argv=None):
    _fix_stdio()
    ap = argparse.ArgumentParser(description="selftest：结果校验 + 故障注入")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--faults", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    n_fail = n_warn = n_pass = 0
    if args.faults or args.all:
        rows = run_faults()
        for name, ok, detail in rows:
            if ok is None:
                print(f"[SKIP] {name} ({detail or 'not supported'})")
            elif ok:
                print(f"[PASS] {name}  {detail}")
                n_pass += 1
            else:
                print(f"[FAIL] {name}  {detail}")
                n_fail += 1

    if (not args.faults) or args.all:
        result_path = os.path.join(args.output, "result.json")
        if not os.path.isfile(result_path):
            print(f"[FAIL] result_not_found  {result_path}")
            n_fail += 1
        else:
            with open(result_path, "r", encoding="utf-8") as fh:
                result = json.load(fh)
            checks = validate_result(result, args.input, args.work, args.strict)
            for cid, level, msg in checks:
                if level == "fail":
                    print(f"[FAIL] {cid}  {msg}")
                    n_fail += 1
                elif level == "warn":
                    print(f"[WARN] {cid}  {msg}")
                    n_warn += 1
                else:
                    print(f"[PASS] {cid}")
                    n_pass += 1
            if args.all:
                # 报告存在性检查
                report = _report_path(args.output)
                if result.get("status") == "ok" and not os.path.isfile(report):
                    print(f"[FAIL] report_missing  {report}")
                    n_fail += 1
                elif result.get("status") == "ok":
                    print(f"[PASS] report_present  {report}")
                    n_pass += 1
            if n_fail == 0 and n_warn == 0:
                print("[PASS] structure  result.json 契约/枚举/证据/七维覆盖全部通过")
                n_pass += 1

    total = n_pass + n_fail + n_warn
    print(f"\nsummary: {n_pass} pass, {n_warn} warn, {n_fail} fail, "
          f"{total} checks")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
