#!/usr/bin/env python3
"""Entry point for the academic-format-adaptation Skill.

Subcommands (all standard library, all deterministic except ``runtime``):
  scaffold  write/merge a partial result.json skeleton before analysis
  verify    check contract completeness and quote locatability
  seal      finalize deterministically (sort, digest, change_log)
  digest    print the deterministic content digest of result.json

Exit codes:
  0  success (verify: all checks pass)
  1  verify warnings (recoverable; quote not located, degraded input)
  2  hard error (invalid JSON, missing case_id, missing contract fields)
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C


def cmd_scaffold(a):
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    manifest = C.discover_input(a.input)
    case_path = a.case or manifest["case_path"]
    case_obj, case_errors = C.load_case(case_path)
    skeleton = C.build_skeleton(case_obj, C.now_iso())
    for e in case_errors:
        skeleton["errors"].append(e)
    for e in manifest["errors"]:
        skeleton.setdefault("degradations", []).append("input scan: %s" % (e,))
    if not manifest["materials"]:
        skeleton.setdefault("degradations", []).append("no readable material files discovered")

    out_path = os.path.join(a.out, "result.json")
    existing, _err = C.read_json_safe(out_path)
    merged = C.merge_incremental(existing, skeleton)
    werr = C.write_json(out_path, merged)
    if werr:
        print(json.dumps({"ok": False, "error": werr}, ensure_ascii=False))
        return 2
    summary = {
        "ok": True,
        "case_id": merged.get("case_id"),
        "status": merged.get("status"),
        "contract_keys": [k for k in merged if k not in C.RESERVED_KEYS],
        "materials": len(manifest["materials"]),
        "errors": merged.get("errors", []),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_verify(a):
    result, err = C.read_json_safe(a.result)
    hard, warn = [], []
    if result is None:
        hard.append("result.json unreadable or invalid: %s" % (err,))
        report = {"passed": False, "hard_errors": hard, "warnings": warn}
        C.write_json(a.report, report)
        return 2
    if not isinstance(result, dict):
        hard.append("result.json root is not an object")

    case_obj, _ce = C.load_case(a.case)
    expected_id = C.case_id_of(case_obj)
    actual_id = result.get("case_id")
    if not actual_id:
        hard.append("result.json missing case_id")
    elif expected_id and actual_id != expected_id:
        hard.append("case_id mismatch: result=%r case=%r" % (actual_id, expected_id))

    if result.get("status") not in C.ALLOWED_STATUS:
        hard.append("invalid status %r" % (result.get("status"),))

    contract = C.output_contract_of(case_obj)
    for key in contract:
        if key not in result:
            hard.append("missing output_contract field: %s" % (key,))
        elif result[key] is None and key not in C.FALLBACK_DEFAULTS:
            warn.append("contract field has null value: %s" % (key,))

    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if key not in result:
            hard.append("missing reserved field: %s" % (key,))

    # fact_source_map completeness + quote locatability
    fsmap = result.get("fact_source_map", [])
    if isinstance(fsmap, list):
        texts = list(C.material_texts([a.material_root] if a.material_root else []))
        texts += list(C.material_texts([a.work]))
        if a.extra_roots:
            texts += list(C.material_texts(a.extra_roots))
        for item in fsmap:
            if not isinstance(item, dict):
                warn.append("fact_source_map item is not an object")
                continue
            cid = item.get("claim_id")
            for field in ("claim_id", "claim_text", "quote", "locator", "source_ref"):
                if not item.get(field):
                    warn.append("claim %r missing field %s" % (cid, field))
            quote = item.get("quote")
            if quote and not C.find_quote(quote, texts):
                warn.append("claim %r quote not located in materials: %s" % (cid, quote[:60]))
    elif fsmap is not None and not isinstance(fsmap, list):
        warn.append("fact_source_map is not a list")

    report = {
        "passed": not hard and not warn,
        "hard_errors": hard,
        "warnings": warn,
        "status": result.get("status"),
        "case_id": actual_id,
        "content_digest": result.get("content_digest"),
    }
    C.write_json(a.report, report)
    print(json.dumps({"passed": report["passed"], "hard_errors": len(hard),
                      "warnings": len(warn)}, ensure_ascii=False))
    if hard:
        return 2
    if warn:
        return 1
    return 0


def cmd_seal(a):
    result, err = C.read_json_safe(a.result)
    if result is None or not isinstance(result, dict):
        print(json.dumps({"ok": False, "error": err or "invalid result"}, ensure_ascii=False))
        return 2
    if result.get("status") == "partial":
        # honest: leave partial unless the agent explicitly promoted it
        pass
    C.append_change(result, 50, "seal", "deterministic finalization: sort, dedupe, digest")
    C.finalize_result(result, C.now_iso())
    werr = C.write_json(a.result, result)
    if werr:
        print(json.dumps({"ok": False, "error": werr}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "content_digest": result.get("content_digest")}, ensure_ascii=False))
    return 0


def cmd_digest(a):
    result, err = C.read_json_safe(a.result)
    if result is None or not isinstance(result, dict):
        print(json.dumps({"ok": False, "error": err or "invalid result"}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "content_digest": C.content_digest(result)}, ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Deterministic scaffold/verify/seal for academic format adaptation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="write partial result skeleton early")
    p.add_argument("--case", default=None)
    p.add_argument("--input", default="/input")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("verify", help="verify contract + quote locatability")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--material-root", default="/input")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--extra-roots", nargs="*", default=None)
    p.add_argument("--report", default="/app/output/verification_report.json")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("seal", help="deterministically finalize result.json")
    p.add_argument("--result", default="/app/output/result.json")
    p.set_defaults(func=cmd_seal)

    p = sub.add_parser("digest", help="print deterministic content digest")
    p.add_argument("--result", default="/app/output/result.json")
    p.set_defaults(func=cmd_digest)

    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
