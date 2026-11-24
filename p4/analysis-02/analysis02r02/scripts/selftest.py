"""selftest.py - verify the skill package before submission.

Checks:
  1. scripts import only the Python standard library;
  2. special-function implementations return known reference values;
  3. `main.py --help` exits 0;
  4. an end-to-end run on a small synthetic dataset produces a valid result.json,
     including every output_contract key and the required envelope keys;
  5. the run is deterministic (same numbers on two executions);
  6. package size / main.py size limits are respected.

Run:  python scripts/selftest.py
"""
import ast
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)


def stdlib_only():
    problems = []
    LOCAL = {"main", "core_impl", "selftest"}
    for fn in ("main.py", "core_impl.py", "selftest.py"):
        path = os.path.join(HERE, fn)
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fn)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mods.add(node.module.split(".")[0])
        bad = sorted(m for m in mods if m not in sys.stdlib_module_names and m not in LOCAL)
        if bad:
            problems.append("%s imports non-stdlib: %s" % (fn, bad))
    return problems


def check_special_functions():
    import core_impl as c
    checks = [
        ("normal_cdf(0)", abs(c.normal_cdf(0.0) - 0.5) < 1e-12),
        ("normal_ppf(0.975)", abs(c.normal_ppf(0.975) - 1.959963984540054) < 1e-7),
        ("gammp(1,1)", abs(c.gammp(1.0, 1.0) - 0.6321205588285577) < 1e-9),
        ("betai(1,1,0.5)", abs(c.betai(1.0, 1.0, 0.5) - 0.5) < 1e-12),
        ("chi2_ppf(0.95,1)", abs(c.chi2_ppf(0.95, 1) - 3.841458820694124) < 1e-6),
        ("t_ppf(0.975,10)", abs(c.t_ppf(0.975, 10) - 2.2281388519649385) < 1e-9),
    ]
    fails = [name for name, ok in checks if not ok]
    desc = c.descriptive([1.0, 2.0, 3.0, 4.0, 5.0], "unit_unknown")
    if abs(desc["mean"] - 3.0) > 1e-9 or abs(desc["sd"] - round(math.sqrt(2.5), 6)) > 1e-9:
        fails.append("descriptive mean/sd wrong: %s" % desc)
    if abs(desc["q1"] - 2.0) > 1e-9 or abs(desc["q3"] - 4.0) > 1e-9:
        fails.append("descriptive quartiles wrong: %s" % desc)
    t = c.student_t([1.0, 2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0, 10.0])
    if abs(t["t"] - (-5.0)) > 1e-9 or t["df"] != 8:
        fails.append("student_t wrong: %s" % t)
    w = c.welch_t([1.0, 2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0, 10.0])
    if abs(w["t"] - (-5.0)) > 1e-9:
        fails.append("welch_t wrong: %s" % w)
    return fails


def run_cmd(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.returncode, r.stdout, r.stderr


def end_to_end():
    problems = []
    tmp = tempfile.mkdtemp(prefix="selftest_stat_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp)
    contract = {
        "summary": "string",
        "table_summary": None,
        "groups": [],
    }
    case = {"case_id": "selftest-case-001", "task_instruction": "two-group comparison",
            "output_contract": contract}
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f)
    rows = ["group,outcome\n"]
    for i in range(30):
        rows.append("A,%.6f\n" % (10.0 + i * 0.5))
        rows.append("B,%.6f\n" % (14.0 + i * 0.5))
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as f:
        f.writelines(rows)
    base = [sys.executable, os.path.join(HERE, "main.py")]
    for cmd in ("init", "survey", "inspect", "analyze", "build", "selfcheck"):
        rc, so, se = run_cmd(base + [cmd, "--input", inp, "--output", out, "--work", work])
        if rc != 0:
            problems.append("command %s exit %s: %s" % (cmd, rc, se.strip()))
            return problems
    rp = os.path.join(out, "result.json")
    if not os.path.isfile(rp):
        problems.append("result.json missing")
        return problems
    with open(rp, "r", encoding="utf-8") as f:
        result = json.load(f)
    for k in ("case_id", "status", "sources", "uncertainty", "change_log", "errors",
              "degradations", "pending_human", "runtime"):
        if k not in result:
            problems.append("missing envelope key: %s" % k)
    for k in contract:
        if k not in result:
            problems.append("missing output_contract key: %s" % k)
    if result["case_id"] != "selftest-case-001":
        problems.append("case_id not preserved: %r" % result["case_id"])
    if result["status"] not in ("ok", "partial", "insufficient_evidence", "input_error"):
        problems.append("invalid status: %r" % result["status"])
    if not result.get("descriptive"):
        problems.append("no descriptive results")
    if not os.path.isfile(os.path.join(out, "quality_checks.json")):
        problems.append("quality_checks.json missing")
    # determinism: re-run analyze twice, compare the statistical numbers
    import contextlib
    import io
    import core_impl as c
    sig = []
    for _ in range(2):
        with contextlib.redirect_stdout(io.StringIO()):
            c.main(["analyze", "--input", inp, "--output", out, "--work", work])
        with open(os.path.join(work, "analysis.json"), "r", encoding="utf-8") as f:
            a = json.load(f)
        sig.append(json.dumps({"d": a["descriptive"], "c": a["comparisons"]}, sort_keys=True))
    if sig[0] != sig[1]:
        problems.append("non-deterministic: two analyze runs differ")
    shutil.rmtree(tmp, ignore_errors=True)
    return problems


def size_limits():
    problems = []
    main_py = os.path.join(HERE, "main.py")
    if os.path.getsize(main_py) > 30 * 1024:
        problems.append("main.py exceeds 30KB")
    total = 0
    n_files = 0
    for root, _dirs, files in os.walk(PKG_ROOT):
        for fn in files:
            p = os.path.join(root, fn)
            total += os.path.getsize(p)
            n_files += 1
    if n_files > 14:
        problems.append("too many files: %d" % n_files)
    if total > 1.2 * 1024 * 1024:
        problems.append("package too large: %d bytes" % total)
    return problems


def main():
    problems = []
    problems += stdlib_only()
    problems += check_special_functions()
    rc, _so, _se = run_cmd([sys.executable, os.path.join(HERE, "main.py"), "--help"])
    if rc != 0:
        problems.append("main.py --help exit %s" % rc)
    problems += end_to_end()
    problems += size_limits()
    if problems:
        print("SELFTEST FAILED")
        for p in problems:
            print("  - " + p)
        return 1
    print("SELFTEST OK: stdlib-only, deterministic, contract-complete, sizes within limits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
