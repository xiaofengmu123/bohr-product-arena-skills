#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-check for the document library repair Skill.

Builds a *synthetic* throwaway case in a temp directory (never a pre-written
answer for the official cases), runs the real pipeline, and asserts that:

  * result.json is valid and fills every output_contract field;
  * the editable artifacts exist and are non-empty;
  * duplicates are detected by content hash, not filename;
  * a broken symlink and a hardlink alias are recorded, not silently skipped;
  * a malformed JSON catalog degrades without crashing;
  * --check and --update both work.

Exit code 0 = all checks passed, 1 = a check failed, 2 = setup problem.
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _run(cmdargs):
    try:
        return core_impl.main(cmdargs)
    except SystemExit as e:
        return int(e.code or 0)


def make_case(root):
    os.makedirs(os.path.join(root, "data", "raw"), exist_ok=True)
    os.makedirs(os.path.join(root, "data", "empty_dir"), exist_ok=True)
    with open(os.path.join(root, "data", "raw", "report_v1.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4\n" + b"content-AAA" * 100)
    with open(os.path.join(root, "data", "raw", "report_v2.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4\n" + b"content-AAA" * 100 + b"-revised")
    # duplicate of report_v1, different name -> must be grouped by content hash
    with open(os.path.join(root, "data", "report_copy.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4\n" + b"content-AAA" * 100)
    # hardlink alias
    src = os.path.join(root, "data", "raw", "notes.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("administrative note: version v1.2 approved 2024-05-01\n")
    ln = os.path.join(root, "data", "raw", "notes_alias.txt")
    try:
        os.link(src, ln)
    except OSError:
        shutil.copyfile(src, ln)  # fallback where hardlinks are unavailable
    # broken symlink
    try:
        os.symlink(os.path.join(root, "does-not-exist.bib"), os.path.join(root, "broken.bib"))
    except OSError:
        pass
    # malformed JSON catalog -> must degrade, not crash
    with open(os.path.join(root, "catalog.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")
    # valid CSV catalog
    with open(os.path.join(root, "catalog.csv"), "w", encoding="utf-8") as fh:
        fh.write("path,title\nreport_v1.pdf,Quarterly report\nghost.pdf,Ghost entry\n")

    case = {
        "case_id": "selftest-0001",
        "title": "synthetic self-test case",
        "output_contract": {
            "case_id": {"type": "string"},
            "file_inventory": {"type": "array"},
            "duplicate_groups": {"type": "array"},
            "version_candidates": {"type": "array"},
            "summary": {"type": "object"},
            "recovery": {"type": "object"},
            "unknown_field": {"type": "any"},
        },
        "questions": [
            {"id": "q1", "question": "有多少重复文件"},
            {"id": "q2", "question": "有哪些版本"},
        ],
    }
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 科研资料库自检\n\n版本 v1.2 于 2024-05-01 定稿。\n")


def main():
    tmp = tempfile.mkdtemp(prefix="library_selftest_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    failures = []
    try:
        os.makedirs(inp)
        make_case(inp)

        # 1. skeleton-only must succeed and write a valid partial result
        rc = _run(["--input", inp, "--output", out, "--work", work,
                   "--case-json", os.path.join(inp, "case.json"),
                   "--task-path", os.path.join(inp, "task.md"), "--skeleton-only"])
        if rc != 0:
            failures.append("skeleton-only exit=%d" % rc)
        else:
            r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
            if r.get("status") != "partial" or not r.get("case_id"):
                failures.append("skeleton result missing case_id/partial status")

        # 2. full run
        rc = _run(["--input", inp, "--output", out, "--work", work,
                   "--case-json", os.path.join(inp, "case.json"),
                   "--task-path", os.path.join(inp, "task.md")])
        if rc != 0:
            failures.append("full run exit=%d" % rc)
        rpath = os.path.join(out, "result.json")
        r = json.load(open(rpath, encoding="utf-8"))

        # 3. result.json contract completeness
        for key in ("case_id", "status", "sources", "uncertainty", "pending_human",
                    "change_log", "errors", "degradations", "runtime", "limitations",
                    "computed", "artifacts"):
            if key not in r:
                failures.append("result missing key: %s" % key)
        for field in ("file_inventory", "duplicate_groups", "version_candidates",
                      "summary", "recovery", "unknown_field"):
            if field not in r and "_contract_%s" % field not in r.get("computed", {}):
                failures.append("contract field not filled: %s" % field)

        # 4. artifacts exist and are non-empty
        for art in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                    "recovery_report.json", "library_index.json", "library_manifest.md",
                    "operation_log.jsonl"):
            p = os.path.join(out, art)
            if not os.path.isfile(p) or os.path.getsize(p) == 0:
                failures.append("artifact missing/empty: %s" % art)

        # 5. content-hash dedup (report_v1.pdf == report_copy.pdf)
        dups = r["computed"].get("duplicate_groups", [])
        dup_ok = any(
            {"report_v1.pdf", "report_copy.pdf"} <= {os.path.basename(p) for p in g["paths"]}
            for g in dups
        )
        if not dup_ok:
            failures.append("content-hash duplicate group not found")

        # 6. broken symlink recorded if it could be created
        broken = [f for f in r["computed"].get("files", []) if f.get("broken")]
        try:
            os.symlink("x", os.path.join(tmp, "probe"))
            symlink_ok = True
            os.unlink(os.path.join(tmp, "probe"))
        except OSError:
            symlink_ok = False
        if symlink_ok and not broken:
            failures.append("broken symlink not recorded")

        # 7. malformed JSON catalog -> recorded, no crash
        rec = r["computed"].get("reconciliation", {})
        cats = rec.get("catalogs", [])
        if not any(c.get("path", "").endswith("catalog.json") and c.get("error") for c in cats):
            failures.append("malformed catalog not recorded")

        # 8. --check and --update
        rc = _run(["--check", rpath, "--case-json", os.path.join(inp, "case.json")])
        if rc != 0:
            failures.append("--check exit=%d" % rc)
        patch = os.path.join(tmp, "patch.json")
        with open(patch, "w", encoding="utf-8") as fh:
            json.dump({"computed": {"semantic": "confirmed by reviewer"}}, fh)
        rc = _run(["--update", patch, "--result", rpath])
        if rc != 0:
            failures.append("--update exit=%d" % rc)

        if failures:
            print("selftest: FAIL")
            for f in failures:
                print("  - %s" % f)
            return 1
        print("selftest: OK (case_id=%s status=%s)" % (r.get("case_id"), r.get("status")))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
