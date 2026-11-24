#!/usr/bin/env python3
"""Self-test: validate result.json contract, evidence traceability, six-dimension
coverage and artifact existence. Writes /app/output/verify_report.json.

Pure Python standard library only. Never fabricates scores or results.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

VALID_STATUS = {"complete", "partial", "failed", "in_progress"}

REQUIRED_TOP = ["case_id", "status", "sources", "uncertainty", "change_log", "pending_human"]

SIX_DIMS = [
    "research_question",
    "chapter_structure",
    "argument_chain",
    "paragraph_relations",
    "redundancy",
    "conclusion_boundary",
]

# These are filled from case.json output_contract when present; if absent the
# default contract below is used. They are checked only as present keys.
DEFAULT_CONTRACT_KEYS = [
    "diagnosis",
    "edit_plan",
    "argument_map",
    "coverage",
    "version_boundary",
    "artifacts",
]

EDIT_ACTIONS = {
    "move", "merge", "split", "narrow", "add_transition",
    "delete_duplicate", "reorder", "flag_confirm",
}


def _has_value(v):
    if v is None:
        return False
    if isinstance(v, (str, list, dict)):
        return bool(v)
    return True


def _check_contract(result, contract_keys):
    findings = []
    for key in contract_keys:
        if key not in result:
            findings.append(("FAIL", "contract", "missing output_contract field: %s" % key))
        elif not _has_value(result[key]):
            findings.append(("WARN", "contract", "output_contract field empty: %s" % key))
    return findings


def _check_traceability(result):
    findings = []
    sources = result.get("sources")
    if not isinstance(sources, list) or not sources:
        findings.append(("WARN", "traceability", "sources is empty; key conclusions not yet linked"))
        return findings
    missing = []
    for i, item in enumerate(sources):
        if not isinstance(item, dict):
            missing.append("sources[%d] not an object" % i)
            continue
        for f in ("claim_id", "claim", "source_ref", "quote"):
            if not item.get(f):
                missing.append("sources[%d] missing %s" % (i, f))
    if missing:
        findings.append(("FAIL", "traceability", "incomplete source entries: %s" % "; ".join(missing[:5])))
    else:
        findings.append(("PASS", "traceability", "all %d sources have claim_id/claim/source_ref/quote" % len(sources)))
    return findings


def _check_coverage(result):
    findings = []
    cov = result.get("coverage")
    if not isinstance(cov, dict):
        findings.append(("WARN", "coverage", "coverage object missing"))
        return findings
    present = [d for d in SIX_DIMS if d in cov]
    missing = [d for d in SIX_DIMS if d not in cov]
    if missing:
        findings.append(("WARN", "coverage", "six-dimension coverage missing: %s" % ", ".join(missing)))
    else:
        findings.append(("PASS", "coverage", "all six dimensions present: %s" % ", ".join(present)))
    # dimensions marked pending means not yet diagnosed
    pending = [d for d in SIX_DIMS if isinstance(cov.get(d), dict) and cov[d].get("status") == "pending"]
    if pending:
        findings.append(("WARN", "coverage", "dimensions still pending: %s" % ", ".join(pending)))
    return findings


def _check_edit_plan(result):
    findings = []
    plan = result.get("edit_plan")
    if not isinstance(plan, list) or not plan:
        findings.append(("WARN", "edit_plan", "edit_plan empty; no executable edits recorded"))
        return findings
    bad_actions = []
    for i, item in enumerate(plan):
        if not isinstance(item, dict):
            bad_actions.append("edit_plan[%d] not object" % i)
            continue
        if item.get("action") not in EDIT_ACTIONS:
            bad_actions.append("edit_plan[%d] unknown action %r" % (i, item.get("action")))
        if not item.get("target"):
            bad_actions.append("edit_plan[%d] missing target locator" % i)
    if bad_actions:
        findings.append(("FAIL", "edit_plan", "; ".join(bad_actions[:5])))
    else:
        findings.append(("PASS", "edit_plan", "%d edit actions with valid vocabulary" % len(plan)))
    return findings


def _check_artifacts(result, out_dir):
    findings = []
    arts = result.get("artifacts")
    if not isinstance(arts, dict) or not arts:
        findings.append(("WARN", "artifacts", "artifacts empty"))
        return findings
    for label, value in arts.items():
        if isinstance(value, str):
            path = value
            if not os.path.isabs(path):
                path = os.path.join(out_dir, path)
            if os.path.exists(path):
                findings.append(("PASS", "artifacts", "%s exists: %s" % (label, path)))
            else:
                findings.append(("WARN", "artifacts", "%s missing: %s" % (label, path)))
    return findings


def self_check(result_path, out_dir):
    report = {"time": None, "result_path": result_path, "checks": [], "overall": "PASS"}
    try:
        import datetime
        report["time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    except Exception:
        report["time"] = None

    if not os.path.exists(result_path):
        report["checks"].append(("FAIL", "input", "result.json not found at %s" % result_path))
        report["overall"] = "FAIL"
        return report

    try:
        with open(result_path, "r", encoding="utf-8-sig") as fh:
            result = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        report["checks"].append(("FAIL", "input", "result.json not valid JSON: %s" % exc))
        report["overall"] = "FAIL"
        return report

    if not isinstance(result, dict):
        report["checks"].append(("FAIL", "input", "result.json root is not an object"))
        report["overall"] = "FAIL"
        return report

    for key in REQUIRED_TOP:
        if key not in result:
            report["checks"].append(("FAIL", "contract", "missing required top-level field: %s" % key))
        else:
            report["checks"].append(("PASS", "contract", "required field present: %s" % key))

    if not result.get("case_id"):
        report["checks"].append(("WARN", "contract", "case_id is empty"))

    if result.get("status") not in VALID_STATUS:
        report["checks"].append(("FAIL", "contract", "invalid status %r" % result.get("status")))

    change_log = result.get("change_log")
    if not isinstance(change_log, list) or not change_log:
        report["checks"].append(("WARN", "contract", "change_log empty; no scaffold record"))

    # contract fields: prefer case.json's own contract when recorded, else default
    contract_keys = result.get("_output_contract_keys") or DEFAULT_CONTRACT_KEYS
    report["checks"].extend(_check_contract(result, contract_keys))
    report["checks"].extend(_check_traceability(result))
    report["checks"].extend(_check_coverage(result))
    report["checks"].extend(_check_edit_plan(result))
    report["checks"].extend(_check_artifacts(result, out_dir))

    if any(level == "FAIL" for level, _a, _b in report["checks"]):
        report["overall"] = "FAIL"
    elif any(level == "WARN" for level, _a, _b in report["checks"]):
        report["overall"] = "WARN"
    else:
        report["overall"] = "PASS"
    return report


def main():
    ap = argparse.ArgumentParser(description="Self-test a result.json for contract and traceability.")
    ap.add_argument("--result", default=os.environ.get("RESULT_JSON", "/app/output/result.json"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    args = ap.parse_args()
    report = self_check(args.result, args.out)
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "verify_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
