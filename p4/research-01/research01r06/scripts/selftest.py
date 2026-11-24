#!/usr/bin/env python3
"""Self-test for the deep-research skill package (standard library only).

Runs the actual CLI (scripts/main.py) end-to-end over four synthetic scenarios
in temporary directories, then asserts the robustness contract:

  S1 normal input        -> partial skeleton, plan/figure/evidence/merge/finalize
  S2 empty input dir     -> result.json with status=insufficient_evidence, exit 0
  S3 bad JSON case       -> result.json with status=input_error, exit 0
  S4 bad-encoding file   -> decoded with fallback, degradation recorded, exit 0

Exits 0 when every assertion passes, 1 otherwise. No prepared case answers are
produced: all fixtures are clearly synthetic and marked as examples.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(cwd, args):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env)
    return proc


def run_ok(cwd, args):
    proc = run(cwd, args)
    if proc.returncode != 0:
        raise AssertionError("command failed rc=%d args=%r\nstdout=%s\nstderr=%s"
                             % (proc.returncode, args, proc.stdout, proc.stderr))
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        raise AssertionError("non-JSON stdout for %r: %s" % (args, proc.stdout))


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)


CASE = {"case_id": "selftest-001", "armiId": "ARMI:selftest",
        "task": "example", "output_contract": {"answer": None, "fields": []}}


def scenario_normal():
    d = tempfile.mkdtemp(prefix="srt-normal-")
    inp, out, wrk = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), json.dumps(CASE, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# Example research task\n\nCompare drug A vs B.\n")
    write(os.path.join(inp, "materials", "p1.md"), "# Paper 1\n\n## Results\nA reduces X by 20%.\n")
    s = run_ok(d, ["skeleton", "--input", inp, "--output", out, "--work", wrk])
    assert s["wrote"], "skeleton did not write"
    plan = {"schema_version": "1.0", "research_question": "Compare A vs B on X",
            "task_type": "synthesis",
            "subquestions": [
                {"id": "SQ1", "text": "efficacy of A", "facets": {}, "depends_on": [],
                 "search": {"query": "drug A X", "sources": ["materials"], "stop_condition": "saturation"}},
                {"id": "SQ2", "text": "efficacy of B", "facets": {}, "depends_on": ["SQ1"],
                 "search": {"query": "drug B X", "sources": ["materials"], "stop_condition": "saturation"}},
            ],
            "search_order": ["SQ1", "SQ2"], "parallel_groups": [],
            "stop_conditions": {"global": "two-sided coverage", "per_sq": ">=1 source"}}
    write(os.path.join(wrk, "plan.json"), json.dumps(plan, ensure_ascii=False))
    r = run_ok(d, ["plan", "--input", inp, "--output", out, "--work", wrk])
    assert r["ok"] and r["subquestions"] == 2, r
    card = {"id": "E1", "subquestion": "SQ1", "source": "materials/p1.md",
            "locator": "Results", "quote": "A reduces X by 20%.",
            "claim": "A reduces X", "judgment": "supports", "reason": "verbatim",
            "conclusion_ids": ["C1"], "figure_ids": []}
    write(os.path.join(wrk, "card.json"), json.dumps(card, ensure_ascii=False))
    r = run_ok(d, ["evidence", "--card", os.path.join(wrk, "card.json"),
                   "--input", inp, "--output", out, "--work", wrk])
    assert r["appended"] == 1 and r["ok"], r
    figure = {"schema_version": "1.0", "figures": [
        {"id": "F1", "source": "materials/p1.md", "locator": "Results",
         "kind": "table", "caption": "outcomes", "claims": [{"text": "A reduces X", "evidence_id": "E1"}]}]}
    write(os.path.join(wrk, "figure_model.json"), json.dumps(figure, ensure_ascii=False))
    r = run_ok(d, ["figure", "--input", inp, "--output", out, "--work", wrk])
    assert r["figures"] == 1, r
    payload = {"status": "partial",
               "research_product": {"type": "synthesis", "question": "Compare A vs B on X",
                                    "summary": "A reduces X; B not assessed.",
                                    "conclusions": [{"id": "C1", "text": "A reduces X",
                                                     "evidence_ids": ["E1"], "strength": "inferred"}]},
               "uncertainty": {"conflicts": [], "biases": [], "gaps": ["no data on B"],
                               "undetermined": []},
               "sources": [{"id": "S1", "path": "materials/p1.md"}],
               "pending_human": []}
    write(os.path.join(wrk, "payload.json"), json.dumps(payload, ensure_ascii=False))
    run_ok(d, ["merge", "--payload", os.path.join(wrk, "payload.json"),
               "--input", inp, "--output", out, "--work", wrk])
    f = run_ok(d, ["finalize", "--input", inp, "--output", out, "--work", wrk])
    assert f["status"] == "partial", f
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert result["case_id"] == "selftest-001", result["case_id"]
    assert result["evidence_matrix"] and result["evidence_matrix"][0]["quote"] == "A reduces X by 20%."
    assert result["answer"] is None and result["fields"] == [], "output_contract not back-filled"
    assert os.path.exists(os.path.join(out, "report.md"))
    assert os.path.exists(os.path.join(out, "evidence_map.json"))
    sc = run_ok(d, ["selfcheck", "--input", inp, "--output", out, "--work", wrk])
    assert sc["valid"] is True, sc
    shutil.rmtree(d, ignore_errors=True)
    return "S1 ok"


def scenario_empty():
    d = tempfile.mkdtemp(prefix="srt-empty-")
    inp, out, wrk = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
    os.makedirs(inp)
    s = run_ok(d, ["skeleton", "--input", inp, "--output", out, "--work", wrk])
    assert s["wrote"], s
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert result["status"] == "insufficient_evidence", result["status"]
    assert os.path.exists(os.path.join(out, "result.json"))
    shutil.rmtree(d, ignore_errors=True)
    return "S2 ok"


def scenario_bad_json():
    d = tempfile.mkdtemp(prefix="srt-badjson-")
    inp, out, wrk = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{not valid json")
    s = run_ok(d, ["skeleton", "--input", inp, "--output", out, "--work", wrk])
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert result["status"] == "input_error", result["status"]
    assert result["errors"], "bad JSON should be recorded in errors"
    shutil.rmtree(d, ignore_errors=True)
    return "S3 ok"


def scenario_bad_encoding():
    d = tempfile.mkdtemp(prefix="srt-enc-")
    inp, out, wrk = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), json.dumps(CASE, ensure_ascii=False))
    # GB18030-encoded material
    write(os.path.join(inp, "materials", "g.md"), "# 结果\n甲优于乙。\n", encoding="gb18030")
    s = run_ok(d, ["skeleton", "--input", inp, "--output", out, "--work", wrk])
    assert s["wrote"], s
    # Reading the material through discover+read path records the fallback encoding.
    from importlib import util as _util
    spec = _util.spec_from_file_location("resultlib", os.path.join(HERE, "resultlib.py"))
    lib = _util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    _text, _errs, degs = lib.read_text(os.path.join(inp, "materials", "g.md"))
    assert any("gb18030" in x for x in degs), degs
    shutil.rmtree(d, ignore_errors=True)
    return "S4 ok"


def main():
    results = []
    for fn in (scenario_normal, scenario_empty, scenario_bad_json, scenario_bad_encoding):
        try:
            results.append((fn.__name__, fn(), True))
        except Exception as exc:  # noqa: BLE001 - report all failures
            results.append((fn.__name__, repr(exc), False))
    ok = all(r[2] for r in results)
    for name, msg, passed in results:
        print("[%s] %s: %s" % ("PASS" if passed else "FAIL", name, msg))
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
