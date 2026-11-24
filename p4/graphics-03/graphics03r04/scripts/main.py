#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly-skill orchestrator (standard library only).

Subcommands:
  scaffold  Write a partial result.json skeleton immediately (first 3 minutes).
  build     Convert spec.json -> layered SVG via build_svg.render.
  check     Validate result.json against the output contract and file existence.

Exit codes (check): 0 = pass, 1 = blocking error, 2 = warnings only.
All paths are resolved relative to this file unless absolute. Defaults point at
the runtime locations /input, /app/output, /app/work.
"""
import argparse
import datetime
import json
import os
import sys
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ALLOWED_STATUS = {"success", "partial", "failed", "insufficient_evidence", "input_error"}
ALLOWED_ENTITY_TYPES = {"gene", "protein", "complex", "family", "cell_type",
                        "compartment", "small_molecule", "other"}
ALLOWED_RELATION_TYPES = {"activation", "inhibition", "binding", "modification",
                          "transport", "conversion", "unknown"}
ALLOWED_EVIDENCE_STATUS = {"direct", "inferred", "missing"}
ALLOWED_DIRECTIONS = {"forward", "backward", "bidirectional", "unknown"}
ALLOWED_FORMATS = {"png", "pdf", "svg"}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default=None):
    """Read JSON with UTF-8 BOM tolerance and bad-encoding/bad-json fallback."""
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return default


def empty_result(case_id="unknown", status="partial"):
    return {
        "case_id": case_id,
        "status": status,
        "task_title": None,
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "degradations": [],
        "errors": [],
        "change_log": [],
        "runtime": {
            "started_at": now_iso(),
            "finished_at": None,
            "seconds": 0,
            "requests_used": 0,
        },
    }


def _extract_case_id(case_path):
    case = load_json(case_path, None)
    if isinstance(case, dict):
        for key in ("case_id", "id", "caseId"):
            if case.get(key):
                return str(case[key])
    return None


def cmd_scaffold(args):
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    case_id = _extract_case_id(args.case)
    status = "partial"
    limitations = []
    if case_id is None:
        if os.path.exists(args.case):
            case_id = "unknown"
            limitations.append("case.json 存在但无 case_id/id/caseId 字段")
        else:
            case_id = "unknown"
            status = "input_error"
            limitations.append("未找到 case.json：%s" % args.case)

    result = empty_result(case_id, status)
    result["task_title"] = _extract_title(args.case)
    result["change_log"] = [{
        "time": now_iso(),
        "action": "skeleton",
        "status": status,
        "detail": "骨架落盘：case_id 已填、契约字段齐备，后续增量改进",
    }]
    if limitations:
        result["limitations"] = limitations

    out_path = os.path.join(args.out, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({"ok": True, "path": out_path, "status": status,
                      "case_id": case_id}, ensure_ascii=False))
    return 0


def _extract_title(case_path):
    case = load_json(case_path, None)
    if isinstance(case, dict):
        for key in ("title", "task_title", "name"):
            if case.get(key):
                return str(case[key])
    return None


def cmd_build(args):
    sys.path.insert(0, os.path.join(HERE))
    try:
        import build_svg
    except ImportError as e:  # pragma: no cover
        print(json.dumps({"ok": False, "error": "build_svg import failed: %s" % e},
                         ensure_ascii=False))
        return 1
    return build_svg.main(["--spec", args.spec, "--out", args.out])


def _has_quote(evidence):
    ev = evidence or []
    if not isinstance(ev, list):
        return False
    for item in ev:
        if isinstance(item, dict) and str(item.get("quote", "")).strip():
            return True
    return False


def _check_file(path, fmt, out_dir):
    """Return (ok, message). Checks existence and format magic where applicable."""
    if not path:
        return False, "empty path"
    full = path if os.path.isabs(path) else os.path.join(out_dir, path)
    if not os.path.exists(full):
        return False, "file missing: %s" % full
    if fmt == "svg":
        try:
            ET.parse(full)
        except Exception as e:
            return False, "svg unparsable: %s" % e
    elif fmt == "pdf":
        with open(full, "rb") as f:
            head = f.read(5)
        if not head.startswith(b"%PDF-"):
            return False, "pdf magic mismatch"
    elif fmt == "png":
        with open(full, "rb") as f:
            head = f.read(8)
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            return False, "png magic mismatch"
    return True, ""


def validate_result(result, out_dir):
    """Return (errors, warnings) lists of strings."""
    errors = []
    warnings = []

    if not isinstance(result, dict):
        return ["result.json 顶层必须是对象"], []

    if not str(result.get("case_id", "")).strip():
        errors.append("case_id 缺失或为空")
    status = str(result.get("status", ""))
    if status not in ALLOWED_STATUS:
        errors.append("status 非法: %r（允许 %s）" % (status, ", ".join(sorted(ALLOWED_STATUS))))
    if not isinstance(result.get("change_log"), list) or not result["change_log"]:
        errors.append("change_log 缺失或为空；首条应为骨架落盘")

    for key in ("editable_source_files", "export_artifacts", "entities", "relations",
                "legend", "data_mappings", "sources", "uncertainty", "pending_human",
                "limitations", "degradations", "errors"):
        if key not in result:
            errors.append("缺少顶层字段: %s" % key)
        elif not isinstance(result[key], list):
            errors.append("字段 %s 必须是数组" % key)

    # editable source files
    esf = result.get("editable_source_files") or []
    if not esf:
        warnings.append("editable_source_files 为空")
    for i, item in enumerate(esf):
        if not isinstance(item, dict) or not item.get("path"):
            errors.append("editable_source_files[%d] 缺 path" % i)
            continue
        fmt = str(item.get("format", "svg")).lower()
        ok, msg = _check_file(item["path"], fmt, out_dir)
        if not ok:
            errors.append("editable_source_files[%d]: %s" % (i, msg))

    # export artifacts
    exf = result.get("export_artifacts") or []
    for i, item in enumerate(exf):
        if not isinstance(item, dict) or not item.get("path"):
            errors.append("export_artifacts[%d] 缺 path" % i)
            continue
        fmt = str(item.get("format", "")).lower()
        if fmt not in ALLOWED_FORMATS:
            errors.append("export_artifacts[%d] format 非法: %r" % (i, fmt))
            continue
        ok, msg = _check_file(item["path"], fmt, out_dir)
        if not ok:
            errors.append("export_artifacts[%d]: %s" % (i, msg))

    # entities
    entity_ids = set()
    for i, e in enumerate(result.get("entities") or []):
        if not isinstance(e, dict):
            errors.append("entities[%d] 必须是对象" % i)
            continue
        if not str(e.get("id", "")).strip():
            errors.append("entities[%d] 缺 id" % i)
        if not str(e.get("label", "")).strip():
            errors.append("entities[%d] 缺 label" % i)
        t = str(e.get("type", "")).lower()
        if t not in ALLOWED_ENTITY_TYPES:
            errors.append("entities[%d] type 非法: %r" % (i, t))
        es = str(e.get("evidence_status", "direct")).lower()
        if es not in ALLOWED_EVIDENCE_STATUS:
            errors.append("entities[%d] evidence_status 非法: %r" % (i, es))
        if es in ("direct", "inferred") and not _has_quote(e.get("evidence")):
            errors.append("entities[%d] evidence_status=%s 但无 quote 非空证据" % (i, es))
        if es == "missing" and e.get("display", True) is not False:
            warnings.append("entities[%d] evidence_status=missing 仍 display:true，将灰虚线渲染" % i)
        eid = str(e.get("id", ""))
        if eid:
            entity_ids.add(eid)

    # relations
    for i, r in enumerate(result.get("relations") or []):
        if not isinstance(r, dict):
            errors.append("relations[%d] 必须是对象" % i)
            continue
        if not str(r.get("source", "")).strip():
            errors.append("relations[%d] 缺 source" % i)
        if not str(r.get("target", "")).strip():
            errors.append("relations[%d] 缺 target" % i)
        t = str(r.get("type", "")).lower()
        if t not in ALLOWED_RELATION_TYPES:
            errors.append("relations[%d] type 非法: %r" % (i, t))
        d = str(r.get("direction", "forward")).lower()
        if d not in ALLOWED_DIRECTIONS:
            errors.append("relations[%d] direction 非法: %r" % (i, d))
        es = str(r.get("evidence_status", "direct")).lower()
        if es not in ALLOWED_EVIDENCE_STATUS:
            errors.append("relations[%d] evidence_status 非法: %r" % (i, es))
        if es in ("direct", "inferred") and not _has_quote(r.get("evidence")):
            errors.append("relations[%d] evidence_status=%s 但无 quote 非空证据" % (i, es))
        if entity_ids:
            for end in ("source", "target"):
                val = str(r.get(end, ""))
                if val and val not in entity_ids:
                    warnings.append("relations[%d] %s=%r 未在 entities 中" % (i, end, val))

    # data mappings: a numeric value must have evidence
    for i, dm in enumerate(result.get("data_mappings") or []):
        if not isinstance(dm, dict):
            continue
        if str(dm.get("value", "")).strip() and not _has_quote(dm.get("evidence")):
            errors.append("data_mappings[%d] 含数值但无 evidence" % i)

    # uncertainty entries
    for i, u in enumerate(result.get("uncertainty") or []):
        if not isinstance(u, dict):
            continue
        if not str(u.get("issue", "")).strip():
            warnings.append("uncertainty[%d] 缺 issue" % i)
        if str(u.get("status", "")).lower() not in ("unresolved", "deferred", "resolved"):
            warnings.append("uncertainty[%d] status 应取 unresolved/deferred/resolved" % i)

    # direction unknown should be surfaced
    unk_els = {str(u.get("element", "")) for u in (result.get("uncertainty") or []) if isinstance(u, dict)}
    for i, r in enumerate(result.get("relations") or []):
        if isinstance(r, dict) and str(r.get("direction", "")).lower() == "unknown":
            rid = str(r.get("id", "")).strip()
            if rid and rid not in unk_els:
                warnings.append("relations[%d] direction=unknown 未出现在 uncertainty" % i)

    # status != success must explain limits
    if status != "success":
        if not (result.get("limitations") or result.get("degradations")):
            warnings.append("status=%s 但 limitations/degradations 均为空" % status)
    if status == "success" and not exf:
        warnings.append("status=success 但无导出件")

    return errors, warnings


def cmd_check(args):
    result = load_json(args.result, None)
    if result is None:
        print(json.dumps({"ok": False, "errors": ["无法读取 result.json: %s" % args.result]},
                         ensure_ascii=False))
        return 1
    errors, warnings = validate_result(result, args.out)
    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        return 1
    if warnings:
        return 2
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="weekly-skill orchestrator (scaffold/build/check)")
    sub = ap.add_subparsers(dest="command")

    p_s = sub.add_parser("scaffold", help="write partial result.json skeleton")
    p_s.add_argument("--case", default="/input/case.json")
    p_s.add_argument("--out", default="/app/output")
    p_s.add_argument("--work", default="/app/work")
    p_s.set_defaults(func=cmd_scaffold)

    p_b = sub.add_parser("build", help="spec.json -> layered SVG")
    p_b.add_argument("--spec", default="/app/work/spec.json")
    p_b.add_argument("--out", default="/app/output")
    p_b.set_defaults(func=cmd_build)

    p_c = sub.add_parser("check", help="validate result.json contract")
    p_c.add_argument("--result", default="/app/output/result.json")
    p_c.add_argument("--out", default="/app/output")
    p_c.set_defaults(func=cmd_check)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "command", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
