#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 文献综述 Skill 的纯标准库实现核心。

只做确定性、可降级的文件 / JSON / 扫描 / 抽取工作，不含任何领域推断。
设计目标（鲁棒性变体）：空输入、坏 JSON、坏编码、缺参数、超大文件、缺软件
都不崩溃；同一输入重复运行产出内容一致（runtime 时间戳除外）。

本模块被 main.py 与 selftest.py 共同复用，禁止在这里做任何"补造数据"的判断。
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

# 默认单文件正文读取上限；超过则跳过正文，仅记录哈希与大小。
MAX_FILE_BYTES = 2 * 1024 * 1024
# 二进制探测读取的头部字节数。
PROBE_BYTES = 4096

PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".text"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".tsv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json", "output_contract.json", "challenge.json"}

# 未找到 output_contract 时的兜底字段集；只用于保住契约，不做领域假设。
DEFAULT_CONTRACT_FIELDS = {
    "research_question": None,
    "screening_criteria": None,
    "screening_log": [],
    "evidence_table": [],
    "themes": [],
    "review_draft": None,
    "conflicts_and_gaps": [],
    "references": [],
}

# 骨架占位标记：selftest 据此判断产物是否仍是骨架而非真实内容。
SKELETON_MARKER = "<!-- SKELETON -->"


def now_ts():
    """UTC 时间戳，用于 change_log / runtime；不参与内容确定性。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[lit-review] " + str(msg), flush=True)


# ---------------------------------------------------------------------------
# 文件读取（容错）
# ---------------------------------------------------------------------------

def read_bytes(path, max_bytes=None):
    """按上限读取整文件；超限返回 None + 错误说明，不读入内存。"""
    try:
        size = os.path.getsize(path)
        if max_bytes is not None and size > max_bytes:
            return None, "文件大小 {0} 字节超过上限 {1} 字节".format(size, max_bytes)
        with open(path, "rb") as f:
            return f.read(), None
    except Exception as e:
        return None, str(e)


def read_text(path, max_bytes=None):
    """容错读文本：先尺寸守卫，再依次尝试编码；绝不抛异常。

    返回 (text, encoding, error)。latin-1 能解码任意字节，故坏编码文件
    也能被读入（内容可能含乱码，但不会崩溃），由上层如实标注。
    """
    data, err = read_bytes(path, max_bytes)
    if data is None:
        return None, None, err or "未知读取错误"
    last = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError) as e:
            last = str(e)
    # 兜底：errors=replace 总能成功，但显式标注降级。
    return data.decode("utf-8", errors="replace"), "utf-8-replace", \
        "编码无法识别，已用替换字符降级: " + (last or "未知")


def salvage_json(text):
    """尽力修复常见坏 JSON：BOM、行注释、块注释、行尾逗号。失败则返回原文。"""
    if text is None:
        return None
    t = text.lstrip("\ufeff \t\r\n")
    t = re.sub(r"//[^\n]*", "", t)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.DOTALL)
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return t


def read_json(path, max_bytes=None):
    """读取 JSON；坏编码/坏 JSON 均降级并返回错误列表，不抛异常。"""
    data, err = read_bytes(path, max_bytes)
    if data is None:
        return None, ["无法读取: " + (err or "未知错误")]
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None, ["编码无法识别"]
    try:
        return json.loads(text), None
    except Exception as e0:
        salvage = salvage_json(text)
        if salvage and salvage != text:
            try:
                return json.loads(salvage), None
            except Exception as e1:
                return None, ["JSON 解析失败: " + str(e0), "修复后仍失败: " + str(e1)]
        return None, ["JSON 解析失败: " + str(e0)]


# ---------------------------------------------------------------------------
# 原子写盘（崩溃不留半截文件）
# ---------------------------------------------------------------------------

def write_text(path, text):
    """先写同目录临时文件再 os.replace，保证重复运行/中途崩溃不产生半截产物。"""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, path)
            return None
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
    except Exception as e:
        return str(e)


def write_json(path, data):
    return write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# 哈希与类型探测
# ---------------------------------------------------------------------------

def sha256_file(path, max_bytes=None):
    """流式 SHA-256；超限文件只哈希前 max_bytes 并标记 oversized。"""
    h = hashlib.sha256()
    try:
        size = os.path.getsize(path)
        oversized = max_bytes is not None and size > max_bytes
        with open(path, "rb") as f:
            if oversized:
                h.update(f.read(max_bytes))
            else:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        return h.hexdigest(), oversized
    except Exception:
        return None, False


def is_probably_binary(path):
    """用头部字节启发式判断二进制，避免把二进制当文本抽取成乱码。"""
    try:
        with open(path, "rb") as f:
            head = f.read(PROBE_BYTES)
        if not head:
            return False
        if b"\x00" in head:
            return True
        ctrl = sum(1 for b in head if b < 9 or (13 < b < 32))
        return (ctrl / len(head)) > 0.30
    except Exception:
        return True


def kind_of(ext):
    ext = ext.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in RECORD_EXTS:
        return "record"
    return "other"


# ---------------------------------------------------------------------------
# output_contract 归一化
# ---------------------------------------------------------------------------

def default_for_spec(spec):
    """由 schema/type 提示给数组字段 []、对象字段 {}，其余 null。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            tl = t.lower()
            if tl in ("array", "list"):
                return []
            if tl in ("object", "dict", "map"):
                return {}
            return None
        if isinstance(t, list):
            lows = {str(x).lower() for x in t}
            if "array" in lows or "list" in lows:
                return []
            if "object" in lows or "dict" in lows:
                return {}
        if "items" in spec:
            return []
        if "properties" in spec:
            return {}
        return None
    if isinstance(spec, str):
        sl = spec.strip().lower()
        if sl in ("array", "list"):
            return []
        if sl in ("object", "dict", "map"):
            return {}
        return None
    return None


def normalize_contract(v):
    """把多种形态的 output_contract 归一化为 [(字段名, 默认值), ...]。

    支持：{"properties": {...}}、{"fields": ...}、字段名->spec 映射、
    ["a","b"]、[{"name":...}]。键名排序保证确定性。返回 None 表示找不到。
    """
    if isinstance(v, dict):
        props = v.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in sorted(props)]
        if v.get("fields") is not None:
            return normalize_contract(v.get("fields"))
        return [(name, default_for_spec(v[name])) for name in sorted(v)]
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id") or item.get("id")
                      or item.get("key") or item.get("field"))
                if nm:
                    out.append((nm, default_for_spec(item)))
        return out
    if isinstance(v, str) and v.strip():
        return [(v, None)]
    return None


def find_key_recursive(node, names, _depth=0):
    """深度优先递归查找任一键名（忽略大小写）；找不到返回 None。"""
    if _depth > 12 or node is None:
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in names:
                return v
        for v in node.values():
            r = find_key_recursive(v, names, _depth + 1)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_key_recursive(item, names, _depth + 1)
            if r is not None:
                return r
    return None


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if isinstance(case, dict):
        for k in ("research_question", "question", "query", "prompt", "title", "objective"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        t = case.get("task")
        if isinstance(t, dict):
            for k in ("research_question", "question", "prompt", "description", "title", "objective"):
                v = t.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """扫描 /input，返回 (case_path, task_path, materials)。确定性排序。"""
    case_path, task_path = None, None
    materials = []
    if not os.path.isdir(input_dir):
        return case_path, task_path, materials
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            low = fn.lower()
            if low == "case.json":
                if case_path is None:
                    case_path = p
            elif low == "task.md":
                if task_path is None:
                    task_path = p
            elif low in META_NAMES:
                continue
            else:
                ext = os.path.splitext(low)[1]
                if ext.lower() in MATERIAL_EXTS:
                    materials.append(p)
    return case_path, task_path, materials


# ---------------------------------------------------------------------------
# PDF 抽取
# ---------------------------------------------------------------------------

def pdf_extract(pdf_path, out_path):
    """调用 pdftotext 抽取；成功返回 (True, None)，失败返回 (False, err)。"""
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", "-layout", pdf_path, out_path],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0 and os.path.isfile(out_path):
            return True, None
        return False, "pdftotext 返回码 {0}: {1}".format(
            proc.returncode, (proc.stderr or "").strip()[:400])
    except FileNotFoundError:
        return False, "未找到 pdftotext 可执行文件"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 材料盘点 / 去重 / 抽取
# ---------------------------------------------------------------------------

def scan_materials(materials, work_dir, max_file_bytes):
    """扫描材料：算哈希、判类型、去重、抽取文本。返回 (inventory, dup_groups, degradations)。"""
    inventory = []
    dup_groups = []
    degradations = []
    seen = {}
    extracted_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)
    for i, p in enumerate(sorted(materials)):
        entry = {
            "source_id": "S{0:03d}".format(i + 1),
            "path": p,
            "basename": os.path.basename(p),
            "size_bytes": None,
            "sha256": None,
            "oversized": False,
            "encoding": None,
            "kind": None,
            "readable": True,
            "extracted_text": None,
            "duplicate_of": None,
            "note": None,
        }
        def add_note(entry, msg):
            entry["note"] = (entry["note"] + " | " if entry["note"] else "") + msg

        try:
            entry["size_bytes"] = os.path.getsize(p)
        except Exception as e:
            add_note(entry, "stat 失败: " + str(e))
        ext = os.path.splitext(p)[1].lower()
        entry["kind"] = kind_of(ext)
        digest, oversized = sha256_file(p, max_file_bytes)
        entry["sha256"] = digest
        entry["oversized"] = oversized

        if oversized:
            entry["readable"] = False
            add_note(entry, "超过大小上限，仅记录哈希，跳过正文")

        # 文本抽取（超大文件跳过）
        if not oversized and entry["kind"] in ("pdf", "text", "record"):
            if is_probably_binary(p) and entry["kind"] != "pdf":
                entry["readable"] = False
                add_note(entry, "疑似二进制内容，跳过文本抽取")
            elif entry["kind"] == "pdf":
                out_txt = os.path.join(extracted_dir, entry["source_id"] + ".txt")
                ok, err = pdf_extract(p, out_txt)
                if ok:
                    entry["extracted_text"] = os.path.relpath(out_txt, work_dir)
                else:
                    entry["readable"] = False
                    add_note(entry, "PDF 抽取失败: " + err)
                    degradations.append(entry["source_id"] + " PDF 抽取失败: " + err)
            else:
                text, enc, err = read_text(p, max_file_bytes)
                entry["encoding"] = enc
                if text is None:
                    entry["readable"] = False
                    add_note(entry, err or "读取失败")
                else:
                    out_txt = os.path.join(extracted_dir, entry["source_id"] + ".txt")
                    if write_text(out_txt, text):
                        add_note(entry, "抽取文本写盘失败")
                    else:
                        entry["extracted_text"] = os.path.relpath(out_txt, work_dir)

        # 内容哈希去重（内容相同的文件只保留首个）
        if digest:
            if digest in seen:
                dup_groups.append({"sha256": digest, "keep": seen[digest],
                                   "duplicate": entry["source_id"]})
                entry["duplicate_of"] = seen[digest]
            else:
                seen[digest] = entry["source_id"]
        inventory.append(entry)
    return inventory, dup_groups, degradations


# ---------------------------------------------------------------------------
# 结果骨架与产物占位
# ---------------------------------------------------------------------------

def build_skeleton(case_id, question, contract_fields,
                   errors=None, degradations=None, status="partial"):
    """构造合法 result.json：case_id + 全部契约字段 + 诚实字段。"""
    result = {"case_id": case_id or "unknown"}
    for name, default in (contract_fields or []):
        result[name] = default
    if question:
        for k in ("research_question", "question"):
            if k in result and result[k] in (None, ""):
                result[k] = question
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = {
        "conflicts": [],
        "bias_risk": [],
        "gaps": [],
        "not_reported": [],
    }
    result["change_log"] = [{
        "time": now_ts(),
        "action": "skeleton_written",
        "detail": "已落盘合法结果骨架，待后续增量填充",
    }]
    result["pending_human"] = []
    if errors:
        result["errors"] = list(errors)
    if degradations:
        result["degradations"] = list(degradations)
    result["runtime"] = {
        "started_at": now_ts(),
        "budget_seconds": 900,
        "budget_requests": 64,
    }
    return result


def write_artifacts_skeleton(output_dir, work_dir, case_id, question, inventory):
    """写出各主产物的骨架占位，确保 selftest 能定位、Agent 能覆盖。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    header = "# 骨架占位 — 请由 Agent 按流程增量填充\n"
    marker_line = SKELETON_MARKER + "\n"

    review = header + marker_line + "\n## 研究问题\n\n" + \
        (question or "（待从 case.json 读取）") + "\n\n## 综述草稿\n\n（尚未生成）\n"
    themes = header + marker_line + "\n（主题归纳尚未执行；主题须从证据聚类生成，不套通用框架）\n"
    gaps = header + marker_line + "\n（冲突与研究空白尚未识别；空白须落到具体证据缺口）\n"

    write_text(os.path.join(output_dir, "review.md"), review)
    write_text(os.path.join(output_dir, "themes.md"), themes)
    write_text(os.path.join(output_dir, "conflicts_and_gaps.md"), gaps)

    screening_log = {
        "skeleton": True,
        "case_id": case_id,
        "screening_criteria": {
            "research_object": None,
            "inclusion": [],
            "exclusion": [],
            "dedup_rule": "按内容 SHA-256 去重",
        },
        "decisions": [
            {"source_id": e["source_id"], "decision": "pending", "reason": None}
            for e in inventory
        ],
        "dedup_records": [],
    }
    write_json(os.path.join(output_dir, "screening_log.json"), screening_log)

    # 空 JSONL：合法但表示尚无证据行
    write_text(os.path.join(output_dir, "evidence_table.jsonl"), "")
