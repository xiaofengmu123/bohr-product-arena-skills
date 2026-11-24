#!/usr/bin/env python3
"""Self-test: build a synthetic demo input, then bootstrap -> evidence -> verify.

This never writes prepared answers into the submission; it exercises the same
scripts the Harness runs, against a clearly-marked synthetic fixture in a
temporary directory.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)


def run(script, args):
    p = os.path.join(HERE, script)
    if not os.path.exists(p):
        return None
    return subprocess.run([sys.executable, p] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main():
    with tempfile.TemporaryDirectory(prefix="paper-struct-selftest-") as tmp:
        indir = os.path.join(tmp, "input")
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "output")
        os.makedirs(indir)
        os.makedirs(work)
        os.makedirs(out)

        case = {
            "id": "selftest-0001",
            "task": "structural review of a synthetic demo paper",
            "output_contract": {
                "diagnosis": None,
                "edit_plan": None,
                "evidence_map": None,
                "version_boundary": None,
                "artifacts": None,
            },
        }
        with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo structural review task\n")
        with open(os.path.join(indir, "paper.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "# Introduction\n"
                "Our method improves accuracy by 12% on the benchmark.\n\n"
                "# Results\n"
                "The model reaches 0.93 accuracy (n=100).\n\n"
                "# Conclusion\n"
                "Therefore the method causes better outcomes.\n"
            )

        checks = []
        r1 = run("bootstrap.py", ["--case", os.path.join(indir, "case.json"),
                                  "--task", os.path.join(indir, "task.md"),
                                  "--out", out])
        checks.append(("bootstrap", r1.returncode == 0 if r1 else False))

        r2 = run("evidence.py", ["--case", os.path.join(indir, "case.json"),
                                 "--input", indir, "--work", work])
        checks.append(("evidence", r2.returncode == 0 if r2 else False))

        result_path = os.path.join(out, "result.json")
        with open(result_path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        checks.append(("result_has_case_id", bool(result.get("case_id"))))
        checks.append(("result_has_status", bool(result.get("status"))))
        checks.append(("result_has_contract_keys",
                       all(k in result for k in case["output_contract"])))

        # Inject a minimal evidence_map entry so verify can exercise the check.
        result["evidence_map"] = [{
            "claim_id": "c1", "dimension": "conclusion_boundary",
            "claim": "causation stated", "locator": "paper.md#Conclusion:L3",
            "quote": "causes better outcomes", "basis": "correlational data",
            "edit": "narrow to association", "confidence": "high",
            "status": "confirmed", "verdict": "needs_human",
        }]
        result["sources"] = [{"claim_id": "c1", "claim": "causation stated",
                              "source_ref": "paper.md#Conclusion:L3",
                              "confidence": "high"}]
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

        r3 = run("verify.py", ["--case", os.path.join(indir, "case.json"),
                               "--result", result_path, "--out", out])
        checks.append(("verify", r3.returncode == 0 if r3 else False))

        vr_path = os.path.join(out, "verify_report.json")
        verify_ok = False
        if os.path.exists(vr_path):
            with open(vr_path, "r", encoding="utf-8") as fh:
                verify_ok = bool(json.load(fh).get("ok"))
        checks.append(("verify_report_ok", verify_ok))

        all_ok = all(v for _, v in checks)
        print(json.dumps({"ok": all_ok, "checks": checks}, ensure_ascii=False))
        return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
