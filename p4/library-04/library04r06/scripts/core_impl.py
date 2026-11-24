#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — research-kb-qa Skill 的实现（仅 Python 标准库）。

本模块把“盘点 → 索引 → 带来源引用的问答 → 可复核交付”固化为四个可独立复跑的
生产步骤，以及一个组合命令与自检入口：

    scaffold    读案例/题目，写 plan.json + case_summary.json + result.json 骨架
    inventory   盘点材料，写 inventory.json + evidence_index.json，并回填 result.json
    finalize    合并 answers.json，写最终 result.json + inventory.md + evidence_map.json
    pipeline    scaffold -> inventory -> finalize
    selftest    空输入 / 坏 JSON / 契约回填 / 确定性 四组自检

设计要点：

1. 任何一步失败都不崩溃：先写一份合法的 partial result.json，再逐步增量改进。
2. 中间表示（plan.json / case_summary.json / inventory.json / evidence_index.json /
   answers.json / trace.json）构成可审计、可复跑的证据链；每一份都可由哈希定位。
3. 确定性输出：固定排序、sort_keys、固定 sha256、LF 换行；时间戳只记录在
   runtime / change_log，不影响可复现的实质内容。
4. output_contract 原样回填全部键（不变名、不删未知键），缺值填 null/[]/{}。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat as statmod
import subprocess
import time
from datetime import datetime, timezone

MAX_TEXT_BYTES = 2 * 1024 * 1024

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml",
    ".yml", ".rst", ".log", ".bib", ".tex", ".py", ".ini", ".cfg", ".toml",
    ".xml", ".html", ".htm", ".org",
}
OFFICE_EXTS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".odp", ".pages", ".numbers", ".key",
}
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".svg", ".heic", ".ico",
}
ARCHIVE_EXTS = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".lzma",
}

TASK_INPUT_NAMES = {"case.json", "task.md", "challenge.json"}

_VERSION_RE = re.compile(
    r"(?:^|[-_.\s])"
    r"(?:v\d+(?:\.\d+)*|ver(?:sion)?[-_.\s]?\d+(?:\.\d+)*|rev[-_.\s]?[A-Za-z0-9]+"
    r"|final|draft|latest|old|new|archived|\d{8}|\d{4}[-_.]\d{1,2}[-_.]\d{1,2})"
    r"(?:$|[-_.\s])",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def mtime_iso(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def rel_posix(path, base):
    try:
        r = os.path.relpath(path, base)
    except ValueError:
        r = path
    return r.replace(os.sep, "/")


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_bytes_limited(path, max_bytes):
    try:
        with open(path, "rb") as f:
            b = f.read(max_bytes + 1)
        if len(b) > max_bytes:
            return None, "file exceeds %d bytes" % max_bytes
        return b, None
    except OSError as e:
        return None, str(e)


def decode_text(b):
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return b.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, None


def read_text(path):
    b, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err is not None or not b:
        return None, err or "empty"
    return decode_text(b) if b else (None, None)


def load_json(path):
    b, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err is not None:
        return None, err
    text, _enc = decode_text(b)
    if text is None:
        return None, "undecodable"
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001
        return None, "bad_json: %s" % type(e).__name__


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 案例读取与计划
# ---------------------------------------------------------------------------

def find_case_json(input_dir):
    for name in ("case.json", "challenge.json"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return p
    return None


def read_case(input_dir):
    case_path = find_case_json(input_dir)
    info = {
        "case_path": rel_posix(case_path, input_dir) if case_path else None,
        "case_id": None,
        "output_contract": {},
        "questions": [],
        "materials": [],
        "errors": [],
        "task_summary": None,
        "task_sha256": None,
    }
    if case_path is None:
        info["errors"].append("case.json not found under input")
    else:
        obj, err = load_json(case_path)
        if err is not None:
            info["errors"].append("case.json %s" % err)
        elif not isinstance(obj, dict):
            info["errors"].append("case.json root is not an object")
        else:
            info["case_id"] = (
                obj.get("case_id") or obj.get("id") or obj.get("challenge_id")
                or obj.get("challengeId") or obj.get("armiId")
            )
            contract = obj.get("output_contract")
            if isinstance(contract, dict):
                info["output_contract"] = contract
            else:
                if isinstance(obj.get("questions"), list):
                    info["output_contract"] = {"questions": obj.get("questions")}
                else:
                    info["output_contract"] = {}
                info["errors"].append(
                    "no output_contract object in case.json; using derived defaults"
                )
            qs = obj.get("questions") or info["output_contract"].get("questions") or []
            if isinstance(qs, str):
                info["questions"] = [qs]
            elif isinstance(qs, list):
                info["questions"] = [
                    q if isinstance(q, str) else json.dumps(q, ensure_ascii=False)
                    for q in qs
                ]
            for k in ("materials", "input_files", "data_path", "materials_path", "sources"):
                v = obj.get(k)
                if isinstance(v, str):
                    info["materials"].append(v)
                elif isinstance(v, list):
                    info["materials"].extend([str(x) for x in v])

    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        info["task_sha256"] = sha256_file(task_path)
        t, _err = read_text(task_path)
        if t:
            info["task_summary"] = t[:4000]
    return info


def make_plan(case):
    return {
        "task": "科研知识库问答：盘点 -> 索引 -> 带来源引用的问答 -> 可复核交付",
        "contract_echo": "output_contract 原样回填全部键；缺值填 null/[]/{}",
        "budget": {
            "model_requests": 64,
            "seconds": 900,
            "tokens_per_response": 16384,
        },
        "phases": [
            {
                "id": "scaffold",
                "deadline_sec": 180,
                "produces": ["result.json", "plan.json", "case_summary.json"],
                "rule": "前 3 分钟内落盘合法骨架，之后只做增量改进",
            },
            {
                "id": "inventory",
                "deadline_sec": 720,
                "produces": ["inventory.json", "evidence_index.json"],
                "rule": "六维度盘点、sha256、重复与版本分组、PDF 页索引",
            },
            {
                "id": "answer",
                "deadline_sec": 840,
                "produces": ["answers.json"],
                "rule": "逐问检索，逐字摘录证据，写 answers.json",
            },
            {
                "id": "finalize",
                "deadline_sec": 900,
                "produces": ["result.json", "inventory.md", "evidence_map.json", "trace.json"],
                "rule": "合并、自检、最终落盘",
            },
        ],
        "intermediate_representations": [
            "plan.json", "case_summary.json", "inventory.json",
            "evidence_index.json", "answers.json", "trace.json",
        ],
        "case_id": case.get("case_id"),
        "question_count": len(case.get("questions") or []),
    }


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def skeleton(case):
    result = {
        "case_id": case.get("case_id"),
        "status": "partial" if case.get("case_id") else "input_error",
        "output_contract": case.get("output_contract", {}),
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [e for e in case.get("errors", [])],
        "degradations": (
            ["case.json missing or unreadable; contract echoed is empty"]
            if not case.get("case_id") else []
        ),
        "library": {},
        "answers": [],
        "deliverables": ["result.json"],
        "runtime": {"started_at": None, "steps": []},
    }
    for k in case.get("output_contract", {}):
        if k not in result:
            result[k] = None
    return result


def scaffold(args):
    os.makedirs(args["output"], exist_ok=True)
    os.makedirs(args["work"], exist_ok=True)
    case = read_case(args["input"])
    os.makedirs(args["work"], exist_ok=True)
    write_json(os.path.join(args["work"], "plan.json"), make_plan(case))
    write_json(os.path.join(args["work"], "case_summary.json"), case)
    result = skeleton(case)
    result["runtime"]["started_at"] = now_iso()
    _append_step(result, "scaffold", "write partial skeleton", "ok")
    write_json(os.path.join(args["output"], "result.json"), result)
    _record_trace(args["work"], "scaffold", "partial skeleton written", "ok")
    print("scaffold: case_id=%s contract_keys=%s" % (
        case.get("case_id"), sorted(case.get("output_contract", {}).keys())))


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def load_summary_or_recompute(args):
    p = os.path.join(args["work"], "case_summary.json")
    if os.path.isfile(p):
        obj, err = load_json(p)
        if err is None and isinstance(obj, dict):
            obj.setdefault("output_contract", {})
            obj.setdefault("questions", [])
            return obj
    return read_case(args["input"])


def permissions(fp):
    try:
        st = os.stat(fp)
        return {
            "mode": oct(statmod.S_IMODE(st.st_mode))[2:].zfill(4),
            "readable": os.access(fp, os.R_OK),
            "writable": os.access(fp, os.W_OK),
            "uid": getattr(st, "st_uid", None),
            "gid": getattr(st, "st_gid", None),
        }
    except OSError as e:
        return {"mode": None, "readable": False, "writable": False, "error": str(e)}


def _looks_like_text(fp):
    try:
        with open(fp, "rb") as f:
            b = f.read(1024)
    except OSError:
        return False
    if b"\x00" in b:
        return False
    try:
        b.decode("utf-8-sig")
        return True
    except UnicodeDecodeError:
        return False


def classify(fp, ext):
    head = b""
    try:
        with open(fp, "rb") as f:
            head = f.read(8)
    except OSError:
        pass
    if head.startswith(b"%PDF"):
        return "pdf"
    if (
        head.startswith(b"\x89PNG")
        or head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"GIF8")
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
    ):
        return "image"
    if head.startswith(b"PK\x03\x04"):
        return "office" if ext in OFFICE_EXTS else "archive"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in TEXT_EXTS:
        return "text"
    if _looks_like_text(fp):
        return "text"
    return "unknown"


def parse_frontmatter(text):
    t = text.lstrip("\ufeff")
    if not (t.startswith("---\n") or t.startswith("---\r\n")):
        return {}
    lines = t.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None or end <= 1:
        return {}
    md = {}
    for ln in lines[1:end]:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k = k.strip().strip("\"'")
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
        else:
            v = v.strip("\"'")
        md[k] = v
    return md


def analyze_file(fp, rel, input_dir):
    st = None
    try:
        st = os.stat(fp)
    except OSError:
        pass
    size = st.st_size if st else 0
    ext = os.path.splitext(fp)[1].lower()
    sha = sha256_file(fp)
    type_class = classify(fp, ext)
    readable = sha is not None
    text_error = None
    encoding = None
    frontmatter = {}
    if type_class == "text" and size <= MAX_TEXT_BYTES:
        b, err = read_bytes_limited(fp, MAX_TEXT_BYTES)
        if err is None and b:
            text, encoding = decode_text(b)
            if text is None:
                text_error = "undecodable"
                readable = False
            else:
                frontmatter = parse_frontmatter(text)
        else:
            text_error = err
            readable = False
    source = "task_input" if os.path.basename(fp) in TASK_INPUT_NAMES else "material"
    return {
        "relative_path": rel,
        "sha256": sha,
        "size_bytes": size,
        "mtime": mtime_iso(fp),
        "type": type_class,
        "extension": ext,
        "text_encoding": encoding,
        "readable": readable,
        "text_error": text_error,
        "permissions": permissions(fp),
        "source": source,
        "metadata": frontmatter,
    }


def _metadata_fields(files):
    fm = {}
    for f in files:
        for k, v in (f.get("metadata") or {}).items():
            fm.setdefault(k, set()).add(str(v))
    file_fields = sorted(
        {k for f in files for k in f if k not in ("metadata", "permissions")}
    )
    return {
        "file_fields": file_fields,
        "frontmatter": {k: sorted(list(v)) for k, v in sorted(fm.items())},
    }


def _sort_tree(node):
    if isinstance(node, dict):
        out = {}
        for k in sorted(node):
            out[k] = _sort_tree(node[k])
        if "__files__" in out:
            out["__files__"] = sorted(out["__files__"])
        return out
    if isinstance(node, list):
        return sorted(node)
    return node


def _directory_tree(files):
    tree = {}
    for f in files:
        rel = f.get("relative_path") or ""
        parts = [p for p in rel.split("/") if p]
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(parts[-1] if parts else rel)
    return _sort_tree(tree)


def _duplicate_groups(files):
    by_hash = {}
    empties = []
    for f in files:
        if f.get("sha256") is None:
            continue
        if f.get("size_bytes") == 0:
            empties.append(f["relative_path"])
            continue
        by_hash.setdefault(f["sha256"], []).append(f["relative_path"])
    groups = [
        {"sha256": h, "paths": sorted(v)}
        for h, v in sorted(by_hash.items()) if len(v) > 1
    ]
    return {"groups": groups, "empty_files": sorted(empties)}


def version_base_marker(stem):
    m = _VERSION_RE.search(stem)
    if not m:
        return stem.lower(), None
    marker = m.group(0).strip(" -_.")
    base = _VERSION_RE.sub(" ", stem).strip()
    base = re.sub(r"[-_.\s]+", "_", base).strip("_")
    return (base or stem).lower(), marker


def _version_families(files):
    buckets = {}
    for f in files:
        name = os.path.basename(f.get("relative_path") or "")
        stem, _ext = os.path.splitext(name)
        base, marker = version_base_marker(stem)
        buckets.setdefault(base, []).append({
            "path": f["relative_path"], "sha256": f["sha256"], "marker": marker,
        })
    families = []
    for base in sorted(buckets):
        members = buckets[base]
        if len(members) < 2:
            continue
        unique = sorted({m["sha256"] for m in members if m["sha256"]})
        families.append({
            "base_name": base,
            "members": sorted(members, key=lambda x: x["path"]),
            "distinct_hashes": unique,
            "is_duplicate_only": len(unique) <= 1 and len(members) > 1,
        })
    return families


def build_library(files):
    file_types = {}
    for f in files:
        file_types.setdefault(f["type"], []).append(f["relative_path"])
    for t in file_types:
        file_types[t] = sorted(file_types[t])
    return {
        "file_types": {"counts": {t: len(v) for t, v in sorted(file_types.items())},
                        "by_type": file_types},
        "metadata_fields": _metadata_fields(files),
        "directory_tree": _directory_tree(files),
        "duplicate_files": _duplicate_groups(files),
        "version_families": _version_families(files),
        "permissions_and_source": {
            f["relative_path"]: {
                "mode": f["permissions"].get("mode"),
                "readable": f["readable"],
                "writable": f["permissions"].get("writable"),
                "source": f["source"],
            } for f in files
        },
    }


def _preview(text):
    s = text.replace("\r", "")
    return s[:200].replace("\n", " \\n ")


def _pages_from_pdf_text(txt_path):
    try:
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return []
    pages = raw.split("\f")
    out = []
    start = 0
    for i, page in enumerate(pages, 1):
        content = page.strip()
        out.append({
            "page": i,
            "char_start": start,
            "char_end": start + len(page),
            "preview": content[:200].replace("\n", " "),
        })
        start += len(page) + 1
    return out


def extract_pdf(src, dst):
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return False
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        r = subprocess.run([pdftotext, "-layout", "-enc", "UTF-8", src, dst])
        if r.returncode != 0:
            return False
    except OSError:
        return False
    return os.path.isfile(dst) and os.path.getsize(dst) > 0


def build_evidence_index(files, args):
    idx = {}
    pdf_text_dir = os.path.join(args["work"], "pdf_text")
    for f in files:
        rel = f["relative_path"]
        entry = {
            "sha256": f["sha256"], "type": f["type"],
            "line_count": None, "pages": [], "text_file": None,
        }
        if f["type"] == "text" and f["readable"]:
            fp = os.path.join(args["input"], rel)
            text, _enc = read_text(fp)
            if text is not None:
                entry["line_count"] = text.count("\n") + 1
                entry["preview"] = _preview(text)
        elif f["type"] == "pdf" and f["readable"]:
            sha = f["sha256"]
            txtp = os.path.join(pdf_text_dir, sha + ".txt")
            if os.path.isfile(txtp) and os.path.getsize(txtp) > 0:
                entry["pages"] = _pages_from_pdf_text(txtp)
                entry["text_file"] = rel_posix(txtp, args["work"])
            else:
                ok = extract_pdf(os.path.join(args["input"], rel), txtp)
                if ok:
                    entry["pages"] = _pages_from_pdf_text(txtp)
                    entry["text_file"] = rel_posix(txtp, args["work"])
                else:
                    entry["needs_ocr"] = True
        idx[rel] = entry
    return {
        "files": idx,
        "note": "pages are 1-based from pdftotext form-feed splits; "
                "empty pages or no text layer => scanned / needs_ocr",
    }


def sources_from_files(files):
    return [
        {
            "relative_path": f["relative_path"], "sha256": f["sha256"],
            "type": f["type"], "size_bytes": f["size_bytes"],
            "source": f["source"],
        } for f in files
    ]


def inventory(args):
    os.makedirs(args["output"], exist_ok=True)
    os.makedirs(args["work"], exist_ok=True)
    case = load_summary_or_recompute(args)
    input_dir = args["input"]
    files = []
    for dirpath, dirnames, filenames in os.walk(input_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            fp = os.path.join(dirpath, fn)
            rel = rel_posix(fp, input_dir)
            files.append(analyze_file(fp, rel, input_dir))
    files.sort(key=lambda x: x["relative_path"])
    library = build_library(files)
    evidence = build_evidence_index(files, args)
    inv = {
        "input_dir": input_dir,
        "files": files,
        "library": library,
        "generated_at": now_iso(),
    }
    write_json(os.path.join(args["work"], "inventory.json"), inv)
    write_json(os.path.join(args["work"], "evidence_index.json"), evidence)

    result = _load_or_skeleton(args, case)
    result["sources"] = sources_from_files(files)
    result["library"] = library
    result["number_of_files"] = len(files)
    _append_step(result, "inventory", "inventory + evidence index built", "ok")
    write_json(os.path.join(args["output"], "result.json"), result)
    _record_trace(args["work"], "inventory", "%d files inventoried" % len(files), "ok")
    print("inventory: %d files, %d types" % (
        len(files), len(library["file_types"]["counts"])))


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _load_or_skeleton(args, case):
    p = os.path.join(args["output"], "result.json")
    if os.path.isfile(p):
        obj, err = load_json(p)
        if err is None and isinstance(obj, dict):
            return obj
    return skeleton(case)


def build_pending(answers, uncertainty):
    pend = []
    for a in answers:
        level = a.get("uncertainty") or (a.get("evidence") or [{}])[0].get("confidence")
        if level in ("insufficient_evidence", "unreadable", "needs_ocr",
                     "conflict", "source_conflict", "pending_human"):
            pend.append({
                "item": a.get("question"),
                "reason": a.get("missing") or level,
                "level": level,
            })
    for u in uncertainty:
        if u.get("level") in ("insufficient_evidence", "unreadable", "needs_ocr",
                              "conflict", "source_conflict", "pending_human"):
            pend.append({
                "item": u.get("item"), "reason": u.get("reason"),
                "level": u.get("level"), "sources": u.get("sources"),
            })
    seen, out = set(), []
    for p in pend:
        key = json.dumps(p, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return sorted(out, key=lambda x: str(x.get("item", "")))


def compute_status(case, result, inv):
    if result.get("case_id") is None:
        return "input_error"
    n_mat = sum(1 for f in (inv.get("files") or []) if f.get("source") == "material")
    answers = result.get("answers") or []
    if n_mat == 0 and not answers:
        return "insufficient_evidence"
    questions = case.get("questions") or []
    if questions:
        answered = {a.get("question") for a in answers if a.get("answer")}
        if len(answered) < len(questions):
            return "partial"
    if not answers:
        return "partial"
    return "ok"


def render_inventory_md(inv):
    lines = ["# Knowledge base inventory", ""]
    files = inv.get("files") or []
    lines.append("## Files (%d)" % len(files))
    lines.append("")
    lines.append("| path | sha256(12) | type | size | source | readable |")
    lines.append("|---|---|---|---|---|---|")
    for f in files:
        lines.append("| %s | %s | %s | %d | %s | %s |" % (
            f["relative_path"], (f["sha256"] or "-")[:12], f["type"],
            f["size_bytes"], f["source"], f["readable"]))
    lib = inv.get("library") or {}
    lines.append("")
    lines.append("## Six-dimension summary")
    lines.append("")
    lines.append("### file_types")
    lines.append("```json")
    lines.append(json.dumps(lib.get("file_types", {}).get("counts", {}),
                            ensure_ascii=False))
    lines.append("```")
    lines.append("### duplicate_files")
    lines.append("```json")
    lines.append(json.dumps(lib.get("duplicate_files", {}).get("groups", []),
                            ensure_ascii=False))
    lines.append("```")
    lines.append("### version_families")
    lines.append("```json")
    lines.append(json.dumps(lib.get("version_families", []), ensure_ascii=False))
    lines.append("```")
    lines.append("### frontmatter fields")
    lines.append("```json")
    lines.append(json.dumps(
        (lib.get("metadata_fields") or {}).get("frontmatter", {}), ensure_ascii=False))
    lines.append("```")
    return "\n".join(lines) + "\n"


def build_evidence_map(answers):
    m = {}
    for a in answers:
        m[str(a.get("question"))] = [
            {
                "file": e.get("file"), "sha256": e.get("sha256"),
                "locator": e.get("locator"), "quote": e.get("quote"),
                "confidence": e.get("confidence"),
            } for e in a.get("evidence", [])
        ]
    return m


def finalize(args):
    os.makedirs(args["output"], exist_ok=True)
    os.makedirs(args["work"], exist_ok=True)
    case = load_summary_or_recompute(args)
    inv = load_json(os.path.join(args["work"], "inventory.json"))[0] \
        if os.path.isfile(os.path.join(args["work"], "inventory.json")) else {}
    if not isinstance(inv, dict):
        inv = {}
    answers_path = args.get("answers") or os.path.join(args["work"], "answers.json")
    answers = {}
    if os.path.isfile(answers_path):
        answers, err = load_json(answers_path)
        if err is not None or not isinstance(answers, dict):
            answers = {}
    result = _load_or_skeleton(args, case)

    ans_list = answers.get("answers") if isinstance(answers.get("answers"), list) else []
    # 确定性排序：按 question 文本
    result["answers"] = sorted(ans_list, key=lambda a: str(a.get("question", "")))
    out_map = answers.get("output") if isinstance(answers.get("output"), dict) else {}
    for k in case.get("output_contract", {}):
        if k in out_map:
            result[k] = out_map[k]
        elif k not in result:
            result[k] = None
    u_list = answers.get("uncertainty") if isinstance(answers.get("uncertainty"), list) else []
    result["uncertainty"] = sorted(u_list, key=lambda x: str(x.get("item", "")))
    result["pending_human"] = build_pending(result["answers"], result["uncertainty"])
    if inv:
        result["library"] = inv.get("library", result.get("library", {}))
        result["sources"] = sources_from_files(inv.get("files", []))
        result["number_of_files"] = len(inv.get("files", []))
    result["status"] = compute_status(case, result, inv)
    result["deliverables"] = [
        "result.json", "inventory.md", "evidence_map.json",
        "plan.json", "inventory.json", "evidence_index.json",
        "answers.json", "trace.json",
    ]
    # 缺失/冲突的诚实标注
    _merge_limitations(result, case, inv)
    _append_step(result, "finalize", "merged answers + final artifacts", result["status"])
    write_json(os.path.join(args["output"], "result.json"), result)
    write_text(os.path.join(args["output"], "inventory.md"), render_inventory_md(inv))
    write_json(os.path.join(args["output"], "evidence_map.json"),
               build_evidence_map(result["answers"]))
    _record_trace(args["work"], "finalize", "final artifacts written", result["status"])
    print("finalize: status=%s answers=%d" % (result["status"], len(result["answers"])))


def _merge_limitations(result, case, inv):
    """把无法处理的材料/合同缺口如实写进 degradations（不编造状态）。"""
    deg = list(result.get("degradations") or [])
    for f in (inv.get("files") or []):
        if f.get("text_error") and not f.get("readable"):
            deg.append("unreadable: %s (%s)" % (f["relative_path"], f["text_error"]))
        if f.get("type") == "pdf" and f.get("sha256") is None:
            deg.append("unreadable pdf: %s" % f["relative_path"])
    if not case.get("output_contract"):
        deg.append("no output_contract provided; emitted structural fields only")
    # 去重
    seen, out = set(), []
    for d in deg:
        if d not in seen:
            seen.add(d)
            out.append(d)
    result["degradations"] = out


# ---------------------------------------------------------------------------
# 组合命令与 trace
# ---------------------------------------------------------------------------

def pipeline(args):
    scaffold(args)
    inventory(args)
    finalize(args)


def _append_step(result, step, action, status):
    entry = {"step": step, "action": action, "status": status, "time": now_iso()}
    result.setdefault("change_log", []).append(entry)
    result.setdefault("runtime", {}).setdefault("steps", []).append(entry)


def _record_trace(work_dir, step, action, status):
    p = os.path.join(work_dir, "trace.json")
    obj = {"case_id": None, "steps": []}
    if os.path.isfile(p):
        loaded, err = load_json(p)
        if err is None and isinstance(loaded, dict):
            obj = loaded
    obj.setdefault("steps", []).append({
        "step": step, "action": action, "status": status, "time": now_iso(),
    })
    write_json(p, obj)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv):
    if not argv:
        return "help", {}
    cmd = argv[0]
    args = {
        "input": DEFAULT_INPUT,
        "output": DEFAULT_OUTPUT,
        "work": DEFAULT_WORK,
        "answers": None,
    }
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "-i"):
            i += 1
            args["input"] = argv[i]
        elif a in ("--output", "-o"):
            i += 1
            args["output"] = argv[i]
        elif a in ("--work", "-w"):
            i += 1
            args["work"] = argv[i]
        elif a in ("--answers", "-a"):
            i += 1
            args["answers"] = argv[i]
        else:
            raise SystemExit("unknown arg: %s" % a)
        i += 1
    return cmd, args


def print_help():
    print(
        "usage: python3 scripts/main.py "
        "{scaffold|inventory|finalize|pipeline|selftest} "
        "[--input DIR] [--output DIR] [--work DIR] [--answers FILE]"
    )


def run(argv):
    cmd, args = parse_args(argv)
    if cmd in ("--help", "-h", "help"):
        print_help()
        return 0
    handlers = {
        "scaffold": scaffold,
        "inventory": inventory,
        "finalize": finalize,
        "pipeline": pipeline,
    }
    if cmd == "selftest":
        import selftest
        return selftest.run()
    fn = handlers.get(cmd)
    if fn is None:
        print("unknown command: %s" % cmd)
        print_help()
        return 2
    fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(__import__("sys").argv[1:]))