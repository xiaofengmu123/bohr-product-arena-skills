#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — checkpoint Skill 的命令行编排入口。

子命令（无第三方依赖，Linux/Windows 通用）：
  discover   阶段 0：扫描 /input，写骨架 result.json + manifest.json + budget.json
  baseline   阶段 1：确定性机械抽取，写 _baseline 与 work/baseline.json
  commit     阶段 2/3：把 Agent 核验后的增量 JSON 原子合并进 result.json
  checkpoint 阶段闸门：更新 budget.json 账本并落盘快照
  report     阶段 4：从 result.json 渲染 report.md / CSV / evidence_map.json
  finalize   阶段 5：兜底 status、runtime、缺省字段，收尾 change_log

默认路径：--input /input --output /app/output --work /app/work。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import core_impl as ci


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="research-frontier-trend-checkpoint Skill orchestrator")
    p.add_argument("--input", default=ci.DEFAULT_INPUT, help="input dir (default /input)")
    p.add_argument("--output", default=ci.DEFAULT_OUTPUT, help="output dir (default /app/output)")
    p.add_argument("--work", default=ci.DEFAULT_WORK, help="work dir (default /app/work)")
    p.add_argument("--version", action="store_true", help="print version and exit")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("discover", help="scan input, write skeleton + manifest + budget ledger")
    sp.add_argument("--force", action="store_true", help="overwrite existing skeleton")

    sp = sub.add_parser("baseline", help="deterministic mechanical extraction")

    sp = sub.add_parser("commit", help="merge a verified analysis patch into result.json")
    sp.add_argument("--file", required=True, help="path to JSON patch (work/patch.json)")
    sp.add_argument("--phase", default="deep_analysis", help="phase label for change_log")
    sp.add_argument("--machine", dest="machine", action="store_true",
                    help="mark _meta.machine=True (machine-generated merge)")
    sp.add_argument("--no-machine", dest="machine", action="store_false")
    sp.set_defaults(machine=False)

    sp = sub.add_parser("checkpoint", help="update budget ledger and snapshot result.json")
    sp.add_argument("--phase", required=True, help="phase id: discover|baseline|deep_analysis|selfcheck|finalize")
    sp.add_argument("--status", required=True, choices=("pending", "running", "done", "skipped"),
                    help="phase status to record")
    sp.add_argument("--requests", type=int, default=None, help="model requests used in this phase")

    sp = sub.add_parser("report", help="render editable report.md / CSV / evidence_map.json")

    sp = sub.add_parser("finalize", help="finalize status/runtime and close change_log")

    sp = sub.add_parser("selftest-hint", help="print pointer to scripts/selftest.py")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# 阶段 0：discover
# ---------------------------------------------------------------------------
def cmd_discover(args):
    in_dir, out_dir, work_dir = Path(args.input), Path(args.output), Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    mat_dir = work_dir / "materials"
    mat_dir.mkdir(parents=True, exist_ok=True)

    result_path = out_dir / "result.json"
    manifest_path = work_dir / "manifest.json"
    if result_path.exists() and not args.force:
        print("[discover] result.json exists; use --force to overwrite", file=sys.stderr)
        return 2

    entries, skipped, degradations = ci.scan_input(in_dir)
    case_obj = ci.load_json(in_dir / "case.json", default={})
    task_text = ""
    for e in entries:
        if e["path"].lower().endswith("task.md") or e["rel"].lower() == "task.md":
            task_text, _, _ = ci.read_text_smart(e["path"])
            break
    case_id, contract, question = ci.extract_case_meta(case_obj, task_text)

    # 归一材料：text 类文件抽取纯文本到 work/materials/；pdf/office 只登记，待平台工具抽取
    documents, errors = [], []
    seq = 0
    for e in entries:
        rel = e["rel"]
        if rel in ("case.json", "task.md", "output_contract.json", "resources.yaml"):
            continue
        seq += 1
        doc_id = "mat_%03d" % seq
        item = {
            "doc_id": doc_id, "original": e["path"], "rel": rel, "kind": e["kind"],
            "extracted": None, "needs_extraction": e["kind"] in ("pdf", "office"),
            "metadata": None,
        }
        if e["kind"] == "text":
            try:
                text, enc, _tr = ci.read_text_smart(e["path"])
                if e["ext"] == ".json":
                    parsed = ci.load_json(e["path"], default=None)
                    if isinstance(parsed, dict):
                        item["metadata"] = parsed
                        # 记录可读的正文摘要字段，避免丢失结构化元数据
                dest = mat_dir / (doc_id + ".txt")
                dest.write_text(text, encoding="utf-8")
                item["extracted"] = str(dest)
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": e["path"], "reason": "read_failed: %s" % exc})
        documents.append(item)

    manifest = {
        "input_dir": str(in_dir),
        "case_file": str(in_dir / "case.json") if (in_dir / "case.json").exists() else None,
        "task_file": str(in_dir / "task.md") if (in_dir / "task.md").exists() else None,
        "case_id": case_id,
        "output_contract": contract,
        "research_question_hint": question,
        "documents": documents,
        "skipped": skipped,
        "errors": errors,
        "degradations": degradations,
        "created_at": ci.now_iso(),
    }
    ci.write_json_atomic(manifest_path, manifest)

    result = ci.build_skeleton(case_id, contract, question)
    result["_meta"]["revision"] = 1
    result["runtime"]["phases"] = _phase_snapshot()
    ci.append_change(result, "discover", "write partial skeleton before any analysis",
                     before="(empty)", after="skeleton + manifest + budget")
    for d in documents:
        result["sources"].append({
            "id": d["doc_id"], "path": d["rel"], "title": None, "year": None,
            "type": d["kind"], "extracted": d["extracted"],
        })
    for item in degradations:
        result.setdefault("degradations", []).append(item)
    for item in errors:
        result.setdefault("errors", []).append(item)
    ci.write_json_atomic(result_path, result)

    budget = _init_budget()
    ci.write_json_atomic(work_dir / "budget.json", budget)
    print(json.dumps({
        "ok": True, "case_id": case_id, "documents": len(documents),
        "skipped": len(skipped), "degradations": len(degradations),
        "result": str(result_path), "manifest": str(manifest_path),
        "budget": str(work_dir / "budget.json"),
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 阶段 1：baseline
# ---------------------------------------------------------------------------
def cmd_baseline(args):
    in_dir, out_dir, work_dir = Path(args.input), Path(args.output), Path(args.work)
    manifest = ci.load_json(work_dir / "manifest.json", default=None)
    result = ci.load_json(out_dir / "result.json", default=None)
    if manifest is None or result is None:
        print("[baseline] manifest/result missing; run discover first", file=sys.stderr)
        return 2

    docs = []
    for doc in manifest.get("documents", []):
        text = ""
        if doc.get("extracted"):
            text, _enc, _tr = ci.read_text_smart(doc["extracted"])
        elif doc.get("needs_extraction"):
            text = ""  # 待平台 pdf-text-tool / pdftotext 抽取后回填
        docs.append(ci.extract_document(doc, text, metadata=doc.get("metadata")))

    dist = ci.year_distribution(docs)
    baseline = {
        "docs": docs,
        "year_distribution": dist,
        "term_candidates": _top_terms(docs),
        "gap_candidates": _top_marked(docs, "gap_lines"),
        "conflict_candidates": _top_marked(docs, "conflict_lines"),
        "representative_candidates": ci.representative_candidates(docs),
        "missing_year_docs": [d["doc_id"] for d in docs if d.get("year") is None],
    }
    ci.write_json_atomic(work_dir / "baseline.json", baseline)

    result["_baseline"] = baseline
    result["time_trend"]["distribution"] = dist
    if not result["time_trend"].get("unit_note"):
        result["time_trend"]["unit_note"] = (
            "机械基线：年份取自 metadata > 文件名 > 文首 4000 字符；"
            "在线发表/见刊/预印本口径尚未核验")
    # sources 同步机械元数据（标题/年份可后续被 Agent 核验覆盖）
    by_id = {d["doc_id"]: d for d in docs}
    for s in result.get("sources", []):
        d = by_id.get(s.get("id"))
        if d:
            s["title"] = d["title"]
            s["year"] = d["year"]
    for d in docs:
        if d.get("year") is None:
            result.setdefault("pending_human", []).append(
                "doc %s：无出版年份元数据，待人工补充" % d["doc_id"])
    ci.bump_revision(result, "baseline", "deterministic mechanical extraction",
                     before="skeleton only", after="%d docs, %d year buckets" % (len(docs), len(dist)))
    ci.write_json_atomic(out_dir / "result.json", result)
    print(json.dumps({
        "ok": True, "docs": len(docs), "year_buckets": len(dist),
        "gap_candidates": len(baseline["gap_candidates"]),
        "conflict_candidates": len(baseline["conflict_candidates"]),
        "missing_year": len(baseline["missing_year_docs"]),
        "baseline": str(work_dir / "baseline.json"),
    }, ensure_ascii=False))
    return 0


def _top_terms(docs, n=40):
    from collections import Counter
    c = Counter()
    for d in docs:
        for t in d.get("terms", []):
            c[t["term"]] += t["count"]
    return [{"term": t, "count": c} for t, c in c.most_common(n)]


def _top_marked(docs, field, n=40):
    out = []
    for d in docs:
        for item in d.get(field, []):
            out.append({"doc_id": d["doc_id"], "year": d.get("year"),
                        "line": item["line"], "text": item["text"]})
    return out[:n]


# ---------------------------------------------------------------------------
# 阶段 2/3：commit —— 合并 Agent 核验后的增量
# ---------------------------------------------------------------------------
def cmd_commit(args):
    out_dir, work_dir = Path(args.output), Path(args.work)
    result = ci.load_json(out_dir / "result.json", default=None)
    patch = ci.load_json(args.file, default=None)
    if result is None:
        print("[commit] result.json missing", file=sys.stderr)
        return 2
    if not isinstance(patch, dict):
        print("[commit] patch is not a JSON object", file=sys.stderr)
        return 2

    changed_keys = [k for k in patch.keys() if k in ci.FIXED_KEYS or k in result]
    ci.deep_merge(result, patch)
    meta = result.setdefault("_meta", {})
    meta["machine"] = bool(args.machine)
    ci.bump_revision(result, "commit", "apply verified %s patch" % args.phase,
                     before="keys: %s" % ",".join(changed_keys) or "(none)",
                     after="revision %d" % (int(meta.get("revision", 0)) + 1))
    ci.write_json_atomic(out_dir / "result.json", result)
    print(json.dumps({"ok": True, "merged_keys": changed_keys,
                      "revision": result["_meta"]["revision"]}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 阶段闸门：checkpoint —— 更新预算账本并落盘快照
# ---------------------------------------------------------------------------
def cmd_checkpoint(args):
    out_dir, work_dir = Path(args.output), Path(args.work)
    budget = ci.load_json(work_dir / "budget.json", default=_init_budget())
    phases = budget.setdefault("phases", [])
    found = False
    for ph in phases:
        if ph["id"] == args.phase:
            ph["status"] = args.status
            ph["ended_at"] = ci.now_iso()
            if args.requests is not None:
                ph["requests_used"] = args.requests
            found = True
            break
    if not found:
        phases.append({"id": args.phase, "status": args.status,
                       "ended_at": ci.now_iso(),
                       "requests_used": args.requests or 0})
    budget["requests_used"] = sum(ph.get("requests_used") or 0 for ph in phases)
    ci.write_json_atomic(work_dir / "budget.json", budget)

    result = ci.load_json(out_dir / "result.json", default=None)
    if result is not None:
        result.setdefault("runtime", {})["phases"] = _phase_snapshot(budget)
        ci.bump_revision(result, "checkpoint", "phase gate %s=%s" % (args.phase, args.status),
                         before="", after="ledger updated")
        ci.write_json_atomic(out_dir / "result.json", result)
    print(json.dumps({"ok": True, "phase": args.phase, "status": args.status,
                      "requests_used": budget["requests_used"]}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 阶段 4：report —— 渲染可编辑产物
# ---------------------------------------------------------------------------
def cmd_report(args):
    out_dir, work_dir = Path(args.output), Path(args.work)
    result = ci.load_json(out_dir / "result.json", default=None)
    if result is None:
        print("[report] result.json missing", file=sys.stderr)
        return 2

    report = _render_report(result)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    ci.write_json_atomic(out_dir / "evidence_map.json", result.get("evidence_map", []))

    tables = out_dir / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    docs = [(s.get("id"), s.get("path"), s.get("title"), s.get("year"), s.get("type"))
            for s in result.get("sources", [])]
    _write_csv(tables / "documents.csv",
               ["doc_id", "path", "title", "year", "type"], docs)
    dist = result.get("time_trend", {}).get("distribution", [])
    _write_csv(tables / "year_distribution.csv",
               ["year", "count", "doc_ids"],
               [(d.get("year"), d.get("count"), ";".join(d.get("doc_ids", []))) for d in dist])
    print(json.dumps({"ok": True, "report": str(out_dir / "report.md"),
                      "evidence_map": str(out_dir / "evidence_map.json")}, ensure_ascii=False))
    return 0


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def _render_report(result):
    rq = result.get("research_question", {}) or {}
    tt = result.get("time_trend", {}) or {}
    te = result.get("topic_evolution", {}) or {}
    rw = result.get("representative_works", {}) or {}
    rg = result.get("research_gaps", {}) or {}
    L = []
    L.append("# %s 研究前沿与趋势分析报告" % result.get("case_id", "unknown"))
    L.append("")
    L.append("- status: `%s`  - revision: %s" % (
        result.get("status"), result.get("_meta", {}).get("revision")))
    L.append("- 内容哈希（剔除时间戳）: `%s`" % ci.content_hash(result))
    L.append("")
    L.append("## 1. 研究问题与范围")
    L.append("")
    L.append("- 问题：%s" % (rq.get("question") or "（未填写）"))
    L.append("- 范围：%s" % (rq.get("scope") or "（未填写）"))
    L.append("- 子问题：%s" % "; ".join(rq.get("sub_questions", [])))
    L.append("")
    proto = result.get("protocol", {}) or {}
    L.append("## 2. 检索与纳排协议")
    L.append("")
    L.append("- 检索范围：%s" % (proto.get("search_scope") or "（未填写）"))
    L.append("- 纳入：%s" % "; ".join(proto.get("inclusion_criteria", [])))
    L.append("- 排除：%s" % "; ".join(proto.get("exclusion_criteria", [])))
    L.append("- 年份口径：%s" % (proto.get("date_definition_note") or "（未填写）"))
    L.append("")
    L.append("## 3. 时间趋势")
    L.append("")
    L.append("- 单位口径：%s" % (tt.get("unit_note") or "（未填写）"))
    L.append("- 年度分布：")
    for d in tt.get("distribution", []):
        L.append("  - %s：%d 篇（%s）" % (d.get("year"), d.get("count"), "; ".join(d.get("doc_ids", []))))
    for c in tt.get("claims", []):
        L.append("- 结论（%s）：%s" % (c.get("support"), c.get("claim")))
    L.append("")
    L.append("## 4. 主题演化")
    L.append("")
    for s in te.get("stage_divisions", []):
        L.append("- 阶段「%s」%s：%s" % (s.get("stage"), s.get("period"), s.get("basis")))
    for e in te.get("events", []):
        L.append("- 事件[%s] %s %s：%s" % (e.get("type"), e.get("term"), e.get("period"), e.get("cause")))
    L.append("")
    L.append("## 5. 代表性工作")
    L.append("")
    L.append("- 选取口径：%s" % "; ".join(rw.get("criteria", [])))
    for s in rw.get("stages", []):
        L.append("- 阶段「%s」%s：" % (s.get("stage"), s.get("period")))
        for w in s.get("works", []):
            L.append("  - [%s] %s（%s, %s）：%s" % (
                w.get("kind"), w.get("doc_id"), w.get("year"), w.get("title"), w.get("rationale")))
    L.append("")
    L.append("## 6. 研究空白")
    L.append("")
    for g in rg.get("gaps", []):
        L.append("- [%s] %s：%s；排除证据：%s" % (
            g.get("type"), g.get("id"), g.get("description"), g.get("exclusion_evidence")))
    L.append("")
    L.append("## 7. 冲突与不确定性")
    L.append("")
    for c in result.get("conflicts", []):
        L.append("- 冲突项 %s：%s" % (c.get("items"), c.get("resolution") or "未决"))
    for u in result.get("uncertainty", []):
        L.append("- 不确定：%s" % u)
    L.append("")
    L.append("## 8. 边界与待人工核验")
    L.append("")
    for lim in result.get("limitations", []):
        L.append("- 边界：%s" % lim)
    for ph in result.get("pending_human", []):
        L.append("- 待人工：%s" % ph)
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 阶段 5：finalize
# ---------------------------------------------------------------------------
def cmd_finalize(args):
    out_dir, work_dir = Path(args.output), Path(args.work)
    result = ci.load_json(out_dir / "result.json", default=None)
    if result is None:
        print("[finalize] result.json missing", file=sys.stderr)
        return 2
    if result.get("status") not in ci.STATUS_VALUES or result.get("_meta", {}).get("machine"):
        # 机械产物（无 Agent 核验）或非法状态：按内容诚实推导
        result["status"] = ci.derive_status(result)
    for key in ci.FIXED_KEYS:
        result.setdefault(key, {} if key not in ("case_id", "status") else None)
    rt = result.setdefault("runtime", {})
    started = rt.get("started_at")
    rt["finished_at"] = ci.now_iso()
    rt["phases"] = _phase_snapshot(ci.load_json(work_dir / "budget.json", default=_init_budget()))
    result.setdefault("_meta", {})["machine"] = False
    ci.bump_revision(result, "finalize", "close runtime and finalize status",
                     before=result.get("status"), after=result.get("status"))
    ci.write_json_atomic(out_dir / "result.json", result)
    print(json.dumps({"ok": True, "status": result["status"],
                      "revision": result["_meta"]["revision"]}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 预算账本
# ---------------------------------------------------------------------------
PHASE_PLAN = [
    {"id": "discover", "label": "发现与落骨架", "deadline_s": 180, "max_requests": 4},
    {"id": "baseline", "label": "机械基线", "deadline_s": 180, "max_requests": 6},
    {"id": "deep_analysis", "label": "四类深度分析", "deadline_s": 360, "max_requests": 40},
    {"id": "selfcheck", "label": "自检", "deadline_s": 100, "max_requests": 8},
    {"id": "finalize", "label": "收尾", "deadline_s": 80, "max_requests": 6},
]


def _init_budget():
    return {
        "total_s": 900, "total_requests": 64, "tokens_per_response": 16384,
        "phases": [dict(ph, status="pending", requests_used=0, ended_at=None) for ph in PHASE_PLAN],
        "requests_used": 0, "created_at": ci.now_iso(),
    }


def _phase_snapshot(budget=None):
    if budget is None:
        return [dict(ph, status="pending", requests_used=0) for ph in PHASE_PLAN]
    return budget.get("phases", [])


def main(argv=None):
    args = _parse_args(argv)
    if args.version:
        print(ci.SKILL_NAME, ci.SKILL_VERSION)
        return 0
    if args.command is None:
        _parse_args(["--help"])
        return 0
    if args.command == "discover":
        return cmd_discover(args)
    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "commit":
        return cmd_commit(args)
    if args.command == "checkpoint":
        return cmd_checkpoint(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "finalize":
        return cmd_finalize(args)
    if args.command == "selftest-hint":
        print("run: python3 scripts/selftest.py --input %s --output %s --work %s"
              % (args.input, args.output, args.work))
        return 0
    print("[main] unknown command: %s" % args.command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
