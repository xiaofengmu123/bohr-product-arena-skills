#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验结果解释与异常分析 Skill 主入口。

职责：
  skeleton  先落盘一版合法的 partial result.json 骨架 + report.md 骨架（前 3 分钟兜底）。
  inspect   生成 data_profile.json（文件清单、每列统计、离群标记、曲线特征、降级记录）。
  build     依据 result.json 生成可编辑 report.md 与 evidence_map.json。
  validate  校验 result.json 的 schema、状态诚实性、证据完整性与复算配方。
  finalize  写入 runtime 耗时并补 change_log 定稿条目。

仅使用 Python 标准库；路径约定：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work

用法（在 Skill 根目录执行）：
  python scripts/main.py skeleton --input /input --output /app/output
  python scripts/main.py inspect   --input /input --output /app/output
  python scripts/main.py build     --input /app/output/result.json --output /app/output
  python scripts/main.py validate  --input /app/output/result.json
  python scripts/main.py finalize  --input /app/output/result.json
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

TOP_KEYS = (
    "case_id", "status", "task_type", "summary", "data_form",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
)
INTERPRETATION_KEYS = (
    "trends", "anomalies", "possible_causes",
    "verification_steps", "followup_experiments",
)
PLAN_KEYS = (
    "hypothesis", "testable_question", "variables", "controls",
    "steps", "parameters", "resources", "interpretation_criteria",
)
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")

# 证据类字段：关键结论必须绑定 locator + 逐字 quote。
EVIDENCE_KEYS = ("evidence", "supporting_evidence", "excluding_evidence")

# 评分维度 -> 流程落点 -> 自检项编号（完整版见 references/scoring_alignment.md）。
SCORING_ALIGNMENT = [
    ("actual_effect", "P2 五段结论全部产出且非空", "S2"),
    ("actual_effect", "P1 画像 + P2 方向/阈值/对象核对", "S4,S6"),
    ("actual_effect", "P2 覆盖全部指标/对照/重复并交代未解释部分", "S7"),
    ("actual_effect", "P2 每条结论 locator+quote+复算配方", "S3,S5"),
    ("use_gain", "阈值/对照/排序比基础 Agent 稳定可复算", "S4,S8"),
    ("use_gain", "P1 自动画像/离群标记减少回查与返工", "S10"),
    ("use_gain", "change_log 记录对照与返工项", "S11"),
    ("innovation", "异常绑定批次/通道/孔位维度并按可检验性排序", "S8"),
    ("innovation", "阈值方法显式、复算配方可复现", "S4,S5"),
    ("reliability", "骨架先落盘、增量写、确定性排序", "S10,S13"),
    ("reliability", "缺数据/单位/对照/重复/饱和/冲突识别", "S6,S7"),
    ("ecology", "output_contract.md 字段与阈值定义齐全", "S12,S14"),
]


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """UTF-8(含 BOM) 优先，坏编码降级 latin-1；超限或不可读返回 (None, reason)。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > max_bytes:
        return None, "file exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    return None, "unreadable encoding"


def load_json_safe(path):
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def sha256_head(path, head_bytes=1_000_000):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            remaining = head_bytes
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return None
    return h.hexdigest()


def discover_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root).replace("\\", "/")
            ext = os.path.splitext(fn)[1].lower()
            out.append({"path": full, "rel": rel, "size_bytes": size, "ext": ext})
    return out


def resolve_case_id(input_dir):
    """从 case.json 或 task.md 解析 challenge_id；缺省回退到输入目录名。"""
    obj = load_json_safe(os.path.join(input_dir, "case.json"))
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "case"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        content = obj.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge[-_ ]?id[:\s`]+([A-Za-z0-9_.-]+)", content, re.I)
            if m:
                return m.group(1)
    text, _ = read_text(os.path.join(input_dir, "task.md"))
    if text:
        m = re.search(r"challenge[-_ ]?id[:\s`]+([A-Za-z0-9_.-]+)", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_ ]([0-9a-fA-F-]{8,})", text)
        if m:
            return m.group(0)
    base = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown"
    return base


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def _empty_interpretation():
    return {k: [] for k in INTERPRETATION_KEYS}


def _empty_plan():
    return {k: ([] if k in ("variables", "controls", "steps", "parameters", "resources")
                else None) for k in PLAN_KEYS}


def _empty_risk():
    return {k: None for k in RISK_KEYS}


def _empty_confirm():
    return {k: [] for k in CONFIRM_KEYS}


def merge_output_contract(result, case_obj):
    """case.json 含 output_contract 时原样回填全部键（缺值 null/[]），不改名不删未知键。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    if contract is None:
        return []
    names = []
    if isinstance(contract, dict):
        for key in ("fields", "required", "schema"):
            v = contract.get(key)
            if isinstance(v, list):
                names.extend(x for x in v if isinstance(x, str))
        if not names:
            names = [k for k in contract if isinstance(k, str)
                     and k not in ("fields", "required", "schema", "description", "title")]
    elif isinstance(contract, list):
        names = [x for x in contract if isinstance(x, str)]
    merged = []
    for k in names:
        if k not in result:
            default = None
            if isinstance(contract, dict) and k in contract:
                default = _default_for(contract[k])
            result[k] = default
            merged.append(k)
    return merged


def _empty_sources():
    return []


def build_skeleton(input_dir, output_dir, started=None):
    """写出一版含全部顶层字段的合法 result.json（status=partial）+ report.md 骨架。"""
    started = started or now_ts()
    case_id = resolve_case_id(input_dir)
    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))

    files = discover_files(input_dir)
    out_abs = os.path.abspath(output_dir) + os.sep
    material_files = [f for f in files
                      if f["rel"] not in ("case.json", "task.md")
                      and not os.path.abspath(f["path"]).startswith(out_abs)]
    has_material = any(f["size_bytes"] > 0 for f in material_files)
    status = "partial" if has_material else "insufficient_evidence"

    result = {
        "case_id": case_id,
        "status": status,
        "task_type": "experiment_result_interpretation",
        "summary": "结果骨架已先行落盘；结论将在读取材料后增量补齐。" if has_material
                   else "输入目录中未发现可解读的材料文件，材料不足，不做推断。",
        "data_form": "generic",
        "interpretation": _empty_interpretation(),
        "experiment_plan": _empty_plan(),
        "evidence_and_risk": _empty_risk(),
        "confirmation_items": _empty_confirm(),
        "sources": _empty_sources(),
        "uncertainty": [],
        "change_log": [{
            "stage": "skeleton",
            "at": started,
            "detail": "先落盘合法 partial 骨架，之后只做增量改进；缺材料时如实标注 insufficient_evidence。",
        }],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"started": started, "finished": None, "elapsed_seconds": None},
    }
    merged = merge_output_contract(result, case_obj)
    if merged:
        result["change_log"].append({
            "stage": "skeleton", "at": started,
            "detail": "回填 output_contract 字段：" + ",".join(merged),
        })

    if has_material:
        result["sources"] = [{
            "rel": f["rel"], "size_bytes": f["size_bytes"], "ext": f["ext"],
            "role": "material", "note": "待画像后补充编码/哈希/读取状态",
        } for f in material_files]

    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"),
                      "# %s\n\n状态：%s\n\n（骨架报告；结论待补齐。）\n" % (case_id, status))
    write_json_atomic(os.path.join(output_dir, "data_profile.json"),
                      {"generated": False, "note": "运行 scripts/main.py inspect 生成。", "inventory": [], "tables": []})
    return result


def cmd_skeleton(args):
    build_skeleton(args.input, args.output)
    print("skeleton written: %s/result.json" % args.output)
    return 0


def cmd_inspect(args):
    from core_impl import inspect_input
    profile = inspect_input(args.input, args.output)
    write_json_atomic(os.path.join(args.output, "data_profile.json"), profile)
    n_tables = len(profile.get("tables", []))
    print("data_profile.json written: %d files, %d tables" % (len(profile.get("inventory", [])), n_tables))
    return 0


def cmd_build(args):
    from core_impl import build_report_and_map
    result = load_json_safe(args.input)
    if result is None:
        print("cannot read result.json", file=sys.stderr)
        return 1
    report, evidence_map = build_report_and_map(result)
    write_text_atomic(os.path.join(args.output, "report.md"), report)
    write_json_atomic(os.path.join(args.output, "evidence_map.json"), evidence_map)
    print("report.md and evidence_map.json written")
    return 0


def cmd_validate(args):
    from core_impl import validate_result
    result = load_json_safe(args.input)
    if result is None:
        print("FATAL: result.json 不可解析", file=sys.stderr)
        return 1
    issues, warnings = validate_result(result)
    for w in warnings:
        print("WARN: %s" % w)
    for e in issues:
        print("ERROR: %s" % e, file=sys.stderr)
    if issues:
        print("validate: FAIL (%d errors, %d warnings)" % (len(issues), len(warnings)))
        return 1
    print("validate: OK (%d warnings)" % len(warnings))
    return 0


def cmd_finalize(args):
    result = load_json_safe(args.input)
    if result is None:
        print("cannot read result.json", file=sys.stderr)
        return 1
    finished = now_ts()
    runtime = result.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    runtime.setdefault("started", finished)
    runtime["finished"] = finished
    try:
        started = time.strptime(runtime["started"], "%Y-%m-%dT%H:%M:%SZ")
        fin = time.strptime(finished, "%Y-%m-%dT%H:%M:%SZ")
        runtime["elapsed_seconds"] = int(max(0, time.mktime(fin) - time.mktime(started)))
    except (ValueError, KeyError):
        runtime["elapsed_seconds"] = None
    result["runtime"] = runtime
    result.setdefault("change_log", []).append({
        "stage": "finalize", "at": finished, "detail": "定稿：写入 runtime 与耗时。",
    })
    write_json_atomic(args.input, result)
    print("finalized: %s (elapsed=%s)" % (args.input, runtime["elapsed_seconds"]))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="实验结果解释与异常分析 Skill 主入口")
    sub = p.add_subparsers(dest="command", required=True)

    def add_io(s):
        s.add_argument("--input", default=DEFAULT_INPUT)
        s.add_argument("--output", default=DEFAULT_OUTPUT)
        s.add_argument("--work", default=DEFAULT_WORK)

    add_io(sub.add_parser("skeleton", help="落盘 partial 骨架"))
    add_io(sub.add_parser("inspect", help="数据清点与画像"))
    add_io(sub.add_parser("build", help="生成 report.md / evidence_map.json"))
    v = sub.add_parser("validate", help="校验 result.json")
    v.add_argument("--input", required=True)
    f = sub.add_parser("finalize", help="定稿并写 runtime")
    f.add_argument("--input", required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "skeleton": cmd_skeleton,
        "inspect": cmd_inspect,
        "build": cmd_build,
        "validate": cmd_validate,
        "finalize": cmd_finalize,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
