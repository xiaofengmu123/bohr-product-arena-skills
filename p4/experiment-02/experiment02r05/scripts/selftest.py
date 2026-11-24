#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 交付自检脚本（selftest.py）

职责：对照评分维度逐条校验 /app/output/result.json 的合法性、契约回填、证据结构、
增量记录与诚实标注。输出 PASS/FAIL/WARN 行；存在 FAIL 时退出码非 0。
仅依赖 Python 标准库；Python >= 3.8。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from review import (
        SEVEN_DIMENSIONS, VERDICTS, STATUSES, RESOURCE_STATES,
        validate_review_item, resolve_status,
    )
except ImportError:  # 相对导入失败时退回同目录绝对导入
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from review import (  # type: ignore
        SEVEN_DIMENSIONS, VERDICTS, STATUSES, RESOURCE_STATES,
        validate_review_item, resolve_status,
    )

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"


class Checker:
    def __init__(self):
        self.fails, self.warns, self.passes = [], [], []

    def check(self, name, ok, level="PASS"):
        if ok:
            self.passes.append(name)
        elif level == "WARN":
            self.warns.append(name)
        else:
            self.fails.append(name)

    def emit(self):
        for n in self.passes:
            print("PASS  %s" % n)
        for n in self.warns:
            print("WARN  %s" % n)
        for n in self.fails:
            print("FAIL  %s" % n)
        print("summary: %d pass / %d warn / %d fail" %
              (len(self.passes), len(self.warns), len(self.fails)))
        return 1 if self.fails else 0


def load_result(path):
    if not os.path.exists(path):
        return None, ["result_missing: %s" % path]
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), []
    except (OSError, ValueError) as exc:
        return None, ["result_unreadable: %s: %s" % (path, exc)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="自检 result.json")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    result, load_errs = load_result(os.path.join(args.output, "result.json"))
    c = Checker()
    for err in load_errs:
        c.check(err, False)
        print("FAIL  %s" % err)
        return c.emit()

    if not isinstance(result, dict):
        c.check("result_is_object", False)
        return c.emit()

    # 1. 基础字段
    c.check("case_id_present", bool(result.get("case_id")))
    c.check("status_valid", result.get("status") in STATUSES)

    # 2. 契约回填：至少存在实验方案/依据风险/确认项三个主产物区块之一
    has_plan = isinstance(result.get("experiment_plan"), dict)
    has_ev = isinstance(result.get("evidence_and_risks"), dict)
    has_conf = isinstance(result.get("confirmation"), dict)
    c.check("contract_main_blocks", has_plan and has_ev and has_conf)
    fid = result.get("contract_fidelity")
    c.check("contract_fidelity_present", isinstance(fid, dict) and bool(fid.get("field_count")))

    # 3. 元信息完整
    c.check("sources_present", isinstance(result.get("sources"), list))
    c.check("uncertainty_present", isinstance(result.get("uncertainty"), list))
    c.check("pending_human_present", isinstance(result.get("pending_human"), list))
    c.check("change_log_present", isinstance(result.get("change_log"), list) and result.get("change_log"))

    # 4. 七类审查覆盖
    review = result.get("review")
    if isinstance(review, dict):
        for dim in SEVEN_DIMENSIONS:
            items = review.get(dim)
            has_items = isinstance(items, list) and len(items) > 0
            if result.get("status") in ("insufficient_evidence", "input_error"):
                c.check("review_dim_%s" % dim, True, "WARN")
            else:
                c.check("review_dim_%s" % dim, has_items)
        for dim in SEVEN_DIMENSIONS:
            for item in review.get(dim, []) or []:
                for problem in validate_review_item(item):
                    c.check("review_item_%s: %s" % (dim, problem), False)
    else:
        c.check("review_is_object", False)

    # 5. 诚实标注
    if result.get("status") == "ok":
        if isinstance(review, dict):
            resolved = resolve_status(review)
            c.check("status_matches_review", resolved == "ok")
    for key in ("errors", "degradations"):
        val = result.get(key)
        c.check("%s_is_list" % key, isinstance(val, list))

    # 6. change_log 增量记录结构
    log = result.get("change_log") or []
    for entry in log:
        ok_entry = (isinstance(entry, dict) and entry.get("step") and entry.get("action"))
        c.check("change_log_entry_shape", ok_entry, "WARN")

    # 7. 确定性占位符不残留：结果中不应出现预写正式答案标记
    raw = json.dumps(result, ensure_ascii=False)
    for marker in ("正式答案", "prewritten_answer", "placeholder_final"):
        c.check("no_fabricated_marker_%s" % marker, marker not in raw, "WARN")

    return c.emit()


if __name__ == "__main__":
    sys.exit(main())
