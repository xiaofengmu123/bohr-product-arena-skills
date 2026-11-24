#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检与确定性校验（仅标准库）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output
  python3 -B scripts/selftest.py --input /input --output /app/output --skip-determinism

校验内容（对应评分维度）：
  1. result.json 合法、含 case_id、status 合法、契约字段无缺键
  2. 每条候选有 provenance（库—查询—原始响应—映射—去重—合并可回溯）与逐字 raw_snapshots
  3. 去重/合并/冲突可解释；coverage 与去重数字自洽
  4. 有候选时 evidence_records 非空、locator/quote 齐全
  5. 同输入两次运行内容一致（content_fingerprint 与去除易变字段后的结果全等）
  6. 空输入 / 坏 JSON / 缺 case.json 不崩溃且产出合法 result.json

退出码 0=全部 PASS；任何 FAIL 退出码 1。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import fedcore as fs  # noqa: E402
import main as pipeline  # noqa: E402

CANONICAL_KEYS = [
    "record_id", "title", "authors", "year", "journal", "volume", "issue", "pages",
    "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher",
    "identifiers", "field_sources", "provenance", "relevance_hint",
]


class Checker:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.passes: List[str] = []

    def check(self, cond: bool, label: str, detail: str = "") -> None:
        if cond:
            self.passes.append(label)
        else:
            self.failures.append(label + ((" -- " + detail) if detail else ""))

    def report(self, title: str) -> None:
        print("\n== %s ==" % title)
        for p in self.passes:
            print("  [PASS] %s" % p)
        for f in self.failures:
            print("  [FAIL] %s" % f)


def _load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def check_contract(output_dir: Path) -> Checker:
    c = Checker()
    result_path = output_dir / "result.json"
    c.check(result_path.exists(), "result.json 存在", str(result_path))
    if not result_path.exists():
        c.report("输出契约")
        return c
    res = _load_json(result_path)
    c.check(isinstance(res, dict), "result.json 是合法 JSON 对象")
    if not isinstance(res, dict):
        c.report("输出契约")
        return c
    c.check(bool(res.get("case_id")), "含 case_id (%s)" % res.get("case_id"))
    c.check(res.get("status") in fs.STATUSES, "status 合法 (%s)" % res.get("status"))
    for k in fs.FIXED_FIELDS:
        c.check(k in res, "契约字段存在: %s" % k)
    # output_contract 未知键已回填为 null/[]
    for k, v in res.items():
        if k not in fs.FIXED_FIELDS:
            c.check(v in (None, [], {}), "额外契约键回填为空值: %s" % k, "got %r" % v if v not in (None, [], {}) else "")
    c.report("1. 输出契约")
    return c


def check_candidates(res: Dict) -> Checker:
    c = Checker()
    candidates = res.get("candidates") or []
    c.check(isinstance(candidates, list), "candidates 为列表 (%d 条)" % len(candidates))
    for m in candidates[:50]:
        rid = m.get("record_id")
        c.check(bool(rid), "候选含 record_id: %s" % rid)
        for k in CANONICAL_KEYS:
            c.check(k in m, "候选含字段 %s (%s)" % (k, rid))
        prov = m.get("provenance") or {}
        snapshots = prov.get("raw_snapshots") or []
        c.check(bool(prov.get("sources")), "provenance.sources 非空 (%s)" % rid)
        c.check(bool(snapshots), "provenance.raw_snapshots 非空 (%s)" % rid)
        for s in snapshots:
            c.check(isinstance(s.get("raw_snapshot"), str) and bool(s["raw_snapshot"]),
                    "raw_snapshot 为逐字字符串 (%s)" % rid)
    c.report("2. 候选溯源")
    return c


def check_coverage(res: Dict) -> Checker:
    c = Checker()
    cov = res.get("coverage") or {}
    total = cov.get("total_input_records")
    merged = cov.get("merged_record_count")
    dup = cov.get("duplicates_merged")
    c.check(isinstance(total, int) and total >= 0, "total_input_records 合法 (%s)" % total)
    c.check(isinstance(merged, int) and merged >= 0, "merged_record_count 合法 (%s)" % merged)
    c.check(isinstance(dup, int) and dup == total - merged,
            "duplicates_merged = total - merged (%s = %s - %s)" % (dup, total, merged))
    c.check(len(res.get("candidates") or []) == merged, "candidates 数量 == merged_record_count")
    per_db = cov.get("per_database") or []
    c.check(isinstance(per_db, list), "coverage.per_database 存在 (%d 库)" % len(per_db))
    for d in per_db:
        c.check("fields_missing" in d and isinstance(d["fields_missing"], list),
                "库 %s 有 fields_missing" % d.get("db"))
    c.check("systematically_missing_fields" in cov, "有 systematically_missing_fields")
    c.report("3. 去重合并与覆盖")
    return c


def check_evidence(res: Dict) -> Checker:
    c = Checker()
    ev = res.get("evidence_records") or []
    has_candidates = bool(res.get("candidates"))
    c.check(isinstance(ev, list), "evidence_records 为列表 (%d 条)" % len(ev))
    if has_candidates:
        c.check(bool(ev), "有候选时 evidence_records 非空")
        for e in ev[:30]:
            c.check(bool(e.get("locator")), "证据 locator 齐全 (%s)" % e.get("evidence_id"))
            c.check(isinstance(e.get("quote"), str) and bool(e["quote"]),
                    "证据 quote 逐字非空 (%s)" % e.get("evidence_id"))
    c.report("4. 证据记录")
    return c


def run_pipeline_to_dict(input_dir: Path) -> Dict:
    tmp = Path(tempfile.mkdtemp(prefix="fedselftest_"))
    out = tmp / "output"
    work = tmp / "work"
    res = pipeline.run_pipeline(input_dir, out, work, decisions_path=None, skeleton_only=False)
    return res


def check_determinism(input_dir: Path) -> Checker:
    c = Checker()
    res1 = run_pipeline_to_dict(input_dir)
    res2 = run_pipeline_to_dict(input_dir)
    fp1 = (res1.get("runtime") or {}).get("content_fingerprint")
    fp2 = (res2.get("runtime") or {}).get("content_fingerprint")
    c.check(bool(fp1) and fp1 == fp2, "两次运行 content_fingerprint 一致 (%s)" % fp1)
    stripped1 = fs._strip_volatile(res1)
    stripped2 = fs._strip_volatile(res2)
    c.check(stripped1 == stripped2, "去除易变时刻字段后结果全等",
            "fingerprint1=%s fingerprint2=%s" % (fp1, fp2))
    c.report("5. 确定性（同输入两次运行）")
    return c


def check_robustness() -> Checker:
    c = Checker()
    base = Path(tempfile.mkdtemp(prefix="fedrobust_"))

    empty_dir = base / "empty"
    empty_dir.mkdir()
    res = run_pipeline_to_dict(empty_dir)
    c.check(res.get("status") in fs.STATUSES, "空输入不崩溃，status=%s" % res.get("status"))
    c.check(res.get("status") in ("input_error", "insufficient_evidence"),
            "空输入状态诚实（input_error/insufficient_evidence）", res.get("status"))

    bad_dir = base / "badjson"
    bad_dir.mkdir()
    (bad_dir / "case.json").write_text("{ this is not json !!", encoding="utf-8")
    res = run_pipeline_to_dict(bad_dir)
    c.check(res.get("status") == "input_error", "坏 JSON case.json -> input_error", res.get("status"))

    missing_dir = base / "missing"
    missing_dir.mkdir()
    res = run_pipeline_to_dict(missing_dir)
    c.check(res.get("status") == "input_error", "缺 case.json -> input_error", res.get("status"))
    shutil.rmtree(base, ignore_errors=True)
    c.report("6. 异常降级")
    return c


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(prog="selftest.py",
                                 description="交付前自检与确定性校验")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--skip-determinism", action="store_true")
    args = ap.parse_args()

    output_dir = Path(args.output)
    input_dir = Path(args.input)

    checkers: List[Checker] = []
    c_contract = check_contract(output_dir)
    checkers.append(c_contract)
    res = _load_json(output_dir / "result.json")
    if isinstance(res, dict):
        checkers.append(check_candidates(res))
        checkers.append(check_coverage(res))
        checkers.append(check_evidence(res))
    if not args.skip_determinism:
        if input_dir.exists():
            checkers.append(check_determinism(input_dir))
        else:
            print("  [WARN] 跳过确定性：--input 不存在")
    checkers.append(check_robustness())

    passes = 0
    failures = 0
    for c in checkers:
        passes += len(c.passes)
        failures += len(c.failures)
    print("\n===============")
    print("PASS %d / FAIL %d" % (passes, failures))
    if failures:
        print("以下条目 FAIL：")
        for c in checkers:
            for f in c.failures:
                print("  - %s" % f)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())