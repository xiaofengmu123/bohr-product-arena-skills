# -*- coding: utf-8 -*-
"""core_impl.py — 科研知识库资料管理与带来源引用问答 Skill 的核心实现。

仅依赖 Python 标准库。所有路径均通过命令行参数传入，缺省指向运行契约的固定位置。

子命令
------
scaffold   阶段 0：探测输入、读取 case_id 与 output_contract、落盘一版最小合法
           result.json 骨架（status=partial），保证任何后续失败都不留空产物。
inventory  阶段 1：递归扫描输入目录，计算 SHA256、文件类型、元数据、权限、
           目录树、重复文件分组、版本家族，并按文本逐字索引生成证据索引。
finalize   阶段 3：读入 Agent 写入的 answers.json 与阶段 1 的清单，合并
           output_contract、构建双向 evidence_map、逐字回核引用、产出最终
           result.json、inventory.md 与 evidence_map.json。
verify     对 evidence_map/answers 中的逐字 quote 做回核，输出核验报告。
selftest   在临时目录运行一轮端到端冒烟测试，验证契约与降级路径。

确定性约定：SHA256 用小写十六进制；列表输出按稳定键排序；JSON 输出
ensure_ascii=False、indent=2、sort_keys=True。文本读取依次尝试 utf-8-sig /
utf-8 / latin-1，失败则标记编码降级，绝不编造内容。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件扫描上限 2MB，超出跳过并记录
MAX_INDEX_TEXT_BYTES = 512 * 1024  # 证据索引中保留全文的上限

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

# 扩展名 -> 类型 映射（内容嗅探仅作为兜底）
EXT_TYPE = {
    ".md": "text", ".markdown": "text", ".txt": "text", ".text": "text",
    ".csv": "text", ".tsv": "text", ".yaml": "text", ".yml": "text",
    ".json": "json", ".jsonl": "text", ".bib": "text", ".tex": "text",
    ".xml": "text", ".html": "text", ".htm": "text", ".rst": "text",
    ".py": "text", ".r": "text", ".ipynb": "json", ".log": "text",
    ".pdf": "pdf",
    ".docx": "office", ".doc": "office", ".xlsx": "office", ".xls": "office",
    ".pptx": "office", ".ppt": "office", ".odt": "office", ".ods": "office",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".tiff": "image", ".tif": "image", ".bmp": "image", ".webp": "image",
    ".svg": "image",
    ".zip": "archive", ".tar": "archive", ".gz": "archive", ".tgz": "archive",
    ".7z": "archive", ".rar": "archive",
}

VERSION_TOKEN_RE = re.compile(
    r"(?:^|[-_ .])?(?:v|ver|version|rev|r)(?:[-_.]?\d+)+", re.IGNORECASE
)
WORD_VERSION_RE = re.compile(
    r"\b(final|draft|drafted|clean|copy|latest|old|bak|backup|updated|new)\b",
    re.IGNORECASE,
)
DATE_VERSION_RE = re.compile(
    r"(\d{8}|\d{4}[-_]\d{1,2}[-_]\d{1,2}|\d{4}[.]\d{1,2}[.]\d{1,2})"
)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def write_json(path: str, obj, sort=True) -> None:
    """确定性写 JSON：ensure_ascii=False、indent=2、可选排序。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"ensure_ascii": False, "indent": 2}
    if sort:
        kwargs["sort_keys"] = True
    p.write_text(json.dumps(obj, **kwargs) + "\n", encoding="utf-8")


def read_json_safe(path: str):
    """容错读 JSON：BOM / 坏编码 / 坏 JSON 均不抛异常，返回 (obj, error)。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, "read_failed:" + str(exc)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None, "decode_failed"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "bad_json:" + str(exc)


def read_text_auto(path: str, max_bytes=MAX_INDEX_TEXT_BYTES):
    """逐文件自适应解码，返回 (text, encoding, note)。超限返回截断说明，失败不抛。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, None, "stat_failed:" + str(exc)
    if size == 0:
        return "", "none", "empty"
    if size > max_bytes:
        return None, None, "too_large:%d" % size
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    return None, None, "decode_failed"


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_for_search(s: str) -> str:
    """逐行小写并压缩水平空白，保留换行，用于逐字引用的可复现回核。"""
    out = []
    for line in s.splitlines():
        out.append(re.sub(r"[ \t\r\f\v]+", " ", line).strip())
    return "\n".join(out)


def normalize_quote(s: str) -> str:
    """quote 规范化：小写、压缩空白、去首尾空白；用于确定性哈希。"""
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_sha256(quote: str) -> str:
    return sha256_of_bytes(normalize_quote(quote).encode("utf-8"))


def sniff_type(relpath: str, prefix: bytes, size: int) -> str:
    ext = os.path.splitext(relpath)[1].lower()
    if ext in EXT_TYPE:
        base = EXT_TYPE[ext]
        if base == "text" and not prefix:
            return "empty"
        return base
    if size == 0:
        return "empty"
    sample = prefix[:4096]
    if b"\x00" in sample:
        return "binary"
    try:
        sample.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        try:
            sample.decode("latin-1")
            return "text"
        except UnicodeDecodeError:
            return "binary"


def list_files_sorted(root: str):
    """确定性递归遍历，按相对路径排序返回文件相对路径列表。"""
    hits = []
    root_path = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.isfile(full):
                hits.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(hits)


# --------------------------------------------------------------------------- #
# 阶段 0：scaffold
# --------------------------------------------------------------------------- #
def materialize_contract_template(oc, depth=0):
    """把 output_contract 递归展开为叶子 -> null 的模板，保留未知结构。

    规则：dict 视为对象递归；list 视为 []；标量视叶子为 null；
    若 dict 含 JSON Schema 风格 "properties"，则展开其 properties。
    """
    if depth > 8:
        return None
    if isinstance(oc, dict):
        if "properties" in oc and isinstance(oc["properties"], dict):
            return {k: materialize_contract_template(v, depth + 1) for k, v in oc["properties"].items()}
        return {k: materialize_contract_template(v, depth + 1) for k, v in oc.items()}
    if isinstance(oc, list):
        return []
    return None


def build_scaffold(input_dir: str, output_dir: str, work_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_path = _find_case(input_dir)
    errors, degradations = [], []
    case = None
    case_summary = {"case_path": None, "case_id": None, "output_contract": {},
                    "task_path": None, "questions": None, "materials_dirs": []}
    status = STATUS_PARTIAL

    if case_path is None:
        status = STATUS_INPUT_ERROR
        errors.append({"stage": "scaffold", "item": "case.json", "detail": "未在输入目录找到 case.json"})
    else:
        case_summary["case_path"] = case_path
        case, err = read_json_safe(os.path.join(input_dir, case_path))
        if err:
            status = STATUS_INPUT_ERROR
            errors.append({"stage": "scaffold", "item": "case.json", "detail": err})
            case = {}
        if case:
            case_summary["case_id"] = (case.get("case_id") or case.get("id")
                                       or case.get("challenge_id") or "unknown_case")
            oc = (case.get("output_contract") if "output_contract" in case
                  else case.get("outputContract", {}))
            if not isinstance(oc, dict):
                oc = {"value": oc}
                degradations.append({"stage": "scaffold", "item": "output_contract",
                                     "detail": "output_contract 非对象结构，已包装为 value"})
            case_summary["output_contract"] = oc
            case_summary["questions"] = case.get("questions") or case.get("queries") or None
            case_summary["materials_dirs"] = _extract_materials_dirs(case)

    task_path = _find_task(input_dir)
    case_summary["task_path"] = task_path

    # 骨架结果：output_contract 全字段回填为 null 模板，附最小合法字段。
    contract = case_summary["output_contract"]
    template = materialize_contract_template(contract) if isinstance(contract, dict) else {}

    skeleton = {
        "case_id": case_summary["case_id"] or "unknown_case",
        "status": status,
        "output_contract": contract,
        "output": template,
        "answers": [],
        "evidence_map": {"schema": "evidence_map_v2", "claims": [], "evidence": [],
                         "conflicts": [], "pending_human": []},
        "sources": [],
        "library": {},
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "step": "scaffold", "time": now_iso(), "action": "落盘最小合法结果骨架",
            "status": status, "detail": "output_contract 已回填；后续阶段增量改进"
        }],
        "errors": errors,
        "degradations": degradations,
        "runtime": {"started_at": now_iso(), "stages": {"scaffold": "done"}},
        "deliverables": {"result": "result.json"},
    }

    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, skeleton)
    write_json(os.path.join(work_dir, "case_summary.json"), case_summary)
    return skeleton


def _find_case(input_dir: str):
    for name in ("case.json", "input.json", "task_input.json"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return name
    # 兜底：在 /input 一级查找唯一个 .json 主文件
    cands = []
    if os.path.isdir(input_dir):
        for fn in os.listdir(input_dir):
            if fn.lower().endswith(".json"):
                cands.append(fn)
    return sorted(cands)[0] if len(cands) == 1 else None


def _find_task(input_dir: str):
    for name in ("task.md", "task.txt", "README.md", "readme.md"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return name
    return None


def _extract_materials_dirs(case: dict):
    dirs = []
    for key in ("materials", "material", "input_files", "files", "data_dir", "materials_dirs"):
        v = case.get(key)
        if isinstance(v, str):
            dirs.append(v)
        elif isinstance(v, list):
            dirs.extend(str(x) for x in v if isinstance(x, str))
        elif isinstance(v, dict) and isinstance(v.get("path"), str):
            dirs.append(v["path"])
    return sorted(set(d for d in dirs if d))


# --------------------------------------------------------------------------- #
# 阶段 1：inventory
# --------------------------------------------------------------------------- #
def build_inventory(input_dir: str, work_dir: str) -> dict:
    files, errors, degradations = [], [], []
    type_counts = {}
    dedupe = {}         # sha256 -> [relpath]
    versions = {}       # base_name -> {path, sha256, token}
    tree = []

    relpaths = list_files_sorted(input_dir)

    for rel in relpaths:
        full = os.path.join(input_dir, rel)
        try:
            st = os.stat(full)
        except OSError as exc:
            errors.append({"stage": "inventory", "item": rel, "detail": "stat_failed:" + str(exc)})
            continue
        size = st.st_size
        if size > MAX_FILE_BYTES:
            degradations.append({"stage": "inventory", "item": rel,
                                 "detail": "size_skipped:%d" % size})
            continue
        try:
            prefix = open(full, "rb").read(256) if size else b""
        except OSError as exc:
            errors.append({"stage": "inventory", "item": rel, "detail": "read_failed:" + str(exc)})
            continue

        ftype = sniff_type(rel, prefix, size)
        type_counts[ftype] = type_counts.get(ftype, 0) + 1

        try:
            digest = sha256_file(full)
        except OSError as exc:
            errors.append({"stage": "inventory", "item": rel, "detail": "hash_failed:" + str(exc)})
            continue
        dedupe.setdefault(digest, []).append(rel)

        mode = st.st_mode
        perms = {
            "mode_octal": format(mode & 0o777, "o"),
            "readable": os.access(full, os.R_OK),
            "writable": os.access(full, os.W_OK),
            "uid": getattr(st, "st_uid", None),
            "gid": getattr(st, "st_gid", None),
        }
        meta = {}
        enc = None
        if ftype in ("text", "json"):
            text, enc, note = read_text_auto(full)
            if note:
                degradations.append({"stage": "inventory", "item": rel, "detail": note})
            if ftype == "text":
                meta = extract_frontmatter(text or "") if text is not None else {}
            if note is None and text is not None:
                meta["line_count"] = len(text.splitlines())

        entry = {
            "path": rel,
            "name": os.path.basename(rel),
            "ext": os.path.splitext(rel)[1].lower(),
            "type": ftype,
            "size": size,
            "mtime": st.st_mtime,
            "sha256": digest,
            "permissions": perms,
            "source": "task_input",
            "text_encoding": enc,
            "metadata": meta,
        }
        files.append(entry)

        base, token = version_base_and_token(rel)
        if token is not None:
            versions.setdefault(base, []).append({"path": rel, "sha256": digest, "version_token": token})

        # 目录树：按父目录累积
        parent = os.path.dirname(rel).replace(os.sep, "/")
        tree.append({"path": rel, "parent": parent, "name": os.path.basename(rel)})

    # 输出确定性排序
    files.sort(key=lambda e: e["path"])
    dup_groups = []
    for digest, paths in sorted(dedupe.items(), key=lambda kv: kv[0]):
        if len(paths) > 1:
            dup_groups.append({"sha256": digest, "paths": sorted(paths)})
    empty_files = sorted(e["path"] for e in files if e["type"] == "empty")

    version_families = []
    for base in sorted(versions):
        members = versions[base]
        if len(members) >= 2:
            # 是否同哈希：不同哈希才代表内容差异
            uniq_hashes = sorted(set(m["sha256"] for m in members))
            version_families.append({
                "base_name": base,
                "members": sorted(members, key=lambda m: m["path"]),
                "distinct_content_hashes": len(uniq_hashes),
                "content_differs": len(uniq_hashes) > 1,
            })

    directory_tree = _build_directory_tree(tree)

    inventory = {
        "generated_at": now_iso(),
        "input_root": os.path.normpath(input_dir),
        "summary": {
            "total_files": len(files),
            "total_bytes": sum(e["size"] for e in files),
            "file_types": dict(sorted(type_counts.items())),
            "error_files": len(errors),
        },
        "directory_tree": directory_tree,
        "files": files,
        "duplicate_groups": dup_groups,
        "version_families": version_families,
        "empty_files": empty_files,
        "errors": errors,
        "degradations": degradations,
    }

    index = build_evidence_index(input_dir, files, work_dir)
    write_json(os.path.join(work_dir, "inventory.json"), inventory)
    write_json(os.path.join(work_dir, "evidence_index.json"), index)
    return inventory


def version_base_and_token(relpath: str):
    """提取版本基名与匹配标记；无版本标记返回 (None, None)。"""
    stem = os.path.splitext(os.path.basename(relpath))[0]
    tokens = []
    m = VERSION_TOKEN_RE.search(stem)
    if m:
        tokens.append(m.group(0))
    m = WORD_VERSION_RE.search(stem)
    if m:
        tokens.append(m.group(0))
    m = DATE_VERSION_RE.search(stem)
    if m:
        tokens.append(m.group(0))
    if not tokens:
        return None, None
    base = stem
    for t in tokens:
        base = base.replace(t, " ")
    base = re.sub(r"[-_.\s]+", "_", base).strip("_").lower() or "unnamed"
    return base, ",".join(sorted(set(tokens)))


def extract_frontmatter(text: str):
    """抽取 Markdown/文本 frontmatter 的 title/author/date/tags（尽力而为）。"""
    meta = {}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                end = i
                break
        if end:
            for ln in lines[1:end]:
                if ":" in ln and not ln.lstrip().startswith(("-", "#")):
                    key, val = ln.split(":", 1)
                    key, val = key.strip().lower(), val.strip()
                    if key in ("title", "author", "date", "tags", "version", "id", "doi"):
                        meta[key] = val
    return meta


def _build_directory_tree(entries):
    root = {"_dirs": {}, "_files": []}
    for e in sorted(entries, key=lambda x: x["path"]):
        parts = e["parent"].split("/") if e["parent"] else []
        node = root
        for part in parts:
            node = node["_dirs"].setdefault(part, {"_dirs": {}, "_files": []})
        node["_files"].append(e["name"])
    return _serialize_tree(root)


def _serialize_tree(node):
    out = []
    for name in sorted(node.get("_dirs", {})):
        child = node["_dirs"][name]
        out.append({"directory": name, "children": _serialize_tree(child)})
    for fn in sorted(node.get("_files", [])):
        out.append({"file": fn})
    return out


def build_evidence_index(input_dir: str, files, work_dir: str):
    docs = []
    for e in files:
        if e["type"] not in ("text", "json", "pdf"):
            continue
        full = os.path.join(input_dir, e["path"])
        doc = {
            "path": e["path"], "sha256": e["sha256"], "kind": e["type"],
            "line_count": None, "text": None, "note": None,
        }
        if e["type"] in ("text", "json"):
            text, enc, note = read_text_auto(full)
            if note:
                doc["note"] = note
            else:
                doc["line_count"] = len(text.splitlines()) if text is not None else 0
                doc["text"] = text
        elif e["type"] == "pdf":
            doc.update(_extract_pdf_text(full, work_dir))
        docs.append(doc)
    docs.sort(key=lambda d: d["path"])
    return {"generated_at": now_iso(), "documents": docs}


def _extract_pdf_text(pdf_path: str, work_dir: str):
    """可用 pdftotext 时抽取文本并按页划分；否则标记 needs_ocr，绝不编造文本。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return {"kind": "pdf", "text": None, "pages": [], "note": "needs_ocr:pdftotext_missing"}
    out_dir = os.path.join(work_dir, "pdf_text")
    os.makedirs(out_dir, exist_ok=True)
    out_name = os.path.splitext(os.path.basename(pdf_path))[0] + ".txt"
    out_path = os.path.join(out_dir, out_name)
    try:
        subprocess.run([exe, "-enc", "UTF-8", pdf_path, out_path],
                       check=True, timeout=60, capture_output=True)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"kind": "pdf", "text": None, "pages": [],
                "note": "needs_ocr:pdftotext_failed:" + str(exc)}
    if not os.path.isfile(out_path):
        return {"kind": "pdf", "text": None, "pages": [],
                "note": "needs_ocr:no_text_layer"}
    text, _enc, note = read_text_auto(out_path, max_bytes=MAX_INDEX_TEXT_BYTES * 2)
    if note or text is None:
        return {"kind": "pdf", "text": None, "pages": [],
                "note": "needs_ocr:" + (note or "empty")}
    pages = text.split("\f")
    return {"kind": "pdf", "text": text, "pages": pages,
            "page_count": len(pages), "note": None}


# --------------------------------------------------------------------------- #
# 证据核验
# --------------------------------------------------------------------------- #
def locate_quote(text: str, quote: str):
    """在文本中定位逐字 quote，返回 {found, start_line, end_line, char_span}。

    先在原始文本精确查找，再逐级放宽到行规范化文本，保证粒度可回核。
    """
    qraw = quote
    idx = text.find(qraw)
    if idx == -1:
        idx = text.find(qraw.strip())
    if idx == -1:
        # 大小写 + 行内空白放宽
        qnorm = re.sub(r"[ \t]+", " ", qraw.strip().lower())
        tnorm = re.sub(r"[ \t]+", " ", text.lower())
        idx = tnorm.find(qnorm)
        if idx != -1:
            end = idx + len(qnorm)
            return _span_to_lines(tnorm, idx, end)
        return {"found": False}
    end = idx + len(qraw.strip())
    return _span_to_lines(text, idx, max(end, idx + len(qraw)))


def _span_to_lines(text, start, end):
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)
    start_line = 1 + sum(1 for ls in line_starts if ls <= start)
    end_line = 1 + sum(1 for ls in line_starts if ls < end)
    return {"found": True, "start_line": start_line, "end_line": end_line,
            "char_span": [start, end]}


def verify_evidence_against_files(evidence_map, input_dir: str):
    """逐条回核 evidence 的 quote 是否命中其 file。返回带核验结果的新结构。"""
    for ev in evidence_map.get("evidence", []):
        rel = ev.get("file")
        full = os.path.join(input_dir, rel) if rel else None
        ev["verification"] = {
            "checked": True, "status": "unverified", "note": None,
            "found_locator": None, "char_span": None,
        }
        if not full or not os.path.isfile(full):
            ev["verification"]["note"] = "file_missing:" + str(rel)
            continue
        text, _enc, note = read_text_auto(full, max_bytes=MAX_INDEX_TEXT_BYTES * 2)
        if note:
            # PDF 抽取文本可能已在 evidence_index，此处退而求其次读文件
            if note.startswith("too_large"):
                ev["verification"]["note"] = "file_too_large_unverified"
            else:
                ev["verification"]["note"] = "unreadable:" + note
            continue
        quote = ev.get("quote") or ""
        if not quote:
            ev["verification"]["note"] = "empty_quote"
            continue
        loc = locate_quote(text or "", quote)
        if loc.get("found"):
            ev["verification"]["status"] = "verified"
            ev["verification"]["found_locator"] = ("%s#L%d-L%d" % (rel, loc["start_line"], loc["end_line"]))
            ev["verification"]["char_span"] = loc["char_span"]
        else:
            ev["verification"]["note"] = "quote_not_found_in_file"
    return evidence_map


# --------------------------------------------------------------------------- #
# 阶段 3：finalize（构建 evidence_map 并把 Agent 答案归一为交付物）
# --------------------------------------------------------------------------- #
def build_final(input_dir: str, output_dir: str, work_dir: str, answers_path: str) -> dict:
    inventory, _ = read_json_safe(os.path.join(work_dir, "inventory.json"))
    inventory = inventory or {}
    case_summary, _ = read_json_safe(os.path.join(work_dir, "case_summary.json"))
    case_summary = case_summary or {}
    answers, ans_err = read_json_safe(answers_path)
    answers = answers or {}
    if ans_err:
        answers["errors"] = (answers.get("errors") or []) + [{
            "stage": "finalize", "item": "answers.json", "detail": ans_err}]

    # 1) 归一化为 evidence_map_v2：claims / evidence / conflicts / pending_human
    raw_answers = answers.get("answers") or []
    evidence_map = {
        "schema": "evidence_map_v2",
        "generated_at": now_iso(),
        "determinism": {"quote_normalization": "lowercase+collapse_whitespace",
                         "hash": "sha256"},
        "claims": [],
        "evidence": [],
        "conflicts": [],
        "pending_human": [],
    }
    ev_by_idx = {}
    for ai, ans in enumerate(raw_answers):
        cid = "C%03d" % (ai + 1)
        ev_refs = []
        for ei, ev in enumerate(ans.get("evidence") or []):
            eid = "E%03d" % (ai + 1) if not ei else "E%03d_%d" % (ai + 1, ei)
            quote = ev.get("quote") or ""
            entry = {
                "evidence_id": eid,
                "file": ev.get("file") or "not_reported",
                "sha256": ev.get("sha256") or "not_reported",
                "locator": ev.get("locator") or ("%s#unknown" % (ev.get("file") or "file")),
                "char_span": ev.get("char_span"),
                "quote": quote,
                "quote_norm": normalize_quote(quote) if quote else None,
                "quote_sha256": quote_sha256(quote) if quote else None,
                "confidence": ev.get("confidence") or ans.get("uncertainty") or "likely",
                "source_role": ev.get("source_role") or "primary",
                "used_by": [cid],
            }
            evidence_map["evidence"].append(entry)
            ev_refs.append(eid)
        uncertainty = ans.get("uncertainty") or "likely"
        claim = {
            "claim_id": cid,
            "question": ans.get("question"),
            "claim": (ans.get("answer") or ""),
            "status": "insufficient_evidence" if not ans.get("answer")
                      else ("conflict" if uncertainty == "conflict"
                            else ("insufficient_evidence" if uncertainty == "insufficient_evidence"
                                  else "unreadable" if uncertainty == "unreadable" else "supported")),
            "confidence": uncertainty,
            "evidence_refs": ev_refs,
            "missing": ans.get("missing") if ans.get("missing") is not None
                       else (ans.get("question") if not ans.get("answer") else None),
        }
        evidence_map["claims"].append(claim)

    for u in (answers.get("uncertainty") or []):
        if not isinstance(u, dict):
            continue
        if u.get("level") == "conflict":
            evidence_map["conflicts"].append({
                "item": u.get("item"), "reason": u.get("reason") or "sources_disagree",
                "sources": u.get("sources") or [],
            })
        elif u.get("level") == "insufficient_evidence":
            evidence_map["pending_human"].append({
                "item": u.get("item"), "reason": u.get("reason") or "no_evidence_in_library",
            })

    # 2) 逐字回核
    evidence_map = verify_evidence_against_files(evidence_map, input_dir)

    # 3) 契约回填：output 覆盖骨架模板
    contract = case_summary.get("output_contract") or {}
    output_template = materialize_contract_template(contract) if isinstance(contract, dict) else {}
    output_vals = answers.get("output") or {}
    for key in output_template:
        if key in output_vals:
            output_template[key] = output_vals[key]

    failures = (inventory.get("errors") or []) + (inventory.get("degradations") or [])
    status = answers.get("status") or STATUS_OK
    if failures:
        status = STATUS_PARTIAL if status == STATUS_OK else status
    if not raw_answers:
        status = STATUS_INSUFFICIENT

    result = {
        "case_id": case_summary.get("case_id") or "unknown_case",
        "status": status,
        "output_contract": contract,
        "output": output_template,
        "answers": raw_answers,
        "evidence_map": evidence_map,
        "sources": [{
            "path": f.get("path"), "sha256": f.get("sha256"), "type": f.get("type"),
            "size": f.get("size"), "version_marker": (version_base_and_token(f.get("path", ""))[1]),
        } for f in (inventory.get("files") or [])],
        "library": {
            "summary": inventory.get("summary") or {},
            "directory_tree": inventory.get("directory_tree") or [],
            "duplicate_groups": inventory.get("duplicate_groups") or [],
            "version_families": inventory.get("version_families") or [],
        },
        "uncertainty": answers.get("uncertainty") or [],
        "pending_human": evidence_map["pending_human"],
        "change_log": [
            {"step": "scaffold", "time": now_iso(), "action": "落盘最小合法结果骨架",
             "status": STATUS_PARTIAL, "detail": "output_contract 已回填"},
            {"step": "inventory", "time": now_iso(),
             "action": "扫描输入、计算哈希、分组重复与版本",
             "status": STATUS_PARTIAL if failures else STATUS_OK,
             "detail": "files=%d" % len(inventory.get("files") or [])},
            {"step": "finalize", "time": now_iso(),
             "action": "合并答案、归一证据映射、逐字回核引用",
             "status": status, "detail": "answers=%d evidence=%d" % (
                 len(raw_answers), len(evidence_map["evidence"]))},
        ],
        "errors": list(failures) + list(answers.get("errors") or []),
        "degradations": inventory.get("degradations") or [],
        "runtime": {
            "started_at": case_summary.get("runtime", {}).get("started_at") or now_iso(),
            "finished_at": now_iso(),
            "deterministic_fingerprint": compute_fingerprint(
                case_summary.get("case_id"), inventory, raw_answers),
        },
        "deliverables": {
            "result": "result.json",
            "inventory": "inventory.md",
            "evidence_map": "evidence_map.json",
        },
    }

    # 4) 附属可读产物
    write_json(os.path.join(output_dir, "evidence_map.json"), evidence_map)
    render_inventory_md(inventory, os.path.join(output_dir, "inventory.md"))
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def compute_fingerprint(case_id, inventory, answers):
    """对确定性核心内容求 SHA256，供独立复跑核对；不含墙钟时间与状态文本。"""
    core = {
        "case_id": case_id,
        "files": sorted(
            [{"path": f["path"], "sha256": f["sha256"]} for f in (inventory.get("files") or [])],
            key=lambda x: x["path"]),
        "answers": answers,
    }
    canon = json.dumps(core, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def render_inventory_md(inventory, path):
    files = inventory.get("files") or []
    lines = [
        "# 科研知识库清单",
        "",
        "- 生成时间：%s" % inventory.get("generated_at"),
        "- 输入目录：%s" % inventory.get("input_root"),
        "- 文件总数：%d；总字节：%d" % (
            len(files), inventory.get("summary", {}).get("total_bytes", 0)),
        "",
        "## 文件清单",
        "",
        "| 相对路径 | 类型 | 大小 | SHA256（前 12 位） | 版本标记 |",
        "|---|---|---|---|---|",
    ]
    for f in files:
        tok = version_base_and_token(f["path"])[1]
        lines.append("| %s | %s | %d | %s | %s |" % (
            f["path"], f["type"], f["size"], f["sha256"][:12], tok or "-"))
    lines += ["", "## 重复文件（内容哈希相同）", ""]
    dups = inventory.get("duplicate_groups") or []
    if not dups:
        lines.append("（无）")
    for g in dups:
        lines.append("- `%s` → %s" % (g["sha256"][:12], ", ".join("`%s`" % p for p in g["paths"])))
    lines += ["", "## 版本家族", ""]
    fams = inventory.get("version_families") or []
    if not fams:
        lines.append("（无）")
    for fam in fams:
        lines.append("- **%s**（内容%s，%d 个成员）：%s" % (
            fam["base_name"],
            "有差异" if fam["content_differs"] else "相同",
            len(fam["members"]),
            ", ".join(m["path"] for m in fam["members"])))
    lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# verify 子命令
# --------------------------------------------------------------------------- #
def cmd_verify(input_dir, work_dir, output_dir):
    em, err = read_json_safe(os.path.join(output_dir, "evidence_map.json"))
    if err or not em:
        em, err2 = read_json_safe(os.path.join(work_dir, "evidence_map.json"))
        if err2 or not em:
            print("verify: evidence_map not found (%s / %s)" % (err, err2))
            return 1
    em = verify_evidence_against_files(em, input_dir)
    write_json(os.path.join(output_dir, "evidence_map.verified.json"), em)
    ok = sum(1 for e in em.get("evidence", []) if e.get("verification", {}).get("status") == "verified")
    total = len(em.get("evidence", []))
    print("verified %d/%d evidence items" % (ok, total))
    return 0 if ok == total else 2


# --------------------------------------------------------------------------- #
# selftest 子命令（端到端冒烟测试）
# --------------------------------------------------------------------------- #
def run_selftest() -> int:
    print("== selftest: 科研知识库 Skill 端到端测试 ==")
    tmp = tempfile.mkdtemp(prefix="libskill_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    wrk = os.path.join(tmp, "work")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)

    case = {
        "case_id": "selftest-case",
        "output_contract": {"answer_summary": "string", "cited_sources": "list"},
        "questions": ["材料 A 的结论是什么？"],
        "materials": ["materials"],
    }
    write_json(os.path.join(inp, "case.json"), case)
    Path(os.path.join(inp, "task.md")).write_text("# 自检任务\n", encoding="utf-8")
    paper = "结论：该材料证实方法 X 有效。\n方法 X 在三种条件下均显著。\n"
    Path(os.path.join(inp, "materials", "paper.md")).write_text(paper, encoding="utf-8")
    Path(os.path.join(inp, "materials", "paper_copy.md")).write_text(paper, encoding="utf-8")
    Path(os.path.join(inp, "materials", "report_v1.md")).write_text("v1 内容\n", encoding="utf-8")
    Path(os.path.join(inp, "materials", "report_v2.md")).write_text("v2 内容不同\n", encoding="utf-8")

    build_scaffold(inp, out, wrk)
    build_inventory(inp, wrk)

    answers = {
        "status": "ok",
        "output": {"answer_summary": "材料 A 证实方法 X 有效", "cited_sources": ["materials/paper.md"]},
        "answers": [{
            "question": "材料 A 的结论是什么？",
            "answer": "材料 A 证实方法 X 有效。",
            "uncertainty": "certain",
            "evidence": [{
                "file": "materials/paper.md",
                "sha256": sha256_of_bytes(paper.encode("utf-8")),
                "locator": "materials/paper.md#L1-L2",
                "quote": "结论：该材料证实方法 X 有效。",
                "confidence": "certain",
            }],
        }],
        "uncertainty": [],
    }
    write_json(os.path.join(wrk, "answers.json"), answers)
    result = build_final(inp, out, wrk, os.path.join(wrk, "answers.json"))

    checks = []
    checks.append(("result.json 存在", os.path.isfile(os.path.join(out, "result.json"))))
    checks.append(("case_id 正确", result.get("case_id") == "selftest-case"))
    checks.append(("status==ok", result.get("status") == STATUS_OK))
    checks.append(("evidence 逐字核验通过",
                   result["evidence_map"]["evidence"][0]["verification"]["status"] == "verified"))
    checks.append(("重复文件分组检出", any(
        "paper_copy.md" in p for g in result["library"]["duplicate_groups"] for p in g["paths"])))
    checks.append(("版本家族检出", any(
        fam["base_name"] == "report" for fam in result["library"]["version_families"])))
    checks.append(("output_contract 回填", result.get("output", {}).get("answer_summary")
                   == "材料 A 证实方法 X 有效"))

    code = 0
    for name, ok in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            code = 1

    # 降级路径：空目录
    empty_in = os.path.join(tmp, "empty")
    os.makedirs(empty_in, exist_ok=True)
    sk = build_scaffold(empty_in, os.path.join(tmp, "empty_out"), os.path.join(tmp, "empty_wk"))
    checks2 = [("空目录降级 status", sk.get("status") == STATUS_INPUT_ERROR)]
    for name, ok in checks2:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            code = 1
    print("== selftest %s ==" % ("PASSED" if code == 0 else "FAILED"))
    return code


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="main.py", description="科研知识库资料管理与带引用问答 Skill")
    sub = parser.add_subparsers(dest="cmd")

    def add_common(p):
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)

    p0 = sub.add_parser("scaffold", help="阶段0：落盘结果骨架")
    add_common(p0)

    p1 = sub.add_parser("inventory", help="阶段1：扫描与证据索引")
    add_common(p1)

    p2 = sub.add_parser("finalize", help="阶段3：合并答案并产出最终结果")
    add_common(p2)
    p2.add_argument("--answers", default=None, help="answers.json 路径")

    p3 = sub.add_parser("verify", help="回核 evidence_map 的逐字引用")
    add_common(p3)

    p4 = sub.add_parser("selftest", help="端到端冒烟测试")

    ns = parser.parse_args(argv)
    if ns.cmd == "scaffold":
        build_scaffold(ns.input, ns.output, ns.work)
        print("scaffold: wrote %s" % os.path.join(ns.output, "result.json"))
        return 0
    if ns.cmd == "inventory":
        inv = build_inventory(ns.input, ns.work)
        print("inventory: files=%d dups=%d versions=%d" % (
            len(inv["files"]), len(inv["duplicate_groups"]), len(inv["version_families"])))
        return 0
    if ns.cmd == "finalize":
        ans = ns.answers or os.path.join(ns.work, "answers.json")
        build_final(ns.input, ns.output, ns.work, ans)
        print("finalize: wrote %s" % os.path.join(ns.output, "result.json"))
        return 0
    if ns.cmd == "verify":
        return cmd_verify(ns.input, ns.work, ns.output)
    if ns.cmd == "selftest":
        return run_selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())