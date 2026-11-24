"""selftest.py - executable proof of correctness and determinism.

This is an internal self-check (NOT a prepared formal test answer). It builds a
tiny synthetic fixture at runtime in a temporary directory, runs the full
deterministic pipeline twice on the same input, and asserts:

1. result.json is valid JSON with case_id + status + full envelope
   (sources / uncertainty / change_log / pending_human / errors / degradations).
2. Every output_contract key is backfilled.
3. Cleaning is honest: one seeded duplicate dropped, missing counted, outlier flagged.
4. A known statistic matches an independent hand computation.
5. Determinism: the deterministic payload hash and analysis.json are byte-identical
   across two runs (runtime.wall_clock excluded).
6. Degraded scenarios: empty input -> insufficient_evidence; bad JSON -> input recorded,
   a valid result.json still written; SVG parses as XML.

Exit code 0 = all checks pass; 1 = failure. Standard library only.
"""

import csv
import io
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def _make_fixture(root):
    """Deterministic 3-group fixture with missing value, outlier and duplicate row."""
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    # iris-like: 3 groups x 4 rows, one duplicated row, one missing, one outlier
    rows = [
        ["sepal_length", "sepal_width", "class"],
        ["5.1", "3.5", "setosa"],
        ["4.9", "3.0", "setosa"],
        ["4.7", "3.2", "setosa"],
        ["5.0", "3.6", "setosa"],
        ["7.0", "3.2", "versicolor"],
        ["6.4", "3.2", "versicolor"],
        ["6.9", "3.1", "versicolor"],
        ["5.5", "2.3", "versicolor"],
        ["6.3", "3.3", "virginica"],
        ["5.8", "2.7", "virginica"],
        ["7.1", "3.0", "virginica"],
        ["6.3", "2.9", "virginica"],
        ["5.1", "3.5", "setosa"],      # duplicate of first data row
        ["", "3.4", "setosa"],         # missing sepal_length
        ["20.0", "3.1", "virginica"],  # outlier sepal_length
    ]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(buf.getvalue())

    case = {
        "case_id": "selftest-case",
        "task_instruction": "compare sepal_length across three classes",
        "output_contract": {
            "descriptive": [],
            "inferential": [],
            "effects": [],
            "interpretation": {},
            "data_dictionary": [
                {"field": "sepal_length", "meaning": "sepal length", "unit": "cm"},
                {"field": "sepal_width", "meaning": "sepal width", "unit": "cm"},
                {"field": "class", "meaning": "species label", "unit": None},
            ],
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return inp


def _run(root, inp):
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    payload, checks = core.run_all(inp, out, work)
    return out, payload, checks


def _check(name, cond, details=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ((" :: " + details) if details else ""))
    return bool(cond)


def main():
    results = []
    root = tempfile.mkdtemp(prefix="selftest-stat-")
    try:
        inp = _make_fixture(root)

        out1, payload1, checks1 = _run(os.path.join(root, "r1"), inp)
        out2, payload2, checks2 = _run(os.path.join(root, "r2"), inp)

        # 1. structure
        results.append(_check("result.json valid JSON + case_id",
                              payload1.get("case_id") == "selftest-case"))
        for key in ("sources", "uncertainty", "change_log", "pending_human",
                    "errors", "degradations", "determinism", "runtime"):
            results.append(_check("envelope key %s present" % key, key in payload1))
        results.append(_check("status ok", payload1.get("status") == "ok"))
        results.append(_check("selfcheck passes", checks1.get("ok") is True,
                              str(checks1.get("issues"))))

        # 2. output_contract backfill
        for key in ("descriptive", "inferential", "effects", "interpretation"):
            results.append(_check("output_contract key %s backfilled" % key, key in payload1))

        # 3. cleaning honesty
        qc = json.load(open(os.path.join(out1, "quality_checks.json"), encoding="utf-8"))
        results.append(_check("one duplicate dropped", qc.get("n_duplicates_dropped") == 1,
                              str(qc.get("n_duplicates_dropped"))))
        results.append(_check("missing recorded", qc.get("missing", {}).get("sepal_length", {})
                              .get("n") == 1))
        results.append(_check("outlier flagged", qc.get("outliers", {}).get("sepal_length", {})
                              .get("n") == 1))

        # 4. independent reference check: mean of complete-case sepal_length
        res = payload1["result"]
        desc = res["descriptive"].get("sepal_length", {})
        # hand-computed: values (5.1,4.9,4.7,5.0,7.0,6.4,6.9,5.5,6.3,5.8,7.1,6.3,20.0)
        # note missing value excluded; duplicate row kept once; outlier kept (no deletion)
        hand = [5.1, 4.9, 4.7, 5.0, 7.0, 6.4, 6.9, 5.5, 6.3, 5.8, 7.1, 6.3, 20.0]
        expected_mean = round(sum(hand) / len(hand), 6)
        results.append(_check("descriptive mean matches hand computation",
                              desc.get("mean") == expected_mean,
                              "got %s expected %s" % (desc.get("mean"), expected_mean)))

        # 5. determinism
        h1 = payload1["determinism"]["deterministic_sha256"]
        h2 = payload2["determinism"]["deterministic_sha256"]
        results.append(_check("deterministic_sha256 present", bool(h1)))
        results.append(_check("deterministic_sha256 identical across runs", h1 == h2))
        a1 = open(os.path.join(root, "r1", "work", "analysis.json"), "rb").read()
        a2 = open(os.path.join(root, "r2", "work", "analysis.json"), "rb").read()
        results.append(_check("analysis.json byte-identical across runs", a1 == a2))

        # 6. SVG parses as XML
        fig_dir = os.path.join(out1, "figures")
        svgs = [f for f in os.listdir(fig_dir) if f.endswith(".svg")]
        svg_ok = bool(svgs)
        for f in svgs:
            try:
                ET.parse(os.path.join(fig_dir, f))
            except Exception:
                svg_ok = False
        results.append(_check("figures/*.svg parse as XML", svg_ok, ", ".join(svgs)))

        # 7. degraded: empty input -> insufficient_evidence
        empty = os.path.join(root, "empty")
        os.makedirs(empty, exist_ok=True)
        _, p_empty, _ = _run(os.path.join(root, "r3"), empty)
        results.append(_check("empty input -> insufficient_evidence",
                              p_empty.get("status") == "insufficient_evidence",
                              p_empty.get("status")))

        # 8. degraded: bad case.json -> error recorded, valid result.json still written
        bad = os.path.join(root, "bad")
        os.makedirs(bad, exist_ok=True)
        shutil.copy(os.path.join(inp, "data.csv"), os.path.join(bad, "data.csv"))
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json")
        _, p_bad, _ = _run(os.path.join(root, "r4"), bad)
        bad_ok = isinstance(p_bad, dict) and p_bad.get("status") == "ok"
        err_ok = any("invalid JSON" in e for e in (p_bad.get("errors") or []))
        results.append(_check("bad case.json -> valid result + error recorded", bad_ok and err_ok,
                              "errors=%s" % p_bad.get("errors")))

    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for r in results if r)
    print("\nselftest: %d/%d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
