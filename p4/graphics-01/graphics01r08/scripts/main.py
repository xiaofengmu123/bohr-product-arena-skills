#!/usr/bin/env python3
"""sci-figure-workflow entry point (Python standard library only).

Subcommands:
  scaffold  write an honest partial/insufficient_evidence result skeleton early
  discover  list input files without assuming fixed names
  record    append a change_log entry and optionally update status
  validate  check result.json + referenced artifacts against the contract
  selftest  run scripts/selftest.py (package self-check)

Defaults target the runtime locations (/input, /app/output, /app/work) and
are overridable for local testing. The script resolves its own directory so
it works regardless of the current working directory.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

REQUIRED_KEYS = ["sources", "uncertainty", "pending_human", "errors", "degradations", "change_log"]
PROVENANCE_KEYS = ["element", "source_ref", "locator", "quote", "mapping"]


def add_io(parser):
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)


def cmd_discover(args):
    files, skipped = C.discover_input(args.input)
    print(json.dumps({"input": args.input, "files": files, "skipped": skipped},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")

    case_path = os.path.join(args.input, "case.json")
    case_obj, case_err = C.read_json(case_path)
    case_id = "unknown"
    contract = {}
    status = "partial"
    uncertainty = []

    if case_obj is None:
        status = "insufficient_evidence"
        uncertainty.append({
            "item": "case.json",
            "reason": case_err or "missing",
            "impact": "cannot determine case_id or output_contract",
            "mitigation": "verify /input/case.json exists and is readable; report blocker",
        })
    else:
        case_id = str(case_obj.get("case_id") or case_obj.get("id") or "unknown")
        raw = case_obj.get("output_contract")
        if isinstance(raw, dict):
            contract = dict(raw)
        elif raw is not None:
            uncertainty.append({
                "item": "output_contract",
                "reason": "present but not an object: %s" % type(raw).__name__,
                "impact": "contract keys not backfilled",
                "mitigation": "use default figure-editor contract",
            })

    # Default figure-editor keys; preserve any unknown output_contract keys verbatim.
    default_contract = {"editable_source_files": [], "export_files": [], "provenance": []}

    result = {}
    for key, value in contract.items():
        if value is None:
            value = [] if isinstance(default_contract.get(key), list) else None
        result[key] = value
    for key, value in default_contract.items():
        result.setdefault(key, value)

    result["case_id"] = case_id
    result["status"] = status
    for key in REQUIRED_KEYS:
        result.setdefault(key, [])
    if uncertainty:
        existing = result.get("uncertainty")
        if not isinstance(existing, list):
            existing = []
        result["uncertainty"] = uncertainty + existing

    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    result["change_log"].append({
        "time": C.utcnow(),
        "action": "scaffold",
        "detail": "Honest partial skeleton written within first 3 minutes; refine incrementally (budget 900s / 64 requests).",
    })
    if not isinstance(result.get("runtime"), dict):
        result["runtime"] = {}
    result["runtime"].setdefault("started_at", C.utcnow())
    result["runtime"].setdefault("finished_at", None)
    result["runtime"].setdefault("seconds", None)
    result["runtime"].setdefault("model_requests_used", 0)
    result["runtime"].setdefault("note", "platform budget: 900s, 64 model requests, 16384 tokens/response")

    C.write_json(result_path, result)
    print(json.dumps({
        "ok": True,
        "path": result_path,
        "case_id": case_id,
        "status": status,
        "output_contract_keys": sorted(contract.keys()),
    }, ensure_ascii=False))
    return 0


def cmd_record(args):
    result_path = args.result or os.path.join(args.output, "result.json")
    obj, err = C.read_json(result_path)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 2
    if args.status and args.status not in C.allowed_statuses():
        print(json.dumps({"ok": False, "error": "invalid status %r" % args.status}, ensure_ascii=False))
        return 2
    if not isinstance(obj.get("change_log"), list):
        obj["change_log"] = []
    obj["change_log"].append({"time": C.utcnow(), "action": args.action, "detail": args.detail})
    if args.status:
        obj["status"] = args.status
    if isinstance(obj.get("runtime"), dict):
        obj["runtime"]["finished_at"] = C.utcnow()
    C.write_json(result_path, obj)
    print(json.dumps({"ok": True, "path": result_path, "status": obj.get("status")}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    result_path = args.result or os.path.join(args.output, "result.json")
    report = {"ok": True, "path": result_path, "checks": [], "errors": [], "warnings": []}
    obj, err = C.read_json(result_path)
    if err:
        report["ok"] = False
        report["errors"].append("result.json: %s" % err)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    def fail(msg):
        report["errors"].append(msg)
        report["ok"] = False

    def check(cond, ok_msg, err_msg, warn=False):
        if cond:
            report["checks"].append(ok_msg)
        elif warn:
            report["warnings"].append(err_msg)
        else:
            fail(err_msg)

    check(bool(obj.get("case_id")), "case_id=%r" % obj.get("case_id"), "case_id missing/empty")
    check(obj.get("status") in C.allowed_statuses(),
          "status=%r valid" % obj.get("status"),
          "status missing or not in %s" % sorted(C.allowed_statuses()))

    for key in REQUIRED_KEYS:
        check(isinstance(obj.get(key), list), "%s is a list" % key, "%s missing/not a list" % key)

    prov = obj.get("provenance")
    check(isinstance(prov, list), "provenance is a list", "provenance missing/not a list")
    if isinstance(prov, list):
        for i, item in enumerate(prov):
            if not isinstance(item, dict):
                fail("provenance[%d] not an object" % i)
                continue
            missing = [k for k in PROVENANCE_KEYS if not item.get(k)]
            check(not missing, "provenance[%d] complete" % i,
                  "provenance[%d] missing %s" % (i, missing))

    def check_files(entries, label):
        check(isinstance(entries, list), "%s is a list" % label, "%s missing/not a list" % label)
        if not isinstance(entries, list):
            return
        for i, f in enumerate(entries):
            if not isinstance(f, dict) or not f.get("path"):
                fail("%s[%d] missing path" % (label, i))
                continue
            full = os.path.join(args.output, f["path"])
            check(os.path.isfile(full), "%s[%d] exists: %s" % (label, i, f["path"]),
                  "%s[%d] file missing: %s" % (label, i, f["path"]))
            if os.path.isfile(full):
                fmt = (f.get("format") or f.get("kind") or "").lower()
                if fmt == "svg" or full.lower().endswith(".svg"):
                    ok, detail, feats = C.svg_check(full)
                    check(ok, "%s[%d] svg parses" % (label, i), "%s[%d] %s" % (label, i, detail))
                    if ok and feats:
                        if feats["text_elements"] == 0:
                            report["warnings"].append("%s[%d] no <text> elements (editable_text claim)" % (label, i))
                        if feats["group_elements"] == 0:
                            report["warnings"].append("%s[%d] no <g> layers (layers claim)" % (label, i))
                elif fmt == "pdf" or full.lower().endswith(".pdf"):
                    ok, detail = C.pdf_check(full)
                    check(ok, "%s[%d] pdf header ok" % (label, i), "%s[%d] %s" % (label, i, detail))
                elif fmt == "png" or full.lower().endswith(".png"):
                    ok, detail, dims = C.png_check(full)
                    check(ok, "%s[%d] png header ok" % (label, i), "%s[%d] %s" % (label, i, detail))
                    if ok:
                        width, height = dims
                        for key, actual in (("width_px", width), ("height_px", height)):
                            declared = f.get(key)
                            if declared is None or actual is None:
                                continue
                            try:
                                declared_int = int(declared)
                            except (TypeError, ValueError):
                                continue
                            if declared_int != actual:
                                report["warnings"].append(
                                    "%s[%d] %s=%s != actual %s" % (label, i, key, declared, actual))

    check_files(obj.get("editable_source_files"), "editable_source_files")
    check_files(obj.get("export_files"), "export_files")

    if not obj.get("sources"):
        report["warnings"].append("sources is empty: add an explanation in change_log/uncertainty")
    if obj.get("status") != "complete" and not obj.get("uncertainty"):
        report["warnings"].append("status=%s but uncertainty is empty: record limits" % obj.get("status"))

    for s in (obj.get("sources") or []):
        if isinstance(s, dict) and s.get("path"):
            p = s["path"]
            full = p if os.path.isabs(p) else os.path.join(args.input, p)
            if not os.path.isfile(full):
                report["warnings"].append("source path not found: %s" % p)

    report["ok"] = report["ok"] and not report["errors"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_selftest(args):
    selftest_py = os.path.join(HERE, "selftest.py")
    if not os.path.isfile(selftest_py):
        print(json.dumps({"ok": False, "error": "selftest.py missing"}, ensure_ascii=False))
        return 2
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.call([sys.executable, selftest_py], env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="sci-figure-workflow helper")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("discover", help="list input files")
    add_io(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("scaffold", help="write honest partial result skeleton")
    add_io(p)
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("record", help="append change_log entry / update status")
    add_io(p)
    p.add_argument("--result", default=None, help="result.json path override")
    p.add_argument("--action", required=True, help="short action label")
    p.add_argument("--detail", default="", help="human-readable detail")
    p.add_argument("--status", default=None, help="optional status to set")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("validate", help="check result.json + artifacts")
    add_io(p)
    p.add_argument("--result", default=None, help="result.json path override")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("selftest", help="run package self-check")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
