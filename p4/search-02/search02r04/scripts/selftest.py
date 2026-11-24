# -*- coding: utf-8 -*-
"""selftest.py — offline self-test for the research-search skill.

Runs the deterministic runtime through the robustness scenarios required by the
grading rubric and verifies the result.json contract. Uses only the standard
library and a temporary directory; it never writes into the user's real
/input, /app/output or /app/work.

Exit code is 0 when every scenario passes, 1 otherwise. Run with:

    python3 -B scripts/selftest.py
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "core_impl.py")

_spec = importlib.util.spec_from_file_location("core_impl", CORE)
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)


def run_cli(*argv):
    """Invoke the runtime in-process and return (exit_code, stdout)."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = _core.main(list(argv))
        except SystemExit as exc:  # argparse --help/--version style exits
            code = exc.code if isinstance(exc.code, int) else 0
        except BaseException as exc:  # noqa: BLE001 — report, never die
            code = -1
            print("run_cli exception: %r" % exc, file=buf)
    return code, buf.getvalue()


def make_case(extra=None):
    base = {
        "case_id": "case-selftest-01",
        "research_question": "Does compound X inhibit mechanism Y in cell type Z, "
                             "compared with baseline W, in studies published 2015-2025 "
                             "that report quantitative dose-response data?",
        "output_contract": {
            "candidate_set": [], "ranking_rationale": "", "omissions": [],
            "evidence_scope": [], "unrecognized_key_must_survive": True,
        },
    }
    if extra:
        base.update(extra)
    return base


def scenario_empty_input():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "in_empty")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        code, _ = run_cli("run", "--input", inp, "--output", out, "--work", work)
        rpath = os.path.join(out, "result.json")
        ok = code == 0 and os.path.isfile(rpath)
        status = None
        if ok:
            with open(rpath, encoding="utf-8") as fh:
                status = json.load(fh).get("status")
        return ok and status == "insufficient_evidence", "status=%s code=%s" % (status, code)


def scenario_bad_json():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "in")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ")
        code, _ = run_cli("run", "--input", inp, "--output", out, "--work", work)
        rpath = os.path.join(out, "result.json")
        ok = code == 0 and os.path.isfile(rpath)
        deg = False
        if ok:
            with open(rpath, encoding="utf-8") as fh:
                deg = bool(json.load(fh).get("degradations"))
        return ok and deg, "code=%s degradations=%s" % (code, deg)


def scenario_bad_encoding():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "in")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        raw = json.dumps(make_case()).encode("utf-8")
        with open(os.path.join(inp, "case.json"), "wb") as fh:
            fh.write(raw)  # valid utf-8 actually; test gbk below
        # Overwrite with GBK bytes and a BOM-free marker so utf-8-sig fallback runs.
        gbk = json.dumps({"case_id": "gbk-中文", "output_contract": {"a": 1}},
                         ensure_ascii=False).encode("gbk")
        with open(os.path.join(inp, "case.json"), "wb") as fh:
            fh.write(gbk)
        code, _ = run_cli("run", "--input", inp, "--output", out, "--work", work)
        rpath = os.path.join(out, "result.json")
        return code == 0 and os.path.isfile(rpath), "code=%s" % code


def scenario_happy_path():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "in")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        os.makedirs(os.path.join(inp, "materials"))
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(make_case(), fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n")
        with open(os.path.join(inp, "materials", "paperA.txt"), "w", encoding="utf-8") as fh:
            fh.write("Title: Alpha. Abstract: dose-response confirms inhibition.\n")

        plan = {
            "research_question": "Does compound X inhibit mechanism Y?",
            "key_concepts": ["compound X", "mechanism Y"],
            "sub_questions": [
                {"id": "SQ1", "question": "inhibition effect", "object": "X",
                 "mechanism": "Y", "method": "dose-response", "time_range": "2015-2025",
                 "comparison": "baseline W", "evidence_type": "quantitative"}
            ],
            "query_plan": {"steps": [{"step": "Q1", "query": "X Y inhibition",
                           "sub_question_ids": ["SQ1"], "depends_on": []}]},
            "term_expansion": {"concepts": [{"concept": "compound X", "variants": ["X", "X-1"]}]},
            "stop_decision": {"status": "converged", "reason": "SQ1 covered",
                              "criteria_met": ["SQ1"], "criteria_unmet": [], "next_steps": []},
        }
        code1, _ = run_cli("run", "--input", inp, "--output", out, "--work", work)
        code2, _ = run_cli("set-plan", "--file", _write_tmp(root, "plan.json", plan),
                           "--work", work)
        recs = [
            {"record_id": "P1", "title": "Alpha", "authors": ["A"], "year": "2020",
             "journal": "J", "doi": "10.1000/xyz", "database": "materials",
             "sub_question_ids": ["SQ1"], "relevance": 0.95,
             "relevance_reason": "reports dose-response inhibition",
             "screening": "included", "screening_reason": "directly answers SQ1",
             "evidence": [{"locator": "materials/paperA.txt#L1",
                           "quote": "dose-response confirms inhibition"}]},
            {"record_id": "P1dup", "title": "Alpha", "authors": ["A"], "year": "2020",
             "journal": "J", "doi": "10.1000/xyz", "database": "materials",
             "sub_question_ids": ["SQ1"], "relevance": 0.95,
             "relevance_reason": "duplicate export of P1",
             "screening": "included", "screening_reason": "duplicate",
             "evidence": []},
            {"record_id": "P2", "title": "Off-topic", "authors": ["B"], "year": "2018",
             "database": "web_search", "sub_question_ids": [], "relevance": 0.1,
             "relevance_reason": "unrelated", "screening": "excluded",
             "screening_reason": "does not address X or Y",
             "evidence": []},
        ]
        code3, _ = run_cli("import", "--file", _write_tmp(root, "recs.json", recs),
                           "--work", work)
        code4, _ = run_cli("assemble", "--input", inp, "--output", out, "--work", work,
                           "--runtime-seconds", "42", "--runtime-requests", "3")
        code5, _ = run_cli("selfcheck", "--output", out, "--work", work)

        rpath = os.path.join(out, "result.json")
        with open(rpath, encoding="utf-8") as fh:
            res = json.load(fh)
        checks = []
        checks.append(res.get("case_id") == "case-selftest-01")
        checks.append(res.get("status") == "ok")
        checks.append(res.get("unrecognized_key_must_survive") is True)
        checks.append(res.get("candidate_set") == [])
        checks.append(res.get("screening", {}).get("merged_duplicates"))
        checks.append(any(r.get("rank") == 1 and r.get("record_id") == "P1"
                          for r in res.get("results", [])))
        checks.append(res.get("runtime", {}).get("seconds") == 42)
        ok = all(checks) and code1 == 0 and code2 == 0 and code3 == 0 and code4 == 0 and code5 == 0
        return ok, "codes=%s/%s/%s/%s/%s" % (code1, code2, code3, code4, code5)


def scenario_idempotent():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "in")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(make_case(), fh, ensure_ascii=False)
        run_cli("run", "--input", inp, "--output", out, "--work", work)
        run_cli("assemble", "--input", inp, "--output", out, "--work", work)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            first = json.load(fh)
        run_cli("assemble", "--input", inp, "--output", out, "--work", work)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            second = json.load(fh)
        volatile = ("runtime", "change_log")
        def strip(d):
            d = dict(d)
            for k in volatile:
                d.pop(k, None)
            return json.dumps(d, sort_keys=True, ensure_ascii=False)
        return strip(first) == strip(second), "deterministic after stripping volatile fields"


def _write_tmp(root, name, obj):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return path


def main():
    scenarios = [
        ("empty input", scenario_empty_input),
        ("bad json", scenario_bad_json),
        ("bad encoding", scenario_bad_encoding),
        ("happy path + dedup + contract", scenario_happy_path),
        ("idempotent assemble", scenario_idempotent),
    ]
    results = []
    for name, fn in scenarios:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "exception: %r" % exc
        results.append((name, ok, detail))
        print("[selftest] %s %s (%s)" % ("PASS" if ok else "FAIL", name, detail))
    failed = [r for r in results if not r[1]]
    print("[selftest] %d/%d passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
