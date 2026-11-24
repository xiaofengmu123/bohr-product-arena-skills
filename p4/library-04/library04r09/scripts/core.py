"""core.py - 科研知识库问答 Skill 的核心实现。

只依赖 Python 标准库。所有函数保持确定性：目录遍历按相对路径排序、
JSON 序列化按 key 排序，确保同一输入两次运行产物一致。

设计目标（精简版）：
  1. 网络/软件缺失一律降级，不崩溃、不编造；
  2. 每个关键结论都带 locator + 逐字 quote，并在 finalize 阶段回读原文件校验；
  3. 输出契约字段动态回填：不知契约内容也能原样保留全部键。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

UTF8 = "utf-8"

# 文本内容可检索的文件扩展名（确定性归属）
TEXT_EXTS = {
    ".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".tex",
    ".bib", ".py", ".rst", ".log", ".ini", ".cfg", ".toml", ".xml",
}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
}
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".svg",
    ".webp",
}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"}

MAGIC = [
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "office"),
    (b"\x89PNG", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"GIF8", "image"),
    (b"BM", "image"),
]

VERSION_PATTERN = re.compile(
    r"(?i)(?:"
    r"v\d+(?:\.\d+)*"
    r"|version[-_]?\d+(?:[._-]?\w+)*"
    r"|rev[-_]?\w+"
    r"|r\d+"
    r"|\b(?:final|draft|latest|old|copy|backup|tmp)\b"
    r"|(?<!\d)\d{8}(?!\d)"
    r"|(?<!\d)\d{4}[-_]\d{1,2}[-_]\d{1,2}(?!\d)"
    r")"
)
SEPARATORS = re.compile(r"[\s._-]+")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jdump(obj) -> str:
    """确定性 JSON 序列化。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def write_json(path, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding=UTF8) as f:
        f.write(jdump(obj))
    # 结尾换行便于 diff
    with open(path, "a", encoding=UTF8) as f:
        f.write("\n")


def load_json(path):
    """读 JSON，返回 (obj | None, error | None)。坏 JSON 不抛异常。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: str):
    """按 utf-8-sig 读文本，失败降级到 latin-1（尽量保留字节），返回 (text, encoding)。"""
    for enc in (UTF8, "utf-8-sig", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding=enc, errors="replace") as f:
        return f.read(), "latin-1(replace)"


def sniff_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    # 无扩展名或多义扩展名按魔数嗅探
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        for magic, kind in MAGIC:
            if head.startswith(magic):
                return kind
        # 可打印比例高则视为文本
        printable = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
        if printable >= max(1, len(head) - 2):
            return "text"
    except OSError:
        pass
    return "unknown"


def _frontmatter(text: str):
    """提取 Markdown YAML frontmatter 的 title/author/date/tags，失败返回空 dict。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end]
    data = {}
    for raw in block.splitlines():
        if ":" not in raw:
            continue
        k, _, v = raw.partition(":")
        data[k.strip().lower()] = v.strip().strip("\"'")
    return {
        "title": data.get("title"),
        "author": data.get("author"),
        "date": data.get("date"),
        "tags": [t.strip() for t in (data.get("tags") or "").strip("[]（）() ").split(",") if t.strip()]
        or None,
    }


def _permissions(st) -> dict:
    return {
        "mode_octal": format(st.st_mode & 0o777, "o"),
        "readable": bool(st.st_mode & 0o444),
        "writable": bool(st.st_mode & 0o222),
        "executable": bool(st.st_mode & 0o111),
        "uid": getattr(st, "st_uid", None),
        "gid": getattr(st, "st_gid", None),
    }


def _mtime(st) -> str:
    return datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_stem(name: str) -> str:
    stem, _ext = os.path.splitext(name)
    cleaned = VERSION_PATTERN.sub(" ", stem)
    cleaned = SEPARATORS.sub(" ", cleaned).strip().lower()
    return cleaned if cleaned else stem.lower()


def iter_files(root: str):
    """确定性遍历：忽略隐藏文件，按相对路径排序。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((full, rel))
    return sorted(out, key=lambda p: p[1])


def extract_pdf_text(path: str):
    """用 pdftotext 提取逐页文本。返回 (pages: list[str], error | None)。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return [], "pdftotext 不可用：PDF 无文本层时标 unreadable/needs_ocr"
    try:
        proc = subprocess.run(
            [exe, "-layout", "-enc", UTF8, path, "-"],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            return [], (proc.stderr or b"").decode("utf-8", "replace")
        raw = proc.stdout.decode(UTF8, "replace")
        pages = raw.split("\f")
        return [p.strip() for p in pages], None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def file_record(full, rel, source) -> dict:
    st = os.stat(full)
    kind = sniff_type(full)
    ftype = kind
    rec = {
        "path": rel,
        "size": st.st_size,
        "sha256": sha256_file(full),
        "type": ftype,
        "ext": os.path.splitext(full)[1].lower(),
        "mtime": _mtime(st),
        "permissions": _permissions(st),
        "source": source,
    }
    if kind == "text":
        text, enc = read_text(full)
        rec["text_encoding"] = enc
        rec["line_count"] = len(text.splitlines())
        if rel.lower().endswith(".md"):
            fm = _frontmatter(text)
            rec["metadata"] = {k: v for k, v in fm.items() if v is not None}
        else:
            rec["metadata"] = {}
    else:
        rec["text_encoding"] = None
        rec["metadata"] = {}
    return rec


def build_inventory(input_dir: str, work_dir: str) -> dict:
    summary = {
        "file_types": {},
        "metadata_fields": {},
        "directory_tree": [],
        "duplicate_groups": [],
        "version_families": [],
        "permissions_and_sources": [],
    }
    files = []
    degradations = []
    by_hash = {}
    by_base = {}
    empty_files = []
    pdf_pages = {}

    for full, rel in iter_files(input_dir):
        try:
            rec = file_record(full, rel, "task_input")
        except OSError as exc:
            degradations.append(
                {"file": rel, "level": "unreadable", "reason": str(exc)}
            )
            continue
        files.append(rec)
        key = (rec["path"], rec["sha256"])
        summary["permissions_and_sources"].append(
            {
                "path": rec["path"],
                "mode_octal": rec["permissions"]["mode_octal"],
                "readable": rec["permissions"]["readable"],
                "writable": rec["permissions"]["writable"],
                "uid": rec["permissions"]["uid"],
                "gid": rec["permissions"]["gid"],
                "source": rec["source"],
            }
        )

        # 文件类型统计
        summary["file_types"][rec["type"]] = summary["file_types"].get(rec["type"], 0) + 1

        # 元数据字段覆盖情况
        if rec["type"] == "text":
            md = rec.get("metadata", {})
            for field in ("title", "author", "date", "tags"):
                if md.get(field):
                    summary["metadata_fields"].setdefault(field, []).append(rec["path"])

        # 重复文件：按 SHA256 分组（空文件单独列，不并入哈希组）
        if rec["size"] == 0:
            empty_files.append(rec["path"])
        else:
            by_hash.setdefault(rec["sha256"], []).append(rec)

        # 版本家族：按“目录 + 去版本基名”分组
        base = _base_stem(os.path.basename(rel))
        dirname = os.path.dirname(rel)
        by_base.setdefault((dirname, base), []).append(rec)

        # PDF 逐页文本缓存，供 finalize 校验 quote
        if rec["type"] == "pdf":
            pages, err = extract_pdf_text(full)
            pdf_pages[rel] = {"pages": pages, "error": err}
            if err:
                degradations.append(
                    {"file": rel, "level": "unreadable", "reason": err,
                     "suggestion": "needs_ocr"}
                )
            else:
                cache = os.path.join(work_dir, "pdf_pages", rec["sha256"] + ".json")
                write_json(cache, {"path": rel, "pages": pages, "sha256": rec["sha256"]})

    # 目录树
    dirs = {}
    for dirpath, _dirnames, _filenames in os.walk(input_dir):
        if os.path.relpath(dirpath, input_dir) == ".":
            rel = "."
        else:
            rel = os.path.relpath(dirpath, input_dir).replace(os.sep, "/")
        try:
            dirs[rel] = _mtime(os.stat(dirpath))
        except OSError:
            dirs[rel] = None
    summary["directory_tree"] = sorted(
        ({"path": k, "mtime": v} for k, v in dirs.items()),
        key=lambda d: d["path"],
    )

    # 重复组
    for h, group in sorted(by_hash.items()):
        if len(group) > 1:
            summary["duplicate_groups"].append(
                {"sha256": h, "paths": sorted(g["path"] for g in group)}
            )
    if empty_files:
        summary["duplicate_groups"].append(
            {"sha256": None, "note": "empty files", "paths": sorted(empty_files)}
        )

    # 版本家族：同一基名但不同哈希视为版本差异，需保留全部成员
    for (_d, _b), group in sorted(by_base.items()):
        distinct = {}
        base_path = None
        for g in group:
            distinct.setdefault(g["sha256"], g["path"])
            if base_path is None:
                base_path = g["path"]
        if len(distinct) > 1:
            summary["version_families"].append(
                {
                    "base": {"path": base_path, "sha256": group[0]["sha256"]},
                    "members": sorted(
                        (
                            {"path": g["path"], "sha256": g["sha256"], "size": g["size"],
                             "mtime": g["mtime"]}
                            for g in group
                        ),
                        key=lambda x: x["path"],
                    ),
                }
            )
    summary["duplicate_groups"].sort(key=lambda d: d["sha256"] or "")
    summary["version_families"].sort(key=lambda v: v["base"]["path"])
    summary["file_types"] = dict(sorted(summary["file_types"].items()))
    summary["metadata_fields"] = {
        k: sorted(set(v)) for k, v in sorted(summary["metadata_fields"].items())
    }

    return {
        "summary": summary,
        "files": files,
        "degradations": degradations,
        "pdf_pages": {
            rel: {"pages": v["pages"], "error": v["error"]} for rel, v in pdf_pages.items()
        },
    }


def echo_contract(contract):
    """把 output_contract 的键原样回填；未知类型给 null，不改名、不删键。"""
    out = {}
    if isinstance(contract, dict):
        for k in contract:
            out[k] = None
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                out[item] = None
            elif isinstance(item, dict):
                out.update({k: None for k in item})
    return sort_by_contract_order(out, contract)


def sort_by_contract_order(filled, contract):
    """尽量保持契约原始 key 顺序。"""
    ordered = {}
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(item.keys())
    for k in keys:
        if k in filled:
            ordered[k] = filled[k]
    for k in filled:
        ordered.setdefault(k, filled[k])
    return ordered


def make_change_log(log, action, status, **extra):
    entry = {"ts": _utcnow(), "step": action, "status": status}
    entry.update({k: v for k, v in extra.items() if v is not None})
    log.append(entry)
    return log


def scaffold(input_dir: str, output_dir: str, work_dir: str) -> dict:
    """阶段 0：3 分钟内落盘一版合法 partial 骨架，之后只做增量改进。"""
    log = []
    errors = []
    degradations = []

    case_raw, case_err = load_json(os.path.join(input_dir, "case.json"))
    if case_err:
        errors.append({"step": "load case.json", "reason": case_err})
    case = case_raw if isinstance(case_raw, dict) else {}

    case_id = (
        case.get("case_id")
        or case.get("id")
        or case.get("challenge_id")
        or "unknown"
    )
    contract = case.get("output_contract") or case.get("outputContract") or None
    echoed = echo_contract(contract) if contract else {}

    read_ok = os.path.isfile(os.path.join(input_dir, "task.md"))
    if not read_ok:
        degradations.append({"step": "task.md", "reason": "missing"})

    result = {
        "case_id": str(case_id),
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": make_change_log(
            log, "scaffold", "ok", phase="0-skeleton"
        ),
        "errors": errors,
        "degradations": degradations,
        "runtime": {
            "started_utc": _utcnow(),
            "budget_seconds": 900,
            "budget_requests": 64,
            "budget_tokens_per_response": 16384,
        },
    }
    result.update(echoed)

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(
        os.path.join(work_dir, "case_summary.json"),
        {
            "case_id": result["case_id"],
            "contract_keys": sorted(echoed.keys())
            if isinstance(echoed, dict)
            else list(echoed),
            "contract_raw": contract,
            "input_ok": read_ok,
        },
    )
    return result


def inventory(input_dir: str, work_dir: str) -> dict:
    """阶段 1：盘点六维度，写 inventory.json。"""
    inv = build_inventory(input_dir, work_dir)
    write_json(os.path.join(work_dir, "inventory.json"), inv)
    write_json(os.path.join(work_dir, "evidence_index.json"), _evidence_index(inv))
    return inv


def _evidence_index(inv: dict) -> dict:
    """给 Agent 检索用的可定位索引：每条含 path/sha256/type/locator 规则。"""
    index = {"by_path": {}, "by_sha256": {}}
    for f in inv["files"]:
        entry = {
            "path": f["path"],
            "sha256": f["sha256"],
            "type": f["type"],
            "size": f["size"],
            "line_count": f.get("line_count"),
            "locator_hint": (
                f["path"] + "#L<start>-L<end>"
                if f["type"] == "text"
                else (f["path"] + "#p<page>" if f["type"] == "pdf" else f["path"])
            ),
        }
        index["by_path"][f["path"]] = entry
        index["by_sha256"][f["sha256"]] = entry
    return index


def verify_quote(evid: dict, input_dir: str, work_dir: str) -> dict:
    """回读原文件校验 quote 是否逐字命中，返回错误列表（空=通过）。"""
    errs = []
    file_path = evid.get("file")
    quote = evid.get("quote")
    locator = evid.get("locator", "")
    if not file_path or not quote:
        return ["证据缺 file 或 quote"]
    full = os.path.join(input_dir, file_path)
    if not os.path.isfile(full):
        return ["文件不存在：" + file_path]
    kind = sniff_type(full)

    if kind == "text":
        text, _ = read_text(full)
        lines = text.splitlines()
        m = re.search(r"#L(\d+)(?:-L(\d+))?", locator)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            window = "\n".join(lines[max(0, start - 1):end])
        else:
            window = text
        if quote not in window and quote not in text:
            errs.append("quote 未在 locator 指定文本中逐字命中")
    elif kind == "pdf":
        # 从 inventory 阶段缓存的逐页文本中核对
        cache_map = _load_pdf_cache_map(work_dir)
        key = evid.get("sha256")
        pages = cache_map.get(key, [])
        m = re.search(r"#p(\d+)", locator)
        if m:
            page_no = int(m.group(1))
            if 1 <= page_no <= len(pages):
                pages = [pages[page_no - 1]]
            else:
                errs.append("页码超出范围：" + locator)
        hay = "\n".join(pages)
        if quote not in hay:
            errs.append("quote 未在 PDF 缓存文本中命中（或需要 OCR）")
    else:
        # 不可检索类型：默认通过但标注
        evid.setdefault("verification", "skipped_non_text")
    return errs


def _load_pdf_cache_map(work_dir: str) -> dict:
    cache_dir = os.path.join(work_dir, "pdf_pages")
    out = {}
    if not os.path.isdir(cache_dir):
        return out
    for fn in sorted(os.listdir(cache_dir)):
        if not fn.endswith(".json"):
            continue
        obj, err = load_json(os.path.join(cache_dir, fn))
        if err is None and isinstance(obj, dict):
            out[obj.get("sha256")] = obj.get("pages", [])
    return out


def collect_sources(inv: dict) -> list:
    return [
        {
            "path": f["path"],
            "sha256": f["sha256"],
            "type": f["type"],
            "size": f["size"],
            "version_marker": _version_marker(f["path"]),
        }
        for f in inv["files"]
    ]


def _version_marker(path: str) -> str:
    m = VERSION_PATTERN.search(os.path.basename(path))
    return m.group(0) if m else None


def det_status(errors, degradations, answers, files) -> str:
    if not files:
        return "insufficient_evidence"
    if errors:
        return "input_error"
    if degradations and not answers:
        return "partial"
    if any(a.get("uncertainty") == "insufficient_evidence" for a in answers):
        return "partial"
    return "ok"


def finalize(input_dir: str, output_dir: str, work_dir: str, answers_path: str) -> dict:
    """阶段 3：合并 answers + inventory → 最终 result.json，并做证据回读校验。"""
    case_raw, case_err = load_json(os.path.join(input_dir, "case.json"))
    case = case_raw if isinstance(case_raw, dict) else {}
    case_id = case.get("case_id") or case.get("id") or case.get("challenge_id") or "unknown"
    contract = case.get("output_contract") or case.get("outputContract") or None
    echoed = echo_contract(contract) if contract else {}

    inv_path = os.path.join(work_dir, "inventory.json")
    inv_raw, inv_err = load_json(inv_path)
    inv = inv_raw if isinstance(inv_raw, dict) else {"files": [], "summary": {}, "degradations": []}

    ans_raw, ans_err = load_json(answers_path)
    answers = (ans_raw or {}).get("answers", []) if isinstance(ans_raw, dict) else []
    output_map = (ans_raw or {}).get("output", {}) if isinstance(ans_raw, dict) else {}
    uncertainty_extra = (ans_raw or {}).get("uncertainty", []) if isinstance(ans_raw, dict) else []

    # 回填契约字段：Agent 提供的 output_map 覆盖 null 默认值
    for k in echoed:
        if k in output_map and output_map[k] is not None:
            echoed[k] = output_map[k]

    failures_recovery = []
    for a in answers:
        for evid in a.get("evidence", []):
            errs = verify_quote(evid, input_dir, work_dir)
            evid["verified"] = not errs
            if errs:
                failures_recovery.append(
                    {"file": evid.get("file"), "locator": evid.get("locator"),
                     "quote": evid.get("quote"), "reason": errs}
                )

    errors = []
    if case_err:
        errors.append({"step": "load case.json", "reason": case_err})
    if inv_err:
        errors.append({"step": "load inventory.json", "reason": inv_err})
    if ans_err:
        errors.append({"step": "load answers.json", "reason": ans_err})
    degradations = list(inv.get("degradations", []))

    status = det_status(errors, degradations, answers, inv.get("files", []))

    result = {
        "case_id": str(case_id),
        "status": status,
        "sources": collect_sources(inv) if inv_raw else [],
        "answers": answers,
        "library": inv.get("summary", {}) if inv_raw else {},
        "uncertainty": uncertainty_extra,
        "pending_human": [
            {"file": d.get("file"), "reason": d.get("reason"), "suggestion": d.get("suggestion")}
            for d in degradations
        ],
        "failures_recovery": failures_recovery,
        "errors": errors,
        "degradations": degradations,
        "runtime": {
            "finished_utc": _utcnow(),
            "budget_seconds": 900,
            "budget_requests": 64,
            "budget_tokens_per_response": 16384,
        },
        "change_log": make_change_log([], "finalize", "ok", phase="3-merge"),
    }
    result.update(echoed)

    os.makedirs(output_dir, exist_ok=True)
    write_json(os.path.join(output_dir, "result.json"), result)
    # 附带可读配套产物
    write_json(os.path.join(output_dir, "evidence_map.json"), _evidence_map(answers))
    return result


def _evidence_map(answers: list) -> dict:
    flat = []
    for a in answers:
        for evid in a.get("evidence", []):
            flat.append(
                {
                    "question": a.get("question", ""),
                    "answer": a.get("answer", ""),
                    "file": evid.get("file"),
                    "sha256": evid.get("sha256"),
                    "locator": evid.get("locator"),
                    "quote": evid.get("quote"),
                    "confidence": evid.get("confidence"),
                    "verified": evid.get("verified", False),
                }
            )
    return {"evidence": flat}