"""core_impl.py — stdlib-only helpers for the academic-slides-evidence-deck Skill.

Design goals
------------
* Deterministic and side-effect-free unless an output path is explicitly given.
* Robust reads: BOM / bad encoding / oversize / malformed JSON degrade instead of
  crashing, and every degradation is reported rather than silently repaired.
* Honest status: missing data yields ``not_reported`` / ``insufficient_evidence``,
  never fabricated numbers, DOIs, citations or scores.

No network access, no third-party packages, no prepared answers.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

MAX_MATERIAL_BYTES = 2 * 1024 * 1024  # skip larger material files; record degradation

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error", "failed", "in_progress")
STATUS_RANK = {"ok": 0, "partial": 1, "insufficient_evidence": 2, "input_error": 3,
               "failed": 4, "in_progress": 5}

EVIDENCE_STATUSES = ("verified_quote", "partial_quote", "inferred", "not_reported", "source_conflict")

DEFAULT_CONTRACT = ("presentation", "evidence_map", "render_record")

# Uncertainty code catalog: one canonical description per failure/limitation class.
UNCERTAINTY_CATALOG = {
    "E1_INPUT_MISSING": "必需输入文件或目录缺失",
    "E2_JSON_MALFORMED": "case.json 无法解析",
    "E3_ENCODING_ISSUE": "文本编码异常（已用替换字符降级读取）",
    "E4_OVERSIZE_SKIPPED": "材料文件超过 2MB，已跳过并记录",
    "E5_TEMPLATE_NO_LAYOUT": "模板缺少所需版式，使用了替代版式",
    "E6_PLACEHOLDER_MISSING": "占位符缺失或 idx 未在版式中定义",
    "E7_FONT_MISSING": "目标字体不可用，已记录回退字体",
    "E8_MEDIA_BROKEN": "图表源文件缺失或损坏",
    "E9_EXPORT_FAILED": "PDF/PNG 导出失败，预览缺失",
    "E10_QUOTE_NOT_LOCATED": "证据定位失败，不能确认与原文逐字一致",
    "E11_SOURCE_CONFLICT": "多个来源对同一指标给出不一致值",
    "E12_MANUAL_REVIEW": "只能人工核验（自动检查不可判定）",
    "E13_HEURISTIC_ONLY": "仅启发式检查，非真实渲染判定",
}

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def uncertainty(code: str, page: Any, detail: str,
                confidence: str = "low", auto_checkable: Optional[bool] = None) -> dict:
    """Build one uncertainty entry; auto_checkable defaults to False only for
    the manual-review code, True otherwise (deterministic script checks)."""
    if auto_checkable is None:
        auto_checkable = code != "E12_MANUAL_REVIEW"
    return {
        "code": code,
        "page": page,
        "detail": detail,
        "confidence": confidence,
        "auto_checkable": auto_checkable,
    }


def read_text_robust(path, max_bytes: int = MAX_MATERIAL_BYTES) -> tuple[Optional[str], Optional[str]]:
    """Return ``(text, error)``. Never raises; encoding issues are recorded."""
    p = Path(path)
    try:
        if not p.exists():
            return None, "not_found"
        if p.stat().st_size > max_bytes:
            return None, "oversize"
        data = p.read_bytes()
    except OSError as e:
        return None, "io:%s" % e
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "encoding_replaced"


def read_json_robust(path) -> tuple[Any, Optional[str]]:
    """Return ``(obj, error)``; obj is None when unreadable or malformed."""
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "json:%s:%s" % (e.lineno, e.colno)


def guess_case_id(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    for k in ("case_id", "caseId", "id", "challenge_id"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return None


def extract_contract_fields(obj: Any) -> list:
    """Read ``output_contract`` declared inside case.json, if any."""
    if not isinstance(obj, dict):
        return []
    oc = obj.get("output_contract")
    if isinstance(oc, dict):
        return [k for k in oc.keys()]
    if isinstance(oc, list):
        out = []
        for item in oc:
            if isinstance(item, dict) and "name" in item:
                out.append(item["name"])
        return out
    return []


def contract_fields(obj: Any) -> list:
    fields = extract_contract_fields(obj)
    return list(fields) if fields else list(DEFAULT_CONTRACT)


def new_skeleton(case_id: Optional[str], contract: Sequence[str],
                 status: str = "in_progress") -> dict:
    """Full result skeleton with every contract field present (null/[]),
    so a partial deliverable is always structurally valid."""
    sk = {
        "case_id": case_id,
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": utc_now(),
            "action": "init",
            "detail": "result skeleton written before any analysis",
        }],
        "degradations": [],
        "errors": [],
        "runtime": {"elapsed_seconds": None, "model_requests": None, "pages_reworked": None},
    }
    for key in contract:
        if key == "presentation":
            sk[key] = {
                "pptx_path": None,
                "pdf_path": None,
                "png_paths": [],
                "slide_count": None,
                "page_plan": [],
            }
        elif key == "evidence_map":
            sk[key] = []
        elif key == "render_record":
            sk[key] = {
                "source_files": [],
                "template_file": None,
                "layouts_used": [],
                "fonts": [],
                "exports": [],
                "integrity": {},
                "media": [],
            }
        else:
            sk[key] = None
    return sk


def atomic_write_json(path, obj: Any) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return str(p)


def merge_result(base: dict, patch: dict) -> dict:
    """Shallow-merge patch into base; nested dicts merge, everything else replaces.
    None values are ignored so a later fill never wipes an earlier field."""
    if not isinstance(base, dict):
        base = {}
    for k, v in patch.items():
        if v is None:
            continue
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def append_unique(items: list, new: list) -> list:
    """Append new dicts not already present (idempotent re-runs)."""
    seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in items}
    out = list(items)
    for x in new:
        k = json.dumps(x, ensure_ascii=False, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


# --------------------------------------------------------------------------- #
# PPTX / PDF / PNG inspection (structural, stdlib only)                       #
# --------------------------------------------------------------------------- #

def _attr(attrs: str, name: str) -> Optional[str]:
    m = re.search(name + r'="([^"]*)"', attrs)
    return m.group(1) if m else None


def _placeholders_of(z, names: set, member: str) -> list:
    if member not in names:
        return []
    xml = z.read(member).decode("utf-8", "replace")
    phs = []
    for m in re.finditer(r"<p:ph\b([^>]*)>", xml):
        attrs = m.group(1)
        phs.append({"type": _attr(attrs, "type") or "", "idx": _attr(attrs, "idx") or ""})
    return phs


def _slide_text_chars(z, names: set, member: str) -> int:
    if member not in names:
        return 0
    xml = z.read(member).decode("utf-8", "replace")
    return sum(len(m.group(1)) for m in re.finditer(r"<a:t>(.*?)</a:t>", xml, re.S))


def inspect_pptx(path) -> dict:
    """Open a pptx as a zip and verify master/layout/placeholder inheritance.
    Purely structural; overflow is a heuristic and is labelled as such."""
    out = {
        "exists": False, "is_zip": False, "errors": [],
        "slides": 0, "masters": 0, "layouts": 0, "notes": 0,
        "themes": [], "media": [],
        "slide_layouts": [], "per_slide_text_chars": [], "overflow_heuristic": [],
    }
    p = Path(path)
    if not p.exists():
        out["errors"].append("not_found")
        return out
    out["exists"] = True
    try:
        out["size_bytes"] = p.stat().st_size
    except OSError:
        pass
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            out["is_zip"] = True
            if "[Content_Types].xml" not in names:
                out["errors"].append("missing_content_types")
            if "ppt/presentation.xml" not in names:
                out["errors"].append("missing_presentation_xml")
            slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
            out["slides"] = len(slides)
            out["masters"] = sum(1 for n in names
                                 if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n))
            out["layouts"] = sum(1 for n in names
                                 if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n))
            out["notes"] = sum(1 for n in names
                               if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n))
            out["themes"] = sorted(n for n in names
                                   if re.fullmatch(r"ppt/theme/theme\d+\.xml", n))
            out["media"] = [
                {"name": n, "bytes": z.getinfo(n).file_size}
                for n in sorted(nm for nm in names if nm.startswith("ppt/media/"))
            ]
            layout_phs = {}
            for ln in sorted(n for n in names
                             if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)):
                layout_phs[ln] = _placeholders_of(z, names, ln)
            for sn in slides:
                mnum = re.search(r"slide(\d+)\.xml$", sn)
                num = mnum.group(1) if mnum else "?"
                rels = "ppt/slides/_rels/slide%s.xml.rels" % num
                layout_name = None
                if rels in names:
                    relxml = z.read(rels).decode("utf-8", "replace")
                    for rm in re.finditer(r"<Relationship\b([^>]*)>", relxml):
                        attrs = rm.group(1)
                        typ = _attr(attrs, "Type") or ""
                        tgt = _attr(attrs, "Target") or ""
                        if typ.endswith("/slideLayout"):
                            layout_name = tgt.rsplit("/", 1)[-1]
                slide_phs = _placeholders_of(z, names, sn)
                layout_key = "ppt/slideLayouts/" + (layout_name or "")
                layout_types = sorted({ph.get("type") for ph in layout_phs.get(layout_key, [])})
                layout_idxs = {ph.get("idx") for ph in layout_phs.get(layout_key, []) if ph.get("idx")}
                slide_idxs = {ph.get("idx") for ph in slide_phs if ph.get("idx")}
                if not slide_idxs:
                    inherited = None  # no placeholders -> not applicable
                elif layout_name and slide_idxs:
                    inherited = all(i in layout_idxs for i in slide_idxs)
                else:
                    inherited = False
                slide_num = int(num) if num.isdigit() else num
                out["slide_layouts"].append({
                    "slide": slide_num,
                    "layout": layout_name,
                    "slide_placeholders": slide_phs,
                    "layout_placeholder_types": layout_types,
                    "placeholder_inherited": inherited,
                })
                chars = _slide_text_chars(z, names, sn)
                out["per_slide_text_chars"].append({"slide": slide_num, "chars": chars})
                if chars > 1100:
                    out["overflow_heuristic"].append({
                        "slide": slide_num, "chars": chars,
                        "note": "text volume above heuristic threshold; confirm with rendered PDF/PNG",
                    })
    except Exception as e:
        out["errors"].append("zip:%s" % e)
    return out


def inspect_pdf(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "is_pdf": False, "error": "not_found"}
    try:
        head = p.read_bytes()[:8]
        size = p.stat().st_size
    except OSError as e:
        return {"exists": True, "is_pdf": False, "error": "io:%s" % e}
    return {"exists": True, "is_pdf": head.startswith(PDF_MAGIC), "size_bytes": size}


def inspect_png(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False, "is_png": False, "error": "not_found"}
    try:
        head = p.read_bytes()[:8]
        size = p.stat().st_size
    except OSError as e:
        return {"path": str(p), "exists": True, "is_png": False, "error": "io:%s" % e}
    return {"path": str(p), "exists": True, "is_png": head == PNG_MAGIC, "size_bytes": size}


# --------------------------------------------------------------------------- #
# Evidence tracing                                                            #
# --------------------------------------------------------------------------- #

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _norm_pos_map(text: str) -> tuple[str, list]:
    norm_chars = []
    positions = []
    last_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not last_space:
                norm_chars.append(" ")
                positions.append(i)
                last_space = True
        else:
            norm_chars.append(ch)
            positions.append(i)
            last_space = False
    return "".join(norm_chars), positions


def locate_quote(text: str, quote: str) -> Optional[int]:
    """Return the 1-based line number where the whitespace-normalized quote first
    appears, or None. Exact match only; no approximate/fuzzy matching."""
    q = normalize_ws(quote)
    if not q or not text:
        return None
    norm, pos = _norm_pos_map(text)
    idx = norm.find(q)
    if idx < 0:
        return None
    orig_idx = pos[idx] if idx < len(pos) else (pos[-1] if pos else 0)
    return text.count("\n", 0, orig_idx) + 1


def resolve_locator(loc: str, texts_root: Optional[str], source_files: Sequence[str]) -> Optional[Path]:
    """Resolve 'file#pN', 'file:lineN' or bare 'file' to a local extracted text path."""
    if not loc:
        return None
    base = loc
    for sep in ("#", ":"):
        if sep in loc:
            base = loc.split(sep, 1)[0]
            break
    base_path = Path(base)
    candidates = []
    if texts_root:
        root = Path(texts_root)
        candidates.append(root / base_path.name)
        candidates.append(root / base_path)
    for sf in source_files or []:
        sfp = Path(sf)
        if sfp.name == base_path.name or str(sf).endswith(base) or base in str(sf):
            candidates.append(sfp)
    for c in candidates:
        try:
            if Path(c).exists():
                return Path(c)
        except OSError:
            continue
    return None


def _page_status(statuses: Sequence[str]) -> str:
    if not statuses:
        return "not_reported"
    if "source_conflict" in statuses:
        return "source_conflict"
    if all(s == "verified_quote" for s in statuses):
        return "verified_quote"
    if any(s == "verified_quote" for s in statuses):
        return "partial_quote"
    if all(s == "inferred" for s in statuses):
        return "inferred"
    return "not_reported"


def build_evidence(plan: Any, texts_root: Optional[str],
                   source_files: Sequence[str]) -> tuple[list, list, list]:
    """Turn a page plan into a page-level evidence map. Every ref carries a locator
    and verbatim quote; unlocatable quotes become not_reported + uncertainty, and
    quote-without-locator goes to pending_human instead of being invented."""
    ev, unc, pending = [], [], []
    if isinstance(plan, list):
        pages = plan
    elif isinstance(plan, dict):
        pages = plan.get("pages", [])
    else:
        pages = []
    for pg in pages:
        if not isinstance(pg, dict):
            continue
        page_no = pg.get("index", pg.get("page"))
        claim = pg.get("claim", "")
        refs_in = pg.get("refs", [])
        refs_out, statuses = [], []
        for ref in refs_in:
            if isinstance(ref, str):
                ref = {"locator": ref}
            loc = ref.get("locator", "")
            quote = ref.get("quote", "")
            st = "not_reported"
            located = {"locator": loc, "quote": quote, "line": None, "match": None}
            if loc and quote:
                fpath = resolve_locator(loc, texts_root, source_files)
                if fpath:
                    text, err = read_text_robust(fpath)
                    if err is None and text:
                        ln = locate_quote(text, quote)
                        if ln is not None:
                            st = "verified_quote"
                            located["line"] = ln
                            located["match"] = "exact"
                        else:
                            unc.append(uncertainty(
                                "E10_QUOTE_NOT_LOCATED", page_no,
                                "quote not found in %s" % loc))
                    else:
                        unc.append(uncertainty(
                            "E10_QUOTE_NOT_LOCATED", page_no,
                            "cannot read %s: %s" % (loc, err)))
                else:
                    unc.append(uncertainty(
                        "E10_QUOTE_NOT_LOCATED", page_no,
                        "locator unresolved: %s" % loc))
            elif quote and not loc:
                pending.append({
                    "item": "locate quote for page %s" % page_no,
                    "reason": "verbatim quote present but no locator",
                    "quote": quote[:200],
                })
            elif loc and not quote:
                st = "inferred"  # source pointed to but no verbatim quote supplied
            else:
                st = "inferred" if claim else "not_reported"
            located["evidence_status"] = st
            refs_out.append(located)
            statuses.append(st)
        ev.append({
            "page": page_no,
            "claim": claim,
            "refs": refs_out,
            "evidence_status": _page_status(statuses),
        })
    return ev, unc, pending


def derive_status(result: dict) -> str:
    """Deterministic status suggestion from artifact presence and uncertainty codes.
    Used by ``seal``; it only downgrades, never upgrades an honest status."""
    if result.get("case_id") is None:
        return "input_error"
    if result.get("errors"):
        return "failed"
    presentation = result.get("presentation") or {}
    pptx = presentation.get("pptx_path")
    pdf = presentation.get("pdf_path")
    pngs = presentation.get("png_paths") or []
    codes = {u.get("code") for u in result.get("uncertainty", [])}
    if codes & {"E1_INPUT_MISSING", "E2_JSON_MALFORMED"}:
        return "input_error"
    if not pptx or not Path(pptx).exists():
        if result.get("evidence_map"):
            return "partial"
        if result.get("sources"):
            return "insufficient_evidence"
        return "input_error"
    if not pdf or not pngs:
        return "partial"
    if codes:
        return "partial"
    return "ok"
