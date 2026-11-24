# -*- coding: utf-8 -*-
"""selftest.py — robustness + correctness self-test for the library-repair Skill.

Standard library only. Creates disposable temp directories, runs the real
pipeline against synthetic inputs, and checks graceful degradation for:

  * empty input            -> insufficient_evidence, exit code 0
  * invalid case.json      -> input_error / recorded error, no crash
  * missing arguments      -> parser defaults resolve (no crash)
  * oversized file         -> too_large recorded, no crash
  * broken symlink         -> recorded, no crash
  * malformed JSON catalog -> recorded as catalog error, other files processed
  * non-UTF-8 filename     -> surrogate-safe, no crash
  * duplicate + version    -> correct groups and chains
  * determinism            -> two runs identical (modulo timestamps)

Exit code 0 = all non-skipped checks pass.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def report(name, ok, note=""):
    global PASS, FAIL, SKIP
    if ok is None:
        SKIP += 1
        print("SKIP  %-28s %s" % (name, note))
    elif ok:
        PASS += 1
        print("PASS  %-28s %s" % (name, note))
    else:
        FAIL += 1
        print("FAIL  %-28s %s" % (name, note))


def make_args(input_root, output_dir, case_json, task_path=None, max_hash_bytes=None,
              skeleton_only=False):
    return types.SimpleNamespace(
        case_json=case_json, task_path=task_path or os.path.join(input_root, "task.md"),
        input_root=input_root, output_dir=output_dir,
        work_dir=os.path.join(output_dir, "..", "work"),
        max_hash_bytes=max_hash_bytes if max_hash_bytes is not None else core_impl.DEFAULT_MAX_HASH_BYTES,
        skeleton_only=skeleton_only, update=None, result=os.path.join(output_dir, "result.json"),
        check=None,
    )


def run_pipeline(args):
    rc = core_impl.run_full(args, time.time())
    path = os.path.join(args.output_dir, "result.json")
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    return rc, result


def write(path, data, encoding="utf-8", binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as f:
            f.write(data)
    else:
        with open(path, "w", encoding=encoding, newline="\n") as f:
            f.write(data)


def test_empty_input():
    root = tempfile.mkdtemp(prefix="selftest_empty_")
    out = tempfile.mkdtemp(prefix="selftest_empty_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "empty-case", "output_contract": {"files": [], "report": None}}))
    write(os.path.join(root, "task.md"), "# empty\n")
    try:
        rc, result = run_pipeline(make_args(root, out, case))
        ok = (rc == 0) and (result["status"] == "insufficient_evidence") and result.get("case_id") == "empty-case"
        report("empty input", ok, "status=%s" % result["status"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_bad_case_json():
    root = tempfile.mkdtemp(prefix="selftest_badcase_")
    out = tempfile.mkdtemp(prefix="selftest_badcase_out_")
    case = os.path.join(root, "case.json")
    write(case, "{ not valid json ")
    write(os.path.join(root, "data.txt"), "hello\n")
    try:
        rc, result = run_pipeline(make_args(root, out, case))
        ok = (rc == 0) and (result.get("status") in ("input_error", "partial", "ok")) \
            and any("bad_json" in e or "json" in e.lower() for e in result.get("errors", []))
        report("bad case.json", ok, "status=%s errors=%d" % (result["status"], len(result.get("errors", []))))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_missing_args_defaults():
    p = core_impl.build_arg_parser()
    ns = p.parse_args([])
    ok = all(getattr(ns, k, None) for k in ("case_json", "input_root", "output_dir"))
    report("missing args default", ok, "%s | %s | %s" % (ns.case_json, ns.input_root, ns.output_dir))


def test_oversized_file():
    root = tempfile.mkdtemp(prefix="selftest_big_")
    out = tempfile.mkdtemp(prefix="selftest_big_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "big-case"}))
    write(os.path.join(root, "task.md"), "# big\n")
    big = os.path.join(root, "big.bin")
    write(big, b"\x00" * 4096, binary=True)
    write(os.path.join(root, "small.txt"), "small\n")
    try:
        rc, result = run_pipeline(make_args(root, out, case, max_hash_bytes=10))
        entries = result["computed"]["inventory"]["files"]
        big_e = next(e for e in entries if e["path"] == "big.bin")
        ok = (rc == 0) and (big_e["status"] == "too_large") and (big_e["sha256"] is None)
        report("oversized file", ok, "status=%s" % big_e["status"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_broken_symlink():
    root = tempfile.mkdtemp(prefix="selftest_link_")
    out = tempfile.mkdtemp(prefix="selftest_link_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "link-case"}))
    write(os.path.join(root, "task.md"), "# link\n")
    link = os.path.join(root, "broken.pdf")
    try:
        os.symlink(os.path.join(root, "missing_target.pdf"), link)
    except (OSError, NotImplementedError):
        report("broken symlink", None, "symlink unsupported on this platform")
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        return
    try:
        rc, result = run_pipeline(make_args(root, out, case))
        aliases = result["computed"]["aliases"]
        ok = (rc == 0) and ("broken.pdf" in aliases["broken_symlinks"])
        report("broken symlink", ok, "broken=%s" % aliases["broken_symlinks"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_bad_json_catalog():
    root = tempfile.mkdtemp(prefix="selftest_badcat_")
    out = tempfile.mkdtemp(prefix="selftest_badcat_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "badcat-case"}))
    write(os.path.join(root, "task.md"), "# badcat\n")
    write(os.path.join(root, "catalog.json"), "{ broken json ")
    write(os.path.join(root, "good.txt"), "good content\n")
    try:
        rc, result = run_pipeline(make_args(root, out, case))
        ok = (rc == 0) and any("catalog" in e for e in result.get("errors", []))
        report("malformed JSON catalog", ok, "errors=%s" % result.get("errors"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_non_utf8_filename():
    root = tempfile.mkdtemp(prefix="selftest_enc_")
    out = tempfile.mkdtemp(prefix="selftest_enc_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "enc-case"}))
    write(os.path.join(root, "task.md"), "# enc\n")
    bad_name = os.fsencode(root) + b"\\" + b"bad_\xff\xfe_name.txt"
    try:
        with open(bad_name, "wb") as f:
            f.write(b"data\n")
    except (OSError, ValueError):
        report("non-utf8 filename", None, "cannot create undecodable name on this platform")
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        return
    try:
        rc, result = run_pipeline(make_args(root, out, case))
        ok = rc == 0 and result.get("status") in ("ok", "partial", "insufficient_evidence")
        report("non-utf8 filename", ok, "status=%s files=%d" % (
            result["status"], len(result["computed"]["inventory"]["files"])))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_dup_and_version():
    root = tempfile.mkdtemp(prefix="selftest_dup_")
    out = tempfile.mkdtemp(prefix="selftest_dup_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "dup-case", "output_contract": {
        "manifest": None, "versions": [], "aliases": [],
    }}))
    write(os.path.join(root, "task.md"), "# dup\n")
    write(os.path.join(root, "report_v1.pdf"), "PDF-A")
    write(os.path.join(root, "report_v2.pdf"), "PDF-B")
    write(os.path.join(root, "report_final.pdf"), "PDF-B")  # duplicate of v2
    try:
        rc, result = run_pipeline(make_args(root, out, case))
        dup = result["computed"]["duplicate_groups"]
        chains = result["computed"]["version_candidates"]
        dup_ok = any(len(v) == 2 for v in dup.values())
        chain_ok = any(c["stem"] == "report" and len(c["members"]) >= 2 for c in chains.values())
        contract_ok = all(k in result for k in ("manifest", "versions", "aliases"))
        ok = rc == 0 and dup_ok and chain_ok and contract_ok
        report("duplicate + version", ok, "dup_groups=%d chains=%d" % (len(dup), len(chains)))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items()
                if k not in ("runtime", "change_log", "seconds", "started_at", "finished_at", "time")}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def test_determinism():
    root = tempfile.mkdtemp(prefix="selftest_det_")
    out1 = tempfile.mkdtemp(prefix="selftest_det1_")
    out2 = tempfile.mkdtemp(prefix="selftest_det2_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "det-case"}))
    write(os.path.join(root, "task.md"), "# det\n")
    write(os.path.join(root, "a.txt"), "alpha")
    write(os.path.join(root, "b.txt"), "beta")
    write(os.path.join(root, "sub/c.txt"), "gamma")
    try:
        _, r1 = run_pipeline(make_args(root, out1, case))
        _, r2 = run_pipeline(make_args(root, out2, case))
        ok = strip_volatile(r1) == strip_volatile(r2)
        report("determinism", ok, "two runs identical (modulo timestamps)")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out1, ignore_errors=True)
        shutil.rmtree(out2, ignore_errors=True)


def test_skeleton_only():
    root = tempfile.mkdtemp(prefix="selftest_skel_")
    out = tempfile.mkdtemp(prefix="selftest_skel_out_")
    case = os.path.join(root, "case.json")
    write(case, json.dumps({"case_id": "skel-case", "output_contract": {"files": []}}))
    write(os.path.join(root, "task.md"), "# skel\n")
    try:
        args = make_args(root, out, case, skeleton_only=True)
        rc = core_impl.run_full(args, time.time())
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            result = json.load(f)
        ok = rc == 0 and result["status"] == "partial" and "files" in result
        report("skeleton-only", ok, "status=%s" % result["status"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def main():
    print("library-repair selftest")
    test_empty_input()
    test_bad_case_json()
    test_missing_args_defaults()
    test_oversized_file()
    test_broken_symlink()
    test_bad_json_catalog()
    test_non_utf8_filename()
    test_dup_and_version()
    test_determinism()
    test_skeleton_only()
    print("-" * 60)
    print("PASS=%d FAIL=%d SKIP=%d" % (PASS, FAIL, SKIP))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
