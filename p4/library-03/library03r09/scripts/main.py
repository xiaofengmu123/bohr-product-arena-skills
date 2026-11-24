#!/usr/bin/env python3
"""main.py — library-organizer 编排入口：读契约 → 先落骨架 → 盘点 → 抽取 → 语义 → 归位。

仅依赖 Python 3 标准库（>=3.8）。承诺：
  * 启动后立即写 /app/output/result.json 骨架（status=partial），只做增量改进；
  * 绝不修改 /input，绝不编造作者/年份/DOI/结论；
  * 幂等、可回滚、失败可续跑。

用法：
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
        [--task /input/task.md] [--decisions /app/work/decisions.json] \
        [--hierarchy document_type,topic] [--apply]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

__version__ = "1.0.0"

DEFAULT_CONTRACT = {"library": {}, "evidence": {}, "operations": [], "failures": {}}
EXTRAS = ("status", "sources", "uncertainty", "change_log",
          "errors", "degradations", "pending_human", "runtime")
MAX_TEXT_BYTES = 2 * 1024 * 1024

# ---------------------------------------------------------------- 基础工具

def read_json(path):
    """utf-8-sig → utf-8 容错读取 JSON；返回 (obj, (state, err))。"""
    if not path or not os.path.isfile(path):
        return None, ("missing", None)
    txt, note = read_text(path)
    if txt is None:
        return None, ("unreadable", note)
    try:
        return json.loads(txt), ("ok", None)
    except Exception as e:  # noqa: BLE001
        return None, ("invalid", str(e))


def read_text(path, limit=MAX_TEXT_BYTES):
    """容错读取文本；超限截断。返回 (data, note)。"""
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "rb") as f:
                b = f.read(limit + 1)
            truncated = len(b) > limit
            b = b[:limit]
            return b.decode(enc), ("truncated" if truncated else None)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    return None, last_err


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    n = 0
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                if limit is not None and n + len(b) > limit:
                    h.update(b[: max(0, limit - n)])
                    n = limit
                    break
                h.update(b)
                n += len(b)
        return h.hexdigest(), n, None
    except Exception as e:  # noqa: BLE001
        return None, n, str(e)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def rel_join(*parts):
    return "/".join(p.replace("\\", "/").strip("/") for p in parts if p)


def safe_name(s, maxlen=80):
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = re.sub(r"[\x00-\x1f]", "", s)
    if not s:
        s = "untitled"
    return s[:maxlen].rstrip(" .") or "untitled"


def log_change(change_log, what, **extra):
    change_log.append({"at": now_iso(), "event": what, **extra})


# ---------------------------------------------------------------- 盘点

def _entry(path, base, is_dir=False):
    rel = os.path.relpath(path, base).replace("\\", "/")
    rec = {"source": rel + "/" if is_dir else rel,
           "path": path, "is_dir": is_dir, "size": 0,
           "sha256": None, "permissions": {}, "errors": []}
    try:
        st = os.stat(path)
        rec["permissions"] = {"stat_ok": True,
                              "readable": os.access(path, os.R_OK),
                              "writable": os.access(path, os.W_OK)}
        if not is_dir:
            rec["size"] = st.st_size
    except Exception as e:  # noqa: BLE001
        rec["permissions"] = {"stat_ok": False, "readable": False, "writable": False}
        rec["errors"].append({"kind": "stat", "detail": str(e)})
        rec["size"] = None
    if not is_dir:
        rec["ext"] = os.path.splitext(path)[1].lower()
        if os.access(path, os.R_OK):
            h, n, err = sha256_file(path)
            rec["sha256"] = h
            if err:
                rec["errors"].append({"kind": "hash", "detail": err})
        else:
            rec["errors"].append({"kind": "unreadable", "detail": "permission denied"})
    return rec


def walk_inventory(input_dir):
    entries = []
    for root, dirs, files in os.walk(input_dir):
        for dname in sorted(dirs):
            entries.append(_entry(os.path.join(root, dname), input_dir, is_dir=True))
        for fname in sorted(files):
            entries.append(_entry(os.path.join(root, fname), input_dir, is_dir=False))
    dirs = {e["source"] for e in entries if e.get("is_dir")}
    return entries, dirs


# ---------------------------------------------------------------- 文本抽取

def find_text_tool():
    for name in ("pdftotext", "pdf-text-tool"):
        p = shutil.which(name)
        if p:
            return name, p
    return None, None


def extract_text(path, work_dir, hashes, degradations):
    ext = os.path.splitext(path)[1].lower()
    out_txt = os.path.join(work_dir, "text", (hashes.get(path) or "x") + ".txt")
    os.makedirs(os.path.dirname(out_txt), exist_ok=True)
    if ext == ".pdf":
        name, _ = find_text_tool()
        if not name:
            degradations.append("no PDF text tool; using filename/bibtex only")
            return None, "no_tool"
        try:
            cp = subprocess.run([name, path, "-"], capture_output=True, timeout=60)
            if cp.returncode == 0 and cp.stdout.strip():
                txt = cp.stdout.decode("utf-8", "replace")
                with open(out_txt, "w", encoding="utf-8") as f:
                    f.write(txt)
                return txt, "ok"
            return None, f"exit {cp.returncode}"
        except Exception as e:  # noqa: BLE001
            return None, str(e)
    if ext in (".txt", ".md", ".bib", ".ris", ".csv", ".json", ".xml", ".html"):
        data, note = read_text(path)
        if data is None:
            return None, "unreadable"
        try:
            with open(out_txt, "w", encoding="utf-8") as f:
                f.write(data)
        except OSError:
            pass
        return data, "ok"
    return None, "binary_or_image"


# ---------------------------------------------------------------- 元数据与语义

TOPIC_MAP = {
    "materials": r"materials|alloy|polymer|ceramic|graphene|perovskite|steel|composite",
    "chemistry": r"chem|synthesis|cataly|reaction|molecule|organic|inorganic",
    "biology": r"bio|cell|protein|gene|genome|dna|rna|enzyme|neur",
    "medicine": r"medic|clinical|patient|drug|disease|therapy|cancer|tumor",
    "physics": r"physic|quantum|optics|laser|semiconductor|magnet|superconductor",
    "computer_science": r"comput|algorithm|neural|machine learning|deep learning|software|data.?mining",
    "mathematics": r"math|statistic|probability|topolog|numercal|optimization",
    "engineering": r"engineer|robot|manufactur|mechanical|electrical|civil",
    "energy": r"energy|battery|solar|fuel|photovolta|wind power",
    "earth": r"climate|geolog|seismic|ocean|atmospher|environment",
    "social": r"econom|psycholog|sociolog|education|policy|law",
}
TOPIC_PATTERNS = {k: re.compile(v, re.I) for k, v in TOPIC_MAP.items()}

DOC_PATTERNS = [
    ("supplementary", re.compile(r"\bsupplement|supporting.info|supp\.|SI\b|appendix", re.I)),
    ("preprint", re.compile(r"\bpreprint|arxiv|biorxiv|medrxiv|chemrxiv|ssrn|tech.?report", re.I)),
    ("conference_paper", re.compile(r"\bconference|proceedings|proc\.|icml|neurips|iclr|cvpr|aaai|acl", re.I)),
    ("thesis", re.compile(r"\bthesis|dissertation|ph\.?d|master.?thesis", re.I)),
    ("dataset", re.compile(r"\bdata|dataset|corpus|benchmark|survey", re.I)),
    ("note", re.compile(r"\bnote|readme|memo|minutes", re.I)),
]

JOURNAL_HINTS = re.compile(
    r"\b(journal|j\.|proceedings of|transactions on|review of|nature|science|cell|lancet|"
    r"physical review|journal of the american|annals|ieee|acm|springer|elsevier|wiley)\b", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
ARXIV_RE = re.compile(r"\barxiv[:/\s]*(\d{4}\.\d{4,5})(?:v\d+)?\b", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def extract_bib_fields(text):
    out = []
    for m in re.finditer(r"@(\w+)\s*\{[^@]*?\}", text or "", re.S):
        block = m.group(0)
        entry = {"entry_type": m.group(1).lower()}
        for f in ("title", "author", "year", "journal", "booktitle", "doi", "school"):
            fm = re.search(rf"\b{f}\s*=\s*[{{\"]([^{{}}]*?)[}}\"]", block, re.I)
            if fm:
                entry[f] = re.sub(r"\s+", " ", fm.group(1)).strip()
        if entry.get("title"):
            out.append(entry)
    return out


def normalize_title(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (t or "").lower())


def _bib_matches(text, stem, entry):
    """只让与当前文件内容/文件名相符的 BibTeX 条目参与，避免张冠李戴。"""
    hay = (text or "")[:8000] + " " + stem
    if entry.get("doi") and entry["doi"].lower() in hay.lower():
        return True
    t = normalize_title(entry.get("title"))
    if len(t) >= 12 and t in normalize_title(hay):
        return True
    return False


def guess_document_type(path, text, ext):
    stem = os.path.basename(path)
    if ext in (".csv", ".json", ".xlsx", ".xls"):
        return "dataset"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp"):
        return "scanned"
    hay = stem + " " + (text or "")[:4000]
    for dtype, pat in DOC_PATTERNS:
        if pat.search(hay):
            return dtype
    if ext in (".bib", ".ris"):
        return "bibliography"
    if ext in (".txt", ".md"):
        return "note"
    if ext == ".pdf":
        return "journal_article"
    return "other"


def guess_topic(text, stem):
    hay = stem + " " + (text or "")[:6000]
    best, score = "general", 0
    for k, pat in TOPIC_PATTERNS.items():
        n = len(pat.findall(hay))
        if n > score:
            best, score = k, n
    return best


def extract_metadata(text, stem, bib_entries, is_bib):
    meta = {"title": None, "authors": [], "year": None, "journal": None,
            "doi": None, "arxiv_id": None}
    ev = []
    if is_bib:
        meta["title"] = stem
        meta["note"] = f"bibliography, {len(bib_entries or [])} parsed entries"
        ev.append({"locator": "filename", "quote": stem})
        return meta, ev
    hay = (text or "")[:8000] + " " + stem
    matching = [b for b in (bib_entries or []) if _bib_matches(text, stem, b)]
    b = matching[0] if matching else None
    if b:
        if b.get("title"):
            meta["title"] = b["title"]
            ev.append({"locator": "bibtex", "quote": b["title"][:120]})
        if b.get("author"):
            meta["authors"] = [a.strip() for a in re.split(r"\s+and\s+", b["author"], flags=re.I)]
            ev.append({"locator": "bibtex", "quote": b["author"][:120]})
        if b.get("year"):
            meta["year"] = b["year"]
            ev.append({"locator": "bibtex", "quote": b["year"]})
        if b.get("journal"):
            meta["journal"] = b["journal"]
            ev.append({"locator": "bibtex", "quote": b["journal"][:120]})
        if b.get("school"):
            meta["journal"] = meta["journal"] or b["school"]
        if b.get("doi"):
            meta["doi"] = b["doi"]
    dm = DOI_RE.search(hay)
    if dm and not meta["doi"]:
        meta["doi"] = dm.group(0).rstrip(".,;")
        ev.append({"locator": "text", "quote": dm.group(0)})
    am = ARXIV_RE.search(hay)
    if am and not meta["arxiv_id"]:
        meta["arxiv_id"] = am.group(1)
        ev.append({"locator": "text", "quote": am.group(0)})
    ym = YEAR_RE.search(hay)
    if ym and not meta["year"]:
        meta["year"] = ym.group(1)
        ev.append({"locator": "text", "quote": ym.group(0)})
    jm = JOURNAL_HINTS.search(hay)
    if jm and not meta["journal"]:
        meta["journal"] = jm.group(0)
        ev.append({"locator": "text", "quote": jm.group(0)})
    return meta, ev


# ---------------------------------------------------------------- 版本与命名

class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def version_keys(meta, title):
    keys = []
    if meta.get("doi"):
        keys.append("doi:" + meta["doi"].lower())
    if meta.get("arxiv_id"):
        keys.append("arxiv:" + meta["arxiv_id"].lower())
    t = normalize_title(title)
    if t and meta.get("year"):
        keys.append(f"title:{t}|{meta['year']}")
    return keys


def guess_version_kind(stem, text, meta):
    hay = stem + " " + (text or "")[:3000]
    if re.search(r"\bsupplement|supporting", hay, re.I):
        return "supplementary"
    if meta.get("arxiv_id") or re.search(r"\bpreprint|arxiv|biorxiv|medrxiv", hay, re.I):
        return "preprint"
    if re.search(r"\brevision|rev\.|v\d+", hay, re.I):
        return "revision"
    if re.search(r"\bpublished|accepted|final|version.of.record|vor\b", hay, re.I):
        return "published"
    return "unknown"


def build_name(meta, ext, topic):
    """缺作者又缺年份时不造 'nodate-标题'，返回 None 回退原文件名清洗。"""
    if not meta.get("authors") and not meta.get("year"):
        return None
    parts = []
    if meta.get("authors"):
        parts.append(safe_name(meta["authors"][0].split(",")[0], 40))
    parts.append(str(meta["year"] or "nodate"))
    parts.append(safe_name(meta.get("title") or topic, 50))
    base = "-".join(p for p in parts if p)
    return (base + ext) if base else None


# ---------------------------------------------------------------- 结果骨架

def deep_placeholder(v):
    if isinstance(v, dict):
        return {k: deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def build_result(case_id, contract, data):
    result = {"case_id": case_id}
    for key in contract:
        result[key] = data.get(key) if key in data else deep_placeholder(contract[key])
    for extra in EXTRAS:
        result.setdefault(extra, data.get(extra))
    return result


def empty_data():
    return {
        "library": {"catalog": [], "hierarchy": {}, "metadata": {}, "versions": []},
        "evidence": {"hashes": {}, "permissions": {}, "locators": []},
        "operations": [],
        "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                     "duplicates": [], "recoveries": []},
        "status": "partial",
        "sources": [], "uncertainty": {}, "change_log": [],
        "errors": [], "degradations": [], "pending_human": [], "runtime": {},
    }


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(description="科研资料整理：分类·命名·去重·版本")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--task", default=None)
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--hierarchy", default="document_type,topic")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    data = empty_data()
    degradations, pending = data["degradations"], data["pending_human"]
    changes = data["change_log"]
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    # 0) 读题
    case, err = read_json(os.path.join(args.input, "case.json"))
    case_id = "unknown"
    contract = None
    if isinstance(case, dict):
        case_id = (case.get("case_id") or case.get("id")
                   or case.get("challenge_id") or "unknown")
        contract = (case.get("output_contract") or case.get("outputContract")
                    or case.get("output_schema") or case.get("result_contract")
                    or case.get("contract"))
    if err[0] == "invalid":
        degradations.append("case.json unreadable or invalid; fallback to default contract")
    if not isinstance(contract, dict) or not contract:
        contract = DEFAULT_CONTRACT

    # 缺输入 → input_error
    if not os.path.isdir(args.input):
        data["status"] = "input_error"
        _finalize(args, data, case_id, contract, t0)
        return 0

    # 1) 立即落骨架
    _finalize(args, data, case_id, contract, t0)
    log_change(changes, "skeleton_written", case_id=case_id)

    # 2) 盘点
    entries, dirs = walk_inventory(args.input)
    files = [e for e in entries if not e.get("is_dir")]
    if not files:
        data["status"] = "insufficient_evidence"
        data["uncertainty"]["no_files"] = "no files found in input"
        _finalize(args, data, case_id, contract, t0)
        return 0

    hashes = {e["path"]: e["sha256"] for e in files if e.get("sha256")}
    inv = {"files": files, "dirs": sorted(dirs), "total_files": len(files),
           "total_bytes": sum((e.get("size") or 0) for e in files)}
    write_json(os.path.join(args.work, "inventory.json"), inv)

    # 3) 抽取文本
    text_map = {}
    for e in files:
        txt, reason = extract_text(e["path"], args.work, hashes, degradations)
        text_map[e["path"]] = txt
        e["has_text_layer"] = (reason == "ok" and bool(txt and txt.strip()))
    bib_entries = []
    for e in files:
        if e.get("ext") in (".bib", ".ris"):
            bib_entries.extend(extract_bib_fields(text_map.get(e["path"]) or ""))

    # decisions（可选，Agent 可写，结构见 references/rules.md）
    decisions = {}
    if args.decisions and os.path.isfile(args.decisions):
        d, derr = read_json(args.decisions)
        if derr[0] == "ok" and isinstance(d, dict):
            decisions = d.get("files") or {}
        else:
            degradations.append("decisions.json invalid; ignored")

    # 4) 语义判断 + 构建 catalog
    catalog = {}
    for e in files:
        src = e["source"]
        text = text_map.get(e["path"])
        stem = os.path.basename(e["path"])
        ext = e.get("ext") or ""
        is_bib = ext in (".bib", ".ris")
        decision = decisions.get(src) or decisions.get(os.path.basename(src)) or {}
        dtype = decision.get("document_type") or guess_document_type(e["path"], text, ext)
        topic = decision.get("topic") or guess_topic(text, stem)
        meta, ev = extract_metadata(text, stem, bib_entries, is_bib)
        if decision.get("metadata"):
            for k, v in decision["metadata"].items():
                if meta.get(k) in (None, "", []):
                    meta[k] = v
            ev.extend(decision.get("evidence") or [])
        version_kind = decision.get("version_kind") or guess_version_kind(stem, text, meta)
        tags = list(decision.get("tags") or [])
        if not tags:
            tags = [t for t in (dtype, topic) if t not in ("other", "general", "unknown")]
        tags = sorted(set(tags))
        suggested = build_name(meta, ext, topic)
        catalog[src] = {
            "source": src, "document_type": dtype, "topic": topic,
            "category_path": rel_join(dtype, topic),
            "suggested_name": suggested,
            "metadata": meta, "tags": tags, "version_kind": version_kind,
            "has_text_layer": e.get("has_text_layer"),
            "sha256": e.get("sha256"), "size": e.get("size"),
            "permissions": e.get("permissions") or {},
            "duplicate_of": None, "version_group": None,
            "keep": True, "action": "copy",
            "evidence": ev or [], "errors": e.get("errors") or [],
        }

    # 去重：字节级 SHA-256
    seen = {}
    for src in sorted(catalog, key=lambda s: (len(s.split("/")), s.lower())):
        h = catalog[src].get("sha256")
        if h and h in seen:
            catalog[src]["duplicate_of"] = seen[h]
            catalog[src]["keep"] = False
            catalog[src]["action"] = "skip_duplicate"
        elif h:
            seen[h] = src

    # 版本组：并查集聚合 DOI/arXiv/标题+年份
    dsu = DSU()
    by_key = {}
    for src, rec in catalog.items():
        for k in version_keys(rec["metadata"], rec["metadata"].get("title")):
            if k in by_key:
                dsu.union(by_key[k], src)
            else:
                by_key[k] = src
    groups = {}
    for src in catalog:
        groups.setdefault(dsu.find(src), []).append(src)
    gid = 0
    for members in sorted(groups.values(), key=lambda m: sorted(m)):
        if len(members) > 1:
            gid += 1
            for m in sorted(members):
                catalog[m]["version_group"] = f"vg{gid:03d}"

    # 5/6) 计划与落盘
    plan, ops = _build_plan(catalog, files, args)
    write_json(os.path.join(args.work, "organization.json"), {"plan": plan, "catalog": catalog})

    rollback = []
    if args.apply:
        rollback = _apply(catalog, args)

    # 7) 汇总
    _summarize(data, catalog, inv, ops, rollback, degradations, pending)
    data["status"] = _decide_status(data, files)
    data["runtime"] = {"seconds": round(time.time() - t0, 3),
                       "files": len(files),
                       "kept": sum(1 for r in catalog.values() if r["keep"])}
    _finalize(args, data, case_id, contract, t0)
    return 0


def _build_plan(catalog, files, args):
    ops, plan, occupied = [], [], {}
    for f in files:
        src = f["source"]
        rec = catalog.get(src)
        if rec is None:
            continue
        if not rec["keep"]:
            plan.append({"source": src, "action": "skip_duplicate",
                         "duplicate_of": rec["duplicate_of"]})
            ops.append({"at": now_iso(), "action": "skip_duplicate", "source": src,
                        "duplicate_of": rec["duplicate_of"]})
            continue
        target_dir = os.path.join(args.output, "library", rec["category_path"].replace("/", os.sep))
        name = rec["suggested_name"] or safe_name(os.path.basename(src), 80)
        if not os.path.splitext(name)[1]:
            name = name + (f.get("ext") or "")
        target = os.path.join(target_dir, name)
        key = target
        if key in occupied and occupied[key] != rec.get("sha256"):
            stem, ext = os.path.splitext(name)
            i = 2
            while key in occupied and occupied[key] != rec.get("sha256"):
                name = f"{stem}-{i}{ext}"
                target = os.path.join(target_dir, name)
                key = target
                i += 1
        occupied[key] = rec.get("sha256")
        rec["suggested_name"] = name
        rec["target"] = target
        plan.append({"source": src, "action": "copy", "target": target})
        ops.append({"at": now_iso(), "action": "plan_copy", "source": src, "target": target})
    return plan, ops


def _apply(catalog, args):
    rollback = []
    for rec in catalog.values():
        if not rec.get("keep") or not rec.get("target"):
            continue
        src_abs = os.path.join(args.input, rec["source"].replace("/", os.sep))
        tgt = rec["target"]
        if not os.path.isfile(src_abs) or os.path.isfile(tgt):
            continue
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        try:
            shutil.copy2(src_abs, tgt)
            rollback.append({"created": tgt, "source": rec["source"]})
        except Exception as e:  # noqa: BLE001
            rec["action"] = "copy_failed"
            rec.setdefault("errors", []).append({"kind": "copy", "detail": str(e)})
    write_json(os.path.join(args.work, "rollback.json"), rollback)
    return rollback


def _summarize(data, catalog, inv, ops, rollback, degradations, pending):
    data["library"]["catalog"] = [v for _, v in sorted(catalog.items())]
    data["library"]["hierarchy"] = _hierarchy(catalog)
    data["library"]["metadata"] = {v["source"]: v["metadata"] for v in catalog.values()}
    data["library"]["versions"] = _versions(catalog)
    data["evidence"]["hashes"] = {v["source"]: v["sha256"] for v in catalog.values()}
    data["evidence"]["permissions"] = {v["source"]: v["permissions"] for v in catalog.values()}
    data["evidence"]["locators"] = [{"source": k, "evidence": v["evidence"]}
                                    for k, v in sorted(catalog.items()) if v["evidence"]]
    data["operations"] = ops
    data["failures"]["duplicates"] = [{"source": v["source"], "duplicate_of": v["duplicate_of"],
                                       "sha256": v["sha256"]}
                                      for v in catalog.values() if v["duplicate_of"]]
    data["failures"]["permission_errors"] = [
        {"source": v["source"], "detail": v["errors"]}
        for v in catalog.values()
        if any(x.get("kind") == "unreadable" for x in v["errors"])]
    data["failures"]["skipped"] = [
        {"source": v["source"], "reason": "unreadable", "detail": v["errors"]}
        for v in catalog.values() if not v.get("sha256")]
    data["failures"]["conflicts"] = _conflicts(catalog)
    data["failures"]["recoveries"] = rollback
    data["sources"] = [{"source": v["source"], "sha256": v["sha256"],
                        "locator": "inventory",
                        "quotes": [q.get("quote") for q in v["evidence"]]}
                       for v in catalog.values()]
    data["uncertainty"] = {
        "unclassified": sum(1 for v in catalog.values()
                            if v["document_type"] in ("other", "unknown")),
        "no_text_layer": sum(1 for v in catalog.values() if v.get("has_text_layer") is False),
        "missing_doi": sum(1 for v in catalog.values() if not v["metadata"].get("doi")),
        "missing_year": sum(1 for v in catalog.values() if not v["metadata"].get("year")),
    }
    data["degradations"] = sorted(set(degradations))
    data["pending_human"] = [{"source": v["source"], "fields": [
        k for k, val in v["metadata"].items() if val in (None, "", [])]}
        for v in catalog.values()
        if any(val in (None, "", []) for val in v["metadata"].values())]


def _hierarchy(catalog):
    tree = {}
    for v in catalog.values():
        if not v["keep"]:
            continue
        node = tree
        for part in v["category_path"].split("/"):
            node = node.setdefault(part, {})
        node.setdefault("_files", []).append(v["source"])
    return tree


def _versions(catalog):
    groups = {}
    for v in catalog.values():
        g = v["version_group"]
        if g:
            groups.setdefault(g, {"group_id": g, "members": [], "kinds": []})
            groups[g]["members"].append(v["source"])
            groups[g]["kinds"].append(v["version_kind"])
    return [{"group_id": g["group_id"], "members": sorted(g["members"]),
             "kinds": sorted(set(g["kinds"]))} for g in groups.values()]


def _conflicts(catalog):
    out = []
    seen_name = {}
    for v in catalog.values():
        key = (v["category_path"], v["suggested_name"])
        seen_name.setdefault(key, []).append((v["source"], v["sha256"]))
    for key, members in seen_name.items():
        if len({m[1] for m in members}) > 1:
            out.append({"target": "/".join(key), "sources": [m[0] for m in members],
                        "note": "same target name, different SHA-256; resolved by -N suffix"})
    return out


def _decide_status(data, files):
    if data["errors"]:
        return "partial"
    if data["failures"]["skipped"] or data["failures"]["permission_errors"]:
        return "partial"
    if any(e.get("has_text_layer") is False for e in files):
        return "partial"
    if data["uncertainty"].get("no_files"):
        return "insufficient_evidence"
    return "ok"


def _finalize(args, data, case_id, contract, t0):
    data.setdefault("case_id", case_id)
    data.setdefault("runtime", {})["seconds"] = round(time.time() - t0, 3)
    data.setdefault("status", "partial")
    for key in contract:
        data.setdefault(key, deep_placeholder(contract[key]))
    result = build_result(case_id, contract, data)
    write_json(os.path.join(args.output, "result.json"), result)
    return result


if __name__ == "__main__":
    sys.exit(main())