#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验结果解释 Skill 主入口：契约骨架 / 增量更新 / 契约校验 / 报告生成 / 定稿。

仅使用 Python 标准库。运行时路径约定：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work

用法：
  python scripts/main.py skeleton --input /input --output /app/output
  python scripts/main.py update   --input /app/output/result.json --patch /app/work/patch.json --phase interpretation --action "写 trends"
  python scripts/main.py validate --input /app/output/result.json
  python scripts/main.py report   --input /app/output/result.json --output /app/output
  python scripts/main.py finalize --input /app/output/result.json
"""

import argparse
import calendar
import copy
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
BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# ---- 输出契约（单一事实来源，validate/skeleton 共用）----
TOP_KEYS = (
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
)

INTERPRETATION_KEYS = (
    "trends", "anomalies", "possible_causes", "verification_steps", "followup_experiments",
)

# 每个列表项的必填子键（id/claim/evidence 通用）
ITEM_KEYS = {
    "trends": ("id", "claim", "direction", "interval", "control", "fit_method", "magnitude", "unit", "evidence"),
    "anomalies": ("id", "claim", "object", "observed", "expected", "deviation", "threshold_method", "threshold_value", "exceeds", "relation_to_control", "evidence"),
    "possible_causes": ("id", "claim", "rank", "confidence", "supporting_evidence", "excluding_evidence", "confound_risk", "testability", "evidence"),
    "verification_steps": ("id", "claim", "action", "target", "expected_if_supported", "decision_rule", "evidence"),
    "followup_experiments": ("id", "claim", "change", "reason", "expected_outcome", "evidence"),
}

PLAN_KEYS = ("hypothesis", "testable_question", "variables", "controls", "steps", "parameters", "resources", "interpretation_criteria")
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")
EVIDENCE_KEYS = ("locator", "quote")
CHANGELOG_KEYS = ("ts", "phase", "action", "scope", "summary")
RUNTIME_KEYS = ("started_at", "finished_at", "elapsed_seconds", "budget_seconds", "budget_requests")

LIST_DEFAULTS = ("interpretation.any", "sources", "uncertainty", "change_log", "errors", "degradations", "limitations", "pending_human")
PLAN_LIST_FIELDS = ("variables", "controls", "steps", "parameters", "resources")
PLAN_STR_FIELDS = ("hypothesis", "testable_question", "interpretation_criteria")


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
    base = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown"
    return base


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def contract_keys_from_case(case_obj):
    """从 case.json 的 output_contract 提取额外必填键（string 键名）。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    names = []
    if isinstance(contract, dict):
        for key in ("fields", "required", "schema"):
            v = contract.get(key)
            if isinstance(v, list):
                names.extend(x for x in v if isinstance(x, str))
        if not names:
            names = [k for k in contract
                     if isinstance(k, str) and k not in ("fields", "required", "schema", "description", "title")]
    elif isinstance(contract, list):
        names = [x for x in contract if isinstance(x, str)]
    # 去重保序
    seen, ordered = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def empty_item(kind):
    item = {k: None for k in ITEM_KEYS.get(kind, ())}
    item["evidence"] = []
    if kind == "possible_causes":
        item["supporting_evidence"] = []
        item["excluding_evidence"] = []
    return item


def default_skeleton(case_id, task_type="experiment_result_interpretation"):
    result = {
        "case_id": case_id or "",
        "status": "partial",
        "task_type": task_type,
        "summary": "",
        "interpretation": {k: [] for k in INTERPRETATION_KEYS},
        "experiment_plan": {
            "hypothesis": None, "testable_question": None,
            "variables": [], "controls": [], "steps": [],
            "parameters": [], "resources": [], "interpretation_criteria": None,
        },
        "evidence_and_risk": {
            "evidence_basis": [], "risk_and_anomaly_handling": None, "assumptions": [],
        },
        "confirmation_items": {
            "safety": None, "ethics": None, "equipment_availability": None, "expert_confirmation": None,
        },
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "ts": now_ts(),
            "phase": "P0-skeleton",
            "action": "skeleton",
            "scope": list(TOP_KEYS),
            "summary": "落盘 partial 骨架（契约字段齐全，待填内容）",
        }],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {
            "started_at": now_ts(),
            "finished_at": None,
            "elapsed_seconds": None,
            "budget_seconds": BUDGET_SECONDS,
            "budget_requests": BUDGET_REQUESTS,
        },
    }
    return result


def merge_output_contract(result, case_obj):
    """若 case.json 含 output_contract，原样回填全部键（缺值给 null/[]），返回新增键名。"""
    names = contract_keys_from_case(case_obj)
    added = []
    for k in names:
        if k not in result:
            result[k] = None
            added.append(k)
    return added


def build_skeleton(input_dir, output_dir):
    case_json_path = os.path.join(input_dir, "case.json")
    case_obj = load_json_safe(case_json_path)
    case_id = resolve_case_id(input_dir)
    result = default_skeleton(case_id)
    added = merge_output_contract(result, case_obj)
    if added:
        result["change_log"].append({
            "ts": now_ts(), "phase": "P0-skeleton", "action": "merge_output_contract",
            "scope": added, "summary": "回填 case.json output_contract 的 %d 个键（缺值 null）" % len(added),
        })
    if os.path.isfile(case_json_path) and case_obj is None:
        result["errors"].append({"locator": "case.json", "quote": "", "note": "case.json 无法解析为 JSON（坏 JSON）"})
    # 空输入标记
    files = [f for f in discover_files(input_dir)
             if f["rel"] not in ("case.json", "task.md") and not f["rel"].startswith(".tmp_")]
    if not files:
        result["status"] = "insufficient_evidence"
        result["summary"] = "未发现任何材料文件"
        result["limitations"].append({"locator": "input", "quote": "", "note": "input 目录无材料文件"})
    os.makedirs(output_dir, exist_ok=True)
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"), render_report(result, include_empty=True))
    return result


def _deep_merge(base, patch):
    """递归合并：dict 递归合并，list/其他类型整体替换。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(patch)


def apply_update(result_path, patch_path, phase, action, summary):
    result = load_json_safe(result_path)
    if result is None:
        raise RuntimeError("无法读取或解析 result.json: %s" % result_path)
    patch = load_json_safe(patch_path)
    if patch is None:
        raise RuntimeError("无法读取或解析 patch: %s" % patch_path)
    before = copy.deepcopy(result)
    result = _deep_merge(result, patch)
    scope = [k for k in result if before.get(k) != result.get(k)] or ["(none)"]
    entry = {
        "ts": now_ts(),
        "phase": phase or "update",
        "action": action or "update",
        "scope": scope,
        "summary": summary or "增量更新",
    }
    result.setdefault("change_log", [])
    if isinstance(result["change_log"], list):
        result["change_log"].append(entry)
    else:
        result["change_log"] = [entry]
    write_json_atomic(result_path, result)
    return scope


def validate(result_path, case_path=None):
    """校验契约与证据完整性。返回 (ok, issues)，issues 每项含 level/path/note。"""
    issues = []
    result = load_json_safe(result_path)
    if result is None:
        return False, [{"level": "error", "path": "root", "note": "result.json 无法解析或缺失"}]
    if not isinstance(result, dict):
        return False, [{"level": "error", "path": "root", "note": "result.json 顶层必须是 object"}]

    for k in TOP_KEYS:
        if k not in result:
            issues.append({"level": "error", "path": k, "note": "缺少必填顶层键 %s" % k})
    if result.get("status") not in STATUSES:
        issues.append({"level": "error", "path": "status", "note": "status 非法: %r" % result.get("status")})
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        issues.append({"level": "error", "path": "case_id", "note": "case_id 必须为非空字符串"})

    interp = result.get("interpretation")
    if isinstance(interp, dict):
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                issues.append({"level": "error", "path": "interpretation." + k, "note": "缺少 interpretation.%s" % k})
                continue
            items = interp.get(k)
            if not isinstance(items, list):
                issues.append({"level": "error", "path": "interpretation." + k, "note": "必须是数组"})
                continue
            if not items:
                issues.append({"level": "info", "path": "interpretation." + k, "note": "为空（如确无该类结论可接受，但应说明原因）"})
            for i, item in enumerate(items):
                base = "interpretation.%s[%d]" % (k, i)
                if not isinstance(item, dict):
                    issues.append({"level": "error", "path": base, "note": "元素必须是 object"})
                    continue
                for sub in ITEM_KEYS.get(k, ()):
                    if sub not in item:
                        issues.append({"level": "error", "path": base + "." + sub, "note": "缺少子键 %s" % sub})
                ev = item.get("evidence")
                if not isinstance(ev, list) or not ev:
                    issues.append({"level": "error", "path": base + ".evidence", "note": "必须为非空证据数组"})
                    continue
                for j, e in enumerate(ev):
                    if not isinstance(e, dict):
                        issues.append({"level": "error", "path": "%s.evidence[%d]" % (base, j), "note": "证据项必须是 object"})
                        continue
                    for ek in EVIDENCE_KEYS:
                        v = e.get(ek)
                        if not isinstance(v, str) or not v.strip():
                            issues.append({"level": "error", "path": "%s.evidence[%d].%s" % (base, j, ek), "note": "%s 必须为非空字符串（locator+逐字 quote）" % ek})
    else:
        issues.append({"level": "error", "path": "interpretation", "note": "interpretation 必须是 object"})

    for group, keys, label in (("experiment_plan", PLAN_KEYS, "experiment_plan"),
                               ("evidence_and_risk", RISK_KEYS, "evidence_and_risk"),
                               ("confirmation_items", CONFIRM_KEYS, "confirmation_items")):
        obj = result.get(group)
        if not isinstance(obj, dict):
            issues.append({"level": "error", "path": group, "note": "%s 必须是 object" % group})
            continue
        for k in keys:
            if k not in obj:
                issues.append({"level": "error", "path": "%s.%s" % (group, k), "note": "缺少 %s.%s" % (group, k)})

    clog = result.get("change_log")
    if not isinstance(clog, list) or not clog:
        issues.append({"level": "error", "path": "change_log", "note": "change_log 必须为非空数组"})
    else:
        for i, e in enumerate(clog):
            if not isinstance(e, dict):
                issues.append({"level": "error", "path": "change_log[%d]" % i, "note": "元素必须是 object"})
                continue
            for ck in ("ts", "phase", "action", "summary"):
                if ck not in e:
                    issues.append({"level": "error", "path": "change_log[%d].%s" % (i, ck), "note": "缺少 %s" % ck})

    rt = result.get("runtime")
    if not isinstance(rt, dict):
        issues.append({"level": "error", "path": "runtime", "note": "runtime 必须是 object"})
    else:
        for rk in RUNTIME_KEYS:
            if rk not in rt:
                issues.append({"level": "error", "path": "runtime." + rk, "note": "缺少 runtime.%s" % rk})

    # case.json 额外契约键也要存在
    if case_path and os.path.isfile(case_path):
        case_obj = load_json_safe(case_path)
        for k in contract_keys_from_case(case_obj):
            if k not in result:
                issues.append({"level": "error", "path": k, "note": "output_contract 要求的键 %s 缺失" % k})

    errors = [i for i in issues if i["level"] == "error"]
    ok = not errors
    return ok, issues


def render_report(result, include_empty=False):
    lines = []
    lines.append("# 实验结果解释与异常分析报告\n")
    lines.append("- case_id: `%s`" % result.get("case_id", ""))
    lines.append("- status: `%s`" % result.get("status", ""))
    lines.append("- task_type: `%s`\n" % result.get("task_type", ""))
    lines.append("## 摘要\n")
    lines.append(str(result.get("summary") or "（待填写）") + "\n")

    interp = result.get("interpretation") or {}
    titles = {
        "trends": "趋势", "anomalies": "异常点", "possible_causes": "可能原因",
        "verification_steps": "复核步骤", "followup_experiments": "后续实验建议",
    }
    for key, title in titles.items():
        lines.append("## %s\n" % title)
        items = interp.get(key) or []
        if not items:
            lines.append("（无）\n")
            continue
        for i, item in enumerate(items, 1):
            claim = item.get("claim") or item.get("action") or item.get("change") or "(无 claim)"
            lines.append("### %s.%d %s\n" % (title, i, claim))
            for fk, fv in item.items():
                if fk in ("claim", "evidence", "id"):
                    continue
                if isinstance(fv, (list, dict)):
                    lines.append("- **%s**: `%s`" % (fk, json.dumps(fv, ensure_ascii=False)))
                elif fv not in (None, ""):
                    lines.append("- **%s**: %s" % (fk, fv))
            ev = item.get("evidence") or []
            if ev:
                lines.append("- **evidence**:")
                for e in ev:
                    lines.append("  - `%s` → %s" % (e.get("locator", ""), e.get("quote", "")))
            lines.append("")

    lines.append("## 实验方案\n")
    plan = result.get("experiment_plan") or {}
    for k in PLAN_KEYS:
        v = plan.get(k)
        if isinstance(v, list):
            lines.append("- **%s**: %s" % (k, json.dumps(v, ensure_ascii=False)))
        elif v:
            lines.append("- **%s**: %s" % (k, v))
    lines.append("")

    lines.append("## 依据与风险\n")
    risk = result.get("evidence_and_risk") or {}
    for k in RISK_KEYS:
        v = risk.get(k)
        if isinstance(v, list):
            lines.append("- **%s**: %s" % (k, json.dumps(v, ensure_ascii=False)))
        elif v:
            lines.append("- **%s**: %s" % (k, v))
    lines.append("")

    lines.append("## 确认项\n")
    conf = result.get("confirmation_items") or {}
    for k in CONFIRM_KEYS:
        if conf.get(k):
            lines.append("- **%s**: %s" % (k, conf[k]))
    lines.append("")

    for sec in ("sources", "uncertainty", "limitations", "pending_human", "errors", "degradations"):
        vals = result.get(sec) or []
        if vals:
            lines.append("## %s\n" % sec)
            for v in vals:
                if isinstance(v, dict):
                    lines.append("- %s" % json.dumps(v, ensure_ascii=False))
                else:
                    lines.append("- %s" % v)
            lines.append("")

    clog = result.get("change_log") or []
    if clog:
        lines.append("## change_log\n")
        for e in clog:
            lines.append("- `%s` [%s] %s: %s" % (e.get("ts", ""), e.get("phase", ""), e.get("action", ""), e.get("summary", "")))
        lines.append("")

    rt = result.get("runtime") or {}
    if rt:
        lines.append("## runtime\n")
        lines.append("- %s\n" % json.dumps(rt, ensure_ascii=False))

    return "\n".join(lines) + "\n"


def build_report(result_path, output_dir):
    result = load_json_safe(result_path)
    if result is None:
        raise RuntimeError("无法读取 result.json: %s" % result_path)
    os.makedirs(output_dir, exist_ok=True)
    write_text_atomic(os.path.join(output_dir, "report.md"), render_report(result))
    # evidence_map：规范化证据映射
    evmap = []
    interp = result.get("interpretation") or {}
    for section, items in interp.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for e in (item.get("evidence") or []):
                if isinstance(e, dict):
                    evmap.append({"section": section, "id": item.get("id"), "locator": e.get("locator"), "quote": e.get("quote")})
    write_json_atomic(os.path.join(output_dir, "evidence_map.json"), {"case_id": result.get("case_id"), "evidence": evmap})
    return render_report(result)


def finalize(result_path):
    result = load_json_safe(result_path)
    if result is None:
        raise RuntimeError("无法读取 result.json: %s" % result_path)
    rt = result.setdefault("runtime", {})
    rt["started_at"] = rt.get("started_at") or now_ts()
    rt["finished_at"] = now_ts()
    try:
        start = calendar.timegm(time.strptime(rt["started_at"], "%Y-%m-%dT%H:%M:%SZ"))
        rt["elapsed_seconds"] = max(0, int(time.time() - start))
    except (ValueError, TypeError, KeyError):
        rt["elapsed_seconds"] = None
    rt.setdefault("budget_seconds", BUDGET_SECONDS)
    rt.setdefault("budget_requests", BUDGET_REQUESTS)
    if result.get("status") == "partial":
        result["change_log"].append({
            "ts": now_ts(), "phase": "P3-finalize", "action": "finalize",
            "scope": ["runtime"], "summary": "定稿并写入 runtime（status 仍为 partial，请据实确认）",
        })
    write_json_atomic(result_path, result)
    return result


def _arg_namespace(**kw):
    return argparse.Namespace(**kw)


def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description="实验结果解释 Skill 主入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("skeleton", help="落盘契约骨架 result.json + report.md")
    ps.add_argument("--input", default=DEFAULT_INPUT)
    ps.add_argument("--output", default=DEFAULT_OUTPUT)

    pu = sub.add_parser("update", help="增量合并 patch.json 并追加 change_log")
    pu.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    pu.add_argument("--patch", required=True)
    pu.add_argument("--phase", default="update")
    pu.add_argument("--action", default="update")
    pu.add_argument("--summary", default="")

    pv = sub.add_parser("validate", help="校验契约与证据完整性")
    pv.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    pv.add_argument("--case", default=None, help="case.json 路径（用于校验额外契约键）")

    pr = sub.add_parser("report", help="生成 report.md 与 evidence_map.json")
    pr.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    pr.add_argument("--output", default=DEFAULT_OUTPUT)

    pf = sub.add_parser("finalize", help="写入 runtime 与定稿 change_log")
    pf.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))

    args = p.parse_args(argv)

    if args.cmd == "skeleton":
        r = build_skeleton(args.input, args.output)
        print("skeleton: case_id=%s status=%s" % (r["case_id"], r["status"]))
    elif args.cmd == "update":
        scope = apply_update(args.input, args.patch, args.phase, args.action, args.summary)
        print("update: scope=%s" % ",".join(scope))
    elif args.cmd == "validate":
        ok, issues = validate(args.input, args.case)
        for i in issues:
            print("[%s] %s: %s" % (i["level"], i["path"], i["note"]))
        print("validate: %s (%d issues)" % ("OK" if ok else "FAIL", len(issues)))
        return 0 if ok else 1
    elif args.cmd == "report":
        build_report(args.input, args.output)
        print("report: wrote report.md + evidence_map.json")
    elif args.cmd == "finalize":
        r = finalize(args.input)
        print("finalize: status=%s elapsed=%s" % (r.get("status"), r.get("runtime", {}).get("elapsed_seconds")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
