#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检（仅标准库，确定性）。

校验内容对应评分维度：
  1. result.json 合法、含 case_id、status 合法、output_contract 无缺键；
  2. 每条候选有 provenance 与逐字 raw_snapshot；
  3. 去重/合并可解释，coverage 列明每库缺失字段与单库记录；
  4. 有候选时 evidence_records 非空，查询式与 locator 齐全；
  5. 合成输入两次运行结果一致，空输入/坏 JSON 不崩溃；
  6. 无伪造：缺失字段填 null/not_reported，缺口写 limitations/degradations。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output [--strict]
退出码 0 = 全部通过；非 0 = 存在 FAIL。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import core_impl as ci
import main as pipeline


PASS: List[str] = []
FAIL: List[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(label)
    else:
        FAIL.append(f"{label} -- {detail}".strip())


# ---------------------------------------------------------------------------
# 结果文件静态检查
# ---------------------------------------------------------------------------

def check_result_file(output_dir: Path) -> Dict[str, Any]:
    rp = output_dir / "result.json"
    check("result.json exists", rp.exists())
    if not rp.exists():
        return {}
    try:
        res = json.loads(rp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        check("result.json valid JSON", False, str(e))
        return {}
    check("result.json valid JSON", True)

    check("has case_id", bool(res.get("case_id")), str(res.get("case_id")))
    check("status valid", res.get("status") in ci.STATUSES, str(res.get("status")))

    missing = [k for k in ci.DEFAULT_CONTRACT if k not in res]
    check("output_contract keys complete", not missing, ",".join(missing))

    # 主产物字段类型。
    for k in ("candidates", "ranking", "evidence_records", "sources",
              "change_log", "errors", "degradations", "limitations"):
        check(f"{k} is list", isinstance(res.get(k), list), f"{k}={type(res.get(k)).__name__}")

    # 候选字段完整性。
    for c in res.get("candidates", []):
        check(f"candidate {c.get('record_id')} has provenance",
              bool(c.get("provenance")), str(c.get("record_id")))
        check(f"candidate {c.get('record_id')} has raw_snapshot",
              isinstance(c.get("raw_snapshot"), str) and len(c.get("raw_snapshot", "")) > 0,
              str(c.get("record_id")))
        check(f"candidate {c.get('record_id')} has field_sources",
              isinstance(c.get("field_sources"), dict), str(c.get("record_id")))

    # 证据记录非空（有候选时）。
    if res.get("candidates"):
        check("evidence_records non-empty when candidates exist", bool(res.get("evidence_records")))
        for ev in res.get("evidence_records", []):
            check(f"evidence {ev.get('evidence_id')} locator+quote",
                  bool(ev.get("locator")) and bool(ev.get("quote")),
                  str(ev.get("evidence_id")))

    # coverage 完整性。
    cov = res.get("coverage")
    if cov is not None:
        for k in ("databases", "total_input_records", "merged_record_count",
                  "duplicates_merged", "records_in_single_db_only"):
            check(f"coverage.{k} present", k in cov, str(k))

    # 无伪造检查：缺字段应写 null / 空，不能以示例填充。
    for c in res.get("candidates", []):
        if c.get("title") in (None, ""):
            check(f"candidate {c.get('record_id')} missing title recorded in missing_metadata",
                  any(mm.get("record_id") == c.get("record_id") and mm.get("field") == "title"
                      for mm in res.get("missing_metadata", [])),
                  str(c.get("record_id")))

    return res


# ---------------------------------------------------------------------------
# 合成案例自测（去重/合并/覆盖/确定性）
# ---------------------------------------------------------------------------

CROSSREF_PAYLOAD = {
    "message": {
        "query": "CRISPR genome editing",
        "items": [
            {
                "DOI": "10.1000/example.1",
                "title": ["CRISPR-Cas9 genome editing in human cells"],
                "author": [{"given": "Jane", "family": "Doe"},
                           {"given": "John", "family": "Smith"}],
                "container-title": ["Nature Methods"],
                "volume": "12", "page": "33-40",
                "published": {"date-parts": [[2015, 5, 1]]},
                "type": "journal-article",
                "publisher": "Nature Publishing",
                "abstract": "We describe a robust CRISPR-Cas9 genome editing method.",
            },
            {
                "DOI": "10.1000/example.2",
                "title": ["Off-target effects of base editors"],
                "author": [{"given": "Alice", "family": "Brown"}],
                "container-title": ["Science"],
                "volume": "360", "page": "100-105",
                "published": {"date-parts": [[2018, 2, 2]]},
                "type": "journal-article",
                "publisher": "AAAS",
            },
        ],
    },
}

EUROPEPMC_PAYLOAD = {
    "resultList": {
        "result": [
            {
                "id": "1",
                "doi": "10.1000/example.1",
                "title": "CRISPR-Cas9 genome editing in human cells",
                "authorString": "Doe J, Smith J",
                "journalTitle": "Nature Methods",
                "volume": "12",
                "pageInfo": "33-40",
                "pubYear": 2015,
                "abstractText": "We describe a robust CRISPR-Cas9 genome editing method.",
                "pmid": "25550000",
            },
        ],
    },
}


def _write_case(dirp: Path, with_case: bool = True) -> None:
    if with_case:
        (dirp / "case.json").write_text(json.dumps({
            "case_id": "synthetic-1",
            "research_question": "CRISPR genome editing",
            "queries": ["CRISPR", "genome editing", "base editor"],
            "databases": ["crossref", "europepmc"],
            "output_contract": {"fields": ["research_question", "search_scope",
                                           "candidates", "ranking", "evidence_records",
                                           "coverage", "empty_results", "conflicts",
                                           "missing_metadata", "permission_issues",
                                           "limitations", "sources", "uncertainty",
                                           "change_log", "errors", "degradations",
                                           "pending_human", "runtime"]},
        }), encoding="utf-8")
        (dirp / "task.md").write_text("# synthetic task\n\n研究问题：CRISPR genome editing\n", encoding="utf-8")
    (dirp / "crossref.json").write_text(json.dumps(CROSSREF_PAYLOAD), encoding="utf-8")
    (dirp / "europepmc.json").write_text(json.dumps(EUROPEPMC_PAYLOAD), encoding="utf-8")


def run_synthetic_checks() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        indir = base / "input"
        outdir = base / "output"
        workdir = base / "work"
        indir.mkdir()
        _write_case(indir)

        res1 = pipeline.run_pipeline(indir, outdir, workdir, started="2026-01-01T00:00:00+00:00")
        # 同一输入再跑一次（覆盖），确认确定性。
        outdir2 = base / "output2"
        res2 = pipeline.run_pipeline(indir, outdir2, base / "work2",
                                     started="2026-01-01T00:00:00+00:00")

        check("synthetic: status ok", res1.get("status") == "ok", str(res1.get("status")))
        check("synthetic: merged_record_count == 2",
              res1.get("coverage", {}).get("merged_record_count") == 2,
              str(res1.get("coverage", {}).get("merged_record_count")))
        check("synthetic: duplicates_merged == 1",
              res1.get("coverage", {}).get("duplicates_merged") == 1,
              str(res1.get("coverage", {}).get("duplicates_merged")))
        check("synthetic: total_input_records == 3",
              res1.get("coverage", {}).get("total_input_records") == 3,
              str(res1.get("coverage", {}).get("total_input_records")))

        # 合并记录的 provenance 应覆盖两个库。
        dup = [c for c in res1["candidates"] if c.get("member_count", 0) > 1]
        check("synthetic: duplicate record has 2 sources",
              dup and len(dup[0].get("provenance", [])) == 2,
              str(len(dup[0].get("provenance", [])) if dup else 0))

        # 确定性：排序、字段取值一致。
        key1 = ci.safe_json_dumps([{k: c.get(k) for k in ("record_id", "title", "year", "doi")}
                                   for c in res1["candidates"]])
        key2 = ci.safe_json_dumps([{k: c.get(k) for k in ("record_id", "title", "year", "doi")}
                                   for c in res2["candidates"]])
        check("synthetic: deterministic across runs", key1 == key2)

        # 排序只来自确定性 relevance_hint。
        check("synthetic: ranking present", len(res1.get("ranking", [])) == 2)

    # 空输入目录（无材料但有 case/task）：不崩溃，写 insufficient_evidence。
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        indir = base / "empty_input"
        indir.mkdir()
        (indir / "case.json").write_text(json.dumps({"case_id": "empty-1",
                                                      "research_question": "anything"}), encoding="utf-8")
        (indir / "task.md").write_text("# empty task\n", encoding="utf-8")
        outdir = base / "out"
        res = pipeline.run_pipeline(indir, outdir, base / "work", started="2026-01-01T00:00:00+00:00")
        check("empty input: no crash", isinstance(res, dict))
        check("empty input: status insufficient_evidence",
              res.get("status") == "insufficient_evidence", str(res.get("status")))

    # 坏 JSON：不崩溃，降级说明。
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        indir = base / "bad_input"
        indir.mkdir()
        (indir / "case.json").write_text("{not valid json", encoding="utf-8")
        (indir / "task.md").write_text("# t\n", encoding="utf-8")
        (indir / "bad.json").write_text("[]]", encoding="utf-8")
        outdir = base / "out"
        res = pipeline.run_pipeline(indir, outdir, base / "work", started="2026-01-01T00:00:00+00:00")
        check("bad json: no crash", isinstance(res, dict))
        check("bad json: degradations recorded", len(res.get("degradations", [])) > 0)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Skill 交付前自检")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--strict", action="store_true",
                   help="任何 FAIL 都导致退出码非 0")
    p.add_argument("--synthetic-only", action="store_true",
                   help="只跑合成案例，不检查 /app/output")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.synthetic_only:
        check_result_file(Path(args.output))
    run_synthetic_checks()

    print("=" * 60)
    print(f"PASS: {len(PASS)}")
    for p in PASS:
        print(f"  [PASS] {p}")
    print(f"FAIL: {len(FAIL)}")
    for f in FAIL:
        print(f"  [FAIL] {f}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
