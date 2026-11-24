#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-staged 自检脚本（只依赖标准库；所有场景退出码 0 视为通过）。

覆盖三类健壮性场景 + 确定性 + 证据逐字核验：
  A. 空输入目录：skeleton 落盘 partial，validate 无 error 级问题；
  B. 坏 JSON 的 case.json：不崩溃，降级记录；
  C. 缺失输入目录：不崩溃，status 可解释；
  D. 确定性：同一输入两次 skeleton 结构字段一致（忽略时间戳）；
  E. 逐字核验：quote 命中 pass、错引 fail、弃权 skip。

用法：
  python3 -B scripts/selftest.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main  # noqa: E402
import litqa  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def _strip_timestamps(data):
    d = json.loads(json.dumps(data))
    if isinstance(d, dict):
        d.pop("runtime", None)
        for item in d.get("change_log", []):
            if isinstance(item, dict):
                item.pop("time_utc", None)
    return d


def scenario_empty_dir(base):
    inp = Path(base) / "empty_in"
    out = Path(base) / "empty_out"
    work = Path(base) / "empty_work"
    inp.mkdir(parents=True, exist_ok=True)
    rc = main.main(["skeleton", "--input", str(inp), "--output", str(out), "--work", str(work)])
    ok1 = rc == 0 and (out / "result.json").exists()
    r = main.load_json_lenient(out / "result.json")[0]
    ok2 = bool(r) and r.get("status") in ("partial", "insufficient_evidence")
    ok3 = r.get("case_id") is not None
    rc_v = main.main(["validate", "--input", str(inp), "--output", str(out)])
    return ok1 and ok2 and ok3 and rc_v == 0, "empty dir skeleton+validate rc=%d/%d status=%s" % (
        rc, rc_v, r.get("status") if r else None)


def scenario_bad_json(base):
    inp = Path(base) / "bad_in"
    out = Path(base) / "bad_out"
    work = Path(base) / "bad_work"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "case.json").write_text('{"case_id": "x", "output_contract": ', encoding="utf-8")
    rc = main.main(["skeleton", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = main.load_json_lenient(out / "result.json")[0]
    has_note = any("case.json" in str(x.get("detail", "")) for x in (r or {}).get("degradations", []))
    return rc == 0 and bool(r) and has_note, "bad json degraded rc=%d" % rc


def scenario_missing_dir(base):
    inp = Path(base) / "not_exist"
    out = Path(base) / "missing_out"
    work = Path(base) / "missing_work"
    rc = main.main(["skeleton", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = main.load_json_lenient(out / "result.json")[0]
    ok = rc == 0 and bool(r) and r.get("status") in ("partial", "insufficient_evidence", "input_error")
    return ok, "missing input rc=%d status=%s" % (rc, r.get("status") if r else None)


def scenario_determinism(base):
    inp = Path(base) / "det_in"
    o1 = Path(base) / "det_out1"
    o2 = Path(base) / "det_out2"
    w = Path(base) / "det_work"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "case.json").write_text(json.dumps({
        "case_id": "det-1",
        "output_contract": {"type": "object", "properties": {"summary": {"type": "string"}}},
    }), encoding="utf-8")
    (inp / "task.md").write_text("test task", encoding="utf-8")
    (inp / "paper.txt").write_text("para one\n\npara two", encoding="utf-8")
    main.main(["skeleton", "--input", str(inp), "--output", str(o1), "--work", str(w)])
    main.main(["skeleton", "--input", str(inp), "--output", str(o2), "--work", str(w)])
    d1 = _strip_timestamps(main.load_json_lenient(o1 / "result.json")[0])
    d2 = _strip_timestamps(main.load_json_lenient(o2 / "result.json")[0])
    return d1 == d2, "deterministic skeleton (contract field summary=%r)" % d1.get("summary")


def scenario_verify(base):
    d = Path(base) / "verify"
    d.mkdir(parents=True, exist_ok=True)
    src = d / "paper.txt"
    src.write_text("We randomized 120 patients.\n\nBlinding was maintained throughout.", encoding="utf-8")
    index, errs = litqa.build_index_for_files([str(src)])
    fid = next(iter(index))
    idx_path = d / "locators.json"
    (d / "locators.json").write_text(json.dumps(
        {"files": index}, ensure_ascii=False, indent=2), encoding="utf-8")
    r = {"case_id": "v", "status": "partial", "assessment": [
        {"dimension": "研究设计", "checkpoint": "随机化", "verdict": "confirmed",
         "polarity": "favorable", "critical": True,
         "locator": "#p1s1", "quote": "We randomized 120 patients.", "basis": "text",
         "source_id": fid},
        {"dimension": "偏倚风险", "checkpoint": "盲法", "verdict": "insufficient",
         "polarity": "adverse", "critical": True,
         "locator": "", "quote": "", "basis": "text", "source_id": fid},
        {"dimension": "报告完整性", "checkpoint": "样本量", "verdict": "confirmed",
         "polarity": "favorable", "critical": False,
         "locator": "#p1s1", "quote": "totally invented sentence", "basis": "text",
         "source_id": fid},
    ], "sources": [], "uncertainty": [], "pending_human": [],
      "change_log": [], "runtime": {}, "budget": {}, "grades": {}}
    rp = d / "result.json"
    rp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    # 直接调用 verify 核心逻辑（不落盘回写，避免与 main 耦合）
    s1, _ = litqa.verify_one(index, fid, "#p1s1", "We randomized 120 patients.")
    s2, _ = litqa.verify_one(index, fid, "", "")
    s3, _ = litqa.verify_one(index, fid, "#p1s1", "totally invented sentence")
    ok = s1 == "pass" and s2 == "skip" and s3 == "fail"
    return ok, "verify pass/skip/fail = %s/%s/%s" % (s1, s2, s3)


def scenario_validate_ok_requires_assessment(base):
    out = Path(base) / "neg_out"
    out.mkdir(parents=True, exist_ok=True)
    r = {"case_id": "x", "status": "ok", "assessment": [], "sources": [],
         "uncertainty": [], "pending_human": [], "change_log": [{"time_utc": "t", "event": "e", "detail": "d"}],
         "runtime": {}}
    (out / "result.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    rc = main.main(["validate", "--input", str(base), "--output", str(out)])
    return rc == 1, "validate flags ok-without-assessment (rc=%d)" % rc


def main_selftest():
    base = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))
    scenarios = [
        ("empty_input", scenario_empty_dir),
        ("bad_json", scenario_bad_json),
        ("missing_input", scenario_missing_dir),
        ("determinism", scenario_determinism),
        ("quote_verify", scenario_verify),
        ("validate_negative", scenario_validate_ok_requires_assessment),
    ]
    for name, fn in scenarios:
        try:
            ok, detail = fn(base)
            record(name, ok, detail)
        except Exception as exc:  # noqa: BLE001
            record(name, False, "exception: %r" % exc)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\nselftest: %d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("failed: %s" % ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main_selftest())
