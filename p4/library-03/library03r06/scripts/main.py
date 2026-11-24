#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — sci-library-organizer 编排入口。

承诺：启动后立即写 /app/output/result.json 骨架（status=partial），此后只做增量改进；
任何中途异常都保留一份合法 JSON 骨架，绝不修改 /input，绝不编造作者/年份/DOI/结论。

用法:
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
        [--task /input/task.md] [--decisions /app/work/decisions.json] \
        [--hierarchy document_type,topic] [--no-extract-text] [--apply]

产出:
    /app/output/result.json         case_id + 契约字段 + status/sources/uncertainty/change_log
    /app/output/library/            --apply 后实际整理目录（copy_not_move，可回滚）
    /app/output/plan.json           计划中间表示（落盘前即可复核）
    /app/output/graph.json          版本/别名关系图（节点+边）
    /app/output/index.json          可检索知识结构（标题/DOI/arXiv/作者/标签/哈希）
    /app/work/ 下的 inventory/plan/graph/index 等中间产物
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import organize  # noqa: E402

__version__ = "1.0.0"

DEFAULT_CONTRACT = {
    "library": {
        "catalog": [],
        "hierarchy": {},
        "metadata": {},
        "versions": [],
        "aliases": [],
        "index": {},
        "queries": [],
    },
    "evidence": {"hashes": {}, "permissions": {}, "locators": []},
    "operations": [],
    "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                 "duplicates": [], "recoveries": []},
}

EXTRAS = ("status", "sources", "uncertainty", "change_log",
          "errors", "degradations", "pending_human", "runtime")

TEXT_FAMILIES = {"bibtex", "notes", "dataset", "code", "manuscript_source", "reference_data"}

DEFAULT_HIERARCHY = ("document_type", "topic")


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rel_posix(root, abs_path):
    rel = os.path.relpath(abs_path, root)
    return rel.replace(os.sep, "/")


def family_of(ext, fn):
    ext = (ext or "").lower()
    if ext in (".bib", ".ris", ".enw"):
        return "bibtex"
    if ext in (".txt", ".md", ".markdown", ".rst", ".org"):
        return "notes"
    if ext in (".csv", ".tsv", ".json", ".db", ".sqlite", ".h5", ".hdf5",
               ".npy", ".npz", ".parquet", ".dat", ".xlsx", ".xls"):
        return "dataset"
    if ext in (".py", ".sh", ".r", ".jl", ".cpp", ".c", ".java", ".go", ".js"):
        return "code"
    if ext in (".tex", ".sty", ".cls"):
        return "manuscript_source"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".docx", ".doc", ".pptx", ".ppt", ".odt", ".odp"):
        return "office"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".gif"):
        return "image"
    if ext in (".zip", ".tar", ".gz", ".7z", ".rar"):
        return "archive"
    return "other"


def build_inventory(input_dir):
    files, dirs, symlink_map = [], [], {}
    summary = {"total": 0, "bytes": 0, "by_family": {}, "unreadable": 0}
    for root, dnames, fnames in os.walk(input_dir):
        dnames.sort()
        for d in dnames:
            dirs.append(rel_posix(input_dir, os.path.join(root, d)))
        for fn in sorted(fnames):
            abs_p = os.path.join(root, fn)
            rel = rel_posix(input_dir, abs_p)
            ext = os.path.splitext(fn)[1].lower()
            fam = family_of(ext, fn)
            rec = {"source": rel, "abs_path": abs_p, "ext": ext, "family": fam,
                   "size": None, "sha256": None, "permissions": {}, "encoding": None,
                   "has_text_layer": None, "sample_text": "", "errors": [], "symlink": False}
            try:
                st = os.lstat(abs_p)
                rec["permissions"] = {"stat_ok": True, "mode": oct(st.st_mode & 0o777),
                                      "readable": os.access(abs_p, os.R_OK),
                                      "writable": os.access(abs_p, os.W_OK)}
            except OSError as ex:
                rec["permissions"] = {"stat_ok": False, "readable": False, "writable": False}
                rec["errors"].append(f"stat failed: {ex}")
                files.append(rec)
                summary["unreadable"] += 1
                continue
            if os.path.islink(abs_p):
                rec["symlink"] = True
                try:
                    symlink_map[rel] = os.readlink(abs_p)
                except OSError as ex:
                    rec["errors"].append(f"readlink failed: {ex}")
            if os.path.isfile(abs_p):
                rec["size"] = st.st_size
                summary["bytes"] += st.st_size
                try:
                    rec["sha256"] = organize.sha256_file(abs_p)
                except OSError as ex:
                    rec["errors"].append(f"hash failed: {ex}")
                    summary["unreadable"] += 1
                if fam in TEXT_FAMILIES:
                    txt, enc, errs = organize.read_text_robust(abs_p)
                    rec["sample_text"] = txt
                    rec["encoding"] = enc
                    rec["errors"].extend(errs)
            summary["total"] += 1
            summary["by_family"][fam] = summary["by_family"].get(fam, 0) + 1
            files.append(rec)
    return {"root": input_dir, "files": files, "dirs": dirs,
            "symlink_map": symlink_map, "summary": summary}


def extract_pdf_text(inv, work_dir, budget_seconds=180):
    text_dir = os.path.join(work_dir, "text")
    os.makedirs(text_dir, exist_ok=True)
    started = time.time()
    count = 0
    for f in inv["files"]:
        if f.get("family") != "pdf" or not f.get("sha256"):
            continue
        if time.time() - started > budget_seconds:
            break
        out = os.path.join(text_dir, f["sha256"] + ".txt")
        if os.path.exists(out):
            continue
        for cmd in ("pdftotext", "pdftotext.exe"):
            if not shutil.which(cmd):
                continue
            try:
                r = subprocess.run([cmd, "-enc", "UTF-8", f["abs_path"], out],
                                   timeout=10, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
            except Exception:  # noqa: BLE001
                continue
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                break
        if os.path.exists(out) and os.path.getsize(out) > 0:
            f["has_text_layer"] = True
            try:
                f["sample_text"] = open(out, "r", encoding="utf-8", errors="replace").read(200000)
            except OSError:
                pass
        else:
            f["has_text_layer"] = False
        count += 1
    return count


def read_case_context(input_dir, task_arg):
    case_path = os.path.join(input_dir, "case.json")
    case, case_err = organize.read_json(case_path)
    case_id = "unknown"
    contract = None
    task_excerpt = None
    degradation = []
    if isinstance(case, dict):
        case_id = (case.get("case_id") or case.get("id") or case.get("challenge_id")
                   or case.get("armiId") or "unknown")
        contract = (case.get("output_contract") or case.get("outputContract")
                    or case.get("output_schema") or case.get("result_contract")
                    or case.get("contract"))
        if isinstance(case.get("task"), str):
            task_excerpt = case["task"][:500]
    elif os.path.exists(case_path):
        degradation.append(f"case.json unreadable or invalid: {case_err or 'unknown'}")
    if not isinstance(contract, dict) or not contract:
        contract = DEFAULT_CONTRACT
    task_path = task_arg or os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        try:
            with open(task_path, "r", encoding="utf-8-sig") as f:
                txt = f.read()
            task_excerpt = task_excerpt or txt[:500]
        except OSError:
            degradation.append("task.md unreadable")
    return case_id, contract, task_excerpt, degradation


def build_hierarchy(entries):
    tree = {}
    for e in entries:
        if not e.get("target"):
            continue
        node = tree
        for part in e["target"].split("/")[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("_files", []).append(e["suggested_name"])
    return tree


def compute_failures(inv, entries, operations, rollback, verify_report, applied):
    skipped, conflicts, perm_errors, duplicates, recoveries = [], [], [], [], []
    for f in inv["files"]:
        p = f.get("permissions") or {}
        if f.get("errors"):
            skipped.append({"source": f["source"], "errors": f["errors"]})
        if p.get("stat_ok") is False or not p.get("readable"):
            perm_errors.append({"source": f["source"], "permissions": p,
                                "error": f.get("errors") or "unreadable"})
    for e in entries:
        if e.get("duplicate_of"):
            duplicates.append({"source": e["source"], "duplicate_of": e["duplicate_of"],
                               "sha256": e.get("sha256")})
    name_map = {}
    for e in entries:
        if e.get("keep") and e.get("target"):
            name_map.setdefault(e["target"], []).append(e)
    for tgt, members in name_map.items():
        distinct = {m["sha256"] for m in members}
        if len(members) > 1 and len(distinct) > 1:
            conflicts.append({"target": tgt, "sources": [m["source"] for m in members],
                              "note": "same target name, different content; resolved by -N suffix"})
    for v in verify_report:
        if not v.get("passed"):
            skipped.append({"source": v.get("check"), "reason": v.get("detail")})
    if rollback:
        recoveries = [{"target": r["target"], "note": "remove this file to roll back"} for r in rollback]
    elif not applied:
        recoveries = [{"note": "dry-run: no files created; nothing to roll back"}]
    else:
        recoveries = []
    return {"skipped": skipped, "conflicts": conflicts, "permission_errors": perm_errors,
            "duplicates": duplicates, "recoveries": recoveries}


def _decide_status(inv, entries, operations, failures, verify_report, applied):
    if not inv["files"]:
        return "insufficient_evidence"
    bad = [f for f in inv["files"] if f.get("errors")]
    bad += [o for o in operations if o.get("result") in ("copy_error", "missing_source", "skipped")]
    bad += [v for v in verify_report if not v.get("passed")]
    if bad or failures.get("permission_errors"):
        return "partial"
    if all(v.get("passed") for v in verify_report):
        return "ok"
    return "partial"


def _adapt_contract(key, data):
    if key in data:
        return data[key]
    if key in ("library", "catalog", "index", "documents", "results") and "library" in data:
        return data["library"]
    if key in ("evidence", "proof", "provenance") and "evidence" in data:
        return data["evidence"]
    if key in ("operations", "changes", "log", "actions") and "operations" in data:
        return data["operations"]
    if key in ("failures", "failure_and_recovery", "recovery") and "failures" in data:
        return data["failures"]
    return None


def _find_queries(inv):
    records = []
    for f in inv["files"]:
        name = os.path.basename(f["source"]).lower()
        if not re.search(r"question|query|search", name):
            continue
        txt = f.get("sample_text") or ""
        obj, err = organize.read_json(f["abs_path"])
        if obj is not None:
            block = None
            if isinstance(obj, list):
                block = obj
            elif isinstance(obj, dict):
                block = obj.get("questions") or obj.get("queries") or obj.get("search")
            if isinstance(block, list):
                for item in block:
                    if isinstance(item, str):
                        records.append({"id": f"{f['source']}#{len(records)+1}", "text": item})
                    elif isinstance(item, dict):
                        records.append({"id": item.get("id") or f"{f['source']}#{len(records)+1}",
                                        "text": item.get("text") or item.get("question") or ""})
                continue
        for ln in txt.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith(("#", "//")):
                continue
            records.append({"id": f"{f['source']}#{len(records)+1}", "text": ln[:300]})
    return records


def _finalize(args, case_id, contract, data, change_log, errors, degradations,
              pending_human, runtime, t0, inv=None, plan=None, graph=None, index=None):
    result = {"case_id": case_id}
    for key in contract:
        if key == "case_id":
            continue
        result[key] = _adapt_contract(key, data)
    for extra in EXTRAS:
        result.setdefault(extra, data.get(extra))
    runtime["elapsed_seconds"] = round(time.time() - t0, 3)
    runtime["finished_at"] = now_utc()
    result["runtime"] = runtime
    result["change_log"] = data.get("change_log") or change_log
    result["errors"] = errors or data.get("errors") or []
    result["degradations"] = degradations or data.get("degradations") or []
    result["pending_human"] = pending_human or data.get("pending_human") or []
    result["status"] = data.get("status") or "partial"
    organize.write_json(os.path.join(args.output, "result.json"), result)
    if inv is not None:
        organize.write_json(os.path.join(args.work, "inventory.json"), inv)
    if plan is not None:
        organize.write_json(os.path.join(args.work, "plan.json"), plan)
        organize.write_json(os.path.join(args.output, "plan.json"), plan)
    if graph is not None:
        organize.write_json(os.path.join(args.work, "graph.json"), graph)
        organize.write_json(os.path.join(args.output, "graph.json"), graph)
    if index is not None:
        organize.write_json(os.path.join(args.work, "index.json"), index)
        organize.write_json(os.path.join(args.output, "index.json"), index)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="sci-library-organizer: 科研资料整理（分类/命名/去重/版本/索引/检索）")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--task", default=None)
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--hierarchy", default=None, help="逗号分隔维度，如 document_type,topic,year")
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--no-extract-text", action="store_true", default=False)
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    case_id, contract, task_excerpt, case_degrad = read_case_context(args.input, args.task)
    skeleton = {"case_id": case_id}
    for key in contract:
        skeleton[key] = organize.deep_placeholder(contract[key])
    for extra in EXTRAS:
        skeleton[extra] = None
    skeleton["status"] = "partial"
    skeleton["change_log"] = [{"ts": now_utc(), "event": "skeleton_written", "detail": "partial result"}]
    organize.write_json(os.path.join(args.output, "result.json"), skeleton)

    change_log = list(skeleton["change_log"])
    errors, degradations, pending_human = [], list(case_degrad), []
    data = {"library": {}, "evidence": {}, "operations": [], "failures": {}}
    runtime = {"started_at": now_utc(), "python": platform.python_version(), "version": __version__}

    if not os.path.isdir(args.input):
        data["status"] = "input_error"
        errors.append({"source": "input", "error": f"input directory missing: {args.input}"})
        _finalize(args, case_id, contract, data, change_log, errors, degradations,
                  pending_human, runtime, t0)
        return 0

    inv = build_inventory(args.input)

    if not args.no_extract_text:
        try:
            n = extract_pdf_text(inv, args.work)
            if n == 0 and any(f["family"] == "pdf" for f in inv["files"]):
                degradations.append("no PDF text extracted (pdftotext unavailable or no text layer)")
        except Exception as ex:  # noqa: BLE001
            degradations.append(f"text extraction error: {ex}")
    organize.write_json(os.path.join(args.work, "inventory.json"), inv)

    if not inv["files"]:
        data["status"] = "insufficient_evidence"
        pending_human.append({"source": "input", "reason": "no files to organize"})
        _finalize(args, case_id, contract, data, change_log, errors, degradations,
                  pending_human, runtime, t0, inv)
        return 0

    decisions = {}
    if args.decisions and os.path.isfile(args.decisions):
        decisions, derr = organize.read_json(args.decisions)
        if derr:
            degradations.append(f"decisions unreadable: {derr}")
            decisions = {}

    entries, warnings = organize.build_entries(inv, decisions)
    for w in warnings:
        pending_human.append(w)
    dups = organize.detect_duplicates(entries)
    version_groups = organize.detect_version_groups(entries)
    alias_records = organize.find_alias_records(inv)
    alias_list = organize.detect_alias_relation(entries, alias_records, inv.get("symlink_map") or {})
    index = organize.build_index(entries)
    graph = organize.build_graph(entries, version_groups, alias_list)
    hierarchy_dims = (args.hierarchy.split(",") if args.hierarchy else list(DEFAULT_HIERARCHY))
    plan = organize.build_plan(entries, hierarchy_dims, {"hierarchy": hierarchy_dims})
    verify_report = organize.verify_plan(entries, plan, inv)

    query_records = _find_queries(inv)
    query_results = organize.answer_queries(entries, index, query_records)

    operations = []
    rollback = []
    applied = bool(args.apply)
    if applied:
        operations, rollback = organize.apply_plan(plan, args.output)
    else:
        for e in plan["entries"]:
            operations.append({"op": "plan", "source": e["source"], "target": e.get("target"),
                               "keep": e.get("keep"), "result": "dry_run_planned"})

    catalog = plan["entries"]
    failures = compute_failures(inv, entries, operations, rollback, verify_report, applied)
    data["library"] = {
        "catalog": catalog,
        "hierarchy": build_hierarchy(entries),
        "metadata": {e["source"]: e["metadata"] for e in entries},
        "versions": version_groups,
        "aliases": alias_list,
        "index": index,
        "queries": query_results,
    }
    data["evidence"] = {
        "hashes": {f["sha256"]: sorted({f2["source"] for f2 in inv["files"]
                                        if f2["sha256"] == f["sha256"]})
                   for f in inv["files"] if f.get("sha256")},
        "permissions": {f["source"]: f["permissions"] for f in inv["files"]},
        "locators": [{"source": e["source"], "evidence": e["evidence"]} for e in entries],
    }
    data["operations"] = operations
    data["failures"] = failures
    data["sources"] = [
        {"source": f["source"], "sha256": f["sha256"], "size": f["size"],
         "locator": "input:" + f["source"]} for f in inv["files"]
    ]
    data["uncertainty"] = {
        "unclassified": sum(1 for e in entries if (e.get("document_type") or "other") == "other"),
        "missing_title": sum(1 for e in entries if not (e.get("metadata") or {}).get("title")),
        "missing_doi": sum(1 for e in entries if not (e.get("metadata") or {}).get("doi")),
        "no_text_layer": sum(1 for f in inv["files"] if f.get("has_text_layer") is False),
        "low_confidence": sum(1 for e in entries if e.get("confidence") == "low"),
    }
    data["errors"] = errors
    data["degradations"] = degradations
    data["pending_human"] = pending_human
    data["runtime"] = runtime
    data["status"] = _decide_status(inv, entries, operations, failures, verify_report, applied)

    change_log.append({"ts": now_utc(), "event": "inventory_done",
                       "detail": f"{inv['summary']['total']} files"})
    change_log.append({"ts": now_utc(), "event": "plan_built",
                       "detail": f"{len(dups)} duplicates, {len(version_groups)} version groups"})
    if applied:
        change_log.append({"ts": now_utc(), "event": "apply_done",
                           "detail": f"{len(rollback)} files created"})
    change_log.append({"ts": now_utc(), "event": "finalize", "detail": data["status"]})
    data["change_log"] = change_log

    _finalize(args, case_id, contract, data, change_log, errors, degradations,
              pending_human, runtime, t0, inv, plan, graph, index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
