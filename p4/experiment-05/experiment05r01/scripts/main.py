#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验结果解释与异常分析 Skill 主入口。

仅使用 Python 标准库。运行时路径约定（Linux 评测环境）：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work
在本地 Windows 自检时用 --input/--output 显式传路径即可，逻辑与平台无关。

子命令：
  skeleton  读 case.json + task.md，立即落盘一版合法 partial result.json 与 report.md 骨架
  evidence  从 result.json 中提取每条 locator+quote 证据，生成细粒度 evidence_map.json
  report    由 result.json 生成可编辑 report.md（含证据索引）
  validate  校验 result.json 的 schema、状态与证据完整性，退出码 0=通过
  finalize  写 runtime 结束时间并追加 change_log
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import types

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000
QUOTE_MAX = 400

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 无机器可读 output_contract 时的兜底契约（与题目“实验方案/依据与风险/确认项”对应）
FALLBACK_CONTRACT = {
    "interpretation": {
        "trends": [], "anomalies": [], "possible_causes": [],
        "verification_steps": [], "followup_experiments": [],
    },
    "experiment_plan": {
        "hypothesis": None, "testable_question": None, "variables": [],
        "controls": [], "steps": [], "parameters": [],
        "resources": [], "interpretation_criteria": [],
    },
    "evidence_and_risk": {
        "evidence_basis": [], "risk_and_anomaly_handling": [], "assumptions": [],
    },
    "confirmation_items": {
        "safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": [],
    },
}

TOP_KEYS = (
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "evidence_map", "sources", "uncertainty",
    "change_log", "errors", "degradations", "limitations",
    "pending_human", "runtime",
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

EVIDENCE_MARKER_KEYS = ("evidence", "supporting_evidence", "excluding_evidence")


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    sys.stderr.write("[main] %s\n" % msg)


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


def atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_%s_%d" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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
        for pat in (r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)",
                    r"challenge[-_ ]([0-9a-fA-F-]{8,})"):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
    base = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown"
    return base


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def _flatten_contract_names(contract):
    """从 output_contract 中抽取需回填的键名，保留嵌套 dict 结构原样回填。"""
    names = []
    if isinstance(contract, list):
        return [x for x in contract if isinstance(x, str)]
    if not isinstance(contract, dict):
        return []
    for key in ("fields", "required", "schema"):
        v = contract.get(key)
        if isinstance(v, list):
            names.extend(x for x in v if isinstance(x, str))
    if not names:
        names = [k for k in contract
                 if isinstance(k, str) and k not in ("fields", "required", "schema", "description", "title")]
    return names


def build_skeleton(input_dir, output_dir):
    """读取输入并落盘合法骨架，返回 (result, change_log 初始项, 缺口说明)。"""
    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    case_id = resolve_case_id(input_dir)
    result = {
        "case_id": case_id,
        "status": "partial",
        "task_type": "experiment_result_interpretation",
        "summary": None,
        "interpretation": {k: [] for k in INTERPRETATION_KEYS},
        "experiment_plan": {k: (_default_for(v)) for k, v in FALLBACK_CONTRACT["experiment_plan"].items()},
        "evidence_and_risk": {k: [] for k in RISK_KEYS},
        "confirmation_items": {k: [] for k in CONFIRM_KEYS},
        "evidence_map": None,
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"start": now_ts(), "end": None, "seconds": None},
    }

    merged = []
    if isinstance(case_obj, dict) and case_obj.get("output_contract") is not None:
        contract = case_obj["output_contract"]
        names = _flatten_contract_names(contract)
        for k in names:
            if k not in result:
                default = _default_for(contract.get(k)) if isinstance(contract, dict) and k in contract else None
                result[k] = default
                merged.append(k)

    # 检查输入是否存在，为空则降级为 insufficient_evidence
    files = discover_files(input_dir)
    gaps = []
    if not files:
        result["status"] = "insufficient_evidence"
        gaps.append("input directory has no discoverable files")
    if case_obj is None:
        gaps.append("case.json missing or unparsable")

    entry = {"at": now_ts(), "stage": "skeleton",
             "note": "partial skeleton written; case_id=%s; merged_contract_keys=%s" % (case_id, merged)}
    if gaps:
        entry["note"] += "; gaps=" + "; ".join(gaps)
        result["limitations"].append({"item": "input gaps at skeleton time", "detail": gaps})
    result["change_log"].append(entry)

    os.makedirs(output_dir, exist_ok=True)
    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    report = skeleton_report(result)
    atomic_write(os.path.join(output_dir, "report.md"), report)
    return result, gaps


def skeleton_report(result):
    lines = [
        "# 实验结果解释与异常分析（骨架）",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`（正式结论定稿前为 partial）" % result.get("status"),
        "- 生成时间: %s" % result["runtime"]["start"],
        "",
        "> 本文件是增量产物骨架。每完成一个阶段（数据清点 → 五段结论 → 证据映射 → 定稿）",
        "> 就覆盖写回一次；最终版本的每条结论必须带 evidence（locator + 逐字 quote）。",
        "",
        "## 五段结论",
        "",
    ]
    for key in INTERPRETATION_KEYS:
        lines.append("### %s" % key)
        lines.append("（待补充）")
        lines.append("")
    lines += [
        "## 实验方案 / 依据与风险 / 确认项",
        "",
        "（待补充：hypothesis、variables、controls、steps、parameters、resources、interpretation_criteria）",
        "",
    ]
    return "\n".join(lines)


def _looks_like_evidence(node):
    if not isinstance(node, dict):
        return False
    return ("locator" in node and isinstance(node["locator"], str) and node["locator"].strip()
            and "quote" in node and isinstance(node["quote"], str) and node["quote"].strip())


def _walk(node, path, out):
    if isinstance(node, dict):
        if _looks_like_evidence(node):
            out.append((path, node))
        for k, v in node.items():
            _walk(v, path + [k], out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, path + [i], out)


def _content_hash(obj):
    h = hashlib.sha256()
    h.update(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:12]


def build_evidence_map(result):
    """细粒度证据映射：证据条目 → 声明归属 → 冲突与覆盖统计。

    evidence_map 结构：
      schema_version / case_id / generated_at / claims / evidence / claim_to_evidence / conflicts / coverage
    """
    claims = []
    seen_claim = {}

    def add_claim(claim_id, ctype, statement):
        if claim_id in seen_claim:
            seen_claim[claim_id]["types"].add(ctype)
            return
        seen_claim[claim_id] = {"types": {ctype}}
        claims.append({"claim_id": claim_id, "type": ctype,
                       "statement": (statement or "").strip()[:500]})

    # 注册五段结论与实验方案各条目为 claim
    interp = result.get("interpretation") or {}
    for sec in INTERPRETATION_KEYS:
        items = interp.get(sec)
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            if isinstance(it, dict):
                cid = it.get("id") or ("%s-%02d" % (sec, i + 1))
                add_claim(cid, sec, it.get("statement") or it.get("conclusion") or it.get("description") or "")
    plan = result.get("experiment_plan") or {}
    for sec in ("hypothesis", "testable_question"):
        v = plan.get(sec)
        if isinstance(v, str) and v.strip():
            add_claim("plan-" + sec, sec, v)

    evidence = []
    by_hash = {}
    claim_to_evidence = {}
    nodes = []
    _walk(result, [], nodes)
    for path, node in nodes:
        h = _content_hash(node)
        eid = node.get("evidence_id") or ("E%03d" % (len(evidence) + 1))
        if h in by_hash:
            eid = by_hash[h]
        else:
            by_hash[h] = eid
            rec = {
                "evidence_id": eid,
                "locator": node.get("locator"),
                "quote": (node.get("quote") or "")[:QUOTE_MAX],
                "metric": node.get("metric"),
                "value": node.get("value"),
                "unit": node.get("unit"),
                "method": node.get("method"),
                "threshold": node.get("threshold"),
                "source_conflict": node.get("source_conflict", False),
                "confidence": node.get("confidence"),
                "located_at": path,
            }
            evidence.append(rec)
        # 归属到最近的 claim：沿路径向上找含 id 的 dict
        for depth in range(len(path), 0, -1):
            parent = _get_by_path(result, path[:depth])
            if isinstance(parent, dict) and isinstance(parent.get("id"), str):
                claim_to_evidence.setdefault(parent["id"], [])
                if eid not in claim_to_evidence[parent["id"]]:
                    claim_to_evidence[parent["id"]].append(eid)
                break

    conflicts = []
    for e in evidence:
        if e.get("source_conflict"):
            conflicts.append({"evidence_id": e["evidence_id"], "locator": e["locator"],
                              "note": e.get("source_conflict") if isinstance(e["source_conflict"], str) else "marked source_conflict"})

    total_claims = len(claims)
    claimed_with_evidence = sum(1 for c in claims if claim_to_evidence.get(c["claim_id"]))
    coverage = {
        "total_claims": total_claims,
        "claims_with_evidence": claimed_with_evidence,
        "claims_without_evidence": [c["claim_id"] for c in claims if not claim_to_evidence.get(c["claim_id"])],
        "total_evidence": len(evidence),
        "total_conflicts": len(conflicts),
    }
    return {
        "schema_version": "1.0",
        "case_id": result.get("case_id"),
        "generated_at": now_ts(),
        "claims": claims,
        "evidence": evidence,
        "claim_to_evidence": claim_to_evidence,
        "conflicts": conflicts,
        "coverage": coverage,
    }


def _get_by_path(obj, path):
    cur = obj
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int):
            cur = cur[p] if 0 <= p < len(cur) else None
        else:
            return None
    return cur


def parser_placeholder(input_path, output_dir):
    """自检脚本复用：构造与 argparse 命名空间等价的轻量对象。"""
    return types.SimpleNamespace(input=input_path, output=output_dir)


def cmd_skeleton(args):
    result, gaps = build_skeleton(args.input, args.output)
    log("skeleton written: status=%s case_id=%s gaps=%d" % (result["status"], result["case_id"], len(gaps)))
    return 0


def cmd_evidence(args):
    result = load_json_safe(args.input)
    if result is None:
        log("cannot load result.json: %s" % args.input)
        return 2
    emap = build_evidence_map(result)
    result["evidence_map"] = {
        "file": "evidence_map.json",
        "generated_at": emap["generated_at"],
        "coverage": emap["coverage"],
    }
    atomic_write_json(args.input, result)
    atomic_write_json(os.path.join(args.output, "evidence_map.json"), emap)
    cov = emap["coverage"]
    log("evidence_map written: evidence=%d claims_with_evidence=%d/%d conflicts=%d" %
        (cov["total_evidence"], cov["claims_with_evidence"], cov["total_claims"], cov["total_conflicts"]))
    return 0


def cmd_report(args):
    result = load_json_safe(args.input)
    if result is None:
        log("cannot load result.json: %s" % args.input)
        return 2
    os.makedirs(args.output, exist_ok=True)
    text = render_report(result)
    atomic_write(os.path.join(args.output, "report.md"), text)
    log("report.md written")
    return 0


def render_report(result):
    out = []
    out.append("# 实验结果解释与异常分析")
    out.append("")
    out.append("- case_id: `%s`" % result.get("case_id"))
    out.append("- status: `%s`" % result.get("status"))
    out.append("- summary: %s" % (result.get("summary") or "（未提供）"))
    out.append("")
    interp = result.get("interpretation") or {}
    titles = {
        "trends": "1. 结果趋势",
        "anomalies": "2. 异常点",
        "possible_causes": "3. 可能原因",
        "verification_steps": "4. 复核步骤",
        "followup_experiments": "5. 后续实验建议",
    }
    for sec in INTERPRETATION_KEYS:
        out.append("## %s" % titles[sec])
        out.append("")
        items = interp.get(sec) or []
        if not items:
            out.append("（空：%s）" % (result.get("uncertainty") and "见 uncertainty" or "未填写"))
            out.append("")
            continue
        for it in items:
            if isinstance(it, dict):
                head = it.get("id") or ""
                text = it.get("statement") or it.get("conclusion") or it.get("description") or json.dumps(it, ensure_ascii=False)
                out.append("### %s %s" % (head, (text or "").strip()[:200]))
                _append_evidence_md(out, it)
                for extra in ("supporting_evidence", "excluding_evidence"):
                    if isinstance(it.get(extra), list) and it[extra]:
                        out.append("- %s:" % extra)
                        for e in it[extra]:
                            if isinstance(e, dict) and e.get("quote"):
                                out.append("  - [%s] `%s`" % (e.get("evidence_id", "?"), e.get("quote")[:120]))
            else:
                out.append("- %s" % it)
            out.append("")
    # 实验方案与风险
    plan = result.get("experiment_plan") or {}
    out.append("## 实验方案")
    out.append("")
    for k in PLAN_KEYS:
        v = plan.get(k)
        out.append("- **%s**: %s" % (k, _fmt_value(v)))
    risk = result.get("evidence_and_risk") or {}
    out.append("")
    out.append("## 依据与风险")
    out.append("")
    for k in RISK_KEYS:
        out.append("- **%s**: %s" % (k, _fmt_value(risk.get(k))))
    conf = result.get("confirmation_items") or {}
    out.append("")
    out.append("## 确认项")
    out.append("")
    for k in CONFIRM_KEYS:
        out.append("- **%s**: %s" % (k, _fmt_value(conf.get(k))))
    for sec, key in (("局限与降级", "limitations"), ("待人工确认", "pending_human"), ("不确定性", "uncertainty")):
        vals = result.get(key) or []
        if vals:
            out.append("")
            out.append("## %s" % sec)
            out.append("")
            for v in vals:
                out.append("- %s" % _fmt_value(v))
    return "\n".join(out) + "\n"


def _append_evidence_md(out, item):
    for key in ("evidence", "supporting_evidence", "excluding_evidence"):
        ev = item.get(key)
        if isinstance(ev, list) and ev:
            out.append("- 证据（%s）:" % key)
            for e in ev:
                if isinstance(e, dict) and e.get("quote"):
                    out.append("  - [%s] `%s` @ %s" % (
                        e.get("evidence_id", "?"), e.get("quote")[:140], e.get("locator", "?")))


def _fmt_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return "（未提供）"
    return str(v)


def cmd_validate(args):
    result = load_json_safe(args.input)
    if result is None:
        log("FAIL: result.json 无法被 json.load 解析")
        return 1
    problems = []
    if not (isinstance(result.get("case_id"), str) and result["case_id"].strip()):
        problems.append("case_id 缺失或为空")
    if result.get("status") not in STATUSES:
        problems.append("status 非法：%r" % result.get("status"))
    for k in TOP_KEYS:
        if k not in result:
            problems.append("缺少顶层字段 %s" % k)
    interp = result.get("interpretation")
    if not isinstance(interp, dict):
        problems.append("interpretation 不是对象")
    else:
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                problems.append("interpretation 缺少 %s" % k)
    # 证据完整性：每个五段结论条目都应带 evidence（locator+quote）
    evidence_nodes = []
    _walk(result, [], evidence_nodes)
    if result.get("status") in ("ok", "partial"):
        for sec in INTERPRETATION_KEYS:
            items = (interp or {}).get(sec) or []
            for i, it in enumerate(items):
                if isinstance(it, dict):
                    ev = it.get("evidence")
                    has = isinstance(ev, list) and any(_looks_like_evidence(e) for e in ev)
                    if not has and it.get("statement"):
                        problems.append("%s 第 %d 条缺 evidence（locator+quote）" % (sec, i + 1))
    if problems:
        for p in problems:
            log("FAIL: " + p)
        return 1
    log("OK: schema 与证据完整性通过（evidence 条目数=%d）" % len(evidence_nodes))
    return 0


def cmd_finalize(args):
    result = load_json_safe(args.input)
    if result is None:
        log("cannot load result.json: %s" % args.input)
        return 2
    rt = result.setdefault("runtime", {})
    start = rt.get("start")
    end = now_ts()
    rt["end"] = end
    if isinstance(start, str):
        try:
            from datetime import datetime
            s = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
            e = datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
            rt["seconds"] = max(0, int((e - s).total_seconds()))
        except ValueError:
            rt["seconds"] = None
    result.setdefault("change_log", []).append(
        {"at": end, "stage": "finalize", "note": "finalized"})
    atomic_write_json(args.input, result)
    log("finalized: runtime=%s" % rt)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="实验结果解释与异常分析 Skill")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("skeleton")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    p.set_defaults(func=cmd_skeleton)

    p = sub.add_parser("evidence")
    p.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("report")
    p.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("validate")
    p.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("finalize")
    p.add_argument("--input", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p.set_defaults(func=cmd_finalize)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
