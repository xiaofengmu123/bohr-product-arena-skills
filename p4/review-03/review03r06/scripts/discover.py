"""discover.py -- discover input, extract text, build location index and figure/table model.

Stdlib only. Reads /input (or --input), writes to /app/work (or --work):
  manifest.json      -> case id, files, material list, errors/degradations
  segments.json      -> location index: one segment per page/paragraph with text + offsets
  figure_model.json  -> structured model of figures/tables and in-text mentions

Text extraction:
  .pdf  -> pdftotext -layout (page-separated); empty output => needs_ocr
  .txt/.md/.csv/.tsv/.json/.xml -> utf-8-sig read (fallback latin-1, mark encoding fallback)
  .docx -> zipfile + word/document.xml text runs
Other files are recorded as unparsed and skipped for review content.

Exit code 0 unless a write fails fatally.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".rst", ".log"}
MAX_DIRECT_READ = 2 * 1024 * 1024  # skip oversized text files for direct read


def log(msg):
    print("[discover] " + msg, flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Discover and extract input materials into a location index.")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p.parse_args(argv)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_text_any(path):
    """Return (text, encoding_note) with tolerant decoding."""
    raw = None
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        return None, "unreadable: %s" % e
    if len(raw) > MAX_DIRECT_READ:
        return None, "oversized (skipped)"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace"), "encoding_fallback(latin-1)"


def iter_files(root):
    """Yield (abs_path, rel_path, size) for regular files under root, deterministic order."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            if os.path.isfile(p):
                out.append((p, os.path.relpath(p, root), os.path.getsize(p)))
    return out


def extract_pdf(path):
    """Return (pages: list[str] or None, degradation: str or None)."""
    try:
        r = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", "-layout", path, "-"],
            capture_output=True, timeout=180)
    except FileNotFoundError:
        return None, "pdftotext_missing"
    except Exception as e:  # noqa: BLE001
        return None, "pdftotext_error:%s" % type(e).__name__
    if r.returncode != 0:
        return None, "pdftotext_rc=%d" % r.returncode
    text = r.stdout.decode("utf-8", "replace")
    if not text.strip():
        return None, "needs_ocr"
    pages = text.split("\f")
    # drop a trailing empty page artifact
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages, None


def extract_docx(path):
    """Minimal docx text extraction using stdlib zipfile; returns paragraphs."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                return [], "docx_no_document_xml"
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return [], "docx_error:%s" % type(e).__name__
    # paragraph boundaries; strip tags; keep text nodes
    paras = re.split(r"</w:p>", xml)
    texts = []
    for para in paras:
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, flags=re.S)
        t = "".join(runs)
        t = (t.replace("&lt;", "<").replace("&gt;", ">")
              .replace("&amp;", "&").replace("&quot;", '"').replace("&apos;", "'"))
        if t.strip():
            texts.append(t)
    return texts, None


def split_paragraphs(text):
    """Split text into non-empty paragraphs on blank lines."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in paras if p]


def norm_text(text):
    return re.sub(r"\s+", " ", text).strip()


FIG_RE = re.compile(
    r"(?i)\b(fig(?:ure)?s?\.?|table)s?\s*(\d+|[IVXLC]+)\b")


def detect_figures(segments):
    """Return figure_model figures/tables with in-text mentions."""
    figures = []
    by_num = {}
    for seg in segments:
        for m in FIG_RE.finditer(seg["text"]):
            kind = "figure" if m.group(1).lower().startswith("fig") else "table"
            num = m.group(2)
            key = (kind, num)
            entry = by_num.get(key)
            mention = {"segment_id": seg["id"], "locator": seg["locator"],
                       "text": seg["text"][:300]}
            if entry is None:
                entry = {"id": "%s_%s" % (kind, num), "kind": kind,
                         "number": num, "locator": seg["locator"],
                         "caption": "", "mentions": []}
                by_num[key] = entry
                figures.append(entry)
            entry["mentions"].append(mention)
    for f in figures:
        # heuristic caption: longest mention text containing the number
        best = ""
        for m in f["mentions"]:
            if len(m["text"]) > len(best):
                best = m["text"]
        f["caption"] = best[:500]
    figures.sort(key=lambda x: (x["kind"], x["number"].zfill(4)))
    return figures


def build_segments(materials):
    """materials: list of dicts with file/rel/path. Returns (segments, errors)."""
    segments = []
    errors = []
    sid = 0
    for mat in materials:
        rel = mat["rel"]
        ext = os.path.splitext(rel)[1].lower()
        pages = None
        paras = None
        degradation = None
        if ext == ".pdf":
            pages, degradation = extract_pdf(mat["path"])
            if degradation:
                errors.append({"file": rel, "kind": degradation,
                               "detail": "PDF not indexed; needs_ocr if empty"})
            if pages:
                for pi, page_text in enumerate(pages, 1):
                    for pidx, para in enumerate(split_paragraphs(page_text), 1):
                        sid += 1
                        segments.append({
                            "id": "s%05d" % sid, "file": rel, "page": pi,
                            "para": pidx, "locator": "p.%d ¶%d" % (pi, pidx),
                            "text": para,
                            "text_norm": norm_text(para)})
        elif ext == ".docx":
            paras, degradation = extract_docx(mat["path"])
            if degradation:
                errors.append({"file": rel, "kind": degradation, "detail": ""})
            for pidx, para in enumerate(paras, 1):
                sid += 1
                segments.append({
                    "id": "s%05d" % sid, "file": rel, "page": None,
                    "para": pidx, "locator": "¶%d" % pidx,
                    "text": para, "text_norm": norm_text(para)})
        elif ext in TEXT_EXTS:
            text, note = read_text_any(mat["path"])
            if text is None:
                errors.append({"file": rel, "kind": note or "unreadable", "detail": ""})
            else:
                if note:
                    errors.append({"file": rel, "kind": note, "detail": "read with fallback"})
                for pidx, para in enumerate(split_paragraphs(text), 1):
                    sid += 1
                    segments.append({
                        "id": "s%05d" % sid, "file": rel, "page": None,
                        "para": pidx, "locator": "¶%d" % pidx,
                        "text": para, "text_norm": norm_text(para)})
        else:
            errors.append({"file": rel, "kind": "unparsed_extension",
                           "detail": "ext=%s" % (ext or "none")})
    return segments, errors


def main(argv=None):
    args = parse_args(argv)
    ensure_dir(args.work)
    ensure_dir(args.output)
    manifest = {
        "case_id": None,
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
        "files": [],
        "materials": [],
        "errors": [],
        "degradations": [],
        "segment_count": 0,
        "figure_count": 0,
    }
    case_path = os.path.join(args.input, "case.json")
    task_path = os.path.join(args.input, "task.md")
    if os.path.isfile(case_path):
        try:
            case = load_json(case_path)
            manifest["case_id"] = case.get("case_id")
            manifest["has_output_contract"] = isinstance(case.get("output_contract"), dict)
        except Exception as e:  # noqa: BLE001
            manifest["errors"].append({"file": "case.json", "kind": "bad_json",
                                       "detail": str(e)})
    else:
        manifest["errors"].append({"file": "case.json", "kind": "missing", "detail": ""})

    work_abs = os.path.abspath(args.work)
    out_abs = os.path.abspath(args.output)
    for p, rel, size in iter_files(args.input):
        manifest["files"].append({"rel": rel, "size": size})
        if rel in ("case.json", "task.md"):
            continue
        # skip hidden files and any work/output artifacts that sit under the input tree
        parts = rel.replace("\\", "/").split("/")
        if any(part.startswith(".") for part in parts):
            continue
        pabs = os.path.abspath(p)
        if pabs == work_abs or pabs.startswith(work_abs + os.sep):
            continue
        if pabs == out_abs or pabs.startswith(out_abs + os.sep):
            continue
        manifest["materials"].append({"rel": rel, "path": p, "size": size})

    segments, seg_errors = build_segments(manifest["materials"])
    manifest["errors"].extend(seg_errors)
    manifest["segment_count"] = len(segments)
    figures = detect_figures(segments)
    manifest["figure_count"] = len(figures)
    if not segments:
        manifest["degradations"].append(
            {"kind": "no_extracted_text", "detail": "no readable text segments"})

    save_json(os.path.join(args.work, "manifest.json"), manifest)
    save_json(os.path.join(args.work, "segments.json"), {"segments": segments})
    save_json(os.path.join(args.work, "figure_model.json"),
              {"schema_version": "1.0", "case_id": manifest["case_id"],
               "figures": [f for f in figures if f["kind"] == "figure"],
               "tables": [f for f in figures if f["kind"] == "table"]})

    log("case_id=%s files=%d materials=%d segments=%d figures=%d errors=%d"
        % (manifest["case_id"], len(manifest["files"]), len(manifest["materials"]),
           len(segments), len(figures), len(manifest["errors"])))
    for e in manifest["errors"]:
        log("  error: %s %s" % (e.get("file", ""), e.get("kind", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
