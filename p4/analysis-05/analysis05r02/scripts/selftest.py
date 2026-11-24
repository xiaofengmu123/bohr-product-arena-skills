"""selftest.py - runnable self-test suite for the budgeted tabular analysis skill.

Runs the full pipeline in-process on a synthetic demo input (clearly labeled as
an example, not a formal case), then checks the contract, row arithmetic,
SVG validity, determinism, and degradation paths. Exit code 0 = all passed.

    python3 -B scripts/selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as impl

DEMO_CSV = """group,target1,target2
A,1.0,10
A,1.2,11
A,1.0,10
B,2.0,20
B,2.2,22
B,,21
C,3.0,30
C,3.2,31
C,3.4,99
D,,
"""

DEMO_CASE = {"case_id": "demo-synthetic-001",
             "output_contract": {"fields": ["analysis_results", "quality_evidence", "anomaly_notes"]}}

DEMO_TASK = "# demo task\n\nCompare target1/target2 across group; compute correlation target1~target2.\n"


def run(argv_list):
    """Invoke core_impl.main in-process; returns exit code."""
    try:
        return impl.main(argv_list)
    except SystemExit as exc:
        return int(exc.code or 0)


def fresh_env(label):
    base = tempfile.mkdtemp(prefix="skilltest_%s_" % label)
    d = {"input": os.path.join(base, "input"), "output": os.path.join(base, "output"),
         "work": os.path.join(base, "work"), "base": base}
    for k in ("input", "output", "work"):
        os.makedirs(d[k], exist_ok=True)
    return d


def write_demo(env):
    with open(os.path.join(env["input"], "data.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(DEMO_CSV)
    with open(os.path.join(env["input"], "case.json"), "w", encoding="utf-8") as fh:
        json.dump(DEMO_CASE, fh)
    with open(os.path.join(env["input"], "task.md"), "w", encoding="utf-8") as fh:
        fh.write(DEMO_TASK)


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print("  [PASS] %s" % name)
    else:
        FAILED += 1
        print("  [FAIL] %s %s" % (name, detail))


def main():
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("== selftest: full pipeline on synthetic demo ==")
    env = fresh_env("demo")
    write_demo(env)
    a = {"input": env["input"], "output": env["output"], "work": env["work"]}

    check("scaffold exit 0", run(["scaffold", "--input", env["input"], "--output", env["output"], "--work", env["work"]]) == 0)
    result = impl.read_json(os.path.join(env["output"], "result.json"))
    check("skeleton has case_id + status", isinstance(result, dict) and result.get("case_id") and result.get("status") == "partial")
    check("skeleton has all contract keys", isinstance(result, dict) and all(k in result for k in ("analysis_results", "quality_evidence", "anomaly_notes")))
    check("clock.json written", os.path.isfile(os.path.join(env["work"], "clock.json")))

    check("profile exit 0", run(["profile", "--input", env["input"], "--work", env["work"]]) == 0)
    prof = impl.read_json(os.path.join(env["work"], "profile.json"))
    check("profile found primary + inferred group/targets", isinstance(prof, dict) and prof.get("primary") and prof.get("inferred", {}).get("group") == "group" and "target1" in prof.get("inferred", {}).get("targets", []))

    check("clean exit 0", run(["clean", "--input", env["input"], "--work", env["work"]]) == 0)
    cm = impl.read_json(os.path.join(env["work"], "clean_meta.json"))
    orig = cm.get("source", {}).get("original_rows")
    final = cm.get("final_rows")
    dropped = sum(r.get("dropped", 0) for r in cm.get("rules", []))
    check("clean_meta row arithmetic (orig-final == dropped)", orig is not None and final is not None and (orig - final) == dropped,
          "orig=%s final=%s dropped=%s" % (orig, final, dropped))
    check("outlier flagged only, not dropped", any(r.get("flagged", 0) > 0 and r.get("dropped", 0) == 0 for r in cm.get("rules", []) if r.get("step") == 3))

    check("analyze exit 0", run(["analyze", "--input", env["input"], "--work", env["work"], "--corr", "target1,target2"]) == 0)
    stats = impl.read_json(os.path.join(env["work"], "stats.json"))
    check("stats has group comparison (ANOVA path)", isinstance(stats, dict) and stats.get("group_comparison", {}).get("target1", {}).get("test_parametric", {}).get("name") == "anova_f")
    check("stats has non-parametric test", stats.get("group_comparison", {}).get("target1", {}).get("test_nonparametric", {}).get("name") == "kruskal_wallis_h")
    check("stats has pearson correlation", stats.get("correlation", {}).get("target1~target2", {}).get("name") == "pearson")
    stats1 = open(os.path.join(env["work"], "stats.json"), "rb").read()

    check("render exit 0", run(["render", "--input", env["input"], "--output", env["output"], "--work", env["work"]]) == 0)
    svg_ok = True
    for f in ("figure_group_box.svg", "figure_group_bar.svg"):
        p = os.path.join(env["output"], f)
        if not os.path.isfile(p):
            svg_ok = False
            break
        try:
            ET.parse(p)
        except Exception:
            svg_ok = False
    check("SVG figures exist and parse", svg_ok)
    check("report.md written", os.path.isfile(os.path.join(env["output"], "report.md")))

    check("finalize exit 0", run(["finalize", "--input", env["input"], "--output", env["output"], "--work", env["work"]]) == 0)
    result = impl.read_json(os.path.join(env["output"], "result.json"))
    check("finalize status ok", result.get("status") == "ok")
    check("finalize backfilled contract fields", isinstance(result.get("analysis_results"), dict) and isinstance(result.get("quality_evidence"), dict) and isinstance(result.get("anomaly_notes"), dict))
    check("finalize has sources with quote", any(s.get("locator") and s.get("quote") for s in result.get("sources", [])))
    check("finalize has change_log", isinstance(result.get("change_log"), list) and len(result.get("change_log", [])) >= 2)

    # determinism: re-run analyze, compare stats bytes
    run(["analyze", "--input", env["input"], "--work", env["work"], "--corr", "target1,target2"])
    stats2 = open(os.path.join(env["work"], "stats.json"), "rb").read()
    check("determinism: stats.json byte-identical on re-run", stats1 == stats2)

    print("== selftest: degradation paths ==")
    # empty input dir with no case.json -> input_error
    env2 = fresh_env("empty")
    run(["scaffold", "--input", env2["input"], "--output", env2["output"], "--work", env2["work"]])
    run(["profile", "--input", env2["input"], "--work", env2["work"]])
    run(["finalize", "--input", env2["input"], "--output", env2["output"], "--work", env2["work"]])
    r2 = impl.read_json(os.path.join(env2["output"], "result.json"))
    check("empty dir (no case.json) -> input_error (exit 0)", r2.get("status") == "input_error")

    # case.json present but no data files -> insufficient_evidence
    env2b = fresh_env("nodata")
    with open(os.path.join(env2b["input"], "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "nodata-001"}, fh)
    run(["scaffold", "--input", env2b["input"], "--output", env2b["output"], "--work", env2b["work"]])
    run(["profile", "--input", env2b["input"], "--work", env2b["work"]])
    run(["finalize", "--input", env2b["input"], "--output", env2b["output"], "--work", env2b["work"]])
    r2b = impl.read_json(os.path.join(env2b["output"], "result.json"))
    check("case present, no data -> insufficient_evidence (exit 0)", r2b.get("status") == "insufficient_evidence")

    # bad case.json
    env3 = fresh_env("badjson")
    with open(os.path.join(env3["input"], "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    run(["scaffold", "--input", env3["input"], "--output", env3["output"], "--work", env3["work"]])
    run(["finalize", "--input", env3["input"], "--output", env3["output"], "--work", env3["work"]])
    r3 = impl.read_json(os.path.join(env3["output"], "result.json"))
    check("bad case.json -> input_error (exit 0)", r3.get("status") == "input_error")

    # CLI help exit 0
    import subprocess
    try:
        p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "main.py"), "--help"],
                           capture_output=True, timeout=30)
        check("CLI --help exit 0", p.returncode == 0)
    except Exception as exc:
        check("CLI --help exit 0", False, "subprocess unavailable: %s" % exc)

    # cleanup
    for e in (env, env2, env2b, env3):
        shutil.rmtree(e["base"], ignore_errors=True)

    print("")
    print("PASS=%d FAIL=%d" % (PASSED, FAILED))
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
