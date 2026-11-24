#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 的执行入口（契约逐字段对齐版）。

子命令（评测 Agent 应按 SKILL.md 顺序执行）：

    python scripts/main.py init      --input /input --work /app/work --output /app/output
    python scripts/main.py show      --input /input --work /app/work --output /app/output
    # 模型阅读 show 输出与实际材料后，把语义判断写进 /app/work/draft.json
    python scripts/main.py check     --input /input --work /app/work --output /app/output
    python scripts/main.py build     --input /input --work /app/work --output /app/output
    python scripts/main.py selfcheck --input /input --work /app/work --output /app/output
    python scripts/main.py contract  --input /input --work /app/work --output /app/output

三个路径参数都可覆盖默认目录；默认路径 /input、/app/work、/app/output 也能直接跑。
退出码恒为 0：真实问题写进 result.json 的 errors/degradations，不靠退出码表达。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import libcore as C  # noqa: E402

_START = time.time()

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

DRAFT_TEMPLATE = {
    "case_id": "",
    "reviewed": False,
    "notes": [],
    "queries": [],
    "_example_note": {
        "target": "F001",
        "kind": "alias|version|metadata|correction",
        "text": "模型判定：…（说明关系或修正，一句话）",
        "basis": "…（判定依据）",
        "confidence": "explicit|inferred|heuristic",
        "evidence": [
            {"source_id": "F001", "locator": "materials/x.pdf", "quote": "逐字片段"}
        ],
    },
    "_example_query": {
        "query_id": "Q01",
        "answer": "…（检索答案）",
        "status": "answered|no_direct_match|insufficient_evidence",
        "evidence": [
            {"source_id": "F001", "locator": "materials/x.pdf", "quote": "逐字片段"}
        ],
    },
}


# ---------------------------------------------------------------- 目录与输入

def resolve_dirs(args):
    input_dir = Path(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    work_dir = Path(args.work) if getattr(args, "work", None) else DEFAULT_WORK
    output_dir = Path(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return input_dir, work_dir, output_dir


def ensure_output(output_dir, work_dir):
    """确认输出目录可写；不可写则依次降级。返回 (实际目录, 降级说明)。"""
    for cand in (output_dir, work_dir / "output_fallback", SKILL_ROOT / "skill_output"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return cand, None if cand == output_dir else f"output_fallback:{cand.name}"
        except OSError:
            continue
    return output_dir, "output_not_writable"


def derive_case_id(case):
    if isinstance(case, dict):
        for key in ("case_id", "id", "case"):
            if case.get(key) not in (None, ""):
                return str(case[key])
    return "unknown"


# ---------------------------------------------------------------- 结果组装

def build_skeleton(case_id, contract, status="partial"):
    result = {}
    for spec in contract.fields:
        result[spec["field"]] = spec.get("default")
    for key in C.ENVELOPE_KEYS:
        if key in result:
            continue
        result[key] = [] if key in C.LIST_ENVELOPE_KEYS else None
    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {}
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    result["contract_audit"] = {"source": contract.source, "total_fields": len(contract.fields),
                                "populated": 0, "pending": len(contract.fields), "fields": []}
    result["runtime"] = {}
    return result


# ---------------------------------------------------------------- draft 应用

def apply_draft(library, draft):
    """把模型的语义判断并入确定性结果；显式版本备注可消解冲突。"""
    analysis = library.get("analysis", {})
    explicit_relations = list(analysis.get("explicit_relations", []))
    metadata = dict(analysis.get("metadata", {}))
    conflicts = list(analysis.get("conflicts", []))
    version_groups = list(analysis.get("version_groups", []))
    resolved_stems = set()
    applied = []
    for n in (draft or {}).get("notes", []):
        kind = n.get("kind")
        target = n.get("target")
        if not target:
            continue
        if kind == "version":
            for c in conflicts:
                if target in c["member_ids"] or target == c.get("stem"):
                    resolved_stems.add(c.get("stem"))
            for vg in version_groups:
                if target in vg["member_ids"] or target == vg.get("stem"):
                    vg.setdefault("notes", []).append(n.get("text", ""))
            explicit_relations.append({"kind": "version", "target": target,
                                       "text": n.get("text"), "basis": n.get("basis"),
                                       "confidence": n.get("confidence"),
                                       "evidence": n.get("evidence", [])})
            applied.append({"target": target, "kind": "version", "effect": "version_note_applied"})
        elif kind == "alias":
            explicit_relations.append({"kind": "alias", "target": target,
                                       "text": n.get("text"), "basis": n.get("basis"),
                                       "confidence": n.get("confidence"),
                                       "evidence": n.get("evidence", [])})
            applied.append({"target": target, "kind": "alias", "effect": "alias_note_applied"})
        elif kind == "metadata":
            metadata.setdefault(target, []).append(n.get("text", ""))
            explicit_relations.append({"kind": "metadata", "target": target,
                                       "text": n.get("text"), "basis": n.get("basis"),
                                       "confidence": n.get("confidence"),
                                       "evidence": n.get("evidence", [])})
            applied.append({"target": target, "kind": "metadata", "effect": "metadata_note_applied"})
        else:  # correction 等
            explicit_relations.append({"kind": kind, "target": target,
                                       "text": n.get("text"), "basis": n.get("basis"),
                                       "confidence": n.get("confidence"),
                                       "evidence": n.get("evidence", [])})
            applied.append({"target": target, "kind": kind, "effect": "correction_recorded"})
    conflicts = [c for c in conflicts if c.get("stem") not in resolved_stems]
    analysis["conflicts"] = conflicts
    analysis["version_groups"] = version_groups
    analysis["explicit_relations"] = explicit_relations
    analysis["metadata"] = metadata
    library["analysis"] = analysis
    return library, applied


# ---------------------------------------------------------------- 各命令

def do_init(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    output_dir, fallback = ensure_output(output_dir, work_dir)

    case, case_err = C.read_json(input_dir / "case.json")
    case_id = derive_case_id(case) if isinstance(case, dict) else "unknown"
    contract = C.discover_contract(case)
    contract_source = contract.source

    files, dirs, errors, degradations = C.scan_tree(input_dir)
    alias_groups, version_groups, conflicts = C.group_files(files)
    explicit = C.extract_explicit(case) if isinstance(case, dict) else {
        "relations": [], "metadata": {}, "queries_raw": []}
    queries = C.normalize_queries(explicit["queries_raw"])

    # 显式关系：为每条关系补 quote_key（case.json 键名，逐字可命中）
    relations = []
    for rel in explicit["relations"]:
        rel["quote_key"] = rel["source"].rsplit(".", 1)[-1]
        relations.append(rel)

    analysis = {
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
        "explicit_relations": relations,
        "metadata": explicit["metadata"],
        "operations": [],
        "recovery_plan": [],
    }
    library = {
        "case_id": case_id,
        "files": files,
        "directories": dirs,
        "analysis": analysis,
        "queries": queries,
        "errors": errors,
        "degradations": degradations,
        "input_error": case_err,
    }

    C.write_json(work_dir / "library.json", library)
    C.write_json(work_dir / "contract_spec.json", contract.to_dict())
    C.write_json(work_dir / "draft.json", dict(DRAFT_TEMPLATE, case_id=case_id))

    result = build_skeleton(case_id, contract, "partial")
    log = C.ChangeLog()
    log.record("result", None, "skeleton", "落盘 partial 结果骨架", "init",
               "applied" if not case_err else "case_unreadable")
    for spec in contract.fields:
        log.record("contract_field:" + spec["field"], None,
                   "null" if spec.get("default") is None else spec.get("default"),
                   "初始化契约字段，待 build 回填", "init", "pending")
    if contract_source == "fallback":
        result["degradations"] = [{"issue": "contract_fallback",
                                   "note": "case.json 无 output_contract，使用默认契约"}]
    if case_err:
        result["errors"] = [case_err]
        result["status"] = "input_error"
    result["change_log"] = log.to_list()
    result["runtime"] = {"wall_seconds": round(time.time() - _START, 3),
                         "skill_version": C.SKILL_VERSION, "python": platform.python_version()}
    C.write_json(output_dir / "result.json", result)

    print("[init] case_id=%s contract_source=%s fields=%d files=%d errors=%d"
          % (case_id, contract_source, len(contract.fields), len(files), len(errors)))
    print("[init] wrote %s" % (output_dir / "result.json"))
    return 0


def do_show(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    library, err = C.read_json(work_dir / "library.json")
    if err:
        print("[show] library.json 不可读: %s" % err)
        return 0
    contract_spec, _ = C.read_json(work_dir / "contract_spec.json")
    print("== case_id: %s ==" % library.get("case_id"))
    print("== 输出契约字段（%s，%d 个）==" % (contract_spec.get("source"), len(contract_spec.get("fields", []))))
    for f in contract_spec.get("fields", []):
        print("  - %s (required=%s)" % (f.get("field"), f.get("required")))
    print("== 文件清单（%d）==" % len(library.get("files", [])))
    for f in library.get("files", []):
        print("  %s %-40s %s %s sha256=%s status=%s" % (
            f.get("id"), f.get("rel_path"), f.get("kind"), f.get("size"),
            (f.get("sha256") or "?")[:12], f.get("file_status")))
    a = library.get("analysis", {})
    print("== 别名组（%d）==" % len(a.get("alias_groups", [])))
    for g in a.get("alias_groups", []):
        print("  sha256=%.12s members=%s" % (g.get("sha256"), ", ".join(g.get("member_ids", []))))
    print("== 版本组（%d）==" % len(a.get("version_groups", [])))
    for g in a.get("version_groups", []):
        print("  %s stem=%s members=%s" % (g.get("group_id"), g.get("stem"),
                                            ", ".join(g.get("member_ids", []))))
    print("== 冲突（%d）==" % len(a.get("conflicts", [])))
    for g in a.get("conflicts", []):
        print("  stem=%s members=%s" % (g.get("stem"), ", ".join(g.get("member_ids", []))))
    print("== 显式关系（%d）==" % len(a.get("explicit_relations", [])))
    for r in a.get("explicit_relations", []):
        print("  kind=%s target=%s source=%s" % (r.get("kind"), r.get("target"), r.get("source")))
    print("== 检索问题（%d）==" % len(library.get("queries", [])))
    for q in library.get("queries", []):
        print("  %s %s" % (q.get("query_id"), q.get("text")))
    print("== errors（%d）/ degradations（%d）==" % (
        len(library.get("errors", [])), len(library.get("degradations", []))))
    for e in library.get("errors", []):
        print("  ERROR %s" % json.dumps(e, ensure_ascii=False))
    for d in library.get("degradations", []):
        print("  DEGRADE %s" % json.dumps(d, ensure_ascii=False))
    print("[show] 请阅读以上内容与实际材料，把语义判断写入 %s" % (work_dir / "draft.json"))
    return 0


def do_check(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    draft, err = C.read_json(work_dir / "draft.json")
    library, _ = C.read_json(work_dir / "library.json")
    files = (library or {}).get("files", [])
    report = {"checks": [], "passed": 0, "failed": 0, "issues": []}
    if err or not isinstance(draft, dict):
        report["issues"].append({"check": "draft_readable", "ok": False, "detail": str(err or "not_dict")})
    else:
        report["checks"].append({"check": "draft_readable", "ok": True})
        report["passed"] += 1
        for kind in ("notes", "queries"):
            items = draft.get(kind)
            if not isinstance(items, list):
                report["issues"].append({"check": "draft.%s_list" % kind, "ok": False,
                                         "detail": "missing_or_not_list"})
                continue
            report["checks"].append({"check": "draft.%s_list" % kind, "ok": True,
                                     "count": len(items)})
            report["passed"] += 1
            for item in items:
                if not isinstance(item, dict):
                    report["issues"].append({"check": "item", "ok": False, "detail": "not_dict"})
                    continue
                for ev in item.get("evidence", []):
                    ok, note = C.verify_evidence(input_dir, ev, files)
                    report["checks"].append({
                        "check": "evidence", "ok": ok,
                        "source_id": ev.get("source_id"), "quote": (ev.get("quote") or "")[:40],
                        "note": note,
                    })
                    if ok:
                        report["passed"] += 1
                    else:
                        report["failed"] += 1
                        report["issues"].append({
                            "check": "evidence", "ok": False,
                            "source_id": ev.get("source_id"),
                            "detail": note, "quote": (ev.get("quote") or "")[:40],
                        })
                if kind == "queries":
                    st = item.get("status")
                    if st not in ("answered", "no_direct_match", "insufficient_evidence"):
                        report["issues"].append({"check": "query_status", "ok": False,
                                                 "query_id": item.get("query_id"),
                                                 "detail": "bad_status:%s" % st})
                        report["failed"] += 1
    report["summary"] = {"passed": report["passed"], "failed": report["failed"]}
    C.write_json(work_dir / "check_report.json", report)
    print("[check] passed=%d failed=%d" % (report["passed"], report["failed"]))
    for issue in report["issues"]:
        print("  ISSUE %s" % json.dumps(issue, ensure_ascii=False))
    return 0


def do_build(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    output_dir, fallback = ensure_output(output_dir, work_dir)

    library, _ = C.read_json(work_dir / "library.json")
    contract_spec, _ = C.read_json(work_dir / "contract_spec.json")
    draft, draft_err = C.read_json(work_dir / "draft.json")
    if not isinstance(library, dict):
        library = {"case_id": "unknown", "files": [], "directories": [],
                   "analysis": {}, "queries": [], "errors": [], "degradations": []}
    case_id = library.get("case_id", "unknown")
    fields = (contract_spec or {}).get("fields", [])
    if not fields:
        contract = C.discover_contract({})
        fields = contract.fields
        contract_source = contract.source
    else:
        contract_source = contract_spec.get("source", "unknown")

    has_draft = isinstance(draft, dict) and bool(
        draft.get("reviewed") or draft.get("notes") or draft.get("queries"))
    if has_draft:
        library, applied = apply_draft(library, draft)
    else:
        applied = []

    canonical = C.compute_contract_values(library, draft if has_draft else None, case_id)

    result = build_skeleton(case_id, C.ContractSpec(fields, contract_source), "partial")
    log = C.ChangeLog()
    log.record("result", "skeleton", "build", "开始收口合并", "build")

    audit_fields = []
    populated = 0
    for spec in fields:
        field = spec["field"]
        key = C._norm_key(field)
        if key in canonical and canonical[key] is not None:
            value = canonical[key]
            src = "computed:" + key
            populated_flag = True
        else:
            value = None
            src = "pending"
            populated_flag = False
        result[field] = value
        if populated_flag:
            populated += 1
        log.record("contract_field:" + field, "null", "filled" if populated_flag else "null",
                   "回填输出契约字段" if populated_flag else "未找到对应来源，如实置 null",
                   "build", "applied" if populated_flag else "pending")
        audit_fields.append({
            "field": field, "present": True, "populated": populated_flag,
            "type": type(value).__name__ if value is not None else "null",
            "source": src, "change_seq": log._seq,
        })

    result["result"] = {
        "summary": "盘点 %d 个文件、%d 个目录；别名组 %d、版本组 %d、冲突 %d；检索问题 %d" % (
            len(library.get("files", [])), len(library.get("directories", [])),
            len(library.get("analysis", {}).get("alias_groups", [])),
            len(library.get("analysis", {}).get("version_groups", [])),
            len(library.get("analysis", {}).get("conflicts", [])),
            len(library.get("queries", [])),
        ),
    }
    result["claims"] = C.build_claims(library, draft if has_draft else None)
    result["sources"] = C.build_sources(library)
    result["uncertainties"] = [
        {"target": "heuristic_groups", "note": "版本/别名分组为启发式，显式备注可覆盖",
         "confidence": "heuristic"},
    ]
    result["uncertainty"] = {
        "summary": "启发式分组存在不确定性；冲突未擅自裁定，进 pending_human",
        "count": len(library.get("analysis", {}).get("conflicts", []))
                 + (1 if contract_source == "fallback" else 0),
        "items": result["uncertainties"],
    }
    result["pending_human"] = C.build_pending(library, audit_fields)
    errors_list = list(library.get("errors", []))
    if library.get("input_error"):
        errors_list.insert(0, library["input_error"])
    result["errors"] = errors_list
    result["degradations"] = C.build_degradations(library, C.ContractSpec(fields, contract_source),
                                                  draft if has_draft else None, fallback)
    result["contract_audit"] = {
        "source": contract_source,
        "total_fields": len(fields),
        "populated": populated,
        "pending": len(fields) - populated,
        "fields": audit_fields,
    }
    for op in applied:
        log.record("draft_apply", op.get("kind"), op.get("effect"), "模型语义判断并入",
                   "build", "applied")
    result["change_log"] = log.to_list()
    result["runtime"] = {"wall_seconds": round(time.time() - _START, 3),
                         "skill_version": C.SKILL_VERSION, "python": platform.python_version()}

    # 状态判定
    input_err = library.get("input_error")
    material_files = [f for f in library.get("files", []) if not f.get("is_task")]
    empty_material = len(material_files) == 0
    has_real_errors = len(result["errors"]) > 0
    result["status"] = C.decide_status(populated, len(fields), has_draft,
                                       bool(input_err), has_real_errors, empty_material)

    C.write_json(output_dir / "result.json", result)
    C.write_json(output_dir / "library_index.json", library)
    C.write_json(output_dir / "contract_report.json", result["contract_audit"])

    # evidence_map.json
    evmap = {}
    for cl in result["claims"]:
        evmap[cl["claim_id"]] = cl["evidence"]
    C.write_json(output_dir / "evidence_map.json", evmap)

    # inventory.md（可读交接清单）
    lines = ["# 科研文档库盘点交接清单", "", "case_id: %s" % case_id, ""]
    lines += ["## 文件清单", ""]
    for f in library.get("files", []):
        lines.append("- %s `%s` (%s, %d B, sha256 %.12s)" % (
            f.get("id"), f.get("rel_path"), f.get("kind"), f.get("size"),
            (f.get("sha256") or "?")))
    lines += ["", "## 版本/别名/冲突", ""]
    for g in library.get("analysis", {}).get("alias_groups", []):
        lines.append("- 别名组: %s" % ", ".join(g["member_ids"]))
    for g in library.get("analysis", {}).get("version_groups", []):
        lines.append("- 版本组 %s: %s" % (g["group_id"], ", ".join(g["member_ids"])))
    for g in library.get("analysis", {}).get("conflicts", []):
        lines.append("- 冲突: %s（%s）" % (", ".join(g["member_ids"]), g.get("reason")))
    lines += ["", "## 检索问题", ""]
    for qa in (draft or {}).get("queries", []):
        lines.append("- %s: %s（%s）" % (qa.get("query_id"), qa.get("answer"), qa.get("status")))
    lines += ["", "## 待人工处理", ""]
    for p in result["pending_human"]:
        lines.append("- %s: %s" % (p.get("target"), p.get("issue")))
    (output_dir / "inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("[build] status=%s contract %d/%d populated" % (result["status"], populated, len(fields)))
    print("[build] wrote result.json, library_index.json, contract_report.json, evidence_map.json, inventory.md")
    return 0


def do_selfcheck(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    checks = []
    ok = True
    result, err = C.read_json(output_dir / "result.json")
    if err:
        checks.append({"check": "result.json_parse", "ok": False, "detail": err.get("error")})
        ok = False
    else:
        checks.append({"check": "result.json_parse", "ok": True})
        if result.get("case_id"):
            checks.append({"check": "case_id", "ok": True, "value": result["case_id"]})
        else:
            checks.append({"check": "case_id", "ok": False})
            ok = False
        if result.get("status") in C.ALLOWED_STATUS:
            checks.append({"check": "status", "ok": True, "value": result["status"]})
        else:
            checks.append({"check": "status", "ok": False, "value": result.get("status")})
            ok = False
        for key in ("sources", "uncertainty", "change_log", "pending_human",
                    "contract_audit", "errors", "degradations"):
            if key in result:
                checks.append({"check": "envelope:" + key, "ok": True})
            else:
                checks.append({"check": "envelope:" + key, "ok": False})
                ok = False
        audit = result.get("contract_audit") or {}
        for f in audit.get("fields", []):
            if not f.get("present"):
                checks.append({"check": "contract:" + f["field"], "ok": False})
                ok = False
    for name in ("library_index.json", "evidence_map.json", "inventory.md",
                 "contract_report.json"):
        p = output_dir / name
        if p.exists() and p.stat().st_size > 0:
            checks.append({"check": "artifact:" + name, "ok": True})
        else:
            checks.append({"check": "artifact:" + name, "ok": False})
            ok = False
    report = {"passed": sum(1 for c in checks if c["ok"]),
              "failed": sum(1 for c in checks if not c["ok"]),
              "ok": ok, "checks": checks}
    C.write_json(work_dir / "selfcheck_report.json", report)
    print("[selfcheck] %s (%d passed / %d failed)" % ("PASS" if ok else "FAIL",
                                                      report["passed"], report["failed"]))
    for c in checks:
        if not c["ok"]:
            print("  FAIL %s" % json.dumps(c, ensure_ascii=False))
    return 0


def do_contract(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    result, err = C.read_json(output_dir / "result.json")
    if err:
        print("[contract] result.json 不可读: %s" % err)
        return 0
    audit = result.get("contract_audit", {})
    print("[contract] source=%s total=%d populated=%d pending=%d" % (
        audit.get("source"), audit.get("total_fields"), audit.get("populated"),
        audit.get("pending")))
    for f in audit.get("fields", []):
        print("  %-24s present=%s populated=%s type=%s source=%s" % (
            f.get("field"), f.get("present"), f.get("populated"),
            f.get("type"), f.get("source")))
    return 0


# ---------------------------------------------------------------- 入口

def main(argv=None):
    parser = argparse.ArgumentParser(description="科研文档库修复 Skill（契约逐字段对齐版）")
    parser.add_argument("command", choices=["init", "show", "check", "build", "selfcheck", "contract"])
    parser.add_argument("--input", default=None)
    parser.add_argument("--work", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    handlers = {
        "init": do_init, "show": do_show, "check": do_check,
        "build": do_build, "selfcheck": do_selfcheck, "contract": do_contract,
    }
    try:
        handlers[args.command](args)
    except Exception as e:  # noqa: BLE001 — 退出码恒为 0，异常降级
        print("[%s] ERROR: %s" % (args.command, e), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
