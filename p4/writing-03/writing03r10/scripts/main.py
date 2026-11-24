#!/usr/bin/env python3
"""Entry point for the format-template-adaptation Skill.

Subcommands:
  scaffold  write an idempotent partial result skeleton (do this first, <180s)
  inspect   discover materials, build spec scaffold / content-unit scaffold / auto hints
  verify    validate result.json against output_contract + check quote locatability

Standard library only. Run from the skill root as `python scripts/main.py ...`.
"""
import argparse
import json
import os
import re
import sys

import core_impl


def cmd_scaffold(a):
    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    result, err = core_impl.build_skeleton(a.case)
    if result is None:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        return 2
    out = os.path.join(a.output, "result.json")
    existing, _ = core_impl.read_json_safe(out)
    merged = core_impl.merge_result(existing if isinstance(existing, dict) else {}, result)
    core_impl.write_json(out, merged)
    print(json.dumps({
        "ok": True,
        "result": out,
        "case_id": result["case_id"],
        "contract_fields": result["_meta"]["contract_fields"],
        "contract_fallback_used": result["_meta"]["contract_fallback_used"],
    }, ensure_ascii=False))
    return 0


def cmd_inspect(a):
    os.makedirs(a.work, exist_ok=True)
    index = core_impl.build_material_index([a.input])
    spec = core_impl.build_spec_scaffold()
    content_units = core_impl.build_content_unit_scaffold()

    hints = []
    for mat in index["materials"]:
        if mat.get("extractable"):
            txt, _ = core_impl.extract_material_text(mat["path"])
            if txt:
                hints.extend(core_impl.auto_spec_hints(txt))

    dumps = {
        "material_index": index["materials"],
        "errors": index["errors"],
        "spec_scaffold": spec,
        "auto_spec_hints": hints,
        "content_unit_scaffold": content_units,
    }
    for name, data in dumps.items():
        core_impl.write_json(os.path.join(a.work, name + ".json"), data)

    edir = os.path.join(a.work, "extracted_text")
    os.makedirs(edir, exist_ok=True)
    for mat in index["materials"]:
        if mat.get("extractable"):
            txt, _ = core_impl.extract_material_text(mat["path"])
            if txt is not None:
                safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", mat["name"])
                with open(os.path.join(edir, safe + ".txt"), "w", encoding="utf-8") as f:
                    f.write(txt)

    print(json.dumps({
        "ok": True,
        "materials": len(index["materials"]),
        "extract_errors": len(index["errors"]),
        "spec_items": len(spec),
        "auto_hints": len(hints),
        "work": a.work,
    }, ensure_ascii=False))
    return 0


def cmd_verify(a):
    report = core_impl.verify_contract(a.result, a.case, [a.input, a.work], a.report)
    core_impl.write_json(a.report, report)
    print(json.dumps({
        "ok": report["ok"],
        "exit_code": report["exit_code"],
        "errors": len(report["errors"]),
        "warnings": len(report["warnings"]),
        "report": a.report,
    }, ensure_ascii=False))
    return report["exit_code"]


def main():
    ap = argparse.ArgumentParser(prog="main.py", description="format-template-adaptation scaffolding/verification")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="write idempotent partial result skeleton")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")

    p2 = sub.add_parser("inspect", help="discover materials and build spec/unit scaffolds")
    p2.add_argument("--input", default="/input")
    p2.add_argument("--work", default="/app/work")

    p3 = sub.add_parser("verify", help="validate result.json against contract")
    p3.add_argument("--result", default="/app/output/result.json")
    p3.add_argument("--case", default="/input/case.json")
    p3.add_argument("--report", default="/app/output/verification_report.json")
    p3.add_argument("--input", default="/input")
    p3.add_argument("--work", default="/app/work")

    a = ap.parse_args()
    if a.cmd == "scaffold":
        return cmd_scaffold(a)
    if a.cmd == "inspect":
        return cmd_inspect(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
