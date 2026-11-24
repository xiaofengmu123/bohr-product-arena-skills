"""selftest.py - case-free self-test for the search pipeline.

Covers: empty input, bad JSON, bad encoding, dedup/conflicts, output_contract
backfill, determinism, --help exit code. Uses only the standard library and
never writes any formal test case or expected answer.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core  # noqa: E402  (sibling module; __main__ guard keeps it import-safe)

PASS = 0
FAIL = 0
DETAILS = []


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("PASS: %s %s" % (name, detail))
    else:
        FAIL += 1
        print("FAIL: %s %s" % (name, detail))
    DETAILS.append((name, ok, detail))


def tmpdir():
    return tempfile.mkdtemp(prefix="srtest_")


def write(path, data, binary=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    mode = "wb" if binary else "w"
    fh = open(path, mode)
    try:
        fh.write(data)
    finally:
        fh.close()


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_help():
    proc = subprocess.run([sys.executable, "-B", os.path.join(HERE, "main.py"), "--help"],
                          capture_output=True, text=True)
    report("help_exit_zero", proc.returncode == 0, "rc=%d" % proc.returncode)


def test_empty_input():
    d = tmpdir()
    try:
        inp, work, out = d + "/in", d + "/work", d + "/out"
        os.makedirs(inp)
        core.run(["scan", "--input", inp, "--work", work])
        core.run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = read_json(out + "/result.json")
        report("empty_input_status", r.get("status") == "insufficient_evidence", "status=%s" % r.get("status"))
        report("empty_input_result_valid", "case_id" in r and isinstance(r.get("results"), list))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_json():
    d = tmpdir()
    try:
        inp, work, out = d + "/in", d + "/work", d + "/out"
        os.makedirs(inp)
        write(inp + "/case.json", "this is not valid json {{")
        core.run(["scan", "--input", inp, "--work", work])
        core.run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = read_json(out + "/result.json")
        report("bad_json_no_crash", True, "result written")
        report("bad_json_recorded", any("case_json" in str(e) or "bad_json" in str(e) for e in r.get("errors", []) + r.get("degradations", [])),
               "errors=%s" % r.get("errors"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_encoding():
    d = tmpdir()
    try:
        inp, work, out = d + "/in", d + "/work", d + "/out"
        os.makedirs(inp + "/materials")
        write(inp + "/case.json", json.dumps({"case_id": "enc", "output_contract": {}}))
        # latin-1 bytes (0xE9) are invalid UTF-8; read_text must fall back without crashing
        write(inp + "/materials/export.jsonl", b'{"title": "caf\xe9 study", "authors": "Doe, A.", "year": 2018}\n', binary=True)
        core.run(["scan", "--input", inp, "--work", work])
        core.run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = read_json(out + "/result.json")
        report("bad_encoding_no_crash", True, "result written")
        records = read_json(work + "/records.json")
        title_ok = any("caf" in str(x.get("title", "")) for x in records)
        report("bad_encoding_decoded_tolerantly", title_ok, "title decoded via fallback")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_conflicts():
    d = tmpdir()
    try:
        inp, work, out = d + "/in", d + "/work", d + "/out"
        os.makedirs(inp + "/materials")
        recs = [
            {"title": "Alpha study of mechanism", "authors": "Smith, J.", "year": 2021,
             "journal": "Nature", "doi": "10.1000/abc123"},
            {"title": "Alpha study of mechanism", "authors": "Smith, J.", "year": 2021,
             "journal": "Science", "doi": None},
        ]
        write(inp + "/materials/export.jsonl",
              "\n".join(json.dumps(r) for r in recs) + "\n")
        core.run(["scan", "--input", inp, "--work", work])
        records = read_json(work + "/records.json")
        primaries = [r for r in records if r["dedup"]["role"] == "primary"]
        duplicates = [r for r in records if r["dedup"]["role"] == "duplicate"]
        report("dedup_groups", len(primaries) == 1 and len(duplicates) == 1,
               "primary=%d duplicate=%d" % (len(primaries), len(duplicates)))
        conflict = any((r["dedup"].get("conflicts")) for r in records)
        report("dedup_conflict_detected", conflict, "conflicts present=%s" % conflict)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_output_contract_backfill():
    d = tmpdir()
    try:
        inp, work, out = d + "/in", d + "/work", d + "/out"
        os.makedirs(inp)
        write(inp + "/case.json", json.dumps({
            "case_id": "c1", "research_question": "RQ?",
            "output_contract": {"required": ["custom_metric", "results"], "fields": [{"name": "extra"}]},
        }))
        core.run(["scan", "--input", inp, "--work", work])
        core.run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = read_json(out + "/result.json")
        report("contract_case_id", r.get("case_id") == "c1", "case_id=%s" % r.get("case_id"))
        report("contract_custom_metric", "custom_metric" in r and r["custom_metric"] is None)
        report("contract_extra", "extra" in r and r["extra"] is None)
        report("contract_echoed", isinstance(r.get("output_contract"), dict))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_determinism():
    d = tmpdir()
    try:
        inp, work1, work2, out1, out2 = (d + "/in", d + "/w1", d + "/w2", d + "/o1", d + "/o2")
        os.makedirs(inp + "/materials")
        write(inp + "/case.json", json.dumps({"case_id": "det", "research_question": "RQ"}))
        write(inp + "/materials/export.json",
              json.dumps([{"title": "Determinism test paper", "authors": "Doe, A.", "year": 2020, "journal": "J"}]))
        for w, o in ((work1, out1), (work2, out2)):
            core.run(["scan", "--input", inp, "--work", w])
            core.run(["skeleton", "--input", inp, "--output", o, "--work", w])
        r1 = read_json(out1 + "/result.json")
        r2 = read_json(out2 + "/result.json")
        for r in (r1, r2):
            r["runtime"]["started_at"] = None
            r["runtime"]["wall_seconds"] = None
        report("determinism", r1 == r2, "result.json identical after stripping wall-clock fields")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_screen_validation():
    d = tmpdir()
    try:
        inp, work, out = d + "/in", d + "/work", d + "/out"
        os.makedirs(inp + "/materials")
        write(inp + "/case.json", json.dumps({"case_id": "sc", "output_contract": {}}))
        write(inp + "/materials/export.json",
              json.dumps([{"title": "Screening paper", "authors": "Doe, A.", "year": 2019, "journal": "J"}]))
        core.run(["scan", "--input", inp, "--work", work])
        bad = [{"id": "rec-001", "decision": "include", "reason": "no evidence"}]
        write(work + "/screen_in.json", json.dumps(bad))
        rc = core.run(["screen", "--work", work, "--file", work + "/screen_in.json"])
        errors = read_json(work + "/errors.json")
        report("screen_validation", rc == 0 and any("include_no_evidence" in str(e) for e in errors),
               "errors=%s" % errors)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    test_help()
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_dedup_conflicts()
    test_output_contract_backfill()
    test_determinism()
    test_screen_validation()
    print("\nSELFTEST: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
