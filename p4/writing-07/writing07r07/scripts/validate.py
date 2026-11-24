#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate result.json against the output contract and integrity rules.

Always exits 0 and writes work/validation_report.json with
passed/warnings/errors. Verifiable = every check has a named id.
"""
import argparse
import json
import os
import sys

REQUIRED_STANDARD = ("status", "sources", "uncertainty", "change_log", "artifacts",
                     "pending_human", "errors", "degradations", "runtime")
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--input", default="/input")
    return ap


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        return None, str(exc)


def _atomic_dump(obj, path):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"validate.py: warning: could not write report: {exc}", file=sys.stderr)


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)

    report = {"passed": [], "warnings": [], "errors": [], "status_honest": True}
    result_path = os.path.join(args.output, "result.json")
    result, err = _load_json(result_path)

    if err is not None:
        report["errors"].append({"check": "result_json_parse", "detail": err})
    elif not isinstance(result, dict):
        report["errors"].append({"check": "result_json_type", "detail": "result.json 顶层不是对象"})
    else:
        # case_id
        if "case_id" not in result:
            report["errors"].append({"check": "case_id_missing", "detail": "缺少 case_id 字段"})
        elif result["case_id"] is None:
            report["warnings"].append({"check": "case_id_null", "detail": "case_id 为 null，须在 input_error 场景下说明原因"})
        else:
            report["passed"].append("case_id_present")

        # standard fields
        for key in REQUIRED_STANDARD:
            if key not in result:
                report["errors"].append({"check": "missing_standard_field", "detail": key})

        # status validity
        st = result.get("status")
        if st not in VALID_STATUS:
            report["errors"].append({"check": "invalid_status", "detail": f"status={st!r} 非法"})
        else:
            report["passed"].append("status_valid")

        # contract fields present
        manifest = _load_json(os.path.join(args.work, "input_manifest.json"))[0]
        if isinstance(manifest, dict):
            for fd in manifest.get("contract_fields") or []:
                name = fd.get("name") if isinstance(fd, dict) else fd
                if name not in result:
                    report["errors"].append({"check": "contract_field_missing", "detail": str(name)})
            if not (manifest.get("contract_fields") or []):
                report["warnings"].append({"check": "no_contract_found", "detail": "未发现 output_contract，需人工依据 task.md 确认"})

        # sources entries well-formed
        sources = result.get("sources")
        if isinstance(sources, list):
            for i, s in enumerate(sources):
                if not isinstance(s, dict) or not (s.get("key") and s.get("locator") and s.get("quote") and s.get("kind")):
                    report["warnings"].append({"check": "source_incomplete", "detail": f"sources[{i}] 缺 key/locator/quote/kind 之一"})
            report["passed"].append(f"sources_count={len(sources)}")

        # evidence_map checks
        em_path = os.path.join(args.output, "evidence_map.json")
        if os.path.isfile(em_path):
            em, em_err = _load_json(em_path)
            if em_err is not None:
                report["errors"].append({"check": "evidence_map_parse", "detail": em_err})
            elif isinstance(em, dict):
                bad = [k for k, v in em.items() if not isinstance(v, dict) or not (v.get("locator") and v.get("quote"))]
                for k in bad:
                    report["warnings"].append({"check": "evidence_incomplete", "detail": f"{k} 缺 locator/quote"})
                report["passed"].append(f"evidence_map_entries={len(em)}")

        # main artifact non-empty
        ms_path = os.path.join(args.output, "manuscript.md")
        if os.path.isfile(ms_path):
            try:
                with open(ms_path, "r", encoding="utf-8-sig") as fh:
                    body = fh.read().strip()
                if body:
                    report["passed"].append("manuscript_nonempty")
                else:
                    report["warnings"].append({"check": "manuscript_empty", "detail": "manuscript.md 为空"})
            except (OSError, UnicodeDecodeError) as exc:
                report["warnings"].append({"check": "manuscript_read_failed", "detail": str(exc)})
        else:
            report["warnings"].append({"check": "manuscript_missing", "detail": "尚无 manuscript.md（partial 阶段可接受）"})

        # honesty heuristic: empty materials + status=ok is suspicious
        if st == "ok" and isinstance(manifest, dict) and not (manifest.get("materials") or []):
            report["status_honest"] = False
            report["warnings"].append({"check": "status_inflated", "detail": "无材料却标 ok，建议改为 insufficient_evidence"})

    _atomic_dump(report, os.path.join(args.work, "validation_report.json"))

    print(f"validate: passed={len(report['passed'])} warnings={len(report['warnings'])} errors={len(report['errors'])}")
    for e in report["errors"]:
        print("  ERROR:", e.get("check"), "-", e.get("detail"))
    for w in report["warnings"]:
        print("  WARN :", w.get("check"), "-", w.get("detail"))
    return 0


if __name__ == "__main__":
    sys.exit(run())
