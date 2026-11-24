#!/usr/bin/env python3
"""Evidence ledger for grant-writing Skill.

Records claims with a four-way evidence status and a done/proposed tense split,
then verifies traceability and exports an evidence map.

Subcommands:
  add      --element E --claim "..." --status verified|paraphrased|inferred|
                not_reported|conflict --tense done|proposed|unknown
                [--locator "file:page"] [--quote "..."] [--note "..."]
  list     show all claims
  verify   check every verified/paraphrased claim has locator+quote; print report
  export   --out evidence_map.json

Ledger stored at /app/work/ledger.json (fallback work/ledger.json).
Pure standard library.
"""
import argparse
import json
import os
import sys
import time

VALID_STATUS = {"verified", "paraphrased", "inferred", "not_reported", "conflict"}
VALID_TENSE = {"done", "proposed", "unknown"}
SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容", "技术路线",
    "创新点", "研究基础与可行性",
]


def ledger_path(work_dir):
    return os.path.join(work_dir, "ledger.json")


def load_ledger(work_dir):
    p = ledger_path(work_dir)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data.get("claims", [])
    return []


def save_ledger(work_dir, claims):
    os.makedirs(work_dir, exist_ok=True)
    with open(ledger_path(work_dir), "w", encoding="utf-8") as f:
        json.dump({"claims": claims, "updated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f, ensure_ascii=False, indent=2)


def work_dir_or_default(work_dir):
    return work_dir or ("/app/work" if os.path.isdir("/app") else "work")


def cmd_add(a):
    status = a.status
    tense = a.tense
    if status not in VALID_STATUS:
        print("invalid status: %s (choose from %s)" % (status, sorted(VALID_STATUS)))
        return 2
    if tense not in VALID_TENSE:
        print("invalid tense: %s (choose from %s)" % (tense, sorted(VALID_TENSE)))
        return 2
    if a.element not in SEVEN_ELEMENTS:
        print("warning: element %r not among seven elements" % a.element)
    claims = load_ledger(a.work)
    claim = {
        "id": "c%03d" % (len(claims) + 1),
        "element": a.element,
        "claim": a.claim,
        "ev_status": status,
        "tense": tense,
        "locator": a.locator or "",
        "quote": a.quote or "",
        "note": a.note or "",
    }
    # Enforce hard rule: not_reported claims must not carry a quote.
    if status == "not_reported" and claim["quote"]:
        print("error: not_reported claim must have empty quote")
        return 2
    if status in ("verified", "paraphrased") and not (claim["locator"] and claim["quote"]):
        print("error: verified/paraphrased claims need locator AND quote")
        return 2
    claims.append(claim)
    save_ledger(a.work, claims)
    print("added %s %s -> %s" % (claim["id"], status, a.claim[:40]))
    return 0


def cmd_list(a):
    claims = load_ledger(a.work)
    if not claims:
        print("(empty ledger)")
        return 0
    for c in claims:
        print("%s [%s|%s] %s :: %s | quote=%s" % (
            c["id"], c["ev_status"], c["tense"], c["element"],
            c["claim"][:60], (c["quote"] or "<none>")[:40]))
    return 0


def cmd_verify(a):
    claims = load_ledger(a.work)
    issues = []
    for c in claims:
        st = c["ev_status"]
        if st in ("verified", "paraphrased"):
            if not (c.get("locator") and c.get("quote")):
                issues.append("%s missing locator/quote" % c["id"])
        if st == "not_reported" and c.get("quote"):
            issues.append("%s not_reported but has quote" % c["id"])
        if st == "conflict" and not c.get("note"):
            issues.append("%s conflict without note" % c["id"])
    report = {"total": len(claims), "issues": issues, "ok": not issues}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_export(a):
    claims = load_ledger(a.work)
    out = a.out or os.path.join(a.work, "evidence_map.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"claims": claims,
                   "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  f, ensure_ascii=False, indent=2)
    print("exported %d claims -> %s" % (len(claims), out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="add one claim")
    p_add.add_argument("--work", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_add.add_argument("--element", required=True)
    p_add.add_argument("--claim", required=True)
    p_add.add_argument("--status", required=True)
    p_add.add_argument("--tense", default="unknown")
    p_add.add_argument("--locator", default="")
    p_add.add_argument("--quote", default="")
    p_add.add_argument("--note", default="")

    p_list = sub.add_parser("list", help="list claims")
    p_list.add_argument("--work", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p_verify = sub.add_parser("verify", help="verify traceability")
    p_verify.add_argument("--work", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p_exp = sub.add_parser("export", help="export evidence map")
    p_exp.add_argument("--work", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_exp.add_argument("--out", default=None)

    a = ap.parse_args(argv)
    a.work = work_dir_or_default(a.work)
    if a.cmd == "add":
        return cmd_add(a)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "export":
        return cmd_export(a)
    return 1


if __name__ == "__main__":
    sys.exit(main())
