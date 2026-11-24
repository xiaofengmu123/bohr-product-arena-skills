# -*- coding: utf-8 -*-
"""Entry wrapper for research-frontier-contract.

Usage:
  python3 scripts/main.py prepare [--input /input] [--output /app/output] [--work /app/work]
  python3 scripts/main.py analyze  [--input /input] [--output /app/output] [--work /app/work]
  python3 scripts/main.py commit --file PATCH.json [--output ...] [--work ...]
  python3 scripts/main.py finalize [--output ...] [--work ...]
  python3 scripts/main.py --version
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402


def _paths(args):
    inp = args.input or "/input"
    out = args.output or "/app/output"
    work = args.work or "/app/work"
    return inp, out, work


def _load_result(out):
    path = os.path.join(out, "result.json")
    obj, err = C.load_json_safe(path)
    return obj or {}, err


def _save_result(out, result):
    C.atomic_write(os.path.join(out, "result.json"), result, ensure_ascii=False)
    return os.path.join(out, "result.json")


def _load_contract(inp):
    case, err = C.load_json_safe(os.path.join(inp, "case.json"))
    if err:
        return case, None, err
    contract = case.get("output_contract") if isinstance(case, dict) else None
    return case, contract, None


def cmd_prepare(args):
    inp, out, work = _paths(args)
    C.ensure_dir(out)
    C.ensure_dir(work)
    case, contract, err = _load_contract(inp)
    task_text = C.read_text_safe(os.path.join(inp, "task.md"))
    files, controls = C.discover_input(inp)

    errors = []
    degradations = []
    if err:
        errors.append({"component": "case.json", "message": err, "recovered": True})

    sampled = files
    if len(files) > C.MAX_FILES:
        sampled = files[:C.MAX_FILES]
        degradations.append({
            "trigger": "too_many_files", "file": "n/a",
            "action": "确定性采样前 %d 篇" % C.MAX_FILES,
            "status": "sampled",
        })

    docs = C.extract_materials(sampled, work)
    for d in docs:
        if d.get("oversize"):
            degradations.append({
                "trigger": "oversize_file", "file": d["rel"],
                "action": "跳过 >2MB 文件", "status": "skipped"})
        if not d.get("parse_ok") and d.get("kind") == "pdf":
            degradations.append({
                "trigger": "pdf_unparsed", "file": d["rel"],
                "action": "无文本层且 pdftotext 不可用，标 unchecked",
                "status": "unparsed"})

    result = C.build_skeleton(case, task_text, contract)
    result["sources"] = [
        {"id": d["id"], "path": d["rel"], "type": d["kind"],
         "parse_ok": d.get("parse_ok", True)} for d in docs]
    result["errors"] = errors
    result["degradations"] = degradations
    if len(docs) == 0:
        result["status"] = "insufficient_evidence"
        result["research_gaps"]["gaps"].append({
            "id": "G000", "type": "coverage_absence",
            "description": "材料不足，无法区分研究空白与检索遗漏",
            "exclusion_evidence": "输入目录无任何文献材料", "evidence_ids": []})
    if not isinstance(contract, dict):
        result.setdefault("errors", []).append({
            "component": "output_contract", "message": "缺失或非 dict，仅产出固定字段",
            "recovered": True})

    # manifest
    C.atomic_write(os.path.join(work, "manifest.json"), {
        "case_id": result["case_id"],
        "files": [{"rel": f["rel"], "size": f["size"]} for f in sampled],
        "controls": [c["rel"] for c in controls],
        "docs": [d["id"] for d in docs],
        "contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else [],
    })

    _save_result(out, result)
    print("prepare: wrote result.json (%d docs, %d contract keys)" % (
        len(docs), len(contract) if isinstance(contract, dict) else 0))
    return 0


def cmd_analyze(args):
    inp, out, work = _paths(args)
    result, err = _load_result(out)
    if err:
        print("analyze: cannot load result.json: %s" % err, file=sys.stderr)
        return 2
    case, contract, _ = _load_contract(inp)
    manifest, merr = C.load_json_safe(os.path.join(work, "manifest.json"))
    docs = []
    if not merr and isinstance(manifest, dict):
        for did in manifest.get("docs", []):
            text_path = os.path.join(work, "materials", did + ".txt")
            rel = next((s["path"] for s in result.get("sources", []) if s.get("id") == did), "")
            docs.append({
                "id": did,
                "rel": rel,
                "kind": next((s["type"] for s in result.get("sources", []) if s.get("id") == did), "text"),
                "text": C.read_text_safe(text_path),
                "parse_ok": next((s.get("parse_ok") for s in result.get("sources", []) if s.get("id") == did), True),
            })
    baseline = C.mechanical_extract(docs, work)
    result["_baseline"] = baseline
    result["time_trend"]["distribution"] = baseline["year_distribution"]
    if not result["time_trend"].get("unit_note"):
        result["time_trend"]["unit_note"] = "年份来自材料文本头部启发式提取，未经元数据核对，待人工确认口径"
    result["machine_candidates"] = {
        "gap_candidates": baseline["gap_candidates"],
        "conflict_candidates": baseline["conflict_candidates"],
        "representative_candidates": baseline["representative_candidates"],
        "machine": True,
    }
    C.append_change_log(result, "analyze",
                        "机械基线：年份/术语/空白句/冲突句/代表作候选",
                        None, result["_baseline"])
    C.re_align(result, contract)
    _save_result(out, result)
    # render editable artifacts
    C.render_report(result, os.path.join(out, "report.md"))
    C.write_evidence_map(result, os.path.join(out, "evidence_map.json"))
    C.render_tables(result, os.path.join(out, "tables"))
    print("analyze: %d docs, %d year buckets, %d gap candidates" % (
        len(baseline["documents"]), len(baseline["year_distribution"]),
        len(baseline["gap_candidates"])))
    return 0


def cmd_commit(args):
    inp, out, _ = _paths(args)
    result, err = _load_result(out)
    if err:
        print("commit: cannot load result.json: %s" % err, file=sys.stderr)
        return 2
    patch, perr = C.load_json_safe(args.file)
    if perr or not isinstance(patch, dict):
        print("commit: bad patch file: %s" % perr, file=sys.stderr)
        return 2
    contract = None
    case, cerr = C.load_json_safe(os.path.join(inp, "case.json"))
    if not cerr and isinstance(case, dict):
        contract = case.get("output_contract")
    reason = patch.pop("_reason", None) or "增量修改 %s" % ", ".join(sorted(patch.keys()))
    action = patch.pop("_action", None) or "commit"
    C.commit(result, patch, action, reason)
    C.re_align(result, contract)
    _save_result(out, result)
    print("commit: applied %s (revision=%s)" % (", ".join(sorted(patch.keys())), result.get("revision")))
    return 0


def cmd_finalize(args):
    _, out, work = _paths(args)
    result, err = _load_result(out)
    if err:
        print("finalize: cannot load result.json: %s" % err, file=sys.stderr)
        return 2
    # decide status honestly
    gaps = (result.get("research_gaps") or {}).get("gaps") or []
    tt = (result.get("time_trend") or {}).get("distribution") or []
    te = (result.get("topic_evolution") or {}).get("events") or []
    rw = (result.get("representative_works") or {}).get("stages") or []
    if result.get("status") not in ("insufficient_evidence", "input_error"):
        if tt and te and rw and gaps:
            result["status"] = "ok"
        else:
            result["status"] = "partial"
    result["runtime"]["phase"] = "finalized"
    result["runtime"]["finished_at"] = C.now_iso()
    C.append_change_log(result, "finalize", "定稿 status/产物/change_log", None, result["status"])
    # ensure no fabricated score keys
    for banned in ("benchmark_score", "score", "白板", "whiteboard"):
        result.pop(banned, None)
    _save_result(out, result)
    C.render_report(result, os.path.join(out, "report.md"))
    C.write_evidence_map(result, os.path.join(out, "evidence_map.json"))
    C.render_tables(result, os.path.join(out, "tables"))
    C.atomic_write(os.path.join(work, "finalized.json"), {
        "case_id": result.get("case_id"), "status": result.get("status"),
        "revision": result.get("revision")})
    print("finalize: status=%s revision=%s" % (result.get("status"), result.get("revision")))
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", help="input dir (default /input)")
    common.add_argument("--output", help="output dir (default /app/output)")
    common.add_argument("--work", help="work dir (default /app/work)")
    p = argparse.ArgumentParser(prog="main.py", parents=[common])
    p.add_argument("--version", action="store_true", help="print version")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("prepare", parents=[common])
    sub.add_parser("analyze", parents=[common])
    c = sub.add_parser("commit", parents=[common])
    c.add_argument("--file", required=True, help="patch JSON path")
    sub.add_parser("finalize", parents=[common])
    args = p.parse_args(argv)
    if args.version:
        print(C.VERSION)
        return 0
    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "analyze":
        return cmd_analyze(args)
    if args.cmd == "commit":
        return cmd_commit(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
