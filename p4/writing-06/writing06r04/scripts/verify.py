#!/usr/bin/env python3
"""Validate result.json against the contract and check evidence traceability.

Writes /app/output/verify_report.json. Exits 0 even when checks FAIL so the
pipeline never crashes; the report carries the pass/fail signal.
"""
import json
import os
import sys

DEFAULTS = {
    "result_json": "/app/output/result.json",
    "verify_report": "/app/output/verify_report.json",
}

SIX_DIMENSIONS = {
    "研究问题与贡献主张对齐",
    "章节结构与叙事顺序",
    "论证链与论据对应",
    "段落关系",
    "重复内容",
    "结论边界与表述尺度",
}
SEVERITIES = {"critical", "major", "minor"}
ACTION_TYPES = {"move", "merge", "split", "narrow", "add_transition", "delete",
                "rewrite", "add_limitation", "reorder", "restate"}
STRENGTHS = {"sufficient", "insufficient", "missing", "circular", "leap"}
STATUSES = {"in_progress", "complete", "partial", "failed"}


def _env(name, default):
    return os.environ.get(name) or default


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main():
    result_json = _env("RESULT_JSON", DEFAULTS["result_json"])
    verify_report = _env("VERIFY_REPORT", DEFAULTS["verify_report"])
    r, err = _read_json(result_json)
    checks = []

    def add(level, code, msg):
        checks.append({"level": level, "code": code, "message": msg})

    if err:
        add("FAIL", "unreadable", "result.json unreadable: %s" % err)
    else:
        for field in ("case_id", "status", "sources", "uncertainty", "change_log"):
            if field not in r or r[field] in (None, ""):
                add("FAIL", "missing_field", "missing required field: %s" % field)
            else:
                add("PASS", "field_present", "field present: %s" % field)

        if r.get("status") not in STATUSES:
            add("WARN", "status_value", "unexpected status: %r" % r.get("status"))

        sources = r.get("sources") or []
        if isinstance(sources, list):
            bad = [i for i, s in enumerate(sources)
                   if not isinstance(s, dict) or not (s.get("locator") and s.get("quote"))]
            if bad:
                add("FAIL", "source_traceability",
                    "%d sources lack locator+quote" % len(bad))
            else:
                add("PASS", "source_traceability", "%d sources have locator+quote" % len(sources))
        else:
            add("FAIL", "sources_type", "sources must be an array")

        diag = r.get("diagnosis")
        if isinstance(diag, dict):
            issues = diag.get("issues")
            if isinstance(issues, list):
                problems = []
                for i, it in enumerate(issues):
                    if not isinstance(it, dict):
                        problems.append("issues[%d] not object" % i)
                        continue
                    for f in ("issue_id", "dimension", "severity", "location",
                              "evidence", "edit_action"):
                        if not it.get(f):
                            problems.append("issues[%d].%s missing" % (i, f))
                    if it.get("dimension") and it["dimension"] not in SIX_DIMENSIONS:
                        problems.append("issues[%d].dimension unknown: %r" % (i, it["dimension"]))
                    if it.get("severity") and it["severity"] not in SEVERITIES:
                        problems.append("issues[%d].severity unknown: %r" % (i, it["severity"]))
                    ev = it.get("evidence") or {}
                    if not (ev.get("locator") and ev.get("quote")):
                        problems.append("issues[%d].evidence lacks locator+quote" % i)
                    ea = it.get("edit_action") or {}
                    if ea.get("action_type") and ea["action_type"] not in ACTION_TYPES:
                        problems.append("issues[%d].edit_action.action_type unknown: %r"
                                        % (i, ea["action_type"]))
                for p in problems[:20]:
                    add("FAIL", "issue_field", p)
                if not problems:
                    add("PASS", "issues_valid", "%d issues validated" % len(issues))
            else:
                add("WARN", "issues_missing", "diagnosis.issues missing or not array")

            cov = diag.get("dimension_coverage")
            if isinstance(cov, dict):
                missing = [d for d in sorted(SIX_DIMENSIONS) if d not in cov]
                if missing:
                    add("WARN", "dimension_unchecked",
                        "dimensions without explicit coverage: %s" % ", ".join(missing))
                else:
                    add("PASS", "dimension_coverage", "all six dimensions covered")
            else:
                add("WARN", "dimension_coverage_missing", "dimension_coverage missing")

            cem = diag.get("claim_evidence_map")
            if isinstance(cem, list):
                bad_cem = [i for i, c in enumerate(cem)
                           if not isinstance(c, dict)
                           or not (c.get("claim") and c.get("claim_location")
                                   and c.get("strength") in STRENGTHS)]
                if bad_cem:
                    add("WARN", "claim_map_field", "%d claims lack required fields" % len(bad_cem))
                else:
                    add("PASS", "claim_map", "%d claims mapped" % len(cem))
            else:
                add("WARN", "claim_map_missing", "claim_evidence_map missing or not array")
        else:
            add("FAIL", "diagnosis_missing", "diagnosis missing or not object")

        cl = r.get("change_log")
        if isinstance(cl, list) and cl:
            add("PASS", "change_log", "%d change_log entries" % len(cl))
        else:
            add("WARN", "change_log_empty", "change_log empty")

    report = {
        "result_json": result_json,
        "ok": not any(c["level"] == "FAIL" for c in checks),
        "fail_count": sum(1 for c in checks if c["level"] == "FAIL"),
        "warn_count": sum(1 for c in checks if c["level"] == "WARN"),
        "checks": checks,
    }
    try:
        os.makedirs(os.path.dirname(verify_report), exist_ok=True)
        with open(verify_report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": report["ok"], "fail": report["fail_count"],
        "warn": report["warn_count"], "verify_report": verify_report,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
