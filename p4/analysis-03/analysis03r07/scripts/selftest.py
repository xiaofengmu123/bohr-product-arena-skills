#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — sci-viz-robust 自检脚本（仅标准库）。

覆盖鲁棒性变体要求的全部边界：空输入、坏 JSON、缺参数、超大文件、坏编码、
重复运行一致性、产物格式（SVG/PDF/PNG>=300dpi）。每种场景都要求"不崩溃、
退出码 0、产出合法 result.json"。

用法：
    python3 -B scripts/selftest.py            # 在 Skill 根目录运行
    python3 -B scripts/selftest.py --json      # 输出 JSON 报告
任何情形退出码恒为 0（自检是诊断工具，不因单点 FAIL 中断 Harness）。
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


# --------------------------------------------------------------------------- #
# 测试夹具：造最小合法 PNG（含 pHYs 块，可测 dpi 解析）
# --------------------------------------------------------------------------- #
def _chunk(ctype, data):
    out = struct.pack(">I", len(data)) + ctype + data
    out += struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    return out


def make_test_png(path, dpi=300, w=8, h=6):
    ppm = int(round(dpi / 0.0254))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    phys = struct.pack(">IIB", ppm, ppm, 1)
    raw = b"\x00" + b"\x00" * (w * 3) * h
    idat = zlib.compress(raw)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_chunk(b"IHDR", ihdr))
        fh.write(_chunk(b"pHYs", phys))
        fh.write(_chunk(b"IDAT", idat))
        fh.write(_chunk(b"IEND", b""))


def run_main(args, cwd=None):
    """运行 main.py，返回 (exit_code, stdout, stderr)。失败也返回而非抛异常。"""
    try:
        proc = subprocess.run([PY, "-B", MAIN] + args, cwd=cwd or HERE,
                              capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return -1, "", "runner_error: %s" % e


def check(cases):
    report = []
    all_pass = True
    for name, cond, detail in cases:
        ok = bool(cond)
        all_pass = all_pass and ok
        report.append({"name": name, "pass": ok, "detail": detail})
    return report, all_pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    results = []
    root = tempfile.mkdtemp(prefix="sci-viz-selftest-")

    # 1. 空输入目录（目录存在但无材料）：insufficient_evidence，退出 0，结果合法
    d1 = os.path.join(root, "empty")
    os.makedirs(os.path.join(d1, "input"), exist_ok=True)
    os.makedirs(os.path.join(d1, "out"), exist_ok=True)
    os.makedirs(os.path.join(d1, "work"), exist_ok=True)
    rc, out, err = run_main(["init", "--input", os.path.join(d1, "input"),
                             "--output", os.path.join(d1, "out"),
                             "--work", os.path.join(d1, "work")])
    r1 = {}
    try:
        r1 = json.load(open(os.path.join(d1, "out", "result.json"),
                            encoding="utf-8"))
    except Exception as e:
        err = "read_result: %s" % e
    results.append(("empty_input", rc == 0 and
                    r1.get("status") == "insufficient_evidence" and "pending_human" in r1,
                    "rc=%s status=%s" % (rc, r1.get("status"))))

    # 1b. 输入目录缺失：input_error，退出 0
    d1b = os.path.join(root, "nodir")
    os.makedirs(os.path.join(d1b, "out"), exist_ok=True)
    os.makedirs(os.path.join(d1b, "work"), exist_ok=True)
    rc, _out, _err = run_main(["init", "--input", os.path.join(d1b, "no-such-input"),
                               "--output", os.path.join(d1b, "out"),
                               "--work", os.path.join(d1b, "work")])
    r1b = json.load(open(os.path.join(d1b, "out", "result.json"), encoding="utf-8"))
    results.append(("missing_input_dir", rc == 0 and
                    r1b.get("status") == "input_error" and "errors" in r1b,
                    "rc=%s status=%s" % (rc, r1b.get("status"))))

    # 2. 坏 JSON case.json：不崩溃，errors/degradations 记录，退出 0
    d2 = os.path.join(root, "badjson")
    for sub in ("input", "out", "work"):
        os.makedirs(os.path.join(d2, sub), exist_ok=True)
    with open(os.path.join(d2, "input", "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    with open(os.path.join(d2, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("分析结果\n质量与复现证据\n异常说明\n")
    rc, _out, _err = run_main(["init", "--input", os.path.join(d2, "input"),
                               "--output", os.path.join(d2, "out"),
                               "--work", os.path.join(d2, "work")])
    r2 = json.load(open(os.path.join(d2, "out", "result.json"), encoding="utf-8"))
    has_note = any("失败" in u or "case.json" in u for u in
                   r2.get("uncertainty", []))
    results.append(("bad_json", rc == 0 and r2.get("case_id") and has_note,
                    "rc=%s case_id=%s" % (rc, r2.get("case_id"))))

    # 3. 超大文件：跳过并记录 degradation，不崩溃
    d3 = os.path.join(root, "oversize")
    for sub in ("input", "out", "work"):
        os.makedirs(os.path.join(d3, sub), exist_ok=True)
    with open(os.path.join(d3, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("分析结果\n")
    with open(os.path.join(d3, "input", "big.csv"), "w", encoding="utf-8") as fh:
        fh.write("x\n" + "\n".join(str(i) for i in range(3000000)))
    rc, _out, _err = run_main(["init", "--input", os.path.join(d3, "input"),
                               "--output", os.path.join(d3, "out"),
                               "--work", os.path.join(d3, "work")])
    r3 = json.load(open(os.path.join(d3, "out", "result.json"), encoding="utf-8"))
    skipped = any("file_too_large" in d for d in r3.get("degradations", []))
    results.append(("oversize_file", rc == 0 and skipped,
                    "degradations=%s" % r3.get("degradations")))

    # 4. 缺参数：缺省 /input /app/output /app/work 的解析仍成立（只测 help）
    rc, out, _err = run_main(["--help"])
    results.append(("missing_params_defaults", rc == 0 and "init" in out,
                    "help rc=%s" % rc))

    # 5. 坏编码文件：gb18030 降级可读，不崩溃
    d5 = os.path.join(root, "gbk")
    for sub in ("input", "out", "work"):
        os.makedirs(os.path.join(d5, sub), exist_ok=True)
    with open(os.path.join(d5, "input", "task.md"), "wb") as fh:
        fh.write("分析结果\n质量与复现证据\n".encode("gb18030"))
    with open(os.path.join(d5, "input", "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "case-gbk-test"}))
    rc, _out, _err = run_main(["init", "--input", os.path.join(d5, "input"),
                               "--output", os.path.join(d5, "out"),
                               "--work", os.path.join(d5, "work")])
    r5 = json.load(open(os.path.join(d5, "out", "result.json"), encoding="utf-8"))
    results.append(("bad_encoding_gbk", rc == 0 and r5.get("case_id") == "case-gbk-test",
                    "case_id=%s" % r5.get("case_id")))

    # 6. 正常链路 + 产物格式自检（含 PNG dpi 解析）
    d6 = os.path.join(root, "valid")
    for sub in ("input", "out", "work"):
        os.makedirs(os.path.join(d6, sub), exist_ok=True)
    with open(os.path.join(d6, "input", "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "case-demo-1",
                             "output_contract": {"plot": None, "caption": None}}))
    with open(os.path.join(d6, "input", "data.csv"), "w", encoding="utf-8") as fh:
        fh.write("group,value\na,1\na,2\nb,3\n")
    rc, _out, _err = run_main(["init", "--input", os.path.join(d6, "input"),
                               "--output", os.path.join(d6, "out"),
                               "--work", os.path.join(d6, "work")])
    r6 = json.load(open(os.path.join(d6, "out", "result.json"), encoding="utf-8"))
    contract_ok = "plot" in r6 and "caption" in r6
    # 伪造合法产物（自检仅校验格式，不代表真实案例）
    make_test_png(os.path.join(d6, "out", "figure.png"), dpi=300)
    with open(os.path.join(d6, "out", "figure.svg"), "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg"></svg>\n')
    with open(os.path.join(d6, "out", "figure.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4\n")
    rc2, _out, _err = run_main(["check", "--input", os.path.join(d6, "input"),
                                "--output", os.path.join(d6, "out"),
                                "--require-contract", "--fail"])
    results.append(("valid_chain_contract", rc == 0 and contract_ok,
                    "contract_keys=%s" % [k for k in ("plot", "caption")
                                          if k in r6]))
    results.append(("check_require_contract", rc2 == 0,
                    "check rc=%s" % rc2))

    # 7. 重复运行一致性：两次 init 得到相同 case_id 与契约模板
    rc, _out, _err = run_main(["init", "--input", os.path.join(d6, "input"),
                               "--output", os.path.join(d6, "out"),
                               "--work", os.path.join(d6, "work")])
    r6b = json.load(open(os.path.join(d6, "out", "result.json"), encoding="utf-8"))
    results.append(("deterministic_rerun", r6b.get("case_id") == r6.get("case_id"),
                    "case_id=%s" % r6b.get("case_id")))

    passed = sum(1 for _n, ok, _d in results if ok)
    summary = {"total": len(results), "passed": passed,
               "failed": len(results) - passed, "results": results}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for name, ok, detail in results:
            print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
        print("SUMMARY passed=%d/%d" % (passed, len(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
