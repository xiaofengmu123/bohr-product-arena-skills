#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic helpers for the paper-structure-logic Skill.

Design constraints:
- standard library only;
- every extraction function is deterministic: no randomness, no clock, no
  dict/set iteration-order dependence (all outputs are sorted);
- locators follow "relpath#L<line>" for text and "relpath#<chart>" for figures.
"""

import json
import os
import re
import shutil
import subprocess

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per file, larger files are skipped

TEXT_EXT = {".md", ".txt", ".rst", ".tex", ".csv", ".tsv", ".json", ".jsonl",
            ".bib", ".xml", ".html", ".htm", ".yaml", ".yml"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xls"}
OFFICE_EXT = {".docx", ".pptx", ".xlsx", ".xls"}

ASSERT_VERBS = [
    "表明", "证实", "证明", "显示", "发现", "揭示", "提示", "暗示", "支持", "一致",
    "suggest", "indicate", "indicates", "show", "shows", "shown", "demonstrate",
    "demonstrates", "reveal", "reveals", "found", "find", "imply", "implies",
    "support", "supports", "confirm", "confirms", "establish", "establishes",
    "significant", "consistent with", "our results", "we show", "we find",
]

CAUSAL_MARKERS = [
    "导致", "引起", "造成", "促进", "抑制", "驱动", "决定", "因果", "因此", "致使",
    "cause", "causes", "caused", "causing", "lead to", "leads to", "led to",
    "induce", "induces", "induce", "drive", "drives", "drove", "determine",
    "determines", "responsible for", "due to", "consequently", "thereby",
]

CORREL_MARKERS = [
    "相关", "关联", "联系", "伴随", "相关于", "呈相关",
    "associated", "association", "correlated", "correlation", "linked",
    "relationship", "trend", "co-occur",
]

HEADING_WORDS = [
    "abstract", "introduction", "background", "related work", "methods",
    "materials", "results", "discussion", "conclusion", "conclusions",
    "references", "experiments", "evaluation", "analysis", "limitations",
    "future work", "acknowledgement", "acknowledgments", "appendix",
    "supplementary", "摘要", "引言", "方法", "材料", "结果", "讨论", "结论",
    "参考文献", "实验", "评估", "分析", "局限性", "附录",
]

VALUE_PATTERNS = {
    "p_value": re.compile(r"\bp\s*[=<>≤≥]+\s*0?\.\d+\b", re.IGNORECASE),
    "percent": re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*%"),
    "ci": re.compile(r"(?:\d{1,2}(?:\.\d+)?)\s*%?\s*(?:CI|置信区间)", re.IGNORECASE),
    "number": re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
}


def now_iso():
    """UTC ISO-8601 timestamp; used only for change_log/runtime, never evidence."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def read_text_safe(path):
    """Read a text file with BOM/bad-encoding tolerance. Returns (text, error)."""
    if not path or not os.path.exists(path):
        return None, f"missing: {path}"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, str(e)
    if size > MAX_FILE_BYTES:
        return None, f"exceeds {MAX_FILE_BYTES} bytes, skipped"
    data = None
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                data = f.read()
            return data, None
        except (UnicodeDecodeError, ValueError) as e:
            last_err = f"decode failed with {enc}"
        except OSError as e:
            return None, str(e)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        return data, last_err + "; read with replacement"
    except OSError as e:
        return None, str(e)


def load_json_safe(path):
    """Read JSON with tolerance; returns (object, error)."""
    text, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"


def write_json_safe(path, obj):
    """Deterministic JSON write: sorted keys, UTF-8, LF, atomic replace."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return None
    except OSError as e:
        return str(e)


def get_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "id", "armiId", "caseId"):
            v = case.get(k)
            if v:
                return v
    return None


def get_output_contract(case):
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        return dict(case["output_contract"])
    return {
        "diagnosis": None,
        "edit_plan": None,
        "source_map": None,
        "version_boundary": None,
        "artifacts": None,
    }


def discover_input(input_dir):
    """Scan input dir; return a deterministic manifest."""
    manifest = {"input_dir": input_dir, "case_json": None, "task_md": None,
                "text": [], "pdf": [], "image": [], "table": [], "office": [],
                "other": [], "skipped": []}
    if not input_dir or not os.path.isdir(input_dir):
        return manifest
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(root, name)
            low = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            rel = os.path.relpath(p, input_dir)
            if size > MAX_FILE_BYTES:
                manifest["skipped"].append({"path": rel, "reason": "exceeds 2MB"})
                continue
            if low == "case.json":
                manifest["case_json"] = p
                continue  # metadata, not paper material
            if low == "task.md":
                manifest["task_md"] = p
                continue  # metadata, not paper material
            ext = os.path.splitext(low)[1]
            entry = {"path": p, "rel": rel, "size": size}
            if ext in TEXT_EXT:
                manifest["text"].append(entry)
            elif ext in PDF_EXT:
                manifest["pdf"].append(entry)
            elif ext in IMAGE_EXT:
                manifest["image"].append(entry)
            elif ext in TABLE_EXT:
                manifest["table"].append(entry)
            elif ext in OFFICE_EXT:
                manifest["office"].append(entry)
            else:
                manifest["other"].append(entry)
    return manifest


def split_paragraphs(text, source):
    """Split text into paragraphs with stable locators (relpath#L<start_line>)."""
    paras = []
    if not text:
        return paras
    lines = text.splitlines()
    cur = []
    cur_start = 1
    para_index = 0
    i = 1
    while i <= len(lines):
        ln = lines[i - 1]
        if ln.strip() == "":
            if cur:
                joined = " ".join(x.strip() for x in cur).strip()
                if joined:
                    paras.append({"locator": f"{source}#L{cur_start}",
                                  "start_line": cur_start, "para_index": para_index,
                                  "text": joined})
                    para_index += 1
                cur = []
            cur_start = i + 1
        else:
            if not cur:
                cur_start = i
            cur.append(ln)
        i += 1
    if cur:
        joined = " ".join(x.strip() for x in cur).strip()
        if joined:
            paras.append({"locator": f"{source}#L{cur_start}",
                          "start_line": cur_start, "para_index": para_index,
                          "text": joined})
    return paras


def split_sentences(text):
    """Deterministic heuristic sentence split; the model verifies boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    parts = []
    buf = ""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf += ch
        if ch in ".!?。！？":
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j < n:
                nxt = text[j]
                is_cap = nxt.isupper() or nxt.isdigit()
                is_cjk = ord(nxt) > 0x2E80
                is_quote = nxt in "\"'“”‘’(["
                if is_cap or is_cjk or is_quote:
                    parts.append(buf.strip())
                    buf = ""
                    i = j - 1
            else:
                parts.append(buf.strip())
                buf = ""
        i += 1
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def _is_body_sentence(h):
    if len(h) > 120:
        return True
    if h[-1] in ".。!?！？":
        return True
    return False


def detect_sections(text, source):
    """Detect section headings (markdown #, numbered, known words)."""
    out = []
    if not text:
        return out
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if not s:
            continue
        heading = None
        level = None
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", s)
        if m:
            level = len(m.group(1))
            heading = m.group(2).strip()
        else:
            m2 = re.match(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})[\.、\)）:：]?\s+(.{2,120})$", s)
            if m2:
                heading = m2.group(2).strip()
                level = m2.group(1).count(".") + 1
        if not heading:
            low = s.lower().rstrip(".:：。")
            for w in HEADING_WORDS:
                if low == w or low.startswith(w + " "):
                    heading = s
                    level = 1
                    break
        if heading and not _is_body_sentence(heading):
            out.append({"locator": f"{source}#L{i}", "line": i,
                        "level": level, "heading": heading})
    return out


def extract_candidate_claims(text, source, max_claims=400):
    """Candidate claim sentences: assertion verbs or any numeric/statistic token."""
    out = []
    if not text:
        return out
    for para in split_paragraphs(text, source):
        for si, sent in enumerate(split_sentences(para["text"])):
            if len(sent) < 12 or len(sent) > 500:
                continue
            markers = []
            low = sent.lower()
            for v in ASSERT_VERBS:
                if v.lower() in low:
                    markers.append(v)
            has_num = bool(re.search(r"\d", sent))
            if markers or has_num:
                out.append({
                    "locator": para["locator"],
                    "para_index": para["para_index"],
                    "sent_index": si,
                    "quote": sent,
                    "markers": sorted(set(markers)),
                })
        if len(out) >= max_claims:
            break
    return out


def _context(text, s, e, width=40):
    a = max(0, s - width)
    b = min(len(text), e + width)
    prefix = "…" if a > 0 else ""
    suffix = "…" if b < len(text) else ""
    return prefix + text[a:b] + suffix


def extract_candidate_values(text, source, max_vals=300):
    """Candidate numeric/statistical tokens with surrounding context."""
    out = []
    if not text:
        return out
    for para in split_paragraphs(text, source):
        for kind, pat in VALUE_PATTERNS.items():
            for m in pat.finditer(para["text"]):
                val = m.group(0)
                out.append({
                    "locator": para["locator"],
                    "para_index": para["para_index"],
                    "kind": kind,
                    "value": val,
                    "quote": _context(para["text"], m.start(), m.end()),
                })
        if len(out) >= max_vals:
            break
    return out[:max_vals]


def _norm_tokens(text):
    t = (text or "").lower()
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", t)
    return [x for x in t.split() if x]


def detect_near_duplicates(paragraphs, threshold=0.55, max_paras=800):
    """Token-Jaccard near-duplicate clusters across paragraphs (capped)."""
    paras = paragraphs[:max_paras]
    n = len(paras)
    toks = [_norm_tokens(p["text"]) for p in paras]
    lens = [len(t) for t in toks]
    clusters = []
    used = [False] * n
    for i in range(n):
        if used[i] or lens[i] == 0:
            continue
        si = set(toks[i])
        grp = [paras[i]["locator"]]
        for j in range(i + 1, n):
            if used[j] or lens[j] == 0:
                continue
            if max(lens[i], lens[j]) / float(max(1, min(lens[i], lens[j]))) > 2.5:
                continue
            sj = set(toks[j])
            union = si | sj
            if not union:
                continue
            jac = len(si & sj) / float(len(union))
            if jac >= threshold:
                grp.append(paras[j]["locator"])
                used[j] = True
        if len(grp) > 1:
            used[i] = True
            clusters.append({"locators": grp, "similarity_method": "token_jaccard"})
    return clusters


def detect_conclusion_boundary(text, source, max_items=200):
    """Flag candidate overclaim sentences via causal vs correlational markers."""
    out = []
    if not text:
        return out
    for para in split_paragraphs(text, source):
        for si, sent in enumerate(split_sentences(para["text"])):
            low = sent.lower()
            causal = sorted(set(c for c in CAUSAL_MARKERS if c.lower() in low))
            correl = sorted(set(c for c in CORREL_MARKERS if c.lower() in low))
            if not causal and not correl:
                continue
            risk = "causal"
            if causal and correl:
                risk = "correlation_as_causation"
            elif correl and not causal:
                risk = "correlational_only"
            out.append({
                "locator": para["locator"],
                "sent_index": si,
                "quote": sent,
                "causal_markers": causal,
                "correl_markers": correl,
                "risk": risk,
            })
            if len(out) >= max_items:
                return out
    return out


def pdftotext_read(path):
    """Extract text from a PDF using pdftotext if available."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not found"
    try:
        r = subprocess.run([exe, "-layout", "-enc", "UTF-8", path, "-"],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace")[:200]
            return None, f"pdftotext exit {r.returncode}: {err}"
        return r.stdout.decode("utf-8", "replace"), None
    except Exception as e:  # noqa: BLE001 - tolerate any tool failure
        return None, f"pdftotext error: {e}"


def _process_text_material(rel, txt, size, buckets):
    buckets["materials"].append({"path": rel, "kind": "text", "size": size,
                                 "chars": len(txt),
                                 "paragraphs": len(split_paragraphs(txt, rel))})
    buckets["sections"] += detect_sections(txt, rel)
    buckets["claims"] += extract_candidate_claims(txt, rel)
    buckets["values"] += extract_candidate_values(txt, rel)
    buckets["dups"] += detect_near_duplicates(split_paragraphs(txt, rel))
    buckets["boundary"] += detect_conclusion_boundary(txt, rel)


def extract_evidence(case, task_text, input_dir, work_dir):
    """Discover materials and extract deterministic candidate evidence."""
    manifest = discover_input(input_dir)
    case_id = get_case_id(case)
    buckets = {
        "materials": [], "sections": [], "claims": [], "values": [],
        "dups": [], "boundary": [], "unreadable": [],
    }
    for entry in manifest["text"]:
        txt, err = read_text_safe(entry["path"])
        if txt is None:
            buckets["unreadable"].append({"path": entry["rel"],
                                          "reason": err or "unreadable"})
            continue
        _process_text_material(entry["rel"], txt, entry["size"], buckets)
    for entry in manifest["pdf"]:
        txt, err = pdftotext_read(entry["path"])
        if txt is None:
            buckets["unreadable"].append({"path": entry["rel"],
                                          "reason": err or "pdftotext unavailable"})
            continue
        buckets["materials"].append({"path": entry["rel"], "kind": "pdf",
                                     "size": entry["size"], "chars": len(txt)})
        buckets["sections"] += detect_sections(txt, entry["rel"])
        buckets["claims"] += extract_candidate_claims(txt, entry["rel"])
        buckets["values"] += extract_candidate_values(txt, entry["rel"])
        buckets["dups"] += detect_near_duplicates(split_paragraphs(txt, entry["rel"]))
        buckets["boundary"] += detect_conclusion_boundary(txt, entry["rel"])
    for entry in manifest["image"]:
        buckets["unreadable"].append({"path": entry["rel"],
                                      "reason": "image: render/OCR required"})
    for entry in manifest["table"]:
        if entry["rel"].lower().endswith((".xlsx", ".xls")):
            buckets["unreadable"].append({"path": entry["rel"],
                                          "reason": "spreadsheet: render required"})
    for entry in manifest["office"]:
        buckets["unreadable"].append({"path": entry["rel"],
                                      "reason": "office: render required"})

    evidence = {
        "case_id": case_id,
        "materials": sorted(buckets["materials"], key=lambda x: x["path"]),
        "sections": sorted(buckets["sections"], key=lambda x: (x["locator"], x.get("line", 0))),
        "candidate_claims": sorted(buckets["claims"], key=lambda x: (x["locator"], x.get("sent_index", 0))),
        "candidate_values": sorted(buckets["values"], key=lambda x: (x["locator"], x.get("kind", ""))),
        "near_duplicates": sorted(buckets["dups"], key=lambda x: (x["locators"][0] if x["locators"] else "")),
        "candidate_boundary": sorted(buckets["boundary"], key=lambda x: (x["locator"], x.get("sent_index", 0))),
        "unreadable": sorted(buckets["unreadable"], key=lambda x: x["path"]),
        "task_excerpt": (task_text or "")[:2000],
    }
    return evidence, manifest, []
