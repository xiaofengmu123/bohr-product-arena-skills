#!/usr/bin/env python3
"""Deterministic self-test for the fact-traced academic writing Skill.

Runs init_result.py and evidence_map.py against synthetic inputs and verifies:
  1. result.json schema (case_id, contract fields, status/sources/uncertainty/pending_human/change_log)
  2. determinism: same input twice -> byte-identical result.json / material_index.json
  3. degradation: empty input dir and bad JSON case.json still produce valid output, exit 0
  4. evidence verification: dangling source/citation detection works

Usage:
    python3 scripts/selftest.py [--input-dir /input --output-dir /app/output --work-dir /app/work]

The optional real paths are checked non-destructively (read-only); the deterministic
re-run and degradation tests always run in a fresh temporary directory.

Exit code 0 = all checks passed; non-zero = at least one check failed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
INIT = os.path.join(HERE, "init_result.py")
EVID = os.path.join(HERE, "evidence_map.py")

SAMPLE_CASE = {
    "id": "case-test-0001",
    "output_contract": {
        "deliverable": {"description": "editable manuscript"},
        "fact_source_map": {"description": "fact to source mapping"},
        "version_limits": {"description": "version and limitations"},
    },
    "task": "rewrite the methods section",
}

SAMPLE_MANUSCRIPT = """# Methods

## Subjects

The study enrolled n = 45 participants [S:m1#L12-L15]. Mean age was 38.4 years
[S:m1#L13]. The protocol followed Smith et al. [C:smith2020].

# Results

## Outcomes

The primary outcome improved by 12.3% [S:m1#L20]. No difference in the control
group [S:m1#L21].

# References

- [C:smith2020]: Smith J., Doe A. A method study. Journal of Methods 2020;12(3):45-56. DOI: unknown
"""


def run_py(script, args):
    return subprocess.run([sys.executable, script] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, name, cond, detail=""):
        if cond:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append((name, detail))

    def summary(self):
        return {"passed": self.passed, "failed": self.failed,
                "failures": self.failures}


def check_schema(r, contract_fields):
    ok = (
        isinstance(r, dict)
        and bool(r.get("case_id"))
        and "status" in r
        and "sources" in r
        and "uncertainty" in r
        and "pending_human" in r
        and "change_log" in r
        and all(k in r for k in contract_fields)
    )
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", "--input", default="/input")
    ap.add_argument("--output-dir", "--output", "--out", default="/app/output")
    ap.add_argument("--work-dir", "--work", default="/app/work")
    a = ap.parse_args(argv)

    res = Result()
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        # ---- 1. normal synthetic case ----
        case_dir = os.path.join(tmp, "normal")
        os.makedirs(os.path.join(case_dir, "materials"))
        write_text(os.path.join(case_dir, "case.json"),
                   json.dumps(SAMPLE_CASE, ensure_ascii=False))
        write_text(os.path.join(case_dir, "task.md"), "# task\n\ndo the writing task\n")
        write_text(os.path.join(case_dir, "materials", "notes.md"),
                   "L12-L15: n = 45 participants, mean age 38.4 years.\n"
                   "L20: primary outcome +12.3%.\nL21: control group no difference.\n")
        out1 = os.path.join(tmp, "out1")
        work1 = os.path.join(tmp, "work1")
        r = run_py(INIT, ["--input-dir", case_dir, "--output-dir", out1,
                          "--work-dir", work1])
        res.check("init_result exit 0 (normal)", r.returncode == 0, r.stderr.strip())

        result1 = read_json(os.path.join(out1, "result.json")) if os.path.exists(
            os.path.join(out1, "result.json")) else None
        res.check("result.json exists (normal)", result1 is not None)
        res.check("result.json schema (normal)",
                  result1 is not None and check_schema(result1,
                                                       ["deliverable", "fact_source_map", "version_limits"]),
                  "missing keys" if result1 else "no file")
        res.check("material index found", os.path.exists(os.path.join(work1, "material_index.json")))
        res.check("material count == 1", result1 is not None and
                  read_json(os.path.join(work1, "material_index.json")).get("materials") and
                  len(read_json(os.path.join(work1, "material_index.json"))["materials"]) == 1)

        # ---- 2. determinism: run again, byte-compare ----
        out2 = os.path.join(tmp, "out2")
        work2 = os.path.join(tmp, "work2")
        r2 = run_py(INIT, ["--input-dir", case_dir, "--output-dir", out2,
                           "--work-dir", work2])
        res.check("init_result exit 0 (re-run)", r2.returncode == 0)
        same_result = read_bytes(os.path.join(out1, "result.json")) == read_bytes(os.path.join(out2, "result.json"))
        same_index = read_bytes(os.path.join(work1, "material_index.json")) == read_bytes(os.path.join(work2, "material_index.json"))
        res.check("determinism: result.json byte-identical", same_result)
        res.check("determinism: material_index.json byte-identical", same_index)

        # ---- 3. evidence_map on synthetic draft ----
        draft_path = os.path.join(out1, "manuscript.md")
        write_text(draft_path, SAMPLE_MANUSCRIPT)
        re_ = run_py(EVID, ["--draft", draft_path,
                            "--index", os.path.join(work1, "material_index.json"),
                            "--work-dir", work1])
        res.check("evidence_map exit 0", re_.returncode == 0, re_.stderr.strip())
        em = read_json(os.path.join(work1, "evidence_map.json")) if os.path.exists(
            os.path.join(work1, "evidence_map.json")) else None
        res.check("evidence_map.json exists", em is not None)
        res.check("evidence_map: valid draft has no dangling markers",
                  em is not None and em.get("dangling_sources") == []
                  and em.get("dangling_citations") == [],
                  "dangling_sources=%r dangling_citations=%r" % (
                      em.get("dangling_sources") if em else None,
                      em.get("dangling_citations") if em else None))

        # dangling source and dangling citation must be detected
        bad_draft = os.path.join(out1, "manuscript_bad.md")
        write_text(bad_draft, "# Methods\n\nSubjects [S:zzz#p1] and [C:notthere].\n")
        rb2 = run_py(EVID, ["--draft", bad_draft,
                            "--index", os.path.join(work1, "material_index.json"),
                            "--work-dir", work1])
        emb = read_json(os.path.join(work1, "evidence_map.json")) if os.path.exists(
            os.path.join(work1, "evidence_map.json")) else None
        res.check("evidence_map detects dangling source", emb is not None
                  and "zzz#p1" in emb.get("dangling_sources", []))
        res.check("evidence_map detects dangling citation", emb is not None
                  and "notthere" in emb.get("dangling_citations", []))

        # ---- 4. empty input dir degradation ----
        empty_dir = os.path.join(tmp, "empty")
        os.makedirs(empty_dir)
        out_e = os.path.join(tmp, "out_empty")
        work_e = os.path.join(tmp, "work_empty")
        re_ = run_py(INIT, ["--input-dir", empty_dir, "--output-dir", out_e,
                            "--work-dir", work_e])
        res.check("empty input: exit 0", re_.returncode == 0, re_.stderr.strip())
        re_json = read_json(os.path.join(out_e, "result.json")) if os.path.exists(
            os.path.join(out_e, "result.json")) else None
        res.check("empty input: valid result.json with degradations",
                  re_json is not None and re_json.get("degradations"))

        # ---- 5. bad JSON degradation ----
        bad_dir = os.path.join(tmp, "badjson")
        os.makedirs(bad_dir)
        write_text(os.path.join(bad_dir, "case.json"), "{ this is not valid json")
        write_text(os.path.join(bad_dir, "task.md"), "# task\n")
        out_b = os.path.join(tmp, "out_bad")
        work_b = os.path.join(tmp, "work_bad")
        rb = run_py(INIT, ["--input-dir", bad_dir, "--output-dir", out_b,
                           "--work-dir", work_b])
        res.check("bad JSON: exit 0", rb.returncode == 0, rb.stderr.strip())
        b_json = read_json(os.path.join(out_b, "result.json")) if os.path.exists(
            os.path.join(out_b, "result.json")) else None
        res.check("bad JSON: valid result.json with degradation noted",
                  b_json is not None and any("case.json" in d for d in b_json.get("degradations", [])))

        # ---- 6. real input read-only check (informational) ----
        if os.path.isdir(a.input_dir) and os.path.exists(os.path.join(a.input_dir, "case.json")):
            res.check("real /input/case.json readable",
                      read_json(os.path.join(a.input_dir, "case.json")) is not None or
                      True)  # read-only presence check; JSON may be arbitrary
        else:
            # Not a failure: the selftest is runnable before the harness mounts /input.
            pass

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    summary = res.summary()
    print(json.dumps({"selftest": "ok" if res.failed == 0 else "failed",
                      **summary}, ensure_ascii=False, sort_keys=True))
    return 0 if res.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
