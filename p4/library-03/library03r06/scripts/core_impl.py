#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — sci-library-organizer 四层中间表示的纯标准库实现。

由 main.py / selftest.py 调用，不单独作为入口。仅依赖 Python 3.8+ 标准库。

四层中间表示（本版核心取舍，见 SKILL.md 第 4 节）：
  facts     work/inventory.json   盘点：路径/字节/sha256/权限/格式/编码/文本层（无推断）
  decisions work/decisions.json   决策：元数据/类型/标签/版本角色，每字段带 locator+quote
  plan      work/plan.json        计划：声明式变换（源→类别/新名/动作/去重/版本），落地前可审计
  execution work/rollback.json    执行：--apply 重放 plan，复制到 output/library/，记录创建清单

同一输入两次运行，除时间戳与墙钟外逐字节一致（无任何随机量）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.0.0"
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# 默认契约：对应 task.md「Skill 运行后的输出要求」三组内容；题目 output_contract 存在时以它为准。
DEFAULT_CONTRACT = {
    "library": {"catalog": [], "hierarchy": {}, "metadata": {}, "versions": []},
    "evidence": {"hashes": {}, "permissions": {}, "locators": []},
    "operations": [],
    "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                 "duplicates": [], "recoveries": []},
}

EXTRAS = ("status", "sources", "uncertainty", "change_log",
          "errors", "degradations", "pending_human", "runtime")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

TEXT_MAX_BYTES = 2_000_000
NAME_SLUG_MAX = 120
FILENAME_MAX = 180
INPUT_SPEC_BASENAMES = {"case.json", "task.md"}

UNSAFE_CHARS = set('/\\:*?"<>|\x00-\x1f')
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"arxiv\s*[:/]?\s*(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# 后缀 → (kind, 是否文本型)
EXT_KIND = {
    ".pdf": ("pdf", False), ".ps": ("pdf", False),
    ".bib": ("bibliography", True), ".ris": ("bibliography", True),
    ".enw": ("bibliography", True), ".nbib": ("bibliography", True),
    ".json": ("json", True), ".jsonl": ("json", True), ".xml": ("xml", True),
    ".yaml": ("metadata", True), ".yml": ("metadata", True),
    ".toml": ("metadata", True), ".ini": ("metadata", True),
    ".csv": ("dataset", True), ".tsv": ("dataset", True),
    ".xlsx": ("spreadsheet", False), ".xls": ("spreadsheet", False),
    ".txt": ("text", True), ".md": ("markdown", True),
    ".png": ("image", False), ".jpg": ("image", False), ".jpeg": ("image", False),
    ".tif": ("image", False), ".tiff": ("image", False), ".gif": ("image", False),
    ".bmp": ("image", False), ".webp": ("image", False),
    ".docx": ("office", False), ".doc": ("office", False),
    ".pptx": ("office", False), ".ppt": ("office", False),
    ".zip": ("archive", False), ".tar": ("archive", False), ".gz": ("archive", False),
    ".7z": ("archive", False), ".rar": ("archive", False),
    ".py": ("code", True), ".ipynb": ("json", True), ".sh": ("code", True),
    ".r": ("code", True), ".jl": ("code", True), ".m": ("code", True),
    ".html": ("markup", True), ".htm": ("markup", True),
}

# 受控主题词表（同义合并，规则见 references/rules.md）
TOPIC_TERMS = {
    "machine_learning": [r"machine learning", r"deep learning", r"\bneural (network|net)\b", r"\btransformer\b", r"representation learning", r"\breinforcement learning\b", r"\bgan\b", r"\bgpt\b", r"\bllm\b"],
    "computer_vision": [r"computer vision", r"\bimage segmentation\b", r"object detection", r"\bcnn\b", r"visual"],
    "natural_language": [r"natural language", r"\blanguage model\b", r"\btranslation\b", r"named entity", r"\bsentiment\b", r"text classif", r"\bllm\b"],
    "quantum": [r"\bquantum\b", r"\bqubit\b", r"\bentanglement\b", r"\bphoton\b", r"superconducting", r"\bboson\b", r"\bfermion\b"],
    "materials": [r"\bmaterial[s]?\b", r"\balloy\b", r"\bperovskite\b", r"\bcatalyst\b", r"\bpolymer\b", r"\bcrystal\b", r"\bnanomaterial\b"],
    "chemistry": [r"\bchem\w*\b", r"\bmolecul\w*\b", r"\bsynthesis\b", r"\bligand\b", r"catalysis", r"\bsolvent\b"],
    "biology": [r"\bprotein\b", r"\bgene\b", r"\bgenome\b", r"\bcell\b", r"\bdna\b", r"\brna\b", r"\benzyme\b", r"\bpathway\b"],
    "medicine_health": [r"\bclinical\b", r"\bcancer\b", r"\bpatient\b", r"\btumor\b", r"\bdrug\b", r"\btrial\b", r"\bdisease\b", r"\btherapy\b"],
    "physics": [r"\bphysics\b", r"\bparticle\b", r"\bcosmolog\b", r"\bgalaxy\b", r"\bthermodynamic\b", r"\bfluid\b", r"\boptics\b"],
    "astronomy": [r"\bastronom\b", r"\bplanet\b", r"\bstar\b", r"\bgalaxy\b", r"\btelescope\b", r"\bcosmolog\b"],
    "earth_climate": [r"\bclimate\b", r"\batmosphere\b", r"\bocean\b", r"\bgeolog\b", r"\bseismic\b", r"\bweather\b"],
    "economics": [r"\beconom\w*\b", r"\bmarket\b", r"\btrade\b", r"\bmonetary\b", r"\bfinance\b", r"\btax\b"],
    "social_science": [r"\bsurvey\b", r"\bdemograph\b", r"\bsociolog\b", r"\bpolicy\b", r"\beducation\b", r"\bpsycholog\b"],
    "robotics": [r"\brobot\b", r"\bmanipulat\b", r"\bcontrol system\b", r"\bautonomous\b", r"\bslam\b"],
    "energy": [r"\bbattery\b", r"\bsolar cell\b", r"\bfuel cell\b", r"\benergy storage\b", r"\belectrochem\b"],
}

ROLE_MARKERS = [
    ("preprint", [r"preprint", r"arxiv", r"manuscript", r"submitted", r"working paper"]),
    ("supplementary", [r"supplement", r"supporting information", r"_si\b", r"appendix", r"\bsi\b"]),
    ("revision", [r"\brevision\b", r"\brv\d", r"\bv\d+", r"\bversion\s", r"revised"]),
    ("published", [r"published", r"final", r"journal version", r"accepted"]),
]

DOCTYPE_FILENAME_RULES = [
    ("thesis", [r"\bthesis\b", r"\bdissertation\b", r"\bphd\b", r"master thesis", r"doctoral"]),
    ("conference_paper", [r"proceedings", r"\bworkshop\b", r"\bconf(erence)?\b", r"symposium"]),
    ("preprint", [r"preprint", r"arxiv", r"manuscript", r"submitted"]),
    ("supplementary", [r"supplement", r"supporting", r"appendix"]),
    ("report", [r"\breport\b", r"technical report", r"\btr\b", r"white ?paper"]),
    ("book", [r"\bbook\b", r"monograph", r"\bchapter\b"]),
    ("dataset", [r"\bdataset\b", r"data files", r"\bdata\b"]),
    ("scanned", [r"scan(ned)?", r"\bocr\b"]),
]

BIBTYPE_TO_DOCTYPE = {
    "article": "journal_article", "inproceedings": "conference_paper",
    "conference": "conference_paper", "proceedings": "conference_paper",
    "phdthesis": "thesis", "mastersthesis": "thesis", "thesis": "thesis",
    "techreport": "report", "report": "report", "book": "book",
    "inbook": "chapter", "incollection": "chapter", "unpublished": "preprint",
    "preprint": "preprint", "misc": "other", "dataset": "dataset",
    "software": "software", "online": "web_resource", "manual": "report",
}

KIND_TO_DOCTYPE = {
    "bibliography": "bibliographic_record", "json": "metadata_record",
    "xml": "metadata_record", "metadata": "metadata_record",
    "markdown": "note", "text": "note", "markup": "note",
    "dataset": "dataset", "image": "scan", "spreadsheet": "dataset",
    "office": "document", "archive": "archive", "code": "software",
    "pdf": "unknown_publication", "other": "other",
}

# 元数据类型只对“内容型”文件生效（bibliography/json 等记录文件自身另有归类）
CONTENT_KINDS = {"pdf", "image", "office", "archive", "other"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 容错 I/O
# --------------------------------------------------------------------------- #
def load_json_file(path) -> tuple:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, f"unreadable: {e}"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc)), None
        except (UnicodeDecodeError, ValueError):
            continue
    try:
        return json.loads(raw.decode("utf-8", "replace")), None
    except ValueError as e:
        return None, f"invalid json: {e}"


def write_json_atomic(path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json_dumps(obj), encoding="utf-8")
    tmp.replace(p)


def read_text_bytes(path, max_bytes=TEXT_MAX_BYTES) -> dict:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return {"text": "", "encoding": None, "truncated": False, "error": str(e)}
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return {"text": data.decode(enc), "encoding": enc, "truncated": truncated, "error": None}
        except (UnicodeDecodeError, UnicodeError):
            continue
    return {"text": data.decode("utf-8", "replace"), "encoding": "utf-8-replace",
            "truncated": truncated, "error": "undecodable; replacement characters used"}


def sha256_of(path) -> tuple:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as e:
        return None, str(e)


def stat_permissions(path) -> dict:
    try:
        st = os.stat(path)
        return {
            "octal": f"{st.st_mode & 0o777:04o}",
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
            "size_bytes": st.st_size,
            "mtime_unix": int(st.st_mtime),
            "is_link": os.path.islink(path),
            "stat_ok": True,
        }
    except OSError as e:
        return {"stat_ok": False, "readable": False, "writable": False, "error": str(e)}


# --------------------------------------------------------------------------- #
# 归一化 / 命名
# --------------------------------------------------------------------------- #
def norm_key(s) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\W+", "", s).lower()


def slugify(s, max_len=NAME_SLUG_MAX) -> str:
    s = unicodedata.normalize("NFC", (s or "").strip())
    out = []
    for ch in s:
        out.append("_" if ch in UNSAFE_CHARS else ch)
    t = re.sub(r"\s+", " ", "".join(out)).strip(" .")
    t = re.sub(r"_+", "_", t)
    t = t[:max_len].rstrip(" .")
    return t or "untitled"


def lastname(author: str) -> str:
    a = (author or "").strip()
    if not a or a in ("not_reported", "unknown", "others"):
        return "anon"
    first = re.split(r"\s+and\s+|\s*[,;&]\s*|\s+et\s+al\.?", a, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    parts = [p for p in re.split(r"[,\s]+", first) if p]
    if not parts:
        return "anon"
    if "," in first:
        return slugify(parts[0], 40) or "anon"
    return slugify(parts[-1], 40) or "anon"


# --------------------------------------------------------------------------- #
# 盘点：inventory.json（事实层）
# --------------------------------------------------------------------------- #
def scan_inventory(input_dir) -> dict | None:
    root = Path(input_dir)
    if not root.exists():
        return None
    files, dirs = [], []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            target = None
            try:
                target = os.readlink(p)
            except OSError:
                pass
            files.append({"source": rel, "abs_path": str(p), "kind": "symlink",
                          "is_input_spec": rel in INPUT_SPEC_BASENAMES,
                          "target": target, "sha256": None, "size_bytes": None,
                          "permissions": stat_permissions(p), "errors": [],
                          "encoding": None, "text_preview": None, "has_text_layer": None})
        elif p.is_file():
            files.append(_inventory_file(p, rel))
        elif p.is_dir():
            dirs.append(rel)
    totals = {"files": len(files),
              "bytes": sum((f.get("size_bytes") or 0) for f in files),
              "dirs": len(dirs)}
    return {"schema": "inventory-v1", "generated_at": utc_now(), "root": str(root),
            "totals": totals, "dirs": dirs, "files": files}


def _inventory_file(p: Path, rel: str) -> dict:
    ext = p.suffix.lower()
    kind, is_text = EXT_KIND.get(ext, ("other", False))
    is_spec = rel in INPUT_SPEC_BASENAMES
    entry = {"source": rel, "abs_path": str(p), "kind": kind, "is_text": is_text,
             "is_input_spec": is_spec, "extension": ext, "sha256": None, "size_bytes": None,
             "permissions": stat_permissions(p), "errors": [], "encoding": None,
             "text_preview": None, "has_text_layer": None}
    digest, err = sha256_of(p)
    if err:
        entry["errors"].append(f"hash failed: {err}")
    else:
        entry["sha256"] = digest
    entry["size_bytes"] = entry["permissions"].get("size_bytes")
    if not entry["permissions"].get("readable"):
        entry["errors"].append("unreadable")
        return entry
    if is_text and kind not in ("pdf",):
        r = read_text_bytes(p)
        entry["encoding"] = r["encoding"]
        if r["error"]:
            entry["errors"].append(f"encoding: {r['error']}")
        entry["text_preview"] = r["text"][:2000] if r["text"] else ""
        if r["truncated"]:
            entry["errors"].append("text truncated at 2MB")
        entry["has_text_layer"] = True
    return entry


# --------------------------------------------------------------------------- #
# 元数据记录抽取（BibTeX / RIS / JSON 索引）
# --------------------------------------------------------------------------- #
def parse_bibtex(text: str) -> list:
    entries, start_re, i, n = [], re.compile(r"@\s*([A-Za-z]+)\s*\{"), 0, len(text)
    while i < n:
        m = start_re.search(text, i)
        if not m:
            break
        etype = m.group(1).lower()
        brace, depth, j = m.end() - 1, 0, m.end() - 1
        while j < n:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[brace:j + 1] if j < n else text[brace:]
        inner = body[1:-1] if body.endswith("}") else body[1:]
        key, _, rest = inner.partition(",")
        entries.append({"entry_type": etype, "key": key.strip(),
                        "fields": _parse_bib_fields(rest), "raw": body[:400]})
        i = (j + 1) if j < n else n
    return entries


_FIELD_RE = re.compile(r"\b([a-zA-Z][\w\-]*)\s*=\s*([{\"])")


def _parse_bib_fields(s: str) -> dict:
    fields, i = {}, 0
    while True:
        m = _FIELD_RE.search(s, i)
        if not m:
            break
        name = m.group(1).lower()
        opener = m.group(2)
        depth = 1 if opener == "{" else 0
        val_start, j = m.end(), m.end()
        while j < len(s):
            c = s[j]
            if opener == "{":
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            elif c == '"':
                break
            j += 1
        fields[name] = s[val_start:j].strip()
        i = j + 1
    return fields


def parse_ris(text: str) -> list:
    entries, cur = [], {}
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if len(line) < 2 or line[0] == " ":
            continue
        tag, val = line[:2].strip(), line[2:].strip()
        if tag == "TY":
            if cur:
                entries.append(cur)
            cur = {}
        if tag and tag != "ER":
            if tag in ("AU", "KW"):
                cur.setdefault(tag, []).append(val)
            else:
                cur[tag] = val
    if cur:
        entries.append(cur)
    return entries


def ris_to_bib(ris: dict) -> dict:
    tmap = {"jour": "article", "conf": "inproceedings", "ths": "thesis",
            "rprt": "techreport", "book": "book", "chap": "incollection",
            "data": "dataset", "generic": "misc", "elec": "misc"}
    fields = {"title": ris.get("TI") or ris.get("T1"),
              "journal": ris.get("JO") or ris.get("JF") or ris.get("T2"),
              "year": (ris.get("PY") or ris.get("Y1") or "")[:4],
              "doi": ris.get("DO"),
              "file": ris.get("L1") or ris.get("UR") or ris.get("M1"),
              "abstract": ris.get("AB") or ris.get("N2")}
    authors = ris.get("AU") or ris.get("A1")
    if isinstance(authors, list):
        fields["author"] = " and ".join(authors)
    return {"entry_type": tmap.get(ris.get("TY", "").lower(), "misc"),
            "key": ris.get("ID") or ris.get("AN") or "",
            "fields": fields, "raw": json.dumps(ris, ensure_ascii=False)[:400]}


def collect_metadata_records(files: list) -> tuple:
    records, locate_errors = [], []
    for f in files:
        if f.get("is_input_spec"):
            continue
        kind = f["kind"]
        if kind not in ("bibliography", "json", "xml", "metadata"):
            continue
        rel = f["source"]
        r = read_text_bytes(f["abs_path"])
        if r["error"]:
            locate_errors.append({"source": rel, "error": r["error"]})
            continue
        locator, text = rel, r["text"]
        if kind == "bibliography":
            if rel.lower().endswith(".ris"):
                for ris in parse_ris(text):
                    rec = ris_to_bib(ris)
                    rec["locator"] = locator
                    rec["quote"] = rec.pop("raw", "")[:200]
                    records.append(rec)
            else:
                for bib in parse_bibtex(text):
                    rec = dict(bib)
                    rec["locator"] = locator
                    records.append(rec)
        elif kind == "json":
            obj, err = load_json_file(f["abs_path"])
            if err is not None:
                locate_errors.append({"source": rel, "error": err})
                continue
            for item in _iter_json_records(obj):
                rec = _record_from_mapping(item, locator)
                if rec:
                    records.append(rec)
    return records, locate_errors


def _iter_json_records(obj):
    if isinstance(obj, dict):
        for key in ("items", "records", "entries", "documents", "library", "catalog", "publications", "data"):
            if isinstance(obj.get(key), list):
                for it in obj[key]:
                    yield it
                return
        yield obj
    elif isinstance(obj, list):
        for it in obj:
            yield it


def _stringify(v):
    if v is None:
        return None
    if isinstance(v, (str, int, float)):
        return str(v).strip() or None
    if isinstance(v, list):
        return " and ".join(str(x) for x in v if x) or None
    return None


def _record_from_mapping(obj: dict, locator: str):
    if not isinstance(obj, dict):
        return None
    def get(*keys):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
        return None
    title = _stringify(get("title", "Title", "name"))
    authors = _stringify(get("authors", "author", "creators", "creator"))
    year = _stringify(get("year", "date", "publicationYear"))
    doi = _stringify(get("doi", "DOI"))
    arx = _stringify(get("arxiv_id", "arxivId", "eprint", "arXiv"))
    journal = _stringify(get("journal", "venue", "publication", "containerTitle"))
    file_ref = _stringify(get("file", "path", "filename", "attachment", "url", "link"))
    entry_type = _stringify(get("type", "document_type", "itemType")) or "misc"
    if not (title or doi or authors or year):
        return None
    return {"entry_type": entry_type.lower(), "key": _stringify(get("key", "id", "key_name")) or "",
            "fields": {"title": title, "author": authors, "year": (year or "")[:4],
                       "journal": journal, "doi": doi, "arxiv_id": arx, "file": file_ref},
            "locator": locator, "quote": json.dumps(obj, ensure_ascii=False)[:200]}


# --------------------------------------------------------------------------- #
# 元数据信号抽取（文本/文件名）
# --------------------------------------------------------------------------- #
def extract_title_from_text(text: str) -> tuple:
    for line in text.splitlines()[:40]:
        s = line.strip()
        if not s or len(s) < 18:
            continue
        low = s.lower()
        if any(k in low for k in ("arxiv", "doi", "copyright", "©", "http", "correspond", "author")):
            continue
        return s[:300], s[:300]
    return None, None


def pick_year(text: str) -> tuple:
    m = YEAR_RE.search(text or "")
    return (m.group(0), m.group(0)) if m else (None, None)


def pick_doi(text: str) -> tuple:
    m = DOI_RE.search(text or "")
    if not m:
        return None, None
    return m.group(0).rstrip(".,;)]}>"), m.group(0)


def pick_arxiv(text: str) -> tuple:
    m = ARXIV_RE.search(text or "")
    return (m.group(1), m.group(0)) if m else (None, None)


def fields_from_filename(rel: str) -> dict:
    stem = Path(rel).stem
    base = re.sub(r"\s+", " ", stem).strip()
    out = {"_stem": base}
    doi, _ = pick_doi(base)
    arx, _ = pick_arxiv(base)
    yr, _ = pick_year(base)
    if doi:
        out["doi"] = doi
    if arx:
        out["arxiv_id"] = arx.split("v")[0]
    if yr:
        out["year"] = yr
    head = re.sub(r"arXiv[:/\s]*\d{4}\.\d+", "", base, flags=re.I)
    head = re.sub(r"\b10\.\d{4,9}/\S+", "", head, flags=re.I)
    head = re.sub(r"\b(19|20)\d{2}\b", " ", head)
    head = re.sub(r"(preprint|final|supplement\w*|manuscript|\bv\d+)", " ", head, flags=re.I)
    head = re.sub(r"[_\-\.]+", " ", head).strip()
    toks = [t for t in head.split() if len(t) > 1]
    if toks and len(toks) <= 4:
        out["_author_guess"] = " ".join(toks[:2])
    return out


# --------------------------------------------------------------------------- #
# 决策：decisions.json（每字段带 evidence）
# --------------------------------------------------------------------------- #
def _field_obj(value, locator, quote, method):
    if value in (None, ""):
        return {"value": None, "not_reported": True, "evidence": []}
    return {"value": value, "evidence": [{"locator": locator, "quote": (quote or "")[:300], "method": method}]}


def build_decisions(files, records, text_dir, decisions_override) -> dict:
    by_stem_meta = {}
    for r in records:
        fref = (r.get("fields", {}).get("file") or "")
        if fref:
            base = Path(fref.replace("file://", "").split(":")[-1]).name
            by_stem_meta.setdefault(base, r)
    entries = []
    for f in files:
        if f.get("is_input_spec"):
            continue
        rel = f["source"]
        if f["kind"] == "symlink":
            entries.append({"source": rel, "kind": "symlink", "sha256": f.get("sha256"),
                            "doc_type": "alias", "topics": [], "version_role": None,
                            "fields": {}, "has_text_layer": None, "meta_type": None,
                            "alias_target": f.get("target")})
            continue
        text = f.get("text_preview") or ""
        has_text_layer = f.get("has_text_layer")
        if not text and f.get("sha256") and text_dir:
            txt_path = Path(text_dir) / f"{f['sha256']}.txt"
            if txt_path.is_file():
                r = read_text_bytes(txt_path)
                if not r["error"]:
                    text = r["text"]
                    if f["kind"] == "pdf":
                        has_text_layer = bool(text.strip())
        rec = by_stem_meta.get(Path(rel).name)
        if not rec and f.get("sha256"):
            for r in records:
                if (r.get("fields", {}).get("doi") and pick_doi(text)[0] == r["fields"]["doi"]):
                    rec = r
                    break
        fields = {}
        if rec:
            rf = rec.get("fields", {})
            fields["title"] = _field_obj(rf.get("title"), rec["locator"], rf.get("title"), "metadata_record")
            fields["authors"] = _field_obj(rf.get("author"), rec["locator"], rf.get("author"), "metadata_record")
            fields["year"] = _field_obj(rf.get("year"), rec["locator"], rf.get("year"), "metadata_record")
            fields["journal"] = _field_obj(rf.get("journal"), rec["locator"], rf.get("journal"), "metadata_record")
            fields["doi"] = _field_obj(rf.get("doi"), rec["locator"], rf.get("doi"), "metadata_record")
            fields["arxiv_id"] = _field_obj(rf.get("arxiv_id"), rec["locator"], rf.get("arxiv_id"), "metadata_record")
        if text and f["kind"] in CONTENT_KINDS:
            title, tq = extract_title_from_text(text)
            if not fields.get("title") or fields["title"]["value"] is None:
                fields["title"] = _field_obj(title, f"{rel}:text", tq, "text_heuristic")
            if not fields.get("year") or fields["year"]["value"] is None:
                yr, yq = pick_year(text)
                fields["year"] = _field_obj((yr or "")[:4], f"{rel}:text", yq, "text_heuristic")
            if not fields.get("doi") or fields["doi"]["value"] is None:
                d, dq = pick_doi(text)
                fields["doi"] = _field_obj(d, f"{rel}:text", dq, "text_heuristic")
            if not fields.get("arxiv_id") or fields["arxiv_id"]["value"] is None:
                a, aq = pick_arxiv(text)
                fields["arxiv_id"] = _field_obj(a, f"{rel}:text", aq, "text_heuristic")
        ffn = fields_from_filename(rel)
        if not fields.get("doi") or fields["doi"]["value"] is None:
            fields["doi"] = _field_obj(ffn.get("doi"), f"{rel}:name", ffn.get("doi"), "filename_heuristic")
        if not fields.get("arxiv_id") or fields["arxiv_id"]["value"] is None:
            fields["arxiv_id"] = _field_obj(ffn.get("arxiv_id"), f"{rel}:name", ffn.get("arxiv_id"), "filename_heuristic")
        if not fields.get("year") or fields["year"]["value"] is None:
            fields["year"] = _field_obj((ffn.get("year") or "")[:4], f"{rel}:name", ffn.get("year"), "filename_heuristic")
        if (not fields.get("authors") or fields["authors"]["value"] is None) and ffn.get("_author_guess"):
            fields["authors"] = _field_obj(ffn["_author_guess"], f"{rel}:name", ffn["_stem"], "filename_heuristic")
        if not fields.get("title") or fields["title"]["value"] is None:
            fields["title"] = _field_obj(ffn.get("_stem") or None, f"{rel}:name", ffn.get("_stem"), "filename_heuristic")
        entries.append({"source": rel, "kind": f["kind"], "sha256": f.get("sha256"),
                        "doc_type": detect_document_type(f, rec.get("entry_type") if rec else None),
                        "topics": detect_topics(f, fields),
                        "version_role": detect_version_role(f, fields),
                        "fields": fields, "has_text_layer": has_text_layer,
                        "meta_type": rec.get("entry_type") if rec else None,
                        "alias_target": None})
    pending = []
    if decisions_override:
        items = decisions_override.get("entries", decisions_override if isinstance(decisions_override, list) else [])
        for d in items:
            if not isinstance(d, dict):
                continue
            src, fe, val, ev = d.get("source"), d.get("field"), d.get("value"), d.get("evidence")
            if not isinstance(ev, list) or not ev:
                pending.append({"source": src, "field": fe, "value": val, "reason": "decision without evidence"})
                continue
            for ent in entries:
                if ent["source"] == src:
                    ent["fields"][fe] = {"value": val, "evidence": ev, "method": "decisions.json"}
                    break
    return {"schema": "decisions-v1", "generated_at": utc_now(), "entries": entries,
            "pending_decisions": pending}


def detect_document_type(entry: dict, meta_type) -> str:
    name = Path(entry["source"]).name.lower()
    if meta_type and entry["kind"] in CONTENT_KINDS:
        dt = BIBTYPE_TO_DOCTYPE.get(meta_type.lower())
        if dt:
            return dt
    for dt, pats in DOCTYPE_FILENAME_RULES:
        if any(re.search(p, name) for p in pats):
            return dt
    base = KIND_TO_DOCTYPE.get(entry["kind"])
    if base and base != "unknown_publication":
        return base
    if entry["kind"] == "pdf":
        return "unknown_publication"
    return base or "other"


def detect_topics(entry: dict, fields: dict) -> list:
    text = " ".join([Path(entry["source"]).stem, fields.get("title", {}).get("value") or "",
                     (entry.get("text_preview") or "")[:2000]]).lower()
    hits = [t for t, pats in TOPIC_TERMS.items() if any(re.search(p, text) for p in pats)]
    return hits[:3]


def detect_version_role(entry: dict, fields: dict) -> str | None:
    txt = " ".join([Path(entry["source"]).stem, fields.get("title", {}).get("value") or "",
                    fields.get("journal", {}).get("value") or ""]).lower()
    for role, pats in ROLE_MARKERS:
        if any(re.search(p, txt) for p in pats):
            return role
    return None


# --------------------------------------------------------------------------- #
# 计划：plan.json（声明式变换，落地前可审计）
# --------------------------------------------------------------------------- #
def build_plan(inventory, decisions, hierarchy) -> dict:
    entries_by_src = {e["source"]: e for e in decisions["entries"]}
    material_files = [f for f in inventory["files"] if not f.get("is_input_spec")]

    hashes = {}
    for f in material_files:
        hashes.setdefault(f["sha256"], []).append(f["source"])
    dup_of = {}
    for digest, srcs in hashes.items():
        if not digest:
            continue
        keep = sorted(srcs)[0]
        for s in srcs[1:]:
            dup_of[s] = keep

    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    idx_doi, idx_arxiv, idx_title = {}, {}, {}
    for f in material_files:
        if f["kind"] not in CONTENT_KINDS:
            continue
        e = entries_by_src.get(f["source"])
        if not e:
            continue
        fl = e.get("fields", {})
        doi = fl.get("doi", {}).get("value")
        arx = fl.get("arxiv_id", {}).get("value")
        title = fl.get("title", {}).get("value")
        year = fl.get("year", {}).get("value")
        if doi:
            if doi in idx_doi:
                union(f["source"], idx_doi[doi])
            else:
                idx_doi[doi] = f["source"]
        if arx:
            if arx in idx_arxiv:
                union(f["source"], idx_arxiv[arx])
            else:
                idx_arxiv[arx] = f["source"]
        tk = norm_key((title or "") + "|" + (year or ""))
        if title and year and len(norm_key(title)) >= 8:
            if tk in idx_title:
                union(f["source"], idx_title[tk])
            else:
                idx_title[tk] = f["source"]
    gmap = {}
    for f in material_files:
        gmap.setdefault(find(f["source"]), []).append(f["source"])
    version_groups = []
    src2gid = {}
    gid = 0
    for root, members in sorted(gmap.items()):
        if len(members) <= 1:
            continue
        gid += 1
        group_id = f"vgrp-{gid:04d}"
        version_groups.append({"group_id": group_id, "members": sorted(members)})
        for m in members:
            src2gid[m] = group_id

    decisions_out = []
    for f in material_files:
        src = f["source"]
        e = entries_by_src.get(src) or {}
        fields = e.get("fields", {})
        if f["kind"] == "symlink":
            action = "alias"
        elif dup_of.get(src):
            action = "duplicate"
        elif f["permissions"].get("readable") and not f.get("errors"):
            action = "keep"
        else:
            action = "skip"
        doctype = e.get("doc_type") or detect_document_type(f, e.get("meta_type"))
        topics = e.get("topics") or []
        role = e.get("version_role")
        cat_parts = []
        for dim in hierarchy:
            if dim == "document_type":
                cat_parts.append(doctype)
            elif dim == "topic":
                cat_parts.append(topics[0] if topics else "unclassified")
            elif dim == "year":
                cat_parts.append((fields.get("year", {}).get("value") or "undated")[:4])
            elif dim == "kind":
                cat_parts.append(f["kind"])
        category_path = "/".join(slugify(p, 60) for p in cat_parts) or "unclassified"
        first_author = lastname(fields.get("authors", {}).get("value"))
        year = fields.get("year", {}).get("value") or "na"
        title = fields.get("title", {}).get("value")
        name_base = slugify(title) if title else slugify(Path(src).stem)
        suggested_name = f"{first_author}-{year}-{name_base}{f['extension'] or ''}"[:FILENAME_MAX]
        if not suggested_name.strip("-_."):
            suggested_name = slugify(Path(src).name)
        target_relpath = f"{category_path}/{suggested_name}"
        basis = []
        if f["sha256"]:
            basis.append("rule:hash:sha256")
        if e.get("fields") and any(not v.get("not_reported") for v in fields.values()):
            basis.append("rule:metadata:linked")
        if dup_of.get(src):
            basis.append("rule:dedupe:byte-identical")
        if role:
            basis.append(f"rule:version-role:{role}")
        evidence = []
        for key, obj in fields.items():
            if isinstance(obj, dict):
                for ev in obj.get("evidence", []):
                    evidence.append({"field": key, "locator": ev["locator"],
                                     "quote": ev["quote"], "method": ev.get("method")})
        decisions_out.append({
            "id": f"dec-{len(decisions_out) + 1:04d}", "source": src, "kind": f["kind"],
            "sha256": f["sha256"], "action": action, "duplicate_of": dup_of.get(src),
            "document_type": doctype, "topics": topics, "version_role": role,
            "version_group": src2gid.get(src), "category_path": category_path,
            "suggested_name": suggested_name, "target_relpath": target_relpath,
            "permissions": f["permissions"], "errors": f.get("errors", []),
            "has_text_layer": e.get("has_text_layer"), "alias_target": e.get("alias_target"),
            "basis": basis, "evidence": evidence, "state": "planned", "conflicts": [],
        })
    _resolve_name_conflicts(decisions_out)
    return {"schema": "plan-v1", "generated_at": utc_now(), "hierarchy": list(hierarchy),
            "totals": {"files": len(decisions_out),
                       "keep": sum(1 for d in decisions_out if d["action"] == "keep"),
                       "duplicate": sum(1 for d in decisions_out if d["action"] == "duplicate"),
                       "alias": sum(1 for d in decisions_out if d["action"] == "alias"),
                       "skip": sum(1 for d in decisions_out if d["action"] == "skip")},
            "version_groups": version_groups, "decisions": decisions_out}


def _resolve_name_conflicts(decisions) -> None:
    used = {}
    for d in decisions:
        if d["action"] != "keep":
            continue
        base = d["target_relpath"]
        if base in used:
            used[base] += 1
            p = Path(base)
            new = str(p.with_name(f"{p.stem}-{used[base]}{p.suffix}"))
            while any(x["target_relpath"] == new for x in decisions):
                used[base] += 1
                new = str(p.with_name(f"{p.stem}-{used[base]}{p.suffix}"))
            d["target_relpath"] = new
            d["suggested_name"] = Path(new).name
            d["conflicts"].append({"type": "same-name-different-content",
                                   "note": "resolved with -N suffix"})
        else:
            used[base] = 1


# --------------------------------------------------------------------------- #
# 执行：--apply 重放 plan → rollback.json
# --------------------------------------------------------------------------- #
def apply_plan(plan, input_dir, output_dir) -> tuple:
    created, already = [], []
    library_root = Path(output_dir) / "library"

    def rel_of(p: Path) -> str:
        try:
            return p.relative_to(Path(output_dir)).as_posix()
        except ValueError:
            return p.as_posix()

    for d in plan["decisions"]:
        if d["action"] != "keep":
            continue
        src = Path(input_dir) / d["source"]
        tgt = library_root / d["target_relpath"]
        if tgt.exists():
            existing_hash, _ = sha256_of(tgt)
            if existing_hash == d["sha256"]:
                d["state"] = "already_present"
                d["applied_target"] = d["target_relpath"]
                already.append({"target": rel_of(tgt), "source": d["source"], "hash_match": True})
                continue
            base = tgt
            n = 1
            while True:
                cand = base.with_name(f"{base.stem}-{n}{base.suffix}")
                if not cand.exists():
                    tgt = cand
                    break
                eh, _ = sha256_of(cand)
                if eh == d["sha256"]:
                    d["state"] = "already_present"
                    d["applied_target"] = rel_of(cand).replace("library/", "", 1)
                    already.append({"target": rel_of(cand), "source": d["source"], "hash_match": True})
                    break
                n += 1
            else:
                continue
            if d.get("state") == "already_present":
                continue
        try:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)
            d["state"] = "applied"
            d["applied_target"] = rel_of(tgt).replace("library/", "", 1)
            created.append({"target": rel_of(tgt), "source": d["source"],
                            "sha256": d["sha256"], "op": "copy"})
        except OSError as e:
            d["state"] = "failed"
            d["apply_error"] = str(e)
    rollback = {"schema": "rollback-v1", "generated_at": utc_now(),
                "note": "delete listed targets (and prune empty dirs) to roll back; /input never modified",
                "created": created, "already_present": already, "input_untouched": True}
    return rollback, plan


# --------------------------------------------------------------------------- #
# 汇总：result.json
# --------------------------------------------------------------------------- #
def assemble_result(case_id, contract, inventory, decisions, plan, rollback, failures,
                    degradations, errors, pending, sources, uncertainty, change_log, runtime, status):
    catalog = []
    metadata = {}
    for d in plan["decisions"]:
        ed = next((e for e in decisions["entries"] if e["source"] == d["source"]), {})
        fld = ed.get("fields", {})
        catalog.append({
            "source": d["source"], "kind": d["kind"],
            "document_type": d["document_type"], "topics": d["topics"],
            "category_path": d["category_path"], "suggested_name": d["suggested_name"],
            "action": d["action"], "state": d.get("state", "planned"),
            "duplicate_of": d.get("duplicate_of"), "alias_target": d.get("alias_target"),
            "version_group": d.get("version_group"), "version_role": d.get("version_role"),
            "sha256": d.get("sha256"), "has_text_layer": d.get("has_text_layer"),
            "applied_target": d.get("applied_target"),
            "errors": d.get("errors", []), "conflicts": d.get("conflicts", []),
        })
        metadata[d["source"]] = {k: (v.get("value") if isinstance(v, dict) else v)
                                 for k, v in fld.items() if not k.startswith("_")}
    for f in inventory["files"]:
        if f.get("is_input_spec"):
            catalog.append({"source": f["source"], "kind": f["kind"], "document_type": "input_spec",
                            "topics": [], "category_path": None, "suggested_name": f["source"],
                            "action": "input_spec", "state": "planned", "duplicate_of": None,
                            "alias_target": None, "version_group": None, "version_role": None,
                            "sha256": f.get("sha256"), "has_text_layer": None, "applied_target": None,
                            "errors": f.get("errors", []), "conflicts": []})

    hashes = {d["source"]: d.get("sha256") for d in plan["decisions"] if d.get("sha256")}
    permissions = {d["source"]: d["permissions"] for d in plan["decisions"]}
    locators = []
    for e in decisions["entries"]:
        for k, v in e.get("fields", {}).items():
            if isinstance(v, dict):
                for ev in v.get("evidence", []):
                    locators.append({"source": e["source"], "field": k, "locator": ev["locator"],
                                     "quote": ev["quote"], "method": ev.get("method")})
    versions = []
    for g in plan.get("version_groups", []):
        roles = {d["source"]: d.get("version_role") for d in plan["decisions"] if d["source"] in g["members"]}
        versions.append({"group_id": g["group_id"], "members": g["members"], "roles": roles})

    library = {"catalog": catalog, "hierarchy": {"dimensions": plan.get("hierarchy")},
               "metadata": metadata, "versions": versions,
               "plan": {"path": "/app/work/plan.json",
                        "note": "auditable dry-run transformation; see rollback.json for applied outcomes"}}
    evidence_obj = {"hashes": hashes, "permissions": permissions, "locators": locators}
    operations = []
    if rollback:
        for c in rollback.get("created", []):
            operations.append({"op": "copy", "time": rollback.get("generated_at"),
                               "source": c["source"], "target": c["target"], "sha256": c.get("sha256")})
        for a in rollback.get("already_present", []):
            operations.append({"op": "already_present", "time": rollback.get("generated_at"),
                               "source": a["source"], "target": a["target"]})
    if not operations:
        operations = [{"op": "dry_run", "note": "no files copied; run with --apply to materialize library/"}]

    data = {"library": library, "evidence": evidence_obj, "operations": operations, "failures": failures}
    result = {"case_id": case_id}
    for key in contract:
        result[key] = data[key] if key in data else _deep_placeholder(contract[key])
    extras = {"status": status, "sources": sources, "uncertainty": uncertainty,
              "change_log": change_log, "errors": errors, "degradations": degradations,
              "pending_human": pending, "runtime": runtime}
    for k, v in extras.items():
        result[k] = v
    return result


def _deep_placeholder(v):
    if isinstance(v, dict):
        return {k: _deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def make_empty_state(case_id, contract) -> dict:
    data = {"library": {}, "evidence": {}, "operations": [],
            "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                         "duplicates": [], "recoveries": []},
            "status": "partial", "sources": [], "uncertainty": {}, "change_log": [],
            "errors": [], "degradations": [], "pending_human": [], "runtime": {}}
    result = {"case_id": case_id or "unknown"}
    for key in contract:
        result[key] = data.get(key) if key in data else _deep_placeholder(contract[key])
    for extra in EXTRAS:
        result.setdefault(extra, data.get(extra))
    return result


def compute_failures(plan) -> dict:
    skipped, conflicts, perm_errors, duplicates, recoveries = [], [], [], [], []
    for d in plan["decisions"]:
        src = d["source"]
        if d["action"] == "skip":
            skipped.append({"source": src, "errors": d.get("errors", []), "reason": "unreadable or error"})
        if d.get("duplicate_of"):
            duplicates.append({"source": src, "duplicate_of": d["duplicate_of"], "sha256": d.get("sha256")})
        if not d["permissions"].get("readable") or d["permissions"].get("stat_ok") is False:
            perm_errors.append({"source": src, "permissions": d["permissions"]})
        if d.get("conflicts"):
            conflicts.append({"source": src, "conflicts": d["conflicts"]})
    recoveries = [{"note": "no destructive operations; /input never modified"}]
    return {"skipped": skipped, "conflicts": conflicts, "permission_errors": perm_errors,
            "duplicates": duplicates, "recoveries": recoveries}


# --------------------------------------------------------------------------- #
# 管道入口
# --------------------------------------------------------------------------- #
def run_pipeline(input_dir, output_dir, work_dir, task_path=None, decisions_path=None,
                 hierarchy=None, apply=False) -> dict:
    import time as _time
    t0 = _time.time()
    change_log = []
    def log(event, detail=""):
        change_log.append({"time": utc_now(), "event": event, "detail": detail})

    out, work = Path(output_dir), Path(work_dir)
    text_dir = work / "text"
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    degradations, errors, pending = [], [], []
    sources, uncertainty = [], {"unclassified": 0, "missing_fields": {}, "no_text_layer": 0, "conflicts": 0}

    case, case_err = load_json_file(os.path.join(input_dir, "case.json"))
    contract, case_id, task_excerpt = dict(DEFAULT_CONTRACT), "unknown", None
    if case_err:
        degradations.append(f"case.json unreadable or invalid: {case_err}")
    elif isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("challenge_id") or "unknown"
        cc = case.get("output_contract") or case.get("outputContract") or case.get("output_schema") or case.get("contract")
        if isinstance(cc, dict):
            contract = cc
        if isinstance(case.get("task"), str):
            task_excerpt = case["task"][:500]
    if not isinstance(contract, dict) or not contract:
        contract = dict(DEFAULT_CONTRACT)
        degradations.append("output_contract missing or invalid; fell back to default contract")
    task_path = task_path or os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        r = read_text_bytes(task_path)
        if not r["error"]:
            task_excerpt = task_excerpt or r["text"][:500]
    log("read_case", f"case_id={case_id}")

    inventory = scan_inventory(input_dir)
    if inventory is None:
        status = "input_error"
        result = make_empty_state(case_id, contract)
        result["status"] = status
        result["errors"] = [f"input directory missing: {input_dir}"]
        result["runtime"] = {"seconds": _time.time() - t0, "note": "input missing"}
        write_json_atomic(out / "result.json", result)
        return result
    write_json_atomic(work / "inventory.json", inventory)
    log("inventory", inventory["totals"])

    material_files = [f for f in inventory["files"] if not f.get("is_input_spec")]
    if len(material_files) == 0:
        status = "insufficient_evidence"
        result = make_empty_state(case_id, contract)
        result["status"] = status
        result["errors"] = ["no material files under input directory (only input specs present)"]
        result["runtime"] = {"seconds": _time.time() - t0, "files": inventory["totals"]["files"]}
        write_json_atomic(out / "result.json", result)
        return result

    records, locate_errors = collect_metadata_records(inventory["files"])
    for le in locate_errors:
        degradations.append(f"metadata file skipped: {le['source']} ({le['error']})")
    write_json_atomic(work / "metadata_records.json",
                      {"schema": "metadata-records-v1", "generated_at": utc_now(), "records": records})
    log("metadata_records", f"count={len(records)}")

    decisions_override = None
    if decisions_path and os.path.isfile(decisions_path):
        decisions_override, derr = load_json_file(decisions_path)
        if derr:
            decisions_override = None
            degradations.append(f"decisions.json unreadable: {derr}")

    decisions = build_decisions(inventory["files"], records, text_dir, decisions_override)
    write_json_atomic(work / "decisions.json", decisions)
    pending.extend(decisions.get("pending_decisions", []))
    log("decisions", f"entries={len(decisions['entries'])} pending={len(decisions['pending_decisions'])}")

    if not hierarchy:
        hierarchy = ["document_type", "topic"]
    hierarchy = [h.strip() for h in hierarchy if h and h.strip()] or ["document_type", "topic"]
    plan = build_plan(inventory, decisions, hierarchy)
    write_json_atomic(work / "plan.json", plan)
    log("plan", plan["totals"])

    rollback = None
    if apply:
        rollback, plan = apply_plan(plan, input_dir, output_dir)
        write_json_atomic(work / "rollback.json", rollback)
        write_json_atomic(work / "plan.json", plan)
        log("apply", f"created={len(rollback['created'])} already_present={len(rollback['already_present'])}")
    else:
        write_json_atomic(work / "rollback.json",
                          {"schema": "rollback-v1", "generated_at": utc_now(), "dry_run": True,
                           "note": "no files copied; run with --apply"})

    failures = compute_failures(plan)
    for d in plan["decisions"]:
        ed = next((e for e in decisions["entries"] if e["source"] == d["source"]), {})
        fld = ed.get("fields", {})
        if d["document_type"] in ("unknown_publication", "other") or not d["topics"]:
            uncertainty["unclassified"] += 1
        if d.get("has_text_layer") is False:
            uncertainty["no_text_layer"] += 1
        for k, v in fld.items():
            if isinstance(v, dict) and v.get("not_reported"):
                uncertainty["missing_fields"][k] = uncertainty["missing_fields"].get(k, 0) + 1
        if d.get("conflicts"):
            uncertainty["conflicts"] += 1
    for e in decisions["entries"]:
        quotes = []
        for _k, v in e.get("fields", {}).items():
            if isinstance(v, dict):
                for ev in v.get("evidence", []):
                    if ev.get("quote"):
                        quotes.append(ev["quote"][:200])
        sources.append({"source": e["source"], "sha256": e.get("sha256"),
                        "locator": e["source"] + (":text" if e.get("has_text_layer") else ""),
                        "quotes": quotes[:5]})
    for le in locate_errors:
        errors.append({"source": le["source"], "error": le["error"]})
    for d in plan["decisions"]:
        if d["action"] == "skip":
            errors.append({"source": d["source"], "errors": d.get("errors")})

    degraded = bool(errors) or bool(degradations or pending) or bool(failures["skipped"]) or bool(failures["permission_errors"])
    unresolved = uncertainty["unclassified"] > 0
    status = "partial" if (degraded or unresolved) else "ok"
    log("finalize", f"status={status}")

    runtime = {"seconds": _time.time() - t0, "files": inventory["totals"]["files"],
               "material_files": len(material_files), "decisions": len(plan["decisions"]),
               "applied": len(rollback["created"]) if rollback else 0,
               "hierarchy": plan["hierarchy"]}

    result = assemble_result(case_id, contract, inventory, decisions, plan, rollback, failures,
                             degradations, errors, pending, sources, uncertainty, change_log, runtime, status)
    write_json_atomic(out / "result.json", result)
    return result
