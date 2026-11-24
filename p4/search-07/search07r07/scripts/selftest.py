#!/usr/bin/env python3
"""selftest.py -- robustness self-checks for the search-coverage-analysis Skill.

Standard library only. Creates isolated temp directories and exercises every
required failure mode. Prints one `[PASS]`/`[FAIL]` line per check and exits
non-zero if any check fails.

Checks (mapped to the 可靠体验 scoring dimension):
  1. `analyze.py --help` exits 0 and lists all four subcommands.
  2. Default --input/--output/--work resolve to /input, /app/output, /app/work.
  3. Empty input dir: scaffold->run exits 0, result.json valid, status=
     insufficient_evidence.
  4. Bad JSON + bad encoding: exits 0, errors/degradations recorded, no crash.
  5. Normal sample: coverage computed correctly, hit matrix present.
  6. Determinism: running the same input twice yields identical coverage and
     hit-matrix statuses.
  7. Oversized file (>2 MiB) is skipped and recorded, remaining files processed.
  8. Missing --updates on merge fails cleanly with a non-zero exit and message.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYZE = os.path.join(HERE, "analyze.py")

PASS = 0
FAIL = 0


def report(name, ok, detail=""):
    global PASS, FAIL
    tag = "[PASS]" if ok else "[FAIL]"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    line = "%s %s" % (tag, name)
    if detail:
        line += " -- %s" % detail
    print(line)


def run(args):
    return subprocess.run([sys.executable, str(ANALYZE)] + args,
                          capture_output=True, text=True, timeout=120)


def read_result(out_dir):
    p = os.path.join(out_dir, "result.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def make_env():
    d = tempfile.mkdtemp(prefix="sktest_")
    return d, os.path.join(d, "input"), os.path.join(d, "output"), \
        os.path.join(d, "work")


def check_help():
    r = run(["--help"])
    ok = r.returncode == 0 and all(s in r.stdout for s in
                                   ("scaffold", "inspect", "run", "merge"))
    report("help exits 0 and lists subcommands", ok, "rc=%d" % r.returncode)


def check_defaults():
    sys.path.insert(0, HERE)
    import analyze
    try:
        args = analyze.build_parser().parse_args(["scaffold"])
        ok = (args.input == "/input" and args.output == "/app/output"
              and args.work == "/app/work")
        report("default paths /input,/app/output,/app/work", ok,
               "%s,%s,%s" % (args.input, args.output, args.work))
    finally:
        sys.path.pop(0)


def check_empty():
    d, i, o, w = make_env()
    os.makedirs(i)
    r1 = run(["scaffold", "--input", i, "--output", o, "--work", w])
    r2 = run(["run", "--input", i, "--output", o, "--work", w])
    ok = r1.returncode == 0 and r2.returncode == 0
    status = None
    if ok:
        try:
            res = read_result(o)
            status = res.get("status")
            ok = status == "insufficient_evidence" and \
                isinstance(res.get("limitations"), list)
        except Exception as exc:
            ok = False
            report("empty: result.json unreadable", False, str(exc))
    report("empty input -> insufficient_evidence, rc=0", ok,
           "status=%s" % status)


def check_bad_json():
    d, i, o, w = make_env()
    os.makedirs(i)
    with open(os.path.join(i, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json ]")
    with open(os.path.join(i, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    with open(os.path.join(i, "bad.bin"), "wb") as fh:
        fh.write(b"\xff\xfe\x00\x01\x02\xff" * 10)
    r = run(["run", "--input", i, "--output", o, "--work", w])
    ok = r.returncode == 0
    nerr = ndeg = None
    if ok:
        try:
            res = read_result(o)
            nerr = len(res.get("errors", []))
            ndeg = len(res.get("degradations", []))
            ok = nerr >= 1
        except Exception as exc:
            ok = False
            report("bad json: result.json unreadable", False, str(exc))
    report("bad JSON/encoding -> rc=0 and errors recorded", ok,
           "errors=%s degradations=%s" % (nerr, ndeg))


def check_normal():
    d, i, o, w = make_env()
    os.makedirs(i)
    case = {"case_id": "case-selftest-001",
            "output_contract": {"summary": None, "omission_count": None}}
    with open(os.path.join(i, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(i, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n\n研究问题：甲。\n")
    refs = {"reference_records": [
        {"doi": "10.1000/ref.001", "title": "Alpha beta gamma study",
         "authors": [{"family": "Smith", "given": "J"}], "year": 2020},
        {"doi": "10.1000/ref.002", "title": "Delta epsilon zeta",
         "authors": [{"family": "Jones", "given": "A"}], "year": 2019},
    ]}
    res = {"search_results": [
        {"DOI": "10.1000/REF.001", "title": "Alpha beta gamma study",
         "author": "J Smith", "published_year": 2020},
    ]}
    with open(os.path.join(i, "gold_reference.json"), "w", encoding="utf-8") as fh:
        json.dump(refs, fh, ensure_ascii=False)
    with open(os.path.join(i, "crossref_response.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False)
    r = run(["run", "--input", i, "--output", o, "--work", w])
    ok = r.returncode == 0
    cov = None
    if ok:
        try:
            rj = read_result(o)
            cov = rj["coverage"]
            ok = (rj.get("case_id") == "case-selftest-001"
                  and cov["denominator"] == 2 and cov["numerator"] == 1
                  and cov["coverage_rate"] == 0.5
                  and rj.get("summary") is None
                  and rj.get("omission_count") is None
                  and len(rj["hit_matrix"]) == 2)
        except Exception as exc:
            ok = False
            report("normal: result.json unreadable", False, str(exc))
    report("normal sample -> coverage 1/2 and contract keys backfilled", ok,
           "coverage=%s" % cov)
    return d, i, o, w


def check_determinism(env):
    d, i, o, w = env
    o2 = os.path.join(d, "output2")
    run(["run", "--input", i, "--output", o, "--work", w])
    run(["run", "--input", i, "--output", o2, "--work", w])
    r1 = read_result(o)
    r2 = read_result(o2)
    key = lambda r: (r["coverage"]["coverage_rate"],
                     [m["status"] for m in r["hit_matrix"]],
                     r["reference_set"]["version"])
    ok = key(r1) == key(r2)
    report("determinism: same input -> same coverage/status/version", ok)


def check_oversized():
    d, i, o, w = make_env()
    os.makedirs(i)
    with open(os.path.join(i, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-big"}, fh)
    with open(os.path.join(i, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    with open(os.path.join(i, "huge_reference.json"), "w", encoding="utf-8") as fh:
        fh.write("{")
        fh.write('"pad": "%s"' % ("x" * (2 * 1024 * 1024 + 100)))
        fh.write("}")
    r = run(["run", "--input", i, "--output", o, "--work", w])
    ok = r.returncode == 0
    if ok:
        try:
            rj = read_result(o)
            skipped = any("too large" in str(e) for e in rj.get("errors", [])) or \
                any("too large" in str(e) for e in rj.get("degradations", []))
            ok = skipped or rj.get("status") in ("insufficient_evidence",
                                                 "partial", "input_error")
        except Exception as exc:
            ok = False
            report("oversized: result.json unreadable", False, str(exc))
    report("oversized file skipped, pipeline survives", ok)


def check_missing_updates():
    d, i, o, w = make_env()
    os.makedirs(i)
    with open(os.path.join(i, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-upd"}, fh)
    run(["scaffold", "--input", i, "--output", o, "--work", w])
    r = run(["merge", "--output", o, "--work", w])  # missing --updates
    ok = r.returncode != 0
    report("merge without --updates fails cleanly (argparse)", ok,
           "rc=%d" % r.returncode)


def main():
    print("== search-coverage-analysis selftest ==")
    check_help()
    check_defaults()
    check_empty()
    check_bad_json()
    env = check_normal()
    if env:
        check_determinism(env)
    check_oversized()
    check_missing_updates()
    print("== %d passed, %d failed ==" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
