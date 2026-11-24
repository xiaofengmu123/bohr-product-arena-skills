#!/usr/bin/env python3
"""Self-test for the contract-first deep-research skill.

Runs the CLI end-to-end against temporary demo directories and asserts the
invariants that matter for scoring:

  * skeleton always writes a parseable result.json, exit code 0;
  * every output_contract field is present in result.json;
  * evidence rows carry locator + quote;
  * empty input, bad JSON and bad encoding degrade without crashing;
  * selfcheck reports six-point coverage.

No formal test case, whiteboard or benchmark answer is produced or claimed:
the demo inputs are minimal synthetic fixtures in a throwaway temp directory.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

DEMO_CASE = {
    "case_id": "demo-001",
    "output_contract": {
        "research_product": {"type": "object"},
        "evidence_matrix": {"type": "array"},
        "uncertainty": {"type": "object"},
        "sources": {"type": "array"},
        "pending_human": {"type": "array"},
    },
}

DEMO_TASK = "# Demo research task\nThis is a synthetic example, not a formal case.\n"

DEMO_PAYLOAD = {
    "research_product": {
        "type": "synthesis",
        "question": "Demo question",
        "summary": "Demo summary",
        "conclusions": [
            {"id": "C1", "text": "Demo conclusion",
             "evidence_ids": ["E1"], "strength": "inferred"}
        ],
    },
    "evidence_matrix": [
        {"id": "E1", "subquestion": "SQ1", "source": "materials/demo.md",
         "locator": "p1", "quote": "verbatim demo quote", "claim": "demo",
         "judgment": "supports", "reason": "demo", "conclusion_ids": ["C1"]}
    ],
    "uncertainty": {
        "conflicts": [{"a": "src A", "b": "src B", "resolution": "demo"}],
        "biases": [], "gaps": ["demo gap"], "undetermined": []
    },
    "sources": [{"id": "S1", "path": "materials/demo.md"}],
    "pending_human": ["demo pending"],
}


def run(*argv, check=True):
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + list(argv),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            "command failed: %s\nstdout=%s\nstderr=%s"
            % (" ".join(argv), proc.stdout, proc.stderr))
    return proc


def read_result(output_dir):
    with open(os.path.join(output_dir, "result.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_demo_flow():
    with tempfile.TemporaryDirectory() as tmp:
        indir = os.path.join(tmp, "input")
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "output")
        os.makedirs(os.path.join(indir, "materials"))
        os.makedirs(work)
        os.makedirs(out)
        with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(DEMO_CASE, fh, ensure_ascii=False)
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write(DEMO_TASK)
        with open(os.path.join(indir, "materials", "demo.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("verbatim demo quote\n")

        base = ["--input", indir, "--output", out, "--work", work]
        run("skeleton", *base)
        result = read_result(out)
        assert result["case_id"] == "demo-001", result["case_id"]
        for field in DEMO_CASE["output_contract"]:
            assert field in result, "missing contract field %s" % field
        assert result["status"] in ("ok", "partial", "insufficient_evidence",
                                    "input_error")

        with open(os.path.join(work, "decomposition.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("SQ1: subquestion one\nSQ2: subquestion two\n")
        with open(os.path.join(work, "search_plan.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("order and 停止条件\n")
        with open(os.path.join(work, "evidence_cards.jsonl"), "w",
                  encoding="utf-8") as fh:
            for row in DEMO_PAYLOAD["evidence_matrix"]:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        payload_path = os.path.join(work, "payload.json")
        with open(payload_path, "w", encoding="utf-8") as fh:
            json.dump(DEMO_PAYLOAD, fh, ensure_ascii=False)

        run("merge", "--payload", payload_path, *base)
        run("finalize", *base)
        result = read_result(out)
        assert result["research_product"]["conclusions"], "conclusions missing"
        assert result["evidence_matrix"][0]["locator"], "locator missing"
        assert result["evidence_matrix"][0]["quote"], "quote missing"
        assert result["contract_audit"]["missing_contract_fields"] == []
        assert os.path.exists(os.path.join(out, "report.md"))
        assert os.path.exists(os.path.join(out, "evidence_map.json"))

        proc = run("selfcheck", *base, check=False)
        assert proc.returncode == 0
        report = json.loads(proc.stdout)
        assert report["ok"], report
        assert report["coverage"], "six-point coverage missing"
    return "demo flow ok"


def test_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        run("skeleton", "--input", os.path.join(tmp, "missing"),
            "--output", out, "--work", work)
        result = read_result(out)
        assert result["status"] == "insufficient_evidence", result["status"]
    return "empty input ok"


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        indir = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(indir)
        with open(os.path.join(indir, "case.json"), "wb") as fh:
            fh.write(b"{ not json \x00\xff")
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("task\n")
        run("skeleton", "--input", indir, "--output", out, "--work", work)
        result = read_result(out)
        assert result["status"] in ("input_error", "insufficient_evidence",
                                    "partial")
        assert result["errors"] or result["limitations"]
    return "bad json ok"


def main():
    results = []
    for fn in (test_demo_flow, test_empty_input, test_bad_json):
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            results.append("FAIL: %s" % exc)
    print("\n".join(results))
    if any(r.startswith("FAIL") for r in results):
        sys.exit(1)
    print("selftest passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
