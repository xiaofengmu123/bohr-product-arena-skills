#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the robust Results & Discussion writing pipeline.

Runs a battery of edge cases against scripts/main.py in temporary directories
and prints a JSON report. Every scenario must degrade gracefully: the process
never raises an unhandled traceback, and a legal result.json / summary is
written where expected.

Run:  python scripts/selftest.py
Exit: always 0; pass/fail is encoded in the JSON report.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main  # noqa: E402


def run_argv(argv):
    """Invoke main.main() with argv, capture stdout JSON, return parsed dict."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    saved = sys.argv
    try:
        sys.argv = ["main.py"] + argv
        with redirect_stdout(buf):
            main.main()
    finally:
        sys.argv = saved
    out = buf.getvalue().strip()
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return {"_raw": out}


def make_case(tmp, *, case_text=None, task_text=None, materials=None):
    """Create a synthetic /input tree and return the input-dir path."""
    indir = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(indir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    if case_text is not None:
        with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write(case_text)
    if task_text is not None:
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write(task_text)
    for name, content in (materials or {}).items():
        with open(os.path.join(indir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    return indir, work, os.path.join(out, "result.json")


GOOD_CASE = json.dumps({
    "case_id": "demo-case",
    "task": "write results & discussion",
    "output_contract": {
        "deliverable": "object", "facts_sources": "array",
        "version_boundaries": "object",
    },
}, ensure_ascii=False)
GOOD_TASK = "# 结果与讨论章节写作\n\n写结果段与讨论段。\n"


def t_empty_input():
    tmp = tempfile.mkdtemp(prefix="st_empty_")
    try:
        indir, work, out = make_case(tmp)
        r = run_argv(["run", "--input-dir", indir, "--work", work, "--out", out])
        ok = r.get("ok") is True
        # build should still produce a legal result.json
        exists = os.path.exists(out)
        if exists:
            parsed = json.load(open(out, encoding="utf-8"))
            has_fields = "case_id" in parsed and "status" in parsed
        else:
            has_fields = False
        return ok and exists and has_fields, f"run envelope={r}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_bad_json():
    tmp = tempfile.mkdtemp(prefix="st_badjson_")
    try:
        indir, work, out = make_case(tmp, case_text="{not valid json",
                                     task_text=GOOD_TASK)
        r = run_argv(["run", "--input-dir", indir, "--work", work, "--out", out])
        exists = os.path.exists(out)
        if exists:
            parsed = json.load(open(out, encoding="utf-8"))
            status = parsed.get("status")
            degraded = bool(parsed.get("errors") or parsed.get("degradations"))
        else:
            status, degraded = None, False
        return (r.get("ok") is True and exists and status in
                ("in_progress", "input_error", "failed") and degraded,
                f"run envelope={r}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_bad_encoding():
    tmp = tempfile.mkdtemp(prefix="st_enc_")
    try:
        indir, work, out = make_case(
            tmp, case_text=GOOD_CASE, task_text=GOOD_TASK,
            materials={"notes.md": "中文结果 p=0.042，均值 12.3\u00b13.4".encode("gb18030").decode("gb18030")})
        # write a GB18030 file on disk
        with open(os.path.join(indir, "notes.md"), "wb") as fh:
            fh.write("中文结果 p=0.042，均值 12.3±3.4".encode("gb18030"))
        r = run_argv(["read", "--input-dir", indir, "--work", work])
        sp = os.path.join(work, "input_summary.json")
        parsed = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
        mats = parsed.get("materials", [])
        read_ok = any(m.get("kind") == "text" and m.get("encoding") == "gb18030"
                      for m in mats)
        return r.get("ok") is True and read_ok, f"materials={mats}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_oversized():
    tmp = tempfile.mkdtemp(prefix="st_big_")
    try:
        indir, work, out = make_case(
            tmp, case_text=GOOD_CASE, task_text=GOOD_TASK,
            materials={"huge.csv": "x,y\n"})
        big = os.path.join(indir, "huge.csv")
        with open(big, "wb") as fh:
            fh.write(b"a,b\n" + b"1,2\n" * (main.MAX_FILE_BYTES // 4 + 10))
        r = run_argv(["read", "--input-dir", indir, "--work", work])
        sp = os.path.join(work, "input_summary.json")
        parsed = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
        skipped = parsed.get("skipped", [])
        skip_ok = any(s.get("file", "").endswith("huge.csv") and
                      s.get("reason") == "oversized" for s in skipped)
        return r.get("ok") is True and skip_ok, f"skipped={skipped}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_missing_params_defaults():
    # No explicit --input-dir/--work/--out; defaults are used without crashing.
    r = run_argv(["read"])
    # On this dev machine /input may not exist; the point is no exception.
    return r.get("ok") is True, f"envelope={r}"


def t_determinism():
    tmp = tempfile.mkdtemp(prefix="st_det_")
    try:
        indir, work, _ = make_case(
            tmp, case_text=GOOD_CASE, task_text=GOOD_TASK,
            materials={"a.txt": "alpha 0.05", "b.txt": "beta 0.001"})
        run_argv(["read", "--input-dir", indir, "--work", work])
        sp = os.path.join(work, "input_summary.json")
        s1 = json.load(open(sp, encoding="utf-8"))
        run_argv(["read", "--input-dir", indir, "--work", work])
        s2 = json.load(open(sp, encoding="utf-8"))
        detail = "identical across runs" if s1 == s2 else "input_summary differs"
        return s1 == s2, detail
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_build_skeleton_and_check():
    tmp = tempfile.mkdtemp(prefix="st_build_")
    try:
        indir, work, out = make_case(
            tmp, case_text=GOOD_CASE, task_text=GOOD_TASK,
            materials={"results.txt": "Group A mean 10.2 (SD 1.1), n=30"})
        run_argv(["run", "--input-dir", indir, "--work", work, "--out", out])
        parsed = json.load(open(out, encoding="utf-8"))
        required = ["case_id", "status", "deliverable", "facts_sources",
                    "version_boundaries", "sources", "uncertainty",
                    "pending_human", "change_log"]
        missing = [k for k in required if k not in parsed]
        # check report
        rchk = run_argv(["check", "--result", out,
                         "--summary", os.path.join(work, "input_summary.json")])
        return (parsed.get("case_id") == "demo-case" and not missing and
                "passed" in rchk), f"missing={missing}, check={rchk}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_patch_preserves():
    tmp = tempfile.mkdtemp(prefix="st_patch_")
    try:
        indir, work, out = make_case(tmp, case_text=GOOD_CASE, task_text=GOOD_TASK)
        run_argv(["build", "--summary", os.path.join(work, "input_summary.json"),
                  "--out", out])
        # simulate agent filling a field, then patch
        parsed = json.load(open(out, encoding="utf-8"))
        parsed["deliverable"] = {"file": "manuscript.md", "files": [],
                                 "format": "markdown", "note": None}
        json.dump(parsed, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        run_argv(["read", "--input-dir", indir, "--work", work])
        run_argv(["build", "--summary", os.path.join(work, "input_summary.json"),
                  "--out", out, "--patch"])
        merged = json.load(open(out, encoding="utf-8"))
        preserved = merged.get("deliverable", {}).get("file") == "manuscript.md"
        return preserved, f"deliverable={merged.get('deliverable')}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t_quote():
    tmp = tempfile.mkdtemp(prefix="st_quote_")
    try:
        f = os.path.join(tmp, "notes.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("第一行结论\n第二行数据 p=0.03\n\n图 2 显示趋势\nFig 3 shows another trend\n")
        q = run_argv(["quote", "--file", f, "--locator", "L2"])
        ok_line = q.get("ok") is True and "p=0.03" in q.get("quote", "")
        q2 = run_argv(["quote", "--file", f, "--locator", "图 2"])
        ok_cn = q2.get("ok") is True and "图 2" in q2.get("quote", "")
        q3 = run_argv(["quote", "--file", f, "--locator", "Fig 3"])
        ok_en = q3.get("ok") is True and "another trend" in q3.get("quote", "")
        return ok_line and ok_cn and ok_en, f"q={q}, q2={q2}, q3={q3}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    ("empty_input", t_empty_input),
    ("bad_json", t_bad_json),
    ("bad_encoding", t_bad_encoding),
    ("oversized_file", t_oversized),
    ("missing_params_defaults", t_missing_params_defaults),
    ("determinism", t_determinism),
    ("build_skeleton_and_check", t_build_skeleton_and_check),
    ("patch_preserves", t_patch_preserves),
    ("quote_extraction", t_quote),
]


def main_selftest():
    results = []
    for name, fn in TESTS:
        try:
            passed, detail = fn()
        except Exception:
            passed, detail = False, traceback.format_exc(limit=6)
        results.append({"test": name, "passed": bool(passed), "detail": detail})
    report = {
        "ok": all(r["passed"] for r in results),
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main_selftest())
