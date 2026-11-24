#!/usr/bin/env python3
"""main.py — sci-library-organizer 编排入口（仅标准库）。

承诺：
  1. 启动后立即写 /app/output/result.json 骨架（status=partial），任何中途异常都保留
     至少一份合法 JSON 骨架。
  2. 绝不修改 /input；整理只做「复制」到 /app/output/library/，并写回滚清单。
  3. 绝不编造作者/年份/DOI；每条结论都进入 evidence_map 并带 locator + 逐字 quote。

用法:
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
        [--task /input/task.md] [--decisions /app/work/decisions.json] \
        [--hierarchy document_type,topic] [--apply]

依赖 scripts/core_impl.py（同目录，仅标准库）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import core_impl as ci

__version__ = "2.0.0"

DEFAULT_CONTRACT = {
    "library": {},
    "evidence": {},
    "operations": [],
    "failures": {},
}

EXTRAS = ("status", "sources", "uncertainty", "change_log",
          "errors", "degradations", "pending_human", "runtime")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------- #
# 契约读取
# --------------------------------------------------------------------------- #

def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def deep_placeholder(v):
    if isinstance(v, dict):
        return {k: deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def read_contract(case):
    if isinstance(case, dict):
        return (case.get("output_contract") or case.get("outputContract")
                or case.get("output_schema") or case.get("result_contract")
                or case.get("contract"))
    return None


def normalize_contract(contract):
    """返回 (keys, cmap)。cmap 为契约 dict（列表契约时各值为 None）。"""
    if isinstance(contract, dict) and contract:
        return list(contract.keys()), contract
    if isinstance(contract, list) and contract:
        keys = [k if isinstance(k, str) else str(k) for k in contract]
        return keys, {k: None for k in keys}
    return list(DEFAULT_CONTRACT.keys()), DEFAULT_CONTRACT


def build_result(case_id, contract, data):
    keys, cmap = normalize_contract(contract)
    result = {"case_id": case_id}
    for key in keys:
        if key in data:
            result[key] = data[key]
        else:
            result[key] = deep_placeholder(cmap.get(key))
    # 完整性兜底：无论契约是否列出，都补齐四类主结果与健壮性字段
    for key in ("library", "evidence", "operations", "failures"):
        if key in data:
            result.setdefault(key, data[key])
    for extra in EXTRAS:
        if extra in data:
            result.setdefault(extra, data[extra])
    return result


def empty_data(status="partial"):
    return {
        "library": {"catalog": {}, "hierarchy": [], "metadata": {},
                    "versions": [], "evidence_map": {}},
        "evidence": {"hashes": {}, "permissions": {}, "locators": [],
                     "evidence_map": {"schema_version": ci.SCHEMA_VERSION,
                                      "claims": [], "by_subject": {}, "conflicts": []},
                     "conflicts": []},
        "operations": [],
        "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                     "duplicates": [], "recoveries": []},
        "status": status,
        "sources": [],
        "uncertainty": {},
        "change_log": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {},
    }


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sci-library-organizer",
        description="科研资料整理：分类·命名·去重·版本，证据溯源、可复核可回滚。")
    ap.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    ap.add_argument("--task", default=None, help="task.md 路径（默认 <input>/task.md）")
    ap.add_argument("--decisions", default=None, help="decisions.json 路径（模型补充）")
    ap.add_argument("--hierarchy", default="document_type",
                    help="目录层级维度，逗号分隔（document_type/topic/year/type）")
    ap.add_argument("--apply", action="store_true", help="实际复制到 output/library（默认干跑）")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    t0 = time.time()
    runtime = {"started_at": ci.now_iso(), "python_version": sys.version.split()[0],
               "argv": list(argv or sys.argv[1:]), "apply": bool(args.apply),
               "pdftotext": bool(ci.find_pdftotext())}

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    work_text = os.path.join(args.work, "text")
    os.makedirs(work_text, exist_ok=True)

    degradations = []

    # 1) 读契约
    case_path = os.path.join(args.input, "case.json")
    case = read_json(case_path)
    input_missing = not os.path.isdir(args.input)
    case_id = "unknown"
    contract = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("challenge_id") or "unknown"
        contract = read_contract(case)
    if not isinstance(contract, (dict, list)) or (isinstance(contract, (dict, list)) and not contract):
        contract = DEFAULT_CONTRACT
    if os.path.isfile(case_path):
        if case is None:
            degradations.append({"source": "case.json",
                                 "reason": "unreadable or invalid JSON; fallback to default contract"})
    else:
        degradations.append({"source": "case.json", "reason": "not found; fallback to default contract"})

    # 2) 立即落骨架（前 3 分钟硬性要求的第一道保障）
    data = empty_data("partial")
    data["runtime"] = runtime
    data["change_log"] = [{"t": ci.now_iso(), "event": "skeleton written"}]
    write_result(args.output, build_result(case_id, contract, data))

    # 3) 缺输入 → input_error
    if input_missing:
        data["status"] = "input_error"
        data["errors"].append("input directory missing")
        data["change_log"].append({"t": ci.now_iso(), "event": "input_error: input missing"})
        finalize(args, data, case_id, contract, runtime, t0)
        return 0

    files, dirs = ci.scan_input(args.input, work_text)
    materials = {r: f for r, f in files.items() if f.get("role") == "material"}

    # 4) 组装 catalog（先由确定性信号构建，再合并模型 decisions）
    bib_matches = ci.merge_bibtex_to_files(files)
    catalog = ci.build_catalog(files, bib_matches, [])
    decisions_applied, decisions_rejected, dec_errors = [], [], []
    if args.decisions:
        decisions_applied, decisions_rejected, dec_errors = ci.merge_decisions(catalog, args.decisions)
        for e in dec_errors:
            degradations.append({"source": "decisions.json", "reason": e})

    versions = ci.build_versions(catalog)
    plans, name_conflicts = ci.plan_organization(catalog, args.hierarchy.split(","))

    # 5) 证据映射（核心强化：每条结论 locator + 逐字 quote）
    evidence_map = ci.build_evidence_map(catalog, files, bib_matches, decisions_applied, [])
    for rel in sorted(catalog):
        catalog[rel]["claims"] = evidence_map["by_subject"].get(rel, [])

    # 6) 落盘（可选）
    rollback, ops = [], []
    if args.apply:
        rollback, ops = ci.apply_organization(catalog, os.path.join(args.output, "library"), plans)

    # 7) 失败与不确定性汇总
    failures = {"skipped": [], "conflicts": [], "permission_errors": [],
                "duplicates": [], "recoveries": []}
    for rel in sorted(files):
        rec = files[rel]
        perm = rec.get("permissions") or {}
        if rec.get("errors") or not perm.get("stat_ok") or not perm.get("readable"):
            failures["permission_errors"].append({"source": rel, "errors": rec.get("errors"),
                                                   "permissions": perm})
        if (rec.get("pdf") or {}).get("encrypted"):
            failures["skipped"].append({"source": rel, "reason": "encrypted pdf"})
    for rel in sorted(catalog):
        e = catalog[rel]
        if e.get("duplicate_of"):
            failures["duplicates"].append({"source": rel, "duplicate_of": e["duplicate_of"],
                                           "sha256": e.get("sha256")})
    failures["conflicts"] = name_conflicts
    for it in decisions_rejected:
        failures["skipped"].append({"source": it.get("item", {}).get("source"),
                                    "reason": "decision without locator/quote: " + it.get("reason", "")})
    failures["recoveries"] = rollback if rollback else [{
        "note": "dry-run: no files copied; nothing to roll back"}]

    uncertainty = {
        "unclassified": [r for r in sorted(catalog) if catalog[r].get("document_type") == "unknown"],
        "no_text_layer": [r for r in sorted(catalog) if catalog[r].get("has_text_layer") is False],
        "missing_metadata_fields": _missing_fields(catalog),
        "unverified_claims": [c["claim_id"] for c in evidence_map["claims"] if not c["verified"]],
        "decisions_rejected": len(decisions_rejected),
        "has_source_conflict": bool(name_conflicts),
    }
    pending_human = []
    for rel in sorted(catalog):
        e = catalog[rel]
        if e.get("role") != "material":
            continue
        for fname in ("title", "authors", "year", "journal", "doi"):
            if (e.get("metadata") or {}).get(fname) in (None, [], ""):
                pending_human.append({"source": rel, "field": fname,
                                      "reason": "not reported in filename/text/BibTeX/decisions"})

    status = _determine_status(materials, catalog, failures, degradations, dec_errors)

    # 8) 写工作产物
    _write_json(args.work, "inventory.json",
                {"files": files, "dirs": dirs, "count": len(files),
                 "generated_at": ci.now_iso()})
    _write_json(args.work, "organization.json",
                {"catalog": catalog, "versions": versions,
                 "plans": {r: {"category_path": plans[r].get("category_path"),
                               "target_rel": plans[r].get("target_rel")} for r in plans},
                 "conflicts": name_conflicts, "generated_at": ci.now_iso()})
    if rollback:
        _write_json(args.work, "rollback.json", {"created": rollback, "generated_at": ci.now_iso()})

    # 9) 组装最终 data 并落盘
    data["library"] = {
        "catalog": catalog,
        "hierarchy": args.hierarchy.split(","),
        "metadata": {r: catalog[r]["metadata"] for r in sorted(catalog)},
        "versions": versions,
        "evidence_map": evidence_map,
    }
    data["evidence"] = {
        "hashes": {r: files[r].get("sha256") for r in sorted(files)},
        "permissions": {r: files[r].get("permissions", {}) for r in sorted(files)},
        "locators": [{"source": r, "locator": f"{r}::bytes=0-{files[r].get('size', 0)}"}
                     for r in sorted(files)],
        "evidence_map": evidence_map,
        "conflicts": [],
    }
    data["operations"] = _all_ops(catalog, {o["source"]: o for o in ops} if ops else None)
    data["failures"] = failures
    data["status"] = status
    data["sources"] = _build_sources(args, case_path)
    data["uncertainty"] = uncertainty
    data["change_log"] = [
        {"t": ci.now_iso(), "event": "skeleton written"},
        {"t": ci.now_iso(), "event": f"catalog built: {len(catalog)} files"},
        {"t": ci.now_iso(), "event": f"status={status}"},
    ]
    data["errors"] = []
    data["degradations"] = degradations
    data["pending_human"] = pending_human
    data["runtime"] = runtime

    finalize(args, data, case_id, contract, runtime, t0)
    return 0


def _missing_fields(catalog):
    out = {"title": 0, "authors": 0, "year": 0, "journal": 0, "doi": 0}
    for rel in catalog:
        m = catalog[rel].get("metadata") or {}
        for fname in out:
            if m.get(fname) in (None, [], ""):
                out[fname] += 1
    return out


def _determine_status(materials, catalog, failures, degradations, dec_errors):
    if not materials:
        return "insufficient_evidence"
    has_unknown = any(catalog[r].get("document_type") == "unknown" for r in materials)
    has_hard = bool(failures["permission_errors"] or failures["skipped"] or dec_errors)
    has_conflict = bool(failures["conflicts"])
    if has_unknown or has_hard or has_conflict or degradations:
        return "partial"
    return "ok"


def _all_ops(catalog, copy_map=None):
    """统一操作清单：覆盖控制文件、去重归档与复制三种角色。"""
    ops = []
    for rel in sorted(catalog):
        e = catalog[rel]
        if e.get("role") != "material":
            ops.append({"source": rel, "action": "keep_control",
                        "status": "kept" if copy_map is not None else "planned",
                        "note": "task control file; not reorganized"})
        elif not e.get("keep"):
            ops.append({"source": rel, "action": "deduplicate",
                        "status": "archived" if copy_map is not None else "planned",
                        "duplicate_of": e.get("duplicate_of")})
        elif copy_map is not None and rel in copy_map:
            ops.append(copy_map[rel])
        else:
            ops.append({"source": rel, "action": "copy", "status": "planned",
                        "target": e.get("target_rel"), "category": e.get("document_type")})
    return ops


def _build_sources(args, case_path):
    sources = []
    for label, path in (("case.json", case_path),
                        ("task.md", args.task or os.path.join(args.input, "task.md"))):
        if os.path.isfile(path):
            try:
                sha = ci.sha256_file(path)
            except OSError:
                sha = None
            sources.append({"source": label, "path": path, "sha256": sha,
                            "locator": f"{label}::bytes=0-{os.path.getsize(path)}",
                            "quotes": []})
    return sources


def write_result(output, result):
    path = os.path.join(output, "result.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"case_id": result.get("case_id"), "status": "partial",
                           "errors": ["failed to write full result"]}, f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass


def _write_json(work, name, obj):
    path = os.path.join(work, name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def finalize(args, data, case_id, contract, runtime, t0):
    runtime["finished_at"] = ci.now_iso()
    runtime["duration_seconds"] = round(time.time() - t0, 2)
    data["runtime"] = runtime
    write_result(args.output, build_result(case_id, contract, data))


if __name__ == "__main__":
    sys.exit(main())
