#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性辅助函数（仅标准库）。证据驱动写作 Skill 的骨架、契约回显、校验与写盘。

设计原则：
- 本模块不做任何"分析结论"，只做可复现的机械动作；
- 所有事实只来自实际读到的文件，绝不补造；
- 任何异常都降级为可读记录，不抛裸异常打断 Agent 流程；
- 契约键回显与"先落盘后分析"是本模块的硬保证。
"""

from __future__ import annotations

import json
import os
import time

# 单文件大小上限：超过则跳过并记录，避免超大材料拖垮运行。
MAX_FILE_BYTES = 2 * 1024 * 1024

# 默认契约：当 case.json 未显式给出 output_contract 时使用。
# 三个字段对应题目"Skill 运行后的输出要求"三列表头：
#   目标产物 / 事实与来源映射 / 版本与边界。
DEFAULT_CONTRACT = {
    "deliverable": {"type": "object"},
    "sources": {"type": "array"},
    "change_log": {"type": "array"},
}

# 契约键的多种可能写法（取第一个非空 dict）。
CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "result_schema")

# case_id 的多种可能写法。
CASE_ID_KEYS = ("case_id", "caseId", "id")

# 终版 status 允许值；in_progress 仅用于骨架阶段。
FINAL_STATUSES = ("complete", "partial", "insufficient_evidence", "input_error", "failed")


def now_iso():
    """返回本地时区 ISO 时间串。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """读取文本文件，容忍 BOM 与坏编码；返回 (text, note)。note 非空表示降级。"""
    if not path or not os.path.exists(path):
        return "", "missing: %s" % path
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", "stat error %s: %s" % (path, e)
    if size > max_bytes:
        return "", "oversize skipped: %s (%d bytes > %d)" % (path, size, max_bytes)
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        return "", "read error %s: %s" % (path, e)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), ""
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace"), "fallback decode: %s" % path


def load_json_robust(path):
    """读取 JSON，容忍 BOM、坏 JSON 与截断；返回 (obj, error)。error 非空表示降级。"""
    text, note = read_text(path)
    if note:
        return None, note
    if not text.strip():
        return None, "empty JSON: %s" % path
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as e:
        # 容错一：尝试取到最后一个 '}' 处再解析（截断场景）。
        cut = text.rfind("}")
        if cut > 0:
            try:
                return json.loads(text[: cut + 1]), "truncated JSON repaired: %s" % path
            except Exception:
                pass
        return None, "bad JSON %s: %s" % (path, e)


def discover_inputs(input_dir):
    """扫描输入目录，不假设固定文件名。返回 {case_json, task_md, materials, notes}。"""
    out = {"case_json": None, "task_md": None, "materials": [], "notes": []}
    if not input_dir or not os.path.isdir(input_dir):
        out["notes"].append("input dir missing: %s" % input_dir)
        return out
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            low = fn.lower()
            if low == "case.json":
                out["case_json"] = p
            elif low == "task.md":
                out["task_md"] = p
            else:
                out["materials"].append(p)
    return out


def resolve_case_id(case):
    """case_id > caseId > id。"""
    if not isinstance(case, dict):
        return None
    for k in CASE_ID_KEYS:
        v = case.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def extract_contract(case):
    """从 case.json 提取 output_contract；缺失或为空时回退默认三字段。返回 (contract, note)。"""
    note = ""
    if isinstance(case, dict):
        for k in CONTRACT_KEYS:
            if k in case:
                v = case.get(k)
                if isinstance(v, dict) and v:
                    return dict(v), "echoed all keys from case.json['%s']" % k
                note = "contract key '%s' present but empty; using default" % k
                break
        else:
            note = "no output_contract key in case.json; using default"
    else:
        note = "case.json not a dict; using default"
    return dict(DEFAULT_CONTRACT), note


def _init_value(spec):
    """按契约键的 type 提示给初始空值，保证键存在但内容诚实为空。"""
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t == "array":
            return []
        if t == "object":
            return {}
        if t in ("string", "str"):
            return ""
        return None
    if isinstance(spec, str) and spec.lower() in ("array", "list"):
        return []
    return None


def build_skeleton(case, contract, input_dir=None, errors=None):
    """生成 status=in_progress 的结果骨架：回显全部契约键并补齐必需字段。"""
    result = {}
    for key, spec in contract.items():
        result[key] = _init_value(spec)
    # 必需字段强制置入（契约若同名则被这里覆盖，保证受控）。
    result["case_id"] = resolve_case_id(case)
    result["status"] = "in_progress"
    result.setdefault("claims", [])
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("limitations", [])
    result.setdefault("errors", list(errors or []))
    result.setdefault("degradations", [])
    result["runtime"] = {
        "scaffolded_at": now_iso(),
        "status_history": ["in_progress"],
        "input_dir": input_dir,
    }
    return result


def deep_merge(base, patch):
    """递归合并 patch 到 base；base 中已有契约键永不被删除；None 不覆盖已有值。"""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch if patch is not None else base
    out = dict(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def validate_result(result, contract=None):
    """校验终版 result.json；返回问题列表（空 = 通过）。只做结构校验，不做事实判定。"""
    issues = []
    if not isinstance(result, dict):
        return ["result is not a dict"]
    if not result.get("case_id"):
        issues.append("missing case_id")
    st = result.get("status")
    if st not in FINAL_STATUSES:
        issues.append("status must be one of %s, got %r" % (FINAL_STATUSES, st))
    if contract:
        for key in contract:
            if key not in result:
                issues.append("missing contract key: %s" % key)
    if st == "complete":
        dv = result.get("deliverable")
        if dv in (None, {}, "", []):
            issues.append("status=complete but deliverable empty")
        src = result.get("sources")
        if not isinstance(src, list) or not src:
            issues.append("status=complete but sources empty")
    src = result.get("sources")
    if isinstance(src, list):
        for i, s in enumerate(src):
            if not isinstance(s, dict):
                issues.append("sources[%d] not an object" % i)
                continue
            if not s.get("quote") and not s.get("locator"):
                issues.append("sources[%d] missing both quote and locator" % i)
    cl = result.get("change_log")
    if not isinstance(cl, list):
        issues.append("change_log is not a list")
    return issues


def atomic_write_json(path, obj):
    """原子写盘：先写临时文件再 rename，避免半成品。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _cell(s):
    return ("" if s is None else str(s)).replace("|", "\\|").replace("\n", " ")


def render_markdown(result, out_dir):
    """把 result.json 渲染成可读 evidence_report.md（不新增事实）。返回文件路径。"""
    lines = []
    lines.append("# 证据驱动写作复核报告")
    lines.append("")
    lines.append("> 本文由 result.json 自动渲染，不新增任何事实；所有内容均可回到 result.json 与原文复核。")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", ""))
    lines.append("- status: `%s`" % result.get("status", ""))
    lines.append("")

    lines.append("## 1. 目标产物")
    lines.append("")
    dv = result.get("deliverable")
    if isinstance(dv, dict) and dv:
        lines.append("- 标题：%s" % dv.get("title", ""))
        lines.append("- 格式：%s" % dv.get("format", ""))
        files = dv.get("files") or []
        for f in files:
            lines.append("- 文件：`%s`" % f)
        if dv.get("summary"):
            lines.append("")
            lines.append("摘要：%s" % dv.get("summary"))
    else:
        lines.append("（空）")
    lines.append("")

    lines.append("## 2. 论断清单（claims）")
    lines.append("")
    claims = result.get("claims") or []
    if claims:
        for c in claims:
            if not isinstance(c, dict):
                continue
            lines.append("### %s" % c.get("id", "?"))
            lines.append("")
            lines.append("- 论断：%s" % c.get("claim", ""))
            lines.append("- 强度：`%s`；支撑范围：`%s`" % (c.get("strength", ""), c.get("support_scope", "")))
            refs = c.get("evidence_refs") or []
            lines.append("- 证据：%s" % (", ".join(map(str, refs)) if refs else "无"))
            if c.get("verb_before") or c.get("verb_after"):
                lines.append("- 表述调整：`%s` → `%s`" % (c.get("verb_before", ""), c.get("verb_after", "")))
            if c.get("verdict"):
                lines.append("- 判定：`%s`" % c.get("verdict"))
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 3. 证据表（事实与来源映射）")
    lines.append("")
    sources = result.get("sources") or []
    if sources:
        lines.append("| id | kind | locator | quote | strength | scope | verdict |")
        lines.append("|-|-|-|-|-|-|-|")
        for s in sources:
            if not isinstance(s, dict):
                continue
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                _cell(s.get("id")), _cell(s.get("kind")), _cell(s.get("locator")),
                _cell(s.get("quote")), _cell(s.get("strength")), _cell(s.get("support_scope")),
                _cell(s.get("verdict"))))
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 4. 不确定性 / 待人工确认 / 限制")
    lines.append("")
    for head, key in (("不确定性", "uncertainty"), ("待人工确认", "pending_human"), ("限制与缺口", "limitations")):
        lines.append("### %s" % head)
        lines.append("")
        items = result.get(key) or []
        if items:
            for it in items:
                lines.append("- %s" % (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
        else:
            lines.append("（无）")
        lines.append("")

    lines.append("## 5. 修改记录（版本与边界）")
    lines.append("")
    cl = result.get("change_log") or []
    if cl:
        for e in cl:
            if isinstance(e, dict):
                lines.append("- [%s] %s：%s" % (e.get("time", ""), e.get("action", ""), e.get("detail", "")))
            else:
                lines.append("- %s" % e)
    else:
        lines.append("（无）")
    lines.append("")

    path = os.path.join(out_dir, "evidence_report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
