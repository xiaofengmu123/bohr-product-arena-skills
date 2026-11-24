#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 基金申请书写作 Skill 的核心实现（仅 Python 标准库）。

本模块承担可复现的机械工作：输入发现、容错读取、来源快照(SHA256)、
结果骨架、题目 output_contract 原样回填、事实台账合并、七要素覆盖检查、
"立得住/说得准/对得上/查得到"四闸门检查、result.json 校验。
正文写作与判断由 Agent 完成；本模块只组装、核对、记录，不生成任何事实。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys

# ---------------------------------------------------------------- 常量

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件读取上限 2MB，超限跳过并记录

# 基金申请书七要素（写作覆盖检查的锚）
ELEMENTS = [
    "立项依据",
    "科学问题",
    "研究目标",
    "研究内容",
    "创新点",
    "技术路线",
    "研究基础与可行性",
]

# "对得上"一致性检查涉及的四类条目（研究目标/研究内容/技术路线/考核指标）
CORRESPONDENCE_KEYS = ["研究目标", "研究内容", "技术路线", "考核指标"]

# 不可核验的修饰词（"说得准"闸门）：命中即标记"需核验/替换"，不自动删除，
# 因为这些词可能合法地出现在被引用的原文中。
UNVERIFIABLE_SUPERLATIVES = [
    "首次", "国际领先", "国内领先", "填补空白", "国内外率先",
    "世界首创", "颠覆性", "革命性", "独一无二", "唯一", "最先进",
]

STATUS_VALUES = (
    "in_progress", "done", "partial", "failed",
    "insufficient_evidence", "input_error", "ok",
)

# ---------------------------------------------------------------- 容错读取


def read_bytes_safe(path: str):
    """读取文件字节；超限返回 None 与错误说明，不抛异常。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, ["getsize_failed: %s" % e]
    if size > MAX_FILE_BYTES:
        return None, ["too_large: %d bytes > %d" % (size, MAX_FILE_BYTES)]
    try:
        with open(path, "rb") as f:
            return f.read(), []
    except OSError as e:
        return None, ["read_failed: %s" % e]


def read_text_safe(path: str):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取，返回 (text, errors)。"""
    raw, errs = read_bytes_safe(path)
    if raw is None:
        return None, errs
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), errs
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), errs + ["decoded_with_replace"]


def read_json_safe(path: str):
    """容错读取 JSON；坏 JSON 返回 (None, errors)，不崩溃。"""
    text, errs = read_text_safe(path)
    if text is None:
        return None, errs
    try:
        return json.loads(text), errs
    except json.JSONDecodeError as e:
        return None, errs + ["bad_json: %s" % e]


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 输入发现


def _is_symlink(path: str) -> bool:
    try:
        return os.path.islink(path)
    except OSError:
        return False


def discover_inputs(input_dir: str):
    """扫描输入目录，不假设固定文件名；返回清单与错误。

    返回 (files, errors)。files 为 dict：相对路径 -> {size, sha256, mtime}。
    跳过目录、符号链接与超限文件，超限/读取失败写进 errors 而非丢弃。
    """
    files = {}
    errors = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not _is_symlink(os.path.join(root, d))]
        for name in sorted(names):
            p = os.path.join(root, name)
            if _is_symlink(p):
                errors.append("symlink_skipped: %s" % p)
                continue
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError as e:
                errors.append("getsize_failed %s: %s" % (rel, e))
                continue
            if size > MAX_FILE_BYTES:
                errors.append("too_large: %s (%d bytes)" % (rel, size))
                files[rel] = {"size": size, "sha256": None,
                              "mtime": None, "skipped": "too_large"}
                continue
            h = hashlib.sha256()
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except OSError as e:
                errors.append("hash_failed %s: %s" % (rel, e))
                continue
            mtime = None
            try:
                mtime = _dt.datetime.fromtimestamp(
                    os.path.getmtime(p), _dt.timezone.utc
                ).isoformat(timespec="seconds")
            except OSError:
                pass
            files[rel] = {"size": size, "sha256": h.hexdigest(), "mtime": mtime}
    return files, errors


def snapshot_sources(input_dir: str):
    """生成来源快照：{files, errors, generated_at}。"""
    files, errors = discover_inputs(input_dir)
    return {"generated_at": now_iso(), "files": files, "errors": errors}


# ---------------------------------------------------------------- 契约


def load_case_meta(case_path: str):
    """读取 case.json，返回 (case_id, contract_hint, errors)。

    contract_hint：case.json 中出现的 output_contract / output_schema /
    required_fields / contract，用于原样回填，不改名不删未知键。
    """
    data, errs = read_json_safe(case_path)
    case_id = None
    contract_hint = {}
    if isinstance(data, dict):
        for key in ("id", "case_id", "challenge_id", "armiId"):
            v = data.get(key)
            if isinstance(v, str) and v:
                case_id = v
                break
        for key in ("output_contract", "output_schema", "required_fields",
                    "contract", "outputContract"):
            v = data.get(key)
            if isinstance(v, dict):
                contract_hint.update(v)
            elif isinstance(v, list):
                contract_hint.setdefault("fields", v)
            elif v is not None:
                contract_hint[key] = v
    return case_id, contract_hint, errs


def _default_for_hint(value):
    """为 contract_hint 的键提供合法的空默认值。"""
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    return None


def build_skeleton(case_id: str, contract_hint: dict | None = None):
    """构建最小合法 result.json 骨架（status=in_progress）。

    题目 output_contract 的键原样回填，缺值给 null/[]/{}，不删除未知键。
    """
    contract_hint = contract_hint or {}
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "artifact": {
            "type": "grant_proposal_section",
            "path": None,
            "format": "markdown",
            "editable": True,
        },
        "fact_source_map": [],
        "version_boundary": {
            "modification_diff": "",
            "manual_confirm": [],
            "material_gaps": [],
            "anomalies": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {
                "ts": now_iso(),
                "action": "skeleton",
                "reason": "先落盘可继续编辑的最小合法结果，之后只做增量改进",
                "impact": "result.json 已可被第三方读取",
            }
        ],
    }
    # 原样回填题目契约字段（不覆盖上面已定义的默认键）
    for key, value in contract_hint.items():
        if key not in result:
            result[key] = _default_for_hint(value)
    return result


# ---------------------------------------------------------------- 台账


def load_fact_ledger(work_dir: str):
    """读取 work/fact_source_map.json（数组），返回 (list, errors)。"""
    p = os.path.join(work_dir, "fact_source_map.json")
    if not os.path.isfile(p):
        return [], []
    data, errs = read_json_safe(p)
    if isinstance(data, list):
        return data, errs
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"], errs
    return [], errs + ["fact_source_map not a list"]


# ---------------------------------------------------------------- 四闸门


def check_element_coverage(draft_text: str):
    """七要素覆盖检查：识别 Markdown 标题或要素关键词。"""
    if not draft_text:
        return {e: False for e in ELEMENTS}
    found = {}
    for e in ELEMENTS:
        head = re.compile(
            r"^#{1,6}\s*" + re.escape(e) + r"\b", re.MULTILINE
        )
        found[e] = bool(head.search(draft_text)) or (e in draft_text)
    return found


def check_unverifiable_superlatives(draft_text: str):
    """说得准闸门：找出不可核验修饰词所在句子，返回 [{word, sentence}]。"""
    hits = []
    if not draft_text:
        return hits
    sentences = re.split(r"(?<=[。；;.!?])\s*", draft_text)
    for word in UNVERIFIABLE_SUPERLATIVES:
        for s in sentences:
            if word in s:
                hits.append({"word": word, "sentence": s.strip()[:200]})
    return hits


def check_correspondence(goals, contents, routes, indicators):
    """对得上闸门：比较四类条目数量与命名是否一致。

    输入为四组字符串列表（可含编号）；输出报告 dict。
    """
    def _norm(items):
        return [re.sub(r"^\s*[\d一二三四五六七八九十、.()（）:：-]+\s*", "", s).strip()
                for s in (items or []) if s and s.strip()]

    g, c, r, i = _norm(goals), _norm(contents), _norm(routes), _norm(indicators)
    report = {
        "counts": {"研究目标": len(g), "研究内容": len(c),
                   "技术路线": len(r), "考核指标": len(i)},
        "equal_counts": len({len(g), len(c), len(r)}) == 1,
        "indicators_missing": len(i) == 0,
        "goal_orphans": [x for x in g if x not in c],
        "route_orphans": [x for x in r if x not in c],
        "indicator_orphans": [x for x in i if x not in g],
        "notes": [],
    }
    if not report["equal_counts"]:
        report["notes"].append(
            "目标/内容/路线数量不一致：目标%d 内容%d 路线%d"
            % (len(g), len(c), len(r))
        )
    if report["indicators_missing"]:
        report["notes"].append("考核指标为空，无法判断目标是否可考核")
    if report["goal_orphans"]:
        report["notes"].append("存在没有对应研究内容的研究目标")
    if report["route_orphans"]:
        report["notes"].append("存在没有对应研究内容的技术路线步骤")
    return report


def four_gate_report(draft_text: str, correspondence: dict | None = None):
    """汇总四闸门检查结果，供 result.json 与自检使用。"""
    report = {
        "element_coverage": check_element_coverage(draft_text),
        "unverifiable_superlatives": check_unverifiable_superlatives(draft_text),
        "correspondence": correspondence or {},
    }
    missing = [e for e, ok in report["element_coverage"].items() if not ok]
    report["missing_elements"] = missing
    report["pass_li_de_zhu"] = not missing  # 立得住依赖七要素齐备
    report["pass_shuo_de_zhun"] = not report["unverifiable_superlatives"]
    report["pass_dui_de_shang"] = bool(correspondence) and correspondence.get(
        "equal_counts", False)
    return report


# ---------------------------------------------------------------- 校验


def validate_result(result: dict):
    """校验 result.json 结构；返回 issues 列表（空即通过）。"""
    issues = []
    if not isinstance(result, dict):
        return ["result is not a dict"]
    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in STATUS_VALUES:
        issues.append("invalid status: %r" % result.get("status"))
    for key in ("artifact", "fact_source_map", "version_boundary",
                "sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing key: %s" % key)
    if not isinstance(result.get("fact_source_map"), list):
        issues.append("fact_source_map must be a list")
    if not isinstance(result.get("change_log"), list):
        issues.append("change_log must be a list")
    for i, item in enumerate(result.get("fact_source_map", [])):
        if not isinstance(item, dict):
            issues.append("fact_source_map[%d] not a dict" % i)
        elif not (item.get("claim") and item.get("source_ref")):
            issues.append(
                "fact_source_map[%d] missing claim/source_ref" % i)
    return issues


def finalize_result(result: dict, status: str = None, artifact_path: str = None,
                    sources: list | None = None, notes: str = None):
    """按实更新 status/artifact.path/sources，并追加一条 change_log。"""
    if status is not None:
        result["status"] = status
    if artifact_path is not None:
        result.setdefault("artifact", {})["path"] = artifact_path
    if sources is not None:
        result["sources"] = sources
    result.setdefault("change_log", []).append(
        {"ts": now_iso(), "action": "finalize",
         "reason": notes or "增量更新 result.json", "impact": "status=%s" % result.get("status")}
    )
    return result
