"""Structural self-check for frontier-trend-review result.json.

Reads /app/output/result.json (or --output), validates the contract, enumerations,
four deliverables, evidence-map linkage and quote locators. Writes a machine-readable
report to /app/work/selftest.json and prints a one-line summary. Exit code 0 unless
result.json itself cannot be parsed as JSON.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
SUPPORT_ENUM = {"reported", "not_reported", "inferred"}
EVIDENCE_STATUS_ENUM = {"reported", "not_reported", "source_conflict", "inferred"}
EVENT_ENUM = {"emergence", "merge", "split", "decline", "terminology_drift"}
WORK_KIND_ENUM = {"origin", "method", "milestone", "benchmark"}
GAP_ENUM = {"open_question", "method_failure", "unresolved_conflict", "coverage_absence"}
REQUIRED_KEYS = (
    "case_id", "status", "research_question", "protocol", "time_trend",
    "topic_evolution", "representative_works", "research_gaps", "evidence_map",
    "conflicts", "sources", "uncertainty", "pending_human", "change_log",
    "limitations", "errors", "degradations", "runtime",
)


def parse_args(argv):
    p = argparse.ArgumentParser(description="frontier-trend-review selftest")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p.parse_args(argv)


def collect(result, failures, warnings):
    # status enum
    if result.get("status") not in STATUS_ENUM:
        failures.append("status not in %s: %r" % (sorted(STATUS_ENUM), result.get("status")))
    # required keys
    for k in REQUIRED_KEYS:
        if k not in result:
            failures.append("missing key: %s" % k)
    # four deliverables non-empty
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    if not isinstance(tt.get("distribution"), list) or not tt.get("distribution"):
        warnings.append("time_trend.distribution empty")
    if not isinstance(te.get("stages"), list) or not te.get("stages"):
        warnings.append("topic_evolution.stages empty (machine baseline may leave empty)")
    if not isinstance(rw.get("stages"), list) or not rw.get("stages"):
        warnings.append("representative_works.stages empty")
    if not isinstance(rg.get("gaps"), list) or not rg.get("gaps"):
        warnings.append("research_gaps.gaps empty")
    # year note
    if not tt.get("unit_note"):
        warnings.append("time_trend.unit_note empty")
    proto = result.get("protocol") or {}
    if not proto.get("date_definition_note"):
        warnings.append("protocol.date_definition_note empty")
    # evidence_map
    ev = result.get("evidence_map")
    if not isinstance(ev, list):
        failures.append("evidence_map must be a list")
        ev = []
    eids = set()
    for e in ev:
        if not isinstance(e, dict) or not e.get("id"):
            failures.append("evidence_map entry missing id")
            continue
        eids.add(e["id"])
        if e.get("status") not in EVIDENCE_STATUS_ENUM:
            failures.append("%s status not in enum: %r" % (e.get("id"), e.get("status")))
        if not e.get("locator"):
            failures.append("%s missing locator" % e.get("id"))
        if not isinstance(e.get("quote"), str):
            failures.append("%s quote not a string" % e.get("id"))
    # claims enum + linkage
    claims = []
    for container, path in (
        (tt.get("claims"), "time_trend.claims"),
        (te.get("claims"), "topic_evolution.claims"),
    ):
        if not isinstance(container, list):
            failures.append("%s must be a list" % path)
            continue
        for i, c in enumerate(container):
            if not isinstance(c, dict):
                failures.append("%s[%d] not a dict" % (path, i))
                continue
            claims.append(c)
            if c.get("support") not in SUPPORT_ENUM:
                failures.append("%s[%d].support not in enum: %r" % (path, i, c.get("support")))
            for evid in c.get("evidence_ids") or []:
                if evid not in eids:
                    warnings.append("%s[%d] evidence_id %s not found in evidence_map" % (path, i, evid))
    # events enum
    for i, evt in enumerate(te.get("events") or []):
        if evt.get("type") not in EVENT_ENUM:
            failures.append("topic_evolution.events[%d].type not in enum: %r" % (i, evt.get("type")))
    # works kind enum
    for s_i, stage in enumerate(rw.get("stages") or []):
        for w_i, w in enumerate(stage.get("works") or []):
            if w.get("kind") not in WORK_KIND_ENUM:
                failures.append("work[%d][%d].kind not in enum: %r" % (s_i, w_i, w.get("kind")))
    # gaps enum + exclusion evidence
    for i, g in enumerate(rg.get("gaps") or []):
        if g.get("type") not in GAP_ENUM:
            failures.append("gap[%d].type not in enum: %r" % (i, g.get("type")))
        if not g.get("exclusion_evidence"):
            warnings.append("gap[%d] missing exclusion_evidence" % i)
        for evid in g.get("evidence_ids") or []:
            if evid not in eids:
                warnings.append("gap[%d] evidence_id %s not found" % (i, evid))
    # fabricated benchmark score detection
    for k in result:
        if "score" in k.lower() and isinstance(result[k], (int, float)):
            warnings.append("possible fabricated score key: %s = %r" % (k, result[k]))
    return ev, eids


def verify_quotes(result, work_dir, failures, warnings):
    ev = result.get("evidence_map") or []
    for e in ev:
        quote = e.get("quote")
        if not quote:
            continue
        loc = e.get("locator") or ""
        path = loc.split("#")[0]
        candidates = [
            os.path.join(work_dir, path),
            os.path.join(work_dir, "materials", os.path.basename(path)),
        ]
        matched = False
        for cand in candidates:
            if not os.path.isfile(cand):
                continue
            text, err = core.read_text(cand)
            if text is None:
                continue
            # quote may be trimmed; check a relaxed prefix (first 60 chars)
            probe = quote[:60].strip()
            if probe and probe in text:
                matched = True
                break
        if not matched:
            warnings.append("%s quote not locatable: %s" % (e.get("id"), loc))


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result_path = os.path.join(args.output, "result.json")
    report = {"path": result_path, "failures": [], "warnings": [], "passed": False}
    if not os.path.isfile(result_path):
        report["failures"].append("result.json missing")
        _write(report, args.work)
        print("selftest: result.json missing")
        return 1
    data, err = core.load_json(result_path)
    if data is None:
        report["failures"].append("result.json unparseable: %s" % err)
        _write(report, args.work)
        print("selftest: result.json unparseable: %s" % err)
        return 1
    collect(data, report["failures"], report["warnings"])
    verify_quotes(data, args.work, report["failures"], report["warnings"])
    report["passed"] = not report["failures"]
    _write(report, args.work)
    print("selftest: %d failures, %d warnings, passed=%s" % (
        len(report["failures"]), len(report["warnings"]), report["passed"]))
    return 0 if not report["failures"] else 1


def _write(report, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "selftest.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    sys.exit(main())
