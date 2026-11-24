# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 入口脚本（仅标准库，逻辑见 core_impl.py）。

三种模式：
  scaffold  发现输入、抽取文本、按页/段落切分语料，落盘 result.json 骨架 + report.md 骨架
            （先交一版合法可解析的 partial 结果，之后只做增量改进）
  update    把 Agent 逐批产出的 findings/claims/commitments/coverage 合并进 result.json，
            并同步重建 report.md（避免整份 JSON 手写出错）
  selfcheck 校验 result.json 与 report.md 的合法性，打印 OK 或 FAIL（退出码区分）

调用示例（路径相对 SKILL.md 所在根目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update --output /app/output --work /app/work \
      --findings /app/work/findings.json --claims /app/work/claims.json --status partial
  python3 -B scripts/main.py selfcheck --output /app/output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_impl import (
    COVERAGE_DIMS,
    COVERAGE_STATUSES,
    RESULT_STATUSES,
    atomic_write_text,
    build_skeleton,
    derive_case_id,
    discover_inputs,
    extract_contract,
    load_json_list,
    load_summary,
    load_task_meta,
    normalize_items,
    now_iso,
    pdf_to_text,
    read_json_tolerant,
    read_text_safe,
    render_report,
    split_paragraphs,
    write_corpus_md,
    write_json_atomic,
    write_meta,
)


def _reconfigure_streams() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def cmd_scaffold(args: argparse.Namespace) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    result_path = out / "result.json"
    if result_path.exists() and not getattr(args, "force", False):
        print("result.json 已存在，跳过 scaffold（如需重建请加 --force）。")
        return 0

    discovered, scan_err = discover_inputs(str(inp))
    errors: List[Dict[str, Any]] = []
    degradations: List[Dict[str, Any]] = []

    task_meta = load_task_meta(discovered)
    if task_meta.get("_parse_error"):
        errors.append({"source": discovered.get("case_json"),
                       "error": task_meta["_parse_error"]})
    case_id = derive_case_id(discovered, task_meta)
    contract = extract_contract(discovered)

    sources: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []

    for p in discovered["materials"]:
        ext = Path(p).suffix.lower()
        entry: Dict[str, Any] = {"path": p, "kind": "material",
                                 "n_pages": None, "chars": 0, "error": None}
        recs: List[Dict[str, Any]] = []
        if ext in {".pdf"}:
            r, err = pdf_to_text(p, str(work))
            if r is not None and not r["error"]:
                page_text = r["text"]
                n_pages = page_text.count("\f") + 1 if page_text.strip() else 0
                recs = split_paragraphs(page_text, p, has_pages=True)
                entry.update(n_pages=n_pages, chars=r["chars"])
            else:
                entry["error"] = err
                degradations.append({"source": p, "issue": err})
        else:
            r = read_text_safe(p)
            if r["error"]:
                entry["error"] = r["error"]
                degradations.append({"source": p, "issue": r["error"]})
            else:
                recs = split_paragraphs(r["text"], p, has_pages=False)
                entry.update(n_pages=1, chars=r["chars"])
        if recs:
            records.extend(recs)
        elif not entry["error"]:
            entry["error"] = "无文本层或抽取为空"
            degradations.append({"source": p, "issue": entry["error"]})
        sources.append(entry)

    # case.json / task.md 也计入 sources，便于追溯任务来源
    for p, kind in ((discovered.get("case_json"), "task"),
                    (discovered.get("task_md"), "task")):
        if p:
            r = read_text_safe(p, max_bytes=200_000)
            sources.append({"path": p, "kind": kind, "n_pages": 1,
                            "chars": r["chars"], "error": r["error"]})

    if scan_err:
        errors.append({"source": str(inp), "error": scan_err})

    if not inp.exists():
        status = "input_error"
    elif not records and not discovered["materials"]:
        status = "insufficient_evidence"
    else:
        status = "partial"

    result = build_skeleton(case_id, task_meta, contract, sources, records,
                            status, errors, degradations)
    write_json_atomic(str(result_path), result)
    write_corpus_md(records, sources, str(work / "corpus.md"))
    write_meta(str(work / "meta.json"), discovered, task_meta,
               len(records), case_id, status)
    atomic_write_text(str(out / "report.md"), render_report(result))

    print("scaffold done: case_id=%s status=%s sources=%d paragraphs=%d" % (
        case_id, status, len(sources), len(records)))
    for d in degradations:
        print("  degradation: %s -> %s" % (d.get("source"), d.get("issue")))
    for e in errors:
        print("  error: %s" % json.dumps(e, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def cmd_update(args: argparse.Namespace) -> int:
    out = Path(args.output)
    result_path = out / "result.json"
    if not result_path.exists():
        print("错误：result.json 不存在，请先运行 scaffold。", file=sys.stderr)
        return 1
    data, err = read_json_tolerant(str(result_path))
    if err is not None or not isinstance(data, dict):
        print("错误：result.json 解析失败：%s" % err, file=sys.stderr)
        return 1

    review = data.setdefault("review", {})
    if not isinstance(review, dict):
        review = data["review"] = {}
    changes: List[str] = []

    for attr, key in (("findings", "findings"), ("claims", "claims"),
                      ("commitments", "commitments")):
        path = getattr(args, attr, None)
        if not path:
            continue
        arr, e = load_json_list(path)
        if e is None:
            review[key] = normalize_items(arr, key)
            changes.append("%s=%d" % (key, len(review[key])))
        else:
            data.setdefault("errors", []).append({"source": path, "error": e})
            data.setdefault("degradations", []).append({"source": path, "issue": e})
            changes.append("%s read failed (%s)" % (key, e))

    if getattr(args, "coverage", None):
        cov, e = read_json_tolerant(args.coverage)
        if e is None and isinstance(cov, dict):
            cur = review.setdefault("coverage", {})
            for k in COVERAGE_DIMS:
                v = cov.get(k)
                if isinstance(v, dict):
                    cur[k] = {
                        "status": v.get("status", "skipped"),
                        "checked": int(v.get("checked", 0) or 0),
                        "note": v.get("note", ""),
                    }
            changes.append("coverage merged")
        else:
            data.setdefault("errors", []).append({"source": args.coverage, "error": e})

    if getattr(args, "summary", None):
        summary = load_summary(args.summary)
        if summary is not None:
            review["summary"] = summary
            changes.append("summary updated")

    if getattr(args, "status", None):
        data["status"] = args.status
        changes.append("status=%s" % args.status)

    for attr in ("uncertainty", "pending_review", "pending_human", "limitations"):
        path = getattr(args, attr, None)
        if not path:
            continue
        arr, e = load_json_list(path)
        if e is None:
            data[attr] = arr
            changes.append("%s=%d" % (attr, len(arr)))
        else:
            data.setdefault("errors", []).append({"source": path, "error": e})

    if not changes:
        changes.append("no-op")
    data.setdefault("change_log", []).append({
        "ts": now_iso(), "event": "incremental_update", "note": "; ".join(changes),
    })
    write_json_atomic(str(result_path), data)
    atomic_write_text(str(out / "report.md"), render_report(data))
    print("updated: %s" % "; ".join(changes))
    return 0


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------
def cmd_selfcheck(args: argparse.Namespace) -> int:
    out = Path(args.output)
    errors: List[str] = []
    warnings: List[str] = []
    result_path = out / "result.json"
    if not result_path.exists():
        print("selfcheck FAIL: result.json 缺失")
        return 1
    data, err = read_json_tolerant(str(result_path))
    if err is not None or not isinstance(data, dict):
        print("selfcheck FAIL: result.json 不可解析：%s" % err)
        return 1

    for k in ("case_id", "status", "review", "sources", "uncertainty", "change_log"):
        if k not in data:
            errors.append("缺少键 %s" % k)
    if not data.get("case_id"):
        errors.append("case_id 为空")
    if data.get("status") not in RESULT_STATUSES:
        errors.append("status 非法：%r" % data.get("status"))

    review = data.get("review")
    if not isinstance(review, dict):
        errors.append("review 不是对象")
    else:
        for k in ("summary", "findings", "claims", "commitments", "coverage", "section_map"):
            if k not in review:
                errors.append("review 缺少键 %s" % k)
        findings = review.get("findings") or []
        if not isinstance(findings, list):
            errors.append("findings 不是数组")
        else:
            for f in findings:
                if not isinstance(f, dict):
                    warnings.append("存在非对象 finding")
                    continue
                if not f.get("locator") or not f.get("quote"):
                    if f.get("status") == "confirmed":
                        errors.append("%s confirmed 但缺 locator/quote" % f.get("id"))
                    else:
                        warnings.append("%s 缺 locator 或 quote（status=%s）" % (
                            f.get("id"), f.get("status")))
        cov = review.get("coverage") or {}
        if not isinstance(cov, dict):
            errors.append("coverage 不是对象")
        else:
            for k in COVERAGE_DIMS:
                v = cov.get(k)
                if not isinstance(v, dict) or v.get("status") not in COVERAGE_STATUSES:
                    errors.append("coverage.%s 缺失或 status 非法" % k)

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        if data.get("status") in ("insufficient_evidence", "input_error"):
            warnings.append("sources 为空，与 status=%s 相符" % data.get("status"))
        else:
            errors.append("sources 为空但 status=%s" % data.get("status"))

    if not (out / "report.md").exists():
        errors.append("report.md 缺失")

    contract = data.get("output_contract")
    if isinstance(contract, dict):
        for k in contract:
            if k not in data:
                errors.append("output_contract 键 %s 未回填" % k)

    if errors:
        print("selfcheck FAIL:")
        for e in errors:
            print("  - " + e)
        for w in warnings:
            print("  ! " + w)
        return 1
    print("selfcheck OK")
    for w in warnings:
        print("  warning: " + w)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘结果骨架")
    s1.add_argument("--input", default="/input")
    s1.add_argument("--output", default="/app/output")
    s1.add_argument("--work", default="/app/work")
    s1.add_argument("--force", action="store_true", help="覆盖已存在的 result.json")

    s2 = sub.add_parser("update", help="合并 findings/claims 等增量更新")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--findings", default=None)
    s2.add_argument("--claims", default=None)
    s2.add_argument("--commitments", default=None)
    s2.add_argument("--coverage", default=None)
    s2.add_argument("--summary", default=None)
    s2.add_argument("--uncertainty", default=None)
    s2.add_argument("--pending_review", default=None)
    s2.add_argument("--pending_human", default=None)
    s2.add_argument("--limitations", default=None)
    s2.add_argument("--status", default=None, choices=RESULT_STATUSES)

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default="/app/output")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_streams()
    args = _argparse().parse_args(argv)
    if args.mode == "scaffold":
        return cmd_scaffold(args)
    if args.mode == "update":
        return cmd_update(args)
    if args.mode == "selfcheck":
        return cmd_selfcheck(args)
    _argparse().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
