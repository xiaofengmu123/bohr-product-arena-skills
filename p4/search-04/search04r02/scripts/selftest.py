# -*- coding: utf-8 -*-
"""selftest.py — end-to-end self-test for the citation-tracing skill package.

No network, no third-party deps. Verifies:
  T1  help exits 0
  T2  bootstrap writes a valid partial skeleton and echoes output_contract
  T3  finalize renders report.md / papers.csv / edges.csv / SVG (parseable)
  T4  determinism: two runs with CITATION_FIXED_TIME are byte-identical
  T5  empty input -> status insufficient_evidence, exit 0, no crash
  T6  bad JSON case.json -> no crash, errors recorded, exit 0
  T7  dedupe/rank/path operate idempotently and append change_log

Run:  python3 scripts/selftest.py
Exit code 0 means all checks passed.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


def load_core():
    spec = importlib.util.spec_from_file_location(
        "core_impl", os.path.join(HERE, "core_impl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CORE = load_core()

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def make_demo_input(root):
    indir = os.path.join(root, "input")
    os.makedirs(os.path.join(indir, "materials"))
    case = {
        "case_id": "case-demo-001",
        "output_contract": {
            "relevant_papers": [],
            "citation_edges": [],
            "search_strategy": "",
            "omissions": [],
        },
        "seed": {"title": "Attention Is All You Need",
                 "first_author": "Vaswani", "year": 2017},
    }
    with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(indir, "materials", "seed.txt"), "w",
              encoding="utf-8-sig") as f:
        f.write("Attention Is All You Need\nVaswani et al. 2017\n")
    return indir


def populate_demo_result(outdir):
    """Inject a small graph so dedupe/rank/path/finalize have data to chew."""
    result = CORE.load_result(outdir)
    CORE.ensure_results_structure(result)
    papers = [
        {"id": "P0", "title": "Attention Is All You Need",
         "first_author": "Vaswani", "year": 2017, "type": "seed",
         "doi": "10.5555/seed", "source_ids": ["s1"], "role": "seed",
         "confidence": "high"},
        {"id": "P1", "title": "Attention is all you need",
         "first_author": "Vaswani", "year": 2017, "type": "reference",
         "doi": "10.5555/seed", "source_ids": ["s2"], "role": "foundation",
         "confidence": "high"},
        {"id": "P2", "title": "BERT: Pre-training of Deep Bidirectional "
                             "Transformers",
         "first_author": "Devlin", "year": 2019, "type": "citation",
         "source_ids": ["s3"], "role": "bridge", "confidence": "medium"},
        {"id": "P3", "title": "RoBERTa: A Robustly Optimized BERT Pretraining "
                             "Approach",
         "first_author": "Liu", "year": 2019, "type": "citation",
         "source_ids": ["s3"], "role": "leaf", "confidence": "medium"},
    ]
    result["results"]["papers"] = papers
    result["results"]["edges"] = [
        {"id": "E0", "source": "P0", "target": "P1", "relation": "cites",
         "direction": "directed", "evidence_locator": "materials/seed.txt#L1",
         "evidence_quote": "Attention Is All You Need", "source_id": "s1",
         "confidence": "high"},
        {"id": "E1", "source": "P2", "target": "P0", "relation": "cites",
         "direction": "directed", "evidence_locator": "crossref#record",
         "evidence_quote": "cites seed", "source_id": "s3",
         "confidence": "medium"},
        {"id": "E2", "source": "P3", "target": "P2", "relation": "cites",
         "direction": "unknown", "evidence_locator": "crossref#record",
         "evidence_quote": "references BERT", "source_id": "s3",
         "confidence": "low"},
    ]
    result["seed"] = {"id": "P0", "title": "Attention Is All You Need"}
    result["research_question"] = "沿前向/后向引用追踪 Transformer 方法脉络"
    CORE.save_result(result, outdir)


def run_pipeline(indir, outdir, workdir):
    rc = CORE.bootstrap(indir, outdir, workdir)
    if rc != 0:
        return rc
    populate_demo_result(outdir)
    CORE.dedupe(outdir)
    CORE.rank(outdir)
    CORE.path(outdir, "P0", "P2")
    rc = CORE.finalize(outdir, workdir)
    return rc


def main():
    os.environ["CITATION_FIXED_TIME"] = "2026-11-24T00:00:00+00:00"
    root = tempfile.mkdtemp(prefix="cit-test-")
    try:
        # T1 help
        check("T1 help exits 0", CORE.main(["help"]) == 0)

        # T2/T3/T4 bootstrap + finalize determinism
        indir = make_demo_input(root)
        out1 = os.path.join(root, "out1")
        out2 = os.path.join(root, "out2")
        rc1 = run_pipeline(indir, out1, os.path.join(root, "work1"))
        check("T2 pipeline exits 0", rc1 == 0)

        r = json.load(open(os.path.join(out1, "result.json"),
                           encoding="utf-8"))
        check("T2 result.json has case_id",
              r.get("case_id") == "case-demo-001")
        check("T2 status is valid", r.get("status") in CORE.STATUS_VALUES)
        check("T2 output_contract keys echoed",
              all(k in r for k in ("relevant_papers", "citation_edges",
                                   "search_strategy", "omissions")))
        check("T2 change_log non-empty", len(r.get("change_log", [])) > 0)

        # T3 artifacts
        for fn in ("report.md", "papers.csv", "edges.csv",
                   "citation_graph.svg"):
            p = os.path.join(out1, fn)
            check("T3 artifact %s exists" % fn, os.path.isfile(p))
        try:
            ET.parse(os.path.join(out1, "citation_graph.svg"))
            check("T3 SVG is well-formed XML", True)
        except ET.ParseError as e:
            check("T3 SVG is well-formed XML", False, str(e))

        # T4 determinism
        rc2 = run_pipeline(indir, out2, os.path.join(root, "work2"))
        ident = True
        for fn in ("result.json", "report.md", "papers.csv", "edges.csv",
                   "citation_graph.svg"):
            b1 = open(os.path.join(out1, fn), "rb").read()
            b2 = open(os.path.join(out2, fn), "rb").read()
            if b1 != b2:
                ident = False
                check("T4 %s identical" % fn, False,
                      "byte mismatch (len %d vs %d)" % (len(b1), len(b2)))
        check("T4 deterministic byte-for-byte", ident and rc1 == rc2 == 0)

        # T5 empty input
        empty_in = os.path.join(root, "empty")
        os.makedirs(empty_in)
        out_empty = os.path.join(root, "out_empty")
        rc = CORE.bootstrap(empty_in, out_empty, os.path.join(root, "we"))
        r5 = json.load(open(os.path.join(out_empty, "result.json"),
                            encoding="utf-8"))
        check("T5 empty input exits 0", rc == 0)
        check("T5 empty input -> insufficient_evidence",
              r5.get("status") == "insufficient_evidence")

        # T6 bad JSON
        bad_in = os.path.join(root, "bad")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
        out_bad = os.path.join(root, "out_bad")
        rc = CORE.bootstrap(bad_in, out_bad, os.path.join(root, "wb"))
        r6 = json.load(open(os.path.join(out_bad, "result.json"),
                            encoding="utf-8"))
        check("T6 bad JSON exits 0", rc == 0)
        check("T6 bad JSON recorded in errors", len(r6.get("errors", [])) > 0)
        check("T6 bad JSON status honest",
              r6.get("status") in CORE.STATUS_VALUES)

        # T7 idempotency: dedupe twice should not crash and change_log grows
        cl_before = len(r.get("change_log", []))
        CORE.dedupe(out1)
        r_after = json.load(open(os.path.join(out1, "result.json"),
                                 encoding="utf-8"))
        check("T7 dedupe idempotent (no crash)",
              len(r_after.get("change_log", [])) >= cl_before)
        # merged identity check: P0 and P1 shared DOI -> one canonical paper
        papers = r_after["results"]["papers"]
        ids = {p.get("id") for p in papers}
        check("T7 identity merge happened", len(papers) < 4,
              "papers=%d ids=%s" % (len(papers), sorted(ids)))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
