# -*- coding: utf-8 -*-
"""fedsearch.py -- 多数据库联合检索核心库（仅标准库，确定性，无网络）。

职责：
  1. 发现 /input 下的材料并分类（Crossref/EuropePMC/PubMed/出版社 JSON、PDF、文本）
  2. 跨库字段归一（统一 title/authors/year/journal/doi/issn/doc_type 等语义）
  3. 论文身份去重（标识符精确 > 标题+一作+年份 > 标题模糊）
  4. 重复簇合并（按 DB 优先级 + 冲突消解，保留逐字段来源）
  5. 覆盖比较（每库 fields_missing、仅单库记录、系统性缺失字段）
  6. 证据记录（source_file + locator + 逐字 quote）

本模块不联网、不随机，任何输入的输出都确定；失败一律降级并标注，绝不编造。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "2.0.0"
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 输出契约默认键：若 /input/case.json 携带 output_contract，则其全部键必须原样回填。
DEFAULT_CONTRACT: List[str] = [
    "research_question", "search_scope", "candidates", "ranking",
    "evidence_records", "coverage", "empty_results", "conflicts",
    "missing_metadata", "permission_issues", "limitations",
]

# 固定字段：无论 output_contract 是否声明，都必须存在。
FIXED_KEYS: List[str] = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime",
]

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记 degradation

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, (list, tuple)):
        return _as_str(value[0])
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for k in ("title", "text", "value", "given", "family", "name", "volume",
                  "issue", "pages", "label", "content"):
            if k in value and _text_of(value[k]):
                parts.append(_text_of(value[k]))
        if not parts:
            return json.dumps(value, ensure_ascii=False)
        return " ".join(parts)
    if isinstance(value, (list, tuple)):
        return "; ".join(_text_of(x) for x in value if _text_of(x))
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _pick(obj: Dict[str, Any], *keys: str) -> Any:
    """依键序返回第一个非 None/非空的值；键可为 'a.b' 点分路径。"""
    for k in keys:
        if k in obj:
            v = obj[k]
        elif "." in k:
            v = _deep_get(obj, k)
        else:
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, dict)) and len(v) == 0:
            continue
        return v
    return None


def _deep_get(obj: Any, dotted: str, default: Any = None) -> Any:
    """按 'a.b.c' 路径取嵌套值，任一层缺失返回 default。"""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def norm_doi(doi: Any) -> Optional[str]:
    s = _as_str(doi)
    if not s:
        return None
    s = _DOI_PREFIX_RE.sub("", s).strip().lower()
    return s or None


def norm_issn(issn: Any) -> Optional[str]:
    s = _as_str(issn)
    if not s:
        return None
    s = re.sub(r"[^0-9Xx]", "", s).upper()
    if len(s) == 8:
        s = s[:4] + "-" + s[4:]
    return s or None


def norm_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        y = int(value)
        return y if 1000 <= y <= 2200 else None
    s = _as_str(value)
    if not s:
        return None
    m = re.search(r"(19|20)\d{2}", s)
    if not m:
        m = re.search(r"\d{4}", s)
    if m:
        y = int(m.group(0))
        return y if 1000 <= y <= 2200 else None
    return None


def norm_pages(value: Any) -> Optional[str]:
    s = _as_str(value)
    if not s:
        return None
    s = s.strip().rstrip(".").rstrip(";")
    return s or None


def tokenize(text: Any) -> List[str]:
    s = _text_of(text).lower()
    toks = re.findall(r"[a-z0-9\u4e00-\u9fff]+", s)
    stop = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to",
            "with", "by", "from", "as", "is", "are", "was", "were", "be"}
    return [t for t in toks if t not in stop and len(t) >= 2]


def _title_key(title: Any) -> str:
    s = _text_of(title).lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return " ".join(s.split())


def _surname(author: Any) -> str:
    if isinstance(author, dict):
        fam = _as_str(_pick(author, "family", "lastname", "last", "surname")) or ""
        giv = _as_str(_pick(author, "given", "firstname", "first", "initials")) or ""
        full = _as_str(_pick(author, "name", "fullname", "full_name")) or ""
        if fam or giv:
            return (fam or giv).lower().strip(" ,.")
        if full:
            return full.split(",")[0].strip().lower() if "," in full else full.split()[-1].strip().lower()
        return ""
    s = _as_str(author) or ""
    s = s.strip().lower()
    if "," in s:
        return s.split(",")[0].strip()
    parts = s.split()
    return parts[-1].strip() if parts else s


def _all_surnames(authors: Any) -> List[str]:
    out: List[str] = []
    for a in _as_list(authors):
        s = _surname(a)
        if s:
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# 文件发现与解析
# --------------------------------------------------------------------------- #
def discover_input_files(input_dir: Path) -> List[Path]:
    if not input_dir.exists():
        return []
    out: List[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if p.is_file():
            out.append(p)
    return out


def classify_file(p: Path) -> str:
    suffix = p.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".txt", ".md", ".csv", ".tsv", ".xml", ".ris", ".bib"):
        return "text"
    return "other"


def load_json(path: Path) -> Tuple[Optional[Any], List[str]]:
    errs: List[str] = []
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, ["read_error:%s:%s" % (path.name, e)]
    if len(raw) > MAX_FILE_BYTES:
        return None, ["too_large:%d" % len(raw)]
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            obj = json.loads(text)
            return obj, []
        except UnicodeDecodeError:
            continue
        except ValueError as e:
            return None, ["bad_json:%s" % e]
    return None, ["bad_encoding"]


def read_text(path: Path) -> Tuple[str, List[str]]:
    degs: List[str] = []
    try:
        raw = path.read_bytes()
    except OSError as e:
        return "", ["read_error:%s:%s" % (path.name, e)]
    if len(raw) > MAX_FILE_BYTES:
        return "", ["too_large:%d" % len(raw)]
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ["bad_encoding_replaced"]


def parse_pdf_text(path: Path) -> Tuple[str, List[str]]:
    """优先用 pdftotext 提取；缺工具则仅列路径并降级。"""
    degs: List[str] = []
    import shutil
    import subprocess
    exe = shutil.which("pdftotext")
    if not exe:
        return "", ["missing_pdftotext:%s" % path.name]
    try:
        proc = subprocess.run(
            [exe, "-layout", str(path), "-"], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", ["pdftotext_failed:%s:%s" % (path.name, e)]
    if proc.returncode != 0:
        return "", ["pdftotext_rc:%d" % proc.returncode]
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return proc.stdout.decode(enc), []
        except UnicodeDecodeError:
            continue
    return proc.stdout.decode("utf-8", errors="replace"), []


# --------------------------------------------------------------------------- #
# 响应分类与记录抽取
# --------------------------------------------------------------------------- #
def classify_response(obj: Any) -> str:
    if not isinstance(obj, dict):
        return "generic"
    if "message" in obj and isinstance(obj.get("message"), dict):
        return "crossref"
    if "resultList" in obj and isinstance(obj.get("resultList"), dict):
        return "europepmc"
    res = obj.get("result")
    if isinstance(res, dict) and ("uids" in res or any(k.isdigit() for k in res)):
        return "pubmed_esummary"
    if "esearchresult" in obj:
        return "pubmed_esearch"
    # 出版社 / 通用：有名片壳即可
    for k in ("items", "results", "records", "documents", "publications", "hits"):
        if k in obj and isinstance(obj[k], list):
            return "publisher"
    return "generic"


def _generic_record_lists(obj: Any, path: str = "") -> List[Tuple[List[Any], str]]:
    """递归搜集「书目记录列表」，返回 (列表, 定位路径)。"""
    found: List[Tuple[List[Any], str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            loc = (path + "." + k) if path else k
            if isinstance(v, dict):
                found.extend(_generic_record_lists(v, loc))
            elif isinstance(v, list):
                if v and all(isinstance(x, dict) for x in v):
                    # 若列表元素像书目（带 title 或 doi/年份），视为记录列表
                    if any(_looks_bib(x) for x in v):
                        found.append((v, loc))
                    else:
                        found.extend(_generic_record_lists(v, loc))
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            loc = "%s[%d]" % (path, i)
            if isinstance(x, (dict, list)):
                found.extend(_generic_record_lists(x, loc))
    return found


def _looks_bib(rec: Any) -> bool:
    if not isinstance(rec, dict):
        return False
    has_title = any(k in rec for k in ("title", "Title", "articleTitle", "title_display"))
    has_id = any(k in rec for k in ("doi", "DOI", "pmid", "PMID", "pmcid", "PMCID",
                                    "DOI", "id", "uid", "year", "pubyear", "pubdate"))
    return has_title and has_id


def extract_records(obj: Any) -> List[Tuple[Dict[str, Any], str]]:
    """返回 [(原始记录 dict, locator)]。"""
    db = classify_response(obj)
    if db == "crossref":
        items = _deep_get(obj, "message.items", [])
        out = []
        for i, rec in enumerate(items):
            if isinstance(rec, dict):
                out.append((rec, "message.items[%d]" % i))
        return out
    if db == "europepmc":
        res = _deep_get(obj, "resultList.result", [])
        out = []
        for i, rec in enumerate(res):
            if isinstance(rec, dict):
                out.append((rec, "resultList.result[%d]" % i))
        return out
    if db == "pubmed_esummary":
        res = obj.get("result", {}) if isinstance(obj, dict) else {}
        uids = res.get("uids") or []
        out = []
        for uid in uids:
            rec = res.get(str(uid))
            if isinstance(rec, dict):
                rec = dict(rec)
                rec.setdefault("uid", uid)
                out.append((rec, "result[\"%s\"]" % uid))
        if not out:
            # 无 uids 键时按数字键兜底
            for k, v in sorted(res.items(), key=lambda kv: kv[0]):
                if k != "uids" and isinstance(v, dict) and str(k).isdigit():
                    out.append((v, "result[\"%s\"]" % k))
        return out
    if db == "pubmed_esearch":
        ids = _deep_get(obj, "esearchresult.idlist", [])
        out = []
        for i, uid in enumerate(ids):
            out.append(({"pmid": _as_str(uid)}, "esearchresult.idlist[%d]" % i))
        return out
    # publisher / generic
    for key in ("items", "results", "records", "documents", "publications", "hits"):
        if isinstance(obj, dict) and isinstance(obj.get(key), list):
            out = []
            for i, rec in enumerate(obj[key]):
                if isinstance(rec, dict) and _looks_bib(rec):
                    out.append((rec, "%s[%d]" % (key, i)))
            if out:
                return out
    # 兜底递归
    out = []
    for lst, loc in _generic_record_lists(obj):
        for i, rec in enumerate(lst):
            if isinstance(rec, dict):
                out.append((rec, "%s[%d]" % (loc, i)))
    return out


def extract_request(obj: Any) -> Dict[str, Any]:
    """从响应中抽取请求/查询上下文（存在则回填，不存在留 null）。"""
    if not isinstance(obj, dict):
        return {}
    req: Dict[str, Any] = {}
    for k in ("query", "request", "term", "searchTerm", "search_term",
              "esearchresult", "queryParams"):
        if k in obj:
            v = obj[k]
            if isinstance(v, dict) and "querykey" in v:
                req.setdefault("query_key", _as_str(_pick(v, "querykey", "term", "query")))
            elif k == "esearchresult" and isinstance(v, dict):
                req.setdefault("query_key", _as_str(_pick(v, "querykey")))
                req.setdefault("count", _as_str(_pick(v, "count", "retmax")))
            else:
                req.setdefault("raw_query", _as_str(v))
    return req


# --------------------------------------------------------------------------- #
# 字段归一
# --------------------------------------------------------------------------- #
def _norm_authors(raw: Dict[str, Any], db: str) -> List[str]:
    """输出归一作者列表，统一为 'Family Name, Given Name'。"""
    source = None
    if db == "crossref":
        source = _pick(raw, "author", "editor", "contributor")
    elif db == "europepmc":
        source = _pick(raw, "authorList", "authors")
        if source is None:
            source = _pick(raw, "authorString")
    elif db in ("pubmed_esummary", "pubmed_esearch"):
        source = _pick(raw, "authors", "author")
    else:
        source = _pick(raw, "authors", "author", "creators", "creator",
                       "contributors", "byline", "editors")

    parsed: List[str] = []
    if isinstance(source, dict) and "author" in source:
        source = source["author"]
    # PubMed / EuropePMC 的 name/authorString 多为「姓 首字母」，按 surname-first 解析
    surname_first = db in ("europepmc", "pubmed_esummary", "pubmed_esearch")

    if isinstance(source, str) and source:
        if db == "europepmc" and "," in source:
            # EuropePMC authorString: "Frangoul H, Altshuler D, Cappellini MD"
            for seg in source.split(","):
                name = _author_surname_first(seg)
                if name and name not in parsed:
                    parsed.append(name)
            return parsed
        for seg in re.split(r"\s*;\s*", source):
            name = _author_to_label(seg, surname_first=surname_first)
            if name and name not in parsed:
                parsed.append(name)
        return parsed

    for item in _as_list(source):
        name = _author_to_label(item, surname_first=surname_first)
        if name and name not in parsed:
            parsed.append(name)
    return parsed


def _author_surname_first(s: Any) -> str:
    """把 'Family Initials'（如 'Frangoul H'）转成 'Family, H'。"""
    s = _as_str(s) or ""
    s = s.strip().strip(".").strip()
    if not s:
        return ""
    toks = s.split()
    if len(toks) == 1:
        return toks[0]
    return "%s, %s" % (toks[0], " ".join(toks[1:]))


def _author_to_label(author: Any, surname_first: bool = False) -> str:
    if isinstance(author, dict):
        fam = _as_str(_pick(author, "family", "lastname", "last", "surname")) or ""
        giv = _as_str(_pick(author, "given", "firstname", "first",
                            "givenNames", "initials")) or ""
        name = _as_str(_pick(author, "name", "fullname", "full_name")) or ""
        if fam or giv:
            return ("%s, %s" % (fam, giv)).strip(", ")
        if name:
            if surname_first:
                return _author_surname_first(name)
            return _author_to_label(name)
        return ""
    s = _as_str(author) or ""
    s = s.strip().strip(".").strip()
    if not s:
        return ""
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        return "%s, %s" % (parts[0], parts[1]) if len(parts) == 2 else parts[0]
    toks = s.split()
    if len(toks) >= 3 and toks[-1] in ("Jr", "Sr", "III", "II"):
        toks = toks[:-1]
    if len(toks) >= 2:
        if surname_first:
            return _author_surname_first(s)
        return "%s, %s" % (toks[-1], " ".join(toks[:-1]))
    return s


def _norm_year_raw(raw: Dict[str, Any], db: str) -> Optional[int]:
    val: Any = None
    if db == "crossref":
        val = _pick(raw, "issued", "published-print", "published-online",
                    "published", "coverDate")
        if isinstance(val, dict) and "date-parts" in val:
            val = val["date-parts"]
        elif isinstance(val, dict):
            val = _pick(val, "date-parts", "year", "date")
    elif db == "europepmc":
        val = _pick(raw, "pubYear", "year", "pubDate", "firstPublicationDate",
                    "journalInfo.dateOfPublication", "journalInfo.printPublicationDate")
    elif db in ("pubmed_esummary", "pubmed_esearch"):
        val = _pick(raw, "pubdate", "epubdate", "sortpubdate", "year", "pubDate")
    else:
        val = _pick(raw, "year", "publication_year", "pubYear", "pubdate",
                    "publishedYear", "date", "publication_date")
    if isinstance(val, list):
        val = val[0] if val else None
        if isinstance(val, list) and val:
            val = val[0]
    return norm_year(val)


def _norm_doi_raw(raw: Dict[str, Any], db: str) -> Optional[str]:
    if db in ("pubmed_esummary", "pubmed_esearch"):
        for artid in _as_list(raw.get("articleids") or raw.get("articleIds")):
            if isinstance(artid, dict):
                if _as_str(_pick(artid, "idtype", "idType", "type")).lower() == "doi":
                    return norm_doi(_pick(artid, "value", "id"))
        # 可能直接有 doi 字段
    return norm_doi(_pick(raw, "DOI", "doi", "publicationDOI", "doiValue", "prism:doi"))


def _norm_pmid(raw: Dict[str, Any], db: str) -> Optional[str]:
    if db == "europepmc":
        return _as_str(_pick(raw, "pmid", "id"))
    if db in ("pubmed_esummary", "pubmed_esearch"):
        return _as_str(_pick(raw, "uid", "pmid"))
    return _as_str(_pick(raw, "pmid", "PMID"))


def _norm_pmcid(raw: Dict[str, Any], db: str) -> Optional[str]:
    if db in ("pubmed_esummary", "pubmed_esearch"):
        for artid in _as_list(raw.get("articleids") or []):
            if isinstance(artid, dict):
                t = _as_str(_pick(artid, "idtype", "idType", "type")).lower()
                if t in ("pmc", "pmcid"):
                    return _as_str(_pick(artid, "value", "id"))
    return _as_str(_pick(raw, "pmcid", "PMCID", "pmcId", "pmc_id"))


def _norm_issn_raw(raw: Dict[str, Any], db: str) -> Optional[str]:
    for key in ("ISSN", "issn", "journal.issn", "journalInfo.journal.issn",
                "journalissn", "prism:issn"):
        v = norm_issn(_deep_get(raw, key) if "." in key else raw.get(key))
        if v:
            return v
    # crossref issn-type
    for it in _as_list(raw.get("issn-type")):
        if isinstance(it, dict) and norm_issn(_pick(it, "value")):
            return norm_issn(_pick(it, "value"))
    return None


def _norm_journal(raw: Dict[str, Any], db: str) -> Optional[str]:
    val = None
    if db == "crossref":
        val = _pick(raw, "container-title", "short-container-title",
                    "journal", "journal-title", "published-journal")
    elif db == "europepmc":
        val = _pick(raw, "journalInfo.journal.title", "journal.title",
                    "journalInfo.journal.medlineAbbreviation", "journalTitle",
                    "journal", "journalInfo.journal.issn")
    elif db in ("pubmed_esummary", "pubmed_esearch"):
        val = _pick(raw, "source", "fulljournalname", "journal", "journaltitle")
    else:
        val = _pick(raw, "journal", "journalName", "publicationName",
                    "journal_name", "sourceTitle", "container-title",
                    "container_title", "journalTitle")
    if isinstance(val, list) and val:
        val = val[0]
    s = _text_of(val)
    return s or None


def _norm_type(raw: Dict[str, Any], db: str) -> Optional[str]:
    if db == "crossref":
        return _as_str(_pick(raw, "type", "article-type"))
    if db == "europepmc":
        v = _pick(raw, "pubType", "pubTypeList.pubType", "type", "publicationType")
    elif db in ("pubmed_esummary", "pubmed_esearch"):
        v = _pick(raw, "pubtype", "articleType", "publication_type", "type")
    else:
        v = _pick(raw, "type", "article_type", "documentType", "pubType",
                  "publicationType", "content_type", "doc_type")
    if isinstance(v, list) and v:
        v = ";".join(_text_of(x) for x in v)
    return _text_of(v) or None


def _norm_abstract(raw: Dict[str, Any], db: str) -> Optional[str]:
    if db == "europepmc":
        val = _pick(raw, "abstractText", "abstract")
    elif db == "crossref":
        val = _pick(raw, "abstract")
    else:
        val = _pick(raw, "abstract", "Abstract", "description", "summary")
    return _text_of(val) or None


def normalize_record(raw: Dict[str, Any], db: str) -> Dict[str, Any]:
    """把单条原始记录归一为统一的 canonical 记录（含 _field_sources 未填，调用方填）。"""
    title = _text_of(_pick(raw, "title", "Title", "articleTitle",
                           "title_display", "publicationTitle", "name"))
    authors = _norm_authors(raw, db)
    year = _norm_year_raw(raw, db)
    journal = _norm_journal(raw, db)
    volume = _as_str(_pick(raw, "volume", "Volume", "journalInfo.volume", "vol"))
    issue = _as_str(_pick(raw, "issue", "Issue", "JournalIssue.Issue", "number"))
    pages = norm_pages(_pick(raw, "page", "pages", "pageInfo", "elocationid",
                             "firstpage", "articlePages"))
    doi = _norm_doi_raw(raw, db)
    pmid = _norm_pmid(raw, db)
    pmcid = _norm_pmcid(raw, db)
    issn = _norm_issn_raw(raw, db)
    publisher = _text_of(_pick(raw, "publisher", "Publisher", "publisherName",
                               "imprint"))
    doc_type = _norm_type(raw, db)
    abstract = _norm_abstract(raw, db)

    ids: Dict[str, Any] = {}
    if doi:
        ids["doi"] = doi
    if pmid:
        ids["pmid"] = pmid
    if pmcid:
        ids["pmcid"] = pmcid
    if issn:
        ids["issn"] = issn
    # 尽量保留出版方原始 id
    raw_id = _as_str(_pick(raw, "id", "uid", "pii", "article_number"))
    if raw_id and raw_id not in ids.values():
        ids.setdefault("source_id", raw_id)

    rec: Dict[str, Any] = {
        "title": title or None,
        "authors": authors,
        "year": year,
        "journal": journal,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "issn": issn,
        "doc_type": doc_type,
        "abstract": abstract,
        "publisher": publisher,
        "identifiers": ids,
    }
    return rec


def field_sources_of(raw: Dict[str, Any], db: str, locator: str) -> Dict[str, str]:
    """记录每个去重后字段的『来源库 + 定位』。"""
    src = "%s@%s" % (db, locator)
    keys = ("title", "authors", "year", "journal", "volume", "issue", "pages",
            "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher")
    return {k: src for k in keys if raw.get(k) not in (None, "", [])}


# --------------------------------------------------------------------------- #
# 身份键与去重
# --------------------------------------------------------------------------- #
def identity_keys(rec: Dict[str, Any]) -> Dict[str, str]:
    keys: Dict[str, str] = {}
    if rec.get("doi"):
        keys["doi"] = rec["doi"]
    if rec.get("pmid"):
        keys["pmid"] = rec["pmid"]
    if rec.get("pmcid"):
        keys["pmcid"] = rec["pmcid"]
    title = _title_key(rec.get("title"))
    first_author = _all_surnames(rec.get("authors"))[:1]
    year = rec.get("year")
    if title and first_author and year:
        keys["title1au_year"] = "%s|%s|%d" % (title, first_author[0], year)
    if title and year:
        keys["title_year"] = "%s|%d" % (title, year)
    return keys


def _fuzzy_title_match(t1: str, t2: str) -> float:
    if not t1 or not t2:
        return 0.0
    if t1 == t2:
        return 1.0
    return SequenceMatcher(None, t1, t2).ratio()


def dedup_records(records: Sequence[Dict[str, Any]]) -> Tuple[List[List[int]], List[str]]:
    """返回 [簇（记录下标列表）...] 与 解释文本（每簇一条）。"""
    clusters: List[List[int]] = []
    reasons: List[str] = []
    exact: Dict[str, int] = {}     # 值 -> 簇下标
    t1y: Dict[str, int] = {}       # title+一作+年份 -> 簇
    title_year: List[Tuple[str, int, int]] = []  # (title_key, year, 簇下标或记录下标)

    assigned: Dict[int, int] = {}  # 记录下标 -> 簇下标

    for i, rec in enumerate(records):
        keys = identity_keys(rec)
        target = None
        reason = None
        if keys.get("doi") and keys["doi"] in exact:
            target = exact[keys["doi"]]
            reason = "doi_exact"
        elif keys.get("pmid") and keys["pmid"] in exact:
            target = exact[keys["pmid"]]
            reason = "pmid_exact"
        elif keys.get("pmcid") and keys["pmcid"] in exact:
            target = exact[keys["pmcid"]]
            reason = "pmcid_exact"
        elif keys.get("title1au_year") and keys["title1au_year"] in t1y:
            target = t1y[keys["title1au_year"]]
            reason = "title_first_author_year"
        else:
            # 标题模糊：与已有簇的首条记录比对
            tk = _title_key(rec.get("title"))
            yy = rec.get("year")
            for ttk, ry, tidx in title_year:
                if yy and ry and abs(int(yy) - int(ry)) <= 1:
                    ratio = _fuzzy_title_match(tk, ttk)
                    if ratio >= 0.90:
                        target = tidx
                        reason = "title_fuzzy_%.2f" % ratio
                        break
            if target is None and tk and yy:
                # 同年同名但无作者/作者缺省，也按标题归并
                for ttk, ry, tidx in title_year:
                    if yy and ry and int(yy) == int(ry) and tk == ttk:
                        target = tidx
                        reason = "title_year_exact"
                        break

        if target is None:
            cid = len(clusters)
            clusters.append([i])
            assigned[i] = cid
            if keys.get("doi"):
                exact[keys["doi"]] = cid
            if keys.get("pmid"):
                exact[keys["pmid"]] = cid
            if keys.get("pmcid"):
                exact[keys["pmcid"]] = cid
            if keys.get("title1au_year"):
                t1y[keys["title1au_year"]] = cid
            # 记录标题目录
            tk = _title_key(rec.get("title"))
            if tk:
                title_year.append((tk, rec.get("year"), cid))
            reasons.append("new_record")
        else:
            clusters[target].append(i)
            assigned[i] = target
            reasons.append("merged:%s" % reason)

    return clusters, reasons


# --------------------------------------------------------------------------- #
# 字段优先级与合并
# --------------------------------------------------------------------------- #
# 各字段的『可信来源优先级』：前面优先。同一库重复时取非空/更长。
DB_PRIORITY = {
    "doi": ["crossref", "europepmc", "pubmed_esummary", "publisher", "generic"],
    "title": ["crossref", "europepmc", "publisher", "pubmed_esummary", "generic"],
    "journal": ["crossref", "europepmc", "pubmed_esummary", "publisher", "generic"],
    "year": ["crossref", "europepmc", "pubmed_esummary", "publisher", "generic"],
    "issn": ["crossref", "publisher", "europepmc", "pubmed_esummary", "generic"],
    "pages": ["crossref", "europepmc", "pubmed_esummary", "publisher", "generic"],
    "volume": ["crossref", "publisher", "europepmc", "pubmed_esummary", "generic"],
    "issue": ["crossref", "publisher", "europepmc", "pubmed_esummary", "generic"],
    "abstract": ["europepmc", "publisher", "crossref", "pubmed_esummary", "generic"],
    "publisher": ["crossref", "publisher", "europepmc", "pubmed_esummary", "generic"],
    "doc_type": ["europepmc", "crossref", "pubmed_esummary", "publisher", "generic"],
    "pmid": ["pubmed_esummary", "europepmc", "crossref", "publisher", "generic"],
    "pmcid": ["europepmc", "pubmed_esummary", "crossref", "publisher", "generic"],
}


def _db_rank(db: str, field: str) -> int:
    order = DB_PRIORITY.get(field, [])
    return order.index(db) if db in order else len(order)


def _choose_scalar(records: Sequence[Dict[str, Any]], field: str) -> Tuple[Any, str, List[Dict[str, Any]]]:
    """按优先级选一次取值；返回 (值, 来源定位, 冲突记录列表)。"""
    cand: List[Dict[str, Any]] = []
    for r in records:
        v = r.get(field)
        if v not in (None, "", []):
            cand.append(r)
    if not cand:
        return None, "", []
    # 按 (库优先级, 年份优先后, 值长度) 排序
    def key(r: Dict[str, Any]) -> Tuple[int, int]:
        return (_db_rank(r.get("_db", "generic"), field), -len(_text_of(r.get(field))))
    cand.sort(key=key)
    chosen = cand[0]
    value = chosen.get(field)
    src = chosen.get("_source_file", "") + "::" + chosen.get("_locator", "")
    conflicts: List[Dict[str, Any]] = []
    seen = set()
    for r in records:
        v = r.get(field)
        if v in (None, "", []):
            continue
        norm = _text_of(v).lower()
        if norm in seen:
            continue
        seen.add(norm)
        if _text_of(value).lower() != norm:
            conflicts.append({
                "field": field,
                "value": v,
                "value_source": "%s@%s" % (r.get("_db", "?"), r.get("_locator", "?")),
                "chosen_value": value,
                "resolution": "priority_%s" % DB_PRIORITY.get(field, ["?"])[0]
                if DB_PRIORITY.get(field) else "priority",
            })
    return value, src, conflicts


def merge_cluster(records: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """把同簇记录合并为一条 merged record；返回 (merged, 冲突)。"""
    ids: Dict[str, Any] = {}
    for r in records:
        for k, v in (r.get("identifiers") or {}).items():
            ids.setdefault(k, v)

    authors_order: List[str] = []
    seen_surname: set = set()
    for r in records:
        for a in r.get("authors", []):
            sur = a.split(",")[0].strip().lower() if "," in a else a.strip().lower()
            if sur and sur not in seen_surname:
                seen_surname.add(sur)
                authors_order.append(a)

    scalar_fields = ("title", "year", "journal", "volume", "issue", "pages",
                     "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher")
    conflicts: List[Dict[str, Any]] = []
    field_sources: Dict[str, str] = {}
    merged: Dict[str, Any] = {"identifiers": ids}
    for f in scalar_fields:
        val, src, cfs = _choose_scalar(records, f)
        merged[f] = val if val is not None else None
        if src:
            field_sources[f] = src
        conflicts.extend(cfs)
    merged["authors"] = authors_order

    # 字段来源为合并后的汇总（每个字段的胜出位置）
    merged["_merged_field_sources"] = field_sources

    # 来源链：库—查询—原始响应—映射—去重—合并
    provenance: List[Dict[str, Any]] = []
    for r in records:
        provenance.append({
            "db": r.get("_db"),
            "source_file": r.get("_source_file"),
            "locator": r.get("_locator"),
            "request": r.get("_request") or {},
            "role": "member_record",
        })
    merged["_cluster"] = len(records)
    merged["_provenance_members"] = provenance
    return merged, conflicts


def record_key(rec: Dict[str, Any]) -> str:
    """单条记录的稳定 id，保证重复运行结果一致。"""
    for f in ("doi", "pmid", "pmcid"):
        if rec.get(f):
            return "%s:%s" % (f, rec[f])
    tk = _title_key(rec.get("title"))
    if tk:
        return "title:%s|%s" % (tk, rec.get("year") or "")
    return "src:%s" % (rec.get("_source_file", "?") + ("::" + (rec.get("_locator") or "")))


# --------------------------------------------------------------------------- #
# 命中 / 覆盖
# --------------------------------------------------------------------------- #
def relevance_hint(rec: Dict[str, Any], terms: Iterable[str]) -> Dict[str, Any]:
    toks = [t.lower() for t in terms if t and len(t) >= 2]
    if not toks:
        return {"score": 0, "matched_terms": [], "method": "term_overlap_auto"}
    hay = _text_of(rec.get("title")) + " " + _text_of(rec.get("abstract")) + \
        " " + _text_of(rec.get("journal")) + " " + " ".join(rec.get("authors", []))
    low = hay.lower()
    matched = sorted({t for t in toks if t in low})
    score = len(matched)
    return {"score": score, "matched_terms": matched, "method": "term_overlap_auto"}