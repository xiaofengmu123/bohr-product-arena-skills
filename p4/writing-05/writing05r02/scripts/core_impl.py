#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — evidence-driven writing Skill 的确定性支撑库（纯标准库）。

职责：
  1. 输入发现与鲁棒读取（BOM / 坏编码 / 坏 JSON / 大小上限 / 缺文件）
  2. 契约回显（case_id / output_contract 键）
  3. PDF 文本抽取（pdftotext -layout，失败降级）
  4. 论断切分启发式（句级 -> 实质论断候选）
  5. locator 构建与校验
  6. result.json 骨架 / 合并 / 校验 / Markdown 渲染

所有函数不产出任何科学事实；事实与证据匹配由调用方（Agent）按 SKILL.md 完成。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
MAX_FILE_BYTES = 2 * 1024 * 1024          # 单文件大小上限 2MB
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MATERIAL_EXTS = {".pdf", ".txt", ".md", ".markdown", ".html", ".docx", ".json", ".csv", ".tex"}

# 允许的状态值（结果文件主状态）
STATUSES = {"complete", "partial", "in_progress", "failed", "insufficient_evidence", "input_error"}

# 证据支持判定（证据对论断的支持方向）
SUPPORT_VERDICTS = {"supports", "partial_supports", "contradicts", "insufficient", "not_found"}

# 证据强度分级（详见 references/01_claim_evidence_protocol.md）
STRENGTH_LEVELS = {"S1", "S2", "S3", "S4", "UNK"}

# 不确定性标记（写得准）
UNCERTAINTY_TAGS = {
    "none",                     # 无特定不确定性
    "correlation_as_causal",    # 相关性可能被写成因果
    "small_sample",             # 小样本 / 单中心 / 代表性不足
    "conflict",                 # 证据相互冲突
    "indirect",                 # 二手转引 / 间接证据
    "not_reported",             # 材料未报告该信息
    "version_uncertain",        # 预印本 / 版本差异
    "retracted_risk",           # 撤稿 / 修正风险
}

# locator 统一正则：S{sec}.P{para}.s{sent} / P{para}.s{sent} / P{para} / page:{n} / char[a:b]
_LOC_RE = re.compile(
    r"^(?:S\d+\.P\d+\.s\d+|P\d+\.s\d+|P\d+|page:\d+|char\[\d+:\d+\])$"
)


# --------------------------------------------------------------------------
# 读取与发现
# --------------------------------------------------------------------------
def read_bytes(path):
    """读原始字节；超限返回 (None, 错误)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat_error: %s" % e
    if size > MAX_FILE_BYTES:
        return None, "too_large(%d>%d)" % (size, MAX_FILE_BYTES)
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except OSError as e:
        return None, "read_error: %s" % e


def read_text_robust(path):
    """按编码表依次尝试解码文本；全部失败返回 (None, 错误列表)。"""
    raw, err = read_bytes(path)
    if err:
        return None, [err]
    errors = []
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), []
        except (UnicodeDecodeError, LookupError) as e:
            errors.append("%s: %s" % (enc, e))
    # 最后兜底：errors=replace
    return raw.decode("utf-8", errors="replace"), errors


def read_json_robust(path):
    """读 JSON：先读文本再解析；失败返回 (None, 错误)。"""
    text, errs = read_text_robust(path)
    if text is None:
        return None, errs
    try:
        return json.loads(text), []
    except (ValueError, TypeError) as e:
        # 去除 BOM 后重试一次
        if text.startswith("\ufeff"):
            try:
                return json.loads(text.lstrip("\ufeff")), []
            except (ValueError, TypeError) as e2:
                return None, ["json_error: %s" % e2]
        return None, ["json_error: %s" % e]


def discover_inputs(input_dir):
    """扫描输入目录，返回 {kind: [paths]} 与错误记录。

    kind: case / task / material / other。不假设固定文件名，按扩展名与文件名特征识别。
    """
    input_dir = os.path.abspath(input_dir)
    found = {"case": [], "task": [], "material": [], "other": []}
    errors = []
    if not os.path.isdir(input_dir):
        return found, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            low = fn.lower()
            base = os.path.basename(p)
            ext = os.path.splitext(low)[1]
            if base == "case.json":
                found["case"].append(p)
            elif base in ("task.md", "task.txt", "readme.md"):
                found["task"].append(p)
            elif ext in MATERIAL_EXTS and base != "case.json":
                found["material"].append(p)
            else:
                found["other"].append(p)
    return found, errors


def extract_case_id(case):
    """case_id 取值优先级：case_id > caseId > id > challenge_id。"""
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def extract_output_contract(case):
    """回显题目 output_contract；缺失时按默认契约补齐。

    扫描键：output_contract / outputContract / contract / result_schema。
    默认契约（题面三张表 + 运行契约）：deliverable / sources / change_log。
    """
    if isinstance(case, dict):
        for k in ("output_contract", "outputContract", "contract", "result_schema", "resultSchema"):
            v = case.get(k)
            if isinstance(v, dict):
                return v
            if isinstance(v, list):
                return {"_fields": v}
    return {"deliverable": "目标产物", "sources": "事实与来源映射", "change_log": "版本与边界"}


def default_skeleton(case_id, contract):
    """生成 result.json 骨架（status=in_progress，claims/sources 为空，含 change_log）。"""
    return {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": {
            "draft": None,           # 成稿（可编辑）相对/绝对路径
            "claim_list": None,      # 论断清单 md
            "evidence_table": None,  # 证据表 md
            "summary": "",
        },
        "claims": [],                # 论断清单：{claim_id, text, kind, strength, uncertainty}
        "sources": [],               # 证据表：{claim_id, source_doc, locator, quote, support, reason}
        "uncertainty": [],           # 不确定项
        "pending_human": [],         # 待人工确认项
        "change_log": [
            {
                "time": now_iso(),
                "action": "scaffold",
                "detail": "初始骨架（早落盘）；后续阶段增量改进",
            }
        ],
        "limitations": [],           # 材料缺口 / 软件缺失 / 截断 / 异常
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": now_iso(),
            "finished_at": None,
            "contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else [],
        },
    }


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_result(path):
    """读现有 result.json；不存在返回 (None, None)。"""
    if not os.path.exists(path):
        return None, None
    obj, err = read_json_robust(path)
    return obj, err


# --------------------------------------------------------------------------
# PDF 文本抽取
# --------------------------------------------------------------------------
def extract_pdf(pdf_path, out_txt):
    """pdftotext -layout 抽取；缺软件/失败返回 (ok, message)。"""
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out_txt],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
    except FileNotFoundError:
        return False, "pdftotext_not_found"
    except subprocess.TimeoutExpired:
        return False, "pdftotext_timeout"
    if r.returncode != 0:
        return False, "pdftotext_rc=%d: %s" % (r.returncode, (r.stderr or "").strip()[:200])
    if not (os.path.exists(out_txt) and os.path.getsize(out_txt) > 0):
        return False, "pdftotext_empty_output"
    return True, "ok"


# --------------------------------------------------------------------------
# 论断切分启发式（确定性起点；Agent 随后精化/合并/分级）
# --------------------------------------------------------------------------
# 句切分：全角句读直接切；半角 .!? 仅在"后接空白"时切，避免把小数 0.72 / P<0.001 切开。
_SENT_SPLIT = re.compile(r"(?<=[。！？;；])\s*|(?<=[.!?])\s+|\n+")

_STRENGTH_HINTS = (
    "显著", "相关", "导致", "表明", "证实", "提示", "关联", "效应", "风险", "降低", "升高",
    "增加", "减少", "优于", "差异", "预测", "影响", "机制", "odds", "risk", "hazard",
)
_CITATION_HINT = re.compile(r"\[\s*\d+(?:\s*[-,]\s*\d+)*\s*\]|\(\s*(?:19|20)\d{2}\s*[a-z]?\s*\)")
_NUMBER_HINT = re.compile(r"\d")


def _is_substantive(sent):
    s = sent.strip()
    if len(s) < 6:
        return False
    if _CITATION_HINT.search(s):
        return True
    if _NUMBER_HINT.search(s):
        return True
    if any(w in s.lower() for w in _STRENGTH_HINTS):
        return True
    return False


def split_claims(text, base_doc=None):
    """把文本切成段落->句子，筛选实质论断候选。返回 claims 候选列表（无强度/证据，待 Agent 精化）。"""
    text = text or ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    claims = []
    cid = 0
    for pi, para in enumerate(paras, 1):
        sents = [s.strip() for s in _SENT_SPLIT.split(para) if s.strip()]
        for si, sent in enumerate(sents, 1):
            if _is_substantive(sent):
                cid += 1
                claims.append({
                    "claim_id": "C%d" % cid,
                    "text": sent,
                    "kind": "claim",
                    "strength": "UNK",
                    "uncertainty": "not_reported",
                    "source_doc": base_doc or "",
                    "locator": "P%d.s%d" % (pi, si),
                    "quote": sent,
                })
    return claims


# --------------------------------------------------------------------------
# locator 构建与校验
# --------------------------------------------------------------------------
def make_locator(section=None, para=None, sent=None, page=None, char_range=None):
    if section is not None and para is not None and sent is not None:
        return "S%d.P%d.s%d" % (section, para, sent)
    if para is not None and sent is not None:
        return "P%d.s%d" % (para, sent)
    if para is not None:
        return "P%d" % para
    if page is not None:
        return "page:%d" % page
    if char_range:
        return "char[%d:%d]" % (char_range[0], char_range[1])
    return "P1"


def is_valid_locator(loc):
    return bool(_LOC_RE.match(str(loc or "")))


# --------------------------------------------------------------------------
# 结果组装 / 校验
# --------------------------------------------------------------------------
def merge_into(base, claims=None, sources=None, meta=None):
    """把 Agent 中间产物合并进 result.json（增量、可重复）。"""
    if claims is not None:
        base["claims"] = list(claims)
    if sources is not None:
        base["sources"] = list(sources)
    if isinstance(meta, dict):
        for k in ("uncertainty", "pending_human", "limitations"):
            if k in meta and meta[k] is not None:
                base[k] = list(meta[k])
        if "summary" in meta and meta.get("summary"):
            base["deliverable"]["summary"] = meta["summary"]
        if "draft" in meta and meta.get("draft"):
            base["deliverable"]["draft"] = meta["draft"]
        if "claim_list" in meta and meta.get("claim_list"):
            base["deliverable"]["claim_list"] = meta["claim_list"]
        if "evidence_table" in meta and meta.get("evidence_table"):
            base["deliverable"]["evidence_table"] = meta["evidence_table"]
        if "status" in meta and meta.get("status"):
            base["status"] = meta["status"]
    return base


def validate_result(result):
    """返回 (ok, 问题列表)。校验字段完整、quote 非空、locator 合法、引用自洽。"""
    problems = []
    if not isinstance(result, dict):
        return False, ["result_not_object"]
    for k in ("case_id", "status", "deliverable", "sources", "change_log"):
        if k not in result:
            problems.append("missing_key:%s" % k)
    if result.get("status") not in STATUSES:
        problems.append("bad_status:%s" % result.get("status"))
    for i, s in enumerate(result.get("sources", [])):
        if not isinstance(s, dict):
            problems.append("sources[%d]_not_object" % i)
            continue
        if not str(s.get("quote", "")).strip():
            problems.append("sources[%d]_empty_quote" % i)
        loc = s.get("locator", "")
        if not is_valid_locator(loc):
            problems.append("sources[%d]_bad_locator:%s" % (i, loc))
        if s.get("support") not in SUPPORT_VERDICTS:
            problems.append("sources[%d]_bad_support:%s" % (i, s.get("support")))
    # 每个 source 必须能指回 claims 中存在的 claim_id
    claim_ids = {c.get("claim_id") for c in result.get("claims", [])}
    for i, s in enumerate(result.get("sources", [])):
        cid = s.get("claim_id")
        if cid and cid not in claim_ids:
            problems.append("sources[%d]_orphan_claim:%s" % (i, cid))
    for i, c in enumerate(result.get("claims", [])):
        if c.get("strength") not in STRENGTH_LEVELS:
            problems.append("claims[%d]_bad_strength:%s" % (i, c.get("strength")))
    return (len(problems) == 0), problems


# --------------------------------------------------------------------------
# Markdown 渲染（论断清单 + 证据表）
# --------------------------------------------------------------------------
def render_markdown(result, out_dir):
    """渲染 claim_list.md 与 evidence_table.md；不新增事实。"""
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    claims = result.get("claims", [])
    lines = ["# 论断清单（claim list）", ""]
    for c in claims:
        lines.append("## %s" % c.get("claim_id", "?"))
        lines.append("")
        lines.append("- 论断：%s" % c.get("text", ""))
        lines.append("- 类型：`%s`　强度：`%s`　不确定性：`%s`" % (
            c.get("kind", ""), c.get("strength", ""), c.get("uncertainty", "")))
        lines.append("")
    p1 = os.path.join(out_dir, "claim_list.md")
    with open(p1, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    paths["claim_list"] = p1

    srcs = result.get("sources", [])
    lines = ["# 证据表（evidence table）", ""]
    lines.append("| 论断 | 来源 | 定位 | 原文摘录 | 支持判定 | 理由 |")
    lines.append("|---|---|---|---|---|---|")
    for s in srcs:
        def esc(x):
            return str(x or "").replace("|", "\\|").replace("\n", " ")
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            esc(s.get("claim_id")), esc(s.get("source_doc")), esc(s.get("locator")),
            esc(s.get("quote")), esc(s.get("support")), esc(s.get("reason"))))
    lines.append("")
    p2 = os.path.join(out_dir, "evidence_table.md")
    with open(p2, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    paths["evidence_table"] = p2
    return paths
