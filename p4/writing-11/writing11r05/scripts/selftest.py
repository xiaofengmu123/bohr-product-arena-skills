#!/usr/bin/env python3
"""Self-test for the academic-translation Skill package.

Runs the real entry point (scripts/main.py) against synthetic, on-disk cases
and asserts the output contract. Exits non-zero on any failure so CI/Harness
can gate on it. No network, no third-party packages, no fabricated scores.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(args, cwd=None):
    return subprocess.run([PY, "-B", MAIN] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", cwd=cwd)


def _ok(tag, cond, detail=""):
    print(("PASS" if cond else "FAIL") + "  " + tag + (("  " + detail) if detail else ""))
    return bool(cond)


def main():
    all_ok = True
    tmp = tempfile.mkdtemp(prefix="transl-skill-selftest-")

    # --- Case 1: normal flow with materials -------------------------------
    inp = os.path.join(tmp, "case1", "input")
    out = os.path.join(tmp, "case1", "output")
    work = os.path.join(tmp, "case1", "work")
    os.makedirs(inp); os.makedirs(out); os.makedirs(work)

    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({
            "case_id": "synth-001",
            "output_contract": {"deliverables": [], "fact_source_map": [],
                                "version_boundary": {}},
            "task": "translate with term/number lock",
        }, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# translate\n")
    src_text = ("The binding affinity was 3.5 million nM and rose to 4,500,000 nM "
                "under pH 7.4 (Smith et al., 2020) [1,3].")
    with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as f:
        f.write(src_text)
    with open(os.path.join(work, "source_text.txt"), "w", encoding="utf-8") as f:
        f.write(src_text)

    base = ["--input", inp, "--output", out, "--work", work]
    r = run(["brief"] + base)
    all_ok &= _ok("brief exit 0", r.returncode == 0, r.stdout.strip()[:60])
    all_ok &= _ok("brief case_id", '"synth-001"' in r.stdout, "")

    r = run(["init"] + base)
    all_ok &= _ok("init exit 0", r.returncode == 0, r.stdout.strip()[:60])
    rj = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    all_ok &= _ok("skeleton case_id", rj.get("case_id") == "synth-001")
    all_ok &= _ok("skeleton has contract", all(k in rj for k in
        ("deliverables", "fact_source_map", "version_boundary",
         "sources", "uncertainty", "pending_human", "change_log")))

    translation = "结合亲和力为 3.5 million nM，在 pH 7.4 下升至 4,500,000 nM（Smith et al., 2020）[1,3]。"
    with open(os.path.join(out, "translation.md"), "w", encoding="utf-8") as f:
        f.write(translation)

    r = run(["add", "--list", "deliverables",
             "--item", json.dumps({"path": "translation.md", "format": "markdown",
                                   "title": "translation", "description": "zh"})] + base)
    all_ok &= _ok("add-deliverable exit 0", r.returncode == 0, r.stdout.strip()[:60])

    r = run(["add-fact", "--item",
             json.dumps({"item": "binding affinity", "kind": "number",
                         "value": "3.5 million", "source": "paper.txt", "quote": "3.5 million nM"})] + base)
    all_ok &= _ok("add-fact exit 0", r.returncode == 0)

    r = run(["add-modification", "--item",
             json.dumps({"what": "lock term", "why": "glossary", "before_after": {"before": "a", "after": "b"}})] + base)
    all_ok &= _ok("add-modification exit 0", r.returncode == 0)

    r = run(["add-pending", "--item",
             json.dumps({"item": "pH unit", "reason": "verify"})] + base)
    all_ok &= _ok("add-pending exit 0", r.returncode == 0)

    r = run(["check"] + base)
    report = json.load(open(os.path.join(work, "check_report.json"), encoding="utf-8"))
    all_ok &= _ok("check report exists", report.get("contract_ok") is True)
    all_ok &= _ok("check no critical number error", report.get("n_errors", 0) == 0,
                  "errors=%d" % report.get("n_errors", 0))

    r = run(["finalize", "--status", "success"] + base)
    all_ok &= _ok("finalize exit 0", r.returncode == 0, r.stdout.strip()[:80])
    rj = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    all_ok &= _ok("final status success", rj.get("status") == "success")
    all_ok &= _ok("change_log grows", len(rj.get("change_log", [])) >= 5)

    # --- Case 2: empty input must still yield a valid partial result -------
    inp2 = os.path.join(tmp, "case2", "input")
    out2 = os.path.join(tmp, "case2", "output")
    work2 = os.path.join(tmp, "case2", "work")
    os.makedirs(inp2); os.makedirs(out2); os.makedirs(work2)
    r = run(["init", "--input", inp2, "--output", out2, "--work", work2])
    all_ok &= _ok("empty-init exit 0", r.returncode == 0)
    r = run(["finalize", "--status", "insufficient_evidence",
             "--input", inp2, "--output", out2, "--work", work2])
    all_ok &= _ok("empty-finalize exit 0", r.returncode == 0, r.stdout.strip()[:80])
    rj = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
    all_ok &= _ok("empty status honest", rj.get("status") == "insufficient_evidence")

    # --- Case 3: bad JSON must degrade, not crash --------------------------
    inp3 = os.path.join(tmp, "case3", "input")
    out3 = os.path.join(tmp, "case3", "output")
    work3 = os.path.join(tmp, "case3", "work")
    os.makedirs(inp3); os.makedirs(out3); os.makedirs(work3)
    with open(os.path.join(inp3, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json ")
    r = run(["brief", "--input", inp3, "--output", out3, "--work", work3])
    all_ok &= _ok("bad-json brief exit 0", r.returncode == 0)

    print("RESULT: " + ("ALL PASS" if all_ok else "SOME FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
