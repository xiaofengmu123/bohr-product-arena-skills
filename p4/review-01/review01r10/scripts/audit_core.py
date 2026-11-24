#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_core：论文质量审查 Skill 的核心库（仅 Python 标准库）。

架构定位（与常见"单一脚本"方案不同的取舍）：
- 本库只做**机械可判定的部分**：输入发现、文本抽取、locator 中间表示、
  逐字引用校验（规范化 + 截断 SHA-256 + 子串命中）、契约回填、确定性成稿。
- 六类分面的**推理判断**（事实核验、引用支持、方法设计、统计报告、论证链、
  学术规范）由 Agent 依据 references/ 中的判据完成，写入 work/draft.json；
  本库不替 Agent 阅读理解，也不臆断任何问题。

这是 "verify-then-emit" 架构的核心：先建立来源索引，Agent 写草稿，
脚本对每一条 quote 做逐字命中校验，未命中则把 confirmed 降级为 to_review，
从机制上杜绝"凭感觉说原文有问题却无逐字证据"。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 枚举与常量
# ---------------------------------------------------------------------------
FACETS = [
    "fact_check", "citation_support", "method_design",
    "statistics_report", "argument_chain", "academic_norms",
]
SEVERITIES = ["critical", "major", "minor", "suggestion"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
ISSUE_TYPES = ["factual_error", "insufficient_evidence", "standards_issue", "suggestion"]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["pass", "needs_revision", "reject"]

ENVELOPE_KEYS = [
    "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "changes", "change_log", "runtime",
]
RESULT_KEYS = ["findings", "unchecked_items", "not_applicable", "coverage", "verdict_summary"]

FINDING_REQUIRED = [
    "finding_id", "facet", "severity", "issue_type", "verdict",
    "claim", "location", "quote", "rationale", "revision_suggestion", "confidence",
]

MAX_TEXT_FILE = 2 * 1024 * 1024
TEXT_KINDS = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml",
              ".tex", ".rst", ".log", ".bib", ".xml", ".html", ".tsv"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg"}
SKIP_NAMES = {"case.json", "task.md"}

ARRAY_HINTS = (
    "list", "items", "findings", "errors", "warnings", "notes", "sources",
    "unchecked", "references", "evidence", "changes", "records", "logs", "results",
)


# ---------------------------------------------------------------------------
# 文本读取与规范化
# ---------------------------------------------------------------------------
def read_text(path: Path):
    """UTF-8 优先（自动去 BOM），坏编码降级 gb18030 / latin-1。返回 (text, problem)。"""
    if not path.exists():
        return None, f"not found: {path}"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gb18030"), "non-utf8 bytes; decoded as gb18030"
    except Exception:
        pass
    return data.decode("latin-1", errors="replace"), "undecodable bytes; latin-1 lossy fallback"


def normalize_ws(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def quote_hash(s):
    """逐字引用的规范化截断 SHA-256（16 位十六进制），用于把证据机械绑定到原文。"""
    return hashlib.sha256(normalize_ws(s).encode("utf-8")).hexdigest()[:16]


def locator(page, idx):
    return f"p{page}#para{idx}"


def parse_locator(s):
    m = re.fullmatch(r"\s*p\s*(\d+)\s*#\s*para\s*(\d+)\s*", str(s), re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"\s*para\s*(\d+)\s*", str(s), re.I)
    if m:
        return 1, int(m.group(1))
    return None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 段落中间表示：每段一个 dict {page, idx, text}，locator = p{page}#para{idx}
# ---------------------------------------------------------------------------
def split_paragraphs(text, page=1):
    out = []
    idx = 1
    for raw in re.split(r"\n\s*\n|\r\n\s*\r\n|\f", text or ""):
        t = normalize_ws(raw)
        if t:
            out.append({"page": page, "idx": idx, "text": t})
            idx += 1
    return out


# ---------------------------------------------------------------------------
# PDF / DOCX 文本抽取（纯标准库、尽力而为、诚实报告局限）
# ---------------------------------------------------------------------------
_TJ = re.compile(rb"\(((?:[^()\\]|\\.)*)\)\s*T[jJ]")
_TICK = re.compile(rb"\(((?:[^()\\]|\\.)*)\)\s*'")
_HEX = re.compile(rb"<([0-9A-Fa-f\s]{2,})>\s*T[jJ]")
_STR = re.compile(r"\(((?:[^()\\]|\\.)*)\)")


def _pdf_unescape(s):
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx in "()\\":
                out.append(nx); i += 2; continue
            if nx == "n":
                out.append("\n"); i += 2; continue
            if nx == "r":
                out.append("\r"); i += 2; continue
            if nx == "t":
                out.append("\t"); i += 2; continue
            if nx == "b":
                out.append("\b"); i += 2; continue
            if nx == "f":
                out.append("\f"); i += 2; continue
            if nx in "01234567":
                j = i + 1
                while j < n and j < i + 4 and s[j] in "01234567":
                    j += 1
                try:
                    out.append(chr(int(s[i + 1:j], 8))); i = j; continue
                except ValueError:
                    pass
            out.append(nx); i += 2; continue
        out.append(c); i += 1
    return "".join(out)


def _pdf_hex_text(hx):
    hx = re.sub(r"\s+", "", hx)
    if len(hx) % 2:
        hx += "0"
    try:
        b = bytes.fromhex(hx)
    except ValueError:
        return ""
    if b and sum(1 for x in b[1::2] if x == 0) > len(b) // 4:
        try:
            return b.decode("utf-16-be", errors="replace")
        except Exception:
            pass
    try:
        return b.decode("latin-1")
    except Exception:
        return b.decode("latin-1", errors="replace")


def _extract_runs(chunk: bytes) -> str:
    pieces = []
    for m in _TJ.finditer(chunk):
        pieces.append(_pdf_unescape(m.group(1).decode("latin-1", errors="replace")))
    for m in _TICK.finditer(chunk):
        pieces.append(_pdf_unescape(m.group(1).decode("latin-1", errors="replace")))
    for m in re.finditer(rb"\[((?:[^\[\]]+))\]\s*TJ", chunk):
        inner = m.group(1).decode("latin-1", errors="replace")
        for mm in _STR.finditer(inner):
            pieces.append(_pdf_unescape(mm.group(1)))
    for m in _HEX.finditer(chunk):
        pieces.append(_pdf_hex_text(m.group(1).decode("ascii", errors="ignore")))
    return " ".join(pieces)


def extract_pdf_text(data: bytes):
    """返回 (pages, problems)。页面分组为近似：每个内容流视作一页。"""
    problems = []
    pages = []
    if not data:
        return [], ["empty pdf"]
    try:
        streams = []
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
            body = m.group(1)
            dec = None
            try:
                dec = zlib.decompress(body)
            except Exception:
                try:
                    dec = zlib.decompress(body.rstrip(b"\r\n"))
                except Exception:
                    dec = body
            streams.append(dec)
        if not streams:
            streams = [data]
        for st in streams:
            runs = _extract_runs(st)
            if runs.strip():
                pages.append(runs)
        if not pages:
            problems.append("no extractable text runs (scanned PDF / missing ToUnicode); "
                            "use platform pdf-text-tool and drop page text into work/text/<sid>.txt, then rescan")
        else:
            problems.append("built-in extractor: page grouping approximate; "
                            "prefer platform pdf-text-tool for page-accurate locators")
    except Exception as exc:
        problems.append(f"pdf extract error: {exc}")
    return pages, problems


def _xml_unescape(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&"))


def extract_docx_paras(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as exc:
        return [], f"docx unzip/parse error: {exc}"
    lines = []
    for p in re.split(r"</w:p>", xml, flags=re.I):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.I | re.S)
        if texts:
            line = "".join(_xml_unescape(t) for t in texts).strip()
            if line:
                lines.append(line)
    return lines, (None if lines else "no paragraphs in word/document.xml")


# ---------------------------------------------------------------------------
# 输入发现与抽取
# ---------------------------------------------------------------------------
def classify_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in PDF_KINDS:
        return "pdf"
    if ext in DOCX_KINDS:
        return "docx"
    if ext in IMAGE_KINDS:
        return "image"
    if ext in TEXT_KINDS:
        return "text"
    return "other"


def material_files(input_dir: Path):
    if not input_dir.exists():
        return []
    out = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.name in SKIP_NAMES:
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        out.append(p)
    return out


def extract_material(path: Path, kind: str):
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        paras = []
        for pi, pg in enumerate(pages, 1):
            paras.extend(split_paragraphs(pg, page=pi))
        return paras, problems
    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return [], [problem]
        paras = split_paragraphs("\n\n".join(lines), page=1)
        return paras, ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    paras = split_paragraphs(text, page=1)
    return paras, ([problem] if problem else [])


def extract_or_reuse(path: Path, kind: str, work: Path, sid: str, rescan: bool):
    pre = work / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            pages = [pg for pg in text.split("\f")]
            paras = []
            for pi, pg in enumerate(pages, 1):
                paras.extend(split_paragraphs(pg, page=pi))
            notes = [f"reused pre-extracted text {pre.name}"]
            if problem:
                notes.append(problem)
            return paras, notes
    return extract_material(path, kind)


def _write_text_store(work: Path, sid: str, paras):
    pages = {}
    for x in paras:
        pages.setdefault(x["page"], []).append(x["text"])
    joined = "\f".join("\n\n".join(pages.get(pi, [])) for pi in sorted(pages))
    if joined.strip():
        try:
            (work / "text" / f"{sid}.txt").write_text(joined, encoding="utf-8")
        except OSError:
            pass


def scan_input(input_dir: Path, work: Path, rescan=False):
    state = {
        "case_id": None, "task_instruction": "", "output_contract": None,
        "contract_keys": [], "degradations": [], "errors": [], "sources": [],
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / "text").mkdir(parents=True, exist_ok=True)

    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            state["errors"].append({"file": "case.json", "error": problem})
            state["degradations"].append("case.json unreadable")
        else:
            if problem:
                state["degradations"].append(f"case.json encoding: {problem}")
            case = None
            try:
                case = json.loads(text)
            except Exception as exc:
                state["errors"].append({"file": "case.json", "error": f"bad JSON: {exc}"})
                state["degradations"].append("case.json bad JSON; contract unknown")
            if isinstance(case, dict):
                state["case_id"] = case.get("case_id") or case.get("id") or case.get("task_id")
                state["task_instruction"] = (case.get("task_instruction") or case.get("task")
                                            or case.get("instruction") or "")
                state["output_contract"] = case.get("output_contract")
                state["contract_keys"] = contract_keys_of(state["output_contract"])
    else:
        state["degradations"].append("case.json missing")

    task_path = input_dir / "task.md"
    if task_path.exists() and not state["task_instruction"]:
        t, _ = read_text(task_path)
        if t:
            state["task_instruction"] = t[:4000]

    for i, p in enumerate(material_files(input_dir), 1):
        sid = f"D{i}"
        kind = classify_kind(p.name)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        entry = {"source_id": sid, "path": str(p), "kind": kind,
                 "chars": 0, "pages": 0, "paras": [], "problems": []}
        if kind in ("image", "other"):
            entry["problems"].append(f"{kind}: no text layer; needs OCR / external tool")
        elif size > MAX_TEXT_FILE:
            entry["problems"].append(f"size {size} > {MAX_TEXT_FILE}; skipped without pretending to check")
            state["degradations"].append(f"{p.name} exceeds {MAX_TEXT_FILE} bytes; skipped")
        else:
            paras, problems = extract_or_reuse(p, kind, work, sid, rescan)
            entry["paras"] = paras
            entry["problems"] = problems
            entry["chars"] = sum(len(x["text"]) for x in paras)
            entry["pages"] = max((x["page"] for x in paras), default=0)
            _write_text_store(work, sid, paras)
        state["sources"].append(entry)
    return state


# ---------------------------------------------------------------------------
# 输出契约回填
# ---------------------------------------------------------------------------
def contract_keys_of(contract):
    if isinstance(contract, dict):
        return list(contract.keys())
    return []


def echo_contract(contract):
    """原样回填全部键：不改名、不删除；未知键给 null/[]（按键名启发式选型）。"""
    if isinstance(contract, dict):
        out = {}
        for k, v in contract.items():
            if isinstance(v, dict):
                out[k] = echo_contract(v)
            elif isinstance(v, list):
                out[k] = []
            else:
                kl = str(k).lower()
                out[k] = [] if any(h in kl for h in ARRAY_HINTS) else None
        return out
    if isinstance(contract, list):
        return []
    return None


# ---------------------------------------------------------------------------
# 逐字引用校验
# ---------------------------------------------------------------------------
def find_quote(sources, quote, source_id=None):
    q = normalize_ws(quote)
    if not q:
        return None
    for src in sources:
        if source_id and src.get("source_id") != source_id:
            continue
        for p in src.get("paras", []):
            if q in normalize_ws(p.get("text", "")):
                return {"source_id": src.get("source_id"),
                        "locator": locator(p["page"], p["idx"])}
    return None


def verify_finding(f, sources):
    """就地给 finding 打上 quote_hash / quote_verified / quote_locator。返回命中 locator 或 None。"""
    quote = f.get("quote", "")
    h = quote_hash(quote)
    f["quote_hash"] = h
    hit = find_quote(sources, quote, None) if normalize_ws(quote) else None
    f["quote_verified"] = bool(hit)
    f["quote_locator"] = hit["locator"] if hit else None
    for e in f.get("evidence", []) if isinstance(f.get("evidence"), list) else []:
        eq = e.get("quote", "")
        e["quote_hash"] = quote_hash(eq)
        ehit = find_quote(sources, eq, e.get("source_id")) if normalize_ws(eq) else None
        e["verified"] = bool(ehit)
        e["matched_locator"] = ehit["locator"] if ehit else None
    return hit


def validate_finding(f, idx=0):
    problems = []
    if not isinstance(f, dict):
        return [f"finding #{idx}: not an object"]
    for k in FINDING_REQUIRED:
        if k not in f or f[k] in (None, ""):
            problems.append(f"finding #{idx}: missing/empty {k}")
    if f.get("facet") not in FACETS:
        problems.append(f"finding #{idx}: bad facet {f.get('facet')!r}")
    if f.get("severity") not in SEVERITIES:
        problems.append(f"finding #{idx}: bad severity {f.get('severity')!r}")
    if f.get("issue_type") not in ISSUE_TYPES:
        problems.append(f"finding #{idx}: bad issue_type {f.get('issue_type')!r}")
    if f.get("verdict") not in VERDICTS:
        problems.append(f"finding #{idx}: bad verdict {f.get('verdict')!r}")
    if f.get("confidence") not in CONFIDENCES:
        problems.append(f"finding #{idx}: bad confidence {f.get('confidence')!r}")
    return problems


# ---------------------------------------------------------------------------
# 骨架、覆盖、汇总、排序、成稿
# ---------------------------------------------------------------------------
def make_skeleton(case_id, contract, status="partial"):
    echoed = echo_contract(contract)
    base = {"case_id": case_id}
    if isinstance(echoed, dict):
        base.update(echoed)
    else:
        base["output_contract"] = echoed
    base.update({
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "changes": [],
        "change_log": [{"ts": now_iso(), "stage": "init", "action": "skeleton written", "detail": ""}],
        "runtime": {"seconds": 0, "generated_at": now_iso()},
        "result": {
            "findings": [],
            "unchecked_items": [],
            "not_applicable": [],
            "coverage": {f: "unchecked" for f in FACETS},
            "verdict_summary": {
                "overall_verdict": "needs_revision",
                "counts": {
                    "total": 0,
                    "by_severity": {s: 0 for s in SEVERITIES},
                    "by_facet": {f: 0 for f in FACETS},
                    "by_verdict": {v: 0 for v in VERDICTS},
                },
            },
        },
    })
    return base


def summarize_sources(sources):
    return [{
        "source_id": s["source_id"], "path": s["path"], "kind": s["kind"],
        "chars": s.get("chars", 0), "pages": s.get("pages", 0),
        "problems": s.get("problems", []),
    } for s in sources]


def compute_coverage(findings, unchecked, not_applicable):
    checked = {f.get("facet") for f in findings if f.get("facet") in FACETS}
    na = {u.get("facet") for u in not_applicable if u.get("facet") in FACETS}
    un = {u.get("facet") for u in unchecked if u.get("facet") in FACETS}
    cov = {}
    for fac in FACETS:
        if fac in na:
            cov[fac] = "not_applicable"
        elif fac in checked:
            cov[fac] = "checked"
        elif fac in un:
            cov[fac] = "unchecked"
        else:
            cov[fac] = "unchecked"
    return cov


def build_verdict_summary(findings):
    counts = {
        "total": len(findings),
        "by_severity": {s: 0 for s in SEVERITIES},
        "by_facet": {f: 0 for f in FACETS},
        "by_verdict": {v: 0 for v in VERDICTS},
    }
    for f in findings:
        sev = f.get("severity")
        if sev in counts["by_severity"]:
            counts["by_severity"][sev] += 1
        fac = f.get("facet")
        if fac in counts["by_facet"]:
            counts["by_facet"][fac] += 1
        ver = f.get("verdict")
        if ver in counts["by_verdict"]:
            counts["by_verdict"][ver] += 1
    crit_confirmed = any(f.get("severity") == "critical" and f.get("verdict") == "confirmed"
                         for f in findings)
    major_confirmed = any(f.get("severity") == "major" and f.get("verdict") == "confirmed"
                          for f in findings)
    crit_open = any(f.get("severity") == "critical" and f.get("verdict") in ("to_review", "not_checked")
                    for f in findings)
    if crit_confirmed:
        overall = "reject"
    elif major_confirmed or crit_open:
        overall = "needs_revision"
    else:
        overall = "pass"
    return {"overall_verdict": overall, "counts": counts}


def sort_findings(findings):
    def key(f):
        fac = FACETS.index(f.get("facet")) if f.get("facet") in FACETS else 99
        sev = SEVERITY_RANK.get(f.get("severity"), 99)
        return (fac, sev, str(f.get("location", "")))
    return sorted(findings, key=key)


def diff_changes(previous, current):
    if not isinstance(previous, dict):
        return []
    out = []
    for k in ("status", "case_id"):
        if previous.get(k) != current.get(k):
            out.append({"field": k, "from": previous.get(k), "to": current.get(k)})
    prev_n = len((previous.get("result") or {}).get("findings") or [])
    cur_n = len((current.get("result") or {}).get("findings") or [])
    if prev_n != cur_n:
        out.append({"field": "result.findings.length", "from": prev_n, "to": cur_n})
    return out


def build_result(index, draft, previous=None):
    case_id = index.get("case_id")
    contract = index.get("output_contract")
    status = draft.get("status") or "partial"
    findings_raw = draft.get("findings") or []
    unchecked = draft.get("unchecked_items") or []
    not_applicable = draft.get("not_applicable") or []
    sources = index.get("sources") or []

    problems = []
    findings = []
    auto_uncertainty = []
    for i, f in enumerate(findings_raw):
        if not isinstance(f, dict):
            problems.append(f"finding #{i}: not an object; skipped")
            continue
        problems.extend(validate_finding(f, i))
        hit = verify_finding(f, sources)
        if f.get("verdict") == "confirmed" and not f.get("quote_verified"):
            f["verdict"] = "to_review"
            auto_uncertainty.append({
                "item": f.get("finding_id", "?"),
                "reason": "quote not verified verbatim against indexed text; verdict downgraded confirmed->to_review",
                "affects": f.get("facet"),
            })
        if f.get("quote_verified") and f.get("quote_locator"):
            lp = parse_locator(f.get("location"))
            lq = parse_locator(f.get("quote_locator"))
            if lp and lq and lp != lq:
                auto_uncertainty.append({
                    "item": f.get("finding_id", "?"),
                    "reason": f"location {f.get('location')} differs from quote hit {f.get('quote_locator')}; verify the locator",
                    "affects": f.get("facet"),
                })
        findings.append(f)

    result = make_skeleton(case_id, contract, status)
    result["sources"] = summarize_sources(sources)
    result["errors"].extend(index.get("errors", []))
    result["degradations"].extend(index.get("degradations", []))
    for p in problems:
        result["errors"].append({"stage": "build", "detail": p})
    result["uncertainty"] = (draft.get("uncertainty") or []) + auto_uncertainty
    result["pending_human"] = draft.get("pending_human") or []
    result["result"]["findings"] = sort_findings(findings)
    result["result"]["unchecked_items"] = unchecked
    result["result"]["not_applicable"] = not_applicable
    result["result"]["coverage"] = compute_coverage(findings, unchecked, not_applicable)
    result["result"]["verdict_summary"] = build_verdict_summary(findings)
    result["changes"] = diff_changes(previous, result)
    result["change_log"].append({
        "ts": now_iso(), "stage": "build", "action": "final result written",
        "detail": f"{len(findings)} findings; status={status}",
    })
    result["runtime"] = {"seconds": result["runtime"].get("seconds", 0), "generated_at": now_iso()}
    return result


def render_report(result):
    L = []
    L.append("# 论文质量审查报告")
    L.append("")
    L.append(f"- case_id: {result.get('case_id')}")
    L.append(f"- status: {result.get('status')}")
    cov = (result.get("result") or {}).get("coverage", {})
    L.append("- coverage: " + "; ".join(f"{f}={v}" for f, v in cov.items()))
    vs = (result.get("result") or {}).get("verdict_summary", {})
    L.append(f"- overall_verdict: {vs.get('overall_verdict')}  counts: {json.dumps(vs.get('counts'), ensure_ascii=False)}")
    L.append("")
    for f in (result.get("result") or {}).get("findings", []):
        L.append(f"## {f.get('finding_id')} [{f.get('facet')} · {f.get('severity')} · {f.get('verdict')}]")
        L.append(f"- 位置：{f.get('location')}")
        L.append(f"- 原文：\u201c{f.get('quote')}\u201d")
        if f.get("quote_verified"):
            L.append(f"- 引用核验：通过（命中 {f.get('quote_locator')}，hash {f.get('quote_hash')}）")
        else:
            L.append("- 引用核验：未逐字命中（需回原文复核）")
        L.append(f"- 问题：{f.get('claim')}")
        L.append(f"- 依据：{f.get('rationale')}")
        L.append(f"- 建议：{f.get('revision_suggestion')}")
        for e in f.get("evidence", []) if isinstance(f.get("evidence"), list) else []:
            tag = "verified" if e.get("verified") else "UNVERIFIED"
            L.append(f"  - 证据 {e.get('source_id')} {e.get('locator')} \u201c{e.get('quote')}\u201d [{tag}]")
        L.append("")
    if (result.get("result") or {}).get("unchecked_items"):
        L.append("## 未检查项")
        for u in result["result"]["unchecked_items"]:
            L.append(f"- {u.get('facet')}: {u.get('reason')}")
        L.append("")
    if result.get("uncertainty"):
        L.append("## 不确定性")
        for u in result["uncertainty"]:
            L.append(f"- {u.get('item')}: {u.get('reason')}")
        L.append("")
    if result.get("degradations"):
        L.append("## 降级说明")
        for d in result["degradations"]:
            L.append(f"- {d}")
        L.append("")
    L.append("> 本报告由 build 自动生成；每条 finding 的原文定位与逐字引用可回原文复核。")
    return "\n".join(L)
