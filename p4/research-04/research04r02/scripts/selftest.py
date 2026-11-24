#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检：契约字段、枚举、四类产物、quote 可定位、无伪造分数。

退出码：0 = 无硬错误（WARN 不阻断）；1 = 存在硬错误。
把逐项结果写入 /app/work/selftest.json，便于失败定位与恢复。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import core_impl as ci


def _check(name, ok, level, detail=""):
    return {"name": name, "ok": bool(ok), "level": level, "detail": str(detail)}


def main(argv=None):
    p = argparse.ArgumentParser(description="checkpoint skill self-check")
    p.add_argument("--input", default=ci.DEFAULT_INPUT)
    p.add_argument("--output", default=ci.DEFAULT_OUTPUT)
    p.add_argument("--work", default=ci.DEFAULT_WORK)
    args = p.parse_args(argv)

    in_dir, out_dir, work_dir = Path(args.input), Path(args.output), Path(args.work)
    checks = []
    result_path = out_dir / "result.json"

    # 1. result.json 存在且合法
    result = ci.load_json(result_path, default=None)
    if result is None or not isinstance(result, dict):
        checks.append(_check("result.json exists and parses as JSON object", False, "FAIL", str(result_path)))
        _emit(checks, work_dir)
        return 1
    checks.append(_check("result.json exists and parses as JSON object", True, "PASS"))

    # 2. case_id
    case_id = result.get("case_id")
    checks.append(_check("case_id present and non-empty", bool(case_id), "PASS" if case_id else "FAIL"))

    # 3. output_contract 全部字段回填（与 /input/case.json 比对）
    case_obj = ci.load_json(in_dir / "case.json", default={})
    contract = None
    if isinstance(case_obj, dict):
        for k in ("output_contract", "output_schema", "output"):
            if isinstance(case_obj.get(k), dict):
                contract = case_obj[k]
                break
    if isinstance(contract, dict):
        missing = [k for k in contract.keys() if k not in result]
        checks.append(_check("output_contract fields echoed back", not missing,
                             "PASS" if not missing else "FAIL",
                             "missing: %s" % ", ".join(missing)))
    else:
        checks.append(_check("output_contract fields echoed back (no contract in case.json)",
                             True, "PASS", "case.json has no output_contract dict"))

    # 4. 固定字段
    missing_fixed = [k for k in ci.FIXED_KEYS if k not in result]
    checks.append(_check("fixed fields present", not missing_fixed,
                         "PASS" if not missing_fixed else "FAIL",
                         "missing: %s" % ", ".join(missing_fixed)))

    # 5. status 枚举
    status = result.get("status")
    checks.append(_check("status in %s" % (ci.STATUS_VALUES,), status in ci.STATUS_VALUES,
                         "PASS" if status in ci.STATUS_VALUES else "FAIL", "got %r" % status))

    # 6. 四类产物结构
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    for label, obj, keys in (
        ("time_trend", tt, ("distribution", "windows", "claims")),
        ("topic_evolution", te, ("stage_divisions", "term_mapping", "events", "claims")),
        ("representative_works", rw, ("criteria", "stages")),
        ("research_gaps", rg, ("gaps", "excluded_not_gaps")),
    ):
        miss = [k for k in keys if k not in obj]
        checks.append(_check("%s has required sub-keys" % label, not miss,
                             "PASS" if not miss else "FAIL", "missing: %s" % ", ".join(miss)))

    # 7. 产物非空（status=ok 时必须齐备；否则 WARN）
    has_trend = bool(tt.get("distribution") or tt.get("claims"))
    has_evo = bool(te.get("stage_divisions") or te.get("events"))
    has_repr = bool(rw.get("stages") or rw.get("criteria"))
    has_gap = bool(rg.get("gaps"))
    products = {"time_trend": has_trend, "topic_evolution": has_evo,
                "representative_works": has_repr, "research_gaps": has_gap}
    empty = [k for k, v in products.items() if not v]
    if status == "ok" and empty:
        checks.append(_check("status=ok requires all four products non-empty", False, "FAIL",
                             "empty: %s" % ", ".join(empty)))
    elif empty:
        checks.append(_check("four products non-empty", False, "WARN",
                             "empty: %s (acceptable for partial/insufficient_evidence)" % ", ".join(empty)))
    else:
        checks.append(_check("four products non-empty", True, "PASS"))

    # 8. evidence_map 条目：id 唯一、quote 可定位、claim 有回链
    evidence = result.get("evidence_map") or []
    ids = [e.get("id") for e in evidence if isinstance(e, dict)]
    checks.append(_check("evidence ids unique", len(ids) == len(set(ids)),
                         "PASS" if len(ids) == len(set(ids)) else "FAIL",
                         "total=%d unique=%d" % (len(ids), len(set(ids)))))

    # 收集可检索文本（work/materials 下全部 txt + 输入 text 文件）
    text_pool = {}
    for p in list((work_dir / "materials").glob("*.txt")) if (work_dir / "materials").exists() else []:
        txt, _e, _t = ci.read_text_smart(p)
        text_pool[str(p)] = txt
    entries, _sk, _dg = ci.scan_input(in_dir)
    for entry in entries:
        if entry["kind"] == "text" and entry["rel"] not in ("case.json", "task.md"):
            txt, _e, _t = ci.read_text_smart(entry["path"])
            text_pool[entry["path"]] = txt

    unlocatable = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        q = (e.get("quote") or "").strip()
        src = e.get("source_file")
        if not q:
            continue  # 允许空 quote（标 not_reported）
        found = False
        if src and src in text_pool:
            found = q in text_pool[src]
        if not found:
            # 兜底：任意材料文本中命中即算可定位
            found = any(q in t for t in text_pool.values())
        if not found:
            unlocatable.append(e.get("id"))
    checks.append(_check("non-empty quotes locatable in materials", not unlocatable,
                         "PASS" if not unlocatable else "FAIL",
                         "unlocatable ids: %s" % ", ".join(map(str, unlocatable))))

    # 9. claims 的 evidence_ids 回链存在
    dangling = []
    ev_ids = {e.get("id") for e in evidence if isinstance(e, dict) and e.get("id")}
    for label, claims in (
        ("time_trend", tt.get("claims", [])),
        ("topic_evolution", te.get("claims", [])),
    ):
        for c in claims:
            for eid in c.get("evidence_ids", []):
                if eid not in ev_ids:
                    dangling.append("%s:%s" % (label, eid))
    for e in te.get("events", []):
        for eid in e.get("evidence_ids", []):
            if eid not in ev_ids:
                dangling.append("topic_evolution.event:%s" % eid)
    for s in rw.get("stages", []):
        for w in s.get("works", []):
            for eid in w.get("evidence_ids", []):
                if eid not in ev_ids:
                    dangling.append("representative_works.work:%s" % eid)
    for g in rg.get("gaps", []):
        for eid in g.get("evidence_ids", []):
            if eid not in ev_ids:
                dangling.append("research_gaps.gap:%s" % eid)
    checks.append(_check("evidence_ids resolve to evidence_map entries", not dangling,
                         "PASS" if not dangling else "FAIL", "dangling: %s" % ", ".join(dangling)))

    # 10. 无伪造评测分数/白板（启发式）
    suspicious = []
    for key in ("benchmark_score", "final_score", "score", "whiteboard_score", "白板"):
        if key in result and isinstance(result[key], (int, float)):
            suspicious.append(key)
    checks.append(_check("no fabricated benchmark/whiteboard scores", not suspicious,
                         "PASS" if not suspicious else "WARN",
                         "found: %s" % ", ".join(suspicious)))

    # 11. change_log 记录
    clog = result.get("change_log") or []
    bad_entries = [i for i, e in enumerate(clog) if not isinstance(e, dict) or "action" not in e]
    checks.append(_check("change_log has entries with action", clog and not bad_entries,
                         "PASS" if clog and not bad_entries else "WARN",
                         "entries=%d bad=%d" % (len(clog), len(bad_entries))))

    # 12. runtime 存在
    checks.append(_check("runtime present", "runtime" in result,
                         "PASS" if "runtime" in result else "WARN"))

    _emit(checks, work_dir)
    fails = [c for c in checks if c["level"] == "FAIL" and not c["ok"]]
    warns = [c for c in checks if c["level"] == "WARN" and not c["ok"]]
    print("selftest: %d checks, %d FAIL, %d WARN" % (len(checks), len(fails), len(warns)))
    for c in checks:
        flag = "PASS" if c["ok"] else c["level"]
        print("  [%s] %s%s" % (flag, c["name"], ("  -> " + c["detail"]) if c["detail"] else ""))
    return 1 if fails else 0


def _emit(checks, work_dir):
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    payload = {
        "run_at": ci.now_iso(),
        "checks": checks,
        "fail": [c for c in checks if c["level"] == "FAIL" and not c["ok"]],
        "warn": [c for c in checks if c["level"] == "WARN" and not c["ok"]],
    }
    ci.write_json_atomic(Path(work_dir) / "selftest.json", payload)


if __name__ == "__main__":
    sys.exit(main())
