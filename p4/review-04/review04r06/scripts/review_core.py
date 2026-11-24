# -*- coding: utf-8 -*-
"""review_core.py — 论文逻辑与论证审查 Skill 的纯标准库核心。

本模块只做确定性、可复核的机械工作：
  输入发现 → 文本抽取 → 段落切分 → 定位符 → 模型校验 → 证据索引 → 报告渲染。
不含随机性，不含任何预置答案；所有结论内容由 Agent 在运行时产出并写入
work/review_model.json，本模块负责规范化、校验与回填。
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 六类检查项：键名稳定（用于 coverage / plan / evidence 对齐），中文仅为展示。
COVERAGE_DIMS = {
    "factual_verification": "事实核验",
    "citation_support": "引用支持",
    "method_design": "方法设计",
    "statistical_reporting": "统计报告",
    "argument_chain": "论证链",
    "academic_norms": "学术规范",
}

FINDING_TYPES = (
    "overclaim",            # 结论强度超出数据
    "unsupported_citation",  # 引用不支持所引观点
    "method_mismatch",       # 方法回答不了研究问题
    "statistical_misreport",  # 统计口径与结论方向相反/误报
    "reasoning_gap",         # 推理跳跃 / 隐含假设 / 替代解释
    "causality_conflation",  # 相关误为因果
    "consistency_break",     # 章节前后不一致 / 承诺未交付
    "norm_issue",            # 学术规范问题
    "suggestion",            # 建议性意见
)
FINDING_SEVERITIES = ("critical", "major", "minor", "suggestion")
FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")
CONFIDENCES = ("high", "medium", "low")
CLAIM_SPEAKERS = ("author", "cited", "assumption")
CLAIM_VERDICTS = ("supported", "overclaimed", "unsupported", "pending")
COMMIT_STATUSES = ("delivered", "missing", "partial", "pending")

TEXT_EXTS = {".pdf", ".txt", ".md", ".json", ".csv", ".tex", ".xml", ".html", ".rst", ".yaml", ".yml"}
MAX_FILE_BYTES = 2_000_000
MAX_PARA_CHARS = 8_000

_HEADING_RE = re.compile(
    r"^(abstract|introduction|background|methods?|materials?|results?|discussions?|conclusions?|"
    r"limitations?|references|acknowledg\w*|摘要|引言|前言|背景|方法|材料|结果|讨论|结论|局限|参考文献|致谢)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# 时间与基础 IO
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """容错读文本：BOM / 坏编码 / 超限都降级并记录 error。"""
    p = Path(path)
    out: Dict[str, Any] = {"path": str(p), "chars": 0, "bytes": 0, "error": None, "text": ""}
    try:
        data = p.read_bytes()
        out["bytes"] = len(data)
        if len(data) > max_bytes:
            data = data[:max_bytes]
            out["error"] = "truncated: file exceeds %d bytes" % max_bytes
        text = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                text = data.decode(enc)
                if enc in ("gb18030", "latin-1"):
                    out["error"] = (out["error"] or "") + " decoded with fallback %s" % enc
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            text = data.decode("utf-8", errors="replace")
            out["error"] = (out["error"] or "") + " undecodable; replaced"
        out["text"] = text
        out["chars"] = len(text)
    except Exception as e:  # noqa: BLE001
        out["error"] = "read failed: %s" % e
    return out


def read_json_tolerant(path: str) -> tuple:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %s" % e


# ---------------------------------------------------------------------------
# 输入发现与文本抽取
# ---------------------------------------------------------------------------
def discover_input(input_dir: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {"case_json": None, "task_md": None, "materials": [], "other": []}
    root = Path(input_dir)
    if not root.is_dir():
        return d
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "case.json":
            d["case_json"] = str(p)
        elif name == "task.md":
            d["task_md"] = str(p)
        elif p.suffix.lower() in TEXT_EXTS:
            d["materials"].append(str(p))
        else:
            d["other"].append(str(p))
    return d


def extract_pdf_text(path: str) -> tuple:
    """用 pdftotext 抽取 PDF 文本，按 \f 分页；不可用/失败则返回错误。"""
    exe = shutil.which("pdftotext")
    if exe is None:
        return [], "pdftotext 不可用"
    try:
        proc = subprocess.run(
            [exe, "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=300,
        )
    except Exception as e:  # noqa: BLE001
        return [], "pdftotext 执行异常: %s" % e
    if proc.returncode != 0:
        return [], "pdftotext 退出码 %d: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
    raw = proc.stdout.decode("utf-8", "replace")
    pages = [pg for pg in raw.split("\f") if pg.strip()]
    return pages, None


def split_paragraphs(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n[ \t]*\n+", text)
    out: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > MAX_PARA_CHARS:
            part = part[:MAX_PARA_CHARS] + "\n[…]（段落过长，已截断）"
        out.append(part)
    return out


def _looks_like_heading(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 90:
        return False
    if t.startswith("#"):
        return True
    if _HEADING_RE.match(t):
        return True
    if re.match(r"^\d+(\.\d+)*[\.、\)]\s*\S", t) and len(t) <= 80:
        return True
    return False


def _heading_label(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^#+\s*", "", s)
    s = re.sub(r"^\d+(\.\d+)*[\.、\)]\s*", "", s)
    s = s.strip()
    return (s[:60] if s else "未命名章节")


def classify(path: str, discovered: Dict[str, Any]) -> str:
    if discovered.get("case_json") and path == discovered["case_json"]:
        return "contract"
    if discovered.get("task_md") and path == discovered["task_md"]:
        return "task"
    return "material"


def build_records(input_dir: str, discovered: Dict[str, Any]) -> tuple:
    """把输入文件转成带全局段落 ID 的语料；返回 (records, sources)。"""
    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    ordered: List[tuple] = []
    if discovered.get("case_json"):
        ordered.append((discovered["case_json"], "contract"))
    if discovered.get("task_md"):
        ordered.append((discovered["task_md"], "task"))
    for m in discovered.get("materials", []):
        ordered.append((m, "material"))

    for path_s, kind in ordered:
        p = Path(path_s)
        src_entry: Dict[str, Any] = {
            "path": path_s, "kind": kind, "n_pages": None,
            "chars": 0, "error": None, "degradations": [],
        }
        if not p.is_file():
            src_entry["error"] = "file missing"
            sources.append(src_entry)
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".pdf":
                pages, err = extract_pdf_text(path_s)
                src_entry["n_pages"] = len(pages)
                if err:
                    src_entry["error"] = err
                    src_entry["degradations"].append("pdf_text_unavailable")
                if not pages:
                    sources.append(src_entry)
                    continue
                total = 0
                for pi, page_text in enumerate(pages, 1):
                    total += len(page_text)
                    for para in split_paragraphs(page_text):
                        records.append({"id": None, "src": path_s, "page": pi,
                                        "section": None, "text": para, "kind": kind,
                                        "chars": len(para)})
                src_entry["chars"] = total
            else:
                rd = read_text_safe(path_s)
                src_entry["chars"] = rd["chars"]
                if rd["error"]:
                    src_entry["error"] = rd["error"]
                    src_entry["degradations"].append("text_fallback")
                src_entry["n_pages"] = 1
                for para in split_paragraphs(rd["text"]):
                    records.append({"id": None, "src": path_s, "page": None,
                                    "section": None, "text": para, "kind": kind,
                                    "chars": len(para)})
        except Exception as e:  # noqa: BLE001
            src_entry["error"] = "extract failed: %s" % e
        sources.append(src_entry)

    current_section = "正文"
    current_src = None
    for i, rec in enumerate(records):
        rec["id"] = "P%04d" % (i + 1)
        if rec["src"] != current_src:
            current_src = rec["src"]
            current_section = "正文"
        if _looks_like_heading(rec["text"]):
            current_section = _heading_label(rec["text"])
            rec["section"] = current_section
        else:
            rec["section"] = current_section
    return records, sources


def locator(rec: Dict[str, Any]) -> str:
    if rec.get("page"):
        return "p%s ¶%s" % (rec["page"], rec["id"])
    return "¶%s" % rec["id"]


def section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_sec: Dict[str, List[str]] = {}
    for r in records:
        by_sec.setdefault(r["section"], []).append(r["id"])
    out = []
    for sec, ids in by_sec.items():
        pages = sorted({r["page"] for r in records if r["section"] == sec and r["page"]})
        out.append({"section": sec, "paras": ids, "pages": pages})
    return out


# ---------------------------------------------------------------------------
# 元数据与结果骨架
# ---------------------------------------------------------------------------
def task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None}
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                    or data.get("id"))
            meta["task_title"] = data.get("title") or data.get("task_title")
    if discovered.get("task_md"):
        rd = read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in rd["text"].splitlines()[:80]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta.setdefault("task_title", ls.lstrip("#").strip())
                break
    return meta


def derive_case_id(discovered: Dict[str, Any], meta: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for k in ("case_id", "caseId", "caseid", "id"):
                v = data.get(k)
                if isinstance(v, str) and v:
                    return v
        return Path(discovered["case_json"]).stem
    if discovered.get("task_md"):
        return "case_from_task"
    return "unknown_case"


def contract_extra(discovered: Dict[str, Any]) -> Dict[str, Any]:
    if not discovered.get("case_json"):
        return {}
    data, _ = read_json_tolerant(discovered["case_json"])
    if isinstance(data, dict):
        c = data.get("output_contract")
        if isinstance(c, dict):
            return dict(c)
    return {}


def minimal_result() -> Dict[str, Any]:
    return {
        "case_id": "unknown_case",
        "status": "input_error",
        "task": {"challenge_id": None, "task_title": None},
        "review": {
            "summary": "",
            "findings": [],
            "claims": [],
            "commitments": [],
            "coverage": {d: {"status": "pending", "checked": 0, "note": ""}
                         for d in COVERAGE_DIMS},
            "section_map": [],
        },
        "evidence_map": [],
        "sources": [],
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }


def build_result_skeleton(case_id: str, meta: Dict[str, Any], contract: Dict[str, Any],
                          sources: List[Dict[str, Any]], records: List[Dict[str, Any]],
                          status: str) -> Dict[str, Any]:
    base = minimal_result()
    base["case_id"] = case_id
    base["status"] = status
    base["task"] = {"challenge_id": meta.get("challenge_id"),
                    "task_title": meta.get("task_title")}
    base["sources"] = sources
    base["review"]["section_map"] = section_map(records)
    base["change_log"] = [{"ts": now_iso(), "event": "scaffold_written",
                           "note": "status=%s paragraphs=%d sources=%d"
                                   % (status, len(records), len(sources))}]
    # 原样回填 output_contract 全部键：顶层合并 + 保留 output_contract 副本。
    if contract:
        for k, v in contract.items():
            base.setdefault(k, v)
        base["output_contract"] = dict(contract)
    return base


def empty_model() -> Dict[str, Any]:
    return {
        "version": 1,
        "summary": "",
        "coverage": {},
        "claims": [],
        "commitments": [],
        "findings": [],
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
    }


# ---------------------------------------------------------------------------
# 中间表示（review_model.json）的校验与规范化
# ---------------------------------------------------------------------------
def _str_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if str(x).strip()]


def check_finding(f: Any) -> tuple:
    errors, warnings = [], []
    if not isinstance(f, dict):
        return ["finding 不是对象"], [], None
    nf = dict(f)
    for k in ("id", "type", "locator", "quote", "claim", "evidence", "reasoning", "suggestion"):
        if not str(nf.get(k) or "").strip():
            errors.append("finding 缺少必填字段 %s" % k)
    if not str(nf.get("severity") or "").strip():
        errors.append("finding 缺少 severity")
    else:
        if nf["severity"] not in FINDING_SEVERITIES:
            warnings.append("finding[%s].severity=%s 非法 → minor" % (nf.get("id"), nf["severity"]))
            nf["severity"] = "minor"
    for k, allowed, default in (("status", FINDING_STATUSES, "pending_review"),
                                ("confidence", CONFIDENCES, "low")):
        v = nf.get(k)
        if v and v not in allowed:
            warnings.append("finding[%s].%s=%s 非法 → %s" % (nf.get("id"), k, v, default))
            nf[k] = default
        elif not v:
            nf[k] = default
    return errors, warnings, nf


def check_claim(c: Any) -> tuple:
    errors, warnings = [], []
    if not isinstance(c, dict):
        return ["claim 不是对象"], [], None
    nc = dict(c)
    for k in ("id", "text", "speaker", "section", "locator", "verdict", "evidence"):
        if not str(nc.get(k) or "").strip():
            errors.append("claim 缺少必填字段 %s" % k)
    if nc.get("speaker") and nc["speaker"] not in CLAIM_SPEAKERS:
        warnings.append("claim[%s].speaker=%s 非法 → author" % (nc.get("id"), nc["speaker"]))
        nc["speaker"] = "author"
    if nc.get("verdict") and nc["verdict"] not in CLAIM_VERDICTS:
        warnings.append("claim[%s].verdict=%s 非法 → pending" % (nc.get("id"), nc["verdict"]))
        nc["verdict"] = "pending"
    return errors, warnings, nc


def check_commitment(m: Any) -> tuple:
    errors, warnings = [], []
    if not isinstance(m, dict):
        return ["commitment 不是对象"], [], None
    nm = dict(m)
    for k in ("id", "promise", "locator", "status"):
        if not str(nm.get(k) or "").strip():
            errors.append("commitment 缺少必填字段 %s" % k)
    if nm.get("status") and nm["status"] not in COMMIT_STATUSES:
        warnings.append("commitment[%s].status=%s 非法 → pending" % (nm.get("id"), nm["status"]))
        nm["status"] = "pending"
    if "delivered_at" not in nm:
        nm["delivered_at"] = None
    return errors, warnings, nm


def validate_review_model(model: Any) -> tuple:
    """返回 (errors, warnings, normalized_model)。非法条目被剔除并记入 errors。"""
    if not isinstance(model, dict):
        return (["review_model 不是对象"], [],
                {"version": 1, "summary": "", "coverage": {}, "claims": [],
                 "commitments": [], "findings": [], "uncertainty": [],
                 "pending_review": [], "pending_human": [], "limitations": []})
    errors, warnings = [], []
    nm: Dict[str, Any] = {
        "version": int(model.get("version") or 1),
        "summary": str(model.get("summary") or ""),
        "coverage": model.get("coverage") if isinstance(model.get("coverage"), dict) else {},
        "claims": [], "commitments": [], "findings": [],
        "uncertainty": _str_list(model.get("uncertainty")),
        "pending_review": _str_list(model.get("pending_review")),
        "pending_human": _str_list(model.get("pending_human")),
        "limitations": _str_list(model.get("limitations")),
    }
    for f in (model.get("findings") or []):
        e, w, nf = check_finding(f)
        errors.extend(e)
        warnings.extend(w)
        if not e:
            nm["findings"].append(nf)
    for c in (model.get("claims") or []):
        e, w, nc = check_claim(c)
        errors.extend(e)
        warnings.extend(w)
        if not e:
            nm["claims"].append(nc)
    for m in (model.get("commitments") or []):
        e, w, nmc = check_commitment(m)
        errors.extend(e)
        warnings.extend(w)
        if not e:
            nm["commitments"].append(nmc)
    cov: Dict[str, Dict[str, Any]] = {}
    for d in COVERAGE_DIMS:
        c = nm["coverage"].get(d) if isinstance(nm["coverage"], dict) else {}
        c = c if isinstance(c, dict) else {}
        status = c.get("status") if c.get("status") in ("done", "partial", "skipped", "pending", "todo") else "pending"
        checked = int(c.get("checked", 0) or 0)
        note = str(c.get("note") or "")
        cov[d] = {"status": status, "checked": checked, "note": note}
    nm["coverage"] = cov
    return errors, warnings, nm


def build_plan(coverage: Dict[str, Any]) -> Dict[str, Any]:
    dims: Dict[str, Dict[str, Any]] = {}
    for d in COVERAGE_DIMS:
        c = coverage.get(d) if isinstance(coverage, dict) else {}
        c = c if isinstance(c, dict) else {}
        status = c.get("status") if c.get("status") in ("todo", "partial", "done", "skipped", "pending") else "todo"
        dims[d] = {"status": status, "checked": int(c.get("checked", 0) or 0),
                   "note": str(c.get("note") or "")}
    return {
        "version": 1,
        "phases": [
            {"id": "scaffold", "label": "脚手架与语料切分", "depends_on": []},
            {"id": "claim_extraction", "label": "主张抽取与 speaker 区分", "depends_on": ["scaffold"]},
            {"id": "six_dim_review", "label": "六类检查项逐维核查", "depends_on": ["claim_extraction"]},
            {"id": "commitment_delivery", "label": "跨章节承诺—交付对照", "depends_on": ["six_dim_review"]},
            {"id": "final_selfcheck", "label": "终检与自检", "depends_on": ["commitment_delivery"]},
        ],
        "dimensions": dims,
        "updated_at": now_iso(),
    }


def derive_status(nm: Dict[str, Any]) -> str:
    cov = nm.get("coverage") or {}
    done = sum(1 for d in COVERAGE_DIMS if (cov.get(d) or {}).get("status") == "done")
    if not nm.get("findings") and not nm.get("claims"):
        return "partial"
    if done == len(COVERAGE_DIMS) and nm.get("findings"):
        return "ok"
    return "partial"


# ---------------------------------------------------------------------------
# 证据索引（确定性派生，保证与 review_model 不漂移）
# ---------------------------------------------------------------------------
def load_records(path: str) -> List[Dict[str, Any]]:
    data, _ = read_json_tolerant(path)
    if isinstance(data, list):
        return data
    return []


def build_evidence_map(model: Dict[str, Any], records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rec_by_id = {r["id"]: r for r in records}
    out = []
    for f in (model.get("findings") or []):
        refs = []
        for pid in (f.get("evidence_refs") or []):
            if not pid:
                continue
            r = rec_by_id.get(pid)
            if r:
                refs.append({"paragraph_id": pid, "locator": locator(r),
                             "quote": (r["text"] or "")[:400]})
        if not refs and f.get("locator"):
            refs.append({"paragraph_id": None, "locator": f["locator"],
                         "quote": str(f.get("quote") or "")[:400]})
        out.append({"finding_id": f.get("id"), "refs": refs})
    return out


# ---------------------------------------------------------------------------
# 校验与报告渲染
# ---------------------------------------------------------------------------
def check_result(result: Dict[str, Any]) -> List[str]:
    issues = []
    if not str(result.get("case_id") or "").strip():
        issues.append("缺 case_id")
    if result.get("status") not in RESULT_STATUSES:
        issues.append("status 非法: %s" % result.get("status"))
    review = result.get("review")
    if not isinstance(review, dict):
        issues.append("缺 review 对象")
        return issues
    findings = review.get("findings")
    if not isinstance(findings, list):
        issues.append("review.findings 非数组")
    else:
        for f in findings:
            if not isinstance(f, dict):
                issues.append("finding 含非对象项")
                continue
            for k in ("id", "type", "severity", "locator", "quote", "claim", "evidence", "reasoning", "suggestion"):
                if not str(f.get(k) or "").strip():
                    issues.append("finding[%s] 缺字段 %s" % (f.get("id"), k))
            if f.get("severity") not in FINDING_SEVERITIES:
                issues.append("finding[%s] severity 非法: %s" % (f.get("id"), f.get("severity")))
    coverage = review.get("coverage")
    if not isinstance(coverage, dict):
        issues.append("review.coverage 非对象")
    else:
        for d in COVERAGE_DIMS:
            if d not in coverage:
                issues.append("coverage 缺维度 %s" % d)
    for key in ("sources", "uncertainty", "pending_review", "pending_human", "change_log"):
        if key not in result:
            issues.append("缺顶层字段 %s" % key)
    return issues


def check_evidence_refs(result: Dict[str, Any], records: List[Dict[str, Any]]) -> List[str]:
    issues = []
    em = result.get("evidence_map")
    if not isinstance(em, list):
        return issues
    rec_ids = {r["id"] for r in records}
    for entry in em:
        if not isinstance(entry, dict):
            issues.append("evidence_map 含非对象项")
            continue
        fid = entry.get("finding_id")
        if not str(fid or "").strip():
            issues.append("evidence_map 缺 finding_id")
            continue
        refs = entry.get("refs")
        if not isinstance(refs, list) or not refs:
            issues.append("evidence_map[%s] 无 refs（定位不可复核）" % fid)
            continue
        for r in refs:
            if not isinstance(r, dict):
                issues.append("evidence_map[%s] ref 非对象" % fid)
                continue
            pid = r.get("paragraph_id")
            if pid and rec_ids and pid not in rec_ids:
                issues.append("evidence_map[%s] 引用不存在段落 %s" % (fid, pid))
            if not str(r.get("quote") or "").strip():
                issues.append("evidence_map[%s] 引用缺逐字 quote" % fid)
    return issues


def render_report(result: Dict[str, Any], records: List[Dict[str, Any]]) -> str:
    review = result.get("review", {})
    L: List[str] = []
    A = L.append
    A("# 论文逻辑与论证审查报告")
    A("")
    A("- case_id: %s" % result.get("case_id"))
    A("- status: %s" % result.get("status"))
    A("- 定位符格式: `p<页> ¶P<段落>`；文本无页码时省略 p。")
    A("")
    A("## 0. 方法与范围")
    for s in result.get("sources", []):
        err = (" error=%s" % s.get("error")) if s.get("error") else ""
        A("- %s [%s] n_pages=%s chars=%s%s" % (s.get("path"), s.get("kind"),
                                               s.get("n_pages"), s.get("chars"), err))
    A("")
    A("## 1. 总评")
    A(str(review.get("summary") or "（未填写）"))
    A("")
    A("## 2. 主张清单")
    if review.get("claims"):
        for c in review["claims"]:
            A("- **%s** [%s] %s — 判定: %s" % (c.get("id"), c.get("locator"),
                                               c.get("text"), c.get("verdict")))
            A("  - 证据: %s" % c.get("evidence"))
    else:
        A("（暂无）")
    A("")
    A("## 3. 承诺—交付对照")
    if review.get("commitments"):
        for m in review["commitments"]:
            A("- **%s** 承诺: %s → 交付: %s [%s]" % (m.get("id"), m.get("promise"),
                                                     m.get("delivered_at"), m.get("status")))
    else:
        A("（暂无）")
    A("")
    A("## 4. 问题清单")
    if review.get("findings"):
        for f in review["findings"]:
            A("### %s %s / %s" % (f.get("id"), f.get("type"), f.get("severity")))
            A("- 定位: `%s`" % f.get("locator"))
            A("- 原文: > %s" % f.get("quote"))
            A("- 主张: %s" % f.get("claim"))
            A("- 依据: %s" % f.get("evidence"))
            A("- 推理: %s" % f.get("reasoning"))
            A("- 建议: %s" % f.get("suggestion"))
            A("")
    else:
        A("（暂无）")
    A("")
    A("## 5. 六类检查覆盖")
    A("| 维度 | 状态 | 检查数 | 说明 |")
    A("|---|---|---|---|")
    for d, label in COVERAGE_DIMS.items():
        c = (review.get("coverage") or {}).get(d, {}) or {}
        A("| %s | %s | %s | %s |" % (label, c.get("status"), c.get("checked"), c.get("note")))
    A("")
    A("## 6. 证据索引")
    if result.get("evidence_map"):
        for e in result["evidence_map"]:
            A("- **%s**:" % e.get("finding_id"))
            for r in e.get("refs", []):
                A("  - %s `%s` %s" % (r.get("paragraph_id"), r.get("locator"),
                                      (r.get("quote") or "")[:80]))
    else:
        A("（暂无）")
    A("")
    A("## 7. 待复核与限制")
    for key, label in (("pending_review", "待复核"), ("pending_human", "需人工决定"),
                       ("uncertainty", "不确定"), ("limitations", "限制")):
        items = result.get(key) or []
        A("- %s: %s" % (label, "；".join(items) if items else "无"))
    A("")
    A("## 8. 变更记录")
    for cl in (result.get("change_log") or []):
        A("- [%s] %s: %s" % (cl.get("ts"), cl.get("event"), cl.get("note")))
    A("")
    return "\n".join(L)


def write_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]], path: str) -> None:
    L = ["# 语料库（逐段审查用）", ""]
    L.append("来源 %d 个，段落 %d 个；定位符 `p<页> ¶P<段落>`；引用原文必须逐字。" % (len(sources), len(records)))
    L.append("")
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_src.setdefault(r["src"], []).append(r)
    for s in sources:
        L.append("## 来源: %s" % s["path"])
        L.append("- kind=%s n_pages=%s chars=%s error=%s" % (s.get("kind"), s.get("n_pages"),
                                                            s.get("chars"), s.get("error")))
        L.append("")
        for r in by_src.get(s["path"], []):
            L.append("### %s [%s] %s" % (locator(r), r["section"], r["kind"]))
            L.append(r["text"])
            L.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(L))
