#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检脚本（纯标准库，可独立运行）。

覆盖：CLI 可调用性、scaffold 早落盘、契约回填、号码规范化、号码抽取、
同族归并、checkpoint、finalize、validate 以及三种异常输入（空目录/坏 JSON/缺输入）。

用法：python scripts/selftest.py   （退出码 0=全部通过，非 0=有失败）
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(SCRIPT_DIR, "main.py")
PY = sys.executable

RESULTS = []


def run(args, cwd=None):
    """运行 main.py，返回 CompletedProcess。强制 UTF-8 IO，避免 Windows 控制台编码干扰。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([PY, "-B", MAIN] + args, capture_output=True, text=True,
                          encoding="utf-8", cwd=cwd, env=env)


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%-6s %s%s" % ("PASS" if cond else "FAIL", name, ("  | " + detail) if detail else ""))


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main():
    print("== selftest: sci-patent-search ==")

    # 0) CLI 可调用
    p = run(["--help"])
    check("main.py --help exit 0", p.returncode == 0, "rc=%d" % p.returncode)

    tmp = tempfile.mkdtemp(prefix="skilltest_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")

    # 1) 空输入目录
    os.makedirs(inp, exist_ok=True)
    p = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("scaffold empty dir exit 0", p.returncode == 0)
    check("empty dir -> insufficient_evidence", r.get("status") == "insufficient_evidence")
    check("empty dir has pending_human", bool(r.get("pending_human")))

    # 2) 坏 JSON
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    p = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("bad JSON exit 0", p.returncode == 0)
    check("bad JSON recorded in errors", any(e.get("item") == "case_json_unreadable"
                                              for e in r.get("errors", [])))

    # 3) 缺输入（目录不存在）
    p = run(["scaffold", "--input", os.path.join(tmp, "nope"), "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("missing dir exit 0", p.returncode == 0)
    check("missing dir -> insufficient_evidence", r.get("status") == "insufficient_evidence")

    # 4) 正常 case.json + output_contract
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "case-demo-42",
        "research_question": "WO/US 同族与法律状态",
        "output_contract": {
            "target_patents": [{"number": "US 10,000,000 B2"}],
            "report_sections": {"family": None, "status": None},
            "notes": None,
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n\n检索主题：光刻胶")
    p = run(["scaffold", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("valid case exit 0", p.returncode == 0)
    check("case_id filled", r.get("case_id") == "case-demo-42")
    check("contract fields present",
          all(k in r for k in ("target_patents", "report_sections", "notes")))
    check("budget ledger present", isinstance(r.get("budget", {}).get("phases"), list)
          and r["budget"]["phases"])

    # 5) 号码规范化
    p = run(["normalize", "--text", "US 10,000,000 B2"])
    norm = json.loads(p.stdout)[0]
    check("normalize US grant", norm.get("normalized") == "US10000000B2"
          and norm.get("number_type") == "grant" and norm.get("format_ok"))
    p = run(["normalize", "--text", "WO 2020/123456 A1"])
    norm = json.loads(p.stdout)[0]
    check("normalize WO pub", norm.get("normalized") == "WO2020123456A1"
          and norm.get("number_type") == "publication")
    p = run(["normalize", "--text", "US 2019/0123456 A1"])
    norm = json.loads(p.stdout)[0]
    check("normalize US pub", norm.get("number_type") == "publication"
          and norm.get("normalized") == "US20190123456A1")
    p = run(["normalize", "--text", "US 15/123,456"])
    norm = json.loads(p.stdout)[0]
    check("normalize US serial", norm.get("number_type") == "application")
    p = run(["normalize", "--text", "CN 109876543.2"])
    norm = json.loads(p.stdout)[0]
    check("normalize CN app", norm.get("country") == "CN"
          and norm.get("number_type") in ("application", "publication"))

    # 6) 号码抽取
    p = run(["extract", "--text", "本文讨论 US 10,000,000 B2 与 WO 2020/123456 A1 的关系。"])
    hits = json.loads(p.stdout)
    check("extract two numbers", len(hits) == 2, "n=%d" % len(hits))

    # 7) 同族归并
    records = [
        {"record_id": "r1", "priority_number": "US 62/100,000", "publication_number": "WO 2020/123456 A1"},
        {"record_id": "r2", "priority_number": "US 62/100,000", "publication_number": "US 2019/0123456 A1"},
        {"record_id": "r3", "priority_number": "CN 2019/999999", "publication_number": "CN 109876543 A"},
    ]
    rec_path = os.path.join(work, "records.json")
    with open(rec_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False)
    p = run(["family", "--file", rec_path])
    fam = json.loads(p.stdout)
    check("family groups 3->2", len(fam["families"]) == 2, "n=%d" % len(fam["families"]))
    check("family observed+exhaustive", all(f["family_type"] == "observed"
          and f["exhaustive"] is False for f in fam["families"]))
    sizes = sorted(f["member_count"] for f in fam["families"])
    check("family merge by priority", sizes == [1, 2], "sizes=%s" % sizes)

    # 8) checkpoint + finalize + validate
    p = run(["checkpoint", "--note", "self-test", "--status", "partial",
             "--output", out, "--work", work])
    check("checkpoint exit 0", p.returncode == 0)
    p = run(["finalize", "--status", "partial", "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("finalize exit 0", p.returncode == 0)
    check("runtime elapsed set", r.get("runtime", {}).get("elapsed_seconds") is not None)
    check("report.md exists", os.path.exists(os.path.join(out, "report.md")))
    p = run(["validate", "--output", out, "--work", work])
    check("validate prints VALID", p.returncode == 0 and "VALID" in p.stdout)

    # 9) budget 命令
    p = run(["budget", "--output", out, "--work", work])
    check("budget exit 0", p.returncode == 0 and "elapsed=" in p.stdout)

    # 汇总
    failed = [n for n, ok, _d in RESULTS if not ok]
    print("\n== %d/%d passed ==" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
