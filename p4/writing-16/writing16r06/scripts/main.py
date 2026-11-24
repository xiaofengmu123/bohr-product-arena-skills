#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-transform-writing Skill — 运行骨架与结果终检。

职责（不替代 Agent 的实际写作，只做"可复核的工程骨架"）：
  1. scaffold  发现输入、解析 case.json/task.md、提取 output_contract，
               立刻落盘一份合法的 result.json 骨架（status=partial），
               并生成 work/ 下的证据表、术语表、改动清单模板。
  2. finalize  校验 Agent 填好的 result.json：契约字段齐全、status 诚实、
               引用字段存在、主产物文件真实存在，并回写 runtime。

只依赖 Python 标准库。默认路径遵循运行时约定：/input、/app/output、/app/work。
"""

import argparse
import json
import os
import sys
import time
import traceback

MAX_FILE_BYTES = 2_000_000          # 单文本文件上限，超限跳过并记录
CORE_FIELDS = ["status", "sources", "uncertainty", "change_log"]  # 结果必备键
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


# --------------------------------------------------------------------------
# 容错读取
# --------------------------------------------------------------------------
def read_text(path):
    """按 utf-8-sig → utf-8 → gb18030 → latin-1 顺序容错解码；超限返回 (None, err)。"""
    if not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return None, "read_error:%s" % e
    if len(data) > MAX_FILE_BYTES:
        return None, "oversize:%dB>%dB" % (len(data), MAX_FILE_BYTES)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "replaced_bad_encoding"


def read_json(path):
    """读取 JSON，坏 JSON 返回 (None, err) 而不崩溃。"""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001 - 需要捕获一切坏 JSON
        # 尝试裁剪到最后一个完整 JSON 值
        for cut in (len(text),):
            pass
        return None, "bad_json:%s" % e


# --------------------------------------------------------------------------
# 输入发现与契约提取
# --------------------------------------------------------------------------
def iter_input_files(input_dir):
    """递归列举输入目录下的文本文件（不假设固定文件名）。"""
    out = []
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            p = os.path.join(root, name)
            if os.path.islink(p):
                continue
            out.append(p)
    return out


def _contract_key(k):
    kk = str(k).lower().replace("-", "_").replace(" ", "")
    return kk in ("output_contract", "outputcontract", "result_contract",
                  "resultcontract", "required_fields", "requiredfields",
                  "output_schema", "outputschema", "contract")


def find_contracts(obj, path="$", out=None):
    """递归定位形如 output_contract 的键，返回 [(path, value), ...]。"""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (path, k)
            if _contract_key(k):
                out.append((p, v))
            find_contracts(v, p, out)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            find_contracts(item, "%s[%d]" % (path, i), out)
    return out


def extract_fields(contract, out=None, depth=0):
    """从契约值（普通 dict / JSON-schema / 列表）抽取字段名，去重保序。"""
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            for k in props:
                if k not in out:
                    out.append(k)
        req = contract.get("required")
        if isinstance(req, list):
            for r in req:
                if isinstance(r, str) and r not in out:
                    out.append(r)
        for k, v in contract.items():
            if k in ("properties", "required", "type", "title",
                     "description", "additionalProperties"):
                continue
            if isinstance(v, (dict, list)):
                extract_fields(v, out, depth + 1)
            else:
                if k not in out:
                    out.append(k)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                if item not in out:
                    out.append(item)
            else:
                extract_fields(item, out, depth + 1)
    return out


def find_case_id(data):
    """从常见键名推断 case_id，缺失时返回未知标识而非崩溃。"""
    if not isinstance(data, dict):
        return None
    for k in ("case_id", "caseId", "caseID", "id", "task_id", "challenge_id"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, int):
            return str(v)
    return None


# --------------------------------------------------------------------------
# 骨架与终检
# --------------------------------------------------------------------------
def build_skeleton(case_id, fields, started):
    result = {"case_id": case_id if case_id else "unknown"}
    for f in fields:
        if f not in result:
            result[f] = None
    for k in CORE_FIELDS:
        result[k] = result.get(k, None)
    result["status"] = "partial"  # 早期诚实状态：骨架已落盘，正文待完善
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime_ms"] = 0
    result["started_at"] = started
    return result


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def scaffold(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    summary = {"input_dir": args.input, "files": [], "errors": [],
               "contracts": [], "fields": [], "case_id": None,
               "task_md": None}
    data = None

    for p in iter_input_files(args.input):
        rel = os.path.relpath(p, args.input)
        name = os.path.basename(p)
        entry = {"path": rel, "bytes": 0, "kind": None, "error": None}
        try:
            entry["bytes"] = os.path.getsize(p)
        except OSError:
            entry["error"] = "stat_error"
        summary["files"].append(entry)

        if name.lower() == "case.json":
            obj, err = read_json(p)
            entry["kind"] = "case_json"
            if err:
                entry["error"] = err
                summary["errors"].append("%s: %s" % (rel, err))
            else:
                data = obj
                summary["case_id"] = find_case_id(obj)
                for path_, c in find_contracts(obj):
                    summary["contracts"].append(
                        {"path": path_, "fields": extract_fields(c)})
        elif name.lower() == "task.md" or name.lower().endswith(".md"):
            text, err = read_text(p)
            entry["kind"] = "markdown"
            if name.lower() == "task.md":
                summary["task_md"] = rel
            if err:
                entry["error"] = err
                summary["errors"].append("%s: %s" % (rel, err))
        else:
            entry["kind"] = "other"

    # 汇总契约字段：优先 case.json，其次 task.md 文本里显式声明的 output_contract
    fields = []
    for c in summary["contracts"]:
        for f in c["fields"]:
            if f not in fields:
                fields.append(f)
    if not fields and summary["task_md"]:
        text, err = read_text(os.path.join(args.input, summary["task_md"]))
        if err is None and text:
            for line in text.splitlines():
                if "output_contract" in line or "result.json" in line:
                    # 只记录需要人工确认的线索，不臆造字段
                    summary["errors"].append(
                        "task.md 提及 output_contract，但未发现结构化字段，"
                        "已按最小契约回填")
                    break

    if not fields:
        fields = ["manuscript", "evidence_map", "term_table",
                  "revision_notes"]  # 写作题通用最小可编辑产物

    summary["fields"] = fields
    case_id = summary["case_id"]

    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    result = build_skeleton(case_id, fields, started)
    result["runtime_ms"] = int((time.time() - t0) * 1000)

    # 空输入 / 缺任务说明：诚实降级为 insufficient_evidence，不编造
    has_case = any(f["kind"] == "case_json" for f in summary["files"])
    if not summary["files"]:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append("输入目录为空，无任何科研材料可处理")
        result["pending_human"].append("请提供 case.json 与源材料后重跑")
    elif not has_case:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append("未发现 case.json，无法确定任务与输出契约")
        result["pending_human"].append("请确认 case.json 是否位于 /input 下")

    out_result = os.path.join(args.output, "result.json")
    write_json(out_result, result)
    write_json(os.path.join(args.work, "inputs_summary.json"), summary)

    # work/ 模板：证据表、术语表、改动清单，供 Agent 增量填写
    write_json(os.path.join(args.work, "evidence_map.json"),
               {"_instructions": "每项含 locator 与逐字 quote，不新增材料外事实",
                "items": []})
    write_json(os.path.join(args.work, "term_table.json"),
               {"_instructions": "锁定同一概念的单一说法；缩写首次展开",
                "terms": []})
    write_json(os.path.join(args.work, "change_log.json"),
               {"_instructions": "每条含 位置/原表述/新表述/理由/证据",
                "entries": []})
    return result, summary, out_result


def validate(result, fields, artifacts_dir=None):
    """终检：返回 (errors, warnings)。"""
    errors, warnings = [], []
    if not isinstance(result, dict):
        return ["result.json 不是对象"], []
    if not result.get("case_id") or result.get("case_id") == "unknown":
        warnings.append("case_id 未知：请从输入回填真实标识")
    st = result.get("status")
    if st not in VALID_STATUS:
        errors.append("status 非法：%r，应属 %s" % (st, sorted(VALID_STATUS)))
    for k in CORE_FIELDS:
        if k not in result:
            errors.append("缺少必备键：%s" % k)
    for f in fields:
        if f not in result:
            errors.append("缺少契约字段：%s" % f)
        elif result[f] in (None, "", []):
            warnings.append("契约字段为空：%s（若确实缺数据请标 not_reported/pending_human）" % f)
    # 主产物文件存在性（仅当字段值形似路径时检查）
    if artifacts_dir:
        for f in fields:
            v = result.get(f)
            if isinstance(v, str) and v.strip() and not v.startswith("http"):
                cand = os.path.join(artifacts_dir, os.path.basename(v)) \
                    if not os.path.isabs(v) else v
                if os.path.sep in v or "/" in v or v.endswith((".md", ".json",
                        ".csv", ".docx", ".pptx", ".xlsx", ".svg", ".pdf", ".txt")):
                    if not os.path.exists(cand):
                        warnings.append("引用产物未找到：%s -> %s" % (f, v))
    if not result.get("sources"):
        warnings.append("sources 为空：关键结论需可回溯")
    if not result.get("change_log"):
        warnings.append("change_log 为空：删改需有清单与理由")
    return errors, warnings


def finalize(args):
    t0 = time.time()
    out_result = os.path.join(args.output, "result.json")
    text, err = read_text(out_result)
    if err:
        return 1, ["无法读取 result.json: %s" % err]
    try:
        result = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return 1, ["result.json 不是合法 JSON: %s" % e]

    # 从 work 恢复契约字段（若 Agent 未改写 inputs_summary）
    fields = []
    summary_path = os.path.join(args.work, "inputs_summary.json")
    if os.path.exists(summary_path):
        s, _e = read_json(summary_path)
        if isinstance(s, dict):
            fields = s.get("fields", [])
    if not fields:
        fields = ["manuscript", "evidence_map", "term_table", "revision_notes"]

    errors, warnings = validate(result, fields, artifacts_dir=args.output)
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("pending_human", [])
    result["runtime_ms"] = int((time.time() - t0) * 1000)
    result["finalized_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    write_json(out_result, result)

    if errors:
        return 1, ["finalize 发现错误："] + errors + ["warnings:"] + warnings
    return 0, ["finalize ok", "warnings: %d" % len(warnings)] + warnings


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="科研成果转化写作 Skill 的运行骨架与终检")
    ap.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="产物目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    ap.add_argument("--phase", choices=["scaffold", "finalize"],
                    default="scaffold", help="scaffold=落骨架；finalize=终检")
    args = ap.parse_args(argv)

    try:
        if args.phase == "scaffold":
            result, summary, out = scaffold(args)
            print(json.dumps({"case_id": result.get("case_id"),
                              "fields": summary["fields"],
                              "contracts": summary["contracts"],
                              "files": len(summary["files"]),
                              "errors": summary["errors"],
                              "result": out}, ensure_ascii=False, indent=2))
            return 0
        code, lines = finalize(args)
        for ln in lines:
            print(ln)
        return code
    except Exception:  # noqa: BLE001 - 任何异常都不留半成品、不崩进程
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
