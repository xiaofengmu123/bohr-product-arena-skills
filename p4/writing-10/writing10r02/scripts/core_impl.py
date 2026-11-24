#!/usr/bin/env python3
"""Runtime self-check for review-response deliverables. Standard library only.

Verifies the mechanical properties of a completed revision, without writing any
content itself:

  1. result.json schema is valid;
  2. coverage: every comment id in the ledger appears in the response letter;
  3. decisions: each item carries accept|partial|reject|clarify|n/a;
  4. locators: each accepted/partial item with an anchor_quote actually occurs
     in the revised manuscript (whitespace-normalized exact substring);
  5. evidence: method/data acceptances carry an evidence_status or anchor;
  6. fact_source_mapping entries each carry a claim plus a source_ref or quote;
  7. version consistency: response-declared version matches manuscript header.

Writes /app/work/check_report.json and appends a change_log entry. Exits 0 when
no blocking failure, 1 otherwise.

Usage:
  python scripts/check_revision.py --result /app/output/result.json \
      --work /app/work
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scaffold  # noqa: E402  (shared schema + read_text + ts)

DEFAULTS = {"result": "/app/output/result.json", "work": "/app/work"}
DECISIONS = {"accept", "partial", "reject", "clarify", "n/a"}
EVIDENCE_TYPES = {"data", "method"}
VERSION_RE = re.compile(r"(?:version|版本)\s*[:：]?\s*([A-Za-z0-9_.\-]+)", re.I)


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        try:
            return json.load(fh)
        except ValueError:
            return None


def version_of(text):
    m = VERSION_RE.search(text or "")
    return m.group(1) if m else None


def run(result_path, work_dir):
    report = {"ok": True, "checks": [], "blocking": []}

    def check(name, ok, detail, blocking=True):
        report["checks"].append({"check": name, "ok": ok, "detail": detail})
        if not ok and blocking:
            report["ok"] = False
            report["blocking"].append(name)

    result = load_json(result_path)
    if result is None:
        check("result_json", False, "cannot load result.json")
        return report

    ok, errs = scaffold.validate_schema(result)
    check("schema", ok, "; ".join(errs))

    ledger = load_json(os.path.join(work_dir, "comment_ledger.json")) or {}
    comments = ledger.get("comments") or []
    resp_path = (result.get("deliverable") or {}).get("path")
    ms_path = (result.get("deliverable") or {}).get("revised_manuscript")
    resp = scaffold.read_text(resp_path) if resp_path else None
    ms = scaffold.read_text(ms_path) if ms_path else None

    if not comments:
        check("ledger", False, "comment ledger empty; coverage not verifiable")
    else:
        if resp is None:
            check("response_file", False, "response letter not found at %s" % resp_path)
        if ms is None:
            check("manuscript_file", False, "revised manuscript not found at %s" % ms_path)

        missing = [c.get("id") for c in comments
                   if c.get("id") and c.get("id") not in (resp or "")]
        check("coverage", not missing,
              "%d/%d comment ids present in response letter" %
              (len(comments) - len(missing), len(comments)))

        undecided = [c.get("id") for c in comments
                     if (c.get("decision") or "").lower() not in DECISIONS]
        check("decisions", not undecided,
              "undecided items: %s" %
              (", ".join(undecided[:8]) if undecided else "none"))

        nms = norm(ms)
        unlocated = []
        for c in comments:
            dec = (c.get("decision") or "").lower()
            q = norm(c.get("anchor_quote") or "")
            if dec in ("accept", "partial") and q and q not in nms:
                unlocated.append(c.get("id"))
        check("locators", not unlocated,
              "anchors absent from manuscript: %s" %
              (", ".join(unlocated[:8]) if unlocated else "none"))

        no_evid = [c.get("id") for c in comments
                   if (c.get("type") or "").lower() in EVIDENCE_TYPES
                   and (c.get("decision") or "").lower() in ("accept", "partial")
                   and not (c.get("evidence_status") or "")
                   and not (c.get("anchor_quote") or "")]
        check("evidence", not no_evid,
              "method/data acceptances lacking evidence note: %s" %
              (", ".join(no_evid[:8]) if no_evid else "none"))

    fsm = result.get("fact_source_mapping") or []
    bad_fsm = [i for i, e in enumerate(fsm)
               if not (e.get("claim") and (e.get("source_ref") or e.get("quote")))]
    check("fact_mapping", not bad_fsm,
          "%d/%d mapping entries carry claim + source/quote" %
          (len(fsm) - len(bad_fsm), len(fsm)), blocking=bool(fsm))

    resp_v = version_of(resp)
    ms_v = version_of(ms)
    if resp_v and ms_v:
        check("version_consistency", resp_v == ms_v,
              "response %r vs manuscript %r" % (resp_v, ms_v))
    else:
        check("version_consistency", True,
              "no explicit version markers; skipped")

    report_path = os.path.join(work_dir, "check_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    result.setdefault("change_log", []).append({
        "ts": scaffold.ts(), "action": "check_revision",
        "detail": "%d checks, blocking=%s" %
                  (len(report["checks"]), ",".join(report["blocking"]) or "none"),
    })
    if report["blocking"] and result.get("status") == "complete":
        result["status"] = "partial"
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default=DEFAULTS["result"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    a = ap.parse_args()
    report = run(a.result, a.work)
    print(json.dumps({"ok": report["ok"], "checks": len(report["checks"]),
                      "blocking": report["blocking"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
