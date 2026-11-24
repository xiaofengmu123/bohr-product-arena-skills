#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验解释 Skill 主入口：骨架落盘 / 契约校验 / 增量写回 / 报告 / 定稿。

仅使用 Python 标准库。运行时路径约定：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work

用法：
  python scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python scripts/main.py set --file /app/output/result.json --key interpretation.trends --value '[{...}]' --append
  python scripts/main.py validate --input /app/output/result.json
  python scripts/main.py report --input /app/output/result.json --output /app/output
  python scripts/main.py finalize --input /app/output/result.json
"""

import argparse
import datetime
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
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
)
INTERPRETATION_KEYS = ("trends", "anomalies", "possible_causes",
                        "verification_steps", "followup_experiments")
PLAN_KEYS = ("hypothesis", "testable_question", "variables", "controls",
             "steps", "parameters", "resources", "interpretation_criteria")
PLAN_SCALARS = ("hypothesis", "testable_question")
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")
SUBSTANTIVE_KEYS = ("trend", "observation", "cause", "step", "suggestion",
                    "finding", "claim", "statement", "value", "text", "expected",
                    "decision_rule", "target", "reason", "expected_outcome")


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """UTF-8(含 BOM)优先，坏编码降级 latin-1；超限或不可读返回 (None, reason)。"""
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
            out.append({"path": full, "rel": rel, "size_bytes": size,
                        "ext": os.path.splitext(fn)[1].lower()})
    return out


def resolve_case_id(input_dir):
    obj = load_json_safe(os.path.join(input_dir, "case.json"))
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "case"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        content = obj.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", content, re.I)
            if m:
                return m.group(1)
    text, _ = read_text(os.path.join(input_dir, "task.md"))
    if text:
        m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_ ]([0-9a-fA-F-]{8,})", text)
        if m:
            return m.group(0)
    return "unknown"


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def merge_output_contract(result, case_obj):
    """若 case.json 含 output_contract，原样回填其全部键（缺值给 null/[]），不改名不删未知键。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    if contract is None:
        return []
    names = []
    spec_by_name = {}
    if isinstance(contract, dict):
        for key in ("fields", "required", "schema"):
            v = contract.get(key)
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, str):
                        names.append(x)
                    elif isinstance(x, dict):
                        for k in x:
                            names.append(k)
                            spec_by_name[k] = x[k]
        if not names:
            for k in contract:
                if isinstance(k, str) and k not in ("fields", "required", "schema", "description", "title"):
                    names.append(k)
                    spec_by_name[k] = contract[k]
    elif isinstance(contract, list):
        for x in contract:
            if isinstance(x, str):
                names.append(x)
            elif isinstance(x, dict):
                for k in x:
                    names.append(k)
                    spec_by_name[k] = x[k]
    merged = []
    for k in names:
        if k not in result:
            result[k] = _default_for(spec_by_name.get(k))
            merged.append(k)
    return merged


def skeleton_report(result):
    lines = [
        "# 实验结果解释报告（骨架）",
        "",
        "- case_id: %s" % result.get("case_id"),
        "- status: %s" % result.get("status"),
        "- 生成时间: %s" % result.get("runtime", {}).get("started_at", ""),
        "",
        "本报告为 P0 阶段骨架；画像与五段结论将在 P1–P3 阶段增量写入。",
    ]
    return "\n".join(lines) + "\n"


def build_skeleton(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)
    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    task_text, _ = read_text(os.path.join(input_dir, "task.md"))
    case_id = resolve_case_id(input_dir)
    files = discover_files(input_dir)

    result = {
        "case_id": case_id,
        "status": "partial",
        "task_type": "experiment_result_interpretation",
        "summary": "",
        "interpretation": {k: [] for k in INTERPRETATION_KEYS},
        "experiment_plan": {k: (None if k in PLAN_SCALARS else []) for k in PLAN_KEYS},
        "evidence_and_risk": {k: [] for k in RISK_KEYS},
        "confirmation_items": {k: [] for k in CONFIRM_KEYS},
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_at": now_ts(), "finished_at": None, "seconds": None},
    }
    merge_output_contract(result, case_obj)

    degradations = result.setdefault("degradations", [])
    limitations = result.setdefault("limitations", [])
    if task_text is None:
        degradations.append("task.md 缺失或不可读")
    if case_obj is None:
        degradations.append("case.json 缺失或不是合法 JSON")

    other_files = [f for f in files if f["rel"] not in ("case.json", "task.md")]
    if case_obj is None and task_text is None and not other_files:
        result["status"] = "insufficient_evidence"
        result["summary"] = "输入目录无可用材料，无法进行结果解释。"
        limitations.append("输入为空：缺少 case.json、task.md 与任何材料文件。")

    result.setdefault("change_log", []).append(
        {"phase": "skeleton", "at": now_ts(), "note": "partial 骨架落盘"})

    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"), skeleton_report(result))
    return 0


def set_value(path, key, value, append):
    """按 dot.path 安全地写回嵌套 JSON 字段；append 时向数组追加（list 则 extend）。"""
    obj = load_json_safe(path)
    if obj is None:
        print("错误：无法读取或解析 %s" % path, file=sys.stderr)
        return 1
    parts = [p for p in key.split(".") if p]
    if not parts:
        print("错误：key 不能为空", file=sys.stderr)
        return 1
    cur = obj
    for p in parts[:-1]:
        if isinstance(cur, dict) and isinstance(cur.get(p), dict):
            cur = cur[p]
        else:
            print("错误：路径 %s 不存在或非对象（在 %s）" % (key, p), file=sys.stderr)
            return 1
    last = parts[-1]
    if append:
        existing = cur.get(last)
        if not isinstance(existing, list):
            existing = []
        if isinstance(value, list):
            existing.extend(value)
        else:
            existing.append(value)
        cur[last] = existing
    else:
        cur[last] = value
    obj.setdefault("change_log", []).append(
        {"phase": "set", "at": now_ts(), "note": "更新 %s" % key})
    write_json_atomic(path, obj)
    return 0


def validate_result(path):
    obj = load_json_safe(path)
    if obj is None:
        return False, ["JSON 无法解析或文件不可读"]
    if not isinstance(obj, dict):
        return False, ["顶层不是 JSON 对象"]
    msgs = []
    ok = True
    cid = obj.get("case_id")
    if not (isinstance(cid, str) and cid.strip() and cid.strip() != "unknown"):
        msgs.append("case_id 缺失或为 unknown")
        ok = False
    if obj.get("status") not in STATUSES:
        msgs.append("status 非法: %r" % obj.get("status"))
        ok = False
    for k in TOP_KEYS:
        if k not in obj:
            msgs.append("缺少顶层字段 %s" % k)
            ok = False
    interp = obj.get("interpretation")
    if isinstance(interp, dict):
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                msgs.append("interpretation 缺少 %s" % k)
                ok = False
            elif not isinstance(interp[k], list):
                msgs.append("interpretation.%s 应为数组" % k)
                ok = False
    else:
        msgs.append("interpretation 缺失或不是对象")
        ok = False
    for section in INTERPRETATION_KEYS:
        items = []
        if isinstance(interp, dict):
            items = interp.get(section, [])
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            ev = it.get("evidence")
            substantive = any(
                isinstance(it.get(k), (str, int, float)) and str(it[k]).strip()
                for k in SUBSTANTIVE_KEYS)
            if substantive and ev is None:
                msgs.append("%s[%d] 有结论但缺少 evidence" % (section, i))
            elif isinstance(ev, list):
                for j, e in enumerate(ev):
                    if not isinstance(e, dict) or not str(e.get("locator", "")).strip() \
                            or not str(e.get("quote", "")).strip():
                        msgs.append("%s[%d].evidence[%d] 缺少 locator 或 quote" % (section, i, j))
    return ok, msgs


def build_report(result_path, output_dir):
    obj = load_json_safe(result_path)
    if obj is None:
        print("错误：无法读取 %s" % result_path, file=sys.stderr)
        return 1
    os.makedirs(output_dir, exist_ok=True)
    lines = ["# 实验结果解释报告", ""]
    lines.append("- case_id: %s" % obj.get("case_id"))
    lines.append("- status: %s" % obj.get("status"))
    lines.append("- task_type: %s" % obj.get("task_type", ""))
    lines.append("")
    summary = obj.get("summary") or ""
    if summary:
        lines += ["## 摘要", "", str(summary), ""]
    interp = obj.get("interpretation")
    if isinstance(interp, dict):
        labels = {
            "trends": "趋势", "anomalies": "异常点",
            "possible_causes": "可能原因", "verification_steps": "复核步骤",
            "followup_experiments": "后续实验建议",
        }
        for key, label in labels.items():
            lines += ["## %s" % label, ""]
            items = interp.get(key, [])
            if not isinstance(items, list) or not items:
                lines.append("（空）")
            for i, it in enumerate(items if isinstance(items, list) else []):
                if isinstance(it, dict):
                    head = (it.get("trend") or it.get("observation") or it.get("cause")
                            or it.get("step") or it.get("suggestion") or it.get("finding")
                            or it.get("claim") or json.dumps(it, ensure_ascii=False))
                    lines.append("%d. %s" % (i + 1, head))
                    for e in (it.get("evidence") or []):
                        if isinstance(e, dict):
                            lines.append("   - 证据 [%s]: %s" % (e.get("locator"),
                                                             (e.get("quote") or "")[:160]))
                else:
                    lines.append("%d. %s" % (i + 1, str(it)))
            lines.append("")
    plan = obj.get("experiment_plan")
    if isinstance(plan, dict):
        lines += ["## 实验方案", ""]
        for k, v in plan.items():
            if v:
                lines.append("- **%s**: %s" % (k, v if isinstance(v, str)
                                              else json.dumps(v, ensure_ascii=False)))
        lines.append("")
    lines += ["## 不确定性与限制", ""]
    for k in ("uncertainty", "limitations", "pending_human", "errors", "degradations"):
        v = obj.get(k) or []
        if v:
            lines.append("### %s" % k)
            for item in v:
                lines.append("- %s" % (json.dumps(item, ensure_ascii=False)
                                       if not isinstance(item, str) else item))
            lines.append("")
    if isinstance(obj.get("sources"), list) and obj["sources"]:
        lines += ["## 来源", ""]
        for s in obj["sources"]:
            if isinstance(s, dict):
                lines.append("- %s: %s" % (s.get("locator", "?"), s.get("path", "")))
            else:
                lines.append("- %s" % str(s))
        lines.append("")
    write_text_atomic(os.path.join(output_dir, "report.md"), "\n".join(lines))

    evmap = {"case_id": obj.get("case_id"), "evidence": []}
    if isinstance(interp, dict):
        for section in INTERPRETATION_KEYS:
            items = interp.get(section, [])
            if not isinstance(items, list):
                continue
            for i, it in enumerate(items):
                if isinstance(it, dict):
                    for e in (it.get("evidence") or []):
                        if isinstance(e, dict):
                            evmap["evidence"].append({
                                "section": section, "index": i,
                                "locator": e.get("locator"), "quote": e.get("quote"),
                                "source_conflict": e.get("source_conflict", False),
                            })
    write_json_atomic(os.path.join(output_dir, "evidence_map.json"), evmap)
    return 0


def finalize_result(path):
    obj = load_json_safe(path)
    if obj is None:
        print("错误：无法读取 %s" % path, file=sys.stderr)
        return 1
    rt = obj.get("runtime")
    if not isinstance(rt, dict):
        rt = {}
        obj["runtime"] = rt
    rt.setdefault("started_at", now_ts())
    rt["finished_at"] = now_ts()
    try:
        st = datetime.datetime.strptime(rt["started_at"], "%Y-%m-%dT%H:%M:%SZ")
        ft = datetime.datetime.strptime(rt["finished_at"], "%Y-%m-%dT%H:%M:%SZ")
        rt["seconds"] = max(0, int((ft - st).total_seconds()))
    except Exception:
        rt["seconds"] = None
    obj.setdefault("change_log", []).append(
        {"phase": "finalize", "at": now_ts(), "note": "runtime 落盘"})
    write_json_atomic(path, obj)
    return 0


def parse_value(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="实验解释 Skill 主入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("skeleton", help="落盘 result.json 骨架")
    a.add_argument("--input", default=DEFAULT_INPUT)
    a.add_argument("--output", default=DEFAULT_OUTPUT)
    a.add_argument("--work", default=DEFAULT_WORK)

    a = sub.add_parser("set", help="按 dot.path 安全增量写回嵌套 JSON 字段")
    a.add_argument("--file", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--value", default=None,
                   help="值（JSON 字面量或普通字符串）")
    a.add_argument("--value-file", default=None,
                   help="从文件读值，避免 shell 对嵌套引号的转义问题")
    a.add_argument("--append", action="store_true")

    a = sub.add_parser("validate", help="校验 schema 与证据完整性")
    a.add_argument("--input", required=True)

    a = sub.add_parser("report", help="从 result.json 生成 report.md/evidence_map.json")
    a.add_argument("--input", required=True)
    a.add_argument("--output", default=DEFAULT_OUTPUT)

    a = sub.add_parser("finalize", help="写 runtime 并定稿")
    a.add_argument("--input", required=True)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "skeleton":
        rc = build_skeleton(args.input, args.output, args.work)
    elif args.cmd == "set":
        if args.value_file:
            try:
                with open(args.value_file, "r", encoding="utf-8-sig") as fh:
                    raw = fh.read()
            except OSError as exc:
                print("错误：无法读取 --value-file: %s" % exc, file=sys.stderr)
                sys.exit(1)
            value = parse_value(raw)
        elif args.value is not None:
            value = parse_value(args.value)
        else:
            print("错误：--value 与 --value-file 至少提供一个", file=sys.stderr)
            sys.exit(1)
        rc = set_value(args.file, args.key, value, args.append)
    elif args.cmd == "validate":
        ok, msgs = validate_result(args.input)
        for m in msgs:
            print(m, file=sys.stderr if not ok else sys.stdout)
        rc = 0 if ok else 1
    elif args.cmd == "report":
        rc = build_report(args.input, args.output)
    elif args.cmd == "finalize":
        rc = finalize_result(args.input)
    else:
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
