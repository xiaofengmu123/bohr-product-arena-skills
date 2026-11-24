#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-plan-arbiter 核心实现（薄入口见 main.py）。

子命令：
  probe     扫描 /input，识别 case.json / task.md / 材料 / PDF / 合同 / 候选方案 / 参数域，
            写 /app/work/input_probe.json。
  scaffold  按 case.json 的 output_contract 落盘 partial result.json 与 plan.md 骨架。
            默认保护已有进度（不覆盖）；--force 可强制重写。
  validate  校验 result.json：结构、类型、output_contract 全键、六要素、预算/排期算术、
            证据 locator。退出码 0=可用（可含警告），1=必须修复。

无子命令时按 probe -> scaffold 顺序执行（先落盘再分析）。

仅依赖 Python 标准库；输出确定性；UTF-8-sig 容错；原子写（.tmp -> os.replace）。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_FILE = 2 * 1024 * 1024  # 2MB
MAX_PROBE_HINTS = 60
TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".rst"}
ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# 无 output_contract 时使用的 fallback 输出结构。
FALLBACK_CONTRACT = {
    "plan": {
        "research_question": None,
        "hypotheses": [],
        "variables": [],
        "controls": [],
        "steps": [],
        "parameters": [],
        "resources": [],
        "readout_criteria": [],
        "time_cost": {
            "total_hours_estimate": None,
            "schedule": [],
            "budget": [],
            "note": None,
        },
    },
    "evidence_and_risks": {"evidence": [], "risks": []},
    "confirmations": {
        "safety": [],
        "ethics": [],
        "equipment_availability": [],
        "expert_review": [],
    },
    "candidate_comparison": [],
    "arithmetic_validation": [],
    "selected_plan": None,
}

BUDGET_KEYS = {"budget", "costs", "cost_items", "budget_lines", "预算", "费用"}
SCHEDULE_KEYS = {"schedule", "steps", "procedure", "gantt", "步骤", "排期"}
TOTAL_KEYS = ("total", "total_cost", "amount", "总计", "合计")
TOTAL_MIN_KEYS = ("total_duration_min", "total_min", "总时长")


# --------------------------------------------------------------------------- #
# 基础 I/O：容错读、原子写
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def read_text(path, max_bytes=MAX_TEXT_FILE):
    """容错读文本，返回 (text, err)。依次尝试 utf-8-sig / utf-8 / latin-1。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error:%s" % exc
    if size > max_bytes:
        return None, "too_large(%dB)" % size
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, "read_error:%s" % exc
    return None, "undecodable"


def load_json_tolerant(path):
    """容错读 JSON，返回 (data, err)。"""
    text, err = read_text(path)
    if text is None:
        return None, err or "unreadable"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - 坏 JSON 需继续
        return None, "json_parse_error:%s" % exc


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def atomic_write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def scan_input(input_dir):
    files = []
    errors = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing:%s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            if name in (".DS_Store", "Thumbs.db"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files.append(
                {
                    "path": full,
                    "rel": rel,
                    "name": name,
                    "size": size,
                    "ext": os.path.splitext(name)[1].lower(),
                }
            )
    files.sort(key=lambda f: f["rel"])
    return files, errors


def categorize(rel, ext):
    low = rel.lower()
    if any(k in low for k in ("candidate", "plan", "variant", "version", "方案", "候选")):
        return "candidate_plan"
    if any(k in low for k in ("contract", "resource", "price", "booking", "quote", "合同", "报价", "排期")):
        return "contract"
    if any(k in low for k in ("parameter", "domain", "range", "量程", "参数", "区间")):
        return "parameter_domain"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return "other"


def find_named(files, target_names, contains=None):
    for f in files:
        if f["name"].lower() in target_names:
            return f
    if contains:
        for f in files:
            if any(c in f["name"].lower() for c in contains):
                return f
    return None


def extract_case_id_from_json(data):
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_challenge_id_from_md(text):
    if not text:
        return None
    m = re.search(
        r"challenge[-_]?id[\s`\"':：=]*[`\"']?([A-Za-z0-9][A-Za-z0-9_.-]{2,})",
        text,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def extract_pdf_text(pdf_path, work_dir):
    """用 pdftotext 提取正文，返回 (txt_path|None, err|None)。"""
    try:
        with open(pdf_path, "rb") as fh:
            digest = hashlib.sha1(fh.read(65536)).hexdigest()[:12]
    except OSError:
        digest = hashlib.sha1(pdf_path.encode("utf-8", "replace")).hexdigest()[:12]
    out_dir = os.path.join(work_dir, "pdf_text")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, digest + ".txt")
    if os.path.exists(out):
        return out, None
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return None, "pdftotext_not_found"
    except subprocess.TimeoutExpired:
        return None, "pdftotext_timeout"
    except Exception as exc:  # noqa: BLE001
        return None, "pdftotext_error:%s" % exc
    if proc.returncode != 0 or not os.path.exists(out):
        return None, ("pdftotext_failed:%s" % (proc.stderr or "").strip())[:200]
    return out, None


def scan_text_hints(text, rel, limit=MAX_PROBE_HINTS):
    hits = []
    for idx, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        kind = None
        if any(k in low for k in ("candidate", "variant", "version", "候选", "方案")):
            kind = "candidate"
        elif any(k in low for k in ("contract", "booking", "price", "quote", "合同", "报价", "排期")):
            kind = "contract"
        elif any(k in low for k in ("domain", "range", "量程", "区间", "参数")):
            kind = "parameter"
        if kind:
            hits.append({"type": kind, "locator": "%s#L%d" % (rel, idx + 1), "line": line[:200]})
            if len(hits) >= limit:
                break
    return hits


def build_probe(input_dir, work_dir, extract_pdfs=True):
    """构建输入探测结果（不写盘）。"""
    t0 = time.time()
    os.makedirs(work_dir, exist_ok=True)
    files, scan_errors = scan_input(input_dir)
    probe = {
        "input_dir": os.path.abspath(input_dir),
        "case_id": None,
        "case_id_source": None,
        "output_contract": None,
        "output_contract_present": False,
        "contract_keys": [],
        "files": [],
        "case_json_path": None,
        "task_md_path": None,
        "candidate_plans": [],
        "contracts": [],
        "parameter_domains": [],
        "pdfs": [],
        "hints": [],
        "errors": [],
        "degradations": [],
        "has_any_input": bool(files),
        "generated_at": now_iso(),
        "runtime_ms": None,
    }
    for err in scan_errors:
        probe["errors"].append({"locator": "input_dir", "message": err})

    for f in files:
        item = dict(f)
        item["category"] = categorize(f["rel"], f["ext"])
        probe["files"].append(item)
        if item["category"] == "pdf":
            probe["pdfs"].append({"rel": f["rel"], "path": f["path"], "size": f["size"]})
        elif item["category"] == "candidate_plan":
            probe["candidate_plans"].append({"rel": f["rel"], "locator": f["rel"]})
        elif item["category"] == "contract":
            probe["contracts"].append({"rel": f["rel"], "locator": f["rel"]})
        elif item["category"] == "parameter_domain":
            probe["parameter_domains"].append({"rel": f["rel"], "locator": f["rel"]})

    # 定位 case.json / task.md
    case_file = find_named(files, {"case.json", "case_input.json"}, contains=("case",))
    task_file = None
    for f in files:
        if f["name"].lower() == "task.md":
            task_file = f
            break
    if task_file is None:
        task_file = find_named(files, set(), contains=("task",))

    case_data = None
    task_text = None
    if case_file:
        probe["case_json_path"] = case_file["rel"]
        case_data, err = load_json_tolerant(case_file["path"])
        if err:
            probe["errors"].append({"locator": case_file["rel"], "message": err})
            probe["degradations"].append("case.json 解析失败，改用 task.md/材料继续")
    if task_file:
        probe["task_md_path"] = task_file["rel"]
        task_text, err = read_text(task_file["path"])
        if err:
            probe["degradations"].append("task.md 读取失败：%s" % err)

    # case_id
    cid = extract_case_id_from_json(case_data)
    if cid:
        probe["case_id"], probe["case_id_source"] = cid, "case.json"
    else:
        cid = extract_challenge_id_from_md(task_text)
        if cid:
            probe["case_id"], probe["case_id_source"] = cid, "task.md"
        else:
            probe["case_id"], probe["case_id_source"] = "unknown", "missing"

    # output_contract
    if isinstance(case_data, dict):
        contract = case_data.get("output_contract")
        if contract is None:
            contract = case_data.get("outputContract")
        if isinstance(contract, dict):
            probe["output_contract"] = contract
            probe["output_contract_present"] = True
            probe["contract_keys"] = list(contract.keys())
        # 从 case.json 内嵌结构补充候选/合同/参数域定位
        for key in case_data.keys():
            lk = str(key).lower()
            if any(t in lk for t in ("candidate", "plans", "variants", "versions")):
                probe["candidate_plans"].append(
                    {"rel": probe["case_json_path"], "locator": "%s#%s" % (probe["case_json_path"], key)}
                )
            elif any(t in lk for t in ("contract", "resource", "price", "booking", "budget")):
                probe["contracts"].append(
                    {"rel": probe["case_json_path"], "locator": "%s#%s" % (probe["case_json_path"], key)}
                )
            elif any(t in lk for t in ("domain", "parameter", "range")):
                probe["parameter_domains"].append(
                    {"rel": probe["case_json_path"], "locator": "%s#%s" % (probe["case_json_path"], key)}
                )

    # 文本提示：候选/合同/参数域逐行定位
    for f in files:
        if f["ext"] not in TEXT_EXTS:
            continue
        text, err = read_text(f["path"])
        if text is None:
            if err and err.startswith("too_large"):
                probe["degradations"].append("%s 超过 2MB 跳过正文扫描" % f["rel"])
            continue
        probe["hints"].extend(scan_text_hints(text, f["rel"]))
        if len(probe["hints"]) >= MAX_PROBE_HINTS:
            break

    # PDF 正文提取
    if extract_pdfs and probe["pdfs"]:
        for pdf in probe["pdfs"]:
            txt_path, err = extract_pdf_text(pdf["path"], work_dir)
            if err:
                pdf["text_error"] = err
                probe["degradations"].append("PDF %s 无法提取正文：%s" % (pdf["rel"], err))
            else:
                pdf["text_path"] = os.path.relpath(txt_path, work_dir).replace(os.sep, "/")
                # 用正文补充参数/合同/候选提示
                txt, _ = read_text(txt_path)
                if txt:
                    probe["hints"].extend(scan_text_hints(txt, pdf["rel"] + "#text", limit=20))

    probe["hints"] = probe["hints"][:MAX_PROBE_HINTS]
    probe["runtime_ms"] = int((time.time() - t0) * 1000)
    return probe


# --------------------------------------------------------------------------- #
# 骨架生成
# --------------------------------------------------------------------------- #
def default_for(spec):
    """按契约规格递归给出空值：dict 递归、list->[]、标量->None。"""
    if isinstance(spec, dict):
        return {k: default_for(v) for k, v in spec.items()}
    if isinstance(spec, list):
        return []
    return None


def build_scaffold(probe):
    result = {}
    contract = probe.get("output_contract")
    if isinstance(contract, dict):
        for key, spec in contract.items():
            result[key] = default_for(spec)
    else:
        for key, spec in FALLBACK_CONTRACT.items():
            result[key] = default_for(spec)

    status = "partial" if probe.get("has_any_input") else "insufficient_evidence"
    result["case_id"] = probe.get("case_id") or "unknown"
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [
        {
            "ts": now_iso(),
            "event": "skeleton_written",
            "detail": "partial skeleton written before analysis (case_id=%s)" % result["case_id"],
        }
    ]
    result["errors"] = list(probe.get("errors") or [])
    result["degradations"] = list(probe.get("degradations") or [])
    result["runtime"] = {"started_at": now_iso(), "probe_runtime_ms": probe.get("runtime_ms")}
    if result["case_id"] == "unknown":
        result["pending_human"].append("case_id 缺失：未能从 case.json/task.md 解析，需人工确认")
    if not probe.get("has_any_input"):
        result["uncertainty"].append("输入目录为空或缺失，无法生成方案，仅落盘最小合法骨架")
    return result


PLAN_TEMPLATE = """# 实验方案（骨架 / {status}）

- case_id: {case_id}
- 状态: {status}（分析前骨架，待增量填实）
- 生成时间: {ts}
- 数据来源约定: 每条关键结论必须带 locator + 逐字 quote；见 references/schema_and_checks.md

## 一、假设与变量
（待填：可检验假设；自变量/因变量/混杂因素；每变量的操作定义、测量方式、单位、量程）

## 二、对照组
（待填：空白/阳性/阴性/剂量梯度；每个对照写明排除哪一类混杂）

## 三、设备材料
（待填：设备型号/量程、耗材规格、试剂信息；标可替代与不可替代）

## 四、时间成本
（待填：每步时长、并行关系、样本量、预算。算术：总价=Σ(单价×数量)；总时长=关键路径）

## 五、参数范围
（待填：参数、单位、允许区间、超出区间处置、依据来源[设备手册/预实验/文献]）

## 六、风险与异常
（待填：失败模式[污染/无信号/重复性差/样本不足…]、判据、止损点、替代路径）

## 七、候选方案比较与算术校验
（待填：3 个候选逐项比较量程/预算/排期；给出选择与修订理由）

## 八、确认项
（待填：安全、伦理、设备可用性、专家确认；未确认不得写成事实）

## 依据与来源
（待填：每条 locator + 逐字 quote；冲突标 source_conflict；缺失标 not_reported）
"""


def build_plan_md(probe, result):
    status = (result or {}).get("status") or "partial"
    case_id = (result or {}).get("case_id") or probe.get("case_id") or "unknown"
    return PLAN_TEMPLATE.format(status=status, case_id=case_id, ts=now_iso())


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def non_empty(value):
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(non_empty(v) for v in value)
    if isinstance(value, dict):
        return any(non_empty(v) for v in value.values())
    return True


def find_key(obj, key, out=None):
    """递归查找同名键（大小写不敏感），返回全部取值列表。"""
    if out is None:
        out = []
    low = key.lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == low:
                out.append(v)
            find_key(v, key, out)
    elif isinstance(obj, list):
        for v in obj:
            find_key(v, key, out)
    return out


def walk(obj, out=None):
    """产出 (parent_dict_or_None, key_or_None, value) 三元组。"""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append((obj, k, v))
            walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out)
    return out


def six_element_status(result):
    """检查六要素，返回 {元素: bool}。"""
    groups = {
        "假设与变量": ["hypotheses", "hypothesis", "variables", "variable", "假设", "变量"],
        "对照组": ["controls", "control", "control_group", "对照"],
        "设备材料": ["resources", "resource", "equipment", "materials", "reagents", "设备", "材料", "试剂"],
        "时间成本": ["time_cost", "schedule", "duration", "duration_min", "duration_hours",
                    "total_duration_min", "total_hours_estimate", "budget", "cost",
                    "时长", "预算", "时间"],
        "参数范围": ["parameters", "parameter", "range", "domain", "参数", "量程", "区间"],
        "风险与异常": ["risks", "risk", "contingency", "风险", "异常"],
    }
    status = {}
    for elem, keys in groups.items():
        vals = []
        for key in keys:
            vals.extend(find_key(result, key))
        status[elem] = any(non_empty(v) for v in vals)
    return status


def critical_path(durations, deps):
    """DAG 最长路径（分钟）。依赖成环返回 None。"""
    nodes = list(durations.keys())
    indeg = {n: 0 for n in nodes}
    adj = {n: [] for n in nodes}
    for n in nodes:
        for d in deps.get(n, []):
            if d in durations and d != n:
                adj[d].append(n)
                indeg[n] += 1
    queue = [n for n in nodes if indeg[n] == 0]
    topo = []
    head = 0
    while head < len(queue):
        n = queue[head]
        head += 1
        topo.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if len(topo) != len(nodes):
        return None
    dist = {n: float(durations[n]) for n in nodes}
    for n in topo:
        for m in adj[n]:
            cand = dist[n] + durations[m]
            if cand > dist[m]:
                dist[m] = cand
    return max(dist.values()) if dist else 0.0


def check_budget(result):
    errors = []
    for parent, key, value in walk(result):
        if not isinstance(value, list) or str(key).lower() not in BUDGET_KEYS:
            continue
        total = 0.0
        numeric = True
        entries = 0
        for i, entry in enumerate(value):
            if not isinstance(entry, dict):
                continue
            entries += 1
            up = entry.get("unit_price")
            qty = entry.get("quantity")
            sub = entry.get("subtotal")
            if isinstance(up, (int, float)) and isinstance(qty, (int, float)):
                expected = float(up) * float(qty)
                if isinstance(sub, (int, float)) and abs(float(sub) - expected) > 1e-6:
                    errors.append(
                        "%s[%d] subtotal 不一致: %s != %s×%s=%s" % (key, i, sub, up, qty, expected)
                    )
            if isinstance(sub, (int, float)):
                total += float(sub)
            elif isinstance(up, (int, float)) and isinstance(qty, (int, float)):
                total += float(up) * float(qty)
            else:
                numeric = False
        if numeric and entries and isinstance(parent, dict):
            for tkey in TOTAL_KEYS:
                tv = parent.get(tkey)
                if isinstance(tv, (int, float)) and abs(float(tv) - total) > 1e-6:
                    errors.append("%s 合计不一致: %s=%s != Σ条目=%s" % (key, tkey, tv, total))
    return errors


def check_schedule(result):
    errors = []
    for parent, key, value in walk(result):
        if not isinstance(value, list) or str(key).lower() not in SCHEDULE_KEYS:
            continue
        durations = {}
        deps = {}
        usable = True
        for i, entry in enumerate(value):
            if not isinstance(entry, dict):
                usable = False
                continue
            nid = entry.get("id") or entry.get("step_id") or entry.get("name") or ("n%d" % i)
            dur = entry.get("duration_min")
            if dur is None and isinstance(entry.get("duration_hours"), (int, float)):
                dur = float(entry["duration_hours"]) * 60.0
            if isinstance(dur, (int, float)) and float(dur) >= 0:
                durations[nid] = float(dur)
            else:
                usable = False
            dep = entry.get("depends_on") or entry.get("depends") or []
            deps[nid] = dep if isinstance(dep, list) else [dep]
        if not usable or not durations:
            continue
        cp = critical_path(durations, deps)
        if cp is None:
            errors.append("%s 依赖成环，无法计算关键路径" % key)
            continue
        if isinstance(parent, dict):
            for tkey in TOTAL_MIN_KEYS:
                tv = parent.get(tkey)
                if isinstance(tv, (int, float)) and abs(float(tv) - cp) > 1e-6:
                    errors.append("%s 关键路径不一致: %s=%s != 计算值=%s" % (key, tkey, tv, cp))
    return errors


def validate_result(probe, result):
    errors = []
    warnings = []
    if not isinstance(result, dict):
        return ["result 不是 JSON 对象"], warnings

    cid = result.get("case_id")
    if not cid:
        errors.append("缺少 case_id")
    elif cid == "unknown":
        warnings.append("case_id=unknown，需人工补填并写入 pending_human")

    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append("status 非法: %r，须为 %s" % (status, list(ALLOWED_STATUS)))

    contract = probe.get("output_contract")
    if isinstance(contract, dict):
        for key in contract.keys():
            if key not in result:
                errors.append("缺少 output_contract 字段: %s" % key)

    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if key in result and not isinstance(result[key], list):
            errors.append("%s 应为数组" % key)

    six = six_element_status(result)
    missing = [elem for elem, ok in six.items() if not ok]
    for elem in missing:
        warnings.append("六要素缺失/为空: %s" % elem)
    if status == "ok" and missing:
        errors.append("status=ok 但六要素缺失: %s（status 不诚实）" % "、".join(missing))

    errors.extend(check_budget(result))
    errors.extend(check_schedule(result))

    evidence = []
    for val in find_key(result, "evidence"):
        if isinstance(val, list):
            evidence.extend([e for e in val if isinstance(e, dict)])
    if evidence:
        missing_loc = [i for i, e in enumerate(evidence) if not (e.get("locator") or e.get("来源"))]
        if missing_loc:
            warnings.append("evidence 中 %d 条缺少 locator" % len(missing_loc))
        missing_quote = [i for i, e in enumerate(evidence) if not (e.get("quote") or e.get("引用"))]
        if missing_quote:
            warnings.append("evidence 中 %d 条缺少 quote" % len(missing_quote))
    else:
        warnings.append("未发现 evidence 条目（每条依据应带 locator+quote）")

    return errors, warnings


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def resolve_paths(args):
    args.input = args.input or DEFAULT_INPUT
    args.output = args.output or DEFAULT_OUTPUT
    args.work = args.work or DEFAULT_WORK
    return args


def run_probe(args):
    args = resolve_paths(args)
    os.makedirs(args.work, exist_ok=True)
    probe = build_probe(args.input, args.work, extract_pdfs=True)
    atomic_write_json(os.path.join(args.work, "input_probe.json"), probe)
    _print_probe_summary(probe)
    return 0


def result_exists_valid(rpath):
    if not os.path.exists(rpath):
        return False
    data, _err = load_json_tolerant(rpath)
    return isinstance(data, dict)


def run_scaffold(args):
    args = resolve_paths(args)
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    probe = build_probe(args.input, args.work, extract_pdfs=False)
    atomic_write_json(os.path.join(args.work, "input_probe.json"), probe)

    rpath = os.path.join(args.output, "result.json")
    result = None
    if not args.force and result_exists_valid(rpath):
        result, _ = load_json_tolerant(rpath)
        print("[scaffold] result.json 已存在且可解析，跳过骨架落盘以保护增量进度（--force 可覆盖）")
    else:
        result = build_scaffold(probe)
        atomic_write_json(rpath, result)
        print("[scaffold] 已写 partial result.json：case_id=%s, status=%s, 契约键=%d"
              % (result["case_id"], result["status"], len(probe.get("contract_keys") or [])))

    ppath = os.path.join(args.output, "plan.md")
    if not args.force and os.path.exists(ppath):
        print("[scaffold] plan.md 已存在，跳过重写（--force 可覆盖）")
    else:
        atomic_write_text(ppath, build_plan_md(probe, result))
        print("[scaffold] 已写 plan.md 骨架")
    return 0


def run_validate(args):
    args = resolve_paths(args)
    os.makedirs(args.work, exist_ok=True)
    probe = build_probe(args.input, args.work, extract_pdfs=False)
    rpath = os.path.join(args.output, "result.json")

    if not os.path.exists(rpath):
        report = {
            "ok": False,
            "errors": ["result.json 不存在: %s" % rpath],
            "warnings": [],
            "generated_at": now_iso(),
        }
        atomic_write_json(os.path.join(args.work, "validate_report.json"), report)
        print("FAIL: result.json 不存在，先运行 scaffold")
        return 1

    result, err = load_json_tolerant(rpath)
    if result is None:
        errors = ["result.json 无法解析: %s" % err]
        warnings = []
    else:
        errors, warnings = validate_result(probe, result)

    if not os.path.exists(os.path.join(args.output, "plan.md")):
        warnings.append("plan.md 缺失（应产出与 result.json 一致的可编辑方案）")

    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "generated_at": now_iso(),
    }
    atomic_write_json(os.path.join(args.work, "validate_report.json"), report)

    print("== validate ==")
    print("errors=%d warnings=%d" % (len(errors), len(warnings)))
    for e in errors:
        print("  [ERROR] %s" % e)
    for w in warnings:
        print("  [WARN ] %s" % w)
    print("RESULT: %s" % ("PASS" if not errors else "NEED_FIX"))
    return 0 if not errors else 1


def _print_probe_summary(probe):
    print("== probe ==")
    print("input: %s" % probe["input_dir"])
    print("case_id: %s (%s)" % (probe["case_id"], probe["case_id_source"]))
    print("output_contract: %s keys=%s"
          % (probe["output_contract_present"], probe["contract_keys"]))
    print("files=%d pdfs=%d candidate_plans=%d contracts=%d parameter_domains=%d hints=%d"
          % (len(probe["files"]), len(probe["pdfs"]), len(probe["candidate_plans"]),
             len(probe["contracts"]), len(probe["parameter_domains"]), len(probe["hints"])))
    if probe["degradations"]:
        print("degradations: %s" % "; ".join(probe["degradations"]))
    if probe["errors"]:
        print("errors: %s" % "; ".join(str(e.get("message", e)) for e in probe["errors"]))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=None, help="输入目录，默认 /input")
    common.add_argument("--output", default=None, help="产物目录，默认 /app/output")
    common.add_argument("--work", default=None, help="工作目录，默认 /app/work")
    common.add_argument("--force", action="store_true", help="强制覆盖已有 result.json/plan.md")

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="sci-plan-arbiter 运行时工具（probe / scaffold / validate）",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("probe", parents=[common], help="扫描输入并写 input_probe.json")
    sub.add_parser("scaffold", parents=[common], help="按 output_contract 落盘 partial 骨架")
    sub.add_parser("validate", parents=[common], help="校验 result.json（退出码 0=可用）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "probe":
        return run_probe(args)
    if args.cmd == "validate":
        return run_validate(args)
    if args.cmd == "scaffold":
        return run_scaffold(args)
    # 默认：先落盘再分析
    rc = run_probe(args)
    if rc != 0:
        return rc
    return run_scaffold(args)


if __name__ == "__main__":
    sys.exit(main())
