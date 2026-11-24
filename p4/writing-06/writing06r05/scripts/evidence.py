#!/usr/bin/env python3
"""Material discovery, text decoding, manuscript segmentation and claim candidates.

Stdlib only. Importable by main.py and reusable standalone:
    python scripts/evidence.py --input /input --out /app/work/evidence.json
"""
import argparse
import json
import os
import re
import sys

MANUSCRIPT_EXTS = {".md", ".txt", ".tex", ".markdown", ".rst", ".org", ".text"}
DATA_EXTS = {".json", ".csv", ".tsv", ".yaml", ".yml"}
PDF_EXTS = {".pdf"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".rtf"}
MAX_BYTES = 2 * 1024 * 1024
# 运行时元数据文件不参与正文切分（它们描述任务/案例，不是被诊断的稿件）
SKIP_SEGMENT_NAMES = {"case.json", "task.md"}

HEADING_NUM_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*[.、)）:\s]+\S")
NAMED_HEADING_RE = re.compile(
    r"^(?:abstract|introduction|methods?|materials?\s+and\s+methods?|results?|"
    r"discussion|conclusion|references|acknowledgements?|appendix|摘要|引言|方法|"
    r"结果|讨论|结论|参考文献|致谢|附录)\b", re.IGNORECASE)
LATEX_HEADING_RE = re.compile(
    r"^\s*\\(section|subsection|subsubsection|chapter|part)\*?\{", re.IGNORECASE)

CITATION_RE = re.compile(
    r"(\[\d+(?:[-,]\d+)*\]|\(\s*[A-Z][A-Za-z&.\- ]+,\s*\d{4}\s*\)|"
    r"\\cite\{[^}]*\}|图\s*\d+|表\s*\d+|Figure\s*\d+|Table\s*\d+)")
NUM_RE = re.compile(
    r"(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:%|‰|nm|µm|μm|mm|cm|km|m|s|ms|min|h|day|"
    r"kg|g|mg|μg|µg|M|mM|μM|µM|nM|℃|°C|K|AU|a\.u\.|±|SE|SD|CI|p\s*[<>=])?")
CAUSAL_RE = re.compile(
    r"(表明|显示|证明|证实|提示|导致|引起|说明|相关|因果|cause|indicate|demonstrat|"
    r"show|suggest|prove|confirm|lead to|correlat|associat)", re.IGNORECASE)
HEDGE_RE = re.compile(
    r"(可能|或许|似乎|初步|推测|有待|potential|possibly|maybe|likely|preliminary|"
    r"tentatively)", re.IGNORECASE)
STRONG_RE = re.compile(
    r"(证明|证实|首次|完全|必然|一定|严格|prove|confirm|establish|definitively)",
    re.IGNORECASE)


def is_heading(line):
    s = line.strip()
    if not s:
        return False
    if re.match(r"^#{1,6}\s+", s):
        return True
    if LATEX_HEADING_RE.match(s):
        return True
    if len(s) < 120 and (HEADING_NUM_RE.match(s) or NAMED_HEADING_RE.match(s)
                         or re.match(r"^\*\*.+\*\*\s*$", s)):
        return True
    return False


def discover(input_dir):
    """Return a stable list of material records under input_dir."""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in PDF_EXTS:
                kind = "pdf"
            elif ext in MANUSCRIPT_EXTS:
                kind = "manuscript"
            elif ext in DATA_EXTS:
                kind = "data"
            elif ext in IMG_EXTS:
                kind = "image"
            elif ext in OFFICE_EXTS:
                kind = "office"
            else:
                kind = "other"
            items.append({"path": p, "name": fn, "ext": ext, "kind": kind,
                          "size": size})
    return items


def read_text(path, max_bytes=MAX_BYTES):
    """Read a text file with tolerant decoding. Returns (text, encoding_or_error)."""
    try:
        if os.path.getsize(path) > max_bytes:
            return None, "oversize: >{} bytes".format(max_bytes)
    except OSError as e:
        return None, "stat error: {}".format(e)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read(), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            return None, "read error: {}".format(e)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), "utf-8-replace"
    except OSError as e:
        return None, "read error: {}".format(e)


def load_json(path):
    text, enc = read_text(path)
    if text is None:
        return None, enc
    try:
        return json.loads(text), "json"
    except Exception as e:  # noqa: BLE001
        return None, "bad json: {}".format(e)


def _flush_paragraph(blocks, source, sec_no, pi, cur_lines, cur_start):
    text = "\n".join(cur_lines).strip()
    if not text:
        return
    blocks.append({
        "locator": "{}#S{}P{}@L{}".format(source, sec_no, pi, cur_start + 1),
        "kind": "paragraph",
        "section": sec_no,
        "paragraph": pi,
        "line_start": cur_start + 1,
        "line_end": cur_start + len(cur_lines),
        "chars": len(text),
        "text": text,
    })


def segment(text, source):
    """Split text into heading/paragraph blocks with deterministic locators."""
    lines = text.splitlines()
    n = len(lines)
    heading_idx = set(i for i, ln in enumerate(lines) if is_heading(ln))
    ranges = []
    if not heading_idx:
        ranges.append((0, n - 1, 0, "(preamble)"))
    else:
        ordered = sorted(heading_idx)
        if ordered[0] > 0:
            ranges.append((0, ordered[0] - 1, 0, "(preamble)"))
        for k, li in enumerate(ordered):
            end = ordered[k + 1] - 1 if k + 1 < len(ordered) else n - 1
            ranges.append((li, end, k + 1, lines[li].strip()))

    blocks = []
    for start, end, sec_no, title in ranges:
        blocks.append({
            "locator": "{}#S{}".format(source, sec_no),
            "kind": "heading",
            "section": sec_no,
            "title": title[:100],
            "line_start": start + 1,
            "line_end": start + 1,
            "chars": 0,
            "text": "",
        })
        pi = 0
        cur_lines = []
        cur_start = None
        for i in range(start, end + 1):
            if i in heading_idx:
                continue
            ln = lines[i]
            if not ln.strip():
                if cur_lines:
                    _flush_paragraph(blocks, source, sec_no, pi, cur_lines, cur_start)
                    pi += 1
                    cur_lines = []
                    cur_start = None
                continue
            if cur_start is None:
                cur_start = i
            cur_lines.append(ln)
        if cur_lines:
            _flush_paragraph(blocks, source, sec_no, pi, cur_lines, cur_start)
    return blocks


def _split_sentences(text):
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return []
    parts = re.split(r"(?<=[。！？!?])", t)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for q in re.split(r"(?<=[.;])\s+", p):
            q = q.strip()
            if q:
                out.append(q)
    return out


def extract_candidate_claims(blocks):
    """Return sentences carrying citation/number/claim/hedge/strong signals."""
    claims = []
    for b in blocks:
        if b["kind"] != "paragraph":
            continue
        for sent in _split_sentences(b["text"]):
            s = sent.strip()
            if len(s) < 8 or len(s) > 800:
                continue
            signals = []
            if CITATION_RE.search(s):
                signals.append("citation")
            if NUM_RE.search(s):
                signals.append("number")
            if CAUSAL_RE.search(s):
                signals.append("claim_verb")
            if HEDGE_RE.search(s):
                signals.append("hedge")
            if STRONG_RE.search(s):
                signals.append("strong_verb")
            if not signals:
                continue
            claims.append({
                "locator": b["locator"],
                "quote": s[:400],
                "signals": signals,
                "section": b["section"],
                "paragraph": b.get("paragraph"),
            })
    return claims


def build_evidence(input_dir):
    ev = {"input_dir": input_dir, "materials": [], "manuscripts": [],
          "candidate_claims": [], "warnings": []}
    for it in discover(input_dir):
        rec = dict(it)
        ev["materials"].append(rec)
        if it["kind"] == "manuscript" and it["name"] in SKIP_SEGMENT_NAMES:
            rec["readable"] = True
            rec["note"] = "元数据/任务说明，不参与正文切分"
            continue
        if it["kind"] == "manuscript":
            text, enc = read_text(it["path"])
            if text is None:
                rec["readable"] = False
                ev["warnings"].append({"file": it["name"], "reason": enc})
                continue
            rec["readable"] = True
            rec["encoding"] = enc
            blocks = segment(text, it["name"])
            claims = extract_candidate_claims(blocks)
            ev["manuscripts"].append({
                "name": it["name"], "path": it["path"], "chars": len(text),
                "sections": [b for b in blocks if b["kind"] == "heading"],
                "paragraphs": [b for b in blocks if b["kind"] == "paragraph"],
                "claims": claims,
            })
            ev["candidate_claims"].extend(claims)
        elif it["kind"] == "data":
            obj, err = load_json(it["path"])
            rec["readable"] = obj is not None
            rec["note"] = "结构化数据，不参与正文切分" if rec["readable"] else err
        elif it["kind"] == "pdf":
            rec["readable"] = None
            rec["note"] = "PDF 需 pdftotext/渲染/OCR 在运行时读取，此处不自动抽取"
        elif it["kind"] == "image":
            rec["readable"] = None
            rec["note"] = "图片需渲染/OCR 在运行时读取"
        elif it["kind"] == "office":
            rec["readable"] = None
            rec["note"] = "Office 文档需 office-renderer，按二进制处理"
        else:
            rec["readable"] = None
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--out", default=os.environ.get("EVIDENCE_JSON",
                                                    "/app/work/evidence.json"))
    a = ap.parse_args()
    ev = build_evidence(a.input)
    outdir = os.path.dirname(a.out) or "."
    try:
        os.makedirs(outdir, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "materials": len(ev["materials"]),
                      "candidate_claims": len(ev["candidate_claims"]),
                      "out": a.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
