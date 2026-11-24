#!/usr/bin/env python3
"""selftest.py — end-to-end self-test for the skill package (stdlib only).

Checks:
  1. stats library inline self-tests pass;
  2. init -> inspect -> analyze -> build -> selfcheck runs on a synthetic case
     containing missing values, an outlier, and a duplicate row;
  3. result.json is valid and backfills every output_contract key;
  4. two runs produce byte-identical numeric results (timestamps ignored);
  5. degradation paths: empty input dir and malformed case.json do not crash.

Usage: python3 scripts/selftest.py   (exit code 0 on pass)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import stats as S  # noqa: E402
import analyze as A  # noqa: E402


class NS:
    pass


def args(inp, out, work):
    a = NS()
    a.input, a.output, a.work = inp, out, work
    return a


def strip_time(obj):
    if isinstance(obj, dict):
        return {k: ("<T>" if k == "time" else strip_time(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_time(v) for v in obj]
    return obj


def normalize_paths(obj, base, base2):
    if isinstance(obj, dict):
        return {k: normalize_paths(v, base, base2) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_paths(v, base, base2) for v in obj]
    if isinstance(obj, str):
        return obj.replace(base, "<BASE>").replace(base2, "<BASE>")
    return obj


def write_synthetic(root):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    os.makedirs(os.path.join(root, "output"), exist_ok=True)
    csv = (
        "group,value\n"
        "A,1.0\n"
        "A,1.2\n"
        "A,1.1\n"
        "A,1.1\n"          # exact duplicate row
        "A,\n"             # missing value
        "A,9.9\n"          # outlier
        "B,2.0\n"
        "B,2.1\n"
        "B,2.2\n"
        "B,2.1\n"
        "B,2.3\n"
    )
    with open(os.path.join(root, "input", "data.csv"), "w", encoding="utf-8") as fh:
        fh.write(csv)
    case = {"case_id": "selftest-case",
            "task_instruction": "compare value between groups",
            "output_contract": {"descriptive": [], "inferential": [],
                                "effects": [], "interpretation": {}}}
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest task\n")
    with open(os.path.join(root, "work", "plan.json"), "w", encoding="utf-8") as fh:
        json.dump({"group_col": "group", "value_col": "value",
                   "design": "two_groups", "value_unit": "arbitrary"}, fh)


def run_pipeline(root):
    a = args(os.path.join(root, "input"), os.path.join(root, "output"),
             os.path.join(root, "work"))
    A.cmd_init(a)
    A.cmd_inspect(a)
    A.cmd_analyze(a)
    A.cmd_build(a)
    return A.cmd_selfcheck(a)


def main():
    ok = True

    def check(name, cond):
        nonlocal ok
        if not cond:
            ok = False
            print("FAIL", name)
        else:
            print("ok  ", name)

    check("stats selftest", S._selftest() == 0)

    base = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        write_synthetic(base)
        rc = run_pipeline(base)
        check("pipeline exit 0", rc == 0)

        with open(os.path.join(base, "output", "result.json"), encoding="utf-8") as fh:
            r = json.load(fh)
        check("case_id", r.get("case_id") == "selftest-case")
        check("status ok", r.get("status") == "ok")
        for k in ("descriptive", "inferential", "effects", "interpretation"):
            check(f"contract key {k} present", k in r)
        check("envelope keys", all(k in r for k in
                                   ("sources", "uncertainty", "change_log")))
        check("change_log has cleaning", any(c["action"] == "drop_missing"
                                             for c in r["change_log"]) and
              any(c["action"] == "drop_duplicates" for c in r["change_log"]))
        check("cleaned CSV exists",
              os.path.exists(r["artifacts"]["cleaned"][0]))
        check("SVG exists and non-empty",
              r["artifacts"]["figures"] and
              os.path.getsize(r["artifacts"]["figures"][0]) > 0)

        # determinism: re-run into a fresh copy, compare numeric payload
        base2 = tempfile.mkdtemp(prefix="skill-selftest2-")
        shutil.copytree(os.path.join(base, "input"), os.path.join(base2, "input"))
        os.makedirs(os.path.join(base2, "work"), exist_ok=True)
        shutil.copy(os.path.join(base, "work", "plan.json"),
                    os.path.join(base2, "work", "plan.json"))
        run_pipeline(base2)
        with open(os.path.join(base2, "output", "result.json"), encoding="utf-8") as fh:
            r2 = json.load(fh)
        check("deterministic (timestamps/paths ignored)",
              strip_time(normalize_paths(r, base, base2)) ==
              strip_time(normalize_paths(r2, base, base2)))

        # degradation: empty input directory
        empty = tempfile.mkdtemp(prefix="skill-empty-")
        for d in ("input", "work", "output"):
            os.makedirs(os.path.join(empty, d), exist_ok=True)
        a = args(os.path.join(empty, "input"), os.path.join(empty, "output"),
                 os.path.join(empty, "work"))
        A.cmd_init(a)
        A.cmd_inspect(a)
        A.cmd_analyze(a)
        A.cmd_build(a)
        with open(os.path.join(empty, "output", "result.json"), encoding="utf-8") as fh:
            re_ = json.load(fh)
        check("empty-input status is partial/insufficient",
              re_.get("status") in ("partial", "insufficient_evidence"))

        # degradation: malformed case.json
        bad = tempfile.mkdtemp(prefix="skill-bad-")
        for d in ("input", "work", "output"):
            os.makedirs(os.path.join(bad, d), exist_ok=True)
        with open(os.path.join(bad, "input", "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        a = args(os.path.join(bad, "input"), os.path.join(bad, "output"),
                 os.path.join(bad, "work"))
        A.cmd_init(a)  # must not raise
        check("malformed case.json tolerated", True)
    finally:
        shutil.rmtree(base, ignore_errors=True)
        if 'base2' in dir():
            shutil.rmtree(base2, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)
        shutil.rmtree(bad, ignore_errors=True)

    print("selftest", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
