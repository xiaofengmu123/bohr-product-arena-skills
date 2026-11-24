#!/usr/bin/env python3
"""selftest.py — sci-library-organizer 自检脚本（仅标准库）。

覆盖：--help 退出码、空目录 insufficient_evidence、缺输入 input_error、
坏 case.json 降级、result.json 合法性与契约全字段、幂等重跑、
--apply 后重复 --apply already_present、证据 locator+quote 存在性。

用法：python3 -B scripts/selftest.py
退出码：0 表示全部通过，1 表示存在失败项（仅作自检信号，不影响 main.py 行为）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s%s" % (mark, name, ("  " + detail) if detail else ""))


def run_main(args, timeout=120):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True, timeout=timeout)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def strip_timestamps(doc):
    """递归去除时间戳类字段，用于幂等比较。"""
    if isinstance(doc, dict):
        return {k: strip_timestamps(v) for k, v in doc.items()
                if k not in ("ts", "generated_at", "started", "finished", "seconds", "runtime")}
    if isinstance(doc, list):
        return [strip_timestamps(v) for v in doc]
    return doc


def test_help():
    r = run_main(["--help"])
    record("help_exit_zero", r.returncode == 0, "exit=%d" % r.returncode)
    record("help_mentions_usage", "usage" in (r.stdout or "").lower(), "")


def test_missing_input(tmp):
    missing = os.path.join(tmp, "nope")
    out = os.path.join(tmp, "out_missing")
    work = os.path.join(tmp, "work_missing")
    r = run_main(["--input", missing, "--output", out, "--work", work])
    ok = r.returncode == 0
    res = load_json(os.path.join(out, "result.json"))
    ok = ok and res.get("status") == "input_error"
    record("missing_input_input_error", ok, "status=%s" % res.get("status"))


def test_empty_input(tmp):
    inp = os.path.join(tmp, "empty_in")
    os.makedirs(inp)
    out = os.path.join(tmp, "out_empty")
    work = os.path.join(tmp, "work_empty")
    r = run_main(["--input", inp, "--output", out, "--work", work])
    res = load_json(os.path.join(out, "result.json"))
    ok = r.returncode == 0 and res.get("status") == "insufficient_evidence"
    record("empty_input_insufficient", ok, "status=%s" % res.get("status"))


def test_bad_case_json(tmp):
    inp = os.path.join(tmp, "bad_in")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not json ]")
    with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write("A short note.\n")
    out = os.path.join(tmp, "out_bad")
    work = os.path.join(tmp, "work_bad")
    r = run_main(["--input", inp, "--output", out, "--work", work])
    res = load_json(os.path.join(out, "result.json"))
    degs = res.get("degradations") or []
    ok = (r.returncode == 0 and any("invalid" in str(d.get("issue", "")) for d in degs)
          and set(("library", "evidence", "operations", "failures")).issubset(res.keys()))
    record("bad_case_json_degrades", ok, "degradations=%d" % len(degs))


def test_result_contract_valid(tmp):
    inp = os.path.join(tmp, "good_in")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-selftest-001"}, fh, ensure_ascii=False)
    with open(os.path.join(inp, "a.pdf"), "w", encoding="utf-8") as fh:
        fh.write("%PDF-1.4 fake\n")
    with open(os.path.join(inp, "a_copy.pdf"), "w", encoding="utf-8") as fh:
        fh.write("%PDF-1.4 fake\n")  # 字节级重复
    out = os.path.join(tmp, "out_good")
    work = os.path.join(tmp, "work_good")
    r = run_main(["--input", inp, "--output", out, "--work", work])
    res = load_json(os.path.join(out, "result.json"))
    required = ("case_id", "status", "sources", "uncertainty", "change_log",
                "library", "evidence", "operations", "failures")
    ok = (r.returncode == 0 and all(k in res for k in required)
          and res["case_id"] == "case-selftest-001"
          and res["status"] in ("ok", "partial", "insufficient_evidence", "input_error"))
    record("result_contract_valid", ok, "case_id=%s status=%s" % (res.get("case_id"), res.get("status")))
    # 重复文件应被识别（a_copy.pdf duplicate_of a.pdf）
    dup = [c for c in res["library"]["catalog"] if c.get("duplicate_of")]
    record("duplicate_detected", len(dup) >= 1, "duplicates=%d" % len(dup))
    # 每个 catalog 条目若结论非空应带证据（document_type 至少 filename locator）
    ev_ok = all(
        (not c.get("document_type") or c.get("evidence"))
        for c in res["library"]["catalog"]
    )
    record("evidence_present_for_types", ev_ok, "")


def test_idempotency(tmp):
    inp = os.path.join(tmp, "idem_in")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-idem-001"}, fh, ensure_ascii=False)
    for n in ("b.txt", "a.pdf"):
        with open(os.path.join(inp, n), "w", encoding="utf-8") as fh:
            fh.write("content of %s\n" % n)
    out1 = os.path.join(tmp, "idem_out1")
    out2 = os.path.join(tmp, "idem_out2")
    work = os.path.join(tmp, "idem_work")
    run_main(["--input", inp, "--output", out1, "--work", work])
    run_main(["--input", inp, "--output", out2, "--work", work])
    r1 = load_json(os.path.join(out1, "result.json"))
    r2 = load_json(os.path.join(out2, "result.json"))
    same = (strip_timestamps(r1["library"]["catalog"])
            == strip_timestamps(r2["library"]["catalog"])
            and strip_timestamps(r1["operations"]) == strip_timestamps(r2["operations"]))
    record("idempotent_catalog_operations", same, "")


def test_apply_idempotent(tmp):
    inp = os.path.join(tmp, "apply_in")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-apply-001"}, fh, ensure_ascii=False)
    with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write("some text\n")
    out = os.path.join(tmp, "apply_out")
    work = os.path.join(tmp, "apply_work")
    run_main(["--input", inp, "--output", out, "--work", work, "--apply"])
    run_main(["--input", inp, "--output", out, "--work", work, "--apply"])
    res = load_json(os.path.join(out, "result.json"))
    copied = [op for op in res["operations"] if op.get("state") == "copied"]
    present = [op for op in res["operations"] if op.get("state") == "already_present"]
    ok = len(present) >= 1 and len(copied) == 0
    record("apply_idempotent_already_present", ok,
           "copied=%d already_present=%d" % (len(copied), len(present)))


def main():
    tmp = tempfile.mkdtemp(prefix="selftest-lib-")
    try:
        test_help()
        test_missing_input(tmp)
        test_empty_input(tmp)
        test_bad_case_json(tmp)
        test_result_contract_valid(tmp)
        test_idempotency(tmp)
        test_apply_idempotent(tmp)
    finally:
        pass  # 保留 tmp 便于排查；正式运行可自行清理
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d passed, %d failed" % (len(RESULTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
