#!/usr/bin/env python3
"""main.py — figure-ir-skill entry point (Python standard library only).

Subcommands:
  init      write a partial result skeleton early (do this within the first
            3 minutes of the 900 s / 64-request budget)
  build     IR -> deterministic layered editable SVG + build_meta.json
  validate  IR + SVG -> checks.json
  bundle    copy SVG/exports into the output dir, merge IR+checks into result.json
  finalize  set final status and artifact inventory
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core_impl as C  # noqa: E402


def add_common(sp, out_default):
    sp.add_argument("--out", default=out_default, help="output file")


def cmd_init(a):
    case, err = C.read_json(a.case)
    case_id = "unknown"
    title = ""
    contract = None
    if case is not None:
        case_id = case.get("case_id") or case.get("id") or "unknown"
        title = case.get("title", "")
        contract = case.get("output_contract")
    if err:
        title = title or f"(input_error: {err})"
    rm = C.ResultManager(a.out)
    result = rm.init(case_id, title, contract=contract)
    fields = [k for k in result if k in C.DEFAULT_CONTRACT or (contract and k in contract)]
    print(C.json_dumps({"ok": True, "case_id": case_id, "contract_fields": fields}))
    return 0


def cmd_build(a):
    ir, err = C.read_json(a.ir)
    if ir is None:
        print(C.json_dumps({"ok": False, "error": err}), file=sys.stderr)
        return 1
    if not isinstance(ir, dict):
        print(C.json_dumps({"ok": False, "error": "ir.json must be a JSON object"}), file=sys.stderr)
        return 1
    meta = C.build_svg(ir, a.out)
    C.write_json(a.meta, meta)
    print(C.json_dumps({"ok": True, **meta}))
    return 0


def cmd_validate(a):
    ir, err = C.read_json(a.ir)
    if ir is None:
        print(C.json_dumps({"ok": False, "error": err}), file=sys.stderr)
        return 1
    checks = C.validate(ir, a.svg)
    summary = {
        "pass": sum(1 for c in checks if c["status"] == "pass"),
        "fail": sum(1 for c in checks if c["status"] == "fail"),
        "warn": sum(1 for c in checks if c["status"] == "warn"),
    }
    C.write_json(a.out, {"checks": checks, "summary": summary})
    print(C.json_dumps({"ok": True, "summary": summary, "checks": checks}))
    return 0


def cmd_bundle(a):
    partial = C.bundle(a.ir, a.meta, a.checks, a.out, a.exports or [])
    print(C.json_dumps({
        "ok": True,
        "n_entities": len(partial.get("entities", [])),
        "n_relations": len(partial.get("relations", [])),
        "n_editable_sources": len(partial.get("editable_sources", [])),
        "n_exports": len(partial.get("exports", [])),
    }))
    return 0


def cmd_finalize(a):
    rm = C.ResultManager(a.out)
    out_dir = Path(a.out).parent
    artifacts = []
    if out_dir.exists():
        for p in sorted(out_dir.iterdir()):
            if p.is_file():
                try:
                    artifacts.append({"path": str(p), "size": p.stat().st_size})
                except OSError:
                    artifacts.append({"path": str(p), "size": None})
    result = rm.finalize(a.status, artifacts=artifacts)
    print(C.json_dumps({"ok": True, "status": result["status"], "n_artifacts": len(artifacts)}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write early result skeleton")
    p.add_argument("--case", default="/input/case.json")
    add_common(p, "/app/output/result.json")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build", help="IR -> layered SVG")
    p.add_argument("--ir", default="/app/work/ir.json")
    add_common(p, "/app/work/figure.svg")
    p.add_argument("--meta", default="/app/work/build_meta.json")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("validate", help="self-check IR + SVG")
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/work/figure.svg")
    add_common(p, "/app/work/checks.json")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("bundle", help="copy artifacts and merge into result.json")
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--meta", default="/app/work/build_meta.json")
    p.add_argument("--checks", default="/app/work/checks.json")
    add_common(p, "/app/output/result.json")
    p.add_argument("--exports", nargs="*", default=[], help="PNG/PDF files to copy into output")
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser("finalize", help="set final status and artifact inventory")
    add_common(p, "/app/output/result.json")
    p.add_argument("--status", default="partial",
                   choices=["in_progress", "complete", "partial", "failed"])
    p.set_defaults(func=cmd_finalize)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
