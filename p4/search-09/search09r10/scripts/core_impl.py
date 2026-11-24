#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence-index 数据面。

职责（确定性、只依赖标准库）：
  1. 发现 /input 下的 case.json、task.md 与材料文件，不假设固定文件名；
  2. 从 case.json 定位 output_contract（任意命名/嵌套），原样回填其全部字段；
  3. 对可抽取文本建立稳定段落证据索引（中间表示），供 verify/grade 复用；
  4. 原子写盘，保证中途异常不留下半截 JSON。

本模块不含任何评分规则；判定词表、分级阈值与引文核验见 rubric.py。
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 2 * 1024 * 1024            # 2MB，超限跳过并记录
MAX_PARAGRAPH_CHARS = 8000                  # 超长段落再按换行拆分
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(
    r"^(output_?contract|output_?schema|contract|result_?schema)$", re.I
)
META_KEYS = {
    "type", "title", "description", "$schema", "$ref", "required",
    "additionalProperties", "definitions", "$defs", "examples", "example",
}
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml",
    ".html", ".htm", ".tex", ".bib", ".rst", ".yaml", ".yml",
}
MATERIAL_SUFFIXES = TEXT_SUFFIXES | {".pdf"}

# result.json 中除契约字段外由本 Skill 声明的结构字段（用于碰撞检测与合并语义）
LIST_FIELDS = {
    "assessment", "candidates", "screening", "evidence_records", "omissions",
    "sources", "uncertainty", "change_log", "errors", "degradations",
    "pending_human", "limitations",
}
SCALAR_FIELDS = {"case_id", "status", "grade", "verification", "runtime"}


# ---------------------------------------------------------------------------
# 基础 I/O（容错，绝不抛异常到上层）
# ---------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以多编码降级读取文本；超大/缺失/坏编码返回 None 并携带错误列表。"""
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat_failed: %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["oversize(%d>%d): %s" % (size, max_bytes, p)]
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            raw = p.read_bytes().decode(enc)
            break
        except (UnicodeDecodeError, OSError) as exc:
            errors.append("decode(%s)_failed: %s: %s" % (enc, p, exc))
    if raw is None:
        return None, errors
    return raw, errors


def load_json_lenient(path):
    """读 JSON；坏 JSON 返回 None 并记录错误，不抛异常。"""
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def atomic_write_json(path, data):
    """先写临时文件再 rename，避免留下半截 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))


def atomic_write_text(path, text):
    """原子写入 UTF-8 文本。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------


def discover_input(input_dir):
    """扫描输入目录，返回分类文件清单；不假设固定文件名。"""
    root = Path(input_dir)
    found = {"case_json": [], "task_md": [], "materials": [], "other": []}
    if not root.is_dir():
        return found, ["input_dir_missing: %s" % root]
    try:
        entries = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError as exc:
        return found, ["input_dir_read_failed: %s: %s" % (root, exc)]
    for p in entries:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"].append(p)
        elif name == "task.md":
            found["task_md"].append(p)
        else:
            try:
                rel_parts = p.relative_to(root).parts[:-1]
            except ValueError:
                rel_parts = ()
            in_materials = any(part.lower() == "materials" for part in rel_parts)
            suffix = p.suffix.lower()
            if in_materials or suffix in MATERIAL_SUFFIXES or "material" in name:
                found["materials"].append(p)
            else:
                found["other"].append(p)
    return found, []


def _rel_path(p, root):
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


# ---------------------------------------------------------------------------
# 契约定位与回填
# ---------------------------------------------------------------------------


def find_contract(obj, depth=0):
    """按键名递归查找 output_contract（大小写不敏感、任意嵌套）。"""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    return None


def resolve_contract(data):
    """优先按键名找契约；找不到则识别 JSON Schema 风格对象作为契约。"""
    hit = find_contract(data)
    if hit is not None:
        return hit
    if isinstance(data, dict) and isinstance(data.get("properties"), dict):
        return data
    return None


def _default_for(value):
    """按契约字段类型描述给出默认值；无法判断则 None。"""
    if isinstance(value, dict):
        typ = str(value.get("type", "")).lower()
        if "array" in typ:
            return []
        if "object" in typ:
            return {}
        if "null" in typ:
            return None
        return None
    if isinstance(value, list):
        return []
    return None


def contract_defaults(contract):
    """把契约字段原样回填为顶层默认值；不改名、不删未知键。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            return {str(k): _default_for(v) for k, v in props.items()}
        return {
            str(k): _default_for(v)
            for k, v in contract.items()
            if str(k) not in META_KEYS
        }
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    return {}


def contract_field_names(contract):
    """返回契约声明的字段名（用于 validate 校验完整性）。"""
    return set(contract_defaults(contract).keys())


def extract_case_id(data, task_text, input_dir):
    """从 case.json 取 case_id，缺失则退回 task.md/目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    if task_text:
        m = re.search(
            r"(?:challenge_id|case_id|caseId)\s*[:：]\s*[`'\"\[]?([A-Za-z0-9._-]+)",
            task_text,
        )
        if m:
            return m.group(1)
    name = Path(input_dir).name.strip()
    return name if name else "unknown"


# ---------------------------------------------------------------------------
# 证据索引（中间表示）
# ---------------------------------------------------------------------------


def _paragraphs_of(text):
    """把文本切成稳定段落：先按空行，再对超长块按换行拆分。"""
    blocks = re.split(r"\n\s*\n", text)
    paras = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= MAX_PARAGRAPH_CHARS:
            paras.append(block)
        else:
            for line in block.split("\n"):
                line = line.strip()
                if line:
                    paras.append(line)
    return paras


def extract_pdf_text(path):
    """尝试调用 pdftotext 抽取文本；不可用/失败时返回降级信息而非崩溃。"""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            timeout=90,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, ["pdftotext_unavailable: %s: %s" % (path, exc)]
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout.decode("utf-8", "replace"), []
    return None, ["pdftotext_failed: %s (rc=%s)" % (path, proc.returncode)]


def _material_text(p, root):
    """返回 (text, note_list, degradation_list)。PDF 走 pdftotext，其余直接读。"""
    if p.suffix.lower() == ".pdf":
        text, degs = extract_pdf_text(p)
        if text is None:
            return None, ["pdf_text_not_extracted"], degs
        return text, [], degs
    text, errors = read_text(p)
    if text is None:
        return None, errors, []
    return text, [], []


def build_index(materials, input_dir):
    """对材料建立段落索引：稳定 ID `rel_path#pN`，lookup 供核验复用。"""
    root = Path(input_dir)
    files = []
    lookup = {}
    errors = []
    degradations = []
    for p in materials:
        rel = _rel_path(p, root)
        kind = p.suffix.lower().lstrip(".") or "file"
        text, notes, degs = _material_text(p, root)
        degradations.extend(degs)
        if text is None:
            files.append({
                "rel_path": rel, "path": str(p), "kind": kind,
                "chars": None, "paragraphs": [],
                "note": "; ".join(notes),
            })
            if "pdf_text_not_extracted" not in "; ".join(notes):
                errors.append("unreadable: %s: %s" % (rel, "; ".join(notes)))
            continue
        paras = _paragraphs_of(text)
        para_records = []
        for i, para in enumerate(paras):
            pid = "%s#p%d" % (rel, i)
            lookup[pid] = {"rel_path": rel, "paragraph_index": i, "text": para}
            para_records.append({"id": pid, "start_char": None, "text": para})
        files.append({
            "rel_path": rel, "path": str(p), "kind": kind,
            "chars": len(text), "paragraphs": para_records, "note": "ok",
        })
    return {
        "version": 1,
        "input_dir": str(root),
        "files": files,
        "lookup": lookup,
    }, errors, degradations


# ---------------------------------------------------------------------------
# 结果骨架
# ---------------------------------------------------------------------------


def build_skeleton(case_id, contract, notes=None):
    """构造一份合法 partial 骨架；契约字段原样回填。"""
    now = time.time()
    result = {"case_id": case_id, "status": "partial"}
    result.update(contract_defaults(contract))
    result.update({
        "assessment": [],
        "candidates": [],
        "screening": [],
        "evidence_records": [],
        "omissions": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "grade": {"overall": "not_graded", "dimensions": {}, "rationale": ""},
        "errors": list(notes or []),
        "degradations": [],
        "pending_human": [],
        "limitations": [],
        "verification": {"status": "not_run", "summary": None},
        "runtime": {"start_epoch": now, "skeleton_epoch": now, "finish_epoch": None},
    })
    # 保证结构字段不被契约同名字段覆盖
    result["case_id"] = case_id
    result["status"] = "partial"
    return result


def ensure_structure(result, case_id):
    """补齐缺失的结构字段，保证 result.json 始终可解析、结构完整。"""
    result = dict(result) if isinstance(result, dict) else {"case_id": case_id}
    result.setdefault("case_id", case_id)
    result.setdefault("status", "partial")
    for key in LIST_FIELDS:
        if key not in result or not isinstance(result[key], list):
            result[key] = []
    result.setdefault("grade", {"overall": "not_graded", "dimensions": {}, "rationale": ""})
    result.setdefault("runtime", {"start_epoch": None, "skeleton_epoch": None, "finish_epoch": None})
    result.setdefault("verification", {"status": "not_run", "summary": None})
    return result


def load_result(output_dir, case_id=None):
    """读取 result.json；缺失/坏 JSON 返回 None 与错误，不抛异常。"""
    path = Path(output_dir) / "result.json"
    data, errors = load_json_lenient(path)
    if data is None:
        return None, errors
    return ensure_structure(data, case_id or data.get("case_id", "unknown")), errors


def append_change_log(result, event, detail):
    """追加变更记录（确定性、时间戳单调）。"""
    entry = {
        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "detail": detail,
    }
    logs = result.setdefault("change_log", [])
    if not isinstance(logs, list):
        logs = []
        result["change_log"] = logs
    logs.append(entry)
    return result


if __name__ == "__main__":
    # 冒烟测试：模块可独立加载并构造合法骨架
    sk = build_skeleton("smoke", None, notes=["smoke"])
    assert sk["status"] == "partial" and sk["case_id"] == "smoke"
    assert sk["assessment"] == [] and isinstance(sk["change_log"], list)
    print("core_impl smoke ok")
