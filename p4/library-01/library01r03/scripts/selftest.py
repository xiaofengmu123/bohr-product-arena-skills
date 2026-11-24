#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - verify determinism, contract handling and robustness.

This is the variant's headline feature. It builds its own throwaway fixtures at
runtime (nothing pre-baked is committed), runs the engine twice on identical
input, canonicalises the volatile timestamp fields, and byte-compares every
editable artifact. It also exercises empty input, bad JSON, missing case.json
and a missing output_contract.

Exit code 0 == all checks pass; anything else fails with a readable message.
Only the Python standard library is used.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable or "python3"

ISO_TIME = re.compile(r"^[12][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9:]{8}")


def canonicalize(obj, subs=()):
    """Strip volatile timestamp fields and normalise run-specific paths so two
    runs can be compared byte-for-byte."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("generated_at",) or (k == "time" and isinstance(v, str) and ISO_TIME.match(v)):
                continue
            out[k] = canonicalize(v, subs)
        return out
    if isinstance(obj, list):
        return [canonicalize(x, subs) for x in obj]
    if isinstance(obj, str):
        for find, repl in subs:
            obj = obj.replace(find, repl)
        return obj
    return obj


def canonical_text(text, subs=(), jsonl=False):
    """Canonicalise a JSON document (dict) or JSONL stream."""
    text = text.strip()
    if not text:
        return text
    if jsonl:
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.dumps(canonicalize(json.loads(line), subs), sort_keys=True, ensure_ascii=False))
            except ValueError:
                lines.append(line)
        return "\n".join(lines) + "\n"
    return json.dumps(canonicalize(json.loads(text), subs), sort_keys=True, ensure_ascii=False)


def run_main(args, cwd=None):
    proc = subprocess.run(
        [PY, "-B", MAIN] + args,
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def make_fixture(root):
    """Deterministic mixed library fixture: files, dirs, duplicates, versions,
    a broken symlink, a catalogue, and administrative version notes."""
    os.makedirs(os.path.join(root, "materials", "raw"), exist_ok=True)
    os.makedirs(os.path.join(root, "materials", "empty_dir"), exist_ok=True)

    def write(rel, content, mtime=1700000000):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.utime(p, (mtime, mtime))

    case = {
        "case_id": "selftest-case-0001",
        "task": "organise and deduplicate",
        "output_contract": {
            "directory_summary": None,
            "duplicate_report": None,
            "owner": "unknown",
        },
    }
    write("case.json", json.dumps(case, ensure_ascii=False))
    write("task.md", "版本 v1 与 v2 差异：v2 替代 v1。 请按内容哈希去重。\n")

    # duplicates (same content, different names)
    write("materials/a/report.pdf", "PDF-A-content")
    write("materials/b/report_copy.pdf", "PDF-A-content")

    # version family (same stem, different content)
    write("materials/analysis_v1.md", "# draft v1\nfirst\n", mtime=1700000001)
    write("materials/analysis_v2.md", "# draft v2\nsecond\n", mtime=1700000002)

    # catalogue listing one path as missing and one with a wrong hash
    write("materials/catalog.json", json.dumps({
        "items": [
            {"path": "materials/a/report.pdf", "sha256": "wronghash"},
            {"path": "materials/gone.txt"},
        ],
    }, ensure_ascii=False))

    # oversized file (small cap would skip; keep under cap so it hashes, but
    # exercise 'too_large' separately via a 3MB file in the robustness case)
    write("materials/large.bin", "x" * 100)

    # broken symlink (best-effort; skip on platforms where it fails)
    try:
        os.symlink("no_such_target.txt", os.path.join(root, "materials", "broken_link.txt"))
    except (OSError, NotImplementedError):
        pass

    return root


def compare_runs(tmp):
    fixture = make_fixture(os.path.join(tmp, "case"))
    out1 = os.path.join(tmp, "out1")
    out2 = os.path.join(tmp, "out2")
    os.makedirs(out1, exist_ok=True)
    os.makedirs(out2, exist_ok=True)

    rc1, so1, se1 = run_main(["--input-root", fixture, "--output-dir", out1, "--work-dir", os.path.join(tmp, "w1")])
    rc2, so2, se2 = run_main(["--input-root", fixture, "--output-dir", out2, "--work-dir", os.path.join(tmp, "w2")])
    if rc1 != 0 or rc2 != 0:
        return False, "engine exit codes %r/%r; stderr=%s%s" % (rc1, rc2, se1, se2)

    # validate result.json with --check
    rc, so, se = run_main(["--check", os.path.join(out1, "result.json"), "--case-json", os.path.join(fixture, "case.json")])
    if rc != 0:
        return False, "--check failed: %s%s" % (so, se)

    artifacts = [
        "result.json", "library_manifest.csv", "directory_tree.txt",
        "dedup_version.json", "recovery_report.json", "library_index.json",
        "answers.json", "library_manifest.md", "operation_log.jsonl",
    ]
    # run-specific paths are part of the invocation, not the input; normalise
    # them so the two runs (to different output dirs) are comparable.
    subs = [
        (out1, "<OUTDIR>"),
        (out2, "<OUTDIR>"),
        (fixture, "<INPUT>"),
        (os.path.join(tmp, "w1"), "<WORK>"),
        (os.path.join(tmp, "w2"), "<WORK>"),
    ]
    for art in artifacts:
        p1, p2 = os.path.join(out1, art), os.path.join(out2, art)
        if not os.path.isfile(p1) or not os.path.isfile(p2):
            return False, "artifact missing: %s" % art
        t1, t2 = read(p1), read(p2)
        if art.endswith(".jsonl"):
            t1, t2 = canonical_text(t1, subs, jsonl=True), canonical_text(t2, subs, jsonl=True)
        elif art.endswith(".json"):
            t1, t2 = canonical_text(t1, subs), canonical_text(t2, subs)
        if t1 != t2:
            return False, "determinism mismatch in %s" % art

    # check substantive facts actually landed in the result
    result = json.loads(read(os.path.join(out1, "result.json")))
    if result.get("case_id") != "selftest-case-0001":
        return False, "case_id not detected"
    if result.get("status") not in ("ok", "partial"):
        return False, "unexpected status %r" % result.get("status")
    if result.get("directory_summary") is not None or "directory_summary" not in result:
        return False, "output_contract fields not backfilled"
    if result.get("computed", {}).get("duplicate_groups") is None:
        return False, "duplicate groups not computed"
    return True, "ok"


def robustness_checks(tmp):
    reports = []

    # 1) empty input
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    out = os.path.join(tmp, "empty_out")
    rc, so, se = run_main(["--input-root", empty, "--output-dir", out])
    reports.append(("empty_input_exit0", rc == 0))
    if os.path.isfile(os.path.join(out, "result.json")):
        r = json.loads(read(os.path.join(out, "result.json")))
        reports.append(("empty_input_status", r.get("status") == "insufficient_evidence"))
    else:
        reports.append(("empty_input_result_exists", False))

    # 2) missing case.json
    nocase = os.path.join(tmp, "nocase")
    os.makedirs(nocase, exist_ok=True)
    with open(os.path.join(nocase, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("no case file\n")
    out2 = os.path.join(tmp, "nocase_out")
    rc, so, se = run_main(["--input-root", nocase, "--output-dir", out2])
    reports.append(("missing_case_exit0", rc == 0))
    if os.path.isfile(os.path.join(out2, "result.json")):
        r2 = json.loads(read(os.path.join(out2, "result.json")))
        reports.append(("missing_case_status", r2.get("status") == "input_error"))
    else:
        reports.append(("missing_case_result_exists", False))

    # 3) bad JSON case file
    badcase = os.path.join(tmp, "badcase")
    os.makedirs(badcase, exist_ok=True)
    with open(os.path.join(badcase, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    out3 = os.path.join(tmp, "badcase_out")
    rc, so, se = run_main(["--input-root", badcase, "--output-dir", out3])
    reports.append(("bad_json_exit0", rc == 0))
    if os.path.isfile(os.path.join(out3, "result.json")):
        r3 = json.loads(read(os.path.join(out3, "result.json")))
        reports.append(("bad_json_status", r3.get("status") == "input_error"))
    else:
        reports.append(("bad_json_result_exists", False))

    # 4) oversize file is not silently skipped (recorded as too_large)
    big = os.path.join(tmp, "big")
    os.makedirs(big, exist_ok=True)
    with open(os.path.join(big, "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "big-case"}))
    with open(os.path.join(big, "huge.bin"), "wb") as fh:
        fh.write(b"0" * (3 * 1024 * 1024))
    out4 = os.path.join(tmp, "big_out")
    rc, so, se = run_main(["--input-root", big, "--output-dir", out4])
    if os.path.isfile(os.path.join(out4, "result.json")):
        r4 = json.loads(read(os.path.join(out4, "result.json")))
        deg = r4.get("degradations") or []
        reports.append(("oversize_recorded", any(d.get("issue") == "too_large" for d in deg)))
    else:
        reports.append(("oversize_recorded", False))

    return reports


def main():
    tmp = tempfile.mkdtemp(prefix="library-selftest-")
    try:
        ok, msg = compare_runs(tmp)
        if not ok:
            print("FAIL determinism: %s" % msg)
            return 1
        print("PASS determinism: two runs byte-identical after timestamp canonicalisation")

        reports = robustness_checks(tmp)
        all_ok = True
        for name, passed in reports:
            print("%s %s" % ("PASS" if passed else "FAIL", name))
            all_ok = all_ok and passed
        if not all_ok:
            return 1

        print("selftest: ALL PASS (%d checks)" % (len(reports) + 1))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
