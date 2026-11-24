#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 实验方案生成 Skill 的确定性实现（仅标准库）。

子命令：probe / scaffold / render / audit / check / all
路径由 --input / --output / --work 给出，缺省为 /input、/app/output、/app/work。

设计约定
  1. 任何子命令都不抛异常给调用方：失败写 errors / degradations 并给退出码。
  2. 写盘一律原子（先写 .tmp 再 os.replace），中途中断不会留下半截 result.json。
  3. 同一输入重复运行，内容字段逐字节一致；仅 change_log 的 ts 与 runtime.generated_at 取当前时间。
  4. 不产出任何案例答案：scaffold 只按契约塑形，内容一律留空（null / []）。
"""

import copy
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

SKILL_VERSION = "1.1.0"
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_BYTES = 2 * 1024 * 1024
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
BASIS_VALUES = (
    "instrument_manual",
    "reagent_label",
    "organizer_contract",
    "literature",
    "pilot_data",
    "kit_insert",
    "assumption",
)

USAGE = """usage: main.py <command> [--input DIR] [--output DIR] [--work DIR] [--strict] [--force]

commands:
  probe     扫描 /input，解析 case.json / task.md / PDF，写 <work>/input_probe.json
  scaffold  按 output_contract 逐键塑形，写合法骨架 result.json（已存在则拒绝覆盖，除非 --force）
  render    由 result.json 生成人类可读 plan.md（唯一渲染入口，保证两者一致）
  audit     计算预算 / 排期 / 样本量 / 量程 / 域约束的实际算术，--write 回填 feasibility_checks
  check     结构、契约镜像、六要素、证据逐字校验；退出码 0=可用（含警告），1=必须修复
  all       probe -> scaffold -> render -> audit --write -> check
"""

ENVELOPE = (
    "case_id",
    "status",
    "artifacts",
    "sources",
    "uncertainty",
    "pending_human",
    "limitations",
    "degradations",
    "errors",
    "change_log",
    "runtime",
)

# 六要素（含“假设与变量”拆成两键）到常见键名的别名表；用于跨契约的语义定位。
SIX = {
    "假设": ("hypothesis", "hypotheses", "h0", "hypothesis_statement"),
    "变量": ("variables", "variable", "variable_table"),
    "对照组": ("controls", "control_groups", "control_arms", "controls_and_groups"),
    "设备材料": ("resources", "equipment", "materials", "equipment_and_materials"),
    "参数范围": ("parameters", "params", "parameter_ranges", "parameter_table"),
    "时间成本": ("schedule", "timeline", "time_cost", "time_plan", "budget"),
    "风险异常": ("risks", "risk_register", "failure_modes", "risks_and_contingency"),
}

TITLES = {
    "plan": "实验方案",
    "objective": "研究目标",
    "hypothesis": "假设与判定规则",
    "variables": "变量（操作定义 / 单位 / 量程）",
    "controls": "对照组（各排除哪类混杂）",
    "design": "实验设计与样本量",
    "steps": "步骤（设备 / 试剂 / 参数 / 时长）",
    "parameters": "关键参数与允许区间",
    "resources": "设备、材料与预算",
    "equipment": "设备",
    "materials": "材料与试剂",
    "budget": "预算",
    "bookings": "机时与预约",
    "schedule": "时间成本与并行",
    "readout": "判读标准与金标准",
    "candidates": "候选方案比较",
    "selected_candidate": "选定方案",
    "revisions": "修订记录",
    "evidence": "依据（逐字定位）",
    "evidence_and_risks": "依据与风险",
    "source_conflicts": "来源冲突",
    "risks": "风险与异常处置",
    "assumptions": "假设前提",
    "feasibility_checks": "可行性算术校验",
    "confirmations": "确认项",
    "safety": "安全",
    "ethics": "伦理",
    "equipment_availability": "设备可用性",
    "expert_signoff": "专家确认",
    "open_questions": "待澄清问题",
    "case_id": "案例标识",
    "status": "状态",
    "artifacts": "产物清单",
    "sources": "来源",
    "uncertainty": "不确定性",
    "pending_human": "待人工确认",
    "limitations": "局限",
    "degradations": "降级记录",
    "errors": "错误",
    "change_log": "变更记录",
    "runtime": "运行信息",
}

# 默认（无 output_contract 时）的塑形种子：只给结构，不给内容。
SEED = {
    "plan": {
        "objective": {"restated": None, "decision_needed": None, "scope_boundary": None},
        "hypothesis": {
            "h0": None,
            "h1": None,
            "decision_rule": None,
            "alpha": None,
            "power": None,
            "expected_effect": None,
        },
        "variables": [],
        "controls": [],
        "design": {
            "n_per_group": None,
            "n_groups": None,
            "n_total": None,
            "randomization": None,
            "blinding": None,
            "replicates": None,
        },
        "steps": [],
        "parameters": [],
        "resources": {
            "equipment": [],
            "materials": [],
            "budget": {"currency": None, "cap": None, "lines": [], "total": None},
            "bookings": [],
        },
        "schedule": {
            "chain": [],
            "parallel_groups": [],
            "total_elapsed_min": None,
            "calendar_days": None,
        },
        "readout": {
            "primary_metric": None,
            "unit": None,
            "protocol": None,
            "gold_standard": None,
            "success_criterion": None,
            "failure_criterion": None,
        },
        "candidates": [],
        "selected_candidate": None,
        "revisions": [],
    },
    "evidence_and_risks": {
        "evidence": [],
        "source_conflicts": [],
        "risks": [],
        "assumptions": [],
        "feasibility_checks": [],
    },
    "confirmations": {
        "safety": [],
        "ethics": [],
        "equipment_availability": [],
        "expert_signoff": [],
        "open_questions": [],
    },
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg):
    sys.stderr.write(str(msg) + "\n")


def read_text_file(path, limit=MAX_TEXT_BYTES):
    """容错读取：utf-8-sig -> utf-8 -> gb18030 -> latin-1；超限截断并记录。"""
    notes = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, ["stat_failed:%s" % exc]
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError as exc:
        return None, ["read_failed:%s" % exc]
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
        notes.append("truncated_at_%d_bytes" % limit)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), notes
        except UnicodeDecodeError:
            continue
    notes.append("decode_replaced")
    return raw.decode("utf-8", "replace"), notes


def load_json_file(path):
    text, notes = read_text_file(path)
    if text is None:
        return None, "unreadable:%s" % ";".join(notes)
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "bad_json:%s" % exc


def atomic_write(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def sha_short(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def iter_walk(obj, path=()):
    """产出 (path_tuple, key, value)；只对 dict 的键产出，list 展开但不产出。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + (k,), k, v
            for item in iter_walk(v, path + (k,)):
                yield item
    elif isinstance(obj, list):
        for v in obj:
            for item in iter_walk(v, path):
                yield item


def find_first(obj, aliases):
    """按别名深度优先找第一个非空值，返回 (path_tuple, value)。"""
    keys = {a.lower() for a in aliases}
    for path, key, val in iter_walk(obj):
        if isinstance(key, str) and key.lower() in keys and not is_empty(val):
            return path, val
    return None, None


def find_all(obj, alias):
    keys = {alias.lower()}
    out = []
    for path, key, val in iter_walk(obj):
        if isinstance(key, str) and key.lower() in keys:
            out.append((path, val))
    return out


def is_empty(val):
    if val is None:
        return True
    if isinstance(val, str):
        return not val.strip()
    if isinstance(val, (list, tuple, dict)):
        return len(val) == 0
    return False


def as_num(val):
    """宽松取数：int/float 直接返回；数字字符串（含前后单位）取第一个数；其余 None。"""
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        m = re.search(r"[-+]?\d+(?:[.,]\d+)?", val.replace(",", "."))
        if m:
            try:
                return float(m.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def num_list(val):
    if not isinstance(val, (list, tuple)) or len(val) < 2:
        return None
    lo, hi = as_num(val[0]), as_num(val[1])
    if lo is None or hi is None:
        return None
    return (lo, hi) if lo <= hi else (hi, lo)


def get_path(obj, path):
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return False, None
        cur = cur[key]
    return True, cur


def contract_leaf_paths(node, prefix=()):
    if isinstance(node, dict):
        if not node:
            yield prefix
            return
        for k, v in node.items():
            for p in contract_leaf_paths(v, prefix + (k,)):
                yield p
        return
    yield prefix


def shape_from_contract(node):
    """把契约塑形成同名同层、内容为空的骨架。标量->None，数组->[]，对象递归。"""
    if isinstance(node, dict):
        return {k: shape_from_contract(v) for k, v in node.items()}
    if isinstance(node, list):
        return []
    return None


def merge_seed(shape, seed):
    """契约给的形状优先；契约未给形状（None）处采用种子结构补齐字段。"""
    if isinstance(shape, dict):
        out = {}
        for k, v in shape.items():
            out[k] = merge_seed(v, seed.get(k) if isinstance(seed, dict) else None)
        if isinstance(seed, dict):
            for k, sv in seed.items():
                if k not in out:
                    out[k] = copy.deepcopy(sv)
        return out
    if shape is None and isinstance(seed, (dict, list)):
        return copy.deepcopy(seed)
    return shape


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #
def try_pdftotext(pdf_path, dest_dir):
    exe = None
    for cand in ("pdftotext", "pdftotext.exe"):
        from shutil import which

        exe = which(cand)
        if exe:
            break
    if not exe:
        return None, "pdftotext_unavailable"
    os.makedirs(dest_dir, exist_ok=True)
    stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", os.path.splitext(os.path.basename(pdf_path))[0])
    out_path = os.path.join(dest_dir, stem + ".txt")
    try:
        subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", pdf_path, out_path],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "pdftotext_failed:%s" % exc
    if not os.path.isfile(out_path):
        return None, "pdftotext_no_output"
    text, _ = read_text_file(out_path)
    return text, None


def extract_case_id(case, task_text):
    for key in ("case_id", "id", "challenge_id", "caseId"):
        val = case.get(key) if isinstance(case, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip(), "case.json:%s" % key
    if task_text:
        m = re.search(r"challenge_id\s*[:：]\s*`?([A-Za-z0-9._-]+)`?", task_text)
        if m:
            return m.group(1), "task.md:challenge_id"
    return "unknown", "not_reported"


def extract_contract(case, task_text):
    """契约来源：case.json 顶层/嵌套 output_contract；task.md 内嵌 json 代码块。"""
    if isinstance(case, dict):
        if isinstance(case.get("output_contract"), dict):
            return case["output_contract"], "case.json:output_contract"
        for path, key, val in iter_walk(case):
            if ("contract" in str(key).lower() or "schema" in str(key).lower()) and isinstance(
                val, dict
            ):
                if any(isinstance(v, (dict, list)) for v in val.values()):
                    return val, "case.json:%s" % "/".join(path)
    if task_text:
        for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", task_text, re.S):
            try:
                obj = json.loads(block)
            except ValueError:
                continue
            if isinstance(obj.get("output_contract"), dict):
                return obj["output_contract"], "task.md:output_contract"
    return None, "not_reported"


def probe(input_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    pdf_dir = os.path.join(work_dir, "pdf_text")
    report = {
        "skill_version": SKILL_VERSION,
        "input_dir": os.path.abspath(input_dir),
        "files": [],
        "case_json": None,
        "case_json_path": None,
        "case_id": "unknown",
        "case_id_source": "not_reported",
        "output_contract": None,
        "contract_source": "not_reported",
        "contract_keys": [],
        "task_md_path": None,
        "task_md_chars": 0,
        "pdf_text": {},
        "notes": [],
        "errors": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        report["errors"].append({"code": "input_dir_missing", "detail": input_dir})
        atomic_write_json(os.path.join(work_dir, "input_probe.json"), report)
        return report

    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            entry = {"path": rel, "bytes": size, "kind": os.path.splitext(name)[1].lower()}
            report["files"].append(entry)
            ext = entry["kind"]
            if ext == ".pdf":
                text, err = try_pdftotext(full, pdf_dir)
                if text is None:
                    report["degradations"].append(
                        {"code": "pdf_text_unavailable", "detail": "%s (%s)" % (rel, err)}
                    )
                else:
                    entry["chars"] = len(text)
                    report["pdf_text"][rel] = len(text)
            elif size > MAX_TEXT_BYTES:
                report["degradations"].append(
                    {"code": "file_too_large", "detail": "%s %d bytes" % (rel, size)}
                )

    task_path = None
    for cand in ("task.md", "TASK.md", "README.md"):
        p = os.path.join(input_dir, cand)
        if os.path.isfile(p):
            task_path = p
            break
    task_text = None
    if task_path:
        task_text, _ = read_text_file(task_path)
        report["task_md_path"] = os.path.relpath(task_path, input_dir).replace("\\", "/")
        report["task_md_chars"] = len(task_text or "")

    case = None
    for cand in ("case.json", "case.JSON", "input.json"):
        p = os.path.join(input_dir, cand)
        if os.path.isfile(p):
            case, err = load_json_file(p)
            report["case_json_path"] = cand
            if err:
                report["errors"].append({"code": "case_json_unreadable", "detail": "%s %s" % (cand, err)})
            break
    if case is None:
        for entry in report["files"]:
            if entry["path"].lower().endswith(".json") and "case" in entry["path"].lower():
                p = os.path.join(input_dir, entry["path"])
                case, err = load_json_file(p)
                if case is not None:
                    report["case_json_path"] = entry["path"]
                    break
    report["case_json"] = case

    cid, cid_src = extract_case_id(case, task_text)
    report["case_id"], report["case_id_source"] = cid, cid_src
    contract, csrc = extract_contract(case, task_text)
    if contract:
        report["output_contract"] = contract
        report["contract_source"] = csrc
        report["contract_keys"] = sorted(contract.keys())
    else:
        report["degradations"].append(
            {"code": "output_contract_absent", "detail": "使用 references 中的 fallback 结构"}
        )

    atomic_write_json(os.path.join(work_dir, "input_probe.json"), report)
    return report


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #
def build_result(probe_report):
    contract = probe_report.get("output_contract")
    if isinstance(contract, dict) and contract:
        result = merge_seed(shape_from_contract(contract), SEED)
    else:
        result = copy.deepcopy(SEED)
    result["case_id"] = probe_report.get("case_id") or "unknown"
    result["status"] = "partial"
    result["artifacts"] = {"plan_md": "plan.md", "result_json": "result.json"}
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["limitations"] = []
    result["degradations"] = list(probe_report.get("degradations") or [])
    result["errors"] = list(probe_report.get("errors") or [])
    result["change_log"] = [
        {
            "ts": now_iso(),
            "event": "skeleton_written",
            "detail": "契约来源=%s；键=%s" % (
                probe_report.get("contract_source"),
                ",".join(sorted(contract.keys())) if isinstance(contract, dict) else "fallback",
            ),
        }
    ]
    result["runtime"] = {
        "skill_version": SKILL_VERSION,
        "generated_at": now_iso(),
        "commands": ["probe", "scaffold"],
    }
    return result


def cmd_scaffold(input_dir, output_dir, work_dir, force=False):
    probe_path = os.path.join(work_dir, "input_probe.json")
    report = None
    if os.path.isfile(probe_path):
        report, err = load_json_file(probe_path)
        if err:
            report = None
    if report is None:
        report = probe(input_dir, work_dir)

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(out_path) and not force:
        existing, err = load_json_file(out_path)
        if existing is not None and not err:
            log("result.json 已存在且可解析，跳过塑形（如需重置加 --force）")
            return 0
        log("result.json 不可解析，按骨架重写")
    result = build_result(report)
    atomic_write_json(out_path, result)
    plan_path = os.path.join(output_dir, "plan.md")
    if not os.path.isfile(plan_path) or force:
        atomic_write(plan_path, render_md(result))
    log("已写骨架: %s" % out_path)
    return 0


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def _title(key):
    return TITLES.get(key, str(key))


def _fmt_scalar(val):
    if val is None:
        return "not_reported"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        return ("%g" % val)
    if isinstance(val, str):
        return val.strip() or "not_reported"
    return str(val)


def _all_scalar(items, keys):
    for it in items:
        if not isinstance(it, dict):
            return False
        if any(k not in it for k in keys):
            return False
        for k in keys:
            v = it[k]
            if isinstance(v, (dict, list)):
                return False
    return True


def render_md(result):
    lines = []
    case_id = result.get("case_id") or "unknown"
    status = result.get("status") or "partial"
    lines.append("# 实验方案：%s" % case_id)
    lines.append("")
    lines.append("> 由 `scripts/main.py render` 从 `result.json` 生成；手工修改请改 result.json 后重渲染。")
    lines.append("> 状态：`%s`　Skill 版本：%s" % (status, SKILL_VERSION))
    lines.append("")

    plan = result.get("plan") if isinstance(result.get("plan"), dict) else None
    if plan:
        sel = plan.get("selected_candidate")
        design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
        sched = plan.get("schedule") if isinstance(plan.get("schedule"), dict) else {}
        budget = {}
        res = plan.get("resources") if isinstance(plan.get("resources"), dict) else {}
        if isinstance(res.get("budget"), dict):
            budget = res["budget"]
        hyp = plan.get("hypothesis") if isinstance(plan.get("hypothesis"), dict) else {}
        lines.append("## 一页摘要")
        lines.append("")
        lines.append("| 项 | 内容 |")
        lines.append("|---|---|")
        lines.append("| 选定方案 | %s |" % _fmt_scalar(sel))
        lines.append("| 样本量 n_total | %s |" % _fmt_scalar(design.get("n_total")))
        lines.append("| 关键参数组数 | %d |" % len(plan.get("parameters") or []))
        lines.append("| 总耗时（分钟） | %s |" % _fmt_scalar(sched.get("total_elapsed_min")))
        lines.append("| 预算合计 / 上限 | %s / %s |" % (_fmt_scalar(budget.get("total")), _fmt_scalar(budget.get("cap"))))
        lines.append("| 判定规则 | %s |" % _fmt_scalar(hyp.get("decision_rule")))
        lines.append("")

    skip = set(ENVELOPE)
    for key, val in result.items():
        if key in skip and key in ("case_id", "artifacts"):
            continue
        lines.extend(_render_node(key, val, 2))
    lines.append("")
    lines.append("## 运行状态与变更")
    lines.append("")
    lines.append("- status: `%s`" % status)
    for key in ("limitations", "degradations", "errors", "uncertainty", "pending_human", "sources", "change_log"):
        if key in result:
            lines.extend(_render_node(key, result[key], 3))
    lines.append("")
    return "\n".join(lines)


def _render_node(key, val, level):
    out = []
    head = "#" * min(level, 6)
    out.append("%s %s" % (head, _title(key)))
    out.append("")
    out.extend(_render_body(val, level))
    out.append("")
    return out


def _render_body(val, level):
    out = []
    if isinstance(val, dict):
        if not val:
            out.append("- not_reported")
            return out
        for k, v in val.items():
            if isinstance(v, (dict, list)):
                out.append("%s %s" % ("#" * min(level + 1, 6), _title(k)))
                out.append("")
                out.extend(_render_body(v, level + 1))
                out.append("")
            else:
                out.append("- **%s**: %s" % (_title(k), _fmt_scalar(v)))
        return out
    if isinstance(val, list):
        if not val:
            out.append("- not_reported")
            return out
        if all(isinstance(it, dict) for it in val):
            keys = []
            for it in val:
                for k in it.keys():
                    if k not in keys:
                        keys.append(k)
            if keys and len(keys) <= 7 and _all_scalar(val, keys):
                out.append("| " + " | ".join(_title(k) for k in keys) + " |")
                out.append("|" + "---|" * len(keys))
                for it in val:
                    out.append("| " + " | ".join(_fmt_scalar(it.get(k)) for k in keys) + " |")
                return out
            for idx, it in enumerate(val, 1):
                out.append("%s %d" % ("#" * min(level + 1, 6), idx))
                out.append("")
                out.extend(_render_body(it, level + 1))
                out.append("")
            return out
        for it in val:
            out.append("- %s" % _fmt_scalar(it))
        return out
    out.append("- %s" % _fmt_scalar(val))
    return out


def cmd_render(output_dir):
    path = os.path.join(output_dir, "result.json")
    result, err = load_json_file(path)
    if result is None:
        log("无法读取 result.json: %s" % err)
        return 1
    atomic_write(os.path.join(output_dir, "plan.md"), render_md(result))
    log("已渲染 plan.md")
    return 0


# --------------------------------------------------------------------------- #
# audit（实际算术）
# --------------------------------------------------------------------------- #
def _plan_ctx(result):
    _p, plan = find_first(result, ("plan", "experiment_plan"))
    return plan if isinstance(plan, dict) else result


def audit_checks(result):
    """计算可行性算术。每项：check / computed / declared / limit / pass / detail。"""
    checks = []
    plan = _plan_ctx(result)

    res = find_first(plan, ("resources", "equipment_and_materials"))[1]
    budget = res.get("budget") if isinstance(res, dict) and isinstance(res.get("budget"), dict) else {}
    lines = budget.get("lines") if isinstance(budget.get("lines"), list) else []
    if lines:
        total = 0.0
        complete = True
        for ln in lines:
            if not isinstance(ln, dict):
                complete = False
                continue
            q = as_num(ln.get("qty", ln.get("quantity", ln.get("units"))))
            p = as_num(ln.get("unit_price", ln.get("price")))
            if q is None or p is None:
                complete = False
                continue
            total += q * p
        declared = as_num(budget.get("total"))
        cap = as_num(budget.get("cap"))
        ok = declared is not None and abs(round(total, 2) - round(declared, 2)) <= 0.01
        checks.append(
            {
                "check": "budget_total_equals_sum_of_lines",
                "computed": round(total, 2),
                "declared": declared,
                "limit": cap,
                "pass": bool(ok and complete),
                "detail": "逐行 qty*unit_price 求和；%s" % ("行数据不完整" if not complete else "行数据完整"),
            }
        )
        if cap is not None and declared is not None:
            checks.append(
                {
                    "check": "budget_within_cap",
                    "computed": round(declared, 2),
                    "declared": cap,
                    "limit": cap,
                    "pass": declared <= cap + 1e-9,
                    "detail": "声明总额对预算上限",
                }
            )

    bookings = res.get("bookings") if isinstance(res, dict) else None
    if isinstance(bookings, list) and bookings:
        bcost = 0.0
        all_ok = True
        for bk in bookings:
            if not isinstance(bk, dict):
                continue
            sessions = as_num(bk.get("sessions", bk.get("n_sessions")))
            per = as_num(bk.get("session_min", bk.get("minutes_per_session")))
            window = as_num(bk.get("window_min", bk.get("available_min")))
            price = as_num(bk.get("unit_price", bk.get("price_per_session")))
            if sessions is not None and per is not None and window is not None:
                need = sessions * per
                ok = need <= window + 1e-9
                all_ok = all_ok and ok
                checks.append(
                    {
                        "check": "booking_capacity:%s" % bk.get("resource", "res"),
                        "computed": round(need, 2),
                        "declared": round(window, 2),
                        "limit": window,
                        "pass": ok,
                        "detail": "所需机时 = sessions * session_min，对可用窗口",
                    }
                )
            if sessions is not None and price is not None:
                bcost += sessions * price
        if bcost > 0:
            cap = as_num(budget.get("cap"))
            checks.append(
                {
                    "check": "booking_cost_sum",
                    "computed": round(bcost, 2),
                    "declared": None,
                    "limit": cap,
                    "pass": (cap is None or bcost <= cap + 1e-9) and all_ok,
                    "detail": "预约费用合计（含窗口容量判定）",
                }
            )

    design = find_first(plan, ("design", "design_and_sample_size"))[1]
    if isinstance(design, dict):
        per = as_num(design.get("n_per_group"))
        groups = as_num(design.get("n_groups"))
        total = as_num(design.get("n_total"))
        if per is not None and groups is not None and total is not None:
            checks.append(
                {
                    "check": "n_total_equals_per_group_times_groups",
                    "computed": per * groups,
                    "declared": total,
                    "limit": None,
                    "pass": abs(per * groups - total) <= 1e-9,
                    "detail": "样本量一致性",
                }
            )

    sched = find_first(plan, ("schedule", "timeline", "time_plan"))[1]
    if isinstance(sched, dict):
        total = as_num(sched.get("total_elapsed_min"))
        chain = sched.get("chain") if isinstance(sched.get("chain"), list) else []
        chain_sum = 0.0
        chain_ok = True
        for node in chain:
            d = as_num(node.get("duration_min")) if isinstance(node, dict) else as_num(node)
            if d is None:
                chain_ok = False
            else:
                chain_sum += d
        if total is not None and chain:
            checks.append(
                {
                    "check": "schedule_ge_critical_chain",
                    "computed": round(chain_sum, 2),
                    "declared": total,
                    "limit": total,
                    "pass": chain_ok and chain_sum <= total + 1e-9,
                    "detail": "关键链时长之和不得超过声明总耗时",
                }
            )
        groups = sched.get("parallel_groups") if isinstance(sched.get("parallel_groups"), list) else []
        if total is not None and groups:
            worst = 0.0
            ok = True
            for g in groups:
                nodes = g.get("steps") if isinstance(g, dict) else None
                if not isinstance(nodes, list):
                    ok = False
                    continue
                s = 0.0
                for node in nodes:
                    d = as_num(node.get("duration_min")) if isinstance(node, dict) else as_num(node)
                    if d is None:
                        ok = False
                    else:
                        s += d
                worst = max(worst, s)
            checks.append(
                {
                    "check": "schedule_ge_longest_parallel_group",
                    "computed": round(worst, 2),
                    "declared": total,
                    "limit": total,
                    "pass": ok and worst <= total + 1e-9,
                    "detail": "最长并行分组时长不得超过总耗时",
                }
            )

    params = find_first(plan, ("parameters", "params", "parameter_ranges"))[1]
    equip = res.get("equipment") if isinstance(res, dict) else None
    emap = {}
    if isinstance(equip, list):
        for e in equip:
            if isinstance(e, dict) and e.get("id"):
                emap[str(e["id"])] = e
    if isinstance(params, list):
        for prm in params:
            if not isinstance(prm, dict):
                continue
            name = prm.get("name", "param")
            dom = prm.get("domain")
            val = prm.get("value", prm.get("value_or_range"))
            if isinstance(dom, list) and dom and val is not None:
                norm = {str(d).strip().lower() for d in dom}
                hit = str(val).strip().lower() in norm
                if not hit:
                    vnum = as_num(val)
                    if vnum is not None:
                        hit = any(as_num(d) is not None and abs(as_num(d) - vnum) < 1e-9 for d in dom)
                checks.append(
                    {
                        "check": "parameter_in_domain:%s" % name,
                        "computed": val,
                        "declared": dom,
                        "limit": dom,
                        "pass": bool(hit),
                        "detail": "取值必须落在案例给定的有限参数域内",
                    }
                )
            eid = prm.get("equipment") or prm.get("equipment_id")
            if eid and str(eid) in emap:
                rng = num_list(emap[str(eid)].get("range", emap[str(eid)].get("range_min_max")))
                vnum = as_num(val)
                if rng and vnum is not None:
                    lo, hi = rng
                    checks.append(
                        {
                            "check": "parameter_within_equipment_range:%s" % name,
                            "computed": vnum,
                            "declared": [lo, hi],
                            "limit": [lo, hi],
                            "pass": lo - 1e-9 <= vnum <= hi + 1e-9,
                            "detail": "参数须在设备量程 %s 内" % eid,
                        }
                    )

    cands = find_first(plan, ("candidates", "candidate_plans", "options"))[1]
    selected = plan.get("selected_candidate") if isinstance(plan, dict) else None
    if isinstance(cands, list) and cands:
        sel_key = str(selected).strip().lower() if selected is not None else None
        found = None
        for c in cands:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id", c.get("name", ""))).strip().lower()
            if sel_key and cid == sel_key:
                found = c
        if found is None:
            checks.append(
                {
                    "check": "selected_candidate_exists",
                    "computed": selected,
                    "declared": [c.get("id") for c in cands if isinstance(c, dict)],
                    "limit": None,
                    "pass": False,
                    "detail": "selected_candidate 必须指向候选清单中的一项",
                }
            )
        else:
            hc = found.get("hard_constraint_check")
            failed = []
            if isinstance(hc, list):
                for item in hc:
                    if isinstance(item, dict) and item.get("pass") is False:
                        failed.append(item.get("constraint"))
            checks.append(
                {
                    "check": "selected_candidate_passes_hard_constraints",
                    "computed": failed,
                    "declared": None,
                    "limit": None,
                    "pass": not failed,
                    "detail": "被选方案不得留有未通过的硬约束",
                }
            )
        if len(cands) >= 3:
            checks.append(
                {
                    "check": "candidate_count_at_least_3",
                    "computed": len(cands),
                    "declared": 3,
                    "limit": 3,
                    "pass": True,
                    "detail": "案例提供 3 个版本化候选方案",
                }
            )
    return checks


def cmd_audit(output_dir, write_back=False):
    path = os.path.join(output_dir, "result.json")
    result, err = load_json_file(path)
    if result is None:
        log("无法读取 result.json: %s" % err)
        return 1
    checks = audit_checks(result)
    for c in checks:
        log("[%s] %s computed=%s declared=%s" % ("PASS" if c["pass"] else "FAIL", c["check"], c["computed"], c["declared"]))
    if write_back:
        container = None
        for alias in ("evidence_and_risks", "evidence", "risks"):
            _p, val = find_first(result, (alias,))
            if isinstance(val, dict):
                container = val
                break
        if container is None:
            container = result
        container["feasibility_checks"] = [
            {
                "check": c["check"],
                "computed": c["computed"],
                "limit": c["limit"],
                "pass": c["pass"],
                "detail": c["detail"],
            }
            for c in checks
        ]
        log_write(result, "audit_writeback", "回填 %d 条算术校验" % len(checks))
        atomic_write_json(path, result)
        log("已回填 feasibility_checks: %d 条" % len(checks))
    return 0


def log_write(result, event, detail):
    result.setdefault("change_log", []).append({"ts": now_iso(), "event": event, "detail": detail})


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def _locator_to_file(locator, input_dir):
    raw = str(locator).strip()
    raw = raw.split("#")[0]
    raw = re.sub(r":(?:p|page|L|l)?\d+(?:-\d+)?$", "", raw)
    raw = raw.strip().strip("\"'")
    for base in (input_dir, os.path.dirname(os.path.abspath(input_dir))):
        cand = os.path.normpath(os.path.join(base, raw))
        if os.path.isfile(cand):
            return cand
    return None


def _norm_ws(text):
    return re.sub(r"\s+", "", text or "")


def verify_quotes(result, input_dir, work_dir):
    """逐字校验：locator 指向本地文本时可核对 quote 是否真的出现在该文件中。"""
    findings = []
    containers = find_all(result, "evidence")
    items = []
    for _p, val in containers:
        if isinstance(val, list):
            items.extend(val)
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            findings.append({"level": "error", "code": "evidence_not_object", "detail": "第 %d 条证据不是对象" % idx})
            continue
        loc = item.get("locator") or item.get("source_locator") or item.get("ref")
        quote = item.get("quote") or item.get("verbatim") or item.get("text")
        if is_empty(loc):
            findings.append({"level": "error", "code": "evidence_missing_locator", "detail": "第 %d 条" % idx})
            continue
        if is_empty(quote):
            findings.append({"level": "error", "code": "evidence_missing_quote", "detail": "第 %d 条" % idx})
            continue
        quote = str(quote)
        if len(_norm_ws(quote)) < 8:
            findings.append({"level": "warn", "code": "evidence_quote_too_short", "detail": "第 %d 条：%r" % (idx, quote[:40])})
        target = _locator_to_file(loc, input_dir)
        if target is None:
            findings.append(
                {"level": "warn", "code": "evidence_locator_unresolved", "detail": "第 %d 条 locator=%s" % (idx, loc)}
            )
            continue
        ext = os.path.splitext(target)[1].lower()
        text = None
        if ext == ".pdf":
            pdf_dir = os.path.join(work_dir, "pdf_text")
            if os.path.isdir(pdf_dir):
                for name in sorted(os.listdir(pdf_dir)):
                    if os.path.splitext(os.path.basename(target))[0][:12] in name:
                        text, _ = read_text_file(os.path.join(pdf_dir, name))
                        break
            level = "warn"  # PDF 抽取可能改变断词/连字符，只降级为提示
        else:
            text, _ = read_text_file(target)
            level = "error"
        if text is None:
            findings.append({"level": "warn", "code": "evidence_text_unavailable", "detail": "第 %d 条 %s" % (idx, loc)})
            continue
        if _norm_ws(quote) not in _norm_ws(text):
            findings.append(
                {"level": level, "code": "evidence_quote_not_found", "detail": "第 %d 条 locator=%s quote=%s" % (idx, loc, _norm_ws(quote)[:40])}
            )
    return findings


def check_result(result, probe_report, input_dir, work_dir, output_dir):
    errors, warnings = [], []
    contract = (probe_report or {}).get("output_contract")

    def err(code, detail):
        errors.append({"code": code, "detail": detail})

    def warn(code, detail):
        warnings.append({"code": code, "detail": detail})

    # 1. 信封
    for key in ENVELOPE:
        if key not in result:
            err("envelope_missing", key)
    if not isinstance(result.get("case_id"), str) or not result.get("case_id", "").strip():
        err("case_id_missing", "case_id 必须是非空字符串")
    status = result.get("status")
    if status not in STATUS_VALUES:
        err("status_invalid", "status=%r 不在 %s" % (status, ", ".join(STATUS_VALUES)))
    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if key in result and not isinstance(result[key], list):
            err("envelope_type", "%s 必须是数组" % key)
    if status == "insufficient_evidence":
        if not result.get("limitations") and not result.get("pending_human"):
            warn("status_reason_missing", "insufficient_evidence 需要 limitations 或 pending_human 说明缺口")

    # 2. 契约镜像
    if isinstance(contract, dict) and contract:
        for path in contract_leaf_paths(contract):
            if not path:
                continue
            ok, _val = get_path(result, path)
            if not ok:
                err("contract_key_missing", "/".join(path))
        for key in contract.keys():
            if key not in result:
                err("contract_root_key_missing", key)

    # 3. 六要素
    six_state = {}
    for name, aliases in SIX.items():
        _p, val = find_first(result, aliases)
        six_state[name] = not is_empty(val)
        if is_empty(val):
            err("six_element_empty", name)
    if status == "ok" and not all(six_state.values()):
        err("status_not_honest", "六要素未齐但 status=ok")

    # 4. 参数卫生
    plan = _plan_ctx(result)
    params = find_first(plan, ("parameters", "params", "parameter_ranges"))[1]
    if isinstance(params, list):
        for prm in params:
            if not isinstance(prm, dict):
                continue
            name = prm.get("name", "unnamed")
            basis = prm.get("basis")
            if basis not in BASIS_VALUES:
                err("parameter_basis_invalid", "%s basis=%r" % (name, basis))
            if is_empty(prm.get("unit")) and as_num(prm.get("value", prm.get("value_or_range"))) is not None:
                err("parameter_unit_missing", str(name))
            if basis and basis != "assumption" and is_empty(prm.get("basis_locator")):
                err("parameter_locator_missing", "%s 依据=%s 但无 basis_locator" % (name, basis))
            if basis == "assumption" and is_empty(prm.get("outside_range_action")):
                warn("assumption_without_action", "%s 为假设值，建议写明超界处置" % name)

    # 5. 对照组目的
    ctrls = find_first(plan, ("controls", "control_groups", "control_arms"))[1]
    if isinstance(ctrls, list) and ctrls:
        for c in ctrls:
            if not isinstance(c, dict):
                continue
            if is_empty(c.get("purpose")) or is_empty(c.get("excludes_confound", c.get("excludes"))):
                warn("control_purpose_missing", "%s 未写排除哪类混杂" % c.get("id", c.get("name", "?")))

    # 6. 候选方案
    cands = find_first(plan, ("candidates", "candidate_plans", "options"))[1]
    if isinstance(cands, list):
        if 0 < len(cands) < 3:
            warn("candidate_count_low", "仅 %d 个候选，案例通常给 3 个" % len(cands))
        for c in cands:
            if isinstance(c, dict) and c.get("pass") is False and is_empty(c.get("rejected_reason", c.get("reason"))):
                warn("rejected_reason_missing", "%s" % c.get("id", "?"))
    if isinstance(contract, dict) and any(k in contract for k in ("plan",)) and is_empty(cands):
        warn("candidates_absent", "未记录候选方案比较")

    # 7. 算术
    checks = audit_checks(result)
    for c in checks:
        if not c["pass"]:
            err("arithmetic_violation", "%s computed=%s declared=%s" % (c["check"], c["computed"], c["declared"]))
    recorded = find_first(result, ("feasibility_checks",))[1]
    if isinstance(recorded, list) and recorded and checks and len(recorded) < len(checks):
        warn("feasibility_checks_incomplete", "记录 %d 条，实算 %d 条" % (len(recorded), len(checks)))

    # 8. 证据
    ev_findings = verify_quotes(result, input_dir, work_dir)
    for f in ev_findings:
        (err if f["level"] == "error" else warn)(f["code"], f["detail"])
    _p, ev = find_first(result, ("evidence",))
    if is_empty(ev):
        warn("evidence_absent", "无 evidence 条目，结论将无法追溯")

    # 9. plan.md 一致性
    plan_path = os.path.join(output_dir, "plan.md")
    if not os.path.isfile(plan_path):
        err("plan_md_missing", plan_path)
    else:
        current, _ = read_text_file(plan_path)
        rendered = render_md(result)
        if _norm_ws(current) != _norm_ws(rendered):
            warn("plan_md_stale", "plan.md 与 result.json 不一致，运行 render 重生成")

    summary = {
        "errors": len(errors),
        "warnings": len(warnings),
        "six_elements": six_state,
        "audit_checks": len(checks),
        "audit_failed": sum(1 for c in checks if not c["pass"]),
        "status": status,
    }
    return {"errors": errors, "warnings": warnings, "summary": summary, "checks": checks}


def cmd_check(input_dir, output_dir, work_dir, strict=False):
    probe_path = os.path.join(work_dir, "input_probe.json")
    probe_report = None
    if os.path.isfile(probe_path):
        probe_report, _err = load_json_file(probe_path)
    if probe_report is None:
        probe_report = probe(input_dir, work_dir)
    result_path = os.path.join(output_dir, "result.json")
    result, err = load_json_file(result_path)
    if result is None:
        print(json.dumps({"errors": [{"code": "result_unreadable", "detail": str(err)}], "warnings": []}, ensure_ascii=False))
        return 1
    report = check_result(result, probe_report, input_dir, work_dir, output_dir)
    atomic_write_json(os.path.join(work_dir, "check_report.json"), report)
    print(json.dumps(report["summary"], ensure_ascii=False))
    for e in report["errors"]:
        log("ERROR %s: %s" % (e["code"], e["detail"]))
    for w in report["warnings"]:
        log("WARN  %s: %s" % (w["code"], w["detail"]))
    if report["errors"]:
        return 1
    if strict and report["warnings"]:
        return 1
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_opts(argv):
    opts = {
        "input": DEFAULT_INPUT,
        "output": DEFAULT_OUTPUT,
        "work": DEFAULT_WORK,
        "strict": False,
        "force": False,
        "write": False,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work") and i + 1 < len(argv):
            opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        if a == "--strict":
            opts["strict"] = True
        elif a == "--force":
            opts["force"] = True
        elif a == "--write":
            opts["write"] = True
        elif a in ("-h", "--help"):
            opts["help"] = True
        i += 1
    return opts


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd = args[0]
    opts = parse_opts(args[1:])
    inp, out, work = opts["input"], opts["output"], opts["work"]
    try:
        if cmd == "probe":
            rep = probe(inp, work)
            print(json.dumps({"case_id": rep["case_id"], "files": len(rep["files"]), "contract": rep["contract_source"]}, ensure_ascii=False))
            return 0
        if cmd == "scaffold":
            return cmd_scaffold(inp, out, work, force=opts["force"])
        if cmd == "render":
            return cmd_render(out)
        if cmd == "audit":
            return cmd_audit(out, write_back=opts["write"])
        if cmd == "check":
            return cmd_check(inp, out, work, strict=opts["strict"])
        if cmd == "all":
            probe(inp, work)
            cmd_scaffold(inp, out, work, force=opts["force"])
            cmd_render(out)
            cmd_audit(out, write_back=opts["write"])
            return cmd_check(inp, out, work, strict=opts["strict"])
        log("未知命令: %s" % cmd)
        print(USAGE)
        return 2
    except Exception as exc:  # 兜底：任何异常都不让进程崩到无产物状态
        log("fatal: %r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
