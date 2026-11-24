#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init_plan.py — S0 bootstrap（骨架落盘）。

读取 /input/case.json 与 /input/task.md，解析 output_contract 的字段清单，
并把一版诚实的 partial 骨架写入 /app/output/result.json，同时在 /app/work 落
三份中间表示种子：contract.json（契约 schema）、plan.json（执行计划模板）。

只用标准库；不编造数据：所有契约字段初始为 null，并在 uncertainty 中说明
“未回填”而非“已确认缺失”。可重复运行但应只在 S0 调用一次。

调用：
    python scripts/init_plan.py [--case /input/case.json --task /input/task.md
                                --out /app/output --work /app/work]
环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 可覆盖缺省路径。
"""
import argparse
import datetime
import json
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 题目“输出要求”的三组交付物，作为 case.json 未提供 output_contract 时的兜底。
DEFAULT_CONTRACT = {
    "editable_source": "可编辑源文件（分层 SVG/PPTX/EPS，文字为文本节点而非转曲）",
    "exports": "导出件（真实渲染的 PNG>=300dpi 与 PDF，字体/色彩空间/背景正确）",
    "provenance": "来源与修改记录（元素来源、版本、数据映射、人工确认）",
}


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=2 * 1024 * 1024):
    """UTF-8 文本读取，容错 BOM / 坏编码 / 超大文件；返回 (text, err, size)。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes + 1)
    except OSError as e:
        return None, "io_error:%s" % e, 0
    if len(raw) > max_bytes:
        return None, "oversize", len(raw)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None, len(raw)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "bad_encoding", len(raw)


def load_json(path):
    """容错 JSON 读取；失败时尽量取首段 JSON 再试一次。返回 (obj, errors)。"""
    text, err, size = read_text(path)
    if err == "oversize":
        return None, ["file_oversize:%s:%d" % (path, size)]
    if text is None:
        return None, [err]
    try:
        return json.loads(text), []
    except Exception as e:
        m = re.search(r"[\[{].*[\]]", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0)), ["json_repaired:%s" % e]
            except Exception as e2:
                return None, ["json_parse_failed:%s" % e2]
        return None, ["json_parse_failed:%s" % e]


def resolve_contract(case):
    """从 case.json 抽取 output_contract；缺失时用本领域默认三组字段。"""
    for key in ("output_contract", "contract", "outputContract"):
        v = case.get(key)
        if isinstance(v, dict) and v:
            return dict(v), key
    return dict(DEFAULT_CONTRACT), "default"


def main(argv=None):
    ap = argparse.ArgumentParser(description="S0 bootstrap：result 骨架 + 中间表示种子")
    ap.add_argument("--case", default=os.path.join(os.environ.get("INPUT_DIR", DEFAULT_INPUT), "case.json"))
    ap.add_argument("--task", default=os.path.join(os.environ.get("INPUT_DIR", DEFAULT_INPUT), "task.md"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", DEFAULT_WORK))
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    case, case_errs = load_json(a.case) if os.path.exists(a.case) else (None, ["missing:%s" % a.case])
    task_text, task_err, _ = read_text(a.task) if os.path.exists(a.task) else (None, "missing:%s" % a.task, 0)

    issues = list(case_errs or [])
    if task_err:
        issues.append("task_read:%s" % task_err)

    case = case if isinstance(case, dict) else {}
    case_id = case.get("case_id") or case.get("id") or None
    if not case_id:
        issues.append("case_id_missing")

    contract, contract_src = resolve_contract(case)
    contract_keys = list(contract.keys())

    # 契约字段展平为 result.json 顶层字段，全部 null + 原因，符合“逐字段填充”约定。
    result = {"case_id": case_id}
    for k in contract_keys:
        result[k] = None
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = [{
        "item": "skeleton", "level": "pending",
        "note": "S0 骨架：output_contract 字段尚未回填，待 S1–S4 材料分析后逐字段写入；"
                "null 表示“未提供”，并非“已确认缺失”。",
    }]
    result["pending_human"] = []
    result["errors"] = issues
    result["change_log"] = [{
        "step": "S0_bootstrap", "time_utc": now_utc(), "status": "done",
        "note": "写入结果骨架；contract 来源=%s；字段=%s" % (contract_src, contract_keys),
    }]

    contract_doc = {
        "source": contract_src,
        "keys": contract_keys,
        "schema": {k: (v if isinstance(v, str) else type(v).__name__) for k, v in contract.items()},
    }

    plan = {
        "case_id": case_id,
        "status": "seed",
        "entities": [],
        "relationships": [],
        "constraints": [],
        "data_sources": [],
        "figure_spec": {
            "figure_id": None, "figure_type": None,
            "target": {"format": "svg", "dpi": 300, "color_space": "RGB", "background": "#FFFFFF"},
        },
        "style_preset": None,
        "validation_rules": [],
        "notes": [],
    }

    result_path = os.path.join(a.out, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(a.work, "contract.json"), "w", encoding="utf-8") as f:
        json.dump(contract_doc, f, ensure_ascii=False, indent=2)
    with open(os.path.join(a.work, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True, "case_id": case_id, "contract_keys": contract_keys,
        "contract_source": contract_src, "issues": issues, "result": result_path,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
