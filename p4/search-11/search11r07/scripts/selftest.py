#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检脚本。

在临时目录中构造合成输入，逐项验证：
  1. --help 退出码 0；
  2. scaffold 空目录 → insufficient_evidence；
  3. scaffold 坏 JSON → 降级不崩溃、status 合法；
  4. scaffold 坏编码文本 → 容错读取、记录 encoding/read_error；
  5. 超大文件 → 截断并记录，不崩溃；
  6. normalize 常见公开号/申请号/优先权号格式；
  7. extract 从 OCR 粘连文本抽号码；
  8. family 按共用优先权归并；
  9. finalize + validate 打印 VALID；
  10. 缺参数（未知子命令）退出码 0。

用法：python3 scripts/selftest.py [--quiet]
退出码：全部通过为 0，任一项失败为 1（供 CI/自查判断）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("[PASS]" if ok else "[FAIL]", name,
                       (" — " + detail) if detail else ""))


def run(args, cwd=None):
    """运行 main.py，返回 (returncode, stdout, stderr)。"""
    proc = subprocess.run(
        [PY, "-B", MAIN] + args,
        cwd=cwd or HERE, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def write(path, content, mode="w", encoding="utf-8"):
    if "b" in mode:
        data = content if isinstance(content, (bytes, bytearray)) else content.encode(encoding or "utf-8")
        with open(path, mode) as fh:
            fh.write(data)
    else:
        with open(path, mode, encoding=encoding) as fh:
            fh.write(content)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_help():
    code, out, _ = run(["--help"])
    check("--help exit 0", code == 0, "rc=%d" % code)


def test_empty_input(tmp):
    inp = os.path.join(tmp, "empty_in")
    out = os.path.join(tmp, "empty_out")
    work = os.path.join(tmp, "empty_work")
    os.makedirs(inp)
    code, _, _ = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("empty input -> insufficient_evidence", code == 0 and r.get("status") == "insufficient_evidence",
          "rc=%d status=%s" % (code, r.get("status")))


def test_bad_json(tmp):
    inp = os.path.join(tmp, "bad_in")
    out = os.path.join(tmp, "bad_out")
    work = os.path.join(tmp, "bad_work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{not valid json")
    code, _, _ = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    has_err = any(e.get("item") == "case_json_unreadable" for e in r.get("errors", []))
    check("bad json -> graceful degrade", code == 0 and r.get("status") in ("partial", "input_error") and has_err,
          "rc=%d status=%s" % (code, r.get("status")))


def test_bad_encoding(tmp):
    inp = os.path.join(tmp, "enc_in")
    out = os.path.join(tmp, "enc_out")
    work = os.path.join(tmp, "enc_work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), json.dumps({"case_id": "enc-case", "output_contract": {"extra": []}}))
    # 单字节 0xE9 在 utf-8 下非法，应触发 latin-1 兜底
    write(os.path.join(inp, "notes.txt"), b"caf\xe9 \xe9tude\n", mode="wb")
    code, _, _ = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    inv = r.get("input_inventory", [])
    txt = [i for i in inv if i.get("rel") == "notes.txt"]
    ok = code == 0 and len(txt) == 1 and (txt[0].get("read_error") or txt[0].get("encoding") in ("latin-1",))
    check("bad encoding -> tolerate", ok, "rc=%d enc=%s" % (code, txt[0].get("encoding") if txt else "?"))


def test_oversized(tmp):
    inp = os.path.join(tmp, "big_in")
    out = os.path.join(tmp, "big_out")
    work = os.path.join(tmp, "big_work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), json.dumps({"case_id": "big-case"}))
    write(os.path.join(inp, "big.txt"), "x" * (3 * 1024 * 1024), mode="wb")
    code, _, _ = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    inv = r.get("input_inventory", [])
    big = [i for i in inv if i.get("rel") == "big.txt"]
    ok = code == 0 and len(big) == 1 and (big[0].get("read_error") or {}).get("truncated")
    check("oversized file -> truncate+record", ok, "rc=%d" % code)


def test_normalize(tmp):
    cases = [
        ("US 12,345,678 B2", "grant"),
        ("WO 2023/123456 A1", "publication"),
        ("EP 3 456 789 A1", "publication"),
        ("US 16/123,456", "application"),
        ("US 62/345,678", "priority"),
        ("CN 109876543 A", "publication"),
        ("KR 10-2019-0123456 B1", "grant"),
    ]
    code, out, _ = run(["normalize", "--numbers"] + [c[0] for c in cases])
    ok_parse = code == 0
    data = json.loads(out)
    matches = 0
    for raw_str, want in cases:
        for d in data:
            if d.get("raw") == raw_str:
                if d.get("number_type") == want:
                    matches += 1
                break
    check("normalize common formats", ok_parse and matches == len(cases),
          "%d/%d matched" % (matches, len(cases)))


def test_extract(tmp):
    text = "优先权 WO2023/123456A1 与公开号 US 12,345,678 B2 属于不同族，申请号 US16/123,456。"
    code, out, _ = run(["extract", "--text", text])
    data = json.loads(out)
    norms = {h["normalized"]["normalized"] for h in data.get("hits", [])}
    ok = code == 0 and "WO2023/123456A1" in norms and "US12345678B2" in norms and "US16/123456" in norms
    check("extract OCR-merged candidates", ok, "norms=%s" % sorted(norms))


def test_family(tmp):
    rec = os.path.join(tmp, "records.json")
    records = [
        {"record_id": "r1", "publication_number": {"raw": "US 12,345,678 B2", "normalized": "US12345678B2", "authority": "US", "kind_code": "B2"},
         "application_number": {"raw": "US 16/123,456", "normalized": "US16/123456"}, "priority_number": None},
        {"record_id": "r2", "publication_number": {"raw": "EP 3 456 789 A1", "normalized": "EP3456789A1", "authority": "EP", "kind_code": "A1"},
         "application_number": None, "priority_number": {"raw": "US 16/123,456", "normalized": "US16/123456"}},
        {"record_id": "r3", "publication_number": {"raw": "WO 2023/123456 A1", "normalized": "WO2023123456A1", "authority": "WO", "kind_code": "A1"},
         "application_number": None, "priority_number": None},
    ]
    write(rec, json.dumps(records, ensure_ascii=False))
    code, out, _ = run(["family", "--file", rec])
    data = json.loads(out)
    fams = data.get("families", [])
    merged = any(len(f.get("members", [])) == 2 for f in fams)
    all_obs = all(f.get("observed") is True and f.get("exhaustive") is False for f in fams)
    check("family merge by shared priority", code == 0 and merged and all_obs and len(fams) == 2,
          "families=%d" % len(fams))


def test_finalize_validate(tmp):
    out = os.path.join(tmp, "fin_out")
    work = os.path.join(tmp, "fin_work")
    inp = os.path.join(tmp, "fin_in")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), json.dumps({"case_id": "fin-case"}))
    run(["scaffold", "--input", inp, "--output", out, "--work", work])
    code, _, _ = run(["finalize", "--status", "ok", "--output", out, "--work", work])
    report_ok = os.path.exists(os.path.join(out, "report.md"))
    vcode, vout, _ = run(["validate", "--output", out, "--work", work])
    check("finalize + validate VALID", code == 0 and report_ok and vcode == 0 and "VALID" in vout,
          "rc=%d valid_out=%r" % (code, "VALID" in vout))


def test_unknown_subcommand(tmp):
    code, _, _ = run(["no-such-command"])
    check("unknown subcommand -> exit 0", code == 0, "rc=%d" % code)


def main():
    quiet = "--quiet" in sys.argv
    tmp = tempfile.mkdtemp(prefix="patent-selftest-")
    try:
        test_help()
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_bad_encoding(tmp)
        test_oversized(tmp)
        test_normalize(tmp)
        test_extract(tmp)
        test_family(tmp)
        test_finalize_validate(tmp)
        test_unknown_subcommand(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("")
    print("selftest: %d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed and not quiet:
        print("failed:")
        for name, _ok, detail in failed:
            print("  - %s (%s)" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
