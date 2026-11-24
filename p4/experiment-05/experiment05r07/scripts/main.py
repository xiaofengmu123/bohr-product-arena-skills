#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验结果解释 Skill 主入口：骨架落盘 / 契约校验 / 报告生成 / 定稿。

仅使用 Python 标准库。运行时路径约定：
  --input   默认 /input
  --output  默认 /app/output
  --work    默认 /app/work

用法：
  python scripts/main.py skeleton --input /input --output /app/output
  python scripts/main.py validate --input /app/output/result.json
  python scripts/main.py report   --input /app/output/result.json --output /app/output
  python scripts/main.py finalize --input /app/output/result.json

设计取向（鲁棒性变体）：确定性输出、原子写盘、编码阶梯降级、空输入/坏输入
优雅降级不崩溃；任何子命令都尽量以退出码 0 收尾并留下合法产物。
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
    "case_id", "status", "task_type", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human", "runtime",
)
INTERPRETATION_KEYS = (
    "trends", "anomalies", "possible_causes", "verification_steps", "followup_experiments",
)
PLAN_KEYS = (
    "hypothesis", "testable_question", "variables", "controls", "steps",
    "parameters", "resources", "interpretation_criteria",
)
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")
EVIDENCE_KEYS = ("evidence", "supporting_evidence", "excluding_evidence")


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# 鲁棒文件读取
# --------------------------------------------------------------------------
def read_text(path, max_bytes=MAX_FILE_BYTES):
    """按编码阶梯读取文本文件，返回 (text, encoding, note)。

    阶梯：utf-8-sig -> utf-8 -> utf-16 -> gbk -> latin-1；
    含 NUL 字节视为二进制；超限返回 (None, None, reason)。
    """
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, None, "unreadable: %s" % exc
    size = len(raw)
    if size > max_bytes:
        return None, None, "file exceeds %d bytes" % max_bytes
    if b"\x00" in raw:
        return None, None, "binary content (NUL byte present)"
    text, used = None, None
    # UTF-16 仅按 BOM 识别，避免偶数字节串被误当 UTF-16 解码
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16"), "utf-16", None
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            used = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return None, None, "undecodable"
    note = None
    if used == "latin-1":
        note = "decoded with latin-1 fallback (original encoding lost)"
    elif used == "gbk":
        note = "decoded with gbk fallback"
    return text, used, note


def load_json_safe(path):
    text, _, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def write_text_atomic(path, text):
    """原子写盘：先写临时文件再 os.replace；失败时降级为直接写，再失败抛异常由调用方记录。"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_%s_%d" % (os.path.basename(path), os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


def write_json_atomic(path, obj):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def sha256_head(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def discover_files(root):
    """确定性文件清单：目录与文件名均排序；跳过符号链接。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append({
                "path": full,
                "rel": rel,
                "size_bytes": size,
                "ext": os.path.splitext(fn)[1].lower(),
            })
    return out


# --------------------------------------------------------------------------
# case_id 解析
# --------------------------------------------------------------------------
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
    text, _, _ = read_text(os.path.join(input_dir, "task.md"))
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


def merge_output_contract(result, case_obj):
    """若 case.json 含 output_contract，原样回填其全部键（缺值给 null/[]），不改名不删未知键。"""
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
            names = [k for k in contract
                     if isinstance(k, str) and k not in ("fields", "required", "schema", "description", "title")]
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


# --------------------------------------------------------------------------
# 骨架
# --------------------------------------------------------------------------
def build_skeleton(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    files = discover_files(input_dir)
    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    task_text, _, _ = read_text(os.path.join(input_dir, "task.md"))
    case_id = resolve_case_id(input_dir)

    errors, degradations = [], []
    case_path = os.path.join(input_dir, "case.json")
    if os.path.exists(case_path) and case_obj is None:
        errors.append({"locator": "input/case.json", "issue": "unparseable_json",
                       "detail": "case.json exists but is not valid JSON"})
    task_path = os.path.join(input_dir, "task.md")
    if os.path.exists(task_path) and task_text is None:
        degradations.append({"locator": "input/task.md", "issue": "undecodable_text",
                             "detail": "task.md could not be decoded"})

    material_files = [f for f in files
                      if not f["rel"].startswith(".")
                      and f["rel"] not in ("case.json", "task.md")]
    empty = case_obj is None and task_text is None and not material_files

    result = {
        "case_id": case_id,
        "status": "insufficient_evidence" if empty else "partial",
        "task_type": "experiment_result_interpretation",
        "summary": None,
        "interpretation": {k: [] for k in INTERPRETATION_KEYS},
        "experiment_plan": {k: ([] if k in ("variables", "controls", "steps", "parameters", "resources") else None)
                            for k in PLAN_KEYS},
        "evidence_and_risk": {k: None for k in RISK_KEYS},
        "confirmation_items": {k: None for k in CONFIRM_KEYS},
        "sources": [],
        "uncertainty": [],
        "change_log": [{"stage": "skeleton", "ts": now_ts(),
                        "note": "skeleton written; input=%s" % ("empty" if empty else "present")}],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_at": now_ts(), "finished_at": None, "duration_s": None},
    }
    merged = merge_output_contract(result, case_obj)
    if merged:
        result["change_log"].append({"stage": "skeleton", "ts": now_ts(),
                                     "note": "merged output_contract keys: %s" % ", ".join(merged)})
    if empty:
        result["limitations"].append("input directory contains no case.json, task.md or material files")
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    # 同步落一份最小 report.md 骨架
    write_text_atomic(os.path.join(output_dir, "report.md"), _report_markdown(result))
    return result


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def _iter_evidence(obj, path=""):
    """递归收集所有证据条目 (locator, quote, source_path)。"""
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in EVIDENCE_KEYS and isinstance(v, list):
                for i, e in enumerate(v):
                    if isinstance(e, dict):
                        items.append((e.get("locator"), e.get("quote"), "%s.%s[%d]" % (path, k, i)))
            items.extend(_iter_evidence(v, ("%s.%s" % (path, k)).strip(".")))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.extend(_iter_evidence(v, "%s[%d]" % (path, i)))
    return items


def validate(result_path):
    obj = load_json_safe(result_path)
    problems, warnings = [], []
    if obj is None:
        return 1, ["result.json is not valid JSON"], []
    if not isinstance(obj, dict):
        return 1, ["result.json top-level is not an object"], []

    missing = [k for k in TOP_KEYS if k not in obj]
    if missing:
        problems.append("missing top-level keys: %s" % ", ".join(missing))
    case_id = obj.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        problems.append("case_id empty or not a string")
    if obj.get("status") not in STATUSES:
        problems.append("status not in %s" % str(STATUSES))

    interp = obj.get("interpretation")
    if isinstance(interp, dict):
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                problems.append("interpretation missing key: %s" % k)
            elif interp[k] in (None, "", []):
                warnings.append("interpretation.%s is empty" % k)
            elif isinstance(interp[k], list):
                for i, item in enumerate(interp[k]):
                    if isinstance(item, dict) and not any(
                            isinstance(item.get(e), list) and len(item[e]) > 0 for e in EVIDENCE_KEYS):
                        warnings.append("interpretation.%s[%d] has no evidence" % (k, i))
    else:
        problems.append("interpretation is not an object")

    evs = _iter_evidence(obj)
    for loc, quote, src in evs:
        if not loc:
            warnings.append("%s: evidence missing locator" % src)
        if not quote:
            warnings.append("%s: evidence missing quote" % src)

    return (1 if problems else 0), problems, warnings


# --------------------------------------------------------------------------
# 报告 / evidence_map
# --------------------------------------------------------------------------
def _kv_block(item):
    lines = []
    if isinstance(item, dict):
        for k, v in item.items():
            if k in EVIDENCE_KEYS:
                continue
            lines.append("- **%s**: %s" % (k, _fmt(v)))
        for ek in ("supporting_evidence", "excluding_evidence", "evidence"):
            ev = item.get(ek)
            if isinstance(ev, list) and ev:
                for e in ev:
                    if isinstance(e, dict):
                        loc = e.get("locator") or "?"
                        quote = (e.get("quote") or "").strip().replace("\n", " ")
                        lines.append("  - 证据 `%s`：> %s" % (loc, quote))
    else:
        lines.append("- %s" % _fmt(item))
    return lines


def _fmt(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return "—"
    return str(v)


def _report_markdown(obj):
    L = []
    L.append("# 实验结果解释与异常分析报告")
    L.append("")
    L.append("- **case_id**: %s" % (obj.get("case_id") or "—"))
    L.append("- **status**: %s" % (obj.get("status") or "—"))
    L.append("- **task_type**: %s" % (obj.get("task_type") or "—"))
    L.append("")
    L.append("## 摘要")
    L.append("")
    L.append(str(obj.get("summary") or "（尚未填写）"))
    interp = obj.get("interpretation") or {}
    titles = {
        "trends": "趋势", "anomalies": "异常点", "possible_causes": "可能原因",
        "verification_steps": "复核步骤", "followup_experiments": "后续实验建议",
    }
    for key in INTERPRETATION_KEYS:
        L.append("")
        L.append("## %s" % titles[key])
        L.append("")
        items = interp.get(key) or []
        if not items:
            L.append("（空）")
        else:
            for item in items:
                L.extend(_kv_block(item))
                L.append("")
    for sec, title in (("experiment_plan", "实验方案"), ("evidence_and_risk", "依据与风险"),
                       ("confirmation_items", "确认项")):
        L.append("## %s" % title)
        L.append("")
        sec_obj = obj.get(sec) or {}
        if isinstance(sec_obj, dict):
            for k, v in sec_obj.items():
                if isinstance(v, list):
                    L.append("### %s" % k)
                    L.append("")
                    for item in v:
                        L.extend(_kv_block(item))
                    L.append("")
                else:
                    L.append("- **%s**: %s" % (k, _fmt(v)))
        L.append("")
    for sec, title in (("sources", "来源"), ("uncertainty", "不确定性"),
                       ("errors", "错误"), ("degradations", "降级记录"),
                       ("limitations", "限制"), ("pending_human", "待人工确认")):
        L.append("## %s" % title)
        L.append("")
        val = obj.get(sec)
        if isinstance(val, list):
            if not val:
                L.append("（空）")
            for item in val:
                L.extend(_kv_block(item))
                L.append("")
        else:
            L.append(_fmt(val))
        L.append("")
    L.append("## 变更记录")
    L.append("")
    for c in obj.get("change_log") or []:
        L.append("- [%s] %s: %s" % (c.get("ts") or "?", c.get("stage") or "?", c.get("note") or ""))
    L.append("")
    return "\n".join(L)


def build_report(result_path, output_dir):
    obj = load_json_safe(result_path)
    if obj is None:
        return 1, "result.json not parseable"
    os.makedirs(output_dir, exist_ok=True)
    write_text_atomic(os.path.join(output_dir, "report.md"), _report_markdown(obj))
    evmap = []
    for loc, quote, src in _iter_evidence(obj):
        evmap.append({"source_path": src, "locator": loc, "quote": quote})
    evmap.sort(key=lambda x: (x["source_path"], x.get("locator") or ""))
    write_json_atomic(os.path.join(output_dir, "evidence_map.json"), evmap)
    return 0, "report.md and evidence_map.json written"


# --------------------------------------------------------------------------
# 定稿
# --------------------------------------------------------------------------
def finalize(result_path):
    obj = load_json_safe(result_path)
    if obj is None:
        return 1, "result.json not parseable"
    rt = obj.setdefault("runtime", {})
    if not isinstance(rt, dict):
        rt = {}
        obj["runtime"] = rt
    finished = now_ts()
    rt["finished_at"] = finished
    start = rt.get("started_at")
    dur = None
    if isinstance(start, str):
        try:
            t0 = time.mktime(time.strptime(start, "%Y-%m-%dT%H:%M:%SZ"))
            t1 = time.mktime(time.strptime(finished, "%Y-%m-%dT%H:%M:%SZ"))
            dur = max(0, int(t1 - t0))
        except (ValueError, OverflowError):
            dur = None
    rt["duration_s"] = dur
    logs = obj.setdefault("change_log", [])
    if not isinstance(logs, list):
        logs = []
        obj["change_log"] = logs
    logs.append({"stage": "finalize", "ts": finished, "note": "runtime finalized"})
    write_json_atomic(result_path, obj)
    return 0, "finalized"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description="实验解释 Skill 主入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("skeleton", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=DEFAULT_INPUT)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
    vp = sub.add_parser("validate")
    vp.add_argument("--input", required=True)
    fp = sub.add_parser("finalize")
    fp.add_argument("--input", required=True)

    args = p.parse_args(argv)
    code, msg = 0, "ok"
    try:
        if args.cmd == "skeleton":
            r = build_skeleton(args.input, args.output)
            msg = "skeleton written; status=%s; case_id=%s" % (r["status"], r["case_id"])
        elif args.cmd == "validate":
            code, problems, warnings = validate(args.input)
            for x in problems:
                print("PROBLEM: %s" % x, file=sys.stderr)
            for x in warnings:
                print("WARNING: %s" % x, file=sys.stderr)
            msg = "validate: %d problem(s), %d warning(s)" % (len(problems), len(warnings))
        elif args.cmd == "report":
            code, msg = build_report(args.input, args.output)
        elif args.cmd == "finalize":
            code, msg = finalize(args.input)
    except OSError as exc:
        code, msg = 1, "filesystem error: %s" % exc
    except Exception as exc:  # 兜底：任何异常都以非 0 退出码返回，不静默
        code, msg = 1, "unexpected error: %r" % exc
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
