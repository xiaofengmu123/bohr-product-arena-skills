"""selftest.py -- in-process self-test for the audit pipeline (stdlib only).

Builds a tiny synthetic fixture in a temp directory at runtime (nothing is
shipped as a pre-made answer or formal test product), then asserts the
pipeline's hard invariants:

  * --help exits 0
  * discover extracts text and records degradations for bad JSON
  * init drops a legal partial skeleton immediately
  * add/set/meta events round-trip through compile
  * output_contract is echoed verbatim, including unknown keys
  * verbatim quotes pass the machine check; non-verbatim quotes are flagged
  * compile is deterministic (same journal -> byte-identical result.json)
  * verify reports the flagged quote
  * an empty input degrades to insufficient_evidence instead of fabricating

Run:  python -B scripts/selftest.py
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = C.main(argv)
    return code, out.getvalue(), err.getvalue()


def main():
    fails = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(detail)))
        if not cond:
            fails.append(name)

    code, _, _ = run_main(["--help"])
    check("--help exit 0", code == 0, code)
    code, _, _ = run_main(["-h"])
    check("-h exit 0", code == 0, code)
    code, _, err = run_main(["add", "--work", "/tmp/x", "--finding", "{bad"])
    check("bad --finding exits 2", code == 2, code)

    root = tempfile.mkdtemp(prefix="selftest_audit_")
    try:
        inp = os.path.join(root, "input")
        work = os.path.join(root, "work")
        outd = os.path.join(root, "output")
        os.makedirs(os.path.join(inp, "materials"))
        os.makedirs(work, exist_ok=True)
        os.makedirs(outd, exist_ok=True)

        sample = ("Alpha and beta isoforms show distinct kinetics.\n"
                  "The half-life was 12.4 hours in cohort A (n=40).")
        with open(os.path.join(inp, "materials", "sample.txt"), "w", encoding="utf-8") as fh:
            fh.write(sample)
        with open(os.path.join(inp, "materials", "bad.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        case = {
            "case_id": "case-demo-001",
            "task": "audit demo (synthetic self-test fixture, not a formal case)",
            "output_contract": {
                "deliverable": "result.json",
                "labels": ["citation", "fact"],
                "unknown_key": {"x": 1},
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# audit demo\n")

        code, _, err = run_main(["discover", "--input", inp, "--work", work, "--output", outd])
        check("discover exit 0", code == 0, err)
        man = C.load_json(os.path.join(work, "manifest.json"))
        check("manifest extracted sample", any("sample.txt" in t for t in man.get("texts", [])),
              man.get("texts"))
        check("bad JSON recorded as error", any("bad.json" in e for e in man.get("errors", [])),
              man.get("errors"))

        code, _, err = run_main(["init", "--input", inp, "--work", work, "--output", outd])
        check("init exit 0", code == 0, err)
        r = C.load_json(os.path.join(outd, "result.json"))
        check("skeleton status partial", r.get("status") == "partial", r.get("status"))
        check("skeleton findings empty", r.get("review_report", {}).get("findings") == [])

        good = {
            "id": "F001", "category": "citation_support", "finding_type": "unsupported_claim",
            "severity": "major", "verdict": "unsupported",
            "locator": "materials/sample.txt, para 2",
            "quote": "The half-life was 12.4 hours in cohort A (n=40).",
            "claim": "The paper asserts a 12.4 h half-life without citing support.",
            "basis": "no citation provided in the excerpt", "source_ref": "not_verified",
            "suggestion": "add the primary citation", "confidence": "high", "review_status": "reviewed",
        }
        bad = dict(good)
        bad.update({"id": "F002", "quote": "The half-life was 99.9 hours.",
                    "locator": "materials/sample.txt, para 2"})

        code, _, err = run_main(["add", "--work", work, "--finding", json.dumps(good, ensure_ascii=False)])
        check("add good exit 0", code == 0, err)
        code, _, err = run_main(["add", "--work", work, "--finding", json.dumps(bad, ensure_ascii=False)])
        check("add bad exit 0", code == 0, err)
        run_main(["set", "--work", work, "--key", "status", "--value", "ok"])
        run_main(["set", "--work", work, "--key", "verdict", "--value", "issues_found"])
        run_main(["meta", "--work", work, "--key", "sources", "--items",
                  json.dumps([{"ref": "materials/sample.txt", "status": "verified"}])])
        run_main(["meta", "--work", work, "--key", "uncertainty", "--items",
                  json.dumps([{"item": "F002 quote", "note": "flagged by machine check"}])])

        code, _, err = run_main(["compile", "--input", inp, "--work", work, "--output", outd])
        check("compile exit 0", code == 0, err)
        r = C.load_json(os.path.join(outd, "result.json"))
        check("case_id echoed", r.get("case_id") == "case-demo-001", r.get("case_id"))
        check("status ok", r.get("status") == "ok", r.get("status"))
        check("verdict issues_found", r.get("summary", {}).get("verdict") == "issues_found")
        check("output_contract echoed", r.get("output_contract", {}).get("deliverable") == "result.json")
        check("unknown contract key preserved",
              r.get("output_contract", {}).get("unknown_key", {}).get("x") == 1)
        check("contract echoed at top level too", r.get("labels") == ["citation", "fact"])
        check("counts total 2", r.get("summary", {}).get("counts", {}).get("total") == 2)
        check("change_log non-empty", bool(r.get("change_log")), r.get("change_log"))
        check("sources merged", len(r.get("sources", [])) == 1, r.get("sources"))
        check("uncertainty merged", len(r.get("uncertainty", [])) == 1, r.get("uncertainty"))
        fmap = {f["id"]: f for f in r.get("review_report", {}).get("findings", [])}
        check("good quote verbatim", fmap.get("F001", {}).get("quote_verified") is True,
              fmap.get("F001", {}).get("quote_verified"))
        check("bad quote flagged", fmap.get("F002", {}).get("quote_verified") is False)
        check("evidence map has both", len(r.get("evidence", {}).get("evidence_map", {})) == 2)
        check("report.md exists", os.path.isfile(os.path.join(outd, "report.md")))

        code, _, err = run_main(["compile", "--input", inp, "--work", work, "--output", outd])
        r2 = C.load_json(os.path.join(outd, "result.json"))
        check("compile deterministic", r == r2)

        code, _, err = run_main(["verify", "--work", work, "--output", outd])
        check("verify exit 0", code == 0, err)
        vrep = C.load_json(os.path.join(work, "verify_report.json"))
        flagged = [i for i in vrep.get("issues", []) if i.get("finding_id") == "F002"
                   and i.get("issue") == "quote_not_verbatim"]
        check("verify flags non-verbatim quote", len(flagged) > 0, vrep.get("issues"))

        empty = os.path.join(root, "empty")
        os.makedirs(empty)
        code, _, err = run_main(["run", "--input", empty,
                                 "--work", os.path.join(root, "ew"),
                                 "--output", os.path.join(root, "eo")])
        check("empty run exit 0", code == 0, err)
        er = C.load_json(os.path.join(root, "eo", "result.json"))
        check("empty -> insufficient_evidence", er.get("status") == "insufficient_evidence",
              er.get("status"))
        check("empty verdict insufficient", er.get("summary", {}).get("verdict") == "insufficient")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("---")
    if fails:
        print("SELFTEST FAILED (%d): %s" % (len(fails), ", ".join(fails)))
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
