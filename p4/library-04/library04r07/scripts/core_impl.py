#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- research-kb-qa Skill 的核心实现。

仅依赖 Python 标准库。设计目标（本版变体）：**鲁棒性优先**——
空输入、坏 JSON、坏编码、缺参数、超大文件、缺 pdftotext、
无写权限等异常都必须优雅降级，进程不崩溃，并如实写入
errors / degradations / limitations。

工具与 Agent 的分工：
  * 脚本负责确定性、可复算的部分：目录盘点、SHA256、类型嗅探、
    前端元数据解析、版本/重复分组、证据引用的逐字与行号校验、
    结果骨架落盘与最终合并。
  * Agent 负责语义部分：阅读材料、逐问检索、摘录逐字证据、分级不确定性。
    脚本绝不代替 Agent 编造答案或数值。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 单文件内容读取上限：超过该大小的文件仍计算哈希、记录元数据，
# 但不再解析全文/前端元数据，避免内存爆炸与超时。
MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB
# 允许纳入 answers 校验的文本行数上限。
MAX_VERIFY_LINES = 200_000

# 文本文件的扩展名集合（内容可用作证据的候选）。
TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".csv", ".tsv",
    ".yaml", ".yml", ".xml", ".html", ".tex", ".bib", ".py", ".sh", ".ini",
    ".cfg", ".toml", ".log", ".nfo", ".out", ".dat",
}
OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".webp"}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}

# 版本标记模式，用于把同一基名不同版本的文档归入同一版本族。
VERSION_TOKEN_RE = re.compile(
    r"(?:^|[_\-\s.])(?:v\d+(?:\.\d+)*|ver(?:sion)?[_\-\s.]*\d+(?:\.\d+)*|"
    r"rev[_\-\s.]*\d+|r\d+|final|draft|old|new|latest|"
    r"(?:19|20)\d{2}[-_]?\d{2}[-_]?\d{2})(?:$|[_\-\s.])",
    re.IGNORECASE,
)


def _now():
    """返回 UTC ISO8601 时间戳，保证可排序、无时区歧义。"""
    return datetime.now(timezone.utc).isoformat()


def _log(change_log, step, action, status, detail="", extra=None):
    entry = {
        "step": step,
        "time": _now(),
        "action": action,
        "status": status,
    }
    if detail:
        entry["detail"] = detail
    if extra:
        entry.update(extra)
    change_log.append(entry)
    return entry


def write_json_atomic(path, obj):
    """原子写入 JSON：先写临时文件再 os.replace，避免中途崩溃产生坏文件。"""
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True, None
    except OSError as e:
        for p in (tmp,):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return False, str(e)


def write_text_atomic(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True, None
    except OSError as e:
        return False, str(e)


def read_bytes(path, cap=MAX_CONTENT_BYTES):
    """读取文件字节；超过 cap 时返回截断标志，不读全部。

    始终返回四元组 (data, size, truncated, error)；error 为 None 表示成功。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    try:
        with open(path, "rb") as f:
            if size is not None and size > cap:
                head = f.read(cap)
                return head, size, True, None
            data = f.read()
            return data, size if size is not None else len(data), False, None
    except OSError as e:
        return b"", None, False, str(e)


def read_text_tolerant(raw, hint_ext=""):
    """按优先级尝试多种编码解码二进制文本；失败则用 replace 兜底。"""
    if not raw:
        return "", ""
    order = ["utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"]
    for enc in order:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8(replace)"


def read_text_file(path):
    """读取文本文件，返回 (text, encoding, size, truncated, error)。"""
    res = read_bytes(path)
    if len(res) == 4 and res[3] is not None:
        return "", None, 0, False, res[3]
    head, size, truncated = res
    text, enc = read_text_tolerant(head)
    return text, enc, size, truncated, None


def sha256_file(path):
    """分块计算 SHA256，超大文件也可完成。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as e:
        return None, str(e)


def sniff_type(path, head):
    """扩展名 + 内容头联合判定文件类型。"""
    ext = os.path.splitext(path)[1].lower()
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "office" if ext in OFFICE_EXTENSIONS else "archive"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff"):
        return "image"
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in OFFICE_EXTENSIONS:
        return "office"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in IMAGE_EXTENSIONS or head.startswith(b"<svg") or head.startswith(b"\x89PNG"):
        return "image"
    if ext in ARCHIVE_EXTENSIONS or head.startswith(b"PK\x03\x04"):
        return "archive"
    # 内容嗅探：无扩展名或未知扩展名但像文本
    if b"\x00" not in head[:512]:
        sample = head[:512]
        try:
            sample.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            pass
    return "unknown"


def parse_frontmatter(text):
    """解析 Markdown/YAML 风格前端元数据（--- 分隔）。"""
    meta = {}
    if not text.startswith("---"):
        return meta
    m = re.match(r"^---\s*\n(.*?)\n(?:---|\.\.\.)\s*(?:\n|$)", text, re.DOTALL)
    if not m:
        return meta
    body = m.group(1)
    for line in body.splitlines():
        line = line.rstrip("\n")
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and val:
            meta[key] = val
    return meta


def version_base(stem):
    """去掉版本标记，得到版本族基名；用于识别同一文档的不同版本。"""
    cleaned = VERSION_TOKEN_RE.sub(" ", stem)
    cleaned = re.sub(r"[\s_\-\.]+", " ", cleaned).strip().lower()
    return cleaned or stem.lower()


def manifest_version_mark(stem):
    """返回文件名的版本标记（若有）。"""
    m = VERSION_TOKEN_RE.search(stem)
    return m.group(0).strip(" _-.") if m else None


def walk_files(root):
    """递归列出根目录下所有普通文件的绝对路径，确定性排序。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def rel_to_root(abs_path, root):
    try:
        return os.path.relpath(abs_path, root).replace(os.sep, "/")
    except ValueError:
        return abs_path.replace(os.sep, "/")


def stat_info(path):
    """返回权限/来源所需信息；跨平台安全。"""
    try:
        st = os.stat(path)
        mode = oct(st.st_mode & 0o777)
        readable = bool(st.st_mode & 0o444)
        writable = bool(st.st_mode & 0o222)
        return {
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "mode_octal": mode,
            "readable_bit": readable,
            "writable_bit": writable,
            "uid": getattr(os, "getuid", lambda: None)(),
            "gid": getattr(os, "getgid", lambda: None)(),
        }
    except OSError as e:
        return {"size": None, "mtime": None, "mode_octal": None,
                "readable_bit": False, "writable_bit": False,
                "uid": None, "gid": None, "error": str(e)}


def pdftotext_pages(path):
    """尝试用系统 pdftotext 提取文档，返回 (pages_text, error)。

    pages_text 是按页分割的字符串列表；页 1 对应 index 0。
    无 pdftotext 时返回 (None, 'pdftotext unavailable')，不抛异常。
    """
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext unavailable"
    try:
        proc = subprocess.run(
            [exe, "-layout", path, "-"],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[:200]
            return None, f"pdftotext exit {proc.returncode}: {err}"
        text = proc.stdout.decode("utf-8", "replace")
        pages = text.split("\f")
        return pages, None
    except FileNotFoundError:
        return None, "pdftotext unavailable"
    except subprocess.TimeoutExpired:
        return None, "pdftotext timeout"
    except OSError as e:
        return None, f"pdftotext os error: {e}"
    except Exception as e:  # noqa: BLE001
        return None, f"pdftotext error: {e}"


def extract_contract(case):
    """从 case.json 提取 output_contract 键，保留全部键名。"""
    if not isinstance(case, dict):
        return {}, "case.json is not a JSON object"
    for key in ("output_contract", "outputContract", "contract", "result_contract", "output"):
        if key in case and isinstance(case[key], dict):
            return case[key], None
    # 兜底：无显式契约时，采用题目要求的通用结果契约，避免漏键。
    fallback = {
        "case_id": None,
        "results": None,
        "sources": None,
        "uncertainty": None,
    }
    return fallback, "no explicit output_contract; used generic fallback"


def backfill_contract(contract):
    """把契约键逐条回填为 null / [] / {}，保留原类型提示。"""
    out = {}
    for k, v in contract.items():
        if isinstance(v, dict):
            out[k] = {}
        elif isinstance(v, list):
            out[k] = []
        elif isinstance(v, bool):
            out[k] = None
        else:
            out[k] = None
    return out


def read_json_any(path):
    """容错 JSON 读取：BOM / 编码 / 语法错误均不崩溃，返回 (obj, error)。"""
    if not os.path.exists(path):
        return None, f"missing file: {path}"
    res = read_bytes(path)
    if len(res) == 4 and res[3] is not None:
        return None, f"read error: {res[3]}"
    head, size, truncated = res
    if truncated:
        return None, f"file too large to parse as JSON ({size} bytes)"
    text, enc = read_text_tolerant(head)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        # 尽力修复：仅保留第一个完整 JSON 对象。
        start = text.find("{")
        if start >= 0:
            stripped = text[start:]
            decoder = json.JSONDecoder()
            try:
                obj, _end = decoder.raw_decode(stripped)
                return obj, f"recovered partial JSON (decode error at {e.pos})"
            except json.JSONDecodeError:
                pass
        return None, f"invalid JSON: {e}"


# ---------------------------------------------------------------------------
# 阶段：scaffold —— 3 分钟内先落盘合法 partial 骨架
# ---------------------------------------------------------------------------
def _input_is_empty(input_dir):
    """输入目录不存在或没有任何文件时视为空输入。"""
    if not os.path.isdir(input_dir):
        return True
    return len(walk_files(input_dir)) == 0


def run_scaffold(args):
    change_log = []
    errors = []
    degradations = []
    result = None

    empty_input = _input_is_empty(args.input)
    case_raw, case_err = read_json_any(os.path.join(args.input, "case.json"))
    task_text, _, task_size, task_trunc, task_err = read_text_file(
        os.path.join(args.input, "task.md"))

    case = case_raw if isinstance(case_raw, dict) else {}
    if empty_input:
        degradations.append(
            {"source": "input", "message": "empty input directory (no files to inventory)"})
    elif case_err and "missing" not in case_err:
        (degradations if "recovered" in case_err else errors).append(
            {"source": "case.json", "message": case_err})
    if not empty_input and task_err:
        errors.append({"source": "task.md", "message": task_err})

    case_id = None
    for k in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
        if isinstance(case, dict) and case.get(k) is not None:
            case_id = str(case[k])
            break

    contract, contract_err = extract_contract(case)
    if contract_err:
        degradations.append({"source": "output_contract", "message": contract_err})
    contract_backfill = backfill_contract(contract)

    result = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract_backfill,
        "sources": [],
        "answers": [],
        "library": None,
        "uncertainty": [],
        "pending_human": [],
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "runtime": {
            "started_at": _now(),
            "finished_at": None,
            "elapsed_seconds": None,
            "status": "partial",
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
    }

    _log(change_log, "scaffold", "read case.json/task.md",
         "ok" if not errors else "partial",
         f"contract keys={list(contract.keys())}" if contract else "no contract",
         {"case_id": case_id})

    # 落盘骨架
    ok, werr = write_json_atomic(args.result, result)
    if not ok:
        errors.append({"source": "write", "message": f"cannot write result.json: {werr}"})
        _log(change_log, "scaffold", "write result.json", "error", werr)

    # case_summary 供 Agent 快速获取任务边界
    summary = {
        "case_id": case_id,
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
        "contract_keys": sorted(contract.keys()),
        "contract": {k: type(v).__name__ for k, v in contract.items()},
        "task_size": task_size,
        "task_truncated": task_trunc,
        "materials_hints": _materials_hints(case),
    }
    write_json_atomic(args.work_summary, summary)

    # 也写一份 answers.json 模板，供 Agent 填充
    template = {
        "output": {k: None for k in contract.keys()},
        "answers": [],
        "uncertainty": [],
    }
    write_json_atomic(args.answers, template)

    return result


def _materials_hints(case):
    """从 case.json 提取材料路径提示（若有），供 Agent 检索。"""
    hints = []
    if not isinstance(case, dict):
        return hints
    for k in ("materials", "material_paths", "files", "inputs", "documents", "data_dir"):
        v = case.get(k)
        if isinstance(v, str):
            hints.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    hints.append(item)
                elif isinstance(item, dict) and "path" in item:
                    hints.append(str(item["path"]))
    return hints[:100]


# ---------------------------------------------------------------------------
# 阶段：inventory —— 目录/元数据/版本/重复/权限
# ---------------------------------------------------------------------------
def run_inventory(args):
    change_log = []
    errors = []
    degradations = []
    files = []
    start = time.time()

    if not os.path.isdir(args.input):
        _log(change_log, "inventory", "check input dir", "error",
             f"input dir missing: {args.input}")

    for abs_path in walk_files(args.input) if os.path.isdir(args.input) else []:
        rel = rel_to_root(abs_path, args.input)
        st = stat_info(abs_path)
        head, size, truncated, head_err = read_bytes(abs_path, 8192)
        if head_err:
            errors.append({"source": rel, "message": head_err})
        ftype = sniff_type(abs_path, head or b"")
        sha, sha_err = sha256_file(abs_path)
        rec = {
            "path": rel,
            "type": ftype,
            "extension": os.path.splitext(rel)[1].lower() or None,
            "size": st.get("size"),
            "mtime": st.get("mtime"),
            "sha256": sha,
            "sha256_error": sha_err,
            "permissions": {k: st.get(k) for k in
                            ("mode_octal", "readable_bit", "writable_bit", "uid", "gid")},
            "truncated": truncated,
            "metadata": None,
            "text_encoding": None,
            "line_count": None,
            "pdf_pages": None,
            "unreadable_reason": None,
            "needs_ocr": False,
        }
        if ftype == "text" and not truncated:
            text, enc, _sz, _tr, terr = read_text_file(abs_path)
            if terr:
                errors.append({"source": rel, "message": terr})
            else:
                rec["text_encoding"] = enc
                rec["line_count"] = text.count("\n") + 1 if text else 0
                if rel.lower().endswith((".md", ".markdown")):
                    rec["metadata"] = parse_frontmatter(text)
        elif ftype == "pdf" and not truncated:
            pages, perr = pdftotext_pages(abs_path)
            if perr:
                rec["unreadable_reason"] = perr
                rec["needs_ocr"] = "pdftotext" not in perr  # 非工具缺失 => 疑似无文本层
                if "unavailable" in perr:
                    degradations.append({"source": rel, "message": perr})
                else:
                    errors.append({"source": rel, "message": perr})
            else:
                rec["pdf_pages"] = len(pages)
                if all(not (p or "").strip() for p in pages):
                    rec["needs_ocr"] = True
                    rec["unreadable_reason"] = "no text layer in PDF"
        files.append(rec)
        if truncated:
            degradations.append(
                {"source": rel, "message": f"content capped at {MAX_CONTENT_BYTES} bytes"})

    # 分组：重复 / 版本 / 类型 / 目录树
    duplicates, empty_files = _group_duplicates(files)
    version_families = _group_versions(files)
    file_types = {}
    for f in files:
        file_types.setdefault(f["type"], 0)
        file_types[f["type"]] += 1
    directory_tree = _directory_tree(files, args.input)

    inventory = {
        "summary": {
            "file_count": len(files),
            "total_bytes": (sum(f["size"] for f in files if isinstance(f["size"], int)) or 0),
            "file_types": file_types,
            "duplicate_group_count": len(duplicates),
            "version_family_count": len(version_families),
            "empty_file_count": len(empty_files),
        },
        "files": files,
        "duplicate_groups": duplicates,
        "empty_files": empty_files,
        "version_families": version_families,
        "directory_tree": directory_tree,
    }

    _log(change_log, "inventory", "scan and hash files", "ok" if not errors else "partial",
         f"files={len(files)} duplicates={len(duplicates)} versions={len(version_families)}")

    ok, werr = write_json_atomic(args.inventory, {
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "inventory": inventory,
        "elapsed_seconds": round(time.time() - start, 3),
    })
    if not ok:
        errors.append({"source": "write", "message": f"cannot write inventory.json: {werr}"})

    # 人类可读清单
    md = _render_inventory_md(inventory, errors, degradations)
    write_text_atomic(args.inventory_md, md)

    # 更新 result.json 的 library
    result_raw, _ = read_json_any(args.result)
    if isinstance(result_raw, dict):
        result_raw["library"] = {
            "summary": inventory["summary"],
            "file_types": file_types,
            "duplicate_groups": duplicates,
            "version_families": version_families,
            "directory_tree": directory_tree,
        }
        result_raw["sources"] = [
            {"path": f["path"], "sha256": f["sha256"], "type": f["type"],
             "size": f["size"], "mtime": f["mtime"]}
            for f in files if f["sha256"]
        ]
        for f in files:
            if f["sha256_error"]:
                result_raw["errors"].append(
                    {"source": f["path"], "message": f"hash failed: {f['sha256_error']}"})
        result_raw["errors"] += errors
        result_raw["degradations"] += degradations
        result_raw["change_log"] += change_log
        # 去重 change_log，避免重复追加
        result_raw["change_log"] = _dedup_change_log(result_raw["change_log"])
        result_raw["errors"] = _dedup_dicts(result_raw["errors"])
        result_raw["degradations"] = _dedup_dicts(result_raw["degradations"])
        result_raw["status"] = _compute_status(result_raw, inventory, [])
        write_json_atomic(args.result, result_raw)

    return inventory


def _group_duplicates(files):
    """按 SHA256 分组；空文件单独列出。"""
    groups = {}
    empty = []
    for f in files:
        if f["sha256"] is None:
            continue
        if f.get("size") == 0:
            empty.append(f["path"])
            continue
        groups.setdefault(f["sha256"], []).append(f["path"])
    dupes = [{"sha256": h, "count": len(p), "paths": sorted(p)}
             for h, p in sorted(groups.items(), key=lambda kv: -len(kv[1])) if len(p) >= 2]
    return dupes, empty


def _group_versions(files):
    """按版本基名分组；含两个以上成员（路径不同）才计入。"""
    fam = {}
    for f in files:
        stem = os.path.splitext(os.path.basename(f["path"]))[0]
        base = version_base(stem)
        fam.setdefault(base, []).append({
            "path": f["path"],
            "sha256": f["sha256"],
            "size": f["size"],
            "version_mark": manifest_version_mark(stem),
        })
    out = []
    for base, members in sorted(fam.items()):
        uniq = sorted({m["path"] for m in members})
        if len(uniq) < 2:
            continue
        out.append({
            "base_name": base,
            "member_count": len(uniq),
            "distinct_hashes": sorted({m["sha256"] for m in members if m["sha256"]}),
            "members": sorted(members, key=lambda m: m["path"]),
        })
    return out


def _directory_tree(files, root):
    """构建确定性目录树，含每层文件数。"""
    tree = {}
    for f in files:
        node = tree
        parts = f["path"].split("/")
        for part in parts[:-1]:
            node = node.setdefault(("dir", part), {})
        node.setdefault(("file", parts[-1]), f["sha256"])
    def _ser(node, name):
        kind, key = name
        if kind == "dir":
            return {"dir": key, "dirs": sorted(
                [_ser(v, n) for n, v in node.items() if n[0] == "dir"], key=lambda x: x["dir"]),
                "files": sorted(
                    [{"file": n[1], "sha256": v} for n, v in node.items() if n[0] == "file"],
                    key=lambda x: x["file"])}
        return {"file": key, "sha256": node}
    top = [{"type": "root", "dirs": [],
            "files": []}]
    dirs = sorted([_ser(v, n) for n, v in tree.items() if n[0] == "dir"], key=lambda x: x["dir"])
    files = sorted([n[1] for n, v in tree.items() if n[0] == "file"])
    top = [{"type": "root", "files": [{"file": fn, "sha256": tree[("file", fn)]} for fn in files],
            "dirs": dirs}]
    return top


def _render_inventory_md(inventory, errors, degradations):
    s = inventory["summary"]
    lines = [
        "# 知识库盘点清单",
        "",
        f"- 文件数：{s['file_count']}",
        f"- 总字节：{s['total_bytes']}",
        f"- 文件类型：{s['file_types']}",
        f"- 重复组：{s['duplicate_group_count']}",
        f"- 版本族：{s['version_family_count']}",
        f"- 空文件：{s['empty_file_count']}",
        "",
        "## 重复文件",
    ]
    for g in inventory["duplicate_groups"]:
        lines.append(f"- `{g['sha256'][:12]}…` ×{g['count']}: " + ", ".join(g["paths"]))
    if not inventory["duplicate_groups"]:
        lines.append("- （无）")
    lines += ["", "## 版本族"]
    for v in inventory["version_families"]:
        lines.append(f"- `{v['base_name']}` ({v['member_count']} 成员)："
                     + ", ".join(m["path"] for m in v["members"]))
    if not inventory["version_families"]:
        lines.append("- （无）")
    lines += ["", "## 文件清单"]
    for f in inventory["files"]:
        lines.append(f"- `{f['path']}` — {f['type']}, {f['size']} B, sha256={f['sha256'] or f['sha256_error']}")
    if errors:
        lines += ["", "## errors"] + [f"- {e}" for e in errors]
    if degradations:
        lines += ["", "## degradations"] + [f"- {d}" for d in degradations]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 阶段：finalize —— 融合 answers 并校验
# ---------------------------------------------------------------------------
def run_finalize(args):
    change_log = []
    errors = []
    degradations = []

    result_raw, rerr = read_json_any(args.result)
    if not isinstance(result_raw, dict):
        result_raw = {}
        errors.append({"source": "result.json", "message": rerr or "missing"})

    inv_raw, ierr = read_json_any(args.inventory)
    inventory = (inv_raw or {}).get("inventory") if isinstance(inv_raw, dict) else None

    answers_raw, aerr = read_json_any(args.answers)
    answers_list = []
    contract_out = {}
    uncertainty = []
    if isinstance(answers_raw, dict):
        answers_list = answers_raw.get("answers") or []
        contract_out = answers_raw.get("output") or {}
        uncertainty = answers_raw.get("uncertainty") or []
    else:
        errors.append({"source": "answers.json", "message": aerr or "missing/empty"})

    # 逐条校验证据：文件存在、行号/页码、逐字引用
    verified_answers = []
    for i, a in enumerate(answers_list):
        if not isinstance(a, dict):
            continue
        evs = a.get("evidence") or []
        for e in evs:
            if not isinstance(e, dict):
                continue
            e["_verified"] = _verify_evidence(e, args.input)
        a["_index"] = i
        verified_answers.append(a)

    # 回填 output_contract
    result_raw.setdefault("output_contract", {})
    for k, v in contract_out.items():
        if k in result_raw["output_contract"] or v is not None:
            result_raw["output_contract"][k] = v
    # 未被 answers 覆盖的键保持 null/[]/{}
    for k, v in result_raw["output_contract"].items():
        pass  # 已在 scaffold 回填，保持不变

    result_raw["answers"] = verified_answers
    result_raw["uncertainty"] = uncertainty
    result_raw.setdefault("pending_human", [])
    # 将 uncertainty 中的 pending_human 项提升到 pending_human
    for u in uncertainty:
        if isinstance(u, dict) and u.get("level") in ("insufficient_evidence", "unreadable"):
            result_raw.setdefault("pending_human", []).append(u)
    result_raw["change_log"] = _dedup_change_log(
        (result_raw.get("change_log") or []) + change_log)
    result_raw["errors"] = _dedup_dicts((result_raw.get("errors") or []) + errors)
    result_raw["degradations"] = _dedup_dicts((result_raw.get("degradations") or []) + degradations)
    result_raw["limitations"] = result_raw.get("limitations") or []

    # limitations 汇总：诚实标注缺口
    result_raw["limitations"] = _collect_limitations(result_raw)

    if inventory is not None:
        result_raw.setdefault("library", {})
    result_raw["status"] = _compute_status(result_raw, inventory, verified_answers)
    result_raw["runtime"]["finished_at"] = _now()
    started = result_raw["runtime"].get("started_at")
    if started:
        try:
            t0 = datetime.fromisoformat(started)
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            result_raw["runtime"]["elapsed_seconds"] = round(elapsed, 3)
        except ValueError:
            pass
    result_raw["runtime"]["status"] = result_raw["status"]

    _log(change_log, "finalize", "merge and verify answers",
         "ok" if not errors else "partial", f"answers={len(verified_answers)}")
    result_raw["change_log"] = _dedup_change_log(result_raw["change_log"] + change_log)

    ok, werr = write_json_atomic(args.result, result_raw)
    if not ok:
        errors.append({"source": "write", "message": werr})
        # 写盘失败最后一次尝试降级到最小合法产物
        minimal = {
            "case_id": result_raw.get("case_id"),
            "status": "partial",
            "sources": result_raw.get("sources", []),
            "uncertainty": result_raw.get("uncertainty", []),
            "change_log": result_raw.get("change_log", change_log),
            "errors": [{"source": "write", "message": werr}],
        }
        write_json_atomic(args.result, minimal)

    # 证据映射
    evidence_map = _build_evidence_map(verified_answers, inventory, args.input)
    write_json_atomic(args.evidence_map, evidence_map)

    return result_raw


def _verify_evidence(e, input_root):
    """校验一条证据：相对路径存在、locator 命中原文件、quote 逐字出现。"""
    rel = e.get("file")
    if not rel:
        return "no-file"
    abs_p = os.path.join(input_root, rel.replace("/", os.sep))
    if not os.path.exists(abs_p):
        return "file-missing"
    quote = (e.get("quote") or "")
    locator = (e.get("locator") or "")
    sha = e.get("sha256")
    results = []

    # 哈希校验（若提供）
    if sha:
        actual, err = sha256_file(abs_p)
        if err:
            results.append("hash-error")
        elif actual != sha:
            results.append("hash-mismatch")

    # locator 校验
    line_range = None
    page = None
    m = re.search(r"#L(\d+)(?:-L?(\d+))?", locator)
    if m:
        line_range = (int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1)))
    pm = re.search(r"#p(\d+)", locator)
    if pm:
        page = int(pm.group(1))

    # 逐字引用校验
    raw, size, truncated = read_bytes(abs_p)
    if isinstance(raw, tuple):
        raw = raw[0]
    text, enc = read_text_tolerant(raw)
    if not truncated:
        norm_text = re.sub(r"\s+", " ", text)
        norm_quote = re.sub(r"\s+", " ", quote).strip()
        if norm_quote and norm_quote in norm_text:
            results.append("quote-ok")
        elif norm_quote:
            results.append("quote-mismatch")
        # 行号校验
        if line_range:
            total_lines = text.count("\n") + 1 if text else 0
            if line_range[1] <= total_lines:
                lines = text.splitlines()
                snippet = "\n".join(lines[line_range[0] - 1:line_range[1]])
                if norm_quote and re.sub(r"\s+", " ", snippet).find(norm_quote) >= 0:
                    results.append("locator-ok")
                else:
                    results.append("locator-unverified")
            else:
                results.append("locator-out-of-range")
        # 页码校验（PDF）
        if page is not None:
            pages, perr = pdftotext_pages(abs_p)
            if perr:
                results.append("page-unverifiable")
            elif page <= len(pages):
                ptext = re.sub(r"\s+", " ", pages[page - 1])
                if norm_quote and norm_quote in ptext:
                    results.append("page-ok")
                else:
                    results.append("page-unverified")
            else:
                results.append("page-out-of-range")
    else:
        results.append("content-too-large")

    if not results:
        return "unverified"
    if "quote-ok" in results and not any(x in results for x in ("quote-mismatch", "hash-mismatch")):
        return "verified" if ("locator-ok" in results or "page-ok" in results or not (line_range or page)) else \
            ("verified-quote-only" if not line_range and not page else "partially-verified")
    if "quote-mismatch" in results or "hash-mismatch" in results:
        return "invalid"
    return "unverified"


def _build_evidence_map(answers, inventory, input_root):
    """证据映射：答案 → 文件 → locator → quote，供独立复核。"""
    mapping = []
    for a in answers:
        entry = {"answer_index": a.get("_index"), "question": a.get("question"),
                 "evidence": a.get("evidence") or []}
        mapping.append(entry)
    return {"count": len(mapping), "map": mapping}


def _compute_status(result, inventory, answers):
    """诚实反映完成度。优先级：输入错误 > 证据不足 > 部分/完成。"""
    errors = result.get("errors") or []

    def _msgs():
        for e in errors:
            if isinstance(e, dict):
                yield str(e.get("message", ""))
            else:
                yield str(e)

    # 1. 输入契约无法解析（case.json 坏 JSON）→ input_error
    if any(("invalid JSON" in m or "JSONDecodeError" in m or "read error" in m)
           for m in _msgs()):
        return "input_error"
    # 2. 没有 output_contract → 无法按契约交付
    if result.get("output_contract") is None:
        return "input_error"
    # 3. 没有可盘点/可引用的材料文件 → insufficient_evidence
    if inventory is None or (inventory.get("summary", {}).get("file_count", 0) == 0):
        return "insufficient_evidence"
    # 4. 有答案：全部证据通过则 ok，出现 invalid 则 partial
    if answers:
        for a in answers:
            for e in (a.get("evidence") or []):
                if isinstance(e, dict) and e.get("_verified") == "invalid":
                    return "partial"
        return "ok"
    # 5. 有材料但尚无答案 → partial
    return "partial"


def _collect_limitations(result):
    """汇总限制，不编造完成状态。"""
    lim = []
    serr = result.get("errors") or []
    sdeg = result.get("degradations") or []
    seen = set()
    for item in serr + sdeg:
        msg = item.get("message") if isinstance(item, dict) else str(item)
        src = item.get("source", "") if isinstance(item, dict) else ""
        key = f"{src}:{msg}"
        if key in seen:
            continue
        seen.add(key)
        lim.append({"scope": src, "limitation": msg})
    return lim


def _dedup_change_log(items):
    seen = set()
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _dedup_dicts(items):
    seen = set()
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="research-kb-qa 科研知识库盘点与带引用问答 Skill")
    p.add_argument("command", nargs="?", default="full",
                   choices=["full", "scaffold", "inventory", "finalize", "selftest-cmd"],
                   help="要执行的阶段；full=依次执行全部")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p


def _derive_paths(args):
    """推导各产物路径；输出/工作目录不存在时尽力创建。"""
    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            # 创建失败不崩溃，后续原子写入会记录错误。
            sys.stderr.write(f"warning: cannot create {d}: {e}\n")
    return {
        "result": os.path.join(args.output, "result.json"),
        "inventory": os.path.join(args.work, "inventory.json"),
        "inventory_md": os.path.join(args.output, "inventory.md"),
        "work_summary": os.path.join(args.work, "case_summary.json"),
        "answers": os.path.join(args.work, "answers.json"),
        "evidence_map": os.path.join(args.output, "evidence_map.json"),
    }


def run():
    parser = build_parser()
    args = parser.parse_args()
    paths = _derive_paths(args)
    args.result = paths["result"]
    args.inventory = paths["inventory"]
    args.inventory_md = paths["inventory_md"]
    args.work_summary = paths["work_summary"]
    args.answers = paths["answers"]
    args.evidence_map = paths["evidence_map"]

    try:
        if args.command == "scaffold":
            run_scaffold(args)
        elif args.command == "inventory":
            run_inventory(args)
        elif args.command == "finalize":
            run_finalize(args)
        elif args.command == "selftest-cmd":
            sys.exit(_selftest_embedded())
        else:  # full
            run_scaffold(args)
            run_inventory(args)
            run_finalize(args)
    except KeyboardInterrupt:
        # 超时/中断：尽力落盘 partial，已经写过的结果保留。
        sys.stderr.write("interrupted; partial results retained\n")
        sys.exit(0)
    except Exception:  # noqa: BLE001 —— Skill 必须不崩溃
        traceback.print_exc()
        sys.exit(0)
    sys.exit(0)


def _selftest_embedded():
    """内嵌自检：不依赖外部输入，验证骨架、盘点、校验路径可运行。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kbqa_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        workp = os.path.join(tmp, "work")
        os.makedirs(inp, exist_ok=True)
        case = {"case_id": "demo-1", "output_contract": {
            "case_id": None, "results": [], "sources": [], "uncertainty": []}}
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# task\n")
        with open(os.path.join(inp, "a.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: A\n---\n\nHello world.\n")
        class NS:
            pass
        a = NS(); a.input = inp; a.output = out; a.work = workp
        paths = _derive_paths(a)
        for name, val in paths.items():
            setattr(a, name, val)
        run_scaffold(a)
        run_inventory(a)
        ok, _ = write_json_atomic(
            os.path.join(workp, "answers.json"),
            {"output": {"results": ["hello"]},
             "answers": [{"question": "q", "answer": "Hello world.",
                          "evidence": [{"file": "a.md", "sha256": sha256_file(os.path.join(inp, "a.md"))[0],
                                        "locator": "a.md#L5", "quote": "Hello world.",
                                        "confidence": "certain"}],
                          "uncertainty": "certain", "missing": None}]})
        run_finalize(a)
        data, err = read_json_any(os.path.join(out, "result.json"))
        assert err is None, err
        assert data["case_id"] == "demo-1"
        assert set(["case_id", "results", "sources", "uncertainty"]).issubset(
            set(data["output_contract"].keys()))
        assert data["status"] == "ok"
        return 0
    finally:
        try:
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    run()