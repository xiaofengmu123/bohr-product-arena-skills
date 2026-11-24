#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检脚本（纯标准库）。

覆盖：空输入目录、坏 JSON、缺输入、含 output_contract 的最小案例、
专利号规范化、号码抽取、同族归并、finalize+validate。

退出码 0 = 全部通过；非 0 = 有失败项。可在提交前与交付后各跑一次。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import patentlib  # noqa: E402

MAIN = os.path.join(SCRIPT_DIR, "main.py")
PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("PASS  %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        FAIL.append(name)
        print("FAIL  %s%s" % (name, (" — " + detail) if detail else ""))


def run_cli(*argv):
    return subprocess.run(
        [sys.executable, MAIN] + list(argv),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def write(p, name, content, encoding="utf-8"):
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, name), "w", encoding=encoding) as fh:
        fh.write(content)


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        # 1) --help 退出码 0
        r = run_cli("--help")
        check("cli --help exit 0", r.returncode == 0, "rc=%d" % r.returncode)

        # 2) 空输入目录
        empty_in = os.path.join(tmp, "empty_in")
        empty_out = os.path.join(tmp, "empty_out")
        empty_work = os.path.join(tmp, "empty_work")
        os.makedirs(empty_in, exist_ok=True)
        r = run_cli("scaffold", "--input", empty_in,
                    "--output", empty_out, "--work", empty_work)
        check("scaffold empty dir rc=0", r.returncode == 0)
        res, err = patentlib.load_json(os.path.join(empty_out, "result.json"))
        check("scaffold empty dir valid json", err is None and isinstance(res, dict))
        check("scaffold empty dir status", res and res.get("status") == "insufficient_evidence")

        # 3) 坏 JSON
        bad_in = os.path.join(tmp, "bad_in")
        write(bad_in, "case.json", "{ this is not json")
        bad_out = os.path.join(tmp, "bad_out")
        bad_work = os.path.join(tmp, "bad_work")
        r = run_cli("scaffold", "--input", bad_in,
                    "--output", bad_out, "--work", bad_work)
        check("scaffold bad json rc=0", r.returncode == 0)
        res, err = patentlib.load_json(os.path.join(bad_out, "result.json"))
        check("scaffold bad json still valid json", err is None and isinstance(res, dict))
        check("scaffold bad json recorded error",
              res and any(e.get("item") == "case_json_unreadable"
                          for e in res.get("errors", [])))

        # 4) 缺输入目录（路径不存在）
        missing_out = os.path.join(tmp, "missing_out")
        missing_work = os.path.join(tmp, "missing_work")
        r = run_cli("scaffold", "--input", os.path.join(tmp, "no_such_dir"),
                    "--output", missing_out, "--work", missing_work)
        check("scaffold missing input rc=0", r.returncode == 0)
        res, err = patentlib.load_json(os.path.join(missing_out, "result.json"))
        check("scaffold missing input insufficient_evidence",
              res and res.get("status") == "insufficient_evidence")

        # 5) 含 output_contract 的最小案例
        mini_in = os.path.join(tmp, "mini_in")
        contract = {"target_patents": None, "required_literature": None,
                    "custom_key": None}
        write(mini_in, "case.json", json.dumps({
            "case_id": "case-selftest",
            "output_contract": contract,
        }, ensure_ascii=False))
        write(mini_in, "task.md", "# 测试任务\n公开号 US 12,345,678 B2")
        mini_out = os.path.join(tmp, "mini_out")
        mini_work = os.path.join(tmp, "mini_work")
        r = run_cli("scaffold", "--input", mini_in,
                    "--output", mini_out, "--work", mini_work)
        check("scaffold contract rc=0", r.returncode == 0)
        res, _ = patentlib.load_json(os.path.join(mini_out, "result.json"))
        check("scaffold contract case_id", res.get("case_id") == "case-selftest")
        check("scaffold contract keys filled",
              res and all(k in res for k in contract))

        # 6) 规范化
        cases = {
            "US 12,345,678 B2": ("grant", "US12345678B2"),
            "US 16/123,456": ("application", "US16123456"),
            "US 62/123,456": ("provisional_priority", "US62123456"),
            "WO 2020/123456 A1": ("publication", "WO2020123456A1"),
            "EP 3 456 789 A1": ("publication", "EP3456789A1"),
            "CN 109 123 456 A": ("publication", "CN109123456A"),
        }
        for raw, (typ, norm) in cases.items():
            n = patentlib.normalize_patent_number(raw)
            check("normalize %s" % raw,
                  n["normalized"] == norm and n["number_type"] == typ,
                  "got %s/%s" % (n["number_type"], n["normalized"]))

        # 7) 抽取
        hits = patentlib.extract_numbers(
            "See US 12,345,678 B2 and WO 2020/123456 A1 and application US 16/123,456.")
        check("extract finds 3", len(hits) == 3, "got %d" % len(hits))
        check("extract carries locator+context",
              all("locator" in h and "context" in h for h in hits))

        # 8) 同族归并
        recs = [
            {"record_id": "r1", "application_number": "US 16/123,456"},
            {"record_id": "r2", "application_number": "US16123456"},
            {"record_id": "r3", "priority_number": "US 62/123,456"},
        ]
        fam = patentlib.group_families(recs)
        check("family merges shared app", fam["families"][0]["member_count"] == 2)
        check("family observed/exhaustive:false",
              fam["families"][0]["family_type"] == "observed"
              and fam["families"][0]["exhaustive"] is False)

        # 9) 证据溯源 reconcile
        res2 = dict(res)
        res2["conclusions"] = [{"statement": "S", "strength": "fact",
                                "status": "supported", "evidence_ids": ["E1"]}]
        res2["evidence_map"] = {"E1": {"quote": "United States Patent 12,345,678 B2",
                                       "locator": "materials/x.pdf#p2"}}
        res2 = patentlib.reconcile_evidence(res2)
        e1 = res2["evidence_map"].get("E1")
        check("reconcile quote_sha256", bool(e1.get("quote_sha256")))
        check("reconcile verification_status", e1.get("verification_status") == "unverified")
        check("reconcile cross-link", e1.get("conclusion_id") == "C1")
        check("reconcile evidence list", len(res2.get("evidence") or []) == 1)

        # 10) finalize + validate
        _write_json(os.path.join(mini_out, "result.json"), res2)
        r = run_cli("finalize", "--output", mini_out, "--work", mini_work)
        check("finalize rc=0", r.returncode == 0, "rc=%d" % r.returncode)
        check("finalize VALID", "VALID" in r.stdout)
        r = run_cli("validate", "--output", mini_out, "--work", mini_work)
        check("validate prints VALID", "VALID" in r.stdout)
        check("report.md generated", os.path.exists(os.path.join(mini_out, "report.md")))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 48)
    print("selftest: %d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: %s" % f)
        return 1
    return 0


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
