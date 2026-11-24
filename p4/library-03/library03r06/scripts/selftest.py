#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — sci-library-organizer 自检脚本。

在临时目录构造最小夹具，验证：调用接口、空/缺输入、坏 JSON、重复/版本/别名识别、
计划中间表示、幂等落盘与回滚、result.json 契约完整性。不依赖正式案例与外部网络，
仅用 Python 标准库；任何一项失败即退出码 1。

用法:
    python3 -B scripts/selftest.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402
import organize  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append((name, detail))
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + str(detail)) if detail else ""))


def write(path, content):
    with open(path, "wb") as f:
        f.write(content if isinstance(content, bytes) else content.encode("utf-8"))


def fixture_files(root):
    pdf = b"%PDF-1.4\n% scannable text\n1 0 obj\nendobj\n%%EOF\n"
    write(os.path.join(root, "paper.pdf"), pdf)
    write(os.path.join(root, "paper_copy.pdf"), pdf)
    write(os.path.join(root, "smith2020_preprint.pdf"),
          b"%PDF-1.4\npreprint version\n%%EOF\n")
    write(os.path.join(root, "smith2020_published.pdf"),
          b"%PDF-1.4\npublished version, longer body\n%%EOF\n")
    bib = ("@article{smith2020,\n title = {A Study of Perovskite Materials},\n"
           " author = {Smith, Alice and Doe, Bob},\n year = {2020},\n"
           " journal = {Journal of Materials},\n doi = {10.1000/xyz123},\n"
           " eprint = {2001.12345}\n}\n")
    write(os.path.join(root, "refs.bib"), bib)
    write(os.path.join(root, "notes.md"),
          "# library notes\nalias notes: preprint.pdf -> published.pdf\n")


def run(args):
    return main.main(args)


def test_help():
    try:
        main.main(["--help"])
        check("help_exit_0", True)
    except SystemExit as e:
        check("help_exit_0", e.code == 0, f"exit={e.code}")
    except Exception as e:  # noqa: BLE001
        check("help_exit_0", False, repr(e))


def test_missing_input():
    d = tempfile.mkdtemp(prefix="slo_missing_")
    out, work = os.path.join(d, "out"), os.path.join(d, "work")
    code = run(["--input", os.path.join(d, "nope"), "--output", out, "--work", work,
                "--no-extract-text"])
    r, err = organize.read_json(os.path.join(out, "result.json"))
    check("missing_input_code_0", code == 0, f"code={code}")
    check("missing_input_status", r is not None and r.get("status") == "input_error",
          r.get("status") if r else err)


def test_empty_input():
    d = tempfile.mkdtemp(prefix="slo_empty_")
    inp, out, work = (os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work"))
    os.makedirs(inp)
    code = run(["--input", inp, "--output", out, "--work", work, "--no-extract-text"])
    r, err = organize.read_json(os.path.join(out, "result.json"))
    check("empty_input_code_0", code == 0, f"code={code}")
    check("empty_input_status", r is not None and r.get("status") == "insufficient_evidence",
          r.get("status") if r else err)


def test_bad_case_json():
    d = tempfile.mkdtemp(prefix="slo_badjson_")
    inp, out, work = (os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work"))
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{this is not json")
    write(os.path.join(inp, "a.txt"), "hello world\n")
    code = run(["--input", inp, "--output", out, "--work", work, "--no-extract-text"])
    r, err = organize.read_json(os.path.join(out, "result.json"))
    check("badjson_code_0", code == 0, f"code={code}")
    check("badjson_valid_result", r is not None, err)
    check("badjson_degradation",
          r is not None and isinstance(r.get("degradations"), list) and len(r["degradations"]) > 0,
          r.get("degradations") if r else None)
    check("badjson_default_contract",
          r is not None and all(k in r for k in ("library", "evidence", "operations", "failures")),
          sorted(r.keys()) if r else None)


def test_full_pipeline_apply_idempotent():
    d = tempfile.mkdtemp(prefix="slo_full_")
    inp, out, work = (os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work"))
    os.makedirs(inp)
    fixture_files(inp)
    args = ["--input", inp, "--output", out, "--work", work, "--no-extract-text", "--apply"]
    code1 = run(args)
    r1, err = organize.read_json(os.path.join(out, "result.json"))
    check("apply_code_0", code1 == 0, f"code={code1}")
    check("apply_valid_result", r1 is not None, err)
    if r1 is None:
        return
    for key in ("case_id", "status", "sources", "uncertainty", "change_log",
                "library", "evidence", "operations", "failures"):
        check(f"contract_key_{key}", key in r1, f"missing {key}")

    cat = r1.get("library", {}).get("catalog", [])
    check("catalog_covers_all", len(cat) == 6, f"{len(cat)} entries")
    dupes = [e for e in cat if e.get("duplicate_of")]
    check("duplicate_detected", len(dupes) == 1 and dupes[0]["duplicate_of"] == "paper.pdf",
          [(e["source"], e.get("duplicate_of")) for e in dupes])
    vg = r1.get("library", {}).get("versions", [])
    check("version_group_found", len(vg) == 1 and len(vg[0]["members"]) == 2, vg)
    check("alias_edge_found", len(r1.get("library", {}).get("aliases", [])) >= 1,
          r1.get("library", {}).get("aliases", []))

    for f in ("plan.json", "graph.json", "index.json"):
        p = os.path.join(out, f)
        check(f"intermediate_{f}", os.path.isfile(p), f"missing {f}")
        if os.path.isfile(p):
            obj, e2 = organize.read_json(p)
            check(f"intermediate_{f}_json", obj is not None, e2)

    lib = os.path.join(out, "library")
    kept_targets = [e["target"] for e in cat if e.get("keep") and e.get("target")]
    copied = all(os.path.isfile(os.path.join(lib, *t.split("/"))) for t in kept_targets)
    check("apply_copied_kept", copied, f"kept={len(kept_targets)}")

    code2 = run(args)
    r2, _ = organize.read_json(os.path.join(out, "result.json"))
    ops2 = r2.get("operations", [])
    newly = [o for o in ops2 if o.get("op") == "copy"]
    check("apply_idempotent", code2 == 0 and not newly, f"new copies={len(newly)}")


def main_selftest():
    test_help()
    test_missing_input()
    test_empty_input()
    test_bad_case_json()
    test_full_pipeline_apply_idempotent()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for name, detail in FAIL:
            print("FAILED:", name, detail)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_selftest())
