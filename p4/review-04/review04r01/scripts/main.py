# -*- coding: utf-8 -*-
"""main.py — 证据溯源论文审查 Skill 的入口脚本（纯标准库）。

三种模式：
  scaffold  发现输入、抽取文本、切分语料，落盘 result.json 骨架 + report.md 骨架（先交一版合法 partial）
  update    把 Agent 产出的 findings/claims/commitments/evidence/coverage 合并进 result.json（增量改进）
  selfcheck 校验 result.json 与 report.md 的合法性、证据映射双向一致性、覆盖维度完整性

调用示例（路径相对 SKILL.md 所在目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update --output /app/output --work /app/work \
      --review /app/work/batch.json --status partial
  python3 -B scripts/main.py selfcheck --output /app/output
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import review_core as rc


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="证据溯源论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    def add_common(sp):
        sp.add_argument("--input", default="/input", help="输入目录（含 case.json/task.md/materials）")
        sp.add_argument("--output", default="/app/output", help="最终产物目录")
        sp.add_argument("--work", default="/app/work", help="中间工作目录")

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘 result.json/report.md 骨架")
    add_common(s1)

    s2 = sub.add_parser("update", help="增量合并审查结果")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--review", default=None, help="部分 review 对象 JSON 文件（可含 findings/claims/commitments/evidence_map/coverage/summary）")
    s2.add_argument("--findings", default=None, help="findings JSON 数组文件")
    s2.add_argument("--claims", default=None, help="claims JSON 数组文件")
    s2.add_argument("--commitments", default=None, help="commitments JSON 数组文件")
    s2.add_argument("--evidence", default=None, help="evidence_map.items JSON 数组文件")
    s2.add_argument("--coverage", default=None, help="coverage 对象 JSON 文件（六维状态）")
    s2.add_argument("--summary", default=None, help="review.summary 文本或 @文件路径")
    s2.add_argument("--status", default=None, choices=rc.RESULT_STATUSES)
    s2.add_argument("--limitation", action="append", default=None, help="追加 limitations（可重复）")
    s2.add_argument("--pending", action="append", default=None, help="追加 pending_review（可重复）")

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default="/app/output")

    return p


def _read_json(path) -> Optional[Any]:
    data, err = rc.read_json_tolerant(path)
    if err:
        return None
    return data


def _load_list(path) -> Optional[List[Any]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data = _read_json(p)
    return data if isinstance(data, list) else None


def _load_obj(path) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data = _read_json(p)
    return data if isinstance(data, dict) else None


def _merge_by_id(dst: List[Any], incoming: List[Any]) -> List[Any]:
    merged: Dict[str, Any] = {}
    for item in dst + incoming:
        if isinstance(item, dict) and item.get("id"):
            merged[item["id"]] = item
    return [merged[k] for k in sorted(merged)]


def _merge_review(rev: Dict[str, Any], batch: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("findings", "claims", "commitments"):
        if isinstance(batch.get(key), list):
            rev[key] = _merge_by_id(rev.get(key) or [], batch[key])
    if isinstance(batch.get("evidence_map"), dict):
        em = rev.setdefault("evidence_map", rc.empty_evidence_map())
        incoming_em = batch["evidence_map"]
        if isinstance(incoming_em.get("items"), list):
            em["items"] = _merge_by_id(em.get("items") or [], incoming_em["items"])
        if isinstance(incoming_em.get("conflicts"), list):
            # 冲突按 id 去重合并
            existing = {c.get("id"): c for c in (em.get("conflicts") or []) if isinstance(c, dict)}
            for c in incoming_em["conflicts"]:
                if isinstance(c, dict) and c.get("id"):
                    existing[c["id"]] = c
            em["conflicts"] = [existing[k] for k in sorted(existing)]
    if isinstance(batch.get("coverage"), dict):
        cov = rev.setdefault("coverage", {})
        for d, v in batch["coverage"].items():
            if d in rc.DIMENSIONS and isinstance(v, dict):
                cur = cov.setdefault(d, {})
                for k in ("status", "checked", "note"):
                    if k in v:
                        cur[k] = v[k]
    if isinstance(batch.get("summary"), str) and batch["summary"].strip():
        rev["summary"] = batch["summary"].strip()
    rc.rebuild_evidence_indexes(rev)
    return rev


def cmd_scaffold(args) -> int:
    rc._configure_stdio()
    t0 = time.time()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        result = rc.skeleton_result("unknown_case", {"challenge_id": None, "task_title": None}, {}, [], [])
        result["status"] = "input_error"
        result["errors"].append("input_dir_missing: %s" % input_dir)
        result["change_log"].append({"ts": rc.iso_now(), "event": "skeleton_written", "note": "input 目录不存在"})
        rc.atomic_write_json(output_dir / "result.json", result)
        rc.atomic_write_text(output_dir / "report.md", rc.render_report(result))
        (work_dir / "corpus.md").write_text("# 语料库\n\n输入目录不存在，无可审查材料。\n", encoding="utf-8")
        rc.atomic_write_json(work_dir / "meta.json", {"case_id": "unknown_case", "input_missing": True})
        print("scaffold done status=input_error input_missing=%s" % input_dir)
        return 0

    discovered = rc.discover_input(input_dir)
    task_meta = rc.load_task_meta(discovered)
    case_id = rc.derive_case_id(discovered, task_meta)
    contract = rc.contract_extra(discovered)
    sources, records = rc.build_sources_and_records(discovered, work_dir)
    rc.write_corpus_md(records, sources, work_dir / "corpus.md")
    rc.atomic_write_json(work_dir / "meta.json", {
        "case_id": case_id, "task": task_meta,
        "n_records": len(records), "n_sources": len(sources),
    })
    result = rc.skeleton_result(case_id, task_meta, contract, sources, records)
    for v in rc.task_file_issues(discovered):
        if v not in result["degradations"]:
            result["degradations"].append(v)
    rc.atomic_write_json(output_dir / "result.json", result)
    rc.atomic_write_text(output_dir / "report.md", rc.render_report(result))
    print("scaffold done status=%s case=%s sources=%d paras=%d elapsed=%.2fs" % (
        result["status"], case_id, len(sources), len(records), time.time() - t0))
    return 0


def cmd_update(args) -> int:
    rc._configure_stdio()
    t0 = time.time()
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"

    data = _read_json(result_path)
    if not isinstance(data, dict):
        data = rc.skeleton_result("unknown_case", {"challenge_id": None, "task_title": None}, {}, [], [])
        data["status"] = "partial"
    rev = data.setdefault("review", {})

    review_batch = _load_obj(args.review) or {}
    if review_batch:
        rev = _merge_review(rev, review_batch)
    findings = _load_list(args.findings)
    if findings is not None:
        rev["findings"] = _merge_by_id(rev.get("findings") or [], findings)
    claims = _load_list(args.claims)
    if claims is not None:
        rev["claims"] = _merge_by_id(rev.get("claims") or [], claims)
    commitments = _load_list(args.commitments)
    if commitments is not None:
        rev["commitments"] = _merge_by_id(rev.get("commitments") or [], commitments)
    evidence = _load_list(args.evidence)
    if evidence is not None:
        em = rev.setdefault("evidence_map", rc.empty_evidence_map())
        em["items"] = _merge_by_id(em.get("items") or [], evidence)
    coverage = _load_obj(args.coverage)
    if coverage is not None:
        rev["coverage"] = _merge_review({"coverage": rev.get("coverage") or {}}, {"coverage": coverage})["coverage"]
    data["review"] = rev
    rc.rebuild_evidence_indexes(rev)

    if args.summary:
        summary = args.summary
        sp = Path(summary)
        if summary.startswith("@") and Path(summary[1:]).exists():
            summary = Path(summary[1:]).read_text(encoding="utf-8", errors="replace")
        elif sp.exists():
            summary = sp.read_text(encoding="utf-8", errors="replace")
        rev["summary"] = summary.strip()
    if args.status:
        data["status"] = args.status
    if args.limitation:
        lim = data.setdefault("limitations", [])
        for v in args.limitation:
            if v not in lim:
                lim.append(v)
    if args.pending:
        pend = data.setdefault("pending_review", [])
        for v in args.pending:
            if v not in pend:
                pend.append(v)

    issues = rc.validate_result(data)
    if issues:
        errs = data.setdefault("errors", [])
        for v in issues[:20]:
            if v not in errs:
                errs.append(v)

    n_f = len(rev.get("findings") or [])
    n_c = len(rev.get("claims") or [])
    n_e = len((rev.get("evidence_map") or {}).get("items") or [])
    data["change_log"].append({
        "ts": rc.iso_now(),
        "event": "incremental_update",
        "note": "merged review batch; findings=%d claims=%d evidence=%d; validation_issues=%d" % (
            n_f, n_c, n_e, len(issues)),
    })
    data["runtime"]["seconds_elapsed"] = round(time.time() - t0, 2)
    rc.atomic_write_json(result_path, data)
    rc.atomic_write_text(output_dir / "report.md", rc.render_report(data))
    print("update done status=%s findings=%d claims=%d evidence=%d issues=%d" % (
        data["status"], n_f, n_c, n_e, len(issues)))
    return 0


def cmd_selfcheck(args) -> int:
    rc._configure_stdio()
    output_dir = Path(args.output)
    result_path = output_dir / "result.json"
    data = _read_json(result_path)
    if not isinstance(data, dict):
        print("SELFCHECK FAIL: result.json 缺失或不可解析: %s" % result_path)
        return 0
    issues = rc.validate_result(data)
    report_path = output_dir / "report.md"
    if not report_path.exists():
        issues.append("report.md 缺失: %s" % report_path)
    # 校验证据引用的 quote 是否能在 sources 中（此处只做结构校验，不强制全文检索）
    if issues:
        print("SELFCHECK ISSUES (%d):" % len(issues))
        for v in issues[:50]:
            print("  - %s" % v)
        print("SELFCHECK SUMMARY: %d issue(s), status=%s" % (len(issues), data.get("status")))
    else:
        print("OK  case=%s status=%s findings=%d evidence=%d" % (
            data.get("case_id"), data.get("status"),
            len((data.get("review") or {}).get("findings") or []),
            len(((data.get("review") or {}).get("evidence_map") or {}).get("items") or [])))
    return 0


def run(argv: List[str]) -> int:
    parser = _argparse()
    args = parser.parse_args(argv)
    if args.mode == "scaffold":
        return cmd_scaffold(args)
    if args.mode == "update":
        return cmd_update(args)
    if args.mode == "selfcheck":
        return cmd_selfcheck(args)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
