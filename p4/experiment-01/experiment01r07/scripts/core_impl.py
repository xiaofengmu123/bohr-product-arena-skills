#!/usr/bin/env python3
"""core_impl.py — 实验方案 Skill 的实现：输入发现 / 骨架落盘 / 算术与证据自检。

仅依赖 Python 标准库。作为 main.py 薄入口之后的具体实现，也可直接运行。
退出码约定：0 = 成功或可用的部分结果；1 = 必须修复（结构 / 契约 / 类型 / 算术硬错误）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 单文本文件上限 2MB，超限跳过并记录 degradations
PDF_TOOL = "pdftotext"

# 可注入六要素骨架的 plan 类键（仅当契约把该键留空为 {} 时才注入）
PLAN_KEYS = {"plan", "experiment_plan", "experimental_plan", "output", "result",
             "方案", "实验方案"}
EVIDENCE_KEYS = {"evidence_and_risks", "evidence", "risks", "evidence_risks",
                 "依据与风险", "依据", "风险"}
CONFIRM_KEYS = {"confirmations", "confirmation", "confirm", "确认项"}
SIX_ORDER = ("hypothesis_and_variables", "controls", "equipment_materials",
             "time_cost", "parameter_ranges", "risks_and_exceptions")

BUDGET_KEYS = ("budget", "budget_ceiling", "ceiling", "total_budget", "预算", "预算上限")
COST_KEYS = ("cost", "price", "amount", "unit_cost", "total_cost", "费用", "价格", "成本")
VALUE_KEYS = ("value", "chosen_value", "selected", "chosen", "actual")
RANGE_KEYS = ("allowed_range", "range", "domain")
DURATION_KEYS = ("duration", "hours", "time", "时长")


# --------------------------------------------------------------------------- #
# 时间与原子写盘
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 容错读取（utf-8-sig / utf-8 / utf-16 / 2MB 上限 / 坏编码降级）
# --------------------------------------------------------------------------- #
def read_text(path, max_bytes=MAX_TEXT_BYTES):
    p = os.path.abspath(path)
    try:
        size = os.path.getsize(p)
    except OSError as e:
        return None, "unreadable", str(e)
    if size > max_bytes:
        return None, "skipped", "size %d bytes > limit %d bytes" % (size, max_bytes)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(p, "r", encoding=enc) as f:
                return f.read(), enc, None
        except (UnicodeDecodeError, OSError):
            continue
    try:
        with open(p, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8", errors="replace"), "utf-8-replace", None
    except OSError as e:
        return None, "unreadable", str(e)


def read_json(path, max_bytes=MAX_TEXT_BYTES):
    """二进制读取 + 去 BOM + utf-8/utf-16 尝试；失败返回 invalid_json 错误信息。"""
    p = os.path.abspath(path)
    try:
        size = os.path.getsize(p)
    except OSError as e:
        return None, "unreadable", str(e)
    if size > max_bytes:
        return None, "skipped", "size %d bytes > limit %d bytes" % (size, max_bytes)
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "unreadable", str(e)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "utf-16"):
        try:
            return json.loads(raw.decode(enc)), None, enc
        except (UnicodeDecodeError, ValueError):
            continue
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), None, "utf-8-replace"
    except Exception as e:  # noqa: BLE001
        return None, "invalid_json: %s" % e, "utf-8-replace"


# --------------------------------------------------------------------------- #
# 递归遍历
# --------------------------------------------------------------------------- #
def iter_dicts(obj, path="$"):
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from iter_dicts(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_dicts(v, "%s[%d]" % (path, i))


def _extract_balanced(text, start_idx):
    """从 start_idx 指向的 '{' 开始，提取配平的 JSON 对象字符串。"""
    depth = 0
    i = start_idx
    in_str = False
    esc = False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx:i + 1]
        i += 1
    return None


def find_output_contract(case_json, task_text):
    """在 case.json（顶层或嵌套）与 task.md 内寻找 output_contract。"""
    if isinstance(case_json, dict):
        if isinstance(case_json.get("output_contract"), dict):
            return case_json["output_contract"]
        for _p, node in iter_dicts(case_json):
            if isinstance(node.get("output_contract"), dict):
                return node["output_contract"]
    if isinstance(task_text, str):
        for m in re.finditer(r'"output_contract"\s*:', task_text):
            j = m.end()
            while j < len(task_text) and task_text[j] in " \t\r\n":
                j += 1
            if j < len(task_text) and task_text[j] == "{":
                seg = _extract_balanced(task_text, j)
                if seg:
                    try:
                        return json.loads(seg)
                    except Exception:
                        continue
    return None


def extract_case_id(case_json, task_text):
    if isinstance(case_json, dict):
        for key in ("case_id", "id", "challenge_id"):
            v = case_json.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(task_text, str):
        for pat in (r'case_id["\']?\s*[:：]\s*["\']?([A-Za-z0-9_.\-]+)',
                    r'challenge_id["\']?\s*[:：]\s*["\']?([A-Za-z0-9_.\-]+)'):
            m = re.search(pat, task_text)
            if m:
                return m.group(1)
    return "unknown"


# --------------------------------------------------------------------------- #
# PDF 提取
# --------------------------------------------------------------------------- #
def extract_pdf(pdf_abs, out_dir):
    base = os.path.splitext(os.path.basename(pdf_abs))[0]
    safe = re.sub(r"[^A-Za-z0-9_.\-]+", "_", base)
    out_txt = os.path.join(out_dir, safe + ".txt")
    if os.path.exists(out_txt) and os.path.getsize(out_txt) > 0:
        with open(out_txt, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None, out_txt
    if shutil.which(PDF_TOOL) is None:
        return None, "%s_unavailable" % PDF_TOOL, None
    try:
        r = subprocess.run([PDF_TOOL, "-layout", pdf_abs, out_txt],
                           capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        return None, "%s_unavailable" % PDF_TOOL, None
    except Exception as e:  # noqa: BLE001
        return None, "%s_error: %s" % (PDF_TOOL, e), None
    if r.returncode != 0 or not os.path.exists(out_txt) or os.path.getsize(out_txt) == 0:
        return None, "%s_failed_rc=%s" % (PDF_TOOL, r.returncode), None
    with open(out_txt, "r", encoding="utf-8", errors="replace") as f:
        return f.read(), None, out_txt


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def _classify(rel):
    base = os.path.basename(rel.lower())
    if ("candidate" in base or "候选" in base or "方案" in base or "plan" in base
            or re.search(r"v[123]\b", base)):
        return "candidate"
    if any(k in base for k in ("contract", "resource", "price", "booking", "availability",
                               "budget", "合同", "价格", "报价", "排期", "资源", "预算")):
        return "contract"
    if any(k in base for k in ("material", "paper", "supplement", "材料", "论文", "补充")):
        return "material"
    return "text"


def scan_input(input_dir, work_dir):
    report = {
        "input_dir": os.path.abspath(input_dir),
        "files": [],
        "case_json": None,
        "task_md": None,
        "output_contract": None,
        "case_id_source": None,
        "pdfs": [],
        "candidate_plans": [],
        "contracts": [],
        "materials": [],
        "other_text": [],
        "degradations": [],
        "errors": [],
    }
    if not os.path.isdir(input_dir):
        report["errors"].append("input dir missing: %s" % input_dir)
        report["case_id_source"] = "unknown"
        return report
    extracted_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            lower = name.lower()
            entry = {"rel": rel, "abs": full}
            report["files"].append(rel)
            if lower == "case.json":
                obj, err, enc = read_json(full)
                if obj is not None:
                    report["case_json"] = obj
                else:
                    report["errors"].append("%s: %s" % (rel, err))
                entry["enc"] = enc
            elif lower == "task.md":
                text, enc, err = read_text(full)
                if text is not None:
                    report["task_md"] = text
                else:
                    report["degradations"].append("%s: %s" % (rel, err))
            elif lower.endswith(".pdf"):
                txt, perr, out_txt = extract_pdf(full, extracted_dir)
                entry["extracted"] = out_txt
                if txt is None:
                    report["degradations"].append("%s: %s" % (rel, perr))
                report["pdfs"].append(entry)
            elif lower.endswith((".json", ".yaml", ".yml", ".csv")):
                kind = _classify(rel)
                entry["kind"] = kind
                if lower.endswith(".json"):
                    obj, err, _ = read_json(full)
                    entry["parsed"] = obj
                    if obj is None:
                        report["degradations"].append("%s: %s" % (rel, err))
                else:
                    text, _, err = read_text(full)
                    entry["text"] = text
                    if text is None:
                        report["degradations"].append("%s: %s" % (rel, err))
                _route_contract_entry(report, entry, kind)
            elif lower.endswith((".md", ".txt")):
                kind = _classify(rel)
                text, _, err = read_text(full)
                entry["kind"] = kind
                entry["text"] = text
                if text is None:
                    report["degradations"].append("%s: %s" % (rel, err))
                _route_contract_entry(report, entry, kind)
    report["output_contract"] = find_output_contract(report["case_json"], report["task_md"])
    report["case_id_source"] = extract_case_id(report["case_json"], report["task_md"])
    return report


def _route_contract_entry(report, entry, kind):
    if kind == "candidate":
        report["candidate_plans"].append(entry)
    elif kind == "contract":
        report["contracts"].append(entry)
    elif kind == "material":
        report["materials"].append(entry)
    else:
        report["other_text"].append(entry)


def print_probe_summary(report):
    print("input_dir: %s" % report["input_dir"])
    print("files: %d" % len(report["files"]))
    print("case_json: %s" % ("yes" if report["case_json"] is not None else "no"))
    print("task_md: %s" % ("yes" if report["task_md"] else "no"))
    print("output_contract: %s" % ("yes" if report["output_contract"] else "no"))
    print("pdfs=%d contracts=%d candidates=%d materials=%d" % (
        len(report["pdfs"]), len(report["contracts"]),
        len(report["candidate_plans"]), len(report["materials"])))
    print("errors=%d degradations=%d" % (len(report["errors"]), len(report["degradations"])))


# --------------------------------------------------------------------------- #
# 契约回填与六要素骨架
# --------------------------------------------------------------------------- #
def placeholder(value):
    """把契约值降级为占位：dict 递归保键、list -> []、类型提示串映射为空结构、叶子 -> null。"""
    if isinstance(value, dict):
        return {k: placeholder(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        t = value.strip().lower()
        if t in ("string", "str", "text", "varchar"):
            return ""
        if t in ("object", "dict", "map", "json"):
            return {}
        if t in ("array", "list"):
            return []
        return None
    return None


def six_skeleton():
    return {
        "hypothesis_and_variables": {
            "research_question": None,
            "hypotheses": [],
            "independent_variables": [],
            "dependent_variables": [],
            "confounders": [],
        },
        "controls": [],
        "equipment_materials": [],
        "time_cost": {"steps": [], "total_time": None, "total_cost": None},
        "parameter_ranges": [],
        "risks_and_exceptions": [],
    }


def _plan_md(case_id, contract):
    ck = (", ".join(sorted(contract.keys()))
          if isinstance(contract, dict) and contract else "（无，使用 fallback 结构）")
    return "\n".join([
        "# 实验方案（骨架 / 待完善）",
        "",
        "- case_id: `%s`" % case_id,
        "- 本文件为骨架草稿，各节按证据增量填写；缺失项一律写 `not_reported`，不得编造。",
        "- output_contract 字段：%s" % ck,
        "",
        "## 1. 假设与变量",
        "- 研究问题（可检验）：【待填 / not_reported】",
        "- 假设 H：",
        "- 自变量（操作定义/单位/量程）：",
        "- 因变量（测量方式/单位/量程）：",
        "- 混杂因素：",
        "",
        "## 2. 对照组",
        "- 空白 / 阴性 / 阳性 / 剂量梯度，逐条写明排除哪类混杂：",
        "",
        "## 3. 设备材料",
        "- 设备（型号/量程）、耗材（规格）、试剂（批号/浓度）；标可替代 vs 不可替代：",
        "",
        "## 4. 时间成本",
        "- 步骤时长、并行关系、样本量、预算估算（附算术）：",
        "",
        "## 5. 参数范围",
        "- 关键参数/单位/允许区间/超出区间处置/依据（手册/预实验/文献）：",
        "",
        "## 6. 风险与异常",
        "- 失败模式 / 判据 / 止损点 / 替代路径：",
        "",
        "## 依据与风险（证据）",
        "- 每条依据带 locator + 逐字 quote：",
        "",
        "## 确认项",
        "- 安全 / 伦理 / 设备可用性 / 专家确认（未确认不写为事实）：",
        "",
    ])


def scaffold(input_dir, output_dir, work_dir):
    """先落盘一版合法 partial 骨架，再做后续分析；异常也不留下缺失产物。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    report = scan_input(input_dir, work_dir)
    ts = utc_now()
    case_id = report.get("case_id_source") or "unknown"
    contract = report.get("output_contract")

    result = {}
    result["case_id"] = case_id
    if isinstance(contract, dict) and contract:
        for k, v in placeholder(contract).items():
            result.setdefault(k, v)
    else:
        result["plan"] = six_skeleton()
        result["evidence_and_risks"] = {"evidence": [], "risks": [], "source_conflicts": []}
        result["confirmations"] = []

    injected = []
    for key, val in list(result.items()):
        if isinstance(val, dict) and not val and key.lower() in PLAN_KEYS:
            result[key] = six_skeleton()
            injected.append(key)
        elif isinstance(val, dict) and not val and key.lower() in EVIDENCE_KEYS:
            result[key] = {"evidence": [], "risks": [], "source_conflicts": []}
            injected.append(key + ":evidence")
        elif isinstance(val, dict) and not val and key.lower() in CONFIRM_KEYS:
            result[key] = []
            injected.append(key + ":confirmations")

    if not os.path.isdir(input_dir):
        status = "insufficient_evidence"
    elif report["case_json"] is None and not report["task_md"]:
        status = "insufficient_evidence"
    elif report["errors"]:
        status = "input_error"
    else:
        status = "partial"

    result.setdefault("status", status)
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    log = result.setdefault("change_log", [])
    if isinstance(log, list):
        log.append({"ts": ts, "event": "skeleton_written",
                    "detail": "partial skeleton committed before analysis"})
        if injected:
            log.append({"ts": ts, "event": "six_element_skeleton_injected",
                        "detail": ",".join(injected)})
    result.setdefault("errors", report["errors"])
    result.setdefault("degradations", report["degradations"])
    result.setdefault("runtime", {"started_at": ts})

    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    atomic_write_text(os.path.join(output_dir, "plan.md"), _plan_md(case_id, contract))
    atomic_write_json(os.path.join(work_dir, "input_probe.json"), report)
    return result, report


def _minimal_fallback(output_dir, exc):
    """写盘/执行崩溃时的最小合法 result.json，不抛异常。"""
    os.makedirs(output_dir, exist_ok=True)
    ts = utc_now()
    obj = {
        "case_id": "unknown",
        "status": "input_error",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"ts": ts, "event": "minimal_fallback",
                        "detail": "scaffold crashed; minimal legal result written"}],
        "errors": ["scaffold_failed: %s" % exc],
        "degradations": ["scaffold exception"],
        "runtime": {"started_at": ts},
    }
    try:
        atomic_write_json(os.path.join(output_dir, "result.json"), obj)
    except Exception:
        pass
    print("scaffold failed; minimal fallback written: %s" % exc, file=sys.stderr)


# --------------------------------------------------------------------------- #
# 数值工具与算术校验
# --------------------------------------------------------------------------- #
def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        if m:
            return float(m.group(0))
    return None


def _node_range(node):
    lo = to_num(node.get("min"))
    hi = to_num(node.get("max"))
    rng = None
    for k in RANGE_KEYS:
        if k in node:
            rng = node[k]
            break
    if isinstance(rng, dict):
        if to_num(rng.get("min")) is not None:
            lo = to_num(rng.get("min"))
        if to_num(rng.get("max")) is not None:
            hi = to_num(rng.get("max"))
    elif isinstance(rng, (list, tuple)) and len(rng) == 2:
        if to_num(rng[0]) is not None:
            lo = to_num(rng[0])
        if to_num(rng[1]) is not None:
            hi = to_num(rng[1])
    return lo, hi


def _node_value(node):
    for k in VALUE_KEYS:
        if k in node and to_num(node[k]) is not None:
            return to_num(node[k])
    return None


def check_arithmetic(obj, report):
    for path, node in iter_dicts(obj):
        lo, hi = _node_range(node)
        val = _node_value(node)
        if lo is not None and hi is not None and val is not None:
            name = node.get("name") or node.get("parameter") or path
            if val < lo or val > hi:
                report["errors"].append(
                    "%s: parameter '%s' value %s outside allowed range [%s,%s]"
                    % (path, name, val, lo, hi))
    check_budget(obj, report)
    check_schedule(obj, report)


def check_budget(obj, report):
    for path, node in iter_dicts(obj):
        budget = None
        for k in BUDGET_KEYS:
            if k in node and to_num(node[k]) is not None:
                budget = to_num(node[k])
                break
        if budget is None:
            continue
        items = None
        for key in ("items", "line_items", "steps", "equipment_materials", "materials"):
            v = node.get(key)
            if isinstance(v, list):
                items = v
                break
        if not items:
            continue
        total = 0.0
        counted = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            c = None
            for k in COST_KEYS:
                if k in it and to_num(it[k]) is not None:
                    c = to_num(it[k])
                    break
            if c is None:
                uc = to_num(it.get("unit_cost"))
                q = to_num(it.get("quantity")) or to_num(it.get("qty")) or 1
                if uc is not None:
                    c = uc * q
            if c is not None:
                total += c
                counted += 1
        if counted and total > budget:
            report["errors"].append(
                "%s: line-item total %s exceeds budget %s" % (path, total, budget))
        elif counted:
            report["checks"].append(
                "%s: budget check ok (total %s <= %s)" % (path, total, budget))


def check_schedule(obj, report):
    for path, node in iter_dicts(obj):
        total = to_num(node.get("total_time")) or to_num(node.get("total_duration"))
        steps = node.get("steps")
        if not isinstance(steps, list) or total is None:
            continue
        s = 0.0
        n = 0
        for it in steps:
            if isinstance(it, dict):
                d = None
                for k in DURATION_KEYS:
                    if k in it and to_num(it[k]) is not None:
                        d = to_num(it[k])
                        break
                if d is not None:
                    s += d
                    n += 1
        if n and abs(s - total) > 1e-6:
            report["warnings"].append(
                "%s: sum of step durations %s != total_time %s" % (path, s, total))


# --------------------------------------------------------------------------- #
# 证据与六要素检查
# --------------------------------------------------------------------------- #
def _looks_like_evidence(d):
    if "quote" in d:
        return True
    return (("locator" in d or "source" in d)
            and ("claim" in d or "value" in d or "statement" in d))


def find_plan(obj):
    for _p, node in iter_dicts(obj):
        if isinstance(node, dict) and all(k in node for k in SIX_ORDER):
            return node
    return None


def plan_nonempty(plan):
    if not isinstance(plan, dict):
        return False
    hv = plan.get("hypothesis_and_variables")
    if isinstance(hv, dict):
        if (hv.get("hypotheses") or hv.get("research_question")
                or hv.get("independent_variables") or hv.get("dependent_variables")):
            return True
    return bool(plan.get("controls") or plan.get("equipment_materials")
                or plan.get("parameter_ranges") or plan.get("risks_and_exceptions"))


def check_evidence(obj, report):
    plan = find_plan(obj)
    found = 0
    incomplete = 0
    for path, node in iter_dicts(obj):
        if isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, dict) and _looks_like_evidence(item):
                    found += 1
                    has_loc = bool(item.get("locator") or item.get("source")
                                   or item.get("page") or item.get("section"))
                    has_quote = bool(item.get("quote"))
                    if not (has_loc and has_quote):
                        incomplete += 1
    if found == 0:
        if plan is not None and plan_nonempty(plan):
            report["warnings"].append(
                "plan has claims but no evidence entries with locator+quote")
        return
    if incomplete:
        report["warnings"].append(
            "%d/%d evidence entries missing locator or quote" % (incomplete, found))


# --------------------------------------------------------------------------- #
# 校验主入口
# --------------------------------------------------------------------------- #
def print_report(report):
    print("validate status: %s" % report["status"])
    for e in report.get("errors", []):
        print("  [ERROR] %s" % e)
    for w in report.get("warnings", []):
        print("  [WARN]  %s" % w)
    for c in report.get("checks", []):
        print("  [OK]    %s" % c)
    print("errors=%d warnings=%d" % (len(report.get("errors", [])),
                                     len(report.get("warnings", []))))


def validate(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    report = {"status": "ok", "errors": [], "warnings": [], "checks": [],
              "checked_at": utc_now()}

    def _finish():
        atomic_write_json(os.path.join(work_dir, "validate_report.json"), report)
        print_report(report)
        return 1 if report["errors"] else 0

    if not os.path.exists(result_path):
        report["status"] = "fail"
        report["errors"].append("result.json missing at %s" % result_path)
        return _finish()
    obj, err, _ = read_json(result_path)
    if obj is None:
        report["status"] = "fail"
        report["errors"].append("result.json invalid: %s" % err)
        return _finish()
    if not isinstance(obj, dict):
        report["status"] = "fail"
        report["errors"].append("result.json root must be an object")
        return _finish()

    # 契约回填检查
    case_json = None
    task_text = None
    cpath = os.path.join(input_dir, "case.json")
    tpath = os.path.join(input_dir, "task.md")
    if os.path.exists(cpath):
        case_json, _e, _ = read_json(cpath)
    if os.path.exists(tpath):
        task_text, _, _ = read_text(tpath)
    contract = find_output_contract(case_json, task_text)
    if isinstance(contract, dict):
        missing = [k for k in contract if k not in obj]
        if missing:
            report["errors"].append("missing contract keys: %s" % missing)

    # 必要信封字段
    for k in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if k not in obj:
            report["errors"].append("missing required field: %s" % k)
    if not obj.get("case_id"):
        report["errors"].append("case_id is empty")

    # 六要素完整性与 status 诚实性
    plan = find_plan(obj)
    if plan is None:
        report["warnings"].append(
            "no six-element plan object found (allowed for insufficient_evidence skeleton)")
    else:
        for el in SIX_ORDER:
            if el not in plan:
                report["warnings"].append("missing six-element key: %s" % el)
        if not plan_nonempty(plan):
            if obj.get("status") == "ok":
                report["errors"].append(
                    "status is 'ok' but six-element plan is empty; use partial/insufficient_evidence")
            else:
                report["warnings"].append("six-element plan is empty (honest partial)")

    check_arithmetic(obj, report)
    check_evidence(obj, report)

    report["status"] = "ok" if not report["errors"] else "fail"
    return _finish()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)
    p = argparse.ArgumentParser(
        prog="main.py", parents=[common],
        description="实验方案 Skill：输入发现 / 骨架落盘 / 算术与证据自检")
    sub = p.add_subparsers(dest="cmd")
    for name in ("probe", "scaffold", "validate"):
        sub.add_parser(name, parents=[common])
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "probe":
        os.makedirs(args.work, exist_ok=True)
        report = scan_input(args.input, args.work)
        atomic_write_json(os.path.join(args.work, "input_probe.json"), report)
        print_probe_summary(report)
        return 0
    if args.cmd == "scaffold":
        try:
            scaffold(args.input, args.output, args.work)
            return 0
        except Exception as e:  # noqa: BLE001
            _minimal_fallback(args.output, e)
            return 0
    if args.cmd == "validate":
        return validate(args.input, args.output, args.work)
    # 默认：probe -> scaffold
    try:
        os.makedirs(args.work, exist_ok=True)
        report = scan_input(args.input, args.work)
        atomic_write_json(os.path.join(args.work, "input_probe.json"), report)
        scaffold(args.input, args.output, args.work)
        return 0
    except Exception as e:  # noqa: BLE001
        _minimal_fallback(args.output, e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
