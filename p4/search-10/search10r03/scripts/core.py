#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — 纯标准库的跨语言文献检索 Skill 支撑库。

职责：
  - 输入发现：不假设固定文件名 / 键名，扫描 /input 目录；
  - 契约提取：从 case.json 或 task.md 内联 JSON 中定位 output_contract；
  - 骨架构建、增量 patch（深合并）、原子落盘；
  - 确定性序列化与内容指纹：同一输入 -> 同一语义产物；
  - 校验与状态摘要。

运行时默认路径（可用 --input/--output/--work 覆盖）：
  /input -> 输入目录；/app/output -> 产物目录；/app/work -> 工作目录

所有函数只依赖标准库，无随机性、无时间依赖的排序（时间戳仅写入 volatile 字段）。
"""

import hashlib
import json
import os
import re
import time

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_TEXT_BYTES = 2 * 1024 * 1024
# 以下顶层字段属于“运行元数据”，不参与确定性内容指纹：
#   runtime 是耗时、change_log 含时间戳，二者不决定语义结果。
VOLATILE_KEYS = ("runtime", "determinism_fingerprint", "change_log")

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 标准语义字段（若 case 提供 output_contract，则以 contract 为准，此处仅兜底）。
STANDARD_FIELDS = {
    "research_question": None,
    "source_languages": [],
    "target_languages": [],
    "terminology_table": [],
    "queries": [],
    "results": [],
    "evidence_map": [],
    "dedup": [],
    "missing": [],
    "conflicts": [],
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
}

# 无论 contract 如何都必须存在的结果字段。
MANDATORY_FIELDS = ("case_id", "status", "sources", "uncertainty", "change_log")


# --------------------------------------------------------------------------
# 文本 / JSON 读取（容错，不崩溃）
# --------------------------------------------------------------------------


def utc_iso():
    """返回 UTC ISO8601 时间戳（仅用于 volatile 字段）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """读取文本文件，返回 (text, encoding)。

    依序尝试 utf-8-sig、utf-8、gb18030，最后以 utf-8 + replace 兜底（永不抛错）。
    顺序固定，保证同一文件重复读取结果一致。
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def safe_json_load(path):
    """读取并解析 JSON，返回 (obj_or_None, error_or_None)。"""
    try:
        text, _enc = read_text(path)
    except OSError as exc:
        return None, "read_error:%s" % exc
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad_json:%s" % exc


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------


def discover_inputs(input_dir):
    """返回 {'case': path|None, 'task': path|None, 'notes': [...], 'materials': [...]}。"""
    found = {"case": None, "task": None, "notes": [], "materials": []}
    if not os.path.isdir(input_dir):
        return found
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return found
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            continue
        low = name.lower()
        if low == "case.json" or low.endswith(".case.json"):
            if found["case"] is None:
                found["case"] = full
        elif low == "task.md" or (low.endswith(".md") and "task" in low):
            if found["task"] is None:
                found["task"] = full
        elif low.endswith(".md"):
            found["notes"].append(full)
    materials = os.path.join(input_dir, "materials")
    if os.path.isdir(materials):
        for n in sorted(os.listdir(materials)):
            full = os.path.join(materials, n)
            if os.path.isfile(full):
                found["materials"].append(full)
    return found


def load_inputs(input_dir):
    """返回 (found, case_dict, task_text, errors, degradations)。

    case 损坏时 case_dict 为空 dict；超 MAX_TEXT_BYTES 的材料/笔记跳过并记入 degradations。
    """
    found = discover_inputs(input_dir)
    case = {}
    errors = []
    degradations = []

    # 超大文件跳过并记录（避免内存/解析崩溃）
    for key in ("materials", "notes"):
        kept = []
        for path in found[key]:
            try:
                size = os.path.getsize(path)
            except OSError:
                degradations.append("size_read_error:%s" % os.path.basename(path))
                continue
            if size > MAX_TEXT_BYTES:
                degradations.append("oversized_file_skipped:%s:%dB" % (os.path.basename(path), size))
            else:
                kept.append(path)
        found[key] = kept

    if found["case"]:
        parsed, err = safe_json_load(found["case"])
        if parsed is None:
            errors.append("case.json:%s" % err)
        elif isinstance(parsed, dict):
            case = parsed
        else:
            errors.append("case.json:not_a_dict")
    else:
        errors.append("case.json_not_found")
    task_text = ""
    if found["task"]:
        try:
            task_text, _enc = read_text(found["task"])
        except OSError as exc:
            errors.append("task.md_read_error:%s" % exc)
    return found, case, task_text, errors, degradations


# --------------------------------------------------------------------------
# 契约提取
# --------------------------------------------------------------------------


def find_contract(obj, _depth=0):
    """在 JSON 对象中递归定位 output_contract / outputContract。"""
    if isinstance(obj, dict):
        for key in ("output_contract", "outputContract", "output_contract_fields"):
            if key in obj and isinstance(obj[key], (dict, list)):
                return obj[key]
        for value in obj.values():
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list) and _depth < 4:
        for value in obj:
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    return None


def _extract_balanced(text, start):
    """从 index `start` 提取配对的 {…} 或 […] 片段。"""
    depth = 0
    i = start
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def find_contract_from_text(text):
    """在自由文本（task.md）中定位 output_contract 后的内联 JSON 对象。"""
    if not text:
        return None
    for match in re.finditer(r"output[_]?contract", text, re.I):
        idx = match.end()
        while idx < len(text) and text[idx] in " \t\r\n:=":
            idx += 1
        if idx < len(text) and text[idx] in "{[":
            fragment = _extract_balanced(text, idx)
            if fragment:
                try:
                    return json.loads(fragment)
                except (ValueError, TypeError):
                    continue
    return None


def find_case_id(case, task_text=""):
    """优先取 case 显式字段，其次 task.md 文本，最后挑战 id。"""
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "case_identifier", "caseID"):
            if case.get(key) not in (None, "", []):
                return str(case[key])
    if task_text:
        m = re.search(r"(?i)case[\s_\-]*id[\s\"':=]+([A-Za-z0-9_\-\.]+)", task_text)
        if m:
            return m.group(1)
    if isinstance(case, dict):
        for key in ("challenge_id", "challengeId", "id"):
            if case.get(key) not in (None, "", []):
                return str(case[key])
    return "unknown_case"


def empty_value_for(name, spec=None):
    """按契约字段名/类型给出空值（list 倾向 []，否则 None）。"""
    list_hints = (
        "results", "queries", "terminology", "terms", "sources", "evidence",
        "missing", "conflicts", "items", "records", "dedup", "errors",
        "degradations", "pending", "uncertainty", "references", "candidates",
        "hits", "change_log",
    )
    if isinstance(spec, dict):
        if spec.get("type") in ("array", "list"):
            return []
        if "default" in spec:
            return spec["default"]
    if isinstance(spec, list):
        return []
    low = str(name).lower()
    if low in list_hints or low.endswith("s"):
        return []
    return None


# --------------------------------------------------------------------------
# 确定性序列化
# --------------------------------------------------------------------------


def canonicalize(obj):
    """递归排序 dict 键；同类 dict 列表按规范 JSON 排序（保证确定性）。

    纯数值/字符串列表保持原顺序（其顺序本身即语义）。
    """
    if isinstance(obj, dict):
        return {str(k): canonicalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, list):
        items = [canonicalize(v) for v in obj]
        if items and all(isinstance(v, dict) for v in items):
            items.sort(key=lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return items
    return obj


def content_fingerprint(obj, volatile_keys=VOLATILE_KEYS):
    """计算语义内容指纹（排除 runtime / change_log / 指纹本身）。"""
    semantic = {k: v for k, v in obj.items() if k not in volatile_keys} if isinstance(obj, dict) else obj
    canon = canonicalize(semantic)
    payload = json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 深合并与原子写
# --------------------------------------------------------------------------


def deep_merge(base, patch):
    """深合并：字典递归合并，列表整体替换，其余值替换。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return patch


def atomic_write_json(path, obj):
    """原子写 JSON：先写临时文件并 fsync，再 os.replace。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
    return path


def load_result(output_dir):
    """读取 result.json，返回 (obj_or_None, error_or_None)。"""
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None, "result.json_not_found"
    return safe_json_load(path)


# --------------------------------------------------------------------------
# 骨架构建与 init
# --------------------------------------------------------------------------


def build_skeleton(case, task_text, case_err_list, degradations=None):
    """构建合法 partial 结果骨架。返回 (skeleton, contract)。"""
    contract = find_contract(case)
    if contract is None:
        contract = find_contract_from_text(task_text)

    case_id = find_case_id(case, task_text)

    skeleton = {"case_id": case_id, "status": "partial"}

    # 回填契约字段（缺值用 []/None，不改名、不删键）
    if contract is not None:
        if isinstance(contract, dict):
            for key, spec in contract.items():
                skeleton[str(key)] = empty_value_for(str(key), spec)
        elif isinstance(contract, list):
            for key in contract:
                if isinstance(key, str):
                    skeleton[key] = empty_value_for(key, None)

    # 补标准语义字段（不与契约键冲突）
    for name, default in STANDARD_FIELDS.items():
        if name not in skeleton:
            skeleton[name] = default

    # 强制存在的结果字段
    for name in MANDATORY_FIELDS:
        if name not in skeleton:
            if name in ("sources", "uncertainty", "change_log"):
                skeleton[name] = []
            elif name == "status":
                skeleton[name] = "partial"
            else:
                skeleton[name] = case_id

    skeleton["errors"] = list(case_err_list or [])
    skeleton["degradations"] = list(degradations or [])

    # 缺失输入直接判 input_error，诚实降级
    if case_id == "unknown_case" and "case.json_not_found" in (case_err_list or []):
        skeleton["status"] = "input_error"
    elif case_id == "unknown_case":
        skeleton["status"] = "input_error"

    skeleton["change_log"] = [{
        "step": "init",
        "at": utc_iso(),
        "summary": "骨架落盘；case_id=%s；status=%s；contract=%s"
                   % (case_id, skeleton["status"], "found" if contract is not None else "none"),
    }]
    skeleton["runtime"] = None
    skeleton["determinism_fingerprint"] = content_fingerprint(skeleton)
    return skeleton, contract


def do_init(input_dir, output_dir, work_dir):
    """init：发现输入、解析契约、原子写入骨架。返回 (result, errors)。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    found, case, task_text, errs, degradations = load_inputs(input_dir)
    skeleton, contract = build_skeleton(case, task_text, errs, degradations)

    atomic_write_json(os.path.join(output_dir, "result.json"), skeleton)

    state = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "started_at": utc_iso(),
        "started_epoch": time.time(),
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract": contract,
        "materials": found["materials"],
        "notes": found["notes"],
        "steps": [],
    }
    atomic_write_json(os.path.join(work_dir, "state.json"), state)
    return skeleton, errs


# --------------------------------------------------------------------------
# patch / finalize / validate / status
# --------------------------------------------------------------------------


def do_patch(output_dir, work_dir, patch_obj):
    """深合并 patch 到 result.json，重算指纹，原子落盘。返回 (result, error)。"""
    result, err = load_result(output_dir)
    if result is None:
        return None, err
    if not isinstance(patch_obj, dict):
        return result, "patch_not_a_dict"
    merged = deep_merge(result, patch_obj)
    merged["determinism_fingerprint"] = content_fingerprint(merged)
    atomic_write_json(os.path.join(output_dir, "result.json"), merged)
    return merged, None


def do_finalize(input_dir, output_dir, work_dir):
    """finalize：重算指纹、记录 runtime、追加 change_log，原子落盘。"""
    result, err = load_result(output_dir)
    if result is None:
        return None, err
    state_path = os.path.join(work_dir, "state.json")
    started_epoch = None
    if os.path.isfile(state_path):
        state, _serr = safe_json_load(state_path)
        if isinstance(state, dict):
            started_epoch = state.get("started_epoch")
    runtime = None
    if started_epoch is not None:
        runtime = max(0, round(time.time() - started_epoch, 2))
    result["runtime"] = runtime
    result["determinism_fingerprint"] = content_fingerprint(result)
    log = list(result.get("change_log") or [])
    log.append({"step": "finalize", "at": utc_iso(),
                "summary": "收尾自检完成；status=%s；runtime=%.2fs" % (result.get("status"), runtime or 0.0)})
    result["change_log"] = log
    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    return result, None


def do_validate(input_dir, output_dir):
    """校验 result.json 合法性、契约字段与状态。返回 issues 列表（空为通过）。"""
    issues = []
    result, err = load_result(output_dir)
    if result is None:
        return ["result.json_unreadable:%s" % err]

    for name in MANDATORY_FIELDS:
        if name not in result:
            issues.append("missing_mandatory_field:%s" % name)

    if result.get("status") not in STATUS_VALUES:
        issues.append("invalid_status:%s" % result.get("status"))

    # 重新发现契约并核对字段
    contract = None
    if os.path.isdir(input_dir):
        _found, case, task_text, _errs, _degs = load_inputs(input_dir)
        contract = find_contract(case)
        if contract is None:
            contract = find_contract_from_text(task_text)
    if contract is not None:
        fields = list(contract.keys()) if isinstance(contract, dict) else [k for k in contract if isinstance(k, str)]
        for field in fields:
            if field not in result:
                issues.append("missing_contract_field:%s" % field)

    if result.get("determinism_fingerprint"):
        expected = content_fingerprint(result)
        if expected != result["determinism_fingerprint"]:
            issues.append("fingerprint_mismatch")

    return issues


def do_status(output_dir):
    """返回进度摘要字符串。"""
    result, err = load_result(output_dir)
    if result is None:
        return "no result.json: %s" % err
    lines = [
        "case_id=%s" % result.get("case_id"),
        "status=%s" % result.get("status"),
        "runtime=%s" % result.get("runtime"),
        "results=%d" % len(result.get("results") or []),
        "queries=%d" % len(result.get("queries") or []),
        "terminology=%d" % len(result.get("terminology_table") or []),
        "dedup=%d" % len(result.get("dedup") or []),
        "missing=%d" % len(result.get("missing") or []),
        "conflicts=%d" % len(result.get("conflicts") or []),
        "change_log=%d" % len(result.get("change_log") or []),
    ]
    return "; ".join(lines)
