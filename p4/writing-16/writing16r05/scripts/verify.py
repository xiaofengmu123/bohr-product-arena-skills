#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研成果转化写作 Skill：交付前自检（仅标准库）。

对 /app/output/result.json 与主产物做结构化校验，不修改内容，只输出
/app/output/verification_report.json 并以退出码表达结论：

  - 0 通过（或仅有无害告警）
  - 1 存在可修复缺口（status 建议仍为 partial / insufficient_evidence）
  - 2 运行错误（如 manifest 缺失）

检查项：
  1) result.json 可解析、含 case_id 与 status；
  2) output_contract 全部键都已出现（对比 manifest.contract_keys）；
  3) 主产物文件存在且非空；
  4) evidence_map / change_log 每项含 locator 与逐字 quote（若契约有该字段）；
  5) 源文数值在改稿中的保留情况（漏掉且未在 change_log 说明的列为告警）。

用法：
  python3 scripts/verify.py --work /app/work --output /app/output
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"
MAX_TEXT_BYTES = 2 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path):
    with open(path, "rb") as fh:
        data = fh.read(MAX_TEXT_BYTES + 1)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def locate_deliverable(result):
    """定位主产物：优先契约 deliverable.path，其次常见键。"""
    for key in ("deliverable", "deliverable_path", "output_path", "manuscript_path"):
        val = result.get(key)
        if isinstance(val, dict) and val.get("path"):
            return val["path"]
        if isinstance(val, str) and val.strip():
            return val.strip()
    for key in ("draft", "manuscript", "document"):
        val = result.get(key)
        if isinstance(val, dict) and val.get("path"):
            return val["path"]
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def resolve_abs(path, output_dir):
    if os.path.isabs(path):
        return path
    return os.path.join(output_dir, path)


def contract_keys_covered(result, manifest):
    missing = []
    for k in manifest.get("contract_keys") or []:
        if k not in result:
            missing.append(k)
    return missing


def check_evidence_entries(result):
    problems = []
    for key in ("evidence_map", "change_log", "sources", "uncertainty"):
        val = result.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            problems.append("%s_not_list" % key)
            continue
        for i, item in enumerate(val):
            if not isinstance(item, dict):
                problems.append("%s[%d]_not_object" % (key, i))
                continue
            if key in ("evidence_map", "change_log"):
                if not (item.get("locator") or item.get("source") or item.get("path")):
                    problems.append("%s[%d]_missing_locator" % (key, i))
                if not (item.get("quote") or item.get("original") or item.get("before")):
                    problems.append("%s[%d]_missing_quote" % (key, i))
    return problems


def num_tokens(text):
    return re.findall(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?![A-Za-z0-9_])", text or "")


def coverage_check(result, manifest, output_dir):
    """源文数值 vs 改稿数值：漏掉的数值若未在 change_log 中说明则列为告警。"""
    warnings = []
    anchors_path = os.path.join(manifest.get("work_dir", DEFAULT_WORK), "evidence_anchors.json")
    if not os.path.exists(anchors_path):
        return warnings
    anchors = load_json(anchors_path)
    source_nums = set()
    for s in anchors.get("sources", []):
        for n in s.get("numbers", []):
            source_nums.add(n["match"])
    dl = locate_deliverable(result)
    if not dl:
        return ["deliverable_path_unknown_cannot_run_coverage"]
    path = resolve_abs(dl, output_dir)
    if not os.path.exists(path):
        return ["deliverable_missing:%s" % dl]
    draft = read_text(path)
    draft_tokens = set(num_tokens(draft))
    change_log_text = json.dumps(result.get("change_log", []), ensure_ascii=False)
    removed_ack = set(num_tokens(change_log_text))
    # 归一化：去掉千分位与纯序号噪音，仅比较数值字符串
    missing = []
    for n in sorted(source_nums):
        core = n.replace(",", "")
        if core in draft_tokens or core in removed_ack:
            continue
        # 允许单位不同的同数值
        num = re.search(r"(\d+(?:\.\d+)?)", n)
        if num and num.group(1) in draft_tokens:
            continue
        missing.append(n)
    if missing:
        warnings.append("source_numbers_absent_from_draft(%d): %s"
                        % (len(missing), ", ".join(missing[:20])))
    return warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    args = ap.parse_args(argv)

    report = {"generated_at": utcnow(), "checks": [], "problems": [], "warnings": []}
    result_path = os.path.join(args.output, "result.json")
    manifest_path = os.path.join(args.work, "manifest.json")

    if not os.path.exists(manifest_path):
        report["problems"].append("manifest_missing")
        report["exit_code"] = 2
        _dump(report, args.output)
        return 2
    manifest = load_json(manifest_path)

    if not os.path.exists(result_path):
        report["problems"].append("result_json_missing")
        report["exit_code"] = 2
        _dump(report, args.output)
        return 2
    try:
        result = load_json(result_path)
        report["checks"].append("result_json_parseable")
    except Exception as exc:
        report["problems"].append("result_json_unparseable:%s" % str(exc))
        report["exit_code"] = 2
        _dump(report, args.output)
        return 2

    # 1. case_id / status
    if not result.get("case_id"):
        report["problems"].append("missing_case_id")
    else:
        report["checks"].append("case_id_present")
    status = result.get("status")
    if status not in ("ok", "partial", "insufficient_evidence", "input_error"):
        report["problems"].append("invalid_status:%s" % status)
    else:
        report["checks"].append("status=%s" % status)

    # 2. contract 键覆盖
    missing = contract_keys_covered(result, manifest)
    if missing:
        report["problems"].append("missing_contract_keys:%s" % ",".join(missing))
    else:
        report["checks"].append("all_contract_keys_present(%d)" % len(manifest.get("contract_keys") or []))

    # 3. 主产物存在且非空
    dl = locate_deliverable(result)
    if not dl:
        report["problems"].append("deliverable_path_unknown")
    else:
        path = resolve_abs(dl, args.output)
        if not os.path.exists(path):
            report["problems"].append("deliverable_missing:%s" % dl)
        elif os.path.getsize(path) == 0:
            report["problems"].append("deliverable_empty:%s" % dl)
        else:
            report["checks"].append("deliverable_exists(%d bytes)" % os.path.getsize(path))

    # 4. evidence_map / change_log 结构
    for p in check_evidence_entries(result):
        report["problems"].append(p)
    if not any(p.startswith("evidence_map") or p.startswith("change_log") for p in report["problems"]):
        report["checks"].append("evidence_entries_have_locator_and_quote")

    # 5. 数值保留（仅告警）
    report["warnings"].extend(coverage_check(result, manifest, args.output))

    # 结论
    if report["problems"]:
        report["exit_code"] = 1
    else:
        report["exit_code"] = 0
    if report["warnings"]:
        report["checks"].append("warnings(%d)_review" % len(report["warnings"]))

    _dump(report, args.output)
    sys.stderr.write("[verify] exit=%d problems=%d warnings=%d\n"
                     % (report["exit_code"], len(report["problems"]), len(report["warnings"])))
    return report["exit_code"]


def _dump(report, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "verification_report.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
