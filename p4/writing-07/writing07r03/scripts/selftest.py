#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the sci-intro-writing Skill.

Pure standard library. Exercises the deterministic runner (main.py) against
synthetic cases and asserts the robustness + determinism guarantees promised in
SKILL.md. Produces a deterministic text report; exit code 0 iff all pass.

Covered guarantees
------------------
- empty input dir    -> exit 0, status insufficient_evidence, valid JSON
- bad JSON / encoding-> no crash, errors/degradations recorded, valid JSON
- contract backfill  -> all output_contract fields appear in result.json
- determinism        -> identical input run twice yields byte-identical outputs
- roundtrip          -> every produced JSON parses
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

REPORT = []


def log(name, ok, detail=""):
    REPORT.append((name, bool(ok), detail))
    tag = "PASS" if ok else "FAIL"
    print("%-4s %-34s %s" % (tag, name, detail))


def run_main(args):
    """Run main.py as a subprocess; return (exit_code, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=60,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), \
        proc.stderr.decode("utf-8", "replace")


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def make_empty_input(tmp):
    d = os.path.join(tmp, "empty_in")
    os.makedirs(d)
    return d


def make_bad_json_input(tmp):
    d = os.path.join(tmp, "bad_in")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json \x00\xff")
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 题目：引言与相关工作写作\n按材料写作。")
    return d


def make_normal_input(tmp):
    d = os.path.join(tmp, "normal_in")
    os.makedirs(os.path.join(d, "materials"))
    case = {
        "case_id": "selftest-case-001",
        "task": "依据给定材料撰写论文引言与相关工作，四段齐全。",
        "output_contract": {
            "introduction": "string",
            "related_work": "array",
            "research_gap": "string",
            "contributions": "array",
        },
    }
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\n背景/相关工作/研究空白/贡献四段，保留来源。")
    with open(os.path.join(d, "materials", "paper.md"), "w", encoding="utf-8") as fh:
        fh.write("# 底稿\n本文提出一种新方法。相关文献[1]使用路线A，存在局限。")
    return d


def main():
    print("=== selftest: sci-intro-writing ===")
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        # ---- 1. empty input ------------------------------------------------ #
        inp = make_empty_input(tmp)
        work = os.path.join(tmp, "w_empty")
        out = os.path.join(tmp, "o_empty")
        code, _, err = run_main(["scan", "--input", inp, "--work", work])
        log("empty_scan_exit0", code == 0, "exit=%d %s" % (code, err.strip()))
        code, _, err = run_main(["skeleton", "--work", work, "--output", out])
        log("empty_skeleton_exit0", code == 0, "exit=%d %s" % (code, err.strip()))
        code, _, _ = run_main(["validate", "--work", work, "--output", out])
        result = read_json(os.path.join(out, "result.json"))
        log("empty_valid_json", isinstance(result, dict))
        log("empty_status_insufficient",
            result.get("status") == "insufficient_evidence",
            "status=%s" % result.get("status"))

        # ---- 2. bad JSON --------------------------------------------------- #
        inp = make_bad_json_input(tmp)
        work = os.path.join(tmp, "w_bad")
        out = os.path.join(tmp, "o_bad")
        code, _, _ = run_main(["run", "--input", inp, "--work", work, "--output", out])
        manifest = read_json(os.path.join(work, "input_manifest.json"))
        result = read_json(os.path.join(out, "result.json"))
        log("badjson_no_crash", isinstance(result, dict))
        log("badjson_error_recorded",
            bool(manifest.get("errors")) or bool(result.get("errors")),
            "errors=%d" % len(result.get("errors", [])))

        # ---- 3. normal contract backfill ----------------------------------- #
        inp = make_normal_input(tmp)
        work = os.path.join(tmp, "w_normal")
        out = os.path.join(tmp, "o_normal")
        code, _, _ = run_main(["run", "--input", inp, "--work", work, "--output", out])
        manifest = read_json(os.path.join(work, "input_manifest.json"))
        result = read_json(os.path.join(out, "result.json"))
        fields = manifest.get("contract_fields", [])
        missing = [f for f in fields if f not in result]
        log("contract_fields_extracted", fields == [
            "contributions", "introduction", "related_work", "research_gap"],
            "fields=%s" % fields)
        log("contract_fields_backfilled", missing == [], "missing=%s" % missing)
        log("case_id_propagated", result.get("case_id") == "selftest-case-001",
            "case_id=%s" % result.get("case_id"))

        # ---- 4. determinism (byte-identical) ------------------------------- #
        work2 = os.path.join(tmp, "w_normal2")
        out2 = os.path.join(tmp, "o_normal2")
        run_main(["run", "--input", inp, "--work", work2, "--output", out2])
        same_manifest = sha256(os.path.join(work, "input_manifest.json")) == \
            sha256(os.path.join(work2, "input_manifest.json"))
        same_result = sha256(os.path.join(out, "result.json")) == \
            sha256(os.path.join(out2, "result.json"))
        log("determinism_manifest", same_manifest)
        log("determinism_result", same_result)

        # ---- 5. roundtrip ---------------------------------------------------- #
        roundtrip_ok = True
        for path in (os.path.join(out, "result.json"),
                     os.path.join(work, "input_manifest.json"),
                     os.path.join(work, "case_normalized.json")):
            try:
                read_json(path)
            except (json.JSONDecodeError, OSError):
                roundtrip_ok = False
        log("roundtrip_all_json", roundtrip_ok)

        # ---- 6. finalize keeps validity ------------------------------------- #
        run_main(["finalize", "--work", work, "--output", out])
        result2 = read_json(os.path.join(out, "result.json"))
        log("finalize_valid", isinstance(result2, dict))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [r for r in REPORT if not r[1]]
    print("=== %d checks, %d failed ===" % (len(REPORT), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
