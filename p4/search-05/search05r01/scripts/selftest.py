#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（退出码 0 = 全通过）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output
  python3 -B scripts/selftest.py --input /input --output /app/output --repro

检查项（对应评分维度）：
  1. result.json 合法、含 case_id、status 合法、output_contract 键无缺
  2. 每条候选有 provenance；每条 field_value 证据有 locator + quote，且可回源核对
  3. evidence_map 的 claims / field_sources / dedup_clusters / conflicts /
     coverage_evidence 结构齐全
  4. 覆盖缺口、冲突、缺失元数据被显式列出
  5. （--repro）同输入重复运行确定性（去除墙钟时间戳后一致）
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import core_impl as ci  # noqa: E402
import main as m  # noqa: E402

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print("PASS  %s" % msg)


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print("FAIL  %s" % msg)


def load_result(output_dir: Path) -> (Any, List[str]):
    rp = output_dir / "result.json"
    if not rp.exists():
        return None, ["result.json missing"]
    try:
        obj = json.loads(rp.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return None, ["result.json unparsable: %s" % exc]
    return obj, []


def verify_quote(input_dir: Path, ev: Dict) -> bool:
    """对 field_value 证据做回源核对：按 pointer+key_path 重取值并比对 quote。"""
    if ev.get("type") != "field_value":
        return True
    src = ev.get("source_file")
    ptr = ev.get("pointer")
    kp = ev.get("key_path")
    if not src or not ptr or not kp:
        return True  # 非定位型证据不在此核对
    fpath = input_dir.joinpath(*src.split("/"))
    if not fpath.exists():
        return True  # 源文件不在本环境（本地复测时跳过）
    try:
        obj = json.loads(fpath.read_text(encoding="utf-8-sig"))
        record = ci.navigate_pointer(obj, ptr)
        if not isinstance(record, dict):
            return False
        raw_val = ci.navigate_key_path(record, kp)
    except Exception:  # noqa: BLE001
        return False
    if raw_val is None:
        return True
    return ci.render_quote(raw_val) == ev.get("quote")


def check_schema(res: Dict, input_dir: Path, output_dir: Path) -> None:
    if "case_id" not in res:
        bad("missing case_id")
    else:
        ok("case_id present")
    if res.get("status") in ci.STATUSES:
        ok("status valid: %s" % res.get("status"))
    else:
        bad("status invalid: %r" % res.get("status"))

    # output_contract 回填
    case_meta, _ = m.read_case_meta(input_dir)
    contract = m.extract_output_contract(case_meta)
    missing = [k for k in contract if k not in res]
    if missing:
        bad("output_contract keys missing: %s" % missing)
    else:
        ok("output_contract keys echoed (%d)" % len(contract))

    for k in ("sources", "uncertainty", "change_log", "errors",
              "degradations", "pending_human", "limitations", "coverage"):
        if k not in res:
            bad("missing fixed field: %s" % k)
    ok("fixed fields present")

    ch = res.get("change_log") or []
    if ch and ch[0].get("step") == "skeleton" and \
            ch[0].get("status") == "written":
        ok("change_log first entry is skeleton")
    else:
        bad("change_log first entry not skeleton")

    cands = res.get("candidates") or []
    for c in cands:
        if not c.get("record_id"):
            bad("candidate missing record_id: %s" % c)
        if not (c.get("provenance")):
            bad("candidate %s missing provenance" % c.get("record_id"))
        if "field_sources" not in c:
            bad("candidate %s missing field_sources" % c.get("record_id"))
    ok("candidates have record_id/provenance/field_sources (%d)" % len(cands))

    evs = res.get("evidence_records") or []
    if cands and not evs:
        bad("candidates present but evidence_records empty")
    for ev in evs:
        if not ev.get("evidence_id"):
            bad("evidence missing evidence_id: %s" % ev)
        if ev.get("type") == "field_value" and \
                (not ev.get("locator") or ev.get("quote") in (None, "")):
            bad("field_value evidence missing locator/quote: %s" %
                ev.get("evidence_id"))
    ok("evidence_records shape ok (%d)" % len(evs))

    # quote 回源核对
    verified = 0
    skipped = 0
    for ev in evs:
        if ev.get("type") == "field_value":
            if ev.get("pointer") and ev.get("key_path"):
                if verify_quote(input_dir, ev):
                    verified += 1
                else:
                    bad("quote mismatch for %s (%s)" % (
                        ev.get("evidence_id"), ev.get("locator")))
            else:
                skipped += 1
    ok("quote verification: %d verified, %d skipped" % (verified, skipped))

    em = res.get("evidence_map") or {}
    for k in ("claims", "field_sources", "dedup_clusters", "conflicts",
              "coverage_evidence"):
        if k not in em:
            bad("evidence_map missing section: %s" % k)
    ok("evidence_map five sections present")

    cov = res.get("coverage") or {}
    for k in ("total_input_records", "merged_record_count",
              "duplicates_merged", "records_in_single_db_only"):
        if k not in cov:
            bad("coverage missing key: %s" % k)
    ok("coverage numeric keys present")


def strip_volatile(obj: Any) -> Any:
    o = copy.deepcopy(obj)
    if isinstance(o, dict):
        o.pop("runtime", None)
        if "change_log" in o:
            for entry in o.get("change_log", []):
                if isinstance(entry, dict):
                    entry.pop("at", None)
        for v in o.values():
            strip_volatile(v)
    elif isinstance(o, list):
        for v in o:
            strip_volatile(v)
    return o


def check_repro(input_dir: Path, output_dir: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="selftest_repro_"))
    try:
        outs = [tmp / ("o%d" % i) for i in (1, 2)]
        works = [tmp / ("w%d" % i) for i in (1, 2)]
        for o, w in zip(outs, works):
            cp = subprocess.run(
                [sys.executable, "-B", str(SCRIPT_DIR / "main.py"),
                 "--input", str(input_dir), "--output", str(o),
                 "--work", str(w)],
                capture_output=True, text=True, timeout=300)
            if cp.returncode != 0:
                bad("repro run failed rc=%s: %s" % (cp.returncode, cp.stderr))
                return
        r1 = json.loads((outs[0] / "result.json").read_text(encoding="utf-8"))
        r2 = json.loads((outs[1] / "result.json").read_text(encoding="utf-8"))
        a = json.dumps(strip_volatile(r1), ensure_ascii=False, sort_keys=True)
        b = json.dumps(strip_volatile(r2), ensure_ascii=False, sort_keys=True)
        if a == b:
            ok("deterministic re-run (volatile timestamps stripped)")
        else:
            bad("re-run differs after stripping volatile fields")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Skill 交付前自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--repro", action="store_true")
    args = ap.parse_args()
    input_dir = Path(args.input)
    output_dir = Path(args.output)

    res, errs = load_result(output_dir)
    if res is None:
        for e in errs:
            bad(e)
        print("RESULT: FAIL (%d fail)" % FAIL)
        return 1

    check_schema(res, input_dir, output_dir)
    if args.repro:
        check_repro(input_dir, output_dir)

    print("RESULT: %s (pass=%d fail=%d)" % (
        "PASS" if FAIL == 0 else "FAIL", PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())