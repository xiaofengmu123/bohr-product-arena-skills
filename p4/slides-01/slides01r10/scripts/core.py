#!/usr/bin/env python3
"""core.py — slides-01 无渲染数据层。

职责：输入发现与契约回填、Deck IR（页级中间表示）模式与校验、
证据/图表交叉校验、运行记账、result.json 组装。纯 Python 标准库。
与 build.py（渲染层）解耦：内容正确性在渲染前单独校验。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"

ROLES = {
    "title", "background", "method", "result",
    "discussion", "limitation", "conclusion", "other",
}
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

# 字段类型提示：决定缺省值为 [] / {} / null
LIST_FIELDS = {
    "content_map", "compliance_checks", "exceptions", "sources", "uncertainty",
    "pending_human", "errors", "degradations", "changes", "change_log",
}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}

# 统一信封：无论题目 output_contract 是否声明，都写入（兼容评分要求）
ENVELOPE = [
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "change_log", "changes", "runtime",
]

# output_contract 缺失时的回退契约（参考本赛道常见七字段）
FALLBACK_CONTRACT = [
    "case_id", "status", "editable_source", "export_artifact",
    "content_map", "compliance_checks", "exceptions",
]

FIGURE_RE = re.compile(
    r"\b(Fig(?:ure)?\.?|Scheme)\s*([A-Za-z0-9][A-Za-z0-9.\-]*)", re.I)
TABLE_RE = re.compile(r"\bTable\s*([A-Za-z0-9][A-Za-z0-9.\-]*)", re.I)
EQ_RE = re.compile(r"\bEq(?:uation)?\.?\s*\(?(\d+)\)?", re.I)
NUM_RE = re.compile(r"\d")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path) -> tuple:
    """返回 (obj|None, err|None)。坏编码/坏 JSON 只报错，不抛异常。"""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, f"{p.name} 读取失败: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{p.name} 为空文件"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, f"{p.name} 非 UTF-8 文本"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{p.name} JSON 解析失败: {exc}"


def write_json(path, obj) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        return True
    except OSError:
        return False


def write_text(path, text) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(text), encoding="utf-8")
        return True
    except OSError:
        return False


def ensure_dir(path) -> tuple:
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def discover_contract_keys(case: dict) -> list:
    """从 case['output_contract'] 提取全部字段名（保持顺序、去重）。"""
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys: list = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    if not keys:
        keys = list(FALLBACK_CONTRACT)
    ordered, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def fill_contract(keys: list, payload: dict) -> tuple:
    """按契约键顺序回填；未知键保留并给缺省值；返回 (filled, missing)。"""
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = default_value(key)
            missing.append(key)
    return filled, missing


def normalize(text) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def dehyphenate(text: str) -> str:
    # 处理 PDF 换行处断词 "hyphen- ated" -> "hyphenated"
    return re.sub(r"-\s+", "", text)


def quote_matches(quote: str, corpus: str) -> bool:
    """逐字命中校验：空语料视为无法机器核验（不判失败）。"""
    if not corpus:
        return False
    q = normalize(quote)
    c = normalize(corpus)
    if not q:
        return False
    return (q in c) or (dehyphenate(q) in dehyphenate(c))


def figure_key(display: str) -> str:
    """把展示名归一为可比较键：'Figure 2'/'Fig. 3b'/'Table 1'/'Eq. (4)'。"""
    s = display.lower().strip()
    s = re.sub(r"\s+", "", s).replace(".", "")
    s = s.replace("(", "").replace(")", "")
    if s.startswith("fig"):
        s = "figure" + s[3:]
    elif s.startswith("eq"):
        s = "equation" + s[2:]
    return s


def extract_figure_mentions(text: str) -> dict:
    """从材料文本抽取图表/公式编号出现次数，供交叉校验。"""
    found: dict = {}
    def add(display, kind):
        key = figure_key(display)
        if key not in found:
            found[key] = {"id": display, "kind": kind, "mentions": 0}
        found[key]["mentions"] += 1

    for m in FIGURE_RE.finditer(text or ""):
        add(f"Figure {m.group(2)}", "figure")
    for m in TABLE_RE.finditer(text or ""):
        add(f"Table {m.group(1)}", "table")
    for m in EQ_RE.finditer(text or ""):
        add(f"Eq. ({m.group(1)})", "equation")
    return found


def ir_stub() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "title": "",
            "audience": "",
            "talk_minutes": 0,
            "slide_cap": 0,
            "template": "",
        },
        "spine": {
            "research_question": "",
            "gap": "",
            "approach_claim": "",
            "key_evidence": [],
            "conclusion": "",
            "limitations": [],
        },
        "figure_index": [],
        "slides": [],
    }


def _issue(level, code, message):
    return {"level": level, "code": code, "message": message}


def validate_ir(ir, materials_text=None) -> tuple:
    """校验 Deck IR：模式 + 证据 + 图表交叉引用 + 逐字引用。

    返回 (ok: bool, issues: list)。level 为 error 者视为不通过。
    """
    issues: list = []
    if not isinstance(ir, dict):
        return False, [_issue("error", "IR_NOT_DICT", "deck_ir 不是 JSON 对象")]

    if ir.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("error", "IR_VERSION",
                             f"schema_version 应为 {SCHEMA_VERSION}，实际 {ir.get('schema_version')!r}"))

    meta = ir.get("meta") or {}
    if not meta.get("title"):
        issues.append(_issue("error", "META_TITLE", "meta.title 缺失"))

    spine = ir.get("spine") or {}
    for field, label in (("research_question", "研究问题"),
                         ("approach_claim", "核心做法"),
                         ("conclusion", "结论")):
        if not spine.get(field):
            issues.append(_issue("error", "SPINE_" + field.upper(), f"spine.{field}（{label}）缺失"))

    # 图表索引：权威表（agent 实际定位并带图注）
    fig_index = ir.get("figure_index") or []
    index_keys = {}
    for entry in fig_index:
        if not isinstance(entry, dict) or not entry.get("id"):
            issues.append(_issue("error", "FIG_INDEX_BAD", "figure_index 存在缺 id 的条目"))
            continue
        index_keys[figure_key(entry["id"])] = entry
        if not entry.get("caption_quote"):
            issues.append(_issue("warning", "FIG_CAPTION", f"{entry['id']} 缺 caption_quote"))
    if not index_keys:
        issues.append(_issue("warning", "FIG_INDEX_EMPTY", "figure_index 为空，图表引用无法交叉校验"))

    # 材料中实际出现的编号（用于防“编造图号”）
    material_keys = extract_figure_mentions(materials_text) if materials_text else {}

    # 图号索引与材料对账：索引里的图号必须在材料中出现过
    for key, entry in index_keys.items():
        if material_keys and key not in material_keys:
            issues.append(_issue("error", "FIG_NOT_IN_SOURCE",
                                 f"figure_index 中的 {entry['id']} 未在材料文本中出现"))

    slides = ir.get("slides")
    if not isinstance(slides, list) or not slides:
        issues.append(_issue("error", "SLIDES_EMPTY", "slides 为空"))
        return not any(i["level"] == "error" for i in issues), issues

    cap = meta.get("slide_cap")
    if isinstance(cap, int) and cap > 0 and len(slides) > cap:
        level = "error" if len(slides) > cap + 2 else "warning"
        issues.append(_issue(level, "SLIDE_CAP", f"页数 {len(slides)} 超过上限 {cap}"))

    for i, s in enumerate(slides):
        tag = f"slides[{i}]"
        if not isinstance(s, dict):
            issues.append(_issue("error", "SLIDE_BAD", f"{tag} 不是对象"))
            continue
        if s.get("index") is None:
            issues.append(_issue("error", "SLIDE_INDEX", f"{tag} 缺 index"))
        if s.get("role") not in ROLES:
            issues.append(_issue("error", "SLIDE_ROLE", f"{tag} role 非法: {s.get('role')!r}"))
        if not s.get("claim"):
            issues.append(_issue("error", "SLIDE_CLAIM", f"{tag} 缺 claim（页级主张）"))
        bullets = s.get("bullets") or []
        if not isinstance(bullets, list):
            issues.append(_issue("error", "SLIDE_BULLETS", f"{tag} bullets 应为数组"))
        elif len(bullets) > 8:
            issues.append(_issue("warning", "SLIDE_DENSITY", f"{tag} 要点过多（{len(bullets)}>8）"))
        else:
            for b in bullets:
                if isinstance(b, str) and len(b) > 300:
                    issues.append(_issue("warning", "BULLET_LONG",
                                         f"{tag} 要点超长（{len(b)} 字符）"))

        notes = s.get("notes") or {}
        if s.get("role") != "title" and not (notes.get("transition") and notes.get("emphasis")):
            issues.append(_issue("warning", "SLIDE_NOTES", f"{tag} 备注缺 transition/emphasis"))

        # 图表引用交叉校验
        for ref_field in ("figure_ref", "table_ref"):
            ref = s.get(ref_field)
            if ref:
                key = figure_key(ref)
                if key not in index_keys:
                    issues.append(_issue("error", "REF_NO_INDEX",
                                         f"{tag}.{ref_field} 引用 {ref} 不在 figure_index"))
                elif material_keys and key not in material_keys:
                    issues.append(_issue("error", "REF_NOT_IN_SOURCE",
                                         f"{tag}.{ref_field} 引用 {ref} 未在材料中出现"))

        # 含数值的主张必须有证据
        claim = str(s.get("claim") or "")
        ev = s.get("evidence") or []
        if NUM_RE.search(claim) and not ev:
            issues.append(_issue("error", "NUM_NO_EVIDENCE",
                                 f"{tag} claim 含数值但无 evidence"))

        if s.get("role") != "title" and not ev:
            issues.append(_issue("warning", "SLIDE_NO_EVIDENCE", f"{tag} 无 evidence"))

        # 逐字引用校验
        for j, e in enumerate(ev or []):
            if not isinstance(e, dict):
                issues.append(_issue("error", "EVIDENCE_BAD", f"{tag}.evidence[{j}] 不是对象"))
                continue
            if not e.get("source_id"):
                issues.append(_issue("error", "EVIDENCE_SOURCE", f"{tag}.evidence[{j}] 缺 source_id"))
            if not e.get("locator"):
                issues.append(_issue("warning", "EVIDENCE_LOCATOR", f"{tag}.evidence[{j}] 缺 locator"))
            if not e.get("quote"):
                issues.append(_issue("error", "EVIDENCE_QUOTE", f"{tag}.evidence[{j}] 缺逐字 quote"))
            elif materials_text:
                if not quote_matches(e["quote"], materials_text):
                    issues.append(_issue("error", "QUOTE_NOT_VERBATIM",
                                         f"{tag}.evidence[{j}] quote 未在材料文本中逐字命中"))
            else:
                issues.append(_issue("warning", "QUOTE_UNVERIFIED",
                                     f"{tag}.evidence[{j}] quote 无法机器核验（无材料文本 dump）"))

    ok = not any(i["level"] == "error" for i in issues)
    return ok, issues


class Runtime:
    """运行记账：步骤、错误、降级、变更。供 result.json 复用。"""

    def __init__(self):
        self.steps: list = []
        self.errors: list = []
        self.degradations: list = []
        self.changes: list = []
        self.started = time.time()

    def step(self, name):
        self.steps.append({"name": name, "at": now_iso()})

    def error(self, item):
        if item not in self.errors:
            self.errors.append(item)

    def degrade(self, item):
        if item not in self.degradations:
            self.degradations.append(item)

    def change(self, item):
        self.changes.append({"at": now_iso(), "note": item})

    def snapshot(self) -> dict:
        return {
            "started_at": datetime.fromtimestamp(
                self.started, tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
            "finished_at": now_iso(),
            "elapsed_seconds": round(time.time() - self.started, 1),
            "steps": list(self.steps),
        }


def build_content_map(ir: dict) -> list:
    """由 IR 生成页级证据映射（content_map）。"""
    rows = []
    for s in (ir or {}).get("slides", []):
        rows.append({
            "index": s.get("index"),
            "role": s.get("role"),
            "claim": s.get("claim"),
            "figure_ref": s.get("figure_ref"),
            "table_ref": s.get("table_ref"),
            "evidence": [
                {"source_id": e.get("source_id"),
                 "locator": e.get("locator"),
                 "quote": e.get("quote")}
                for e in (s.get("evidence") or [])
            ],
        })
    return rows


def assemble_result(case: dict, keys: list, ir: dict, artifacts: dict,
                    checks: list, rt: Runtime, status: str,
                    sources: list = None) -> tuple:
    """组装最终 result.json。返回 (result, missing_keys)。"""
    payload = {
        "case_id": (case or {}).get("case_id") or "unknown-case",
        "status": status if status in STATUSES else "partial",
        "editable_source": artifacts.get("editable_source", {}),
        "export_artifact": artifacts.get("export_artifact", {}),
        "content_map": build_content_map(ir),
        "compliance_checks": checks,
        "exceptions": artifacts.get("exceptions", []),
        "sources": sources or [],
        "uncertainty": artifacts.get("uncertainty", []),
        "pending_human": artifacts.get("pending_human", []),
        "errors": list(rt.errors),
        "degradations": list(rt.degradations),
        "change_log": list(rt.changes),
        "changes": list(rt.changes),
        "runtime": rt.snapshot(),
    }
    filled, missing = fill_contract(keys, payload)
    for key in ENVELOPE:
        if key not in filled:
            filled[key] = payload.get(key, default_value(key))
    filled["case_id"] = payload["case_id"]
    filled["status"] = payload["status"]
    return filled, missing
