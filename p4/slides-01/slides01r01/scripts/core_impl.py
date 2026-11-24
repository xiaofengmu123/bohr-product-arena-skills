#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 核心库：契约发现、证据溯源校验、纯标准库 PPTX 生成、PDF 页数统计。

设计约束：
- 仅依赖 Python 标准库（json / re / zipfile / xml.etree / pathlib / datetime）。
- 所有函数确定性执行（相同输入 -> 相同输出），便于他人复核与重复运行。
- 本文件被 scripts/main.py 与 scripts/selftest.py 直接 import；自身不执行副作用。
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

VERSION = "1.0.0"
PKG_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# 契约与状态
# --------------------------------------------------------------------------- #
# 当 /input/case.json 未提供 output_contract 时的回退字段（与题目「七字段」一致）
STD_FIELDS = [
    "case_id", "status", "editable_source", "export_artifact",
    "content_map", "compliance_checks", "exceptions",
]
# 无论 output_contract 是什么，都会附加的统一信封字段（题目硬性要求）
ENVELOPE_FIELDS = [
    "sources", "uncertainty", "pending_human", "errors",
    "degradations", "change_log", "runtime",
]
LIST_FIELDS = {
    "content_map", "compliance_checks", "exceptions", "sources",
    "uncertainty", "pending_human", "errors", "degradations", "change_log",
}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}
STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ROLES = {
    "title", "background", "problem", "method", "result", "discussion",
    "limitation", "conclusion", "appendix", "other",
}
EVIDENCE_KINDS = {"figure", "table", "formula", "data", "text", "schematic", "code", "supplement"}
FIDELITY = {"verbatim", "paraphrase", "numeric_inference", "inference"}

# OOXML 命名空间（生成/修改 pptx 时使用）
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|[A-Za-z\u00b5\u03bc\u03a9\u00b0]+)?")
# 仅匹配「带单位的数值」，避免把列表编号（如 1.1）误报为科学数值
_UNIT_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|[A-Za-z\u00b5\u03bc\u03a9\u00b0][A-Za-z\u00b5\u03bc\u03a9\u00b0/]*)?")


def now_iso() -> str:
    """当前时间 ISO8601（带时区、秒精度），用于 change_log / runtime。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _esc(text: str) -> str:
    """转义 XML 文本节点中的特殊字符。"""
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --------------------------------------------------------------------------- #
# 输入输出 IO（容错：BOM / 坏编码 / 坏 JSON / 超限）
# --------------------------------------------------------------------------- #
def read_bytes(path: Path, max_bytes: int = 2 * 1024 * 1024):
    """读取文件字节；失败或超限返回 (None, err)。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if len(raw) > max_bytes:
        return None, f"{Path(path).name} 超过 {max_bytes} 字节上限，跳过"
    return raw, None


def read_json(path: Path):
    """读取 UTF-8(带 BOM) JSON；容错返回 (obj|None, err|None)。"""
    raw, err = read_bytes(path)
    if err:
        return None, err
    if not raw.strip():
        return None, f"{Path(path).name} 为空文件"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, f"{Path(path).name} 非 UTF-8 文本"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"


def read_text(path: Path, max_bytes: int = 2 * 1024 * 1024):
    """读取文本；utf-8-sig 优先，失败退化为 errors='replace'，不抛异常。"""
    raw, err = read_bytes(path, max_bytes)
    if err:
        return None, err
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), None


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def write_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text), encoding="utf-8")
        return True
    except OSError:
        return False


def write_bytes(path: Path, data: bytes) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 契约发现：动态读取 output_contract，缺什么键回填什么值，绝不改名/删未知键
# --------------------------------------------------------------------------- #
def discover_contract(case: dict):
    """从 case.json 中提取 case_id 与 output_contract 键名列表。

    output_contract 支持 dict（key=字段名）与 list（str 或含 name/key/field 的 dict）。
    缺失时回退到 STD_FIELDS，并在返回值中标注降级。
    """
    if not isinstance(case, dict):
        return "unknown-case", list(STD_FIELDS), "case.json 不是对象，已回退到标准七字段"
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    raw = case.get("output_contract")
    keys: list = []
    degraded = None
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    v = item.get(cand)
                    if isinstance(v, str) and v.strip():
                        keys.append(v.strip())
                        break
    if not keys:
        degraded = "output_contract 缺失或为空，已回退到标准七字段"
        keys = list(STD_FIELDS)
    ordered, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    return str(case_id), ordered, degraded


def default_for(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def scaffold_result(case: dict, status: str = "partial") -> dict:
    """生成一版**合法**的 partial result 骨架（所有契约字段已占位）。

    这是「前 3 分钟必须落盘」的确定性实现：任何字段缺失都给 null/[]/{},
    保证即使后续处理失败，result.json 也是可解析、可交接的最小交付。
    """
    case_id, keys, degraded = discover_contract(case)
    if status not in STATUS:
        status = "partial"
    result: dict = {}
    for k in keys:
        if k == "case_id":
            result[k] = case_id
        elif k == "status":
            result[k] = status
        else:
            result[k] = default_for(k)
    # 主产物占位（仅当字段是 dict 时填入，构建后由 finalize 覆盖）
    if isinstance(result.get("editable_source"), dict):
        result["editable_source"] = {
            "path": "presentation.pptx", "format": "pptx", "slides": 0,
            "note": "占位；build-pptx 后更新为真实页数",
        }
    if isinstance(result.get("export_artifact"), dict):
        result["export_artifact"] = {
            "pdf": {"path": "presentation.pdf", "pages": 0},
            "png": [],
            "note": "占位；导出渲染后更新",
        }
    # 附加统一信封（不覆盖契约里已有的同名字段，只补缺失）
    for k in ENVELOPE_FIELDS:
        result.setdefault(k, default_for(k))
    result["runtime"] = {
        "skill": "slides-01", "version": VERSION, "started": now_iso(),
        "phase": "scaffold", "seconds": 0.0, "requests": 0,
    }
    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    result["change_log"].append({
        "t": now_iso(), "phase": "scaffold",
        "note": "写入 partial 骨架，全部契约字段已占位，可安全交接",
    })
    if degraded:
        result.setdefault("degradations", [])
        if isinstance(result["degradations"], list):
            result["degradations"].append(degraded)
    return result


# --------------------------------------------------------------------------- #
# 输入清单：扫描 /input，供 Agent 决定材料分类（不假设固定文件名）
# --------------------------------------------------------------------------- #
def inventory_input(input_root: Path) -> list:
    """返回 /input 下除 case.json 外的文件清单（相对路径 + 字节数）。"""
    items = []
    if not input_root.is_dir():
        return items
    for p in sorted(input_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(input_root).as_posix()
            if rel == "case.json":
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            items.append({"path": rel, "bytes": size, "suffix": p.suffix.lower()})
    return items


# --------------------------------------------------------------------------- #
# 证据溯源校验：每条结论必须带 locator + 逐字 quote；数值保真；备注与页面分离
# --------------------------------------------------------------------------- #
def extract_numbers(text: str) -> list:
    """提取文本中的「数值 + 单位」片段（用于保真比对，启发式）。"""
    return [m.group(0).replace(" ", "") for m in _NUM_RE.finditer(text or "")]


def extract_unit_numbers(text: str) -> list:
    """仅提取带单位的数值（降低列表编号/页码的误报）。"""
    out = []
    for m in _UNIT_NUM_RE.finditer(text or ""):
        s = m.group(0).replace(" ", "")
        # 必须有单位后缀才算科学数值，否则跳过纯数字/小数
        if re.search(r"[A-Za-z\u00b5\u03bc\u03a9\u00b0%]", s):
            out.append(s)
    return out


def validate_draft(draft: dict) -> list:
    """校验页级计划 draft.json，返回 findings 列表（level/code/msg/slide）。

    校验项对应评分维度中的「结果正确性 / 可核验性」：
    1. 每页 claim 非空且 role 合法；
    2. 每页至少 1 条 evidence，且每条都有 source_id + locator + 非空 quote；
    3. 数值保真：claim/bullets 中的数字+单位应逐字出现在某条 quote 中，
       否则必须显式标记 numeric_inference=true（禁止静默改写）；
    4. 讲解备注不得是页面文字的逐字复制（speaker notes 要补充信息而非复读）。
    """
    findings = []
    slides = draft.get("slides") if isinstance(draft, dict) else []
    if not isinstance(slides, list) or not slides:
        findings.append({"level": "error", "code": "no_slides", "slide": None,
                         "msg": "draft.json 缺少 slides 列表，无法构建页级计划"})
        return findings
    for s in slides:
        idx = s.get("index")
        claim = (s.get("claim") or "").strip()
        role = s.get("role") or "other"
        ev = s.get("evidence") or []
        notes = (s.get("speaker_notes") or "").strip()
        bullets = s.get("bullets") or []
        body = " ".join([claim] + [str(b) for b in bullets])
        if not claim:
            findings.append({"level": "error", "code": "claim_empty", "slide": idx,
                             "msg": "页面缺少 claim（一页一个主张）"})
        if role not in ROLES:
            findings.append({"level": "warn", "code": "role_unknown", "slide": idx,
                             "msg": f"role={role!r} 不在允许集合"})
        if not isinstance(ev, list) or not ev:
            findings.append({"level": "error", "code": "no_evidence", "slide": idx,
                             "msg": "页面缺少 evidence 溯源，无法回溯到论文图表/章节"})
        else:
            for e in ev:
                if not e.get("source_id"):
                    findings.append({"level": "warn", "code": "ev_no_source", "slide": idx,
                                     "msg": "evidence 缺少 source_id"})
                if not e.get("locator"):
                    findings.append({"level": "warn", "code": "ev_no_locator", "slide": idx,
                                     "msg": "evidence 缺少 locator（图/表/章节定位）"})
                if not (e.get("quote") or "").strip():
                    findings.append({"level": "error", "code": "ev_no_quote", "slide": idx,
                                     "msg": "evidence 缺少逐字 quote，证据不可复核"})
                if e.get("kind") not in EVIDENCE_KINDS:
                    findings.append({"level": "warn", "code": "ev_kind", "slide": idx,
                                     "msg": f"evidence.kind={e.get('kind')!r} 不在允许集合"})
        # 数值保真（仅对「结果/方法」类页面严格，其余页面宽松）：
        # claim 全文比对；bullets 只比对带单位的数值（跳过列表编号）。
        quotes = " ".join((e.get("quote") or "") for e in ev)
        if role in {"result", "method"} and quotes:
            nums = extract_numbers(claim)
            nums += extract_unit_numbers(" ".join(str(b) for b in bullets))
            for num in nums:
                if num not in quotes and not s.get("numeric_inference"):
                    findings.append({
                        "level": "warn", "code": "numeric_not_verbatim", "slide": idx,
                        "msg": f"数值/单位 {num!r} 未在 quote 中逐字命中，且未标 numeric_inference",
                    })
        if notes and notes in body:
            findings.append({"level": "warn", "code": "notes_duplicate_body", "slide": idx,
                             "msg": "speaker_notes 与页面正文逐字重复，起不到讲解提示作用"})
    return findings


def validate_result(result: dict, contract_keys: list) -> list:
    """校验最终 result.json：契约字段齐备、case_id/status 合法、主产物可追溯。"""
    findings = []
    for k in contract_keys:
        if k not in result:
            findings.append({"level": "error", "code": "missing_contract_key",
                             "msg": f"result.json 缺少契约字段 {k!r}"})
    if not (result.get("case_id") or "").strip() or result.get("case_id") == "unknown-case":
        findings.append({"level": "warn", "code": "case_id_unknown",
                         "msg": "case_id 未知或缺失，需回读 /input/case.json"})
    if result.get("status") not in STATUS:
        findings.append({"level": "error", "code": "status_invalid",
                         "msg": f"status={result.get('status')!r} 不在 {sorted(STATUS)}"})
    return findings


# --------------------------------------------------------------------------- #
# PDF 页数（只读，不做渲染）
# --------------------------------------------------------------------------- #
def pdf_page_count(path: Path) -> int:
    """统计 PDF 页数；不是 PDF 或读取失败返回 -1。

    实现为统计 `/Type /Page`（且不匹配 `/Type /Pages`）的对象个数；
    对多数生成器产出的 PDF 足够稳定，无需外部依赖。
    """
    raw, err = read_bytes(path, max_bytes=64 * 1024 * 1024)
    if err or not raw or not raw.startswith(b"%PDF-"):
        return -1
    n = len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", raw))
    return n if n > 0 else -1


# --------------------------------------------------------------------------- #
# 纯标准库 PPTX 生成（可编辑 OOXML；提供模板时继承其 master/layout/theme）
# --------------------------------------------------------------------------- #
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{CT_NS}">'
    f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    f'<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
    f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
    f'<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    '{overrides}'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PR_NS}">'
    f'<Relationship Id="rId1" Type="{R_NS}/officeDocument" Target="ppt/presentation.xml"/>'
    "</Relationships>"
)

_PRESENTATION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:presentation xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
    '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
    '<p:sldIdLst>{slide_ids}</p:sldIdLst>'
    '<p:sldSz cx="9144000" cy="6858000" type="screen4x3"/>'
    '<p:notesSz cx="6858000" cy="9144000"/>'
    "</p:presentation>"
)

_PRESENTATION_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PR_NS}">'
    f'<Relationship Id="rId1" Type="{R_NS}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    f'<Relationship Id="rId2" Type="{R_NS}/theme" Target="theme/theme1.xml"/>'
    '{slide_rels}'
    "</Relationships>"
)

_SLIDE_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldMaster xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
    '<p:cSld><p:bg><p:bgPr><a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
    '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>'
    "</p:sldMaster>"
)

_SLIDE_MASTER_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PR_NS}">'
    f'<Relationship Id="rId1" Type="{R_NS}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    f'<Relationship Id="rId2" Type="{R_NS}/theme" Target="../theme/theme1.xml"/>'
    "</Relationships>"
)

_SLIDE_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldLayout xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}" type="blank" preserve="1">'
    '<p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
    "</p:sldLayout>"
)

_SLIDE_LAYOUT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PR_NS}">'
    f'<Relationship Id="rId1" Type="{R_NS}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    "</Relationships>"
)

_THEME = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<a:theme xmlns:a="{A_NS}" name="Slides01">'
    '<a:themeElements>'
    '<a:clrScheme name="Slides01">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1F4E79"/></a:dk2>'
    '<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
    '<a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
    '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>'
    '<a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
    '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>'
    '<a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
    '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
    '</a:clrScheme>'
    '<a:fontScheme name="Slides01">'
    '<a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
    '</a:fontScheme>'
    '<a:fmtScheme name="Slides01">'
    '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    '</a:fmtScheme>'
    '</a:themeElements>'
    "</a:theme>"
)


def _slide_xml(title: str, bullets: list, notes_lines: list) -> str:
    paras = "".join(
        f'<a:p><a:pPr marL="228600" indent="-228600"><a:buChar char="&#8226;"/></a:pPr>'
        f'<a:r><a:rPr lang="zh-CN" sz="1800"/><a:t>{_esc(b)}</a:t></a:r></a:p>'
        for b in bullets
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="457200" y="274638"/><a:ext cx="8229600" cy="1143000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="2800" b="1"/><a:t>'
        f'{_esc(title)}</a:t></a:r></a:p></p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="685800" y="1600200"/><a:ext cx="7772400" cy="4800600"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody></p:sp>'
        '</p:spTree></p:cSld>'
        f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
        "</p:sld>"
    )


def _notes_xml(notes_lines: list) -> str:
    paras = "".join(
        f'<a:p><a:r><a:rPr lang="zh-CN" sz="1400"/><a:t>{_esc(line)}</a:t></a:r></a:p>'
        for line in notes_lines
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:notes xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="685800" y="685800"/><a:ext cx="7772400" cy="5943600"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody></p:sp>'
        '</p:spTree></p:cSld></p:notes>'
    )


def _blank_package(slides: list):
    """构造最小可打开的空白 deck 包（dict: partname -> bytes）。"""
    entries = {
        "[Content_Types].xml": _CONTENT_TYPES.replace("{overrides}", "").encode("utf-8"),
        "_rels/.rels": _ROOT_RELS.encode("utf-8"),
        "ppt/presentation.xml": _PRESENTATION.replace("{slide_ids}", "").encode("utf-8"),
        "ppt/_rels/presentation.xml.rels": _PRESENTATION_RELS.replace("{slide_rels}", "").encode("utf-8"),
        "ppt/slideMasters/slideMaster1.xml": _SLIDE_MASTER.encode("utf-8"),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _SLIDE_MASTER_RELS.encode("utf-8"),
        "ppt/slideLayouts/slideLayout1.xml": _SLIDE_LAYOUT.encode("utf-8"),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _SLIDE_LAYOUT_RELS.encode("utf-8"),
        "ppt/theme/theme1.xml": _THEME.encode("utf-8"),
    }
    return _append_slides(entries, slides, first_num=1, layout_target="../slideLayouts/slideLayout1.xml")


def _append_slides(entries: dict, slides: list, first_num: int, layout_target: str) -> dict:
    """向已有 OOXML 包追加 slide/notes，并更新 presentation.xml 与 content-types。"""
    slide_ids, slide_rels, overrides = [], [], []
    for i, s in enumerate(slides, start=first_num):
        title = str(s.get("title") or s.get("claim") or f"Slide {i}")
        bullets = [str(b) for b in (s.get("bullets") or [])]
        notes = str(s.get("speaker_notes") or "").splitlines() or [""]
        rid = f"rId{i + 1}"
        slide_ids.append(f'<p:sldId id="{255 + i}" r:id="{rid}"/>')
        slide_rels.append(
            f'<Relationship Id="{rid}" Type="{R_NS}/slide" Target="slides/slide{i}.xml"/>'
        )
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
        overrides.append(
            f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
        )
        entries[f"ppt/slides/slide{i}.xml"] = _slide_xml(title, bullets, notes).encode("utf-8")
        entries[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PR_NS}">'
            f'<Relationship Id="rId1" Type="{R_NS}/slideLayout" Target="{layout_target}"/>'
            f'<Relationship Id="rId2" Type="{R_NS}/notesSlide" Target="../notesSlides/notesSlide{i}.xml"/>'
            "</Relationships>"
        ).encode("utf-8")
        entries[f"ppt/notesSlides/notesSlide{i}.xml"] = _notes_xml(notes).encode("utf-8")
    # 更新 presentation.xml：插入 sldIdLst
    pres = entries.get("ppt/presentation.xml", b"").decode("utf-8")
    if "{slide_ids}" in pres:
        pres = pres.replace("{slide_ids}", "".join(slide_ids))
    elif "</p:sldIdLst>" in pres:
        pres = pres.replace("</p:sldIdLst>", "".join(slide_ids) + "</p:sldIdLst>")
    else:
        pres = pres.replace("</p:presentation>", "<p:sldIdLst>" + "".join(slide_ids) + "</p:sldIdLst></p:presentation>")
    entries["ppt/presentation.xml"] = pres.encode("utf-8")
    # 更新 presentation rels
    rels = entries.get("ppt/_rels/presentation.xml.rels", b"").decode("utf-8")
    if "{slide_rels}" in rels:
        rels = rels.replace("{slide_rels}", "".join(slide_rels))
    elif "</Relationships>" in rels:
        rels = rels.replace("</Relationships>", "".join(slide_rels) + "</Relationships>")
    entries["ppt/_rels/presentation.xml.rels"] = rels.encode("utf-8")
    # 更新 content-types
    ct = entries.get("[Content_Types].xml", b"").decode("utf-8")
    if "{overrides}" in ct:
        ct = ct.replace("{overrides}", "".join(overrides))
    elif "</Types>" in ct:
        ct = ct.replace("</Types>", "".join(overrides) + "</Types>")
    entries["[Content_Types].xml"] = ct.encode("utf-8")
    return entries


def _template_entries(template_path: Path):
    """读取模板 pptx 的所有部件；不是 zip 返回 (None, err)。"""
    try:
        with zipfile.ZipFile(template_path, "r") as z:
            return {n: z.read(n) for n in z.namelist()}, None
    except (OSError, zipfile.BadZipFile) as exc:
        return None, f"模板无法作为 zip 打开: {exc.__class__.__name__}"


def _pick_layout(entries: dict) -> str:
    """从模板中挑选 slideLayout：优先「标题和内容/Title and Content」，否则第一个。"""
    names = sorted(
        n for n in entries
        if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)
    )
    if not names:
        return "../slideLayouts/slideLayout1.xml"
    for n in names:
        blob = entries[n]
        if b"Title and Content" in blob or "Title and Content".encode() in blob:
            return "../slideLayouts/" + Path(n).name
        if "标题和内容".encode("utf-8") in blob:
            return "../slideLayouts/" + Path(n).name
    return "../slideLayouts/" + Path(names[0]).name


def build_pptx(draft: dict, out_path: Path, template_path=None):
    """根据 draft.json 生成可编辑 pptx。

    返回 (ok: bool, err: str|None, info: dict)。info 含 slides 页数。
    提供模板时：复制模板包并追加 slide，继承其 master/layout/theme（真实继承，
    不是「新建空白 + 贴文本框」）；无模板时：生成最小合法空白 deck。
    """
    slides = draft.get("slides") if isinstance(draft, dict) else []
    if not isinstance(slides, list) or not slides:
        return False, "draft.json 缺少 slides，无法生成", {}
    if template_path and Path(template_path).is_file():
        entries, err = _template_entries(Path(template_path))
        if err:
            return False, err, {}
        layout_target = _pick_layout(entries)
        # 计算起始编号：模板已有 slideN 的编号之后
        existing = [int(m.group(1)) for n in entries
                    if (m := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n))]
        first_num = (max(existing) + 1) if existing else 1
        entries = _append_slides(entries, slides, first_num=first_num, layout_target=layout_target)
    else:
        entries = _blank_package(slides)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return True, None, {"slides": len(slides), "inherited_template": bool(template_path)}
    except OSError as exc:
        return False, f"写入 pptx 失败: {exc.__class__.__name__}", {}


def validate_pptx(path: Path):
    """结构校验：能否作为 zip 打开、是否含 presentation.xml、能否被 ET 解析。"""
    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            if "ppt/presentation.xml" not in names:
                return False, "缺 ppt/presentation.xml"
            ET.fromstring(z.read("ppt/presentation.xml"))
            return True, None
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        return False, f"pptx 校验失败: {exc.__class__.__name__}"
