#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（仅标准库，退出码 0 = 全部通过）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output [--work /app/work]

检查三类事项：
  1) 结果契约：result.json 合法、case_id/status 合法、契约字段无缺键；
  2) 证据溯源：候选有 provenance 与逐字 raw_snapshot，证据记录带 locator/quote；
  3) 鲁棒性：空输入、坏 JSON、缺参数、超大文件、缺 case.json 均优雅降级不崩溃；
  4) 确定性：同一输入重复分析结果一致（不含运行时时间戳）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import core_impl as fs
import main as main_mod

CHECKS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


# --------------------------------------------------------------------------
# 1) 结果契约
# --------------------------------------------------------------------------

def check_contract(output_dir: Path) -> None:
    rp = output_dir / "result.json"
    check("result_exists", rp.exists(), str(rp))
    if not rp.exists():
        return
    try:
        res = json.loads(rp.read_text(encoding="utf-8"))
        check("result_valid_json", True)
    except Exception as exc:
        check("result_valid_json", False, str(exc))
        return
    check("has_case_id", isinstance(res.get("case_id"), str) and bool(res.get("case_id")))
    check("status_valid", res.get("status") in fs.STATUSES, str(res.get("status")))
    missing = [k for k in fs.DEFAULT_CONTRACT if k not in res]
    check("contract_keys_present", not missing, "missing: %s" % ", ".join(missing))
    check("skeleton_first",
          (res.get("change_log") or [{}])[0].get("step") == "skeleton",
          "change_log[0] must be skeleton write")
    check("runtime_present",
          isinstance(res.get("runtime"), dict) and res["runtime"].get("finished_at") is not None)
    check("sources_is_list", isinstance(res.get("sources"), list))
    check("uncertainty_present",
          isinstance(res.get("uncertainty"), dict) and "note" in res.get("uncertainty", {}))
    check("change_log_is_list", isinstance(res.get("change_log"), list))


# --------------------------------------------------------------------------
# 2) 证据溯源
# --------------------------------------------------------------------------

def check_provenance(output_dir: Path) -> None:
    rp = output_dir / "result.json"
    if not rp.exists():
        return
    try:
        res = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        return
    candidates = res.get("candidates") or []
    check("candidates_is_list", isinstance(candidates, list))
    all_ok = True
    for c in candidates:
        if not isinstance(c.get("record_id"), str):
            all_ok = False
            break
        prov = c.get("provenance")
        if not isinstance(prov, dict):
            all_ok = False
            break
        if not isinstance(prov.get("dbs"), list) or not prov.get("source_files"):
            all_ok = False
            break
        snaps = prov.get("raw_snapshots") or []
        if not snaps or any(not isinstance(s.get("snapshot"), str) for s in snaps):
            all_ok = False
            break
        fs_ok = isinstance(c.get("field_sources"), dict)
        if not fs_ok:
            all_ok = False
            break
    check("candidate_provenance", all_ok)

    ev = res.get("evidence_records") or []
    check("evidence_is_list", isinstance(ev, list))
    if candidates:
        ev_ok = bool(ev) and all(
            isinstance(e.get("locator"), str) and isinstance(e.get("quote"), str)
            for e in ev)
        check("evidence_nonempty_with_locator_quote", ev_ok)

    cov = res.get("coverage") or {}
    check("coverage_has_missing_fields",
          isinstance(cov.get("systematically_missing_fields"), dict))
    check("coverage_has_single_db",
          isinstance(cov.get("records_in_single_db_only"), list))
    check("coverage_numbers_consistent",
          cov.get("merged_record_count") == len(candidates),
          "merged=%s candidates=%s" % (cov.get("merged_record_count"), len(candidates)))


# --------------------------------------------------------------------------
# 3) 鲁棒性（隔离临时目录，不触碰正式输入）
# --------------------------------------------------------------------------

def _run_isolated(files: Dict[str, bytes]) -> Dict:
    """在临时目录构造输入并跑完整分析，返回 result；任何异常转为字符串。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        inp.mkdir()
        for rel, content in files.items():
            p = inp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        case_meta, case_errs = fs.read_case_meta(inp / "case.json")
        case_id = (case_meta or {}).get("case_id") if isinstance(case_meta, dict) else "unknown"
        skeleton = fs.build_skeleton(case_id, fs.iso_now(),
                                     (case_meta or {}).get("output_contract")
                                     if isinstance(case_meta, dict) else None)
        if case_errs:
            skeleton["errors"].extend(case_errs)
            skeleton["status"] = "input_error"
        try:
            analysis = fs.analyze_inputs(inp)
        except Exception as exc:
            return {"_crashed": "analyze: %s" % exc}
        try:
            res = fs.build_result(skeleton, case_meta, analysis)
            return res
        except Exception as exc:
            return {"_crashed": "build_result: %s" % exc}


def check_robustness() -> None:
    # 空输入目录
    r = _run_isolated({})
    check("robust_empty_input_no_crash", "_crashed" not in r)
    check("robust_empty_input_honest",
          r.get("status") in ("insufficient_evidence", "input_error"),
          str(r.get("status")))

    # 坏 JSON + 好响应 + 超大文件
    good = json.dumps({
        "message": {"items": [{
            "DOI": "10.1234/example",
            "title": ["A Robust Test Paper"],
            "author": [{"given": "A.", "family": "Author"}],
            "published-print": {"date-parts": [[2020]]},
            "container-title": ["J Robust"], "volume": "1", "page": "1-2",
        }]},
    }, ensure_ascii=False).encode("utf-8")
    r = _run_isolated({
        "case.json": json.dumps({"case_id": "t1", "research_question": "robust test paper"}).encode(),
        "bad.json": b"{ not json !! ",
        "oversize.json": b"x" * (fs.MAX_FILE_SIZE + 100),
        "good.json": good,
        "notes.md": b"hello \xff\xfe bad encoding",
    })
    check("robust_bad_json_no_crash", "_crashed" not in r, str(r.get("_crashed")))
    check("robust_oversize_skipped",
          any("oversize" in d for d in r.get("degradations", [])))
    check("robust_parsed_good_records",
          len(r.get("candidates", [])) >= 1 and r.get("coverage", {}).get("total_input_records") == 1)

    # 缺 case.json 但有材料
    r = _run_isolated({"good.json": good})
    check("robust_missing_case_no_crash", "_crashed" not in r)
    check("robust_missing_case_status",
          r.get("status") == "input_error", str(r.get("status")))


# --------------------------------------------------------------------------
# 4) 确定性
# --------------------------------------------------------------------------

def check_determinism() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "input"
        root.mkdir()
        payload = {
            "message": {"items": [
                {"DOI": "10.1/a", "title": ["Alpha Study"],
                 "author": [{"given": "X", "family": "Y"}],
                 "published": {"date-parts": [[2019]]}},
                {"DOI": "10.1/a", "title": ["Alpha Study"],
                 "author": [{"given": "X", "family": "Y"}],
                 "published": {"date-parts": [[2019]]}},
                {"title": ["Beta Study"], "author": [{"given": "Z", "family": "W"}],
                 "published": {"date-parts": [[2021]]}},
            ]},
        }
        (root / "a.json").write_text(json.dumps(payload), encoding="utf-8")
        (root / "case.json").write_text(
            json.dumps({"case_id": "det", "research_question": "alpha beta study"}),
            encoding="utf-8")
        a1 = fs.analyze_inputs(root)
        a2 = fs.analyze_inputs(root)
        key1 = json.dumps({
            "sources": a1["sources"], "records": a1["records"],
            "degradations": a1["degradations"], "errors": a1["errors"],
        }, ensure_ascii=False, sort_keys=True)
        key2 = json.dumps({
            "sources": a2["sources"], "records": a2["records"],
            "degradations": a2["degradations"], "errors": a2["errors"],
        }, ensure_ascii=False, sort_keys=True)
        check("determinism_analyze", key1 == key2)
        case_meta, _ = fs.read_case_meta(root / "case.json")
        sk = fs.build_skeleton("det", fs.iso_now(), None)
        r1 = fs.build_result(sk, case_meta, a1)
        r2 = fs.build_result(sk, case_meta, a2)
        # 去掉运行时时间戳后比较
        strip = lambda r: {k: v for k, v in r.items() if k not in ("runtime", "change_log")}
        check("determinism_result",
              json.dumps(strip(r1), ensure_ascii=False, sort_keys=True)
              == json.dumps(strip(r2), ensure_ascii=False, sort_keys=True))
        check("dedup_worked", r1["coverage"]["duplicates_merged"] == 1,
              str(r1["coverage"]["duplicates_merged"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="交付前自检")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--skip-robustness", action="store_true",
                        help="跳过隔离鲁棒性测试")
    args = parser.parse_args(argv)

    # 缺省参数检查（真正解析无参调用，而非依赖用户显式传参）
    try:
        dflt = main_mod.parse_args([])
        check("defaults_point_to_contract",
              dflt.input == "/input" and dflt.output == "/app/output"
              and dflt.work == "/app/work")
    except Exception as exc:
        check("defaults_point_to_contract", False, str(exc))

    check_contract(Path(args.output))
    check_provenance(Path(args.output))
    check_determinism()
    if not args.skip_robustness:
        check_robustness()

    failed = [c for c in CHECKS if not c[1]]
    for name, ok, detail in CHECKS:
        mark = "PASS" if ok else "FAIL"
        line = "[%s] %s" % (mark, name)
        if detail:
            line += "  (%s)" % detail
        print(line)
    print("-" * 60)
    print("total=%d pass=%d fail=%d" % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
    if failed:
        print("FAILED:", ", ".join(c[0] for c in failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
