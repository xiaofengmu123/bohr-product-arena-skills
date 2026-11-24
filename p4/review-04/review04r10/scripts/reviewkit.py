# -*- coding: utf-8 -*-
"""reviewkit.py — 论文逻辑与论证审查 Skill 的核心纯函数库（仅标准库）。

架构取舍（与常见的 scaffold/update 单文件实现刻意不同）：
1. 唯一中间表示 = 审计台账 work/ledger.json：主张(claims)优先、findings 必须引用 claim_id，
   强制"主张—证据"逐条对齐；从数据层面降低"把对照组/假设/转述观点误判为缺陷"的概率。
2. 校验顺序固定：发现输入 → 抽取/切分语料 → 落盘骨架 → 填 ledger → 规范化 ledger
   → 交叉引用校验 → 生成 result.json/report.md → schema 自检。任一步失败保留上一版合法产物。
3. 无随机数；输出 sort_keys 序列化；除 change_log/runtime 时间戳外，重复运行字节一致。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LEDGER_SCHEMA = "audit-ledger/1"

RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
SPEAKERS = ("author", "cited", "assumption", "unknown")
VERDICTS = ("supported", "overclaimed", "unsupported", "pending")
SEVERITIES = ("critical", "major", "moderate", "minor", "suggestion")
FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")
CONFIDENCES = ("high", "medium", "low")
COMMIT_DELIVERED = ("yes", "no", "partial", "pending")

FINDING_TYPES = (
    "overclaim", "unsupported_claim", "citation_mismatch",
    "methodological_gap", "statistical_misreport", "causal_overreach",
    "commitment_unmet", "internal_inconsistency", "figure_text_mismatch",
    "reporting_omission", "normative_issue", "suggestion",
)

COVERAGE_DIMS = (
    "factual_verification", "citation_support", "method_design",
    "statistical_reporting", "argument_chain", "academic_norms",
)

COVERAGE_STATUSES = ("done", "partial", "skipped", "pending")

MAX_FILE_BYTES = 2_000_000

SECTION_KEYWORDS = (
    ("abstract", "Abstract"),
    ("introduction", "Introduction"),
    ("background", "Background"),
    ("related work", "Related Work"),
    ("materials and methods", "Materials and Methods"),
    ("methods", "Methods"),
    ("methodology", "Methodology"),
    ("experimental setup", "Experimental Setup"),
    ("experiments", "Experiments"),
    ("results", "Results"),
    ("discussion", "Discussion"),
    ("conclusion", "Conclusion"),
    ("references", "References"),
    ("bibliography", "Bibliography"),
    ("appendix", "Appendix"),
    ("supplementary", "Supplementary"),
    ("acknowledg", "Acknowledgments"),
    ("conflict of interest", "Conflict of Interest"),
    ("funding", "Funding"),
    ("data availability", "Data Availability"),
    ("limitations", "Limitations"),
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# 容错 I/O
# --------------------------------------------------------------------------
def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """读取文本，容错 BOM / 坏编码 / 超大文件；返回 text/error/chars/encoding。"""
    p = Path(path)
    result: Dict[str, Any] = {"text": "", "error": None, "chars": 0,
                              "encoding": None, "truncated": False}
    try:
        st = p.stat()
    except OSError as e:
        result["error"] = "missing:" + str(e)
        return result
    if st.st_size > max_bytes:
        result["error"] = "too_large"
        return result
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            result["text"] = raw.decode(enc)
            result["encoding"] = enc
            result["chars"] = len(result["text"])
            return result
        except UnicodeDecodeError:
            continue
    # latin-1 永不失败，但标记降级
    result["text"] = raw.decode("latin-1")
    result["encoding"] = "latin-1"
    result["error"] = "bad_encoding_fallback_latin1"
    result["chars"] = len(result["text"])
    return result


def read_json_tolerant(path: str) -> tuple:
    """容错读 JSON，返回 (data, error)；坏 JSON 返回 ({}, error)。"""
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001
        return {}, str(e)


def write_json_atomic(path: str, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(str(tmp), str(p))


def write_text_atomic(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(p))


# --------------------------------------------------------------------------
# 输入发现与元数据
# --------------------------------------------------------------------------
def discover_input(input_dir: str) -> Dict[str, Any]:
    root = Path(input_dir)
    out: Dict[str, Any] = {"case_json": None, "task_md": None,
                           "materials": [], "notes": []}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue
        name = p.name.lower()
        if p.parent == root and name == "case.json":
            out["case_json"] = str(p)
        elif p.parent == root and name == "task.md":
            out["task_md"] = str(p)
        elif name.endswith((".pdf", ".txt", ".md", ".tex", ".html")):
            if name.endswith(".md") and "notes" in parts:
                out["notes"].append(str(p))
            else:
                out["materials"].append(str(p))
        if len(out["materials"]) >= 200:
            break
    return out


def load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None}
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        meta["_case_json_error"] = err
        meta["task_title"] = data.get("title") or data.get("task_title")
        meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                or data.get("id"))
    if discovered.get("task_md"):
        rd = read_text_safe(discovered["task_md"], max_bytes=200_000)
        text = rd["text"]
        m = re.search(r"challenge[-_]id\s*[:：]\s*`?([A-Za-z0-9\-_]+)`?", text)
        if m:
            meta["challenge_id"] = meta["challenge_id"] or m.group(1)
        for line in text.splitlines()[:60]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta.setdefault("task_title", ls.lstrip("#").strip())
                break
    return meta


def derive_case_id(discovered: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        for key in ("id", "case_id", "caseId", "caseid"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def load_contract(discovered: Dict[str, Any]) -> Dict[str, Any]:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        c = data.get("output_contract")
        if isinstance(c, dict):
            return dict(c)
    return {}


# --------------------------------------------------------------------------
# 文本抽取与切分
# --------------------------------------------------------------------------
def _read_parser_jsonl(pdf_path: str) -> Optional[List[str]]:
    stem = Path(pdf_path).stem
    cand = Path(pdf_path).parent / ".parser" / (stem + ".jsonl")
    if not cand.is_file():
        return None
    pages: List[str] = []
    try:
        for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                pages.append(obj["text"])
            elif isinstance(obj, str):
                pages.append(obj)
    except Exception:  # noqa: BLE001
        return None
    return pages or None


def _extract_pdf(path: str) -> Dict[str, Any]:
    exe = shutil.which("pdftotext")
    if not exe:
        # 无 pdftotext：尝试平台预抽取层
        pages = _read_parser_jsonl(path)
        if pages:
            return {"pages": pages, "error": None, "n_pages": len(pages)}
        return {"pages": [], "error": "no_pdftotext", "n_pages": 0}
    try:
        proc = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"],
                              capture_output=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        return {"pages": [], "error": "pdftotext_failed:" + str(e), "n_pages": 0}
    if proc.returncode != 0:
        return {"pages": [], "error": "pdftotext_rc:" + str(proc.returncode), "n_pages": 0}
    raw = proc.stdout.decode("utf-8", errors="replace")
    pages = [p.strip() for p in raw.split("\f")]
    while pages and not pages[-1].strip():
        pages.pop()
    return {"pages": pages, "error": None, "n_pages": len(pages)}


def _extract_any(path: str) -> Dict[str, Any]:
    if Path(path).name.lower().endswith(".pdf"):
        return _extract_pdf(path)
    rd = read_text_safe(path)
    if rd["error"] in (None, "bad_encoding_fallback_latin1"):
        return {"pages": [rd["text"]], "error": rd["error"]}
    return {"pages": [], "error": rd["error"]}


def _split_paragraphs(text: str) -> List[str]:
    parts = re.split(r"\n\s*\n", text)
    out: List[str] = []
    for part in parts:
        s = part.strip()
        if s:
            out.append(s)
    return out


def _guess_section(paragraph: str) -> Optional[str]:
    lines = [l.strip() for l in paragraph.splitlines() if l.strip()]
    if not lines:
        return None
    first = lines[0]
    if first.startswith("#"):
        title = first.lstrip("#").strip()
        return title or None
    m = re.match(r"^(\d+(?:\.\d+){0,3})\s*[\.、\)\:\u3001]\s*(.+)$", first)
    if m:
        title = m.group(2).strip()
        if 2 <= len(title) <= 80:
            return title
    low = first.lower().rstrip(".:：")
    for key, label in SECTION_KEYWORDS:
        if low == key or low.startswith(key + " ") or low.startswith(key + ":"):
            return label
    return None


def locator(rec: Dict[str, Any]) -> str:
    pid = int(rec.get("para_id", 0))
    page = rec.get("page")
    if page is not None:
        return "p%d\u00b6P%04d" % (page, pid)
    return "P%04d" % pid


# --------------------------------------------------------------------------
# 语料库构建
# --------------------------------------------------------------------------
def build_corpus(input_dir: str) -> tuple:
    discovered = discover_input(input_dir)
    task_meta = load_task_meta(discovered)
    case_id = derive_case_id(discovered)
    contract = load_contract(discovered)
    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    counter = {"n": 0}

    def next_id() -> int:
        counter["n"] += 1
        return counter["n"]

    def add_source(path: str, kind: str, ex: Dict[str, Any]) -> None:
        recoverable = ex.get("error") in (None, "bad_encoding_fallback_latin1")
        if ex.get("error") and not recoverable:
            sources.append({"path": path, "kind": kind, "n_pages": 0, "chars": 0,
                            "error": ex["error"]})
            return
        pages = ex.get("pages") or []
        section: Optional[str] = None
        total_chars = 0
        for pidx, page_text in enumerate(pages, start=1):
            total_chars += len(page_text)
            for para in _split_paragraphs(page_text):
                head = _guess_section(para)
                if head and len(para) <= 160:
                    section = head
                pid = next_id()
                rec = {"para_id": pid,
                       "page": pidx if len(pages) > 1 else None,
                       "section": section or "front_matter",
                       "text": para, "src": path, "kind": kind}
                records.append(rec)
        sources.append({"path": path, "kind": kind, "n_pages": len(pages),
                        "chars": total_chars, "error": ex.get("error")})

    for path in discovered["materials"]:
        add_source(path, "material", _extract_any(path))
    for path in discovered["notes"]:
        add_source(path, "note", _extract_any(path))
    if discovered["task_md"]:
        add_source(discovered["task_md"], "task", _extract_any(discovered["task_md"]))
    return records, sources, task_meta, case_id, contract


def build_section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order: List[str] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        sec = rec["section"]
        if sec not in seen:
            seen[sec] = {"section": sec, "paras": [], "pages": []}
            order.append(sec)
        seen[sec]["paras"].append(locator(rec))
        pg = rec.get("page")
        if pg is not None and pg not in seen[sec]["pages"]:
            seen[sec]["pages"].append(pg)
    return [seen[s] for s in order]


def write_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    path: str) -> None:
    lines = ["# 语料库（供逐段审查，locator 可直接引用）", "",
             "来源文件 %d 个，段落 %d 个。" % (len(sources), len(records)),
             "定位符格式：`p<页>¶P<段落>`；无页码的文本省略 p 部分。引用原文必须逐字复制。", ""]
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        key = src["path"]
        recs = by_src.get(key, [])
        lines.append("")
        lines.append("## 来源: %s" % key)
        lines.append("- kind=%s n_pages=%s chars=%s error=%s" % (
            src.get("kind"), src.get("n_pages"), src.get("chars"), src.get("error")))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s] %s" % (locator(rec), rec["section"], rec["kind"]))
            lines.append(rec["text"])
            lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------
def empty_audit() -> Dict[str, Any]:
    return {
        "summary": "",
        "claims": [],
        "commitments": [],
        "findings": [],
        "coverage": {d: {"status": "pending", "checked": 0, "note": ""}
                     for d in COVERAGE_DIMS},
        "section_map": [],
    }


def make_base_result(case_id: str, task_meta: Dict[str, Any],
                     sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "status": "partial",
        "task": {"challenge_id": task_meta.get("challenge_id"),
                 "task_title": task_meta.get("task_title"),
                 "output_contract": {}},
        "audit": empty_audit(),
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [{"ts": utcnow_iso(), "event": "scaffold",
                        "note": "partial 骨架已落盘，可解析"}],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }


def make_result_skeleton(case_id: str, task_meta: Dict[str, Any],
                         contract: Dict[str, Any], sources: List[Dict[str, Any]],
                         records: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = make_base_result(case_id, task_meta, sources)
    base["audit"]["section_map"] = build_section_map(records)
    base["task"]["output_contract"] = copy.deepcopy(contract)
    result: Dict[str, Any] = {}
    # 原样回填 output_contract 全部键（缺值 None），再以本包字段兜底，避免冲突覆盖诚实状态
    for k, v in contract.items():
        result[k] = copy.deepcopy(v) if v is not None else None
    for k, v in base.items():
        result[k] = v
    return result


def minimal_result(case_id: str, note: str) -> Dict[str, Any]:
    base = make_base_result(case_id, {}, [])
    base["status"] = "input_error"
    base["errors"].append(note)
    base["change_log"].append({"ts": utcnow_iso(), "event": "degrade", "note": note})
    return base


def add_degradation(result: Dict[str, Any], msg: str) -> None:
    d = result.setdefault("degradations", [])
    if not isinstance(d, list):
        d = []
        result["degradations"] = d
    if msg not in d:
        d.append(msg)


# --------------------------------------------------------------------------
# 审计台账（唯一中间表示）
# --------------------------------------------------------------------------
def empty_ledger() -> Dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "summary": "",
        "status": "partial",
        "claims": [],
        "commitments": [],
        "findings": [],
        "coverage": {d: {"status": "pending", "checked": 0, "note": ""}
                     for d in COVERAGE_DIMS},
        "limitations": [],
        "pending_review": [],
        "uncertainty": [],
        "errors": [],
        "degradations": [],
        "section_map": [],
        "runtime_used": {"requests": 0, "seconds": 0},
    }


def _coerce(value: Any, allowed: tuple, default: Any) -> Any:
    return value if value in allowed else default


def _str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value]


def normalize_ledger(ledger: Any) -> tuple:
    """规范化 ledger，返回 (ledger_dict, warnings)。任何结构错误都不抛出。"""
    warnings: List[str] = []
    out = empty_ledger()
    if not isinstance(ledger, dict):
        return out, ["ledger 非对象，已按空台账处理"]
    if ledger.get("schema") != LEDGER_SCHEMA:
        warnings.append("ledger.schema 未知或缺失，按 %s 处理" % LEDGER_SCHEMA)
    out["summary"] = str(ledger.get("summary") or "")
    status = ledger.get("status")
    out["status"] = status if status in RESULT_STATUSES else "partial"

    seen_ids: set = set()

    def uniq(cid: str) -> str:
        base = cid
        n = 2
        while cid in seen_ids:
            cid = "%s_%d" % (base, n)
            n += 1
        seen_ids.add(cid)
        return cid

    claims_raw = ledger.get("claims")
    claims: List[Dict[str, Any]] = []
    if isinstance(claims_raw, list):
        for i, c in enumerate(claims_raw):
            if not isinstance(c, dict):
                c = {}
            cid = uniq(str(c.get("id") or "C%03d" % (i + 1)))
            claims.append({
                "id": cid,
                "text": str(c.get("text") or ""),
                "speaker": _coerce(c.get("speaker"), SPEAKERS, "unknown"),
                "section": str(c.get("section") or ""),
                "locator": str(c.get("locator") or ""),
                "quote": str(c.get("quote") or ""),
                "verdict": _coerce(c.get("verdict"), VERDICTS, "pending"),
                "evidence": str(c.get("evidence") or ""),
            })
    out["claims"] = claims
    claim_ids = {c["id"] for c in claims}

    commits_raw = ledger.get("commitments")
    commits: List[Dict[str, Any]] = []
    if isinstance(commits_raw, list):
        for i, cm in enumerate(commits_raw):
            if not isinstance(cm, dict):
                cm = {}
            commits.append({
                "id": uniq(str(cm.get("id") or "M%03d" % (i + 1))),
                "promise": str(cm.get("promise") or ""),
                "promise_locator": str(cm.get("promise_locator") or ""),
                "delivered_locator": (str(cm["delivered_locator"])
                                      if cm.get("delivered_locator") else None),
                "delivered": _coerce(cm.get("delivered"), COMMIT_DELIVERED, "pending"),
            })
    out["commitments"] = commits

    findings_raw = ledger.get("findings")
    findings: List[Dict[str, Any]] = []
    if isinstance(findings_raw, list):
        for i, f in enumerate(findings_raw):
            if not isinstance(f, dict):
                f = {}
            cid = str(f.get("claim_id") or "")
            if cid and cid not in claim_ids:
                warnings.append("finding #%d 的 claim_id=%s 未在 claims 中" % (i + 1, cid))
            findings.append({
                "id": uniq(str(f.get("id") or "F%03d" % (i + 1))),
                "claim_id": cid,
                "type": _coerce(f.get("type"), FINDING_TYPES, "suggestion"),
                "severity": _coerce(f.get("severity"), SEVERITIES, "minor"),
                "locator": str(f.get("locator") or ""),
                "quote": str(f.get("quote") or ""),
                "claim": str(f.get("claim") or ""),
                "evidence": str(f.get("evidence") or ""),
                "reasoning": str(f.get("reasoning") or ""),
                "suggestion": str(f.get("suggestion") or ""),
                "status": _coerce(f.get("status"), FINDING_STATUSES, "probable"),
                "confidence": _coerce(f.get("confidence"), CONFIDENCES, "medium"),
            })
    out["findings"] = findings

    cov_raw = ledger.get("coverage")
    cov: Dict[str, Any] = {}
    if isinstance(cov_raw, dict):
        for d in COVERAGE_DIMS:
            item = cov_raw.get(d) or {}
            checked = item.get("checked")
            cov[d] = {
                "status": _coerce(item.get("status"), COVERAGE_STATUSES, "pending"),
                "checked": int(checked) if isinstance(checked, (int, float)) else 0,
                "note": str(item.get("note") or ""),
            }
    else:
        cov = {d: {"status": "pending", "checked": 0, "note": ""} for d in COVERAGE_DIMS}
    out["coverage"] = cov

    for key in ("limitations", "pending_review", "uncertainty", "errors", "degradations"):
        out[key] = _str_list(ledger.get(key))

    if isinstance(ledger.get("section_map"), list):
        out["section_map"] = ledger["section_map"]
    ru = ledger.get("runtime_used")
    if isinstance(ru, dict):
        out["runtime_used"] = {
            "requests": int(ru.get("requests") or 0),
            "seconds": float(ru.get("seconds") or 0.0),
        }
    return out, warnings


# --------------------------------------------------------------------------
# 合并 / 校验 / 报告
# --------------------------------------------------------------------------
def merge(result: Dict[str, Any], ledger: Any) -> Dict[str, Any]:
    normalized, warnings = normalize_ledger(ledger)
    audit = result.setdefault("audit", empty_audit())
    audit["summary"] = normalized["summary"]
    audit["claims"] = normalized["claims"]
    audit["commitments"] = normalized["commitments"]
    audit["findings"] = normalized["findings"]
    audit["coverage"] = normalized["coverage"]
    if normalized.get("section_map"):
        audit["section_map"] = normalized["section_map"]
    if normalized["status"] in RESULT_STATUSES:
        result["status"] = normalized["status"]
    for key in ("limitations", "pending_review", "uncertainty", "errors", "degradations"):
        result[key] = normalized.get(key) or []
    ru = normalized.get("runtime_used") or {}
    if ru.get("requests"):
        result["runtime"]["model_requests_used"] = int(ru["requests"])
    if ru.get("seconds"):
        result["runtime"]["seconds_elapsed"] = float(ru["seconds"])
    cl = [e for e in result.get("change_log", []) if e.get("event") != "merge"]
    cl.append({"ts": utcnow_iso(), "event": "merge",
               "note": "已合并审计台账（findings=%d, claims=%d）" % (
                   len(normalized["findings"]), len(normalized["claims"]))})
    result["change_log"] = cl
    for w in warnings:
        add_degradation(result, w)
    return result


def validate_result(result: Any) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    if not isinstance(result, dict):
        return [{"level": "critical", "msg": "result 不是对象"}]
    if not result.get("case_id"):
        issues.append({"level": "critical", "msg": "缺少 case_id"})
    if result.get("status") not in RESULT_STATUSES:
        issues.append({"level": "critical", "msg": "status 非法: %r" % result.get("status")})
    audit = result.get("audit")
    if not isinstance(audit, dict):
        issues.append({"level": "critical", "msg": "缺少 audit 对象"})
        return issues
    cov = audit.get("coverage")
    if isinstance(cov, dict):
        for d in COVERAGE_DIMS:
            if d not in cov:
                issues.append({"level": "warning", "msg": "coverage 缺维度 %s" % d})
    else:
        issues.append({"level": "warning", "msg": "coverage 非对象"})
    claim_ids = {c.get("id") for c in (audit.get("claims") or []) if isinstance(c, dict)}
    for f in (audit.get("findings") or []):
        if not isinstance(f, dict):
            continue
        fid = f.get("id", "?")
        for field in ("id", "type", "severity", "locator", "quote", "claim",
                      "evidence", "reasoning", "suggestion"):
            if not f.get(field):
                issues.append({"level": "warning",
                               "msg": "finding %s 缺字段 %s" % (fid, field)})
        if f.get("severity") not in SEVERITIES:
            issues.append({"level": "warning", "msg": "finding %s severity 非法" % fid})
        if f.get("type") not in FINDING_TYPES:
            issues.append({"level": "warning", "msg": "finding %s type 非法" % fid})
        if f.get("claim_id") and f["claim_id"] not in claim_ids:
            issues.append({"level": "warning",
                           "msg": "finding %s 的 claim_id=%s 未在 claims 中"
                                  % (fid, f["claim_id"])})
    for c in (audit.get("claims") or []):
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "?")
        for field in ("id", "text", "speaker", "verdict"):
            if not c.get(field):
                issues.append({"level": "warning",
                               "msg": "claim %s 缺字段 %s" % (cid, field)})
    return issues


def _md(s: Any) -> str:
    s = "" if s is None else str(s)
    return s.replace("|", "\\|").replace("\n", " ")


def render_report(result: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("# 论文逻辑与论证审查报告")
    L.append("")
    L.append("- case_id: `%s`" % result.get("case_id", ""))
    L.append("- status: `%s`" % result.get("status", ""))
    L.append("")
    task = result.get("task") or {}
    L.append("## 任务")
    L.append("- challenge_id: `%s`" % task.get("challenge_id"))
    L.append("- title: %s" % task.get("task_title"))
    L.append("")
    audit = result.get("audit") or {}
    L.append("## 总评")
    L.append(audit.get("summary") or "（未填写）")
    L.append("")
    L.append("## 主张清单")
    claims = audit.get("claims") or []
    if claims:
        L.append("| ID | speaker | verdict | 主张 | 定位 |")
        L.append("|---|---|---|---|---|")
        for c in claims:
            L.append("| %s | %s | %s | %s | %s |" % (
                _md(c.get("id")), _md(c.get("speaker")), _md(c.get("verdict")),
                _md(c.get("text")), _md(c.get("locator"))))
    else:
        L.append("（空）")
    L.append("")
    L.append("## 问题清单")
    findings = audit.get("findings") or []
    if findings:
        for f in findings:
            L.append("### %s · %s · %s" % (f.get("id"), f.get("type"), f.get("severity")))
            L.append("- 定位: `%s`" % f.get("locator"))
            L.append("- 原文: > %s" % f.get("quote"))
            L.append("- 主张: %s" % f.get("claim"))
            L.append("- 依据: %s" % f.get("evidence"))
            L.append("- 理由(三问): %s" % f.get("reasoning"))
            L.append("- 建议: %s" % f.get("suggestion"))
            L.append("- 状态/置信度: %s / %s" % (f.get("status"), f.get("confidence")))
            if f.get("claim_id"):
                L.append("- 关联主张: `%s`" % f["claim_id"])
            L.append("")
    else:
        L.append("（空）")
    L.append("")
    L.append("## 六类检查项覆盖")
    cov = audit.get("coverage") or {}
    L.append("| 维度 | 状态 | 检查数 | 说明 |")
    L.append("|---|---|---|---|")
    for d, c in cov.items():
        L.append("| %s | %s | %s | %s |" % (
            _md(d), _md(c.get("status")), c.get("checked"), _md(c.get("note"))))
    L.append("")
    L.append("## 来源")
    for s in result.get("sources") or []:
        suffix = (" · " + str(s.get("error"))) if s.get("error") else ""
        L.append("- `%s` (kind=%s, pages=%s, chars=%s%s)" % (
            s.get("path"), s.get("kind"), s.get("n_pages"), s.get("chars"), suffix))
    L.append("")
    for key, label in (("pending_review", "待复核"), ("pending_human", "需人工"),
                       ("limitations", "限制"), ("uncertainty", "不确定性"),
                       ("errors", "错误"), ("degradations", "降级")):
        L.append("## %s" % label)
        items = result.get(key) or []
        if items:
            for x in items:
                L.append("- %s" % str(x))
        else:
            L.append("（无）")
        L.append("")
    L.append("## 变更记录")
    for e in result.get("change_log") or []:
        L.append("- %s %s: %s" % (e.get("ts"), e.get("event"), e.get("note")))
    L.append("")
    return "\n".join(L)
