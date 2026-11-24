#!/usr/bin/env python3
"""Self-test for robustness: empty input, bad JSON, missing params, oversized file,
plus a positive quote-location check.

Builds synthetic boundary inputs in a temp directory and runs the real
`main.py` pipeline against each. Asserts graceful degradation: no traceback,
expected exit code, and a valid result.json / verification_report.json.

Exit code: 0 when all scenarios pass, 1 otherwise.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(input_dir, output_dir, work_dir, extra=None):
    cmd = [
        sys.executable, "-B", MAIN,
        "--input", input_dir,
        "--output", output_dir,
        "--work", work_dir,
    ]
    if extra:
        cmd += extra
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def load(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(content)


GOOD_CASE = json.dumps({
    "case_id": "selftest-001",
    "output_contract": {
        "deliverable": None,
        "fact_source_map": [],
        "version_boundary": None,
    },
    "materials": [{"path": "paper.md", "type": "text"}],
}, ensure_ascii=False)

PAPER = "The half-life of compound X was 3.2 h at 37 degrees Celsius in PBS.\n"
QUOTE = "half-life of compound X was 3.2 h"


def scenario(no, name, prepare, expect_rc=(0,), check=None, preseed=None):
    tmp = tempfile.mkdtemp(prefix="selftest-%s-" % name)
    input_dir = os.path.join(tmp, "input")
    output_dir = os.path.join(tmp, "output")
    work_dir = os.path.join(tmp, "work")
    prepare(input_dir)
    if preseed:
        preseed(output_dir)
    proc = run_main(input_dir, output_dir, work_dir)
    result = load(os.path.join(output_dir, "result.json"))
    report = load(os.path.join(output_dir, "verification_report.json"))

    problems = []
    if proc.returncode not in expect_rc:
        problems.append("rc=%d not in %s" % (proc.returncode, expect_rc))
    if "Traceback" in proc.stderr:
        problems.append("traceback in stderr")
    if not isinstance(result, dict):
        problems.append("result.json missing or not a JSON object")
    else:
        if not result.get("case_id"):
            problems.append("result.json lacks case_id")
        if "status" not in result:
            problems.append("result.json lacks status")
    if check:
        problems.extend(check(result, report, proc))
    status = "PASS" if not problems else "FAIL"
    print("[%s] %02d %s  rc=%d%s" % (
        status, no, name, proc.returncode,
        "" if not problems else "  -> " + "; ".join(problems),
    ))
    return not problems


def checks():
    out = []

    def prep_empty(d):
        os.makedirs(d, exist_ok=True)  # truly empty input

    out.append(scenario(1, "empty_input", prep_empty, expect_rc=(0,),
        check=lambda r, rep, p: (
            [] if r.get("status") in ("partial", "failed")
            else ["expected degraded status, got %r" % r.get("status")]
        )))

    def prep_bad_json(d):
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "case.json"), "{ not valid json !!")

    out.append(scenario(2, "bad_json", prep_bad_json, expect_rc=(0,),
        check=lambda r, rep, p: (
            [] if any(g.get("issue") == "bad_json" for g in r.get("degradations", []))
            else ["expected bad_json degradation in result.json"]
        )))

    def prep_oversized(d):
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "case.json"), GOOD_CASE)
        write(os.path.join(d, "huge.txt"), b"A" * (2 * 1024 * 1024 + 16))

    out.append(scenario(3, "oversized_file", prep_oversized, expect_rc=(0,),
        check=lambda r, rep, p: (
            [] if any(g.get("issue") == "oversized" for g in r.get("degradations", []))
            else ["expected oversized degradation in result.json"]
        )))

    # Positive path: pre-seed a fact_source_map with a verbatim quote, then
    # verify that the quote is located in the extracted paper.md corpus.
    def prep_quote(d):
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "case.json"), GOOD_CASE)
        write(os.path.join(d, "paper.md"), PAPER)

    def preseed_quote(outdir):
        os.makedirs(outdir, exist_ok=True)
        write(os.path.join(outdir, "result.json"), json.dumps({
            "case_id": "selftest-001",
            "status": "partial",
            "deliverable": None,
            "fact_source_map": [{
                "claim_id": "c1",
                "claim_text": "compound X half-life",
                "quote": QUOTE,
                "source_ref": "paper.md",
                "locator": "paper.md#L1",
                "evidence_level": "primary_quote",
            }],
            "version_boundary": None,
        }, ensure_ascii=False))

    out.append(scenario(4, "quote_located", prep_quote, expect_rc=(0,),
        check=lambda r, rep, p: (
            [] if rep and rep.get("quotes", {}).get("quotes_located", 0) == 1
            else ["expected 1 located quote, report=%r" % (rep and rep.get("quotes"))]
        ), preseed=preseed_quote))

    # missing/unknown params: run with no args at all (defaults), and unknown flag.
    tmp = tempfile.mkdtemp(prefix="selftest-param-")
    p1 = run_main(os.path.join(tmp, "e"), os.path.join(tmp, "o"), os.path.join(tmp, "w"))
    ok_params = p1.returncode in (0, 2) and "Traceback" not in p1.stderr
    p2 = subprocess.run(
        [sys.executable, "-B", MAIN, "--bogus-flag"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok_unknown = p2.returncode == 2 and "Traceback" not in p2.stderr
    status = "PASS" if (ok_params and ok_unknown) else "FAIL"
    print("[%s] %02d %s  rc_noargs=%d rc_unknown=%d%s" % (
        status, 5, "missing_unknown_params", p1.returncode, p2.returncode,
        "" if (ok_params and ok_unknown) else "  -> clean arg handling failed",
    ))
    out.append(ok_params and ok_unknown)

    return out


def main():
    print("academic-format-adaptation self-test")
    print("python: %s" % sys.version.split()[0])
    results = checks()
    passed = sum(1 for r in results if r)
    print("----")
    print("PASS %d/%d" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
