#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""academic-talk-organizer：共享实现（仅 Python 标准库）。

本文件承担 Skill 包中可复用的确定性逻辑，与 main.py / selftest.py 配合使用：

  1. 输入发现（case.json / task.md / 材料文件），BOM 与坏编码容错、单文件大小上限、
     坏 JSON 降级不崩溃；
  2. 输出契约作为唯一真源：默认领域契约 + case.json.output_contract 合并，
     未知键原样保留、不改名、不删除；
  3. result.json 的确定性初始化 / 深度合并 / 追加式 change_log；
  4. 校验（字段逐一对齐、类型、status 枚举、单页唯一主张、证据条目完整性）
     与证据检查（locator + 逐字 quote）。

不依赖任何第三方包；所有路径均相对本文件所在目录解析。
"""

from __future__ import annotations

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 常量与默认值
# ---------------------------------------------------------------------------

DEFAULT_INPUT_DIR = "/input"
DEFAULT_WORK_DIR = "/app/work"
DEFAULT_OUTPUT_DIR = "/app/output"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件 2MB 上限，超限跳过并记录

STATUS_ENUM = ("complete", "partial", "failed", "insufficient_evidence", "input_error")

# 领域契约（output_contract 的默认字段）：名称、类型提示、是否必需、默认值、说明。
# 运行时若 case.json 提供 output_contract，则其未知键会原样追加到该契约之后。
DOMAIN_CONTRACT = [
    ("report_goal", "object", True, {}, "报告目标：objective/audience/duration_min/slide_count/template 等可检查目标。"),
    ("audience", "object", True, {}, "听众：prior_knowledge/concerns/likely_questions/terminology_scope。"),
    ("narrative", "object", True, {}, "叙事：story_arc/sequence_rationale/omissions，记录取舍理由而非按论文目录搬运。"),
    ("pages", "array", True, [], "页级结构数组；每页含 page_index/title/single_claim/evidence_strength/figures/speaker_notes/source_refs/locator/quote。"),
    ("editable_deck", "object", True, {}, "可编辑演示文件：path/format/template/layout。"),
    ("evidence_map", "array", True, [], "页级证据映射：page_index/claim/locator/quote/figure_table/confidence。"),
    ("qa_prep", "array", True, [], "问答预演：question/answer/cites_page/category。"),
    ("rendering", "object", True, {}, "渲染与版本记录：source_files/exports/fonts/template/notes。"),
]

# result.json 顶层固定元数据字段（在领域契约之后追加）。
META_CONTRACT = [
    ("status", "enum", True, "partial", "complete|partial|failed|insufficient_evidence|input_error，如实标注。"),
    ("sources", "array", True, [], "实际读取的来源清单：path/type/read。"),
    ("uncertainty", "array", True, [], "无法判断、材料缺失、证据不足等逐条说明。"),
    ("pending_human", "array", True, [], "需人工裁决或补料的项。"),
    ("change_log", "array", True, [], "时间戳+动作+影响范围的增量记录。"),
    ("errors", "array", False, [], "运行错误记录。"),
    ("degradations", "array", False, [], "降级说明（缺图/字体/导出失败/超时等）。"),
    ("runtime", "object", False, {}, "运行信息（工具、耗时等，非评测分数）。"),
]

PAGE_FIELDS = (
    ("page_index", "integer", True),
    ("title", "string", True),
    ("single_claim", "string", True),
    ("evidence_strength", "enum:high|medium|low|n/a", True),
    ("figures", "array", True),
    ("speaker_notes", "string", True),
    ("source_refs", "array", True),
    ("locator", "string", True),
    ("quote", "string", True),
)

EVIDENCE_FIELDS = (
    ("page_index", "integer", True),
    ("claim", "string", True),
    ("locator", "string", True),
    ("quote", "string", True),
    ("figure_table", "string", False),
    ("confidence", "enum:high|medium|low", False),
)

QA_FIELDS = (
    ("question", "string", True),
    ("answer", "string", True),
    ("cites_page", "array", True),
    ("category", "string", False),
)

MATERIAL_EXTENSIONS = (
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
    ".md", ".txt", ".tex", ".csv", ".json", ".pptx", ".odp", ".html",
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def first_nonempty(*values):
    for v in values:
        if v is not None and v != "":
            return v
    return None


def normalize(text: str) -> str:
    """折叠空白，便于逐字片段比对。"""
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())


def empty_value(type_hint: str):
    """按类型提示返回合法的空骨架值。"""
    if type_hint.startswith("enum"):
        return ""
    if type_hint in ("string", "unknown"):
        return ""
    if type_hint == "object":
        return {}
    if type_hint == "array":
        return []
    if type_hint == "boolean":
        return False
    return None  # integer / number 等用 null 占位


def new_page(page_index=1, title="", single_claim="", evidence_strength="low",
             figures=None, speaker_notes="", source_refs=None, locator="", quote=""):
    return {
        "page_index": page_index,
        "title": title,
        "single_claim": single_claim,
        "evidence_strength": evidence_strength,
        "figures": figures if figures is not None else [],
        "speaker_notes": speaker_notes,
        "source_refs": source_refs if source_refs is not None else [],
        "locator": locator,
        "quote": quote,
    }


def new_evidence(page_index=1, claim="", locator="", quote="",
                 figure_table="", confidence="medium"):
    return {
        "page_index": page_index,
        "claim": claim,
        "locator": locator,
        "quote": quote,
        "figure_table": figure_table,
        "confidence": confidence,
    }


def new_qa(question="", answer="", cites_page=None, category=""):
    return {
        "question": question,
        "answer": answer,
        "cites_page": cites_page if cites_page is not None else [],
        "category": category,
    }


# ---------------------------------------------------------------------------
# 输入发现与读取
# ---------------------------------------------------------------------------

def read_bytes_size(path: str):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def read_json_safe(path: str):
    """读取 JSON；返回 (data, error)。缺文件/超限/坏编码/坏 JSON 均降级。"""
    if not os.path.exists(path):
        return None, "missing: %s" % path
    size = read_bytes_size(path)
    if size < 0:
        return None, "unreadable: %s" % path
    if size > MAX_FILE_BYTES:
        return None, "too_large(%dB): %s" % (size, path)
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, "read_error(%s): %s" % (exc, path)
    if raw is None:
        return None, "bad_encoding: %s" % path
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "bad_json(%s): %s" % (exc, path)


def read_text_safe(path: str):
    """读取文本；返回 (text, error)。"""
    if not os.path.exists(path):
        return None, "missing: %s" % path
    size = read_bytes_size(path)
    if size < 0:
        return None, "unreadable: %s" % path
    if size > MAX_FILE_BYTES:
        return None, "too_large(%dB): %s" % (size, path)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, "read_error(%s): %s" % (exc, path)
    return None, "bad_encoding: %s" % path


def discover_inputs(input_dir: str):
    """扫描输入目录，识别 case.json / task.md / 材料文件。"""
    found = {"case_json": None, "task_md": None, "materials": [], "errors": []}
    if not os.path.isdir(input_dir):
        found["errors"].append("input_dir_missing: %s" % input_dir)
        return found
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        found["errors"].append("input_dir_unreadable(%s): %s" % (exc, input_dir))
        return found
    for name in names:
        full = os.path.join(input_dir, name)
        base = name.lower()
        if os.path.isdir(full):
            for sub in sorted(os.listdir(full)):
                sf = os.path.join(full, sub)
                if os.path.isfile(sf):
                    found["materials"].append(sf)
            continue
        if base == "case.json":
            found["case_json"] = full
        elif base == "task.md":
            found["task_md"] = full
        elif base.endswith(MATERIAL_EXTENSIONS):
            found["materials"].append(full)
    return found


# ---------------------------------------------------------------------------
# 输出契约处理
# ---------------------------------------------------------------------------

def build_contract_spec(case_data):
    """以默认领域契约为底，合并 case.json.output_contract 的未知键（原样保留）。"""
    spec = []
    seen = set()
    for name, hint, required, default, desc in DOMAIN_CONTRACT:
        spec.append((name, {"type": hint, "required": required,
                            "default": default, "description": desc}))
        seen.add(name)
    provided = {}
    if isinstance(case_data, dict):
        oc = case_data.get("output_contract")
        if isinstance(oc, dict):
            provided = oc
    for key, value in provided.items():
        if key not in seen:
            spec.append((key, {"type": "unknown", "required": False,
                               "default": value, "description": "user-provided contract field"}))
            seen.add(key)
    return spec, provided


def resolve_case_id(case_data):
    if not isinstance(case_data, dict):
        return ""
    return first_nonempty(case_data.get("id"), case_data.get("armiId"),
                          case_data.get("case_id"), case_data.get("caseId")) or ""


def canonical_top_order(contract_spec):
    order = ["case_id"]
    for name, _spec in contract_spec:
        order.append(name)
    for name, _hint, _required, _default, _desc in META_CONTRACT:
        order.append(name)
    return order


def reorder_dict(data, order):
    """按固定顺序重建 dict，未知键保持原序追加，保证重复运行结果稳定。"""
    if not isinstance(data, dict):
        return data
    out = {}
    for key in order:
        if key in data:
            out[key] = data[key]
    for key, value in data.items():
        if key not in out:
            out[key] = value
    return out


def bootstrap_result(case_data, task_text, contract_spec, provided_contract):
    """生成一版字段齐全、状态为 partial 的合法骨架（前 3 分钟先落盘）。"""
    result = {"case_id": resolve_case_id(case_data)}
    for name, spec in contract_spec:
        if name in provided_contract:
            result[name] = provided_contract[name]
        else:
            result[name] = empty_value(spec["type"])
    for name, hint, _required, _default, _desc in META_CONTRACT:
        result[name] = empty_value(hint)
    # 明确初始状态与来源清单：只记录实际读取到的文件。
    result["status"] = "partial"
    result["sources"] = _initial_sources(case_data)
    result["change_log"] = [{
        "ts": now_iso(),
        "actor": "script",
        "action": "bootstrap",
        "scope": ["*"],
        "note": "落盘 partial 骨架，字段对齐 output_contract，之后仅增量改进。",
    }]
    # 用 task.md 的头部信息作为 narrative 的种子，但不编造科学内容。
    if task_text and isinstance(result.get("narrative"), dict):
        head = " ".join(task_text.split())[:200]
        if head:
            result["narrative"] = {"task_note": head, "story_arc": "", "sequence_rationale": "",
                                   "omissions": []}
    return result


def _initial_sources(case_data):
    sources = []
    if isinstance(case_data, dict):
        for key in ("id", "armiId"):
            if case_data.get(key):
                sources.append({"path": "case.json#%s" % key, "type": "identifier",
                                "read": True})
    return sources


def deep_merge(base, update):
    """确定性深合并：dict 递归合并，list/标量整体替换。"""
    if isinstance(base, dict) and isinstance(update, dict):
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                deep_merge(base[key], value)
            else:
                base[key] = value
        return base
    return update


def append_change(result, actor, action, scope, note=""):
    log = result.get("change_log")
    if not isinstance(log, list):
        result["change_log"] = log = []
    log.append({
        "ts": now_iso(),
        "actor": actor or "agent",
        "action": action or "merge",
        "scope": list(scope) if isinstance(scope, (list, tuple)) else [str(scope)],
        "note": note or "",
    })


def write_result(path, result, contract_spec):
    """规范化键序后写盘；缺目录自动创建。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    result = reorder_dict(result, canonical_top_order(contract_spec))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def dump_contract(path, contract_spec, provided_contract):
    """把契约规格落盘，供后续 merge/validate/evidence-check 重建同一契约。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {
        "spec": [[name, spec] for name, spec in contract_spec],
        "provided": provided_contract if isinstance(provided_contract, dict) else {},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def load_contract_spec_file(path):
    """从 work/contract.json 重建契约规格；缺失时退回默认契约。"""
    data, err = read_json_safe(path)
    spec = []
    provided = {}
    if err is None and isinstance(data, dict):
        for item in data.get("spec") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict):
                spec.append((item[0], item[1]))
        if isinstance(data.get("provided"), dict):
            provided = data["provided"]
    if not spec:
        spec, provided = build_contract_spec({})
    return spec, provided


# ---------------------------------------------------------------------------
# 校验与证据检查
# ---------------------------------------------------------------------------

def validate(result, contract_spec, strict=False):
    """字段对齐与结构校验；errors 阻塞，warnings 不阻塞（strict 例外）。"""
    report = {"ok": True, "errors": [], "warnings": [], "counts": {}}
    if not isinstance(result, dict):
        report["ok"] = False
        report["errors"].append("result is not a JSON object")
        return report

    if not result.get("case_id"):
        report["errors"].append("case_id is missing or empty")

    # 领域契约字段逐一对齐
    aligned = 0
    for name, _spec in contract_spec:
        if name not in result:
            report["errors"].append("contract field missing: %s" % name)
        else:
            aligned += 1
    report["counts"]["contract_fields"] = aligned

    for name in ("status", "sources", "uncertainty", "change_log"):
        if name not in result:
            report["errors"].append("metadata field missing: %s" % name)

    if result.get("status") not in STATUS_ENUM:
        report["errors"].append("invalid status: %r" % result.get("status"))

    # pages：结构 + 单页唯一主张
    pages = result.get("pages")
    if not isinstance(pages, list):
        if result.get("status") == "complete":
            report["errors"].append("pages must be an array when status=complete")
    else:
        report["counts"]["pages"] = len(pages)
        claims = []
        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                report["warnings"].append("page %d is not an object" % idx)
                continue
            for key, _hint, required in PAGE_FIELDS:
                if required and page.get(key) in (None, ""):
                    report["warnings"].append("page %d missing %s" % (idx, key))
            claim = page.get("single_claim")
            if claim:
                claims.append(normalize(claim))
        seen = set()
        for claim in claims:
            if claim in seen:
                report["warnings"].append("duplicate single_claim: %r" % claim[:80])
            seen.add(claim)

    # evidence_map
    em = result.get("evidence_map")
    if isinstance(em, list):
        report["counts"]["evidence_map"] = len(em)
        for entry in em:
            if not isinstance(entry, dict):
                report["errors"].append("evidence_map entry is not an object")
                continue
            if not entry.get("locator"):
                report["errors"].append("evidence_map entry missing locator")
            if not entry.get("quote"):
                report["warnings"].append("evidence_map entry missing verbatim quote")

    # qa_prep 覆盖四类追问（提示性警告，不阻塞）
    qa = result.get("qa_prep")
    if isinstance(qa, list):
        report["counts"]["qa_prep"] = len(qa)
        cats = {e.get("category") for e in qa if isinstance(e, dict)}
        for want in ("mechanism", "data", "control", "limitation"):
            if want not in cats:
                report["warnings"].append("qa_prep lacks category: %s" % want)

    log = result.get("change_log")
    if not isinstance(log, list) or not log:
        report["warnings"].append("change_log is empty")

    if strict:
        report["ok"] = not report["errors"] and not report["warnings"]
    else:
        report["ok"] = not report["errors"]
    return report


def evidence_check(result, work_dir):
    """校验证据可溯源：locator 非空 + 逐字 quote 非空 + 来源文件可定位。

    逐字比对对 PDF 抽取文本做空白折叠后的子串匹配；未命中记为 warning
    （抽取噪声所致），缺失 locator 记为 error。
    """
    report = {"ok": True, "errors": [], "warnings": [], "verified": 0,
              "unverifiable": 0, "mismatched": 0}
    em = result.get("evidence_map") if isinstance(result, dict) else None
    if not isinstance(em, list):
        report["warnings"].append("evidence_map not an array; nothing to check")
        return report

    source_texts = _load_source_texts(result, work_dir)

    for idx, entry in enumerate(em):
        if not isinstance(entry, dict):
            report["errors"].append("evidence_map[%d] not an object" % idx)
            continue
        locator = entry.get("locator", "")
        quote = entry.get("quote", "")
        if not locator:
            report["errors"].append("evidence_map[%d] missing locator" % idx)
        if not quote:
            report["warnings"].append("evidence_map[%d] missing verbatim quote" % idx)
            report["unverifiable"] += 1
            continue
        matched = False
        target = _source_for_locator(locator, source_texts)
        if target is None:
            report["unverifiable"] += 1
            report["warnings"].append(
                "evidence_map[%d] locator has no readable source file: %s" % (idx, locator[:80]))
            continue
        if normalize(quote) in target:
            report["verified"] += 1
            matched = True
        else:
            report["mismatched"] += 1
            report["warnings"].append(
                "evidence_map[%d] quote not found verbatim at %s (may be extraction noise)" % (idx, locator[:80]))
        if matched:
            continue

    # pages 的 source_refs 也要可定位（缺少 ref 文件记为 warning）
    for pidx, page in enumerate(result.get("pages") or []):
        if not isinstance(page, dict):
            continue
        refs = page.get("source_refs") or []
        if not refs:
            report["warnings"].append("page %s has no source_refs" % pidx)

    report["ok"] = not report["errors"]
    return report


def _load_source_texts(result, work_dir):
    """把 sources 清单里的可读文本加载到 {path: normalized_text}。"""
    texts = {}
    sources = result.get("sources") if isinstance(result, dict) else []
    if not isinstance(sources, list):
        return texts
    for src in sources:
        if not isinstance(src, dict):
            continue
        path = src.get("path")
        if not path:
            continue
        full = path if os.path.isabs(path) else os.path.join(work_dir, path)
        if not full.lower().endswith((".md", ".txt", ".json", ".csv", ".tex", ".html")):
            continue  # PDF/图片不做逐字匹配，避免误导
        if not os.path.isfile(full):
            continue
        text, err = read_text_safe(full)
        if err is None and text is not None:
            texts[path] = normalize(text)
    return texts


def _source_for_locator(locator, source_texts):
    """从 locator 抽取来源路径，返回可读文本或 None。locator 形如 materials/x.pdf#p3。"""
    head = (locator or "").split("#")[0].strip()
    if not head:
        return None
    if head in source_texts:
        return source_texts[head]
    # 尝试仅按文件名匹配
    base = os.path.basename(head)
    for key, text in source_texts.items():
        if os.path.basename(key) == base or base in key:
            return text
    return None


# ---------------------------------------------------------------------------
# 自检（无需外部文件，证明脚本可独立运行）
# ---------------------------------------------------------------------------

def selftest():
    """内存端到端自检：契约对齐、骨架、合并、校验、证据检查。返回 (ok, report)。"""
    case = {
        "id": "selftest-case-000",
        "armiId": "ARMI:self.test",
        "output_contract": {"custom_metric": "kept-as-is"},
    }
    task_text = "self-test task: organize material into editable slides."
    spec, provided = build_contract_spec(case)
    result = bootstrap_result(case, task_text, spec, provided)
    result["pages"] = [new_page(1, "Title", "One claim", "high",
                                figures=["fig1.png"], speaker_notes="note",
                                source_refs=["materials/s.txt#p1"], locator="materials/s.txt#p1",
                                quote="one verbatim sentence")]
    result["evidence_map"] = [new_evidence(1, "One claim", "materials/s.txt#p1",
                                           "one verbatim sentence", "Fig 1", "high")]
    result["qa_prep"] = [
        new_qa("mechanism q", "answer", [1], "mechanism"),
        new_qa("data q", "answer", [1], "data"),
        new_qa("control q", "answer", [1], "control"),
        new_qa("limitation q", "answer", [1], "limitation"),
    ]
    append_change(result, "agent", "merge", ["pages", "evidence_map", "qa_prep"], "selftest fill")
    vrep = validate(result, spec, strict=False)
    erep = evidence_check(result, DEFAULT_WORK_DIR)  # 无真实来源文件 → unverifiable 而非 error
    ok = vrep["ok"] and erep["ok"] and result.get("status") == "partial"
    return ok, {"validate": vrep, "evidence": erep,
                "contract_fields": vrep["counts"].get("contract_fields"),
                "pages": vrep["counts"].get("pages")}
