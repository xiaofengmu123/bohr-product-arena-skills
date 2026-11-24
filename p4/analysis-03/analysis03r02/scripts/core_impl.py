#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-viz-checkpoint 核心实现（仅 Python 标准库）。

本模块是“先落盘、后改进”流水线的无副作用工具层：
  - 输入发现：不假设固定文件名 / 列名 / 键名；
  - 契约提取：把 output_contract 的所有叶值归一为 null / [] 回填模板；
  - 预算台账：把 900s / 64 次请求拆成阶段预算，每次提交记录消耗；
  - 检查点：init 立即写 result.json 骨架与 work/plan.md，任何异常都留合法产物；
  - 校验：check 核对结构、契约回填、预算记录与 sources 证据结构。

设计约束：所有公开函数尽力不抛异常；写盘失败时由调用方降级为最小结果并记录错误。
"""
import datetime
import hashlib
import json
import os
import re
import tempfile

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
MAX_FILE_BYTES = 2 * 1024 * 1024            # 单文本文件读取上限
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log",
                 "errors", "degradations", "runtime", "artifacts", "budget")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")

PHASE_PLAN = (
    {"id": "A", "name": "骨架落盘", "window": "0-3 分钟", "time_sec": 180,
     "requests": 4, "gate": "result.json(partial) 与 work/plan.md 已写盘"},
    {"id": "B", "name": "主分析", "window": "3-12 分钟", "time_sec": 540,
     "requests": 45, "gate": "清洗/统计/选图/编码/标注/导出逐项落盘"},
    {"id": "C", "name": "收尾自检", "window": "12-15 分钟", "time_sec": 180,
     "requests": 10, "gate": "check 通过且 status/runtime 已更新"},
)
RESERVE_REQUESTS = 5
TOTAL_SECONDS = 900
TOTAL_REQUESTS = 64

# output_contract 可能的键名（归一化后比对）
CONTRACT_CANDIDATE_KEYS = (
    "outputcontract", "resultcontract", "contract", "resultschema", "expectedoutput",
)


# --------------------------------------------------------------------------- #
# 时间与内容指纹
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def elapsed_since(start_iso):
    """返回自 start_iso 起的秒数；无法解析时返回 None。"""
    if not start_iso:
        return None
    try:
        start = datetime.datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
        delta = datetime.datetime.now(datetime.timezone.utc) - start
        return max(0, int(delta.total_seconds()))
    except (ValueError, TypeError):
        return None


def content_fingerprint(data, length=12):
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(data, str):
        data = str(data)
    return hashlib.sha256(data.encode("utf-8", "replace")).hexdigest()[:length]


# --------------------------------------------------------------------------- #
# 安全读写
# --------------------------------------------------------------------------- #
def read_text_safe(path, limit=MAX_FILE_BYTES):
    """utf-8-sig -> utf-8 -> gb18030 -> latin-1 逐级降级；返回 (text, encoding, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", None, "unreadable: %s" % exc
    if size > limit:
        return "", None, "file_too_large(%d > %d)" % (size, limit)
    last_error = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as exc:
            last_error = "%s: %s" % (enc, exc)
        except OSError as exc:
            return "", None, "unreadable: %s" % exc
    return "", None, "undecodable: " + last_error


def write_text_atomic(path, text):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tmp_",
                               suffix=os.path.splitext(path)[1] or ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_json_atomic(path, obj):
    return write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def load_json_safe(path):
    """读取 JSON；坏 JSON 不抛异常，返回 (obj, error)。"""
    text, _enc, err = read_text_safe(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return {}, "bad_json: %s" % exc


# --------------------------------------------------------------------------- #
# 参数解析与对象操作
# --------------------------------------------------------------------------- #
def parse_scalar(raw):
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def parse_assign(raw):
    """解析 'status="ok"' 或 '{"a":1}'；无法解析时放入 __raw__ 键。"""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"__raw__": raw}
    if "=" in raw:
        key, val = raw.split("=", 1)
        return {key.strip(): parse_scalar(val)}
    return {"__raw__": raw}


def merge_deep(base, extra):
    for key, val in extra.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            merge_deep(base[key], val)
        else:
            base[key] = val
    return base


def set_dotted(obj, dotted, value):
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return obj


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    """扫描输入目录，返回 (found, degradations)。不假设固定文件名。"""
    found = {"case_json": None, "task_md": None, "data": [], "materials": []}
    degradations = []
    if not os.path.isdir(input_dir):
        return found, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            low = name.lower()
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                degradations.append("unreadable: %s (%s)" % (rel, exc))
                continue
            if low == "case.json":
                found["case_json"] = full
            elif low == "task.md":
                found["task_md"] = full
            elif low.endswith(DATA_EXTS):
                found["data"].append(full)
            elif low.endswith(TEXT_EXTS):
                found["materials"].append(full)
            if size > MAX_FILE_BYTES:
                degradations.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
    return found, degradations


# --------------------------------------------------------------------------- #
# 契约提取
# --------------------------------------------------------------------------- #
def _normalize_key(key):
    return re.sub(r"[^a-z0-9]", "", (key or "").lower())


def find_contract_in(obj, path=""):
    """递归查找含 contract/schema 语义的字段，返回 (value, path)；(None, '') 表示未找到。"""
    if isinstance(obj, dict):
        for key, val in obj.items():
            cur = (path + "." + key) if path else key
            if _normalize_key(key) in CONTRACT_CANDIDATE_KEYS and isinstance(val, dict):
                return val, cur
            hit, hit_path = find_contract_in(val, cur)
            if hit is not None:
                return hit, hit_path
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            hit, hit_path = find_contract_in(val, "%s[%d]" % (path, idx))
            if hit is not None:
                return hit, hit_path
    return None, ""


def extract_contract_from_text(text):
    """从 task.md 文本中保守提取契约：仅接受含契约键的 JSON 代码块。"""
    if not text:
        return None, ""
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL):
        block = match.group(1).strip()
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        contract, path = find_contract_in(obj)
        if contract is not None:
            return contract, "task.md#json-block"
    return None, ""


def blank_template(value):
    """把契约值归一为回填模板：dict 递归保留键，叶值 -> None；list -> []。"""
    if isinstance(value, dict):
        return {k: blank_template(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def extract_contract(case_obj):
    """从 case.json 提取契约与显式 case_id，返回 (contract, contract_path, case_id)。"""
    contract, path = find_contract_in(case_obj)
    case_id = None
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id"):
            if case_obj.get(key):
                case_id = str(case_obj[key])
                break
    return contract, path, case_id


def case_context(input_dir):
    """读取 case.json/task.md 并汇总为上下文，返回 dict。任何失败都写进 degradations。"""
    found, degradations = discover_inputs(input_dir)
    case_obj, case_err = {}, None
    if found["case_json"]:
        case_obj, case_err = load_json_safe(found["case_json"])
        if case_err:
            degradations.append("case.json: %s" % case_err)
    else:
        degradations.append("case_json_missing")

    task_text, _enc, task_err = "", None, None
    if found["task_md"]:
        task_text, _enc, task_err = read_text_safe(found["task_md"])
        if task_err:
            degradations.append("task.md: %s" % task_err)
    else:
        degradations.append("task_md_missing")

    contract, contract_path, explicit_id = extract_contract(case_obj)
    if contract is None:
        contract, fallback_path = extract_contract_from_text(task_text)
        if contract is not None:
            contract_path = fallback_path
    case_id = explicit_id or ("%s-%s" % (
        os.path.basename(os.path.abspath(input_dir)), content_fingerprint(case_obj)))
    return {
        "case_obj": case_obj,
        "task_md": task_text,
        "contract": contract,
        "contract_path": contract_path,
        "case_id": case_id,
        "found": found,
        "degradations": degradations,
    }


# --------------------------------------------------------------------------- #
# 预算台账
# --------------------------------------------------------------------------- #
def new_budget():
    return {
        "total_seconds": TOTAL_SECONDS,
        "total_requests": TOTAL_REQUESTS,
        "reserve_requests": RESERVE_REQUESTS,
        "started_at": None,
        "updated_at": None,
        "elapsed_seconds": None,
        "phases": [
            {"id": p["id"], "name": p["name"], "window": p["window"],
             "time_sec": p["time_sec"], "requests": p["requests"],
             "gate": p["gate"], "done": False, "recorded_at": None}
            for p in PHASE_PLAN
        ],
        "note": "阶段 A 必须在前 3 分钟内完成骨架落盘；越界则降级并记录 degradations。",
    }


def mark_phase_done(budget, phase_id, started_at):
    budget["updated_at"] = now_iso()
    if started_at:
        budget["elapsed_seconds"] = elapsed_since(started_at)
    for phase in budget.get("phases", []):
        if phase["id"] == phase_id:
            phase["done"] = True
            phase["recorded_at"] = now_iso()
    return budget


# --------------------------------------------------------------------------- #
# 结果骨架与校验
# --------------------------------------------------------------------------- #
def build_skeleton(case_id, contract, ts=None):
    ts = ts or now_iso()
    obj = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"ts": ts, "seq": 0, "step": "init", "phase": "A",
             "action": "写入 result.json 骨架(partial) 与 work/plan.md",
             "files": ["/app/output/result.json", "/app/work/plan.md"]}
        ],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": ts, "finished_at": None, "seconds": None},
        "artifacts": [],
        "budget": new_budget(),
    }
    obj["budget"]["started_at"] = ts
    if isinstance(contract, dict):
        for key, val in contract.items():
            if key in RESERVED_KEYS:
                obj.setdefault("uncertainty", []).append(
                    {"item": "reserved_key_conflict", "key": key,
                     "reason": "契约字段与保留键同名，保留键语义优先",
                     "pending_human": True})
                continue
            obj[key] = blank_template(val)
    return obj


def validate(obj, require_contract=True, contract=None):
    """返回 (checks, failed)。checks 每项 {level, code, message}。"""
    checks = []
    failed = 0

    def add(level, code, message):
        nonlocal failed
        checks.append({"level": level, "code": code, "message": message})
        if level == "FAIL":
            failed += 1

    if not isinstance(obj, dict):
        add("FAIL", "not_object", "result.json 顶层必须是对象")
        return checks, failed
    if not obj.get("case_id"):
        add("FAIL", "missing_case_id", "缺少 case_id")
    if obj.get("status") not in STATUS_VALUES:
        add("FAIL", "bad_status", "status 必须是 %s" % list(STATUS_VALUES))
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations", "artifacts", "budget"):
        if key not in obj:
            add("WARN", "missing_%s" % key, "缺少保留键 %s" % key)
    if require_contract and isinstance(contract, dict):
        for key in contract:
            if key not in obj:
                add("FAIL", "missing_contract_%s" % key, "契约字段缺失: %s" % key)
    for idx, item in enumerate(obj.get("sources", [])):
        if not isinstance(item, dict) or not item.get("locator") or "quote" not in item:
            add("WARN", "bad_source_%d" % idx, "sources[%d] 缺少 locator/quote" % idx)
    budget = obj.get("budget")
    if isinstance(budget, dict):
        phases = budget.get("phases", [])
        done_a = any(isinstance(p, dict) and p.get("id") == "A" and p.get("done") for p in phases)
        if not done_a:
            add("WARN", "phase_a_not_marked", "阶段 A 未在 budget.phases 标记完成")
    return checks, failed
