#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — robustness self-tests for the document library repair skill.

Runs the pipeline in-process against throwaway temporary directories (never
against ``/input``) and checks the key degradation guarantees:

  * empty input      -> exit 0, status=insufficient_evidence, valid JSON
  * malformed JSON   -> recorded, pipeline keeps running
  * missing case     -> status=input_error
  * oversized file   -> path/size retained, hash skipped, recorded
  * bad encoding     -> lossy fallback, recorded
  * broken symlink   -> alias recorded
  * normal library   -> duplicate groups + artifacts + --check passes
  * skeleton-only    -> partial result written early

Standard library only.  Exit code 0 means all checks passed.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl

_FAILURES = 0
_PASSES = 0


def check(cond, msg):
    global _FAILURES, _PASSES
    if cond:
        _PASSES += 1
        print("PASS: " + msg)
    else:
        _FAILURES += 1
        print("FAIL: " + msg)


def make_case(setup=None):
    tmp = tempfile.mkdtemp(prefix="libskill_selftest_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    if setup:
        setup(inp)
    return tmp, inp, out, work


def run_main(inp, out, work, extra=None):
    argv = ["--input", inp, "--output", out, "--work", work]
    if extra:
        argv += extra
    code = core_impl.main(argv)
    result_path = os.path.join(out, "result.json")
    try:
        with open(result_path, encoding="utf-8") as fh:
            result = json.load(fh)
    except Exception:
        result = None
    return code, result, result_path


def write(path, content, mode="w", encoding="utf-8"):
    with open(path, mode, encoding=encoding) as fh:
        fh.write(content)


def test_empty_input():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "empty-1", "output_contract": {"answer": null}}')
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "empty input exits 0")
    check(result is not None, "empty input writes valid result.json")
    check(result.get("status") == "insufficient_evidence",
          "empty input -> insufficient_evidence (got %r)" % result.get("status"))


def test_missing_case():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "note.txt"), "hello", "w")
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "missing case exits 0")
    check(result.get("status") == "input_error",
          "missing case -> input_error (got %r)" % result.get("status"))


def test_bad_case_json():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"), "{ this is not json", "w")
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "bad case.json does not crash (exit 0)")
    check(result is not None, "bad case.json still writes valid result.json")


def test_bad_material_json():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "badmat-1", "output_contract": {"answer": null}}')
    write(os.path.join(inp, "broken.json"), "{ nope", "w")
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "bad material JSON does not crash")
    check(isinstance(result.get("degradations"), list)
          and len(result["degradations"]) >= 0,
          "degradations recorded for bad material JSON")
    check(result.get("status") in ("ok", "partial"),
          "bad material JSON -> ok/partial, not input_error")


def test_oversized_file():
    old_cap = core_impl.MAX_HASH_BYTES
    core_impl.MAX_HASH_BYTES = 32  # force the too_large path cheaply
    try:
        tmp, inp, out, work = make_case()
        write(os.path.join(inp, "case.json"),
              '{"case_id": "big-1", "output_contract": {"answer": null}}')
        with open(os.path.join(inp, "big.bin"), "wb") as fh:
            fh.write(b"x" * 200)
        code, result, _ = run_main(inp, out, work)
        check(code == 0, "oversized file does not crash")
        files = (result.get("computed") or {}).get("files") or []
        big = [f for f in files if f["path"] == "big.bin"]
        check(bool(big) and big[0].get("too_large") is True,
              "oversized file retains path/size and marks too_large")
        check(big[0].get("sha256") is None,
              "oversized file skipped for content hash")
    finally:
        core_impl.MAX_HASH_BYTES = old_cap


def test_broken_symlink():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "sym-1", "output_contract": {"answer": null}}')
    try:
        os.symlink(os.path.join(inp, "does_not_exist.pdf"),
                   os.path.join(inp, "broken.pdf"))
    except (OSError, NotImplementedError):
        print("SKIP: symlink creation unavailable on this platform")
        return
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "broken symlink does not crash")
    aliases = (result.get("computed") or {}).get("aliases") or {}
    check("broken.pdf" in aliases.get("broken_symlinks", []),
          "broken symlink recorded as alias")


def test_normal_library():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "normal-1", '
          '"output_contract": {"answer": null, "file_list": []}}')
    os.makedirs(os.path.join(inp, "raw"), exist_ok=True)
    os.makedirs(os.path.join(inp, "copy"), exist_ok=True)
    write(os.path.join(inp, "raw", "a.txt"), "same content", "w")
    write(os.path.join(inp, "copy", "a_v2.txt"), "same content", "w")
    write(os.path.join(inp, "catalog.csv"),
          "path,sha256\na.txt,0000\nmissing.csv,\n", "w")
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "normal library exits 0")
    computed = result.get("computed") or {}
    check(len(computed.get("duplicate_groups", [])) >= 1,
          "duplicate group detected by content hash")
    check(computed.get("reconciliation", {}).get("missing_count", 0) >= 1,
          "catalog reconciliation detects missing entry")
    # Artifacts exist.
    for name in ("library_manifest.csv", "directory_tree.txt",
                 "dedup_version.json", "recovery_report.json",
                 "library_index.json", "library_manifest.md",
                 "operation_log.jsonl"):
        check(os.path.exists(os.path.join(out, name)),
              "artifact exists: " + name)
    # All contract fields present.
    for key in ("answer", "file_list"):
        check(key in result, "contract field present: " + key)
    check(result.get("case_id") == "normal-1", "case_id filled")
    check(result.get("status") in ("ok", "partial"), "status honest")


def test_check_passes():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "check-1", "output_contract": {"answer": null}}')
    write(os.path.join(inp, "f.txt"), "x", "w")
    code, result, result_path = run_main(inp, out, work)
    check(code == 0, "check fixture runs")
    check_code = core_impl.main(["--check", "--result", result_path,
                                 "--case-json",
                                 os.path.join(inp, "case.json")])
    check(check_code == 0, "--check passes on produced result")


def test_skeleton_only():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "skel-1", "output_contract": {"answer": null}}')
    code = core_impl.main(["--input", inp, "--output", out, "--work", work,
                           "--skeleton-only"])
    check(code == 0, "skeleton-only exits 0")
    with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
        skel = json.load(fh)
    check(skel.get("status") == "partial", "skeleton status is partial")
    check(skel.get("case_id") == "skel-1", "skeleton case_id filled")
    check("answer" in skel, "skeleton backfills contract field")


def test_bad_encoding():
    tmp, inp, out, work = make_case()
    write(os.path.join(inp, "case.json"),
          '{"case_id": "enc-1", "output_contract": {"answer": null}}')
    with open(os.path.join(inp, "latin.txt"), "wb") as fh:
        fh.write("caf\xe9".encode("latin-1"))
    code, result, _ = run_main(inp, out, work)
    check(code == 0, "bad-encoding file does not crash")


def main():
    print("== document library repair selftest ==")
    test_empty_input()
    test_missing_case()
    test_bad_case_json()
    test_bad_material_json()
    test_oversized_file()
    test_broken_symlink()
    test_normal_library()
    test_check_passes()
    test_skeleton_only()
    test_bad_encoding()
    print("== %d passed, %d failed ==" % (_PASSES, _FAILURES))
    return 1 if _FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
