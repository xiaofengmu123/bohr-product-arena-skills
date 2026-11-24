#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - robustness and artifact self-checks.

Run from the Skill root:
    python scripts/selftest.py

Checks:
  A1  --help exits 0 and lists all subcommands
  A2  scripts use standard library only
  B1  demo runs end-to-end and validate returns valid=true
  B2  empty input dir -> status=input_error, exit 0, no crash
  B3  bad JSON -> errors recorded, exit 0, no crash
  B4  insufficient evidence -> status=insufficient_evidence
  C1  repeated runs produce byte-identical result.json
  D1  artifacts parse / open correctly
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
FORBIDDEN_IMPORTS = ("numpy", "pandas", "matplotlib", "scipy", "requests",
                     "bs4", "lxml", "yaml", "PIL", "sklearn", "torch")

checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    print("%-4s %-42s %s" % ("PASS" if ok else "FAIL", name, detail))


def run_cmd(args, **kwargs):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, text=True, **kwargs,
    )


def a1_help():
    proc = run_cmd(["--help"])
    ok = proc.returncode == 0 and all(s in proc.stdout for s in
                                      ("inspect", "assemble", "validate", "demo"))
    check("A1 help+subcommands", ok, "exit=%d" % proc.returncode)


def a2_stdlib():
    bad = []
    for root, _, names in os.walk(HERE):
        for name in names:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith(("import ", "from ")):
                        mod = line.split()[1].split(".")[0]
                        if mod in FORBIDDEN_IMPORTS:
                            bad.append("%s: %s" % (name, line.strip()))
    check("A2 stdlib-only", not bad, "; ".join(bad))


def run_demo(tmp):
    demo_work = os.path.join(tmp, "demo_work")
    demo_out = os.path.join(tmp, "demo_work", "demo_output")
    proc = run_cmd(["demo", "--work", demo_work, "--input", os.path.join(tmp, "noinput"),
                    "--output", os.path.join(tmp, "nooutput")])
    result_path = os.path.join(demo_out, "result.json")
    return proc, result_path


def b1_demo(tmp):
    proc, result_path = run_demo(tmp)
    if proc.returncode != 0 or not os.path.exists(result_path):
        check("B1 demo end-to-end", False, "exit=%d stderr=%s" % (proc.returncode, proc.stderr[:200]))
        return
    vproc = run_cmd(["validate", result_path])
    try:
        verdict = json.loads(vproc.stdout)
    except Exception:  # noqa: BLE001
        verdict = {"valid": False}
    ok = verdict.get("valid") is True
    check("B1 demo end-to-end", ok, "status=%s" % verdict.get("issues", "")[:120])


def b2_empty_input(tmp):
    empty = os.path.join(tmp, "empty_in")
    out = os.path.join(tmp, "empty_out")
    work = os.path.join(tmp, "empty_work")
    os.makedirs(empty)
    proc = run_cmd(["inspect", "--input", empty, "--output", out, "--work", work])
    result, _ = load_json(os.path.join(out, "result.json"))
    ok = proc.returncode == 0 and result and result.get("status") == "input_error"
    check("B2 empty input", ok, "status=%s" % (result or {}).get("status"))


def b3_bad_json(tmp):
    bad = os.path.join(tmp, "bad_in")
    out = os.path.join(tmp, "bad_out")
    work = os.path.join(tmp, "bad_work")
    os.makedirs(bad)
    with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not json ")
    proc = run_cmd(["inspect", "--input", bad, "--output", out, "--work", work])
    result, _ = load_json(os.path.join(out, "result.json"))
    ok = (proc.returncode == 0 and result and result.get("status") == "input_error"
          and result.get("errors"))
    check("B3 bad JSON", ok, "errors=%s" % (result or {}).get("errors"))


def b4_insufficient(tmp):
    in_dir = os.path.join(tmp, "insuff_in")
    out = os.path.join(tmp, "insuff_out")
    work = os.path.join(tmp, "insuff_work")
    os.makedirs(in_dir)
    with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "no-plans", "research_question": "q"}, fh)
    proc = run_cmd(["inspect", "--input", in_dir, "--output", out, "--work", work])
    result, _ = load_json(os.path.join(out, "result.json"))
    ok = proc.returncode == 0 and result and result.get("status") == "insufficient_evidence"
    check("B4 insufficient evidence", ok, "status=%s" % (result or {}).get("status"))


def c1_deterministic(tmp):
    proc1, p1 = run_demo(tmp)
    # second run in a fresh temp dir so the demo regenerates from scratch
    tmp2 = tempfile.mkdtemp(prefix="skillselftest2_")
    proc2, p2 = run_demo(tmp2)
    if not (os.path.exists(p1) and os.path.exists(p2)):
        check("C1 deterministic", False, "missing results")
        return
    with open(p1, "rb") as fh:
        b1 = fh.read()
    with open(p2, "rb") as fh:
        b2 = fh.read()
    check("C1 deterministic", b1 == b2, "bytes_equal=%s" % (b1 == b2))


def d1_artifacts(tmp):
    _, result_path = run_demo(tmp)
    outdir = os.path.dirname(result_path)
    ok = True
    detail = []
    for name in ("comparison_matrix.svg", "comparison_matrix.csv",
                 "plan_comparison.md", "comparison_matrix.pdf"):
        fp = os.path.join(outdir, name)
        if not os.path.exists(fp):
            ok = False
            detail.append("missing:%s" % name)
    with open(os.path.join(outdir, "comparison_matrix.pdf"), "rb") as fh:
        pdf = fh.read()
    if not (pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")):
        ok = False
        detail.append("pdf:bad-trailer")
    with open(os.path.join(outdir, "comparison_matrix.csv"), "rb") as fh:
        if fh.read(3) != b"\xef\xbb\xbf":
            ok = False
            detail.append("csv:no-bom")
    check("D1 artifacts", ok, "; ".join(detail))


def load_json(path):
    if not os.path.exists(path):
        return None, ["missing"]
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), []
    except Exception as exc:  # noqa: BLE001
        return None, [str(exc)]


def main(argv):
    tmp = tempfile.mkdtemp(prefix="skillselftest_")
    a1_help()
    a2_stdlib()
    b1_demo(tmp)
    b2_empty_input(tmp)
    b3_bad_json(tmp)
    b4_insufficient(tmp)
    c1_deterministic(tmp)
    d1_artifacts(tmp)
    failed = [c for c in checks if not c[1]]
    print("\n%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    if failed:
        print("failed:", ", ".join(c[0] for c in failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
