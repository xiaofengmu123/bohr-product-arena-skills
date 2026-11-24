"""selftest.py — deterministic self-check for the skill (Python stdlib only).

Runs the pipeline against synthetic inputs in a temp directory and verifies:
  * result.json parses and carries case_id + all output_contract keys
  * status is honest for empty / bad-input scenarios
  * report.md and figure SVGs exist and are well-formed XML
  * stats.json is byte-identical across two runs (determinism)

Exit code 0 = all checks passed; 1 = at least one check failed.
"""

import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core  # noqa: E402

PASS = 0
FAIL = 0
MESSAGES = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        MESSAGES.append(f"  PASS  {name}")
    else:
        FAIL += 1
        MESSAGES.append(f"  FAIL  {name}  {detail}")


def make_input(root, with_data=True, bad_case_json=False, omit_case=False):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    if not omit_case:
        case = {"case_id": "synth-001",
                "output_contract": {"analysis_results": {}, "quality_evidence": {},
                                    "anomaly_notes": {}, "custom_key": {}}}
        if bad_case_json:
            with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
                f.write("{ this is not json ")
        else:
            with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
                json.dump(case, f)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# synthetic self-test task\nCompare groups and correlations.\n")
    if with_data:
        rows = ["group,a,b"]
        import random
        rnd = random.Random(42)
        for i in range(60):
            g = i % 3
            rows.append(f"g{g},{10 + g * 5 + rnd.gauss(0, 1):.4f},{2 * (10 + g * 5) + rnd.gauss(0, 1):.4f}")
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
    return inp, out, work


def run_one(root, **kw):
    inp, out, work = make_input(root, **kw)
    ctx = core.run_pipeline(inp, out, work)
    return inp, out, work, ctx


def main():
    print("=== research-data-pipeline selftest ===\n")
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")

    # 1. normal case
    inp, out, work, ctx = run_one(os.path.join(tmp, "normal"))
    rpath = os.path.join(out, "result.json")
    check("result.json exists", os.path.isfile(rpath))
    r = json.loads(core.read_text_robust(rpath)[0]) if os.path.isfile(rpath) else {}
    check("result.json has case_id", r.get("case_id") == "synth-001")
    check("status is ok", r.get("status") == "ok", f"got {r.get('status')}")
    keys = r.keys()
    check("contract keys present",
          all(k in keys for k in ("analysis_results", "quality_evidence",
                                   "anomaly_notes", "custom_key")))
    check("meta fields present",
          all(k in keys for k in ("sources", "uncertainty", "change_log", "status")))
    check("report.md exists", os.path.isfile(os.path.join(out, "report.md")))
    svg_ok = True
    for f in ("figure_group_box.svg", "figure_corr_scatter.svg"):
        fp = os.path.join(out, f)
        if not os.path.isfile(fp):
            svg_ok = False
            continue
        try:
            ET.parse(fp)
        except Exception:
            svg_ok = False
    check("figures exist and parse as XML", svg_ok)
    stats1 = core.read_text_robust(os.path.join(work, "stats.json"))[0]
    check("group tests computed", "group_comparison" in json.loads(stats1)
          and len(json.loads(stats1)["group_comparison"]) > 0)

    # determinism: run twice on the SAME input dir, compare stats.json bytes
    inp2, _, _ = make_input(os.path.join(tmp, "normal2"))
    work_a = os.path.join(tmp, "det_a_work")
    work_b = os.path.join(tmp, "det_b_work")
    core.run_pipeline(inp2, os.path.join(tmp, "det_a_out"), work_a)
    core.run_pipeline(inp2, os.path.join(tmp, "det_b_out"), work_b)
    stats_a = core.read_text_robust(os.path.join(work_a, "stats.json"))[0]
    stats_b = core.read_text_robust(os.path.join(work_b, "stats.json"))[0]
    check("stats.json deterministic across runs", stats_a == stats_b)

    # 2. empty data dir
    inp, out, work, ctx = run_one(os.path.join(tmp, "empty"), with_data=False)
    r = json.loads(core.read_text_robust(os.path.join(out, "result.json"))[0])
    check("empty data -> insufficient_evidence", r.get("status") == "insufficient_evidence",
          f"got {r.get('status')}")

    # 3. bad case.json
    inp, out, work, ctx = run_one(os.path.join(tmp, "badcase"), bad_case_json=True)
    r = json.loads(core.read_text_robust(os.path.join(out, "result.json"))[0])
    check("bad case.json -> input_error", r.get("status") == "input_error",
          f"got {r.get('status')}")

    # 4. missing case.json
    inp, out, work, ctx = run_one(os.path.join(tmp, "nocase"), omit_case=True)
    r = json.loads(core.read_text_robust(os.path.join(out, "result.json"))[0])
    check("missing case.json -> input_error", r.get("status") == "input_error",
          f"got {r.get('status')}")

    print("\n".join(MESSAGES))
    print(f"\n{FAIL} failed, {PASS} passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
