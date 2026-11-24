#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: validate the produced result.json against the input contract.

Usage
-----
  python3 -B scripts/selftest.py --input /input --output /app/output

Exit codes
----------
  0  all checks pass (or there is no output yet and nothing to check)
  1  at least one contract / integrity check failed

The script only verifies what actually exists on disk; it never fabricates
values. A missing ``result.json`` is reported as a failure so the calling
agent can go back and produce one.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbscan  # noqa: E402

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _flatten_contract(contract, prefix, out):
    if isinstance(contract, dict):
        for key in sorted(contract.keys()):
            val = contract[key]
            path = key if not prefix else "%s.%s" % (prefix, key)
            if isinstance(val, dict) and "type" not in val and val:
                _flatten_contract(val, path, out)
            else:
                out.append(path)
    elif isinstance(contract, list):
        if all(isinstance(x, str) for x in contract):
            out.extend(contract)
        else:
            for item in contract:
                if isinstance(item, dict):
                    name = (item.get("name") or item.get("field")
                            or item.get("key") or item.get("path"))
                    if name:
                        out.append(name)
                    else:
                        _flatten_contract(item, prefix, out)
                else:
                    out.append(str(item))
    elif isinstance(contract, str) and contract.strip():
        out.append(contract)


def has_path(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)

    failures = []
    checks = []

    def check(ok, msg):
        checks.append({"ok": bool(ok), "check": msg})
        if not ok:
            failures.append(msg)

    result_path = os.path.join(args.output, "result.json")
    result = load_json(result_path)
    check(result is not None, "result.json exists and parses")

    case_path = os.path.join(args.input, "case.json")
    case = load_json(case_path)
    case_id = None
    contract = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("caseId") or case.get("id")
        contract = (case.get("output_contract") or case.get("outputContract")
                    or case.get("contract"))

    if result is not None:
        if case_id is not None:
            check(str(result.get("case_id")) == str(case_id),
                  "result.case_id matches input case.json")
        else:
            check(True, "no case_id in input; case_id check skipped")

        check(result.get("status") in ALLOWED_STATUS,
              "result.status in %s" % (ALLOWED_STATUS,))

        for key in ("sources", "uncertainty", "change_log"):
            check(isinstance(result.get(key), list),
                  "result.%s is a list" % key)

        for i, s in enumerate(result.get("sources") or []):
            if not isinstance(s, dict):
                check(False, "sources[%d] is an object" % i)
                continue
            check("locator" in s and "quote" in s,
                  "sources[%d] has locator + quote" % i)

        for i, c in enumerate(result.get("change_log") or []):
            if not isinstance(c, dict):
                check(False, "change_log[%d] is an object" % i)
                continue
            check(all(k in c for k in ("ts", "step", "change", "status")),
                  "change_log[%d] has ts/step/change/status" % i)

        for i, q in enumerate(result.get("query_log") or []):
            if not isinstance(q, dict):
                check(False, "query_log[%d] is an object" % i)
                continue
            check("sql" in q and "row_count" in q,
                  "query_log[%d] has sql + row_count" % i)

        # output_contract field-by-field mirror
        contract_fields = []
        _flatten_contract(contract, "", contract_fields)
        for f in contract_fields:
            check(has_path(result, f),
                  "contract field %r present in result.json" % f)

        # artifacts referenced must exist
        for rel, role in (result.get("artifacts") or {}).items():
            p = os.path.join(args.output, rel)
            check(os.path.isfile(p),
                  "artifact %s (%s) exists" % (rel, role))

        # contract fidelity record consistent with the mirror
        fid = result.get("contract_fidelity")
        if isinstance(fid, dict):
            check(fid.get("total") == len(contract_fields),
                  "contract_fidelity.total equals number of contract leaves")

    report_path = os.path.join(args.output, "selftest_report.json")
    report = {"checks": checks, "failures": failures,
              "passed": len(checks) - len(failures), "total": len(checks)}
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    for f in failures:
        print("FAIL: %s" % f)
    print("selftest: %d/%d checks passed" % (report["passed"], report["total"]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
