#!/usr/bin/env python3
"""validate_result.py — 交付前自检：对 result.json 与产物做可勾选的机械校验。

检查项（对应评分维度的"可核验性 / 完整性 / 异常处理"）：
1. result.json 存在且可解析为 JSON。
2. case_id 非空。
3. status ∈ {ok, partial, insufficient_evidence, input_error, in_progress}。
4. 输入契约（input_manifest.json 中的 output_contract）的全部键都在 result.json 顶层出现。
5. result.json 声明的主产物文件真实存在。
6. evidence_map.json 每条证据都有 locator 与 quote。
7. mindmap.svg 可被 XML 解析器解析。
8. 文本中不存在明显的占位/伪造标记（TODO / XXX / lorem / 待补 / 编造）。

退出码恒为 0（报告式校验，不打断 Harness）；硬问题写入 hard_issues，
Agent 应读取报告并逐条修复后再定稿。
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULTS = {"work": "/app/work", "output": "/app/output"}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error", "in_progress"}
PLACEHOLDER_PAT = re.compile(r"\b(TODO|FIXME|XXX|lorem|ipsum)\b|待补|待填|占位|编造", re.IGNORECASE)
ARTIFACT_FIELDS = ("primary_artifacts", "artifacts", "outputs", "main_outputs", "mindmap_svg",
                   "mindmap_md", "mindmap_mmd", "mindmap_graphml", "evidence_map")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--work", default=DEFAULTS["work"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    args = ap.parse_args(argv)

    hard, warnings, checks = [], [], []
    result_path = os.path.join(args.output, "result.json")

    # 1. result.json
    result = None
    if not os.path.exists(result_path):
        hard.append("result.json 不存在")
    else:
        try:
            result = read_json(result_path)
            checks.append(("result.json 可解析", True))
        except Exception as exc:
            hard.append("result.json 解析失败: %s" % exc)
            result = None

    if result is not None:
        # 2. case_id
        cid = result.get("case_id")
        if not cid:
            hard.append("result.json 缺 case_id 或为空")
        else:
            checks.append(("case_id 非空", True))

        # 3. status
        st = result.get("status")
        if st not in ALLOWED_STATUS:
            hard.append("status 非法: %r（应为 %s）" % (st, "/".join(sorted(ALLOWED_STATUS))))
        else:
            checks.append(("status 合法", True))

        # 4. 契约键
        manifest = {}
        manifest_path = os.path.join(args.work, "input_manifest.json")
        if os.path.exists(manifest_path):
            try:
                manifest = read_json(manifest_path)
            except Exception:
                manifest = {}
        contract = manifest.get("output_contract")
        if isinstance(contract, dict):
            missing = [k for k in contract.keys() if k not in result]
            if missing:
                hard.append("result.json 缺契约键: %s" % ", ".join(missing))
            else:
                checks.append(("契约键全部回填（%d 个）" % len(contract), True))
        else:
            warnings.append("input_manifest.json 无 output_contract，无法核对契约键")

        # 5. 主产物存在性
        checked_files = 0
        for fld in ARTIFACT_FIELDS:
            val = result.get(fld)
            if not val:
                continue
            paths = []
            if isinstance(val, list):
                for it in val:
                    if isinstance(it, dict) and it.get("path"):
                        paths.append(it["path"])
                    elif isinstance(it, str):
                        paths.append(it)
            elif isinstance(val, str):
                paths.append(val)
            for rel in paths:
                abs_p = os.path.join(args.output, rel)
                if os.path.exists(abs_p):
                    checked_files += 1
                else:
                    hard.append("声明产物缺失: %s" % rel)
        if checked_files:
            checks.append(("主产物文件存在（%d 个）" % checked_files, True))

        # 6. 证据映射
        ev_path = os.path.join(args.output, "evidence_map.json")
        if os.path.exists(ev_path):
            try:
                ev = read_json(ev_path)
                items = ev.get("items", [])
                bad = [i for i in items if not i.get("locator") or not i.get("quote")]
                if bad:
                    hard.append("evidence_map.json 有 %d 条缺 locator/quote 的证据" % len(bad))
                else:
                    checks.append(("证据映射每条含 locator+quote（%d 条）" % len(items), True))
            except Exception as exc:
                warnings.append("evidence_map.json 解析失败: %s" % exc)
        else:
            warnings.append("未发现 evidence_map.json")

        # 7. SVG 可解析
        svg_path = os.path.join(args.output, "mindmap.svg")
        if os.path.exists(svg_path):
            try:
                ET.parse(svg_path)
                checks.append(("mindmap.svg 可被 XML 解析", True))
            except Exception as exc:
                hard.append("mindmap.svg 解析失败: %s" % exc)

        # 8. 占位/伪造标记
        def scan(obj, path=""):
            found = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    found += scan(v, path + "." + str(k))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    found += scan(v, path + "[%d]" % i)
            elif isinstance(obj, str) and PLACEHOLDER_PAT.search(obj):
                found.append("%s: %r" % (path or "result", obj[:80]))
            return found

        marks = scan(result)
        if marks:
            warnings.append("发现占位/待补标记 %d 处（请替换为真实内容）: %s"
                            % (len(marks), marks[0]))
        else:
            checks.append(("无占位/伪造标记", True))

    # 附加：spec 质量（如果存在）
    spec_path = os.path.join(args.work, "mindmap_spec.json")
    if os.path.exists(spec_path):
        try:
            spec = read_json(spec_path)
            n = len(spec.get("nodes", []))
            r = len(spec.get("relations", []))
            if n == 0:
                warnings.append("mindmap_spec.json 节点数为 0（仅根节点），可能信息覆盖不足")
            checks.append(("spec 节点 %d / 关系 %d" % (n, r), True))
        except Exception:
            pass

    report = {
        "checked_at": now_iso(),
        "pass": len(hard) == 0,
        "checks": [{"item": c[0], "ok": c[1]} for c in checks],
        "hard_issues": hard,
        "warnings": warnings,
    }
    rep_path = os.path.join(args.work, "validation_report.json")
    with open(rep_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"pass": report["pass"], "hard": len(hard),
                      "warnings": len(warnings)}, ensure_ascii=False))
    for h in hard:
        print("HARD:", h)
    for w in warnings:
        print("WARN:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
