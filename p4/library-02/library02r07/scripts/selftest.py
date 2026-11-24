#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - robustness self-test for the document-library repair Skill.

Runs the pipeline against synthetic inputs covering the failure modes listed in
SKILL.md and the scoring rubric.  Standard library only; no test fixtures are
shipped with the Skill (they are generated in a temp directory here).

Usage:
    python3 -B scripts/selftest.py          # run all checks, exit non-zero on fail
"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def run(args):
    """Run core_impl.main with an argv list; returns exit code."""
    old = sys.argv
    sys.argv = ["core_impl.py"] + args
    try:
        code = core_impl.main()
    finally:
        sys.argv = old
    return code


def read_result(out):
    with io.open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def write(path, content, encoding="utf-8"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with io.open(path, "w", encoding=encoding) as fh:
        fh.write(content)


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # ---------------------------------------------------------------
        # 1. empty input directory
        # ---------------------------------------------------------------
        d = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(d, "input"), exist_ok=True)
        os.makedirs(os.path.join(d, "out"), exist_ok=True)
        write(os.path.join(d, "input", "case.json"), '{"case_id": "c-empty"}')
        code = run(["--input", os.path.join(d, "input"),
                    "--output", os.path.join(d, "out"),
                    "--work", os.path.join(d, "work")])
        r = read_result(os.path.join(d, "out"))
        check("empty input exits 0", code == 0, "code=%s" % code)
        check("empty input status insufficient_evidence",
              r.get("status") == "insufficient_evidence", r.get("status"))
        check("empty input case_id kept", r.get("case_id") == "c-empty")

        # ---------------------------------------------------------------
        # 2. bad JSON in case.json -> input_error, no crash
        # ---------------------------------------------------------------
        d2 = os.path.join(tmp, "badcase")
        os.makedirs(os.path.join(d2, "input"), exist_ok=True)
        os.makedirs(os.path.join(d2, "out"), exist_ok=True)
        write(os.path.join(d2, "input", "case.json"), "{not valid json")
        write(os.path.join(d2, "input", "a.txt"), "hello")
        code = run(["--input", os.path.join(d2, "input"),
                    "--output", os.path.join(d2, "out"),
                    "--work", os.path.join(d2, "work")])
        r = read_result(os.path.join(d2, "out"))
        check("bad case.json exits 0", code == 0, "code=%s" % code)
        check("bad case.json status input_error",
              r.get("status") == "input_error", r.get("status"))
        check("bad case.json recorded in errors",
              any("case_json" in e for e in (r.get("errors") or [])),
              json.dumps(r.get("errors")))

        # ---------------------------------------------------------------
        # 3. bad encoding file -> degraded, not crash; other files survive
        # ---------------------------------------------------------------
        d3 = os.path.join(tmp, "badenc")
        os.makedirs(os.path.join(d3, "input"), exist_ok=True)
        os.makedirs(os.path.join(d3, "out"), exist_ok=True)
        write(os.path.join(d3, "input", "case.json"),
              '{"case_id": "c-enc", "output_contract": {"answer": "string"}}')
        write(os.path.join(d3, "input", "good.txt"), "line one\nline two\n")
        with io.open(os.path.join(d3, "input", "bad.bin"), "wb") as fh:
            fh.write(b"\xff\xfe\x00\x9c\xff\xff")
        code = run(["--input", os.path.join(d3, "input"),
                    "--output", os.path.join(d3, "out"),
                    "--work", os.path.join(d3, "work")])
        r = read_result(os.path.join(d3, "out"))
        check("bad encoding exits 0", code == 0, "code=%s" % code)
        check("contract echoed", r.get("answer") == "string",
              json.dumps({k: r.get(k) for k in ("answer", "status")}))
        files = {f["path"] for f in r["computed"]["files"]}
        check("good file indexed", "good.txt" in files, files)

        # ---------------------------------------------------------------
        # 4. oversized file -> skipped content hash, recorded
        # ---------------------------------------------------------------
        d4 = os.path.join(tmp, "oversize")
        os.makedirs(os.path.join(d4, "input"), exist_ok=True)
        os.makedirs(os.path.join(d4, "out"), exist_ok=True)
        write(os.path.join(d4, "input", "case.json"), '{"case_id": "c-big"}')
        with io.open(os.path.join(d4, "input", "big.bin"), "wb") as fh:
            fh.seek(core_impl.HASH_LIMIT + 1)
            fh.write(b"\x00")
        code = run(["--input", os.path.join(d4, "input"),
                    "--output", os.path.join(d4, "out"),
                    "--work", os.path.join(d4, "work")])
        r = read_result(os.path.join(d4, "out"))
        check("oversized exits 0", code == 0, "code=%s" % code)
        big = [f for f in r["computed"]["files"] if f["path"] == "big.bin"]
        check("oversized recorded too_large",
              bool(big) and big[0].get("hash_status") == "too_large",
              json.dumps(big))

        # ---------------------------------------------------------------
        # 5. dedup + version + alias (symlink) end-to-end
        # ---------------------------------------------------------------
        d5 = os.path.join(tmp, "full")
        os.makedirs(os.path.join(d5, "input", "proj"), exist_ok=True)
        os.makedirs(os.path.join(d5, "out"), exist_ok=True)
        write(os.path.join(d5, "input", "case.json"),
              '{"case_id": "c-full", "questions": ["how many files?", "search: buffer"]}')
        write(os.path.join(d5, "input", "proj", "protocol_v1.txt"),
              "buffer A\nmethod section\n")
        write(os.path.join(d5, "input", "proj", "protocol_v2.txt"),
              "buffer A changed\nmethod section\n")
        write(os.path.join(d5, "input", "proj", "copy_of_protocol_v2.txt"),
              "buffer A changed\nmethod section\n")
        # symlink alias (skip gracefully if not permitted on the platform)
        try:
            os.symlink(os.path.join(d5, "input", "proj", "protocol_v2.txt"),
                       os.path.join(d5, "input", "proj", "alias_link.txt"))
        except (OSError, NotImplementedError):
            pass
        code = run(["--input", os.path.join(d5, "input"),
                    "--output", os.path.join(d5, "out"),
                    "--work", os.path.join(d5, "work")])
        r = read_result(os.path.join(d5, "out"))
        check("full run exits 0", code == 0, "code=%s" % code)
        check("full run status ok/partial",
              r.get("status") in ("ok", "partial"), r.get("status"))
        dup = r["computed"]["duplicate_groups"]
        check("duplicate detected (content hash)", any(
            len(g["paths"]) >= 2 for g in dup), json.dumps(dup))
        ver = r["computed"]["version_candidates"]
        check("version candidate chain detected", len(ver) >= 1, json.dumps(ver))
        check("mechanical answer present",
              len(r["computed"].get("answers") or []) >= 1,
              json.dumps(r["computed"].get("answers")))
        # artifacts present
        for rel in ("library_manifest.csv", "directory_tree.txt",
                    "dedup_version.json", "recovery_report.json",
                    "library_index.json", "library_manifest.md",
                    "operation_log.jsonl"):
            check("artifact %s exists" % rel,
                  os.path.isfile(os.path.join(d5, "out", rel)), rel)

        # ---------------------------------------------------------------
        # 6. determinism: two runs, identical modulo runtime timestamps
        # ---------------------------------------------------------------
        def strip_rt(obj):
            if isinstance(obj, dict):
                return {k: strip_rt(v) for k, v in obj.items()
                        if k not in ("runtime", "change_log", "time")}
            if isinstance(obj, list):
                return [strip_rt(v) for v in obj]
            return obj

        out2 = os.path.join(d5, "out2")
        run(["--input", os.path.join(d5, "input"),
             "--output", out2, "--work", os.path.join(d5, "work2")])
        r2 = read_result(out2)
        check("deterministic across runs",
              strip_rt(r) == strip_rt(r2), "result differs")

        # ---------------------------------------------------------------
        # 7. --check passes a good result
        # ---------------------------------------------------------------
        code = run(["--check", os.path.join(d5, "out", "result.json"),
                    "--case-json", os.path.join(d5, "input", "case.json")])
        check("--check passes good result", code == 0, "code=%s" % code)

        # ---------------------------------------------------------------
        # 8. --skeleton-only writes partial skeleton fast
        # ---------------------------------------------------------------
        d8 = os.path.join(tmp, "skeleton")
        os.makedirs(os.path.join(d8, "input"), exist_ok=True)
        os.makedirs(os.path.join(d8, "out"), exist_ok=True)
        write(os.path.join(d8, "input", "case.json"), '{"case_id": "c-sk"}')
        code = run(["--skeleton-only", "--input", os.path.join(d8, "input"),
                    "--output", os.path.join(d8, "out"),
                    "--work", os.path.join(d8, "work")])
        r = read_result(os.path.join(d8, "out"))
        check("skeleton exits 0", code == 0, "code=%s" % code)
        check("skeleton status partial", r.get("status") == "partial",
              r.get("status"))
        check("skeleton case_id present", r.get("case_id") == "c-sk")

        # ---------------------------------------------------------------
        # 9. --update merges a semantic patch
        # ---------------------------------------------------------------
        patch = os.path.join(d8, "patch.json")
        write(patch, '{"status": "ok", "computed": {"semantic_note": "x"}}')
        code = run(["--update", patch, "--result",
                    os.path.join(d8, "out", "result.json")])
        r = read_result(os.path.join(d8, "out"))
        check("update exits 0", code == 0, "code=%s" % code)
        check("update merged status", r.get("status") == "ok", r.get("status"))
        check("update merged computed", r["computed"].get("semantic_note") == "x")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
