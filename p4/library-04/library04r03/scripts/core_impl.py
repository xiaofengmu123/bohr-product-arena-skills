"""core_impl.py — shared, deterministic implementation for the research-KB QA skill.

Design goals
------------
* Pure Python standard library only (no third-party packages).
* Deterministic: for the same input bytes, every content-derived field
  (`sources`, `library`, `answers[].evidence`, `uncertainty`, the six-dimension
  summaries) is byte-for-byte identical across runs.  Only wall-clock fields
  (`change_log[].time`, `runtime.*`) vary, and they are isolated so a reviewer
  can diff the rest.
* No fabrication: the pipeline records observable facts (paths, hashes, sizes,
  permissions, verbatim byte/line content).  It never invents author names,
  DOIs, page numbers, or answers.  Content-level answers are produced by the
  calling agent and merged unchanged in the finalize phase.

Phases
------
* scaffold  — read case.json/task.md, write the partial result skeleton first.
* inventory — walk the input tree, build the six-dimension library + index.
* qa        — extract questions and write honest retrieval candidates
              (uncertainty=insufficient_evidence until the agent fills answers).
* finalize  — merge inventory + answers into /app/output/result.json plus the
              editable artifacts inventory.md and evidence_map.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# constants / defaults
# --------------------------------------------------------------------------
TEXT_EXT = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".csv", ".tsv", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".tex", ".bib",
    ".py", ".log", ".xml", ".html", ".htm", ".rtf",
}
PDF_EXT = {".pdf"}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt",
              ".ods", ".odp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
             ".svg", ".webp"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
               ".zst"}

MAX_TEXT_BYTES = 2 * 1024 * 1024      # 2 MiB per text file
MAX_QUOTE_CHARS = 220                 # verbatim quote truncation limit
DEFAULT_ROOT = {"input": "/input", "output": "/app/output", "work": "/app/work"}

_VERSION_RE = re.compile(
    r"(?i)[\s._\-]?(?:v\d+(?:\.\d+)*|version[\s._\-]?\d+(?:\.\d+)*"
    r"|rev[\s._\-]?\d+|r\d+|final|draft[\s._\-]?\d*|dated|old|new|latest"
    r"|copy\s*\d+|\d{4}[\-_.]?\d{1,2}[\-_.]?\d{1,2}|\d{8})$"
)

# Task metadata files are the runtime contract, not knowledge-base material.
TASK_META_BASENAMES = {"case.json", "task.md"}

UTF8_SIG = ("utf-8-sig", "utf-8", "latin-1")


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def now_iso() -> str:
    """Wall-clock timestamp.  Isolated because it is the one non-replayable bit."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def log_entry(step: str, action: str, status: str) -> dict:
    return {"step": step, "time": now_iso(), "action": action,
            "status": status}


def sha256_file(path: str) -> str | None:
    """Hex SHA-256 of file bytes, or None if unreadable."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def safe_json(path: str):
    """Load JSON, returning (data, error).  Never raises."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001 - must never crash the pipeline
        return None, f"{type(exc).__name__}: {exc}"


def write_json(path: str, obj) -> None:
    """Deterministic JSON writer: sorted keys, stable separators, UTF-8."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.write("\n")


def normalize_id(value) -> str | None:
    if isinstance(value, dict):
        for key in ("challenge_id", "case_id", "id"):
            if key in value and value[key]:
                return str(value[key])
        return None
    if value:
        return str(value)
    return None


# --------------------------------------------------------------------------
# input discovery and per-file facts (all observable, deterministic)
# --------------------------------------------------------------------------
def sniff_type(name: str, head: bytes) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in OFFICE_EXT:
        return "office"
    if ext in IMAGE_EXT:
        return "image"
    if ext in ARCHIVE_EXT:
        return "archive"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        # ZIP container: could be docx/xlsx/xlsx/archive — keep extension truth.
        return "office" if ext in OFFICE_EXT else "archive"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if head[:2] in (b"\xff\xd8", b"GIF", b"BM"):
        return "image"
    if ext in TEXT_EXT or b"\x00" not in head[:4096]:
        return "text"
    return "unknown"


def read_text(path: str):
    """Best-effort text read.  Returns (text, encoding, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_TEXT_BYTES + 1)
    except OSError as exc:
        return None, None, f"{type(exc).__name__}: {exc}"
    truncated = len(raw) > MAX_TEXT_BYTES
    raw = raw[:MAX_TEXT_BYTES]
    if b"\x00" in raw[:4096]:
        return None, None, "binary_content_sniffed"
    for enc in UTF8_SIG:
        try:
            text = raw.decode(enc)
            return (text, enc, "truncated" if truncated else None)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", (
        "fallback_latin1" + (";truncated" if truncated else ""))


def extract_frontmatter(text: str) -> dict:
    """Extract a YAML-like frontmatter block from Markdown/TeX style files.

    Only the common scalar fields are captured; the raw block is kept verbatim
    so a reviewer can confirm no rewriting happened.
    """
    meta: dict = {}
    if not text:
        return meta
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text,
                 re.DOTALL)
    if not m:
        return meta
    raw = m.group(1)
    meta["frontmatter_raw"] = raw[:MAX_QUOTE_CHARS]
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        if key in ("title", "author", "authors", "date", "tags", "doi",
                   "version", "license", "status"):
            meta[key] = value[:MAX_QUOTE_CHARS]
    return meta


def discover_inputs(input_dir: str):
    """Walk input_dir, return sorted list of file fact dicts."""
    files: list[dict] = []
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            if name in TASK_META_BASENAMES:
                continue
            full = os.path.join(root, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                st = os.stat(full)
            except OSError:
                continue
            head = b""
            try:
                with open(full, "rb") as fh:
                    head = fh.read(64)
            except OSError:
                head = b""
            sha = sha256_file(full) if os.access(full, os.R_OK) else None
            entry = {
                "relpath": rel,
                "abspath": full,
                "bytes": st.st_size,
                "mtime": int(st.st_mtime),
                "mode_octal": format(st.st_mode & 0o777, "03o"),
                "readable": bool(os.access(full, os.R_OK)),
                "writable": bool(os.access(full, os.W_OK)),
                "sha256": sha,
                "type": sniff_type(name, head),
                "source": "task_input",
            }
            files.append(entry)
    return files


VERSION_BASE_CACHE: dict = {}


def version_base(name: str):
    """Return (normalized_base, has_marker) for version-family grouping."""
    if name in VERSION_BASE_CACHE:
        return VERSION_BASE_CACHE[name]
    stem, _ext = os.path.splitext(name)
    m = _VERSION_RE.search(stem)
    has_marker = False
    base = stem
    if m:
        has_marker = True
        base = stem[:m.start()].rstrip(" ._-")
    base = re.sub(r"[\s_.\-]+", "_", base).strip("_").lower() or stem.lower()
    VERSION_BASE_CACHE[name] = (base, has_marker)
    return base, has_marker


# --------------------------------------------------------------------------
# directory tree
# --------------------------------------------------------------------------
def build_tree(relpaths: list[str]) -> dict:
    dirs: dict = {}
    for rp in sorted(relpaths):
        parts = rp.split("/")
        parent = "/".join(parts[:-1]) or "."
        dirs.setdefault(parent, []).append(parts[-1])
    return {
        "root": ".",
        "directories": [
            {"path": d, "entries": sorted(names)}
            for d, names in sorted(dirs.items())
        ],
    }


# --------------------------------------------------------------------------
# inventory = six-dimension library
# --------------------------------------------------------------------------
def build_inventory(input_dir: str, errors: list, degradations: list) -> dict:
    files = discover_inputs(input_dir)
    errors = errors if errors is not None else []
    degradations = degradations if degradations is not None else []

    # 1. file types
    type_counts: dict = {}
    for f in files:
        type_counts[f["type"]] = type_counts.get(f["type"], 0) + 1

    # 2. metadata fields (frontmatter + size/mtime/encoding)
    for f in files:
        f["metadata"] = {"size_bytes": f["bytes"], "mtime": f["mtime"],
                         "readable": f["readable"], "writable": f["writable"]}
        f["text_encoding"] = None
        f["text_error"] = None
        if f["type"] == "text":
            text, enc, err = read_text(f["abspath"])
            f["text_encoding"] = enc
            f["text_error"] = err
            if err:
                degradations.append(
                    {"file": f["relpath"], "kind": "text_degraded",
                     "detail": err})
            f["metadata"].update(extract_frontmatter(text or ""))
        elif f["type"] == "pdf":
            text, perr = pdf_text(f["abspath"])
            if perr:
                f["pdf_error"] = perr
                f["needs_ocr_or_pdftotext"] = True
                degradations.append(
                    {"file": f["relpath"], "kind": "pdf_text_unavailable",
                     "detail": perr})
            else:
                pages = text.count("\f") + 1 if text else 0
                f["pdf_pages"] = pages
                f["pdf_preview"] = text[:500]
                f["needs_ocr_or_pdftotext"] = False

    # 3. directory tree + 6. permission/source are already on each file dict
    tree = build_tree([f["relpath"] for f in files])

    # 4. duplicate groups by SHA256
    by_hash: dict = {}
    empty_files: list[str] = []
    for f in files:
        if f["sha256"] is None:
            continue
        if f["bytes"] == 0:
            empty_files.append(f["relpath"])
            continue
        by_hash.setdefault(f["sha256"], []).append(f["relpath"])
    duplicate_groups = [
        {"sha256": h, "paths": sorted(ps)}
        for h, ps in sorted(by_hash.items()) if len(ps) >= 2
    ]

    # 5. version families
    by_base: dict = {}
    for f in files:
        name = f["relpath"].rsplit("/", 1)[-1]
        base, has_marker = version_base(name)
        by_base.setdefault(base, []).append({**{k: f[k] for k in
            ("relpath", "sha256", "bytes", "mtime")},
            "filename": name, "version_marker": has_marker})
    version_families = []
    for base, members in sorted(by_base.items()):
        flagged = any(m["version_marker"] for m in members)
        unique_hashes = {m["sha256"] for m in members if m["sha256"]}
        if len(members) >= 2 or flagged or len(unique_hashes) > 1:
            version_families.append({
                "base": base,
                "members": sorted(members, key=lambda m: m["relpath"]),
                "distinct_hashes": len(unique_hashes),
            })

    # permission summary
    perms = {
        "total": len(files),
        "readable": sum(1 for f in files if f["readable"]),
        "writable": sum(1 for f in files if f["writable"]),
        "modes": sorted({f["mode_octal"] for f in files}),
    }

    return {
        "generated_at": now_iso(),
        "input_dir": input_dir,
        "summary": {
            "file_count": len(files),
            "total_bytes": sum(f["bytes"] for f in files),
            "file_types": type_counts,
            "duplicate_groups": len(duplicate_groups),
            "version_families": len(version_families),
            "empty_files": len(empty_files),
            "permissions": perms,
        },
        "files": files,
        "directory_tree": tree,
        "duplicate_groups": duplicate_groups,
        "empty_files": sorted(empty_files),
        "version_families": version_families,
    }


# --------------------------------------------------------------------------
# questions + retrieval (no answer fabrication)
# --------------------------------------------------------------------------
def extract_questions(case, task_text: str) -> list:
    """Collect question items; returns list of {id, text}."""
    out: list = []
    if isinstance(case, dict):
        raw = None
        for key in ("questions", "queries", "qa", "asks"):
            if key in case:
                raw = case[key]
                break
        seen = 0
        for item in (raw if isinstance(raw, list) else [raw]):
            if isinstance(item, str) and item.strip():
                seen += 1
                out.append({"id": seen, "text": item.strip()})
            elif isinstance(item, dict):
                seen += 1
                text = item.get("question") or item.get("text") or item.get(
                    "query") or ""
                if text:
                    out.append({"id": item.get("id", seen),
                                "text": str(text).strip()})
    return out


def tokenize(text: str) -> set:
    return {t for t in re.findall(r"[0-9a-z\u4e00-\u9fff]{2,}",
                                   text.lower())}


def retrieve_candidates(files: list, question: str, limit: int = 6):
    """Deterministic lexical retrieval over text files only.

    Returns a sorted list of {file, sha256, locator, quote, score}.  This is a
    recall aid, not an answer: the calling agent must verify each quote before
    promoting it from a candidate to evidence.
    """
    q_tokens = tokenize(question)
    candidates: list = []
    for f in files:
        if f["type"] != "text" or not f.get("text_encoding"):
            continue
        text, _enc, _err = read_text(f["abspath"])
        if text is None:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            l_tokens = tokenize(stripped)
            overlap = len(q_tokens & l_tokens)
            if overlap > 0:
                quote = stripped[:MAX_QUOTE_CHARS]
                candidates.append({
                    "file": f["relpath"],
                    "sha256": f["sha256"],
                    "locator": f"{f['relpath']}#L{idx}",
                    "quote": quote,
                    "score": overlap,
                })
    candidates.sort(key=lambda c: (-c["score"], c["file"], c["locator"]))
    return candidates[:limit]


# --------------------------------------------------------------------------
# finalize: merge everything into the result
# --------------------------------------------------------------------------
def derive_status(files_count: int, answers: list, errors: list,
                  degradations: list) -> str:
    case_bad = any(isinstance(e, str) and e.startswith("case.json:")
                   for e in errors)
    if case_bad and files_count == 0:
        # corrupt contract AND nothing to salvage -> input_error
        return "input_error"
    if files_count == 0:
        # readable (or missing) contract but no material -> insufficient evidence
        return "insufficient_evidence"
    if not answers:
        return "partial"
    if case_bad:
        # materials were processed despite a corrupt contract: be honest
        return "partial"
    levels = {a.get("uncertainty") for a in answers if isinstance(a, dict)}
    if levels and levels <= {"certain", "likely"} and not degradations:
        return "ok"
    return "partial"


def finalize(case_summary: dict, inventory: dict, answers: dict,
             errors: list, degradations: list) -> dict:
    case_id = case_summary.get("case_id") or "unknown"
    contract = case_summary.get("output_contract") or {}

    # Echo output_contract keys verbatim at top level (missing -> null/[]/{}),
    # then layer our standard fields on top so our keys win any collision.
    echoed: dict = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            if v is None:
                echoed[k] = None
            elif isinstance(v, (list, dict)):
                echoed[k] = v
            else:
                echoed[k] = v
    elif isinstance(contract, list) and contract:
        echoed = {"output_contract_items": contract}

    answer_list = answers.get("answers") or [] if isinstance(answers, dict) \
        else []
    files = inventory.get("files", [])
    status = derive_status(len(files), answer_list, errors, degradations)

    pending_human = []
    for a in answer_list:
        if a.get("uncertainty") in ("insufficient_evidence", "unreadable",
                                    "conflict") and not a.get("answer"):
            pending_human.append({
                "item": a.get("question", ""),
                "why": a.get("missing") or a.get("uncertainty"),
            })

    # 失败与恢复记录：跳过/冲突/无权限/重复/恢复（对应交付要求）
    failures_recovery = []
    for f in files:
        if not f.get("readable"):
            failures_recovery.append({"kind": "permission_denied",
                                      "file": f["relpath"],
                                      "action": "skipped"})
        elif f.get("sha256") is None:
            failures_recovery.append({"kind": "hash_unavailable",
                                      "file": f["relpath"],
                                      "action": "skipped"})
    for dg in inventory.get("duplicate_groups", []):
        failures_recovery.append({"kind": "duplicate",
                                  "files": dg["paths"],
                                  "action": "grouped_not_merged"})
    for e in errors:
        failures_recovery.append({"kind": "input_error", "detail": e,
                                  "action": "recorded"})
    for d in degradations:
        failures_recovery.append({"kind": d.get("kind", "degradation"),
                                  "file": d.get("file"),
                                  "detail": d.get("detail"),
                                  "action": "recorded"})
    for u in (answers.get("uncertainty") or [] if isinstance(answers, dict)
              else []):
        if isinstance(u, dict) and u.get("level") == "conflict":
            failures_recovery.append({"kind": "source_conflict",
                                      "item": u.get("item"),
                                      "sources": u.get("sources"),
                                      "action": "both_recorded"})

    result = {
        "case_id": case_id,
        "status": status,
        "output_contract": contract,
        "answers": answer_list,
        "library": {
            "summary": inventory.get("summary", {}),
            "directory_tree": inventory.get("directory_tree", {}),
            "duplicate_groups": inventory.get("duplicate_groups", []),
            "version_families": inventory.get("version_families", []),
            "empty_files": inventory.get("empty_files", []),
        },
        "sources": sorted(
            [{"relpath": f["relpath"], "sha256": f["sha256"],
              "type": f["type"], "size": f["bytes"],
              "mode_octal": f["mode_octal"], "readable": f["readable"],
              "source": f["source"], "metadata": f["metadata"],
              "text_encoding": f.get("text_encoding")} for f in files],
            key=lambda s: s["relpath"]),
        "uncertainty": answers.get("uncertainty") or [] if isinstance(
            answers, dict) else [],
        "pending_human": pending_human,
        "failures_recovery": failures_recovery,
        "errors": list(errors),
        "degradations": list(degradations),
    }
    # layer echoed contract fields (never overwrite our fixed keys)
    for k, v in echoed.items():
        if k not in result:
            result[k] = v
    return result


def render_inventory_md(inventory: dict) -> str:
    s = inventory.get("summary", {})
    lines = [
        "# 科研知识库清单（六维度）",
        "",
        f"- 生成时间：{inventory.get('generated_at')}",
        f"- 文件总数：{s.get('file_count')}；总字节：{s.get('total_bytes')}",
        f"- 文件类型：{json.dumps(s.get('file_types'), ensure_ascii=False)}",
        f"- 重复组：{s.get('duplicate_groups')}；版本族："
        f"{s.get('version_families')}；空文件：{s.get('empty_files')}",
        "",
        "## 版本族（version_families）",
    ]
    for vf in inventory.get("version_families", []):
        lines.append(f"- `{vf['base']}`（去重哈希数 {vf['distinct_hashes']}）")
        for m in vf["members"]:
            lines.append(f"  - {m['relpath']}  sha256={m['sha256'][:16]}"
                         f"{'  [version]' if m['version_marker'] else ''}")
    lines.append("")
    lines.append("## 重复文件（duplicate_groups）")
    for dg in inventory.get("duplicate_groups", []):
        lines.append(f"- sha256={dg['sha256'][:16]} → {', '.join(dg['paths'])}")
    if not inventory.get("duplicate_groups"):
        lines.append("- 无")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# optional PDF text via pdftotext (graceful degradation)
# --------------------------------------------------------------------------
def pdf_text(path: str):
    """Best-effort pdftotext.  Returns (text, error)."""
    for tool in ("pdftotext",):
        try:
            proc = subprocess.run(
                [tool, "-layout", "-enc", "UTF-8", path, "-"],
                capture_output=True, timeout=30)
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout.decode("utf-8", errors="replace"), None
            return None, f"{tool} exit {proc.returncode}"
        except FileNotFoundError:
            return None, "pdftotext_not_found"
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    return None, "no_pdf_tool"