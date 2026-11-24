#!/usr/bin/env python3
"""Self-test for evidence-traced-talk-organizer.

Builds a synthetic minimal case in a temporary directory, runs scaffold ->
update -> evidence-check -> validate, and asserts the pipeline behaves.
This is a smoke test only: it uses clearly labeled synthetic data and never
substitutes for a real case result.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
    )


def main():
    tmp = tempfile.mkdtemp(prefix="evidence_talk_selftest_")
    inp = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(inp)
    os.makedirs(work)
    os.makedirs(out)

    case = {
        "id": "synthetic-case-001",
        "armiId": "ARMI:TEST",
        "output_contract": {"report_goal": None, "audience": None, "narrative": None,
                            "pages": [], "editable_deck": None, "evidence_map": [],
                            "qa_prep": [], "rendering": None},
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# synthetic task\n")
    paper = "We compared Alpha and Baseline. Alpha improved accuracy by 12% on the held-out set (Table 1).\n"
    with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write(paper)

    # 1. scaffold
    r = run(["scaffold", "--case", os.path.join(inp, "case.json"),
             "--task", os.path.join(inp, "task.md"), "--out", out])
    assert r.returncode == 0, "scaffold failed:\n" + r.stdout + r.stderr
    result_path = os.path.join(out, "result.json")
    assert os.path.exists(result_path), "skeleton not written"
    with open(result_path, encoding="utf-8") as fh:
        skel = json.load(fh)
    assert skel["case_id"] == "synthetic-case-001"
    assert skel["status"] == "partial"
    assert "report_goal" in skel and "evidence_map" in skel

    # 2. update with a complete minimal result
    patch = {
        "report_goal": "synthetic goal",
        "audience": {"prior": "expert", "concern": "method"},
        "narrative": "problem-method-result",
        "pages": [{"page_index": 0, "title": "t", "single_claim": "Alpha improves accuracy by 12%",
                   "evidence_strength": "high", "figures": ["Table 1"],
                   "speaker_notes": "note", "source_refs": ["paper.txt"]}],
        "evidence_map": [{
            "page_index": 0,
            "claim": "Alpha improves accuracy by 12%",
            "evidence": [{
                "fragment": "improved accuracy by 12%",
                "source_ref": "paper.txt",
                "locator": {"type": "page", "page": 1, "section": "Results", "figure_or_table": "Table 1"},
                "quote": "Alpha improved accuracy by 12% on the held-out set (Table 1).",
                "quote_offset": paper.find("Alpha improved accuracy by 12%"),
                "strength": "high",
                "status": "verified",
            }],
        }],
        "qa_prep": [{"question": "control?", "answer": "baseline", "page": 0}],
        "primary_artifact": "deck.html",
        "editable_deck": {"path": "deck.html", "format": "html", "template": "none"},
        "rendering": {"source": "deck.html", "preview": "deck.pdf"},
        "status": "complete",
    }
    patch_file = os.path.join(work, "patch.json")
    with open(patch_file, "w", encoding="utf-8") as fh:
        json.dump(patch, fh, ensure_ascii=False)
    r = run(["update", "--result", result_path, "--patch-file", patch_file])
    assert r.returncode == 0, "update failed:\n" + r.stdout + r.stderr

    # create the referenced artifacts so validate can find them
    with open(os.path.join(out, "deck.html"), "w", encoding="utf-8") as fh:
        fh.write("<html></html>\n")
    with open(os.path.join(out, "deck.pdf"), "w", encoding="utf-8") as fh:
        fh.write("%PDF-1.4 synthetic\n")

    # 3. evidence-check (quote found in sources.json)
    sources = {"sources": [{"path": "paper.txt", "text": paper}]}
    with open(os.path.join(work, "sources.json"), "w", encoding="utf-8") as fh:
        json.dump(sources, fh, ensure_ascii=False)
    r = run(["evidence-check", "--result", result_path,
             "--sources", os.path.join(work, "sources.json")])
    assert r.returncode == 0, "evidence-check failed:\n" + r.stdout + r.stderr

    # 4. validate
    r = run(["validate", "--result", result_path, "--out", out])
    assert r.returncode == 0, "validate failed:\n" + r.stdout + r.stderr

    print("selftest ok: scaffold -> update -> evidence-check -> validate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
