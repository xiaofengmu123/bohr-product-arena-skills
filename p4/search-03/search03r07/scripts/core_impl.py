#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discover.py — 输入发现、容错解析、写 partial 骨架与 normalized 文件。

职责（阶段 1）：
  1) 解析 --input/--work/--output（缺省 /input、/app/work、/app/output）
  2) 扫描输入目录，识别 case.json/task.md 与 materials 文件（PDF/md/jsonl/txt）
  3) 以多重容错策略解析 case.json（多编码、BOM、尾逗号、JSON 注释、坏 JSON）
  4) 规范化科研问题、候选文献、纳排标准、output_contract
  5) **立即落盘一版合法 result.json 骨架**（"前 3 分钟落盘"铁律）
  6) 全程捕获异常；最坏情况仍写最小合法产物，退出码恒为 0

仅标准库，Python 3.7+。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_STAMP = None
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过
MAX_TEXT_LEN = 200_000
DEFAULT_CONTRACT_FIELDS = ["results", "evidence", "status_notes"]
FIELD_ALIASES = {
    "case_id": ["case_id", "caseId", "id", "challenge_id", "challengeId", "task_id"],
    "research_question": ["research_question", "researchQuestion", "query", "question",
                          "topic", "research_topic", "task"],
    "papers": ["papers", "candidates", "records", "results", "items", "documents",
               "literature", "entries", "articles"],
    "inclusion": ["inclusion_criteria", "inclusion", "include", "include_criteria",
                  "criteria", "criteria_in"],
    "exclusion": ["exclusion_criteria", "exclusion", "exclude", "exclude_criteria"],
    "output_contract": ["output_contract", "outputContract", "output_schema",
                        "outputSchema", "expected_fields", "expectedFields", "schema"],
}
PAPER_KEY_ALIASES = {
    "record_id": ["record_id", "recordId", "id", "paper_id", "doc_id"],
    "title": ["title", "name", "paper_title", "heading"],
    "authors": ["authors", "author", "authors_list", "creators", "creator"],
    "year": ["year", "pub_year", "publication_year", "published_year"],
    "venue": ["venue", "journal", "source", "conference", "publisher", "publication"],
    "doi": ["doi", "DOI"],
    "url": ["url", "link", "href", "pdf_url", "pdf"],
    "abstract": ["abstract", "summary", "description", "snippet", "text"],
    "source_type": ["source_type", "type", "kind", "pub_type", "publication_type"],
}
STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "with",
             "is", "are", "was", "were", "be", "by", "as", "at", "from", "using",
             "study", "analysis"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("latin-1", errors="replace")


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _strip_json_comments(text: str) -> str:
    out = []
    i, n = 0, len(text)
    in_str, esc = False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_json_robust(path: Path):
    """Read and parse JSON with layered tolerance.

    Returns (obj|None, errors, degradations). A recovered fallback parse is
    reported as a degradation, not an error; only total failure becomes errors.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, [f"{path}: read failed: {e}"], []
    text = _decode_bytes(raw)
    text = text.lstrip("\ufeff").strip()
    attempts = [
        text,
        _strip_trailing_commas(text),
        _strip_json_comments(text),
        _strip_trailing_commas(_strip_json_comments(text)),
    ]
    failures = []
    for idx, candidate in enumerate(attempts):
        try:
            obj = json.loads(candidate)
            degradations = []
            if idx > 0:
                degradations.append(
                    f"{path.name}: 以第 {idx + 1} 级容错策略解析成功"
                    f"（JSON 含尾逗号/注释等非标准写法，已修复）。")
            return obj, [], degradations
        except (json.JSONDecodeError, ValueError) as e:
            failures.append(f"{path.name}: parse attempt {idx + 1} failed: {e}")
    return None, failures, []


def probe_environment():
    import platform
    env = {
        "python": platform.python_version(),
        "python_executable": sys.executable or "python",
        "platform": platform.platform(),
        "cwd": os.getcwd(),
    }
    env["pdftotext_available"] = shutil.which("pdftotext") is not None
    env["pdftoppm_available"] = shutil.which("pdftoppm") is not None
    return env


def _input_files(input_dir: Path):
    recs = {"case_json": None, "task_md": None, "materials": []}
    if not input_dir.exists():
        return recs
    if input_dir.is_file():
        name = input_dir.name.lower()
        if name == "case.json":
            recs["case_json"] = input_dir
        elif name == "task.md":
            recs["task_md"] = input_dir
        else:
            recs["materials"].append(input_dir)
        return recs
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        n = p.name.lower()
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        if n == "case.json" and recs["case_json"] is None:
            recs["case_json"] = p
        elif n == "task.md" and recs["task_md"] is None:
            recs["task_md"] = p
        elif n.endswith((".pdf", ".md", ".jsonl", ".txt", ".json")):
            recs["materials"].append((p, size))
    return recs


def _get_first(case: dict, aliases):
    for a in aliases:
        if a in case and case[a] not in (None, "", [], {}):
            return case[a]
    return None


def _field(rec, aliases):
    if not isinstance(rec, dict):
        return None
    for a in aliases:
        if a in rec:
            return rec[a]
    return None


def _norm_authors(val):
    if val is None:
        return None
    if isinstance(val, str):
        v = val.strip()
        return v if v else None
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                nm = _field(item, ["name", "author", "full_name", "given_name"])
                # combine family/given if needed
                if nm is None:
                    fam = _field(item, ["family", "family_name", "last_name"])
                    giv = _field(item, ["given", "given_name", "first_name"])
                    if fam or giv:
                        nm = " ".join(x for x in (giv, fam) if x)
                if nm:
                    parts.append(str(nm))
        return "; ".join(parts) if parts else None
    return str(val).strip() or None


def _norm_year(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val)
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return int(m.group(0))
    return None


def _norm_doi(val):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    return s or None


def _norm_url(val):
    if val is None:
        return None
    s = str(val).strip()
    return s if (s.startswith("http://") or s.startswith("https://") or s.startswith("www.")) else None


def extract_case_id(case, task_text):
    cid = _get_first(case, FIELD_ALIASES["case_id"]) if isinstance(case, dict) else None
    if cid:
        return str(cid)
    if task_text:
        m = re.search(r"challenge[_ -]?id[:：\s]*([A-Za-z0-9_-]+)", task_text, flags=re.I)
        if m:
            return m.group(1)
    return None


def _collect_candidates(case):
    """Return (raw_candidates_or_None, found_path)."""
    if not isinstance(case, dict):
        return None, None
    papers = _get_first(case, FIELD_ALIASES["papers"])
    if papers is not None:
        if isinstance(papers, list):
            return papers, "papers"
        if isinstance(papers, dict):
            for sub in ("items", "papers", "candidates", "records", "results", "list"):
                if isinstance(papers.get(sub), list):
                    return papers[sub], f"papers.{sub}"
            return list(papers.values()), "papers.values"
    # search one level deep
    for key, val in case.items():
        if isinstance(val, dict):
            inner = _get_first(val, FIELD_ALIASES["papers"])
            if isinstance(inner, list):
                return inner, f"{key}.papers"
            if isinstance(inner, dict):
                for sub in ("items", "papers", "candidates", "records", "results", "list"):
                    if isinstance(inner.get(sub), list):
                        return inner[sub], f"{key}.papers.{sub}"
    return None, None


def normalize_candidate(raw, index):
    if isinstance(raw, str):
        raw = {"title": raw}
    if not isinstance(raw, dict):
        raw = {}
    rec = {}
    for canon, aliases in PAPER_KEY_ALIASES.items():
        rec[canon] = _field(raw, aliases)
    rec["authors"] = _norm_authors(rec.get("authors"))
    rec["year"] = _norm_year(rec.get("year"))
    rec["doi"] = _norm_doi(rec.get("doi"))
    rec["url"] = _norm_url(rec.get("url"))
    if rec.get("title") is None:
        # last-resort: any short non-empty string value
        for v in raw.values():
            if isinstance(v, str) and 3 <= len(v.strip()) <= 400:
                rec["title"] = v.strip()
                break
    if rec.get("source_type") is None:
        st = str(rec.get("source_type") or "").strip().lower() or None
        if not st:
            if isinstance(rec.get("venue"), str) and any(
                    k in rec["venue"].lower() for k in ("arxiv", "preprint", "biorxiv", "medrxiv", "ssrn")):
                st = "preprint"
            else:
                st = None
        rec["source_type"] = st
    # preserve unknown fields transparently
    known = set(PAPER_KEY_ALIASES)
    extra = {k: v for k, v in raw.items() if k not in known}
    if extra:
        rec["extra"] = extra
    return rec


def _unique_record_id(rec, index, used):
    base = rec.get("record_id") or rec.get("doi") or rec.get("url") or f"rec_{index:04d}"
    if base is None:
        base = f"rec_{index:04d}"
    base = str(base)
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}#{n}"
        n += 1
    used.add(candidate)
    return candidate


def _read_task_md(path):
    if path is None:
        return None
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            return None
        return _decode_bytes(raw)[:MAX_TEXT_LEN]
    except OSError:
        return None


def _sources_list(files):
    out = []
    cj = files["case_json"]
    if cj:
        try:
            size = cj.stat().st_size
        except OSError:
            size = None
        out.append({"path": str(cj), "kind": "case_json", "role": "task+candidates", "size": size})
    tm = files["task_md"]
    if tm:
        try:
            size = tm.stat().st_size
        except OSError:
            size = None
        out.append({"path": str(tm), "kind": "task_md", "role": "instructions", "size": size})
    for p, size in files["materials"]:
        out.append({"path": str(p), "kind": p.suffix.lower().lstrip(".") or "unknown",
                    "role": "material", "size": size,
                    "skipped": bool(size is not None and size > MAX_FILE_BYTES)})
    return out


def _contract_fields_and_raw(case):
    raw = None
    if isinstance(case, dict):
        raw = _get_first(case, FIELD_ALIASES["output_contract"])
        if raw is None:
            for k in ("output_contract", "outputContract", "output_schema", "outputSchema",
                      "expected_fields", "expectedFields"):
                if k in case and case[k] not in (None, "", []):
                    raw = case[k]
                    break
    fields = []
    if isinstance(raw, dict):
        if "properties" in raw and isinstance(raw["properties"], dict):
            fields = list(raw["properties"].keys())
        else:
            fields = [k for k in raw.keys() if isinstance(k, str)]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                for k in ("name", "field", "key", "id"):
                    if item.get(k):
                        fields.append(str(item[k]))
                        break
    # merge default, keep order, dedupe preserving first occurrence
    merged = []
    for f in fields + list(DEFAULT_CONTRACT_FIELDS):
        if f not in merged:
            merged.append(f)
    return raw, merged


def _skeleton_record(rec, index, used):
    rid = _unique_record_id(rec, index, used)
    return {
        "record_id": rid,
        "title": rec.get("title"),
        "authors": rec.get("authors"),
        "year": rec.get("year"),
        "venue": rec.get("venue"),
        "doi": rec.get("doi"),
        "url": rec.get("url"),
        "source_type": rec.get("source_type"),
        "relevance": "unknown",
        "tier": "pending",
        "priority_score": None,
        "rank": None,
        "reasons": [],
        "evidence": [],
        "confidence": "low",
        "criteria_matched": [],
        "conflict": None,
        "dedupe": None,
    }


def build_skeleton(case, task_text, files, env, errs, degradations):
    case_id = extract_case_id(case, task_text)
    raw_contract, contract_fields = _contract_fields_and_raw({} if not isinstance(case, dict) else case)
    raw_cands, cand_path = _collect_candidates(case)
    sources = _sources_list(files)
    skel = {
        "case_id": case_id,
        "status": "partial",
        "results": [],
        "evidence": [],
        "status_notes": [],
        "omissions": [],
        "dedupe_groups": [],
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": [str(e) for e in errs],
        "degradations": [str(d) for d in degradations],
        "change_log": [{"ts": now_iso(), "step": "discover", "action": "skeleton_written"}],
        "runtime": {
            "started_at": now_iso(),
            "environment": env,
            "budget": {"model_requests": 64, "seconds": 900, "tokens_per_response": 16384},
        },
    }
    if raw_contract is not None:
        skel["output_contract"] = raw_contract
    for f in contract_fields:
        skel.setdefault(f, None)
    if raw_cands is not None and len(raw_cands) == 0:
        skel["status"] = "insufficient_evidence"
        skel["status_notes"].append(f"候选集为空：路径 {cand_path or '?'} 下无候选文献。")
        skel["omissions"].append("未发现候选文献；请检查 case.json 的 papers/candidates/records 等字段。")
        return skel, []
    if raw_cands is None:
        skel["status"] = "insufficient_evidence"
        skel["status_notes"].append("未发现候选文献字段。")
        skel["omissions"].append("未发现候选文献字段；请检查 case.json 的 papers/candidates/records 等字段。")
        return skel, []
    normalized = []
    used = set()
    for i, raw in enumerate(raw_cands):
        rec = normalize_candidate(raw, i)
        normalized.append({"record_id": None, **rec})
    # assign record ids deterministically to both normalized and skeleton
    for i, rec in enumerate(normalized):
        rid = _unique_record_id(rec, i, used)
        rec["record_id"] = rid
    skel["results"] = [_skeleton_record(rec, i, used) for i, rec in enumerate(normalized)]
    skel["status_notes"].append(f"发现 {len(normalized)} 条候选（来自 {cand_path}），骨架已落盘，待逐步判断。")
    return skel, normalized


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _minimal_result(input_dir, work_dir, output_dir, message):
    """Absolute fallback: still produce a legal result.json."""
    env = probe_environment()
    skel = {
        "case_id": None,
        "status": "input_error",
        "results": [],
        "evidence": [],
        "status_notes": [message],
        "omissions": [],
        "dedupe_groups": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": ["输入不可解析，需人工提供 case.json/task.md。"],
        "errors": [message],
        "degradations": ["discover 进入最小降级路径。"],
        "change_log": [{"ts": now_iso(), "step": "discover", "action": "minimal_fallback"}],
        "runtime": {"started_at": now_iso(), "environment": env,
                    "budget": {"model_requests": 64, "seconds": 900, "tokens_per_response": 16384}},
    }
    for d in (work_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "result.json", skel)
    write_json(work_dir / "case_normalized.json", {"case_id": None, "candidates": [], "question": None,
                                                   "inclusion": None, "exclusion": None, "contract_fields": DEFAULT_CONTRACT_FIELDS})


def main(argv=None):
    global RUNTIME_STAMP
    RUNTIME_STAMP = now_iso()
    ap = argparse.ArgumentParser(description="discover: discover inputs and write partial result skeleton")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    env = probe_environment()
    degradations = []
    errs = []

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # cannot even make dirs -> minimal result to cwd (best effort)
        print(f"discover: cannot create work/output dirs: {e}")
        try:
            _minimal_result(Path(".") / "input", Path(".") / "work", Path(".") / "output",
                            f"目录创建失败: {e}")
        except Exception:
            pass
        return 0

    try:
        files = _input_files(input_dir)
        task_text = _read_task_md(files["task_md"])
        case = None
        if files["case_json"] is not None:
            if files["case_json"].stat().st_size > MAX_FILE_BYTES:
                degradations.append(f"{files['case_json'].name} 超过 {MAX_FILE_BYTES} 字节，已跳过，未解析。")
                case = None
            else:
                case, parse_errs, parse_degs = load_json_robust(files["case_json"])
                errs.extend(parse_errs)
                degradations.extend(parse_degs)
        # collect oversized materials
        for p, size in files["materials"]:
            if size is not None and size > MAX_FILE_BYTES:
                degradations.append(f"{p.name} 超过 {MAX_FILE_BYTES} 字节，已跳过，未读取。")
        # build question/inclusion/exclusion for normalized
        case_dict = case if isinstance(case, dict) else {}
        question = _get_first(case_dict, FIELD_ALIASES["research_question"])
        if question is None and task_text:
            # use task.md's first # heading as fallback question marker
            m = re.search(r"^#\s+(.+)$", task_text, flags=re.M)
            question = ("(from task.md) " + m.group(1).strip()) if m else None
        inclusion = _get_first(case_dict, FIELD_ALIASES["inclusion"])
        exclusion = _get_first(case_dict, FIELD_ALIASES["exclusion"])
        raw_contract, contract_fields = _contract_fields_and_raw(case_dict)

        skel, normalized = build_skeleton(case_dict, task_text, files, env, errs, degradations)
        write_json(output_dir / "result.json", skel)
        norm_obj = {
            "case_id": skel["case_id"],
            "question": question,
            "inclusion": inclusion,
            "exclusion": exclusion,
            "contract_fields": contract_fields,
            "candidates": normalized,
            "candidate_path": _collect_candidates(case_dict)[1],
            "task_text_head": (task_text[:4000] if task_text else None),
            "environments": env,
        }
        write_json(work_dir / "case_normalized.json", norm_obj)
        print(f"discover: status={skel['status']} candidates={len(normalized)} "
              f"errors={len(errs)} degradations={len(degradations)}")
        print(f"discover: wrote {output_dir / 'result.json'} + {work_dir / 'case_normalized.json'}")
        return 0
    except Exception as e:  # noqa: BLE001 — 鲁棒性铁律：绝不崩溃
        errs.append(f"discover unexpected: {type(e).__name__}: {e}")
        try:
            _minimal_result(input_dir, work_dir, output_dir, f"discover unexpected: {e}")
        except Exception:
            pass
        print(f"discover: unexpected error, degraded: {e}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())