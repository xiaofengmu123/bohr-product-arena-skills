#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在不依赖正式案例的前提下验证脚本契约与确定性。

覆盖场景（对应评分维度“可靠体验”与“运行稳定性”）：
  S1 正常输入：init 写出合法 result.json，契约字段齐备，status=partial，指纹一致；
  S2 确定性：同一输入 init 两次，语义内容指纹逐字节一致；
  S3 空输入目录：init 仍写合法 result.json，status=input_error，不抛异常；
  S4 坏 JSON：case.json 损坏，记录 errors/degradations，继续处理 task.md，不崩溃；
  S5 坏编码：task.md 含坏字节，read_text 兜底不崩溃；
  S6 patch + validate：深合并后 validate 无 issues；
  S7 CLI --help：main.py 与 selftest.py 可独立以 --help 运行（exit code 0）。

只依赖标准库；可直接运行：python3 -B scripts/selftest.py
也可由编排器触发：python3 -B scripts/main.py selftest
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


SYNTH_CASE = {
    "case_id": "synthetic-pt-en-001",
    "task": "已知项检索：从葡萄牙语研究问题出发，找回同一论文的葡语原文与英语版本。",
    "source_language": "pt",
    "target_language": "en",
    "output_contract": {
        "research_question": {"type": "object"},
        "terminology_table": {"type": "array"},
        "queries": {"type": "array"},
        "results": {"type": "array"},
        "evidence_map": {"type": "array"},
        "dedup": {"type": "array"},
        "missing": {"type": "array"},
        "conflicts": {"type": "array"},
    },
}

SYNTH_TASK = """# 跨语言文献检索（自检合成输入，非正式案例）
本文件仅用于自检，验证输入发现与容错读取。
case_id: synthetic-pt-en-001
"""


def _write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class Check:
    def __init__(self):
        self.passed = []
        self.failed = []

    def check(self, name, cond, detail=""):
        if cond:
            self.passed.append(name)
        else:
            self.failed.append((name, detail))

    def report(self):
        return self.passed, self.failed


def _cli_help(script):
    py = sys.executable
    try:
        proc = subprocess.run(
            [py, "-B", script, "--help"],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def run_selftest(quiet=False):
    checks = Check()
    root = tempfile.mkdtemp(prefix="xlang-selftest-")
    here = os.path.dirname(os.path.abspath(__file__))

    try:
        # ---- S1/S2：正常输入 + 确定性 ----
        norm = os.path.join(root, "in-normal")
        _write(os.path.join(norm, "case.json"), json.dumps(SYNTH_CASE, ensure_ascii=False))
        _write(os.path.join(norm, "task.md"), SYNTH_TASK)

        out1 = os.path.join(root, "out-1")
        out2 = os.path.join(root, "out-2")
        work1 = os.path.join(root, "work-1")
        work2 = os.path.join(root, "work-2")

        res1, errs1 = core.do_init(norm, out1, work1)
        res2, errs2 = core.do_init(norm, out2, work2)

        checks.check("S1_status_partial", res1.get("status") == "partial", res1.get("status"))
        checks.check("S1_case_id", res1.get("case_id") == "synthetic-pt-en-001", res1.get("case_id"))
        contract_fields = ["research_question", "terminology_table", "queries", "results",
                           "evidence_map", "dedup", "missing", "conflicts"]
        missing_fields = [f for f in contract_fields if f not in res1]
        checks.check("S1_contract_fields", not missing_fields, "missing=%s" % missing_fields)
        checks.check("S1_result_json_legal", os.path.isfile(os.path.join(out1, "result.json")))
        fp1 = res1.get("determinism_fingerprint")
        fp2 = res2.get("determinism_fingerprint")
        checks.check("S2_determinism_fingerprint", bool(fp1) and fp1 == fp2, "%s vs %s" % (fp1, fp2))
        checks.check("S2_semantic_identical",
                     core.canonicalize({k: v for k, v in res1.items() if k not in core.VOLATILE_KEYS})
                     == core.canonicalize({k: v for k, v in res2.items() if k not in core.VOLATILE_KEYS}))

        # ---- S3：空输入 ----
        empty = os.path.join(root, "in-empty")
        os.makedirs(empty, exist_ok=True)
        out3 = os.path.join(root, "out-3")
        res3, errs3 = core.do_init(empty, out3, os.path.join(root, "work-3"))
        checks.check("S3_input_error", res3.get("status") == "input_error", res3.get("status"))
        checks.check("S3_result_exists", os.path.isfile(os.path.join(out3, "result.json")))
        checks.check("S3_errors_recorded", bool(res3.get("errors")), str(res3.get("errors")))

        # ---- S4：坏 JSON，但有 task.md 可继续 ----
        bad = os.path.join(root, "in-badjson")
        _write(os.path.join(bad, "case.json"), "{ not valid json", encoding="utf-8")
        _write(os.path.join(bad, "task.md"), SYNTH_TASK)
        res4, errs4 = core.do_init(bad, os.path.join(root, "out-4"), os.path.join(root, "work-4"))
        checks.check("S4_no_crash", isinstance(res4, dict))
        checks.check("S4_error_recorded", any("bad_json" in e for e in (res4.get("errors") or [])),
                     str(res4.get("errors")))

        # ---- S5：坏编码 task.md ----
        badenc = os.path.join(root, "in-badenc")
        _write(os.path.join(badenc, "case.json"), json.dumps(SYNTH_CASE, ensure_ascii=False))
        _write_bytes(os.path.join(badenc, "task.md"), b"# task\n\xff\xfe broken \x80 bytes\n")
        res5, errs5 = core.do_init(badenc, os.path.join(root, "out-5"), os.path.join(root, "work-5"))
        checks.check("S5_no_crash", isinstance(res5, dict) and res5.get("case_id") == "synthetic-pt-en-001")

        # ---- S6：patch + validate ----
        patch_obj = {
            "status": "partial",
            "research_question": {"question": "synthetic", "known_item": True,
                                  "source_languages": ["pt"], "target_languages": ["en"]},
            "results": [{"title": "Synthetic Work", "work_key": "synthetic-work",
                         "body_language": "pt", "version": "original"}],
        }
        core.do_patch(out1, work1, patch_obj)
        issues = core.do_validate(norm, out1)
        checks.check("S6_validate_ok", not issues, "; ".join(issues))
        res_after = core.load_result(out1)[0]
        checks.check("S6_patch_merged", res_after.get("results") and res_after["results"][0]["title"] == "Synthetic Work")

        # ---- S7：CLI --help ----
        checks.check("S7_main_help", _cli_help(os.path.join(here, "main.py")))
        checks.check("S7_selftest_help", _cli_help(os.path.join(here, "selftest.py")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed, failed = checks.report()
    if not quiet:
        print("SELFTEST passed=%d failed=%d" % (len(passed), len(failed)))
        for name in passed:
            print("  [ok] %s" % name)
        for name, detail in failed:
            print("  [FAIL] %s  %s" % (name, detail))
    return not failed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="cross-lingual literature search skill selftest")
    parser.add_argument("--quiet", action="store_true", help="只输出摘要，不逐条打印")
    args = parser.parse_args()
    sys.exit(0 if run_selftest(quiet=args.quiet) else 1)
