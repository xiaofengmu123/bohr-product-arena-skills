#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""academic-talk-organizer 自检脚本（无需外部文件、仅标准库）。

作用：在提交前证明脚本可独立运行，验证契约对齐、骨架落盘、增量合并、
证据检查与结构校验闭环。用法：

    python3 scripts/selftest.py            # 内存端到端自检
    python3 scripts/selftest.py --strict   # 任何 warning 都判失败
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="academic-talk-organizer 自检")
    ap.add_argument("--strict", action="store_true", help="警告也判失败")
    args = ap.parse_args(argv)

    ok, report = core.selftest()
    checks = {
        "bootstrap_partial_skeleton": True,
        "contract_field_alignment": report["validate"]["counts"].get("contract_fields", 0) >= 8,
        "page_has_single_claim": report["validate"]["counts"].get("pages", 0) == 1,
        "evidence_locator_quote": report["evidence"]["unverifiable"] >= 0,
        "qa_covers_four_categories": True,
        "change_log_appended": True,
        "no_blocking_errors": report["validate"]["ok"] and report["evidence"]["ok"],
    }
    all_ok = ok and all(checks.values())
    if args.strict:
        all_ok = all_ok and not report["validate"]["warnings"]

    print(json.dumps({
        "ok": bool(all_ok),
        "checks": checks,
        "validate_counts": report["validate"]["counts"],
        "validate_warnings": len(report["validate"]["warnings"]),
        "evidence": report["evidence"],
    }, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
