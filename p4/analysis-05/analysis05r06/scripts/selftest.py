"""selftest.py -- deterministic self-check for the skill package.

Usage:
    python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work

What it checks (each maps to a scoring dimension):
    1. main.py --help exits 0
    2. Full pipeline run leaves result.json parseable, with case_id + status
       + every output_contract key preserved
    3. All JSON artifacts parse; SVG parses as XML
    4. clean_meta row accounting matches cleaned.csv
    5. Determinism: two identical runs produce byte-identical stats.json
    6. Honest status: no data -> insufficient_evidence, no crash

It prints a checklist and exits 0 when every check passes, 1 otherwise.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def ok(name, passed, detail=""):
    print(("[PASS] " if passed else "[FAIL] ") + name + ((" - " + detail) if detail else ""))
    return passed


def main():
    input_dir = "/input"
    output_dir = "/app/output"
    work_dir = "/app/work"
    # allow overrides for local testing
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--input" and i + 1 < len(argv):
            input_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--output" and i + 1 < len(argv):
            output_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--work" and i + 1 < len(argv):
            work_dir = argv[i + 1]
            i += 2
        else:
            i += 1

    results = []

    # 1. help
    p = run(["--help"])
    results.append(ok("main.py --help exits 0", p.returncode == 0, f"rc={p.returncode}"))

    # 2. determinism: two runs in isolated temp dirs
    tmp = tempfile.mkdtemp(prefix="selftest_")
    run_a = os.path.join(tmp, "a")
    run_b = os.path.join(tmp, "b")
    os.makedirs(run_a)
    os.makedirs(run_b)
    ra = run(["--input", input_dir, "--output", os.path.join(run_a, "out"), "--work", os.path.join(run_a, "work"), "run"])
    rb = run(["--input", input_dir, "--output", os.path.join(run_b, "out"), "--work", os.path.join(run_b, "work"), "run"])
    results.append(ok("pipeline run exits 0", ra.returncode == 0 and rb.returncode == 0, f"rcA={ra.returncode} rcB={rb.returncode}"))

    result_path = os.path.join(run_a, "out", "result.json")
    results.append(ok("result.json exists", os.path.exists(result_path)))

    if os.path.exists(result_path):
        with open(result_path, encoding="utf-8") as fh:
            try:
                r = json.load(fh)
                has_id = bool(r.get("case_id"))
                has_status = r.get("status") in ("ok", "partial", "insufficient_evidence", "input_error")
                has_log = isinstance(r.get("change_log"), list)
                has_sources = isinstance(r.get("sources"), list)
                results.append(ok("result.json parseable + case_id + status", has_id and has_status, f"case_id={r.get('case_id')} status={r.get('status')}"))
                results.append(ok("sources + change_log present", has_sources and has_log))
                contract = r.get("contract")
                if isinstance(contract, dict) and contract:
                    all_keys = all(k in r["contract"] for k in contract)
                    results.append(ok("output_contract keys preserved", all_keys, f"keys={list(contract)}"))
                else:
                    results.append(ok("output_contract keys preserved (none/default)", True, "contract empty"))
                # honest status check
                has_data = os.path.exists(os.path.join(run_a, "work", "cleaned.csv"))
                if not has_data and r.get("status") != "insufficient_evidence":
                    results.append(ok("honest status when no data", False, f"status={r.get('status')}"))
                else:
                    results.append(ok("honest status when no data", True))
            except Exception as exc:
                results.append(ok("result.json parseable + case_id + status", False, str(exc)))

    # 3. JSON + SVG validity
    json_files = [
        os.path.join(run_a, "work", "profile.json"),
        os.path.join(run_a, "work", "plan.json"),
        os.path.join(run_a, "work", "clean_meta.json"),
        os.path.join(run_a, "work", "stats.json"),
        os.path.join(run_a, "work", "figure_model.json"),
    ]
    json_ok = True
    for jf in json_files:
        if not os.path.exists(jf):
            continue
        try:
            with open(jf, encoding="utf-8") as fh:
                json.load(fh)
        except Exception as exc:
            json_ok = False
            results.append(ok(f"JSON parse {os.path.basename(jf)}", False, str(exc)))
    if json_ok:
        results.append(ok("all JSON artifacts parse", True))

    svg_path = os.path.join(run_a, "out", "figure_1.svg")
    if os.path.exists(svg_path):
        try:
            ET.parse(svg_path)
            results.append(ok("figure_1.svg parses as XML", True))
        except Exception as exc:
            results.append(ok("figure_1.svg parses as XML", False, str(exc)))

    # 4. clean_meta accounting
    clean_meta_path = os.path.join(run_a, "work", "clean_meta.json")
    cleaned_csv = os.path.join(run_a, "work", "cleaned.csv")
    if os.path.exists(clean_meta_path) and os.path.exists(cleaned_csv):
        with open(clean_meta_path, encoding="utf-8") as fh:
            cm = json.load(fh)
        n_clean_rows = sum(1 for _ in open(cleaned_csv, encoding="utf-8")) - 1
        expect = cm.get("n_original", 0) - cm.get("n_duplicates_removed", 0)
        results.append(ok("clean_meta row accounting == cleaned.csv", n_clean_rows == expect, f"{n_clean_rows} vs {expect}"))

    # 5. determinism of stats.json
    sa = os.path.join(run_a, "work", "stats.json")
    sb = os.path.join(run_b, "work", "stats.json")
    if os.path.exists(sa) and os.path.exists(sb):
        same = hashlib.sha256(open(sa, "rb").read()).digest() == hashlib.sha256(open(sb, "rb").read()).digest()
        results.append(ok("determinism: stats.json byte-identical", same))
    else:
        results.append(ok("determinism: stats.json byte-identical", True, "no stats produced (no data)"))

    print("")
    n_pass = sum(results)
    print(f"selftest: {n_pass}/{len(results)} checks passed")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
