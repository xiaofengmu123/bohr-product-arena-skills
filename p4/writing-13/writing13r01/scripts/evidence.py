#!/usr/bin/env python3
"""Evidence ledger for grant-writing skill.

Maintains the fine-grained ``fact_source_map`` inside result.json. Every claim
is recorded at conclusion level with a ``locator`` (file + page/paragraph/table)
and a verbatim ``quote``, plus a three-level trace from artifact paragraph to
seven-element to claim id. Standard library only.

Commands:
  add       --claim ... --kind ... --locator ... --quote ... --ref ... [--status]
  list      [--status mapped]
  verify    exit 0 iff every mapped claim has non-empty locator AND quote
  export    write <output>/evidence_map.json and print its path
  trace     --artifact ... --element ... --claims C1,C2
  snapshot  --input-dir ...  (SHA256 manifest, same as main.py)
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_RESULT = "/app/output/result.json"

CLAIM_KINDS = {"fact", "number", "term", "citation", "figure", "policy",
               "inference", "quote"}
CLAIM_STATUS = {"mapped", "unmapped", "conflict", "pending_human", "not_reported"}


def resolve_result(path):
    if path and os.path.isfile(path):
        return path
    for cand in (DEFAULT_RESULT, os.path.join(ROOT, "output", "result.json")):
        if os.path.isfile(cand):
            return cand
    return DEFAULT_RESULT


def load_result(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            obj = json.load(fh)
        if isinstance(obj, dict):
            return obj
    except (OSError, ValueError):
        pass
    return None


def save_result(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def fsm(obj):
    fs = obj.setdefault("fact_source_map", {})
    if not isinstance(fs, dict):
        fs = {"claims": [], "trace": {}, "conflicts": [], "unmapped": []}
        obj["fact_source_map"] = fs
    fs.setdefault("claims", [])
    fs.setdefault("trace", {})
    fs.setdefault("conflicts", [])
    fs.setdefault("unmapped", [])
    fs.setdefault("granularity", "claim-level locator + verbatim quote")
    return fs


def cmd_add(args):
    path = resolve_result(args.result)
    obj = load_result(path)
    if obj is None:
        obj = {"case_id": args.case, "status": "in_progress",
               "fact_source_map": {}, "change_log": [], "sources": [],
               "uncertainty": [], "pending_human": [], "version_boundary": {},
               "artifact": {}}
    fs = fsm(obj)
    claim = {
        "claim_id": args.claim_id or ("C%02d" % (len(fs["claims"]) + 1)),
        "claim_text": args.claim,
        "kind": args.kind if args.kind in CLAIM_KINDS else "fact",
        "status": args.status if args.status in CLAIM_STATUS else "mapped",
        "locator": args.locator or "",
        "quote": args.quote or "",
        "source_ref": args.ref or "",
        "note": args.note or "",
    }
    if claim["status"] in ("unmapped", "pending_human", "not_reported") and claim["quote"]:
        claim["status"] = "mapped" if claim["locator"] else claim["status"]
    fs["claims"].append(claim)
    if claim["status"] == "unmapped":
        fs["unmapped"].append(claim["claim_id"])
    if claim["status"] == "conflict":
        fs["conflicts"].append(claim["claim_id"])
    obj.setdefault("change_log", []).append({
        "action": "evidence_add", "claim_id": claim["claim_id"],
        "reason": args.reason or "", "impact": "fact_source_map extended",
    })
    save_result(path, obj)
    print(json.dumps({"ok": True, "claim_id": claim["claim_id"],
                      "total_claims": len(fs["claims"])}, ensure_ascii=False))
    return 0


def cmd_list(args):
    path = resolve_result(args.result)
    obj = load_result(path)
    if obj is None:
        print("no result.json", file=sys.stderr)
        return 1
    fs = fsm(obj)
    for c in fs["claims"]:
        if args.status and c.get("status") != args.status:
            continue
        print(json.dumps(c, ensure_ascii=False))
    return 0


def cmd_verify(args):
    path = resolve_result(args.result)
    obj = load_result(path)
    if obj is None:
        print("FAIL: no result.json at %s" % path)
        return 1
    fs = fsm(obj)
    bad = []
    for c in fs["claims"]:
        if c.get("status") == "mapped":
            if not (c.get("locator") or "").strip():
                bad.append(c.get("claim_id") + ":missing_locator")
            if not (c.get("quote") or "").strip():
                bad.append(c.get("claim_id") + ":missing_quote")
        elif c.get("status") in ("unmapped", "conflict", "pending_human"):
            if not (c.get("note") or "").strip():
                bad.append(c.get("claim_id") + ":missing_note")
    report = {"total_claims": len(fs["claims"]),
              "mapped": sum(1 for c in fs["claims"] if c.get("status") == "mapped"),
              "unmapped": len(fs["unmapped"]),
              "conflicts": len(fs["conflicts"]),
              "issues": bad}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if bad else 0


def cmd_export(args):
    path = resolve_result(args.result)
    obj = load_result(path)
    if obj is None:
        print("no result.json", file=sys.stderr)
        return 1
    fs = fsm(obj)
    out_dir = args.output
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "evidence_map.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fs, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(out_path)
    return 0


def cmd_trace(args):
    path = resolve_result(args.result)
    obj = load_result(path)
    if obj is None:
        print("no result.json", file=sys.stderr)
        return 1
    fs = fsm(obj)
    tr = fs["trace"]
    if args.element:
        tr.setdefault("element_to_claims", {})[args.element] = \
            [x.strip() for x in args.claims.split(",") if x.strip()]
    if args.artifact:
        tr.setdefault("artifact_to_element", {})[args.artifact] = args.element or ""
    save_result(path, obj)
    print(json.dumps(tr, ensure_ascii=False, indent=2))
    return 0


def cmd_snapshot(args):
    files = []
    for root, dirs, names in os.walk(args.input_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            h = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            files.append({
                "path": os.path.relpath(full, args.input_dir).replace("\\", "/"),
                "size": os.path.getsize(full),
                "sha256": h.hexdigest(),
            })
    print(json.dumps(files, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evidence ledger for grant-writing skill")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--result", default=None)
    p.add_argument("--case", default=None)
    p.add_argument("--claim", required=True)
    p.add_argument("--claim-id", default=None)
    p.add_argument("--kind", default="fact")
    p.add_argument("--locator", default="")
    p.add_argument("--quote", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--status", default="mapped")
    p.add_argument("--note", default="")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("list")
    p.add_argument("--result", default=None)
    p.add_argument("--status", default=None)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("verify")
    p.add_argument("--result", default=None)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("export")
    p.add_argument("--result", default=None)
    p.add_argument("--output", default="/app/output")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("trace")
    p.add_argument("--result", default=None)
    p.add_argument("--artifact", default=None)
    p.add_argument("--element", default=None)
    p.add_argument("--claims", default="")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("snapshot")
    p.add_argument("--input-dir", required=True)
    p.set_defaults(fn=cmd_snapshot)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
