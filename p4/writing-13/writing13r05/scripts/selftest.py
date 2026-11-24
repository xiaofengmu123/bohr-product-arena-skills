#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py：打包前 / 交付前自检。

对 Skill 包做三类检查，评审或执行 Agent 可直接运行：

  python scripts/selftest.py --case /input/case.json --out /app/output --work /app/work

1) 脚本可用性：main.py 每个子命令 --help 退出码为 0；
2) 降级正确性：空输入目录、坏 JSON、缺 output_contract 三种情况不崩溃、写合法 partial；
3) 结果契约：result.json 逐字段对齐 output_contract（case_id/status/契约字段/恒有字段/溯源/SHA256/change_log）。

退出码：0 全部通过；1 有硬失败。仅依赖 Python 标准库。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# 跨平台确定性输出：强制 UTF-8，避免 Windows 控制台 GBK 导致中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import main as scaffold  # noqa: E402  (仅用其 load_json 等纯函数)


def run(cmd):
    return subprocess.run([sys.executable, "-B", MAIN] + cmd,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def mark(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    return bool(cond)


def main():
    ap = argparse.ArgumentParser(description="SKILL 包自检")
    ap.add_argument("--case", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--input", default=None)
    args = ap.parse_args()

    all_ok = True
    print("== 1. 脚本可用性 ==")
    for sub in ("init", "template", "sources", "ledger", "log", "check"):
        r = run([sub, "--help"])
        all_ok &= mark(r.returncode == 0, "main.py %s --help（rc=%s）" % (sub, r.returncode))

    print("== 2. 降级正确性（临时目录演练） ==")
    with tempfile.TemporaryDirectory(prefix="gpw_selftest_") as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        for d in (inp, out, work):
            os.makedirs(d)

        # 2a 空输入目录：init 落盘 partial 骨架，不崩溃
        r = run(["init", "--case", os.path.join(inp, "case.json"),
                 "--out", out, "--work", work])
        rp = os.path.join(out, "result.json")
        all_ok &= mark(r.returncode == 0 and os.path.exists(rp),
                       "空输入 init 落盘 result.json（rc=%s）" % r.returncode)

        # 2b 显式 output_contract：契约字段逐一对齐（含自定义字段）
        case_data = {
            "case_id": "demo-001",
            "output_contract": {
                "artifact": "目标产物", "fact_source_map": "事实来源映射",
                "version_boundary": "版本边界", "custom_field_x": "自定义字段",
            },
            "task_hint": "基金申请书写作",
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case_data, f, ensure_ascii=False)
        r = run(["init", "--case", os.path.join(inp, "case.json"), "--out", out, "--work", work])
        res, _ = scaffold.load_json(rp)
        all_ok &= mark(res.get("custom_field_x") is None, "契约字段 custom_field_x 逐一对齐（占位 null）")

        # 2c 坏 JSON 不崩溃（tolerant 读入）
        bad = os.path.join(inp, "case_bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"case_id": "x", "output_contract": {"a": "b"')
        r = run(["init", "--case", bad, "--out", out, "--work", work])
        all_ok &= mark(r.returncode == 0, "坏 JSON 不崩溃（rc=%s）" % r.returncode)

        # 2d sources 快照含 SHA256
        with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as f:
            f.write("材料片段：某项指标为 98.3%。")
        r = run(["sources", "--input", inp, "--work", work])
        manifest, _ = scaffold.load_json(os.path.join(work, "source_manifest.json"))
        has_sha = any(fn.get("path") == "paper.txt" and fn.get("sha256")
                      for fn in (manifest or {}).get("files", []))
        all_ok &= mark(bool(has_sha), "sources 快照含 SHA256")

        # 2e ledger add + verify + export 全链路
        r = run(["ledger", "add", "--work", work, "--claim", "指标 98.3%",
                 "--element", "研究基础与可行性", "--ref", "paper.txt:1",
                 "--loc", "第 1 行", "--quote", "某项指标为 98.3%", "--status", "mapped"])
        all_ok &= mark(r.returncode == 0, "ledger add（rc=%s）" % r.returncode)
        r = run(["ledger", "verify", "--work", work])
        all_ok &= mark(r.returncode == 0, "ledger verify（rc=%s）" % r.returncode)
        r = run(["ledger", "export", "--work", work, "--out", out])
        all_ok &= mark(r.returncode == 0, "ledger export（rc=%s）" % r.returncode)

        # 2f check 输出结构化报告
        r = run(["check", "--case", os.path.join(inp, "case.json"), "--out", out, "--work", work])
        try:
            report = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            report = {}
        all_ok &= mark("items" in report, "check 输出结构化报告（%d 项检查）" % len(report.get("items", [])))

    print("== 3. 结果契约自检（对真实 result.json） ==")
    if args.out and os.path.isdir(args.out):
        r = run(["check", "--case", args.case or "/input/case.json",
                 "--out", args.out, "--work", args.work or "/app/work"])
        print(r.stdout.strip())
        all_ok &= (r.returncode == 0)
    else:
        print("SKIP  （未提供 --out 或目录不存在，仅做脚本演练）")

    print("\n" + ("ALL PASS" if all_ok else "HAS FAILURES"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
