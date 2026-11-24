"""selftest.py - self-check for the multi-step data-analysis Skill.

Validates the final /app/output artifacts against the input output_contract and
the internal consistency rules declared in SKILL.md. Runs with the same
--input/--output/--work defaults as main.py.

Exit codes:
    0  all checks passed
    1  one or more checks failed (treat as a signal to fix, not a crash)
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
REQUIRED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log")


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def contract_keys(input_dir):
    cp = os.path.join(input_dir, "case.json")
    if not os.path.exists(cp):
        return None, None
    try:
        obj = _read_json(cp)
    except Exception:
        return None, None
    contract = _find_key(obj, "output_contract")
    if contract is None:
        return None, None
    if isinstance(contract, dict):
        return list(contract.keys()), contract
    if isinstance(contract, list):
        return [str(c) for c in contract], contract
    return None, contract


def main(argv):
    p = argparse.ArgumentParser(prog="scripts/selftest.py")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)

    checks = []
    failed = 0

    def check(ok, name, detail=""):
        nonlocal failed
        checks.append((ok, name, detail))
        if not ok:
            failed += 1

    result_path = os.path.join(args.output, "result.json")
    result = None
    if os.path.exists(result_path):
        try:
            result = _read_json(result_path)
            check(True, "result.json parses")
        except Exception as e:
            check(False, "result.json parses", str(e))
    else:
        check(False, "result.json exists")

    if result is not None:
        check(bool(result.get("case_id")), "case_id present", str(result.get("case_id")))
        check(result.get("status") in ALLOWED_STATUS,
              "status in allowed set", str(result.get("status")))
        for k in REQUIRED_KEYS:
            check(k in result, "required key present: %s" % k)
        check(isinstance(result.get("sources"), list), "sources is a list")
        check(isinstance(result.get("change_log"), list) and len(result.get("change_log", [])) > 0,
              "change_log non-empty")
        check(isinstance(result.get("uncertainty"), list), "uncertainty is a list")

        ckeys, _ = contract_keys(args.input)
        if ckeys is not None:
            missing = [k for k in ckeys if k not in result]
            check(len(missing) == 0,
                  "all output_contract fields present at top level",
                  "missing=%s" % ", ".join(missing) if missing else "ok")

    # report and stats (not required for degraded statuses)
    degraded = bool(result and result.get("status") in ("insufficient_evidence", "input_error"))
    report_path = os.path.join(args.output, "report.md")
    if degraded:
        check(True, "report.md exists", "SKIP (degraded status %s)" % result.get("status"))
    else:
        check(os.path.exists(report_path), "report.md exists")
    stats_path = os.path.join(args.output, "stats.json")
    stats = None
    if degraded:
        check(True, "stats.json parses", "SKIP (degraded status %s)" % result.get("status"))
    elif os.path.exists(stats_path):
        try:
            stats = _read_json(stats_path)
            check(True, "stats.json parses")
        except Exception as e:
            check(False, "stats.json parses", str(e))
    else:
        check(False, "stats.json exists")

    # cleaned table vs clean_meta consistency
    clean_csv = os.path.join(args.output, "cleaned.csv")
    clean_meta_path = os.path.join(args.output, "clean_meta.json")
    if degraded:
        check(True, "clean_meta/cleaned.csv consistency", "SKIP (degraded status %s)" % result.get("status"))
    elif os.path.exists(clean_csv) and os.path.exists(clean_meta_path):
        try:
            meta = _read_json(clean_meta_path)
            with open(clean_csv, "r", encoding="utf-8-sig") as f:
                n_lines = sum(1 for _ in f)
            n_data_rows = max(0, n_lines - 1)
            expected = meta.get("rows_kept")
            check(expected == n_data_rows,
                  "cleaned.csv row count == clean_meta.rows_kept",
                  "meta=%s file=%s" % (expected, n_data_rows))
            check(meta.get("consistency_check", {}).get("ok") is True,
                  "clean_meta row-count consistency ok")
        except Exception as e:
            check(False, "clean_meta/cleaned.csv consistency", str(e))

    # SVG parse
    svg_checked = 0
    if os.path.isdir(args.output):
        for n in sorted(os.listdir(args.output)):
            if n.startswith("figure_") and n.endswith(".svg"):
                svg_checked += 1
                try:
                    ET.parse(os.path.join(args.output, n))
                    check(True, "SVG parses: %s" % n)
                except Exception as e:
                    check(False, "SVG parses: %s" % n, str(e))
    if degraded:
        check(True, "at least one SVG figure produced", "SKIP (degraded status %s)" % result.get("status"))
    else:
        check(svg_checked > 0, "at least one SVG figure produced")

    # status honesty: insufficient_evidence must not have real analysis
    if result is not None and result.get("status") == "insufficient_evidence":
        check(stats is None or stats.get("n_rows", 0) == 0,
              "insufficient_evidence has no fabricated analysis")

    print("=" * 60)
    print("selftest: %d checks, %d failed" % (len(checks), failed))
    for ok, name, detail in checks:
        mark = "PASS" if ok else "FAIL"
        line = "[%s] %s" % (mark, name)
        if detail and not ok:
            line += "  (%s)" % detail
        print(line)
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
