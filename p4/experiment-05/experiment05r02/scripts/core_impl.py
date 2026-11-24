#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — “实验结果解释与异常分析” Skill 的纯标准库实现。

职责：
  * 统一文本/JSON 读取与降级（UTF-8-sig -> UTF-8 -> latin-1）
  * 输入发现、case_id 解析、output_contract 回填
  * 阶段化时间/请求预算（900 秒 / 64 请求，前 3 分钟必落盘 partial 骨架）
  * 数据清点画像（CSV/TSV/JSON 表格 + 文本材料）
  * result.json schema 校验与 finalize（补 change_log / runtime / 渲染 report.md）

只依赖 Python 标准库。XLSX / MAT / PDF 不属于本脚本职责：由执行 Agent 先用
平台提供的 pandas / openpyxl / scipy / pdftotext 转成 CSV 或纯文本，再交给
`inspect` 与正文阅读。脚本自身在任何平台裸 Python 3 下均可运行。
"""

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# result.json 顶层契约：与 references/output_contract.md 保持一致。
TOP_KEYS = (
    "case_id", "status", "task_type", "summary",
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
EVIDENCE_KEYS = ("evidence", "supporting_evidence", "excluding_evidence")

# 阶段预算：与 SKILL.md 第 4 节保持同步。deadline_s 为累计秒。
PHASES = (
    {"id": "P0", "label": "骨架落盘", "budget_s": 180, "requests": 6, "deadline_s": 180},
    {"id": "P1", "label": "数据清点与画像", "budget_s": 300, "requests": 18, "deadline_s": 480},
    {"id": "P2", "label": "五段结论与证据映射", "budget_s": 300, "requests": 28, "deadline_s": 780},
    {"id": "P3", "label": "自检与定稿", "budget_s": 120, "requests": 8, "deadline_s": 900},
)
TOTAL_BUDGET_S = 900
TOTAL_REQUESTS = 64
RESERVE_REQUESTS = 4  # 64 - (6+18+28+8)


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """UTF-8（含 BOM）优先；坏编码降级 latin-1；超限/不可读返回 (None, reason)。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > max_bytes:
        return None, "file exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
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
    """返回按相对路径稳定排序的文件清单；用于 sources 与画像。"""
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
            out.append({
                "path": full, "rel": rel, "size_bytes": size,
                "ext": os.path.splitext(fn)[1].lower(),
                "sha256": sha256_head(full),
            })
    out.sort(key=lambda x: x["rel"])
    return out


def resolve_case_id(input_dir):
    """case.json 显式字段 -> content 文本 -> task.md -> 目录名兜底。"""
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


def _contract_names(case_obj):
    """从 case.json 的 output_contract 提取需要回填的字段名列表。"""
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
    return names


def merge_output_contract(result, case_obj):
    """回填 output_contract 全部键：缺值给 null/[]，不改名、不删未知键。"""
    merged = []
    if not isinstance(case_obj, dict):
        return merged
    contract = case_obj.get("output_contract")
    names = _contract_names(case_obj)
    for k in names:
        if k not in result:
            default = None
            if isinstance(contract, dict) and k in contract:
                default = _default_for(contract[k])
            result[k] = default
            merged.append(k)
    return merged


def empty_template(case_id):
    """带全部顶层键的空模板；未知值一律 null/[]，不臆造。"""
    return {
        "case_id": case_id,
        "status": "partial",
        "task_type": None,
        "summary": None,
        "interpretation": {k: [] for k in INTERPRETATION_KEYS},
        "experiment_plan": {k: None for k in PLAN_KEYS},
        "evidence_and_risk": {k: None for k in RISK_KEYS},
        "confirmation_items": {k: None for k in CONFIRM_KEYS},
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {
            "start_ts": now_ts(),
            "end_ts": None,
            "elapsed_s": None,
            "requests_used": None,
            "requests_total": TOTAL_REQUESTS,
            "seconds_budget": TOTAL_BUDGET_S,
        },
    }


def _run_state_path(work_dir):
    return os.path.join(work_dir, "run_state.json")


def _load_run_state(work_dir):
    p = _run_state_path(work_dir)
    return load_json_safe(p)


def _save_run_state(work_dir, state):
    os.makedirs(work_dir, exist_ok=True)
    write_json_atomic(_run_state_path(work_dir), state)


def init_run_state(work_dir):
    state = {
        "start_ts": now_ts(),
        "start_epoch": time.time(),
        "phases": PHASES,
        "current_phase": "P0",
        "requests_used": 0,
        "last_checkpoint": now_ts(),
        "elapsed_s": 0,
    }
    _save_run_state(work_dir, state)
    return state


def checkpoint(work_dir, phase=None, requests_used=None):
    """更新预算台账并打印剩余额度；不写 result.json，只做预算记账。"""
    state = _load_run_state(work_dir)
    if state is None:
        state = init_run_state(work_dir)
    state["elapsed_s"] = max(0.0, time.time() - float(state.get("start_epoch", time.time())))
    if phase:
        state["current_phase"] = phase
    if requests_used is not None:
        state["requests_used"] = int(requests_used)
    state["last_checkpoint"] = now_ts()
    _save_run_state(work_dir, state)

    elapsed = state["elapsed_s"]
    used = state["requests_used"]
    remaining_s = max(0, TOTAL_BUDGET_S - elapsed)
    lines = [
        "phase=%s" % state["current_phase"],
        "elapsed_s=%.1f remaining_s=%.1f" % (elapsed, remaining_s),
        "requests_used=%d remaining=%d (total=%d)" % (used, max(0, TOTAL_REQUESTS - used), TOTAL_REQUESTS),
    ]
    print("\n".join(lines))
    return state


def build_skeleton(input_dir, output_dir, work_dir):
    """P0：立即写出一版合法 partial result.json + report.md + run_state.json。"""
    os.makedirs(output_dir, exist_ok=True)
    init_run_state(work_dir)

    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    case_id = resolve_case_id(input_dir)
    files = discover_files(input_dir)
    has_input = bool(case_obj) or bool(files)

    result = empty_template(case_id)
    merge_output_contract(result, case_obj)

    if not has_input:
        result["status"] = "insufficient_evidence"
        result["summary"] = "输入目录无可解析的 case.json 或材料文件，无法产生基于证据的解释。"
        result["limitations"].append("no readable input under " + input_dir)
    else:
        result["status"] = "partial"
        result["summary"] = "骨架已落盘；结论待基于材料证据补充（本版为 early-partial，非最终结论）。"
        for f in files:
            result["sources"].append({
                "rel": f["rel"], "size_bytes": f["size_bytes"],
                "ext": f["ext"], "sha256": f["sha256"],
                "role": None, "read": False,
            })

    result["change_log"].append({
        "phase": "P0", "action": "skeleton", "ts": now_ts(),
        "note": "partial 骨架落盘；后续阶段增量改写，不改名不删键",
    })
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"), render_report(result))
    checkpoint(work_dir, "P0", 0)
    return os.path.join(output_dir, "result.json")


# ---------------------------------------------------------------------------
# 数据画像
# ---------------------------------------------------------------------------

def _to_float(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    idx = q * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo)


def column_stats(name, raw_values):
    """一列数值的描述统计 + 离群标记；全文本列返回 None。"""
    vals = [_to_float(v) for v in raw_values]
    numerics = [v for v in vals if v is not None]
    if not numerics:
        return None
    n = len(raw_values)
    missing = n - len(numerics)
    sorted_v = sorted(numerics)
    mean = statistics.fmean(numerics)
    median = statistics.median(numerics)
    try:
        stdev = statistics.stdev(numerics)
    except statistics.StatisticsError:
        stdev = None
    q1 = _quantile(sorted_v, 0.25)
    q3 = _quantile(sorted_v, 0.75)
    iqr = (q3 - q1) if (q1 is not None and q3 is not None) else None
    lo_iqr = (q1 - 1.5 * iqr) if iqr is not None else None
    hi_iqr = (q3 + 1.5 * iqr) if iqr is not None else None

    mad = None
    if median is not None:
        abs_dev = sorted([abs(v - median) for v in numerics])
        mad = statistics.median(abs_dev)
    lo_mad = (median - 3 * 1.4826 * mad) if (mad is not None and median is not None) else None
    hi_mad = (median + 3 * 1.4826 * mad) if (mad is not None and median is not None) else None

    z_out, iqr_out, mad_out = [], [], []
    for i, v in enumerate(vals):
        if v is None:
            continue
        if stdev and stdev > 0 and abs(v - mean) > 3 * stdev:
            z_out.append(i)
        if lo_iqr is not None and hi_iqr is not None and (v < lo_iqr or v > hi_iqr):
            iqr_out.append(i)
        if lo_mad is not None and hi_mad is not None and (v < lo_mad or v > hi_mad):
            mad_out.append(i)

    saturated = False
    unique = sorted(set(numerics))
    if len(unique) <= 2 and n > 2:
        saturated = True
    return {
        "column": name, "n": n, "n_numeric": len(numerics), "n_missing": missing,
        "mean": round(mean, 6), "median": round(median, 6),
        "stdev": round(stdev, 6) if stdev is not None else None,
        "min": round(min(numerics), 6), "max": round(max(numerics), 6),
        "q1": round(q1, 6) if q1 is not None else None,
        "q3": round(q3, 6) if q3 is not None else None,
        "iqr": round(iqr, 6) if iqr is not None else None,
        "mad": round(mad, 6) if mad is not None else None,
        "zscore_outlier_rows": z_out[:50],
        "iqr_outlier_rows": iqr_out[:50],
        "mad_outlier_rows": mad_out[:50],
        "n_zscore_outliers": len(z_out),
        "n_iqr_outliers": len(iqr_out),
        "n_mad_outliers": len(mad_out),
        "suspected_saturation_or_constant": saturated,
    }


def _sniff_delimiter(sample):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return None


def _profile_tabular(path):
    text, enc = read_text(path)
    if text is None:
        return {"path": path, "error": "unreadable", "reason": enc}
    delim = _sniff_delimiter(text[:8192])
    if delim is None:
        return {"path": path, "error": "delimiter_unknown"}
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return {"path": path, "error": "empty"}
    header = rows[0]
    body = rows[1:]
    columns = []
    for j, h in enumerate(header):
        col_vals = [r[j] for r in body if j < len(r)]
        st = column_stats(h, col_vals)
        if st is not None:
            columns.append(st)
    return {
        "path": path, "delimiter": repr(delim), "n_rows": len(body),
        "n_columns": len(header), "columns": columns,
        "encoding": enc,
    }


def _profile_json(path):
    obj = load_json_safe(path)
    if obj is None:
        return {"path": path, "error": "bad_json"}
    rows = None
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                rows = v
                break
    if not rows:
        return {"path": path, "error": "not_tabular"}
    # 只取前 2000 行做画像，防内存膨胀
    rows = rows[:2000]
    keys = []
    for r in rows:
        if isinstance(r, dict):
            for k in r:
                if k not in keys:
                    keys.append(k)
    columns = []
    if keys:
        for k in keys:
            st = column_stats(k, [r.get(k) for r in rows if isinstance(r, dict)])
            if st is not None:
                columns.append(st)
    return {"path": path, "n_rows": len(rows), "columns": columns}


def inspect_data(input_dir, output_dir, work_dir):
    """P1：扫描 /input，生成 data_profile.json（文件清单 + 数值画像 + 降级记录）。"""
    files = discover_files(input_dir)
    profile = {"input_dir": input_dir, "generated_ts": now_ts(),
               "files": [], "tabular": [], "textual": [], "unhandled": []}
    for f in files:
        ext = f["ext"]
        entry = {"rel": f["rel"], "size_bytes": f["size_bytes"],
                 "ext": ext, "sha256": f["sha256"]}
        if ext in (".csv", ".tsv", ".txt") and f["size_bytes"] <= MAX_FILE_BYTES:
            st = _profile_tabular(f["path"])
            st["rel"] = f["rel"]
            profile["tabular"].append(st)
            entry["kind"] = "tabular"
        elif ext == ".json" and f["size_bytes"] <= MAX_FILE_BYTES:
            st = _profile_json(f["path"])
            st["rel"] = f["rel"]
            profile["tabular"].append(st)
            entry["kind"] = "tabular"
        elif ext in (".md", ".txt") and f["size_bytes"] <= MAX_FILE_BYTES:
            text, enc = read_text(f["path"])
            entry["kind"] = "textual"
            entry["lines"] = text.count("\n") + 1 if text else 0
            entry["encoding"] = enc
            profile["textual"].append(entry)
        else:
            entry["kind"] = "unhandled"
            entry["note"] = "exceeds limit or unsupported ext; 由 Agent 用平台工具转 CSV/文本后重跑 inspect"
            profile["unhandled"].append(entry)
        profile["files"].append(entry)

    os.makedirs(output_dir, exist_ok=True)
    write_json_atomic(os.path.join(output_dir, "data_profile.json"), profile)
    checkpoint(work_dir, "P1", None)
    return os.path.join(output_dir, "data_profile.json")


# ---------------------------------------------------------------------------
# 校验与定稿
# ---------------------------------------------------------------------------

def _item_has_evidence(item):
    if not isinstance(item, dict):
        return False
    ev = item.get("evidence")
    if not isinstance(ev, list) or not ev:
        return False
    for e in ev:
        if isinstance(e, dict) and e.get("locator") and e.get("quote"):
            return True
    return False


def validate(result_path):
    """校验 schema 与证据完整性；返回 (ok, issues)。退出码 0=通过。"""
    issues = []
    obj = load_json_safe(result_path)
    if obj is None:
        return False, ["result.json 不可解析或缺失"]
    if not isinstance(obj, dict):
        return False, ["result.json 顶层不是对象"]

    if not (isinstance(obj.get("case_id"), str) and obj["case_id"].strip()):
        issues.append("case_id 为空")
    if obj.get("status") not in STATUSES:
        issues.append("status 非法：%r" % obj.get("status"))

    missing_top = [k for k in TOP_KEYS if k not in obj]
    if missing_top:
        issues.append("缺顶层键: " + ", ".join(missing_top))

    interp = obj.get("interpretation")
    if isinstance(interp, dict):
        for k in INTERPRETATION_KEYS:
            if k not in interp:
                issues.append("interpretation 缺键: " + k)
    else:
        issues.append("interpretation 不是对象")

    for sub, keys in (("experiment_plan", PLAN_KEYS),
                      ("evidence_and_risk", RISK_KEYS),
                      ("confirmation_items", CONFIRM_KEYS)):
        d = obj.get(sub)
        if isinstance(d, dict):
            for k in keys:
                if k not in d:
                    issues.append("%s 缺键: %s" % (sub, k))
        else:
            issues.append(sub + " 不是对象")

    # 证据完整性：status=ok 时，非空结论段必须逐条带 evidence（locator+quote）。
    if obj.get("status") == "ok" and isinstance(interp, dict):
        for k in INTERPRETATION_KEYS:
            items = interp.get(k)
            if isinstance(items, list):
                for i, item in enumerate(items):
                    if isinstance(item, dict) and not _item_has_evidence(item):
                        issues.append("interpretation.%s[%d] 缺 evidence(locator+quote)" % (k, i))

    ok = not issues
    if issues:
        for s in issues:
            print("ISSUE:", s, file=sys.stderr)
    else:
        print("ok: schema 与证据完整性通过")
    return ok, issues


def render_report(obj):
    """由 result.json 确定性渲染可编辑 report.md；不添加 result.json 之外的新结论。"""
    lines = ["# 实验结果解释与异常分析", ""]
    lines.append("- case_id: `%s`" % obj.get("case_id"))
    lines.append("- status: `%s`" % obj.get("status"))
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append(str(obj.get("summary") or "（未填写）"))
    lines.append("")
    interp = obj.get("interpretation") or {}
    section_titles = {
        "trends": "趋势", "anomalies": "异常点", "possible_causes": "可能原因",
        "verification_steps": "复核步骤", "followup_experiments": "后续实验建议",
    }
    for key, title in section_titles.items():
        lines.append("## " + title)
        lines.append("")
        items = interp.get(key) or []
        if not items:
            lines.append("（未填写）")
        else:
            for i, item in enumerate(items, 1):
                if isinstance(item, dict):
                    lines.append("### %s %d" % (title, i))
                    lines.append("")
                    for f in ("claim", "conclusion", "observation", "description"):
                        if item.get(f):
                            lines.append(str(item[f]))
                            break
                    else:
                        lines.append(json.dumps(item, ensure_ascii=False, indent=2))
                    lines.append("")
                    ev = item.get("evidence")
                    if isinstance(ev, list) and ev:
                        lines.append("证据：")
                        for e in ev:
                            if isinstance(e, dict) and e.get("locator"):
                                q = e.get("quote") or ""
                                lines.append("- `%s` %s" % (e["locator"], q[:120]))
                else:
                    lines.append("- " + str(item))
                lines.append("")
    lines.append("## 依据与风险")
    lines.append("")
    lines.append(json.dumps(obj.get("evidence_and_risk"), ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("## 确认项")
    lines.append("")
    lines.append(json.dumps(obj.get("confirmation_items"), ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("## 不确定性 / 限制 / 待人工确认")
    lines.append("")
    lines.append("- uncertainty: " + json.dumps(obj.get("uncertainty"), ensure_ascii=False))
    lines.append("- limitations: " + json.dumps(obj.get("limitations"), ensure_ascii=False))
    lines.append("- pending_human: " + json.dumps(obj.get("pending_human"), ensure_ascii=False))
    lines.append("")
    lines.append("## 变更记录")
    lines.append("")
    for c in obj.get("change_log") or []:
        if isinstance(c, dict):
            lines.append("- %s [%s] %s %s" % (
                c.get("ts", ""), c.get("phase", ""), c.get("action", ""), c.get("note", "")))
    lines.append("")
    return "\n".join(lines) + "\n"


def finalize(result_path, work_dir):
    """P3：补齐 runtime / change_log，渲染最终 report.md，并做一次校验。"""
    obj = load_json_safe(result_path)
    if obj is None:
        print("FATAL: result.json 不可解析，finalize 中止", file=sys.stderr)
        return False
    state = _load_run_state(work_dir)
    if state is not None:
        start_epoch = float(state.get("start_epoch", time.time()))
        elapsed = max(0.0, time.time() - start_epoch)
        obj.setdefault("runtime", {})
        obj["runtime"]["end_ts"] = now_ts()
        obj["runtime"]["elapsed_s"] = round(elapsed, 2)
        obj["runtime"]["requests_used"] = state.get("requests_used")
        obj["runtime"]["requests_total"] = TOTAL_REQUESTS
        obj["runtime"]["seconds_budget"] = TOTAL_BUDGET_S
    for k in TOP_KEYS:
        obj.setdefault(k, None if k not in ("sources", "uncertainty", "change_log",
                                            "errors", "degradations", "limitations",
                                            "pending_human") else [])
    obj["change_log"] = obj.get("change_log") or []
    obj["change_log"].append({"phase": "P3", "action": "finalize", "ts": now_ts(),
                              "note": "定稿：runtime 与变更记录已闭合"})
    write_json_atomic(result_path, obj)
    report_path = os.path.join(os.path.dirname(result_path), "report.md")
    write_text_atomic(report_path, render_report(obj))
    ok, issues = validate(result_path)
    print("finalize 完成；validate=%s" % ("PASS" if ok else "FAIL"))
    return ok
