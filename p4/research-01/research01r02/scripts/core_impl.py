#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 深度研究 Skill 的纯标准库实现。

被 scripts/main.py 与 scripts/selftest.py 复用，提供：
- 输入发现与文本/JSON 容错读取
- result.json 骨架、增量合并、汇总与六点自检的确定性逻辑
不依赖第三方包，也不依赖当前工作目录；所有路径由调用方显式传入。
"""

import copy
import hashlib
import json
import os
import re
import time

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

TEXT_EXTS = {".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html",
             ".tex", ".bib", ".yaml", ".yml", ".rst", ".log", ".py", ".ipynb"}
DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
            ".odt", ".ods", ".odp"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".tif", ".tiff",
            ".bmp"}
MAX_TEXT_BYTES = 2 * 1024 * 1024

# 900 秒总预算拆成阶段截止时刻（自 P0 起算的秒数）
PHASE_DEADLINES = {"P0": 180, "P1": 720, "P2": 2100, "P3": 3000, "P4": 3600}
TOTAL_BUDGET_SECONDS = 900
TOTAL_BUDGET_REQUESTS = 64

CONTRACT_KEYS = ("output_contract", "output_schema", "result_schema", "contract")
CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId")

JUDGMENTS = ("supports", "partial", "unsupported", "conflicts", "extraction_failed")
STRENGTHS = ("established", "inferred", "conjectural")

_BOILERPLATE = {"尚存争议", "需要更多研究", "有待进一步研究", "研究不足", "证据有限",
                "数据不足", "未知", "无", "none", "n/a"}


# --------------------------------------------------------------------------
# 基础 I/O（容错：坏编码、坏 JSON、大文件均不崩溃）
# --------------------------------------------------------------------------

def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if not path:
        return []
    try:
        os.makedirs(path, exist_ok=True)
        return []
    except OSError as exc:
        return [str(exc)]


def _decode(data, errors):
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    errors.append("无法解码文本（utf-8/gb18030/latin-1 均失败）")
    return ""


def read_text(path):
    """返回 (text, errors, degradations)。>2MB 截断并记入 degradations。"""
    errors, degs = [], []
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return "", [str(exc)], []
    if len(data) > MAX_TEXT_BYTES:
        degs.append("截断到 2MB：%s" % path)
        data = data[:MAX_TEXT_BYTES]
    return _decode(data, errors), errors, degs


def load_json(path):
    """返回 (obj, errors, degradations)；任何失败都返回 None 而不是抛异常。"""
    errors, degs = [], []
    if not os.path.exists(path):
        return None, ["缺失文件：%s" % path], []
    if os.path.getsize(path) > MAX_TEXT_BYTES:
        degs.append("超大 JSON 截断读取：%s" % path)
    text, errs, dg = read_text(path)
    errors.extend(errs)
    degs.extend(dg)
    try:
        return json.loads(text), errors, degs
    except (ValueError, TypeError) as exc:
        errors.append("JSON 解析失败 %s：%s" % (path, exc))
        return None, errors, degs


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return [str(exc)]


def write_json(path, obj):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path, obj):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return []
    except OSError as exc:
        return [str(exc)]


def read_jsonl(path):
    """逐行读 JSONL；坏行降级记录而不中断。"""
    errors, degs = [], []
    if not os.path.exists(path):
        return [], [], []
    text, errs, dg = read_text(path)
    errors.extend(errs)
    degs.extend(dg)
    items = []
    for idx, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except ValueError as exc:
            degs.append("%s 第 %d 行非 JSON：%s" % (path, idx + 1, exc))
    return items, errors, degs


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------

def discover_input(root):
    inv = {"exists": os.path.isdir(root), "root": root, "total_files": 0,
           "case_json": None, "task_md": None, "files": [], "materials": [],
           "by_kind": {}, "case_id": "", "contract_fields": []}
    if not inv["exists"]:
        return inv
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = -1
            ext = os.path.splitext(fn)[1].lower()
            entry = {"path": rel, "abs": fp, "size": size, "ext": ext}
            inv["files"].append(entry)
            inv["total_files"] += 1
            if fn == "case.json":
                kind = "case_json"
                if inv["case_json"] is None:
                    inv["case_json"] = fp
            elif fn in ("task.md", "task.txt"):
                kind = "task_md"
                if inv["task_md"] is None:
                    inv["task_md"] = fp
            elif ext in TEXT_EXTS:
                kind = "text"
            elif ext in DOC_EXTS:
                kind = "doc"
            elif ext in IMG_EXTS:
                kind = "image"
            else:
                kind = "other"
            inv["by_kind"].setdefault(kind, []).append(entry)
            if kind in ("text", "doc", "image"):
                inv["materials"].append(entry)

    if inv.get("case_json"):
        case_obj, _, _ = load_json(inv["case_json"])
        if isinstance(case_obj, dict):
            inv["case_id"] = extract_case_id(case_obj)
            inv["contract_fields"] = sorted(build_contract(case_obj).keys())
    return inv


# --------------------------------------------------------------------------
# 契约与骨架
# --------------------------------------------------------------------------

def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val).strip()
    return ""


def _contract_shape(val):
    """把 output_contract 原样转成‘结构一致、叶子为 null/[]’的骨架形状。"""
    if isinstance(val, dict):
        return {k: _contract_shape(v) for k, v in val.items()}
    if isinstance(val, list):
        return []
    return None


def build_contract(obj):
    """从 case.json 提取 output_contract 等价键，返回嵌套形状字典；缺则 {}。"""
    if not isinstance(obj, dict):
        return {}
    for key in CONTRACT_KEYS:
        val = obj.get(key)
        if isinstance(val, dict):
            return _contract_shape(val)
        if isinstance(val, list):
            shape = {}
            for item in val:
                if isinstance(item, str):
                    shape[item] = None
                elif isinstance(item, dict) and item.get("name"):
                    shape[item["name"]] = _contract_shape(
                        item.get("fields") or item.get("schema") or {})
            return shape
    return {}


def new_skeleton(case_id, contract, note=""):
    """构造合法 partial 结果；契约字段回填为 null/[] 形状。"""
    result = {
        "case_id": case_id or "",
        "status": STATUS_PARTIAL,
        "research_product": {
            "type": "synthesis",
            "question": "",
            "summary": "",
            "conclusions": [],
        },
        "evidence_matrix": [],
        "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
        "sources": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime_seconds": 0.0,
    }
    for field, shape in contract.items():
        result[field] = copy.deepcopy(shape)
    if note:
        result["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    return result


# --------------------------------------------------------------------------
# 增量合并（列表只追加 + 去重，保证幂等与不丢已落盘数据）
# --------------------------------------------------------------------------

def _item_key(item):
    if isinstance(item, dict):
        for key in ("id", "source", "question", "text", "field", "title", "claim"):
            if key in item and item[key] is not None:
                return (key, item[key])
        payload = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        return ("hash", hashlib.sha256(payload.encode("utf-8")).hexdigest())
    return ("val", item)


def deep_merge(base, patch):
    for key, val in patch.items():
        if key not in base:
            base[key] = copy.deepcopy(val)
            continue
        bv = base[key]
        if isinstance(bv, dict) and isinstance(val, dict):
            deep_merge(bv, val)
        elif isinstance(bv, list) and isinstance(val, list):
            seen = {repr(_item_key(x)) for x in bv}
            for item in val:
                if repr(_item_key(item)) not in seen:
                    bv.append(copy.deepcopy(item))
                    seen.add(repr(_item_key(item)))
        else:
            base[key] = copy.deepcopy(val)
    return base


# --------------------------------------------------------------------------
# 证据卡规范化
# --------------------------------------------------------------------------

def normalize_evidence_cards(cards):
    """补齐字段、补 id、校验判定值，保证 evidence_matrix 结构一致。"""
    errors, degs = [], []
    out = []
    counter = 1
    used = set()
    for card in cards:
        if not isinstance(card, dict):
            degs.append("跳过非对象证据卡：%s" % type(card).__name__)
            continue
        cid = card.get("id")
        if not (isinstance(cid, str) and cid.strip()):
            while ("E%d" % counter) in used:
                counter += 1
            cid = "E%d" % counter
            counter += 1
        if cid in used:
            degs.append("证据 id 重复，跳过：%s" % cid)
            continue
        used.add(cid)
        judgment = card.get("judgment", "supports")
        if judgment not in JUDGMENTS:
            degs.append("证据 %s 的 judgment 非法（%s），回退为 unsupported" % (cid, judgment))
            judgment = "unsupported"
        out.append({
            "id": cid,
            "subquestion": card.get("subquestion", ""),
            "source": card.get("source", ""),
            "locator": card.get("locator", ""),
            "quote": card.get("quote", ""),
            "claim": card.get("claim", ""),
            "judgment": judgment,
            "reason": card.get("reason", ""),
            "conclusion_ids": card.get("conclusion_ids") or [],
            "trace": card.get("trace") or {},
        })
    return out, errors, degs


# --------------------------------------------------------------------------
# 各子命令的业务逻辑（供 main.py 与 selftest.py 复用）
# --------------------------------------------------------------------------

def run_discover(input_dir):
    return discover_input(input_dir)


def run_skeleton(input_dir, output_dir, work_dir):
    summary = {"case_id": "", "contract_fields": [], "wrote": False,
               "already_exists": False, "errors": [], "degradations": []}
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    inv = discover_input(input_dir)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = load_json(inv["case_json"])
    case_id = extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = build_contract(case_obj if isinstance(case_obj, dict) else None)
    summary["case_id"] = case_id
    summary["contract_fields"] = sorted(contract.keys())
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)

    result_path = os.path.join(output_dir, "result.json")
    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = load_json(result_path)
        if not isinstance(existing, dict):
            result = new_skeleton(case_id, contract, note="修复不可读的 result.json")
            summary["errors"].extend(write_json(result_path, result))
            summary["wrote"] = True
    else:
        note = "研究开始前的骨架"
        result = new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            result["status"] = STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "未在 %s 下找到 case.json/task.md/材料" % input_dir)
        elif inv.get("case_json") and not isinstance(case_obj, dict):
            result["status"] = STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json 存在但不可解析；契约字段回退为空")
        summary["errors"].extend(write_json(result_path, result))
        summary["wrote"] = True

    # run_state：重复运行保留首次 started_at，避免阶段计时被重置
    state_path = os.path.join(work_dir, "run_state.json")
    old, _, _ = load_json(state_path)
    started_at = (old.get("started_at") if isinstance(old, dict) else None) or time.time()
    state = {
        "case_id": case_id,
        "contract": contract,
        "started_at": started_at,
        "deadlines": dict(PHASE_DEADLINES),
        "total_budget_seconds": TOTAL_BUDGET_SECONDS,
        "total_budget_requests": TOTAL_BUDGET_REQUESTS,
        "requests_used": 0,
        "current_phase": "P0",
        "p0_ok": False,
        "phase_history": (old.get("phase_history") if isinstance(old, dict) else []) or [],
        "inventory": inv,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
    }
    summary["errors"].extend(write_json(state_path, state))
    return summary


def run_checkpoint(output_dir, work_dir, phase=None, requests=None, note=""):
    ensure_dir(work_dir)
    state_path = os.path.join(work_dir, "run_state.json")
    state, errs, degs = load_json(state_path)
    if not isinstance(state, dict):
        return {"ok": False, "fatal": True,
                "errors": errs or ["run_state.json 缺失——请先执行 skeleton"],
                "degradations": degs}
    started_at = state.get("started_at")
    elapsed = None
    if isinstance(started_at, (int, float)):
        elapsed = round(time.time() - started_at, 1)
    deadline = PHASE_DEADLINES.get(phase) if phase else None
    late = bool(elapsed is not None and deadline is not None and elapsed > deadline)

    if isinstance(requests, (int, float)):
        state["requests_used"] = max(int(state.get("requests_used", 0)), int(requests))
    if phase:
        state["current_phase"] = phase
        hist = state.setdefault("phase_history", [])
        hist.append({"phase": phase, "elapsed_seconds": elapsed,
                     "requests_used": state.get("requests_used", 0),
                     "note": note or ""})

    p0_ok = True
    obj, _, _ = load_json(os.path.join(output_dir, "result.json"))
    if phase and phase != "P0" and not isinstance(obj, dict):
        p0_ok = False
    state["p0_ok"] = p0_ok
    write_json(state_path, state)

    rem_sec = max(0, TOTAL_BUDGET_SECONDS - elapsed) if elapsed is not None else TOTAL_BUDGET_SECONDS
    rem_req = max(0, TOTAL_BUDGET_REQUESTS - int(state.get("requests_used", 0)))
    return {"ok": True, "phase": phase, "elapsed_seconds": elapsed,
            "deadline_seconds": deadline, "late": late, "p0_ok": p0_ok,
            "remaining_seconds": rem_sec,
            "requests_used": int(state.get("requests_used", 0)),
            "remaining_requests": rem_req, "note": note or ""}


def run_trace(work_dir, action="", params=None, outcome="", summary="", note=""):
    ensure_dir(work_dir)
    params = params if isinstance(params, (dict, list)) else (params or "")
    entry = {"action": action, "params": params, "outcome": outcome,
             "summary": summary, "note": note, "ts": iso_now()}
    errs = append_jsonl(os.path.join(work_dir, "tool_ledger.jsonl"), entry)
    return {"ok": not errs, "errors": errs, "wrote": "tool_ledger.jsonl"}


def run_merge(output_dir, work_dir, payload_path):
    payload, errs, degs = load_json(payload_path)
    if not isinstance(payload, dict):
        return {"ok": False, "errors": errs or ["payload 非 JSON 对象"], "degradations": degs}
    result_path = os.path.join(output_dir, "result.json")
    result, e2, d2 = load_json(result_path)
    errs.extend(e2)
    degs.extend(d2)
    if not isinstance(result, dict):
        state, _, _ = load_json(os.path.join(work_dir, "run_state.json"))
        if not isinstance(state, dict):
            state = {}
        contract = state.get("contract") or {}
        case_id = state.get("case_id") or extract_case_id(payload) or ""
        result = new_skeleton(case_id, contract, note="merge 时重建骨架")
    result = deep_merge(result, payload)
    result.setdefault("change_log", []).append(
        {"ts": iso_now(), "action": "merge", "detail": os.path.basename(payload_path)})
    errs.extend(write_json(result_path, result))
    return {"ok": not errs, "errors": errs, "degradations": degs,
            "conclusions": len(result.get("research_product", {}).get("conclusions") or []),
            "evidence_rows": len(result.get("evidence_matrix") or [])}


def _build_report(result, notes):
    lines = []
    rp = result.get("research_product") or {}
    lines.append("# 深度研究报告\n")
    lines.append("- case_id：`%s`" % (result.get("case_id") or "（空）"))
    lines.append("- status：`%s`" % (result.get("status") or ""))
    lines.append("- runtime_seconds：%s" % result.get("runtime_seconds", 0))
    lines.append("\n## 研究问题\n\n%s" % (rp.get("question") or "（未填写）"))
    lines.append("\n## 综合结论\n\n%s" % (rp.get("summary") or "（未填写）"))
    lines.append("\n## 结论清单\n")
    for con in rp.get("conclusions") or []:
        ev = ", ".join(con.get("evidence_ids") or [])
        lines.append("- **%s**（%s）%s ← 证据 %s" % (
            con.get("id", ""), con.get("strength", ""), con.get("text", ""), ev or "无"))
    lines.append("\n## 证据矩阵\n")
    lines.append("| id | 来源 | 定位 | 论断 | 判定 |")
    lines.append("|---|---|---|---|---|")
    for ev in result.get("evidence_matrix") or []:
        lines.append("| %s | %s | %s | %s | %s |" % (
            ev.get("id", ""), str(ev.get("source", "")).replace("|", "\\|"),
            str(ev.get("locator", "")).replace("|", "\\|"),
            str(ev.get("claim", ""))[:80].replace("|", "\\|"),
            ev.get("judgment", "")))
    unc = result.get("uncertainty") or {}
    lines.append("\n## 冲突证据\n")
    for item in unc.get("conflicts") or []:
        lines.append("- %s" % json.dumps(item, ensure_ascii=False))
    lines.append("\n## 研究空白 / 待人工\n")
    for item in unc.get("gaps") or []:
        lines.append("- [gap] %s" % (json.dumps(item, ensure_ascii=False)
                                     if isinstance(item, (dict, list)) else str(item)))
    for item in result.get("pending_human") or []:
        lines.append("- [pending] %s" % (json.dumps(item, ensure_ascii=False)
                                         if isinstance(item, (dict, list)) else str(item)))
    lines.append("\n## 限制与异常\n")
    for item in result.get("limitations") or []:
        lines.append("- [limitation] %s" % item)
    for item in result.get("errors") or []:
        lines.append("- [error] %s" % item)
    for item in result.get("degradations") or []:
        lines.append("- [degradation] %s" % item)
    if notes.get("decomposition.md"):
        lines.append("\n## 问题拆解\n\n%s\n" % notes["decomposition.md"])
    if notes.get("search_plan.md"):
        lines.append("\n## 检索计划\n\n%s\n" % notes["search_plan.md"])
    if notes.get("synthesis.md"):
        lines.append("\n## 综合笔记\n\n%s\n" % notes["synthesis.md"])
    return "\n".join(lines)


def _build_tool_ledger(work_dir, output_dir, cards, summary):
    entries = []
    raw, errs, degs = read_jsonl(os.path.join(work_dir, "tool_ledger.jsonl"))
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    for item in raw:
        if isinstance(item, dict):
            entries.append(item)
    for card in cards:
        tr = card.get("trace")
        if isinstance(tr, dict) and tr:
            entry = {"source_card": card.get("id"), "tool": tr.get("tool", ""),
                     "params": tr.get("params", ""), "outcome": tr.get("outcome", ""),
                     "summary": tr.get("summary", "")}
            entries.append(entry)
    seen, deduped = set(), []
    for entry in entries:
        key = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    if deduped:
        out_path = os.path.join(output_dir, "tool_ledger.jsonl")
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in deduped)
        summary["errors"].extend(write_text(out_path, text))
        summary.setdefault("wrote", []).append("tool_ledger.jsonl")


def run_finalize(input_dir, output_dir, work_dir):
    summary = {"ok": True, "errors": [], "degradations": [], "wrote": []}
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    state, _, _ = load_json(os.path.join(work_dir, "run_state.json"))
    if not isinstance(state, dict):
        state = {}
    contract = state.get("contract") or {}
    case_id = state.get("case_id") or ""
    started_at = state.get("started_at")

    result_path = os.path.join(output_dir, "result.json")
    result, errs, degs = load_json(result_path)
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    if not isinstance(result, dict):
        result = new_skeleton(case_id, contract, note="finalize 时 result.json 缺失，重建骨架")
        summary["errors"].append("result.json 缺失或不可解析，已重建骨架")

    for field in contract:
        result.setdefault(field, None)

    cards, cerrs, cdegs = read_jsonl(os.path.join(work_dir, "evidence_cards.jsonl"))
    summary["errors"].extend(cerrs)
    summary["degradations"].extend(cdegs)
    cards, nerrs, ndegs = normalize_evidence_cards(cards)
    summary["errors"].extend(nerrs)
    summary["degradations"].extend(ndegs)

    matrix = result.setdefault("evidence_matrix", [])
    existing_ids = {row.get("id") for row in matrix if isinstance(row, dict)}
    for card in cards:
        if card.get("id") in existing_ids:
            continue
        matrix.append(card)
        existing_ids.add(card.get("id"))

    sources = result.setdefault("sources", [])
    for card in cards:
        src = card.get("source")
        if isinstance(src, str) and src and src not in sources:
            sources.append(src)

    if isinstance(started_at, (int, float)):
        result["runtime_seconds"] = round(max(0.0, time.time() - started_at), 1)
    else:
        result.setdefault("runtime_seconds", 0.0)

    notes = {}
    for name in ("decomposition.md", "search_plan.md", "synthesis.md"):
        text, _, _ = read_text(os.path.join(work_dir, name))
        notes[name] = text or ""

    summary["errors"].extend(write_json(result_path, result))
    summary["wrote"].append("result.json")

    emap = {
        "case_id": result.get("case_id", ""),
        "question": (result.get("research_product") or {}).get("question", ""),
        "evidence": matrix,
        "conclusions": (result.get("research_product") or {}).get("conclusions", []),
        "uncertainty": result.get("uncertainty", {}),
    }
    summary["errors"].extend(write_json(os.path.join(output_dir, "evidence_map.json"), emap))
    summary["wrote"].append("evidence_map.json")

    summary["errors"].extend(write_text(os.path.join(output_dir, "report.md"),
                                        _build_report(result, notes)))
    summary["wrote"].append("report.md")

    for name, text in notes.items():
        if text:
            summary["errors"].extend(write_text(os.path.join(output_dir, name), text))
            summary["wrote"].append(name)

    _build_tool_ledger(work_dir, output_dir, cards, summary)
    summary["ok"] = not summary["errors"]
    return summary


# --------------------------------------------------------------------------
# 六点覆盖自检
# --------------------------------------------------------------------------

def _is_boilerplate(gaps, pending):
    texts = [str(x).strip() for x in list(gaps) + list(pending)]
    if not texts:
        return True
    return all(len(t) < 8 or t in _BOILERPLATE for t in texts)


def run_selfcheck(input_dir, output_dir, work_dir):
    report = {"ok": True, "checks": [], "errors": [], "summary": ""}

    def check(name, passed, detail):
        report["checks"].append({"check": name, "passed": bool(passed), "detail": detail})
        if not passed:
            report["ok"] = False

    result, errs, degs = load_json(os.path.join(output_dir, "result.json"))
    report["errors"].extend(errs)
    if not isinstance(result, dict):
        report["ok"] = False
        report["summary"] = "result.json 不可解析"
        return report
    check("result_json_parses", True, "result.json 为合法 JSON 对象")

    state, _, _ = load_json(os.path.join(work_dir, "run_state.json"))
    contract = (state.get("contract") if isinstance(state, dict) else {}) or {}
    missing = [f for f in contract if f not in result]
    check("output_contract_fields", not missing,
          "缺失契约字段：%s" % (", ".join(missing) if missing else "无"))

    check("case_id_present", "case_id" in result, "case_id 字段存在")

    dec, _, _ = read_text(os.path.join(work_dir, "decomposition.md"))
    check("decomposition", bool(dec) and len(re.findall(r"SQ\d+", dec)) >= 2,
          "decomposition.md 含 ≥2 个可检索子问题")

    sp, _, _ = read_text(os.path.join(work_dir, "search_plan.md"))
    has_order = any(k in sp for k in ("顺序", "阶段", "先查", "前置"))
    has_stop = any(k in sp for k in ("停止", "饱和", "截止", "缺口"))
    check("search_plan", bool(sp) and has_order and has_stop,
          "search_plan.md 写清顺序与停止条件")

    matrix = result.get("evidence_matrix") or []
    rp = result.get("research_product") or {}
    conclusions = rp.get("conclusions") or []
    ids = {ev.get("id") for ev in matrix if isinstance(ev, dict)}
    bad = []
    for con in conclusions:
        for eid in con.get("evidence_ids") or []:
            if eid not in ids:
                bad.append("%s→%s" % (con.get("id"), eid))
    check("evidence_backlink", not bad,
          "结论 evidence_ids 命中证据矩阵（%s）" % ("缺 %s" % ",".join(bad) if bad else "全部命中"))

    loc_ok = bool(matrix) and all(
        isinstance(ev, dict) and ev.get("locator") and ev.get("quote") for ev in matrix)
    check("evidence_locator_quote", loc_ok, "evidence_matrix 每行含 locator+quote")

    conflicts = (result.get("uncertainty") or {}).get("conflicts") or []
    conf_ok = all(isinstance(c, dict) and len(c.get("sources") or []) >= 2
                  and (c.get("resolution") or c.get("status")) for c in conflicts)
    check("conflicts", conf_ok, "冲突逐条并列 ≥2 个来源与解决说明")

    check("conclusions", bool(conclusions) and bool(rp.get("question")),
          "conclusions 非空且 question 非空")

    gaps = (result.get("uncertainty") or {}).get("gaps") or []
    pending = result.get("pending_human") or []
    check("gaps_non_boilerplate", not _is_boilerplate(gaps, pending),
          "研究空白/待人工为具体缺口而非套话")

    status = result.get("status")
    check("status_valid", status in VALID_STATUSES, "status 取值合法（%s）" % status)
    if status == STATUS_INSUFFICIENT:
        check("insufficient_honest", bool(pending) or bool(result.get("limitations")),
              "材料不足时已写明缺口或限制")

    passed = sum(1 for c in report["checks"] if c["passed"])
    report["summary"] = "%d/%d 项通过" % (passed, len(report["checks"]))
    return report
