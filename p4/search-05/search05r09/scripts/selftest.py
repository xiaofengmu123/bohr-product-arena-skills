#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（仅标准库）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work

检查项（对应评分维度）：
  1. result.json 合法、case_id 存在、status 合法
  2. 输出契约字段无缺键（case_id + DEFAULT_CONTRACT 并集）
  3. 每条候选有 provenance / raw_snapshot（逐字片段）/ field_sources
  4. evidence_records 非空（有候选时），每项带 locator + 逐字 quote
  5. coverage 列明各库 fields_missing 与仅单库记录
  6. 同输入重复运行结果确定（忽略运行时时间戳）
  7. 空输入 / 坏 JSON / 缺 case.json 不崩溃且产出合法 result.json
退出码：0=全部通过；非 0=存在 FAIL。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fedsearch as fs

SCRIPT_DIR = Path(__file__).resolve().parent
MAIN_PY = SCRIPT_DIR / "main.py"


def _load(path: Path) -> Tuple[Any, List[str]]:
    if not path.is_file():
        return None, ["missing result.json: %s" % path]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except Exception as e:
        return None, ["result.json 不可解析: %s" % e]


def _strip_volatile(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "runtime":
                out[k] = {kk: (None if kk in ("started_at", "finished_at") else vv)
                          for kk, vv in v.items()} if isinstance(v, dict) else v
            elif k == "change_log":
                out[k] = [{kk: (None if kk == "at" else vv) for kk, vv in e.items()}
                          for e in v] if isinstance(v, list) else v
            else:
                out[k] = _strip_volatile(v)
        return out
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


def fingerprint(res: Any) -> str:
    return json.dumps(_strip_volatile(res), ensure_ascii=False, sort_keys=True)


def _run_main(args: List[str], cwd: Path = None) -> int:
    return subprocess.run(
        [sys.executable, "-B", str(MAIN_PY)] + args,
        capture_output=True, text=True, cwd=cwd or SCRIPT_DIR,
    ).returncode


def check_contract(res: Dict[str, Any]) -> List[str]:
    errs = []
    keys = set(fs.DEFAULT_CONTRACT)
    for k in sorted(keys):
        if k not in res:
            errs.append("missing contract key: %s" % k)
    return errs


def check_candidates(res: Dict[str, Any]) -> List[str]:
    errs = []
    for c in res.get("candidates", []):
        if not c.get("record_id"):
            errs.append("candidate 缺 record_id")
        prov = c.get("provenance") or {}
        if not prov.get("contributing_records"):
            errs.append("%s 缺 provenance.contributing_records" % c.get("record_id"))
        if not c.get("raw_snapshot"):
            errs.append("%s 缺 raw_snapshot（逐字片段）" % c.get("record_id"))
        if not c.get("field_sources"):
            errs.append("%s 缺 field_sources" % c.get("record_id"))
        for s in c.get("raw_snapshot", []):
            if not s.get("locator") or not s.get("verbatim_fields"):
                errs.append("%s 的 raw_snapshot 缺 locator/逐字字段" % c.get("record_id"))
    return errs


def check_evidence(res: Dict[str, Any]) -> List[str]:
    errs = []
    evs = res.get("evidence_records", [])
    if res.get("candidates") and not evs:
        errs.append("有候选但 evidence_records 为空")
    for e in evs:
        if not e.get("locator") or e.get("quote") is None:
            errs.append("evidence %s 缺 locator/quote" % e.get("evidence_id"))
    return errs


def check_coverage(res: Dict[str, Any]) -> List[str]:
    errs = []
    cov = res.get("coverage") or {}
    for k in ("databases", "total_input_records", "merged_record_count",
              "duplicates_merged", "records_in_single_db_only",
              "systematically_missing_fields"):
        if k not in cov:
            errs.append("coverage 缺 %s" % k)
    for db, info in (cov.get("systematically_missing_fields") or {}).items():
        if "fields_missing" not in info:
            errs.append("coverage.%s 缺 fields_missing" % db)
    return errs


def check_status(res: Dict[str, Any]) -> List[str]:
    errs = []
    if not res.get("case_id"):
        errs.append("缺 case_id")
    if res.get("status") not in fs.STATUSES:
        errs.append("status 非法: %r" % res.get("status"))
    return errs


def check_robustness() -> List[str]:
    errs = []
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        # 1) 空输入
        empty_in = base / "empty_in"
        empty_out = base / "empty_out"
        empty_work = base / "empty_work"
        empty_in.mkdir()
        rc = _run_main(["--input", str(empty_in), "--output", str(empty_out),
                        "--work", str(empty_work)])
        r, e = _load(empty_out / "result.json")
        if rc != 0 or r is None or r.get("status") not in fs.STATUSES:
            errs.append("空输入未稳健处理 (rc=%d)" % rc)
        # 2) 坏 JSON
        bad_in = base / "bad_in"
        bad_out = base / "bad_out"
        bad_work = base / "bad_work"
        bad_in.mkdir()
        (bad_in / "x.json").write_text("{ not json", encoding="utf-8")
        rc = _run_main(["--input", str(bad_in), "--output", str(bad_out),
                        "--work", str(bad_work)])
        r, e = _load(bad_out / "result.json")
        if rc != 0 or r is None or r.get("status") not in fs.STATUSES:
            errs.append("坏 JSON 未稳健处理 (rc=%d)" % rc)
        # 3) 缺 case.json 但有空响应文件
        miss_in = base / "miss_in"
        miss_out = base / "miss_out"
        miss_work = base / "miss_work"
        miss_in.mkdir()
        rc = _run_main(["--input", str(miss_in), "--output", str(miss_out),
                        "--work", str(miss_work)])
        r, e = _load(miss_out / "result.json")
        if rc != 0 or r is None or not r.get("case_id"):
            errs.append("缺 case.json 未稳健处理 (rc=%d)" % rc)
    return errs


def check_determinism(input_dir: Path) -> List[str]:
    errs = []
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        outs = []
        for i in (1, 2):
            o = base / ("o%d" % i)
            w = base / ("w%d" % i)
            rc = _run_main(["--input", str(input_dir), "--output", str(o),
                            "--work", str(w)])
            r, e = _load(o / "result.json")
            if rc != 0 or r is None:
                errs.append("determinism run %d failed rc=%d" % (i, rc))
                return errs
            outs.append(fingerprint(r))
        if outs[0] != outs[1]:
            errs.append("同输入两次运行结果不一致（忽略时间戳后）")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    res, load_errs = _load(Path(args.output) / "result.json")
    checks: List[Tuple[str, bool, str]] = []
    if res is None:
        for e in load_errs:
            checks.append(("result.json 合法", False, e))
    else:
        groups = {
            "status/case_id": check_status(res),
            "contract keys": check_contract(res),
            "candidates 可回溯": check_candidates(res),
            "evidence_records": check_evidence(res),
            "coverage": check_coverage(res),
        }
        for name, errs in groups.items():
            checks.append((name, not errs, "; ".join(errs) if errs else "ok"))
    checks.append(("robustness", True, ""))
    rob_errs = check_robustness()
    checks[-1] = ("robustness", not rob_errs, "; ".join(rob_errs) if rob_errs else "ok")
    det_errs = check_determinism(Path(args.input))
    checks.append(("determinism", not det_errs, "; ".join(det_errs) if det_errs else "ok"))

    all_ok = True
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print("[%s] %s: %s" % (mark, name, detail))
    print("selftest %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
