#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — sci-library-organizer 自检脚本。

在临时目录构造最小合成输入，逐条验证：帮助退出码、空目录、缺输入、坏 JSON、
契约字段、去重、损坏 PDF 降级、BibTeX 元数据、证据定位、幂等性、--apply 幂等
与回滚。全部通过退出码 0，否则 1。合成输入仅用于自检，不冒充正式案例结果。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  — " + detail) if detail else ""))


def run_main(args):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True)


def make_input(base):
    d = os.path.join(base, "input")
    os.makedirs(os.path.join(d, "sub"))
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "selftest-case-001"}, f)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 自检任务\n整理文献目录。\n")
    with open(os.path.join(d, "refs.bib"), "w", encoding="utf-8") as f:
        f.write("@article{smith2020deep,\n  title={Deep Learning for Vision},\n"
                "  author={Smith, John and Li, Wei},\n  journal={Journal of ML},\n"
                "  year={2020},\n  doi={10.1234/abcd.5678}\n}\n")
    with open(os.path.join(d, "paper_copy.txt"), "w", encoding="utf-8") as f:
        f.write("same content")
    with open(os.path.join(d, "sub", "paper_copy.txt"), "w", encoding="utf-8") as f:
        f.write("same content")  # 字节级重复
    with open(os.path.join(d, "notes.md"), "w", encoding="utf-8") as f:
        f.write("# Notes\nA protein folding dataset, no DOI.\n")
    with open(os.path.join(d, "broken.pdf"), "wb") as f:
        f.write(b"not a real pdf")  # 损坏/非 PDF → 诚实降级
    return d


def read_result(out):
    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    tmp = tempfile.mkdtemp(prefix="selftest-library-")

    # 1 帮助
    r = run_main(["--help"])
    check("help_exit0", r.returncode == 0, "exit=%d" % r.returncode)

    # 2 缺输入
    out_missing = os.path.join(tmp, "out_missing")
    r = run_main(["--input", os.path.join(tmp, "nope"), "--output", out_missing,
                  "--work", os.path.join(tmp, "work_missing")])
    rr = read_result(out_missing) if os.path.exists(os.path.join(out_missing, "result.json")) else None
    check("missing_input_status", rr is not None and rr.get("status") == "input_error",
          "status=%s" % (rr.get("status") if rr else "no-result"))
    check("missing_input_exit0", r.returncode == 0, "exit=%d" % r.returncode)

    # 3 空目录
    out_empty = os.path.join(tmp, "out_empty")
    empty_in = os.path.join(tmp, "empty_in")
    os.makedirs(empty_in)
    r = run_main(["--input", empty_in, "--output", out_empty,
                  "--work", os.path.join(tmp, "work_empty")])
    rr = read_result(out_empty)
    check("empty_status", rr.get("status") == "insufficient_evidence",
          "status=%s" % rr.get("status"))

    # 4 坏 case.json
    out_bad = os.path.join(tmp, "out_bad")
    bad_in = os.path.join(tmp, "bad_in")
    os.makedirs(bad_in)
    with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    r = run_main(["--input", bad_in, "--output", out_bad, "--work", os.path.join(tmp, "work_bad")])
    rr = read_result(out_bad)
    check("badjson_no_crash", r.returncode == 0, "exit=%d" % r.returncode)
    check("badjson_degraded", any("case.json" in d for d in rr.get("degradations", [])),
          "degradations=%s" % rr.get("degradations"))

    # 5 正常输入 + 幂等
    inp = make_input(tmp)
    out1 = os.path.join(tmp, "out1")
    work1 = os.path.join(tmp, "work1")
    run_main(["--input", inp, "--output", out1, "--work", work1])
    r1 = read_result(out1)
    check("result_has_case_id", r1.get("case_id") == "selftest-case-001")
    check("result_valid_json", isinstance(r1, dict) and "status" in r1 and "sources" in r1
          and "uncertainty" in r1 and "change_log" in r1 and "pending_human" in r1)
    check("contract_fields_present", all(k in r1 for k in ("library", "evidence",
                                                            "operations", "failures")))
    check("catalog_covers_all_files", len(r1["library"]["catalog"]) == 5,
          "catalog=%d" % len(r1["library"]["catalog"]))
    dups = r1["failures"]["duplicates"]
    check("duplicate_detected", len(dups) == 1 and dups[0]["duplicate_of"] == "paper_copy.txt",
          "dups=%s" % dups)
    check("corrupt_pdf_degraded", any("pdf-" in e["error"] for e in r1["failures"]["skipped"]),
          "skipped=%s" % r1["failures"]["skipped"])
    check("early_skeleton_logged", any(e.get("phase") == "1_skeleton" for e in r1["change_log"]))
    check("budget_recorded", r1.get("runtime", {}).get("budget", {}).get("total_seconds") == 900)
    check("request_budget_recorded", r1.get("runtime", {}).get("budget", {}).get("max_model_requests") == 64)
    check("bib_metadata_extracted",
          any(rec["source"] == "refs.bib" and rec["metadata"].get("title") == "Deep Learning for Vision"
              for rec in r1["library"]["catalog"]))
    found_evidence = False
    for s in r1["sources"]:
        for q in s.get("quotes", []):
            if q.get("field") == "title" and q.get("quote"):
                found_evidence = True
    check("evidence_locator_quote", found_evidence)

    out2 = os.path.join(tmp, "out2")
    run_main(["--input", inp, "--output", out2, "--work", os.path.join(tmp, "work2")])
    r2 = read_result(out2)

    def strip(rec):
        out = {}
        for k, v in rec.items():
            if k in ("abs", "mtime", "text_preview", "text_len", "operation"):
                continue
            if k == "permissions" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk != "mtime"}
            out[k] = v
        return out
    cat1 = json.dumps([strip(x) for x in r1["library"]["catalog"]], sort_keys=True, ensure_ascii=False)
    cat2 = json.dumps([strip(x) for x in r2["library"]["catalog"]], sort_keys=True, ensure_ascii=False)
    check("idempotent_catalog", cat1 == cat2)

    # 6 --apply 幂等（跑两次）
    outa = os.path.join(tmp, "outa")
    worka = os.path.join(tmp, "worka")
    run_main(["--input", inp, "--output", outa, "--work", worka, "--apply"])
    ra = read_result(outa)
    lib_files = []
    for root, _, files in os.walk(os.path.join(outa, "library")):
        for f in files:
            lib_files.append(os.path.relpath(os.path.join(root, f), os.path.join(outa, "library")))
    check("apply_created_files", len(lib_files) >= 3, "library_files=%d" % len(lib_files))
    check("apply_phase_recorded", any(p["phase"] == "4_apply" for p in ra["runtime"]["actual"]["phases"]))
    check("skeleton_written_at", bool(ra["runtime"]["actual"].get("skeleton_written_at")))
    run_main(["--input", inp, "--output", outa, "--work", worka, "--apply"])
    rb = read_result(outa)
    already = [op for op in rb.get("operations", []) if op.get("action") == "already_present"]
    check("apply_idempotent", len(already) >= 1, "already_present=%d" % len(already))
    check("rollback_written", os.path.exists(os.path.join(worka, "rollback.json")))

    failed = [c for c in CHECKS if not c[1]]
    print("\n%d checks, %d failed" % (len(CHECKS), len(failed)))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
