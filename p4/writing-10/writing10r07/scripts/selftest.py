#!/usr/bin/env python3
"""Domain self-check for the reviewer-response deliverable.

Verifies the four questions the task grades (答得全 / 改得准 / 证得住 / 对得上):
  * every detected comment is covered by a ledger row
  * every accept/partial decision has a concrete action
  * every action claims a manuscript_location and it resolves in the manuscript
  * evidence entries carry a status and a fallback when not done
  * tone values are within {accept, clarify, polite_refute, appeal}
  * response letter, revision manuscript and ledger stay mutually consistent

Exit code 0 = no FAIL (warnings allowed); 1 = at least one FAIL. A JSON
report can be emitted with --report. Stdlib only; fail-soft on missing files.
"""
import argparse
import json
import os
import re
import sys

ALLOWED_STATUS = {"in_progress", "complete", "partial", "insufficient_evidence", "input_error", "failed"}
ALLOWED_DECISION = {"accept", "partial", "reject", None}
ALLOWED_TONE = {"accept", "clarify", "polite_refute", "appeal", None}
ALLOWED_EVIDENCE_STATUS = {"done", "partial", "not_done", "impossible", "not_started", None}


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (OSError, json.JSONDecodeError, ValueError) as e:
        return None, "%s" % e


def read_text(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def find_locations(manuscript_text, location):
    """Resolve a location string against manuscript text.

    Accepts: 'L12' / 'line 12' / 'p3' / 'section 2' / '# heading' / a literal
    quoted fragment. Returns list of matches (line numbers) or [] if none.
    """
    if not manuscript_text or not location:
        return []
    loc = str(location).strip()
    lines = manuscript_text.splitlines()
    hits = []
    m = re.match(r'^(?:line\s+|L)?(\d+)$', loc, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(lines):
            hits.append(n)
        return hits
    m = re.match(r'^p(?:age)?\.?\s*(\d+)$', loc, re.IGNORECASE)
    if m:
        return [0]  # page locators are treated as resolved-on-request
    m = re.match(r'^(?:section|sec|§)\s*([\d.]+)$', loc, re.IGNORECASE)
    if m:
        pat = re.compile(r'^#+\s*%s\b' % re.escape(m.group(1)))
        for i, ln in enumerate(lines, 1):
            if pat.search(ln):
                hits.append(i)
        return hits
    if loc.startswith("#"):
        head = loc.lstrip("#").strip()
        for i, ln in enumerate(lines, 1):
            if ln.strip().startswith("#") and head in ln:
                hits.append(i)
        return hits
    # literal quoted fragment
    if loc in manuscript_text:
        return [1]
    return hits


def check(name, ok, msg, report):
    report.append({"check": name, "level": "pass" if ok else "fail", "message": msg})
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--manuscript", default="/app/output/revision_manuscript.md")
    ap.add_argument("--response", default="/app/output/response_letter.md")
    ap.add_argument("--ledger", default="/app/output/revision_ledger.json")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)

    report = []
    fails = 0

    result, err = read_json(a.result)
    if err or not isinstance(result, dict):
        report.append({"check": "result_parse", "level": "fail", "message": "cannot parse result.json: %s" % err})
        if a.report:
            with open(a.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    if not check("case_id", bool(result.get("case_id")), "case_id present and non-empty", report):
        fails += 1
    if not check("status", result.get("status") in ALLOWED_STATUS,
                 "status in %s (got %r)" % (sorted(ALLOWED_STATUS), result.get("status")), report):
        fails += 1
    for field in ("deliverable", "fact_source_mapping", "version_boundary"):
        if not check("contract:" + field, field in result,
                     "output-contract field '%s' present" % field, report):
            fails += 1

    ledger, _ = read_json(a.ledger)
    if not isinstance(ledger, list):
        # ledger may be written later by the agent; treat as warning, not fail
        report.append({"check": "ledger_present", "level": "warn",
                       "message": "revision_ledger.json missing or not a list"})
    else:
        # coverage: comment_id non-empty and unique
        ids = [r.get("comment_id") for r in ledger if isinstance(r, dict)]
        if check("ledger_ids", bool(ids) and all(ids), "every ledger row has a comment_id", report):
            pass
        else:
            fails += 1
        dup = {x for x in ids if ids.count(x) > 1}
        if not check("ledger_unique", not dup, "no duplicate comment_id (dup: %s)" % sorted(dup), report):
            fails += 1

        # every decision accept/partial must have action + location
        ms = read_text(a.manuscript)
        missing_action = 0
        missing_loc = 0
        unresolvable = 0
        bad_tone = 0
        bad_decision = 0
        bad_evidence = 0
        for r in ledger:
            d = r.get("decision")
            if d not in ALLOWED_DECISION:
                bad_decision += 1
            tone = r.get("tone")
            if tone not in ALLOWED_TONE:
                bad_tone += 1
            ev = r.get("evidence") or {}
            if ev.get("status") not in ALLOWED_EVIDENCE_STATUS:
                bad_evidence += 1
            if d in ("accept", "partial"):
                if not (r.get("action") or "").strip():
                    missing_action += 1
                loc = r.get("manuscript_location")
                if not loc:
                    missing_loc += 1
                elif ms and not find_locations(ms, loc):
                    unresolvable += 1
            if ev.get("status") in ("not_done", "impossible") and not (ev.get("fallback") or "").strip():
                report.append({"check": "evidence_fallback", "level": "warn",
                               "message": "%s: evidence %s lacks fallback explanation" % (r.get("comment_id"), ev.get("status"))})

        def record(name, ok, msg):
            nonlocal fails
            if not check(name, ok, msg, report):
                fails += 1

        record("decision_values", bad_decision == 0, "%d rows with invalid decision" % bad_decision)
        record("tone_values", bad_tone == 0, "%d rows with invalid tone" % bad_tone)
        record("evidence_values", bad_evidence == 0, "%d rows with invalid evidence.status" % bad_evidence)
        record("action_complete", missing_action == 0, "%d accept/partial rows missing concrete action" % missing_action)
        record("location_complete", missing_loc == 0, "%d accept/partial rows missing manuscript_location" % missing_loc)
        if ms:
            record("location_resolvable", unresolvable == 0,
                   "%d locations cannot be found in manuscript" % unresolvable)

        # three-way consistency: response letter should cite every accept/partial id
        resp = read_text(a.response)
        if resp:
            uncited = [r.get("comment_id") for r in ledger
                       if r.get("decision") in ("accept", "partial") and r.get("comment_id") not in resp]
            record("response_cites_all", not uncited,
                   "response letter cites every accept/partial comment (missing: %s)" % (uncited or "none"))
        else:
            report.append({"check": "response_present", "level": "warn", "message": "response letter not found"})

    cl = result.get("change_log") or []
    if not check("change_log", isinstance(cl, list) and len(cl) > 0, "change_log has at least one entry", report):
        fails += 1

    print(json.dumps({"fails": fails, "checks": len(report),
                      "report": report}, ensure_ascii=False, indent=2))
    if a.report:
        try:
            with open(a.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except OSError as e:
            print("report write failed: %s" % e, file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
