#!/usr/bin/env python3
"""selftest.py — end-to-end smoke test for the skill scripts.

Creates synthetic inputs under a temp dir, runs scripts/main.py through the
real CLI, and asserts the produced result.json is legal, its quotes verify
against the synthetic materials, and empty-input / bad-JSON paths degrade
gracefully. Standard library only. No formal test cases, no benchmark scores.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

MATERIAL = (
    "# Example material\n"
    "\n"
    "## Section 2.1\n"
    "\n"
    "The trial reported a 12% relative reduction in adverse events "
    "(95% CI 5–19).\n"
)

CASE = {
    "case_id": "demo-001",
    "task": "assess the intervention",
    "output_contract": {
        "research_product": {
            "type": "synthesis",
            "question": None,
            "summary": None,
            "conclusions": [],
        },
        "evidence_matrix": [],
        "uncertainty": {"conflicts": [], "gaps": []},
        "sources": [],
    },
}

PAYLOAD = {
    "status": "ok",
    "research_product": {
        "type": "synthesis",
        "question": "Does the intervention reduce adverse events?",
        "summary": "One synthetic trial reports a 12% relative reduction.",
        "conclusions": [{
            "id": "C1",
            "text": "The trial reported a 12% relative reduction in adverse events.",
            "evidence_ids": ["E1"],
            "strength": "established",
        }],
    },
    "evidence_matrix": [{
        "id": "E1",
        "subquestion": "SQ1",
        "source": "materials/a.md",
        "locator": {"unit_type": "section", "unit_id": "2.1"},
        "quote": "The trial reported a 12% relative reduction in adverse events (95% CI 5–19).",
        "claim": "The intervention reduces adverse events by 12%.",
        "judgment": "supports",
        "reason": "verbatim in material",
        "conclusion_ids": ["C1"],
    }],
    "uncertainty": {
        "conflicts": [{
            "source_a": "materials/a.md", "source_b": "materials/b.md",
            "issue": "direction of effect", "resolution": "both recorded",
        }],
        "gaps": ["no long-term follow-up data"],
    },
    "sources": [{"path": "materials/a.md", "role": "trial report"}],
    "pending_human": ["confirm confidence interval boundaries"],
}


def run(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, env=env,
    )
    proc.stdout_s = proc.stdout.decode("utf-8", "replace")
    proc.stderr_s = proc.stderr.decode("utf-8", "replace")
    return proc


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    root = tempfile.mkdtemp(prefix="skill-selftest-")
    failures = []
    try:
        # --- happy path -----------------------------------------------------
        inp = os.path.join(root, "in")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        write(os.path.join(inp, "case.json"), json.dumps(CASE))
        write(os.path.join(inp, "task.md"), "# demo task\n")
        write(os.path.join(inp, "materials", "a.md"), MATERIAL)

        common = ["--input", inp, "--output", out, "--work", work]
        r = run(["skeleton"] + common)
        assert r.returncode == 0, "skeleton exited %d: %s" % (r.returncode, r.stderr_s)
        res_path = os.path.join(out, "result.json")
        with open(res_path, encoding="utf-8") as fh:
            skel = json.load(fh)
        assert skel.get("status") == "partial", skel.get("status")
        assert skel.get("case_id") == "demo-001"
        assert "research_product" in skel and "uncertainty" in skel

        write(os.path.join(work, "decomposition.md"),
              "- SQ1 intervention effect\n- SQ2 safety profile\n")
        write(os.path.join(work, "search_plan.md"),
              "order: SQ1 then SQ2; sources: materials; stop: coverage>=1 each\n")
        write(os.path.join(work, "payload.json"), json.dumps(PAYLOAD))

        r = run(["merge", "--payload", os.path.join(work, "payload.json")] + common)
        assert r.returncode == 0, "merge exited %d" % r.returncode
        r = run(["finalize"] + common)
        assert r.returncode == 0, "finalize exited %d" % r.returncode

        with open(res_path, encoding="utf-8") as fh:
            final = json.load(fh)
        assert final.get("status") == "ok"
        assert final["research_product"]["conclusions"][0]["evidence_ids"] == ["E1"]
        emap_path = os.path.join(out, "evidence_map.json")
        with open(emap_path, encoding="utf-8") as fh:
            emap = json.load(fh)
        assert emap["quote_integrity"]["verified"] == 1, emap["quote_integrity"]
        assert emap["conclusion_index"][0]["evidence"][0]["verified"] is True

        r = run(["selfcheck"] + common)
        assert r.returncode == 0, "selfcheck exited %d" % r.returncode
        stdout = r.stdout_s
        assert "结论: PASS" in stdout, stdout
        print("[PASS] happy path: skeleton -> merge -> finalize -> selfcheck")

        # --- empty input ----------------------------------------------------
        inp2 = os.path.join(root, "empty-in")
        out2 = os.path.join(root, "empty-out")
        work2 = os.path.join(root, "empty-work")
        os.makedirs(inp2, exist_ok=True)
        r = run(["skeleton", "--input", inp2, "--output", out2, "--work", work2])
        assert r.returncode == 0, "empty skeleton exited %d" % r.returncode
        with open(os.path.join(out2, "result.json"), encoding="utf-8") as fh:
            empty = json.load(fh)
        assert empty.get("status") == "insufficient_evidence", empty.get("status")
        print("[PASS] empty input degrades to insufficient_evidence, exit 0")

        # --- bad JSON -------------------------------------------------------
        inp3 = os.path.join(root, "bad-in")
        out3 = os.path.join(root, "bad-out")
        work3 = os.path.join(root, "bad-work")
        write(os.path.join(inp3, "case.json"), "{not json")
        r = run(["skeleton", "--input", inp3, "--output", out3, "--work", work3])
        assert r.returncode == 0, "bad-JSON skeleton exited %d" % r.returncode
        with open(os.path.join(out3, "result.json"), encoding="utf-8") as fh:
            bad = json.load(fh)
        assert bad.get("status") == "input_error", bad.get("status")
        assert bad.get("errors"), "expected errors recorded"
        print("[PASS] bad JSON degrades to input_error, exit 0")
    except AssertionError as exc:
        failures.append(str(exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("[FAIL]")
        for f in failures:
            print("  -", f)
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
