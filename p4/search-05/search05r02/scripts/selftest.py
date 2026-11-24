#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（仅标准库）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output

逐项检查 result.json 与脚本质量；全部通过时退出码 0，否则退出码 1。
对应 SKILL.md 第 6 节自检清单与评分维度。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as fs  # noqa: E402


def _load_result(output_dir: Path):
    p = output_dir / "result.json"
    if not p.exists():
        return None, "missing result.json"
    obj, degs = fs.load_json(p)
    if obj is None:
        return None, "bad_json result.json: " + "; ".join(degs)
    if not isinstance(obj, dict):
        return None, "result.json not an object"
    return obj, None


def _canonical(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict = {}
        for k, v in obj.items():
            if k in ("runtime", "at", "started_at", "finished_at"):
                continue
            out[k] = _canonical(v)
        return out
    if isinstance(obj, list):
        return [_canonical(x) for x in obj]
    return obj


def _check(name: str, ok: bool, detail: str,
           results: List[Tuple[str, bool, str]]) -> None:
    results.append((name, ok, detail))


def run_checks(input_dir: Path, output_dir: Path,
               script_dir: Path) -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []

    # 脚本可编译
    for fn in ("core.py", "main.py", "selftest.py"):
        sp = script_dir / fn
        try:
            subprocess.run([sys.executable, "-m", "py_compile", str(sp)],
                           capture_output=True, check=True)
            _check("py_compile:%s" % fn, True, "compiles", results)
        except subprocess.CalledProcessError as e:
            _check("py_compile:%s" % fn, False,
                   e.stderr.decode("utf-8", "replace")[:200], results)

    res, err = _load_result(output_dir)
    if res is None:
        _check("result_json_valid", False, err, results)
        return results
    _check("result_json_valid", True, "valid JSON object", results)

    _check("case_id", isinstance(res.get("case_id"), str)
           and res["case_id"] != "", str(res.get("case_id")), results)
    _check("status_legal", res.get("status") in fs.STATUSES,
           str(res.get("status")), results)

    # output_contract 字段无缺键（用 case.json 的 output_contract 或默认契约）
    case_path = input_dir / "case.json"
    contract = fs.DEFAULT_CONTRACT
    if case_path.exists():
        case, _ = fs.load_json(case_path)
        if isinstance(case, dict) and isinstance(case.get("output_contract"),
                                                 list):
            contract = case["output_contract"]
    missing = [k for k in contract if k not in res]
    _check("contract_keys", not missing,
           "missing=" + ",".join(missing) if missing else "all present", results)

    # skeleton-first：首条 change_log 必须是落盘骨架
    cl = res.get("change_log", [])
    _check("skeleton_first",
           bool(cl) and isinstance(cl[0], dict)
           and cl[0].get("step") == "skeleton",
           cl[0].get("step") if cl else "empty change_log", results)

    # 阶段预算记录
    phases = (res.get("runtime") or {}).get("phases", [])
    pids = [p.get("id") for p in phases if isinstance(p, dict)]
    _check("phase_budget_recorded",
           any(x in pids for x in ("P0_skeleton", "P1_parse_fuse")),
           "phases=" + ",".join(pids), results)

    candidates = res.get("candidates", [])
    for c in candidates[:50]:
        if not isinstance(c, dict):
            continue
        has_prov = bool(c.get("provenance")) and bool(c.get("field_sources"))
        has_snap = isinstance(c.get("member_records"), list) and all(
            isinstance(m, dict) and m.get("raw_snapshot")
            for m in c["member_records"])
        _check("candidate_trace:%s" % c.get("record_id"),
               has_prov and has_snap,
               "provenance=%s raw_snapshot=%s" % (has_prov, has_snap), results)

    # 去重可解释：多库合并的记录必须有 dedup_reason
    merged_multi = [c for c in candidates
                    if isinstance(c, dict) and c.get("db_count", 1) > 1]
    ok_dedup = all(isinstance(c.get("dedup_reason"), list)
                   and c["dedup_reason"] for c in merged_multi)
    _check("dedup_explainable", ok_dedup,
           "multi_db_candidates=%d" % len(merged_multi), results)

    cov = res.get("coverage") or {}
    req_cov = {"total_input_records", "merged_record_count",
               "duplicates_merged", "records_in_single_db_only",
               "systematically_missing_fields", "per_database"}
    missing_cov = [k for k in req_cov if k not in cov]
    _check("coverage_fields", not missing_cov,
           "missing=" + ",".join(missing_cov) if missing_cov else "all present",
           results)

    evs = res.get("evidence_records", [])
    if candidates:
        ok_ev = bool(evs) and all(
            isinstance(e, dict) and e.get("source_file") and e.get("locator")
            and e.get("quote") for e in evs[:50])
        _check("evidence_records", ok_ev,
               "count=%d" % len(evs), results)
    else:
        _check("evidence_records", True, "no candidates, skipped", results)

    # 无伪造：状态为 insufficient_evidence 时不得有候选
    if res.get("status") == "insufficient_evidence":
        _check("no_fabrication", not candidates,
               "candidates=%d" % len(candidates), results)
    else:
        _check("no_fabrication", True, "status=%s" % res.get("status"), results)

    return results


def determinism_check(input_dir: Path, script_dir: Path) -> Tuple[bool, str]:
    """同输入重跑两次，去时间戳后必须逐字一致。"""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        r1, r2 = None, None
        for tag in ("a", "b"):
            out = base / ("out_" + tag)
            work = base / ("work_" + tag)
            cmd = [sys.executable, "-B", str(script_dir / "main.py"),
                   "--input", str(input_dir), "--output", str(out),
                   "--work", str(work)]
            cp = subprocess.run(cmd, capture_output=True)
            if cp.returncode != 0:
                return False, "run failed: " + cp.stderr.decode(
                    "utf-8", "replace")[:200]
            obj, _ = fs.load_json(out / "result.json")
            if tag == "a":
                r1 = _canonical(obj)
            else:
                r2 = _canonical(obj)
        return (r1 == r2), "deterministic" if r1 == r2 else "non-deterministic"


def main() -> int:
    p = argparse.ArgumentParser(description="多库联合检索交付自检")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    args = p.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    script_dir = Path(__file__).resolve().parent

    results = run_checks(input_dir, output_dir, script_dir)

    det_ok, det_msg = determinism_check(input_dir, script_dir)
    results.append(("determinism", det_ok, det_msg))

    n_fail = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print("[%s] %s -- %s" % (mark, name, detail))
        if not ok:
            n_fail += 1

    print("-" * 60)
    print("selfcheck: %d checks, %d failed" % (len(results), n_fail))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
