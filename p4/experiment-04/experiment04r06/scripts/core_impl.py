# -*- coding: utf-8 -*-
"""core_impl.py — 实验方案比较与选择 Skill 的核心实现。

仅使用 Python 标准库。三层结构化中间表示（IR），每层都可独立复核：

  case_pack.json        事实层：约束、候选方案、材料索引、output_contract、输入指纹
  plans_model.json      方案层：七类字段 + 五维评分 + 证据注册表 + 成本/时间明细
  comparison_model.json 比较层：比较框架、权重、硬阈值规则、选择、确认项、敏感性配置

由 ``assemble`` 汇总为 ``result.json`` 及 ``comparison_matrix.{csv,svg,pdf}``、
``plan_comparison.md`` 等可编辑产物。中间产物同时落入 /app/output，作为可复核的
审查工件。

确定性：输入指纹、排序、评分、敏感性扰动与矩阵布局全部确定，无随机成分。
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------- #
# 常量与约定
# --------------------------------------------------------------------------- #

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
CRITERIA = ("goal_fit", "risk", "cost", "feasibility", "evidence")
SEVEN_FIELDS = (
    "assumptions",
    "variables",
    "controls",
    "steps",
    "parameters",
    "resources",
    "interpretation_criteria",
)
COST_KINDS = (
    "machine_time",
    "consumable",
    "labor",
    "prep",
    "booking",
    "rework",
    "other",
)
MAX_FILE_BYTES = 2 * 1024 * 1024
SCHEMA_VERSION = "1.1"
DEFAULT_CONTRACT_KEYS = ("experimental_plan", "evidence_and_risks", "confirmation_items")

DEFAULT_WEIGHTS = {
    "goal_fit": 0.25,
    "feasibility": 0.25,
    "evidence": 0.20,
    "cost": 0.15,
    "risk": 0.15,
}


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def write_json(path, obj):
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def read_json_file(path):
    """读 JSON；返回 (obj 或 None, errors 列表)。"""
    text, note = read_text_file(path)
    if text is None:
        return None, ["file_unreadable: %s" % note.get("reason", "unknown")]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        return None, ["bad_json(%s): %s" % (os.path.basename(path), exc)]
    except ValueError as exc:
        return None, ["bad_json(%s): %s" % (os.path.basename(path), exc)]


def read_text_file(path):
    """UTF-8 优先，容错 BOM/GBK/替换编码。返回 (text 或 None, note)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, {"reason": "stat_failed: %s" % exc}
    if size > MAX_FILE_BYTES:
        return None, {"reason": "over_2MB", "size": size}
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), {}
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, {"reason": "read_failed: %s" % exc}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), {"encoding": "replaced"}
    except OSError as exc:
        return None, {"reason": "read_failed: %s" % exc}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def input_fingerprint(files):
    """files: [(abs_path, rel_path)]。对 (相对路径, 内容哈希) 排序后整体哈希。"""
    h = hashlib.sha256()
    parts = []
    for abs_path, rel_path in files:
        rel = rel_path.replace("\\", "/")
        parts.append((rel, sha256_file(abs_path)))
    parts.sort(key=lambda x: x[0])
    for rel, digest in parts:
        h.update(rel.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(digest.encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest()


def discover_files(input_dir):
    if not os.path.isdir(input_dir):
        return []
    files = []
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__"))
        for name in sorted(names):
            abs_path = os.path.join(root, name)
            rel_path = os.path.relpath(abs_path, input_dir)
            files.append((abs_path, rel_path))
    return files


def find_case_json(files):
    exact = None
    partial = None
    any_json = None
    for abs_path, rel_path in files:
        base = os.path.basename(abs_path).lower()
        if base == "case.json":
            exact = (abs_path, rel_path)
            break
        if base.endswith(".case.json") and partial is None:
            partial = (abs_path, rel_path)
        if base.endswith(".json") and any_json is None and "case" in base:
            any_json = (abs_path, rel_path)
    if exact:
        return exact
    if partial:
        return partial
    if any_json:
        return any_json
    for abs_path, rel_path in files:
        if os.path.basename(abs_path).lower().endswith(".json"):
            return (abs_path, rel_path)
    return None


def find_task_md(files):
    for abs_path, rel_path in files:
        if os.path.basename(abs_path).lower() == "task.md":
            return (abs_path, rel_path)
    for abs_path, rel_path in files:
        if os.path.basename(abs_path).lower().endswith(".md"):
            return (abs_path, rel_path)
    return None


def classify_material(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "pdf":
        return "pdf"
    if ext in ("md", "markdown", "txt", "rst"):
        return "text"
    if ext == "csv":
        return "csv"
    if ext in ("json", "yaml", "yml", "toml"):
        return "data"
    if ext in ("xlsx", "xls"):
        return "spreadsheet"
    if ext in ("docx", "doc"):
        return "document"
    if ext in ("png", "jpg", "jpeg", "tif", "tiff", "svg"):
        return "image"
    return "other"


def _extract_str_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "id", "case"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, int):
            return str(val)
    return None


def _looks_like_plan(node):
    if not isinstance(node, dict):
        return False
    name_like = ("name", "plan_id", "planId", "title", "id", "方案", "名称")
    body_like = (
        "steps", "parameters", "variables", "controls", "assumptions",
        "resources", "cost", "feasibility", "risks", "dimension_ratings",
    )
    has_name = any(k in node for k in name_like)
    has_body = any(k in node for k in body_like)
    return has_name and has_body


_CANDIDATE_KEYS = (
    "candidates", "plans", "options", "alternatives", "schemes",
    "solutions", "candidate_plans", "候选", "方案",
)


def discover_candidates(obj):
    """递归发现候选方案数组；找不到键名时按形状启发式回退。"""
    found = []
    seen = set()

    def _add(node):
        marker = id(node)
        if marker in seen or not isinstance(node, dict):
            return
        seen.add(marker)
        found.append(node)

    def _walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key in _CANDIDATE_KEYS or str(key).lower() in _CANDIDATE_KEYS:
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict):
                                _add(item)
                    elif isinstance(val, dict):
                        _add(val)
                _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    if not found:
        def _walk_shape(node):
            if isinstance(node, dict):
                if _looks_like_plan(node):
                    _add(node)
                for val in node.values():
                    _walk_shape(val)
            elif isinstance(node, list):
                for item in node:
                    _walk_shape(item)
        _walk_shape(obj)
    return found


def assign_plan_ids(candidates_raw):
    return [
        dict(raw, plan_id=raw.get("plan_id") or raw.get("id") or ("P%d" % (i + 1)))
        for i, raw in enumerate(candidates_raw)
    ]


# --------------------------------------------------------------------------- #
# 数值计算：成本 / 时间 / 权重 / 阈值 / 敏感性
# --------------------------------------------------------------------------- #

def sum_cost_lines(lines):
    total = 0.0
    usable = False
    for line in lines or []:
        amount = line.get("amount")
        if amount is None:
            unit = line.get("unit_cost")
            qty = line.get("quantity")
            if unit is not None and qty is not None:
                try:
                    amount = float(unit) * float(qty)
                except (TypeError, ValueError):
                    amount = 0.0
            else:
                amount = 0.0
        try:
            total += float(amount)
            usable = True
        except (TypeError, ValueError):
            continue
    return total if usable else None


def compute_plan_cost(plan):
    cost = plan.get("cost") or {}
    method = cost.get("method", "computed")
    currency = cost.get("currency", "CNY")
    lines = cost.get("lines") or []
    computed = sum_cost_lines(lines)
    declared = cost.get("total")
    if method == "not_computable":
        return {
            "method": "not_computable", "total": None, "currency": currency,
            "lines": lines, "computed_total": computed,
        }
    if method == "override" and declared is not None:
        return {
            "method": "override", "total": declared, "currency": currency,
            "lines": lines, "computed_total": computed,
        }
    if declared is not None:
        return {
            "method": method, "total": declared, "currency": currency,
            "lines": lines, "computed_total": computed,
        }
    if computed is None:
        return {
            "method": "not_computable", "total": None, "currency": currency,
            "lines": lines, "computed_total": None,
        }
    return {
        "method": method, "total": computed, "currency": currency,
        "lines": lines, "computed_total": computed,
    }


def compute_plan_time(plan):
    time_info = plan.get("time") or {}
    if time_info.get("override_total") is not None:
        parts = time_info.get("parts") or {}
        return {
            "total": time_info["override_total"], "unit": time_info.get("unit", "h"),
            "parts": parts, "computed": True,
        }
    parts = time_info.get("parts") or {}
    total = None
    usable = False
    for key in ("prep", "run", "booking", "rework"):
        val = parts.get(key)
        if val is None:
            continue
        try:
            total = (total or 0.0) + float(val)
            usable = True
        except (TypeError, ValueError):
            continue
    if not usable:
        total = None
    return {
        "total": total, "unit": time_info.get("unit", "h"),
        "parts": parts, "computed": bool(usable),
    }


def normalize_weights(weights, criteria=None):
    criteria = criteria or CRITERIA
    present = {}
    for c in criteria:
        val = weights.get(c)
        if val is not None:
            try:
                present[c] = float(val)
            except (TypeError, ValueError):
                present[c] = 0.0
    if not present:
        present = {c: 1.0 for c in criteria}
    total = sum(present.values())
    if total <= 0:
        present = {c: 1.0 for c in criteria}
        total = len(criteria)
    return {c: present.get(c, 0.0) / total for c in criteria}


def weighted_score(ratings, weights):
    available = [c for c in CRITERIA if ratings.get(c) is not None]
    if not available:
        return None
    denom = sum(weights.get(c, 0.0) for c in available)
    if denom <= 0:
        denom = float(len(available))
        return sum((float(ratings.get(c, 0.0)) or 0.0) / denom for c in available)
    return sum((float(ratings.get(c, 0.0)) or 0.0) * weights.get(c, 0.0) / denom
               for c in available)


def get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def compare_values(actual, op, expected):
    if actual is None:
        return False
    if op == "eq":
        try:
            return actual == expected or str(actual) == str(expected)
        except Exception:
            return False
    if op == "neq":
        try:
            return not (actual == expected or str(actual) == str(expected))
        except Exception:
            return True
    try:
        a = float(actual)
        b = float(expected)
    except (TypeError, ValueError):
        return False
    if op == "gt":
        return a > b
    if op == "gte":
        return a >= b
    if op == "lt":
        return a < b
    if op == "lte":
        return a <= b
    return False


def apply_thresholds(aug, plan_id, rules):
    log = []
    eliminated = False
    for rule in rules or []:
        path = rule.get("path")
        op = rule.get("op", "eq")
        value = rule.get("value")
        action = rule.get("action", "flag")
        reason = rule.get("reason") or ("%s %s %s" % (path, op, value))
        actual = get_path(aug, path)
        if not compare_values(actual, op, value):
            continue
        entry = {
            "plan_id": plan_id, "rule": reason, "path": path,
            "actual": actual, "expected": value, "action": action,
        }
        log.append(entry)
        if action == "eliminate":
            eliminated = True
    return eliminated, log


# --------------------------------------------------------------------------- #
# 产物写入：CSV / SVG / MD / PDF
# --------------------------------------------------------------------------- #

def write_csv(path, matrix, weights):
    columns = matrix["columns"]
    rows = matrix["rows"]
    scores = matrix["scores"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["criterion", "weight"] + columns)
    for c in rows:
        writer.writerow([c, ("%.3f" % weights.get(c, 0.0))]
                        + [scores.get(c, {}).get(p, "") for p in columns])
    writer.writerow(["weighted_score", ""]
                    + [("%.3f" % matrix["weighted"].get(p, "")) if isinstance(
                        matrix["weighted"].get(p), (int, float)) else ""
                       for p in columns])
    writer.writerow(["feasible", ""]
                    + [("yes" if matrix["feasible"].get(p) else "no") for p in columns])
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("\ufeff")
        fh.write(buf.getvalue())
        fh.write("\n")


def _score_color(norm):
    norm = max(0.0, min(1.0, float(norm)))
    r = int(235 * (1.0 - norm))
    g = int(235 * norm)
    b = 90
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    text = "#000000" if lum > 150 else "#ffffff"
    return "#%02x%02x%02x" % (r, g, b), text


def write_svg(path, title, matrix, weights):
    SVG_NS = "http://www.w3.org/2000/svg"
    ET.register_namespace("", SVG_NS)
    columns = matrix["columns"]
    rows = matrix["rows"]
    scores = matrix["scores"]
    normed = matrix["normalized"]
    feasible = matrix["feasible"]

    label_w = 150
    cell_w = 96
    head_h = 44
    row_h = 34
    title_h = 46
    width = label_w + len(columns) * cell_w
    height = title_h + head_h + (len(rows) + 2) * row_h

    root = ET.Element("{%s}svg" % SVG_NS, {
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
        "font-family": "sans-serif",
    })
    bg = ET.SubElement(root, "{%s}rect" % SVG_NS, {
        "x": "0", "y": "0", "width": str(width), "height": str(height),
        "fill": "#fbfbf8",
    })
    t = ET.SubElement(root, "{%s}text" % SVG_NS, {
        "x": str(label_w), "y": "30", "font-size": "16", "font-weight": "bold",
        "text-anchor": "middle", "fill": "#1a1a1a",
    })
    t.text = title

    # 表头
    for ci, col in enumerate(columns):
        x = label_w + ci * cell_w
        r = ET.SubElement(root, "{%s}rect" % SVG_NS, {
            "x": str(x), "y": str(title_h), "width": str(cell_w),
            "height": str(head_h), "fill": "#e8e8e2", "stroke": "#c9c9c3",
        })
        txt = ET.SubElement(root, "{%s}text" % SVG_NS, {
            "x": str(x + cell_w / 2), "y": str(title_h + 26), "font-size": "12",
            "font-weight": "bold", "text-anchor": "middle", "fill": "#1a1a1a",
        })
        txt.text = col

    # 行标签
    for ri, c in enumerate(rows):
        y = title_h + head_h + ri * row_h
        r = ET.SubElement(root, "{%s}rect" % SVG_NS, {
            "x": "0", "y": str(y), "width": str(label_w), "height": str(row_h),
            "fill": "#f0f0ea", "stroke": "#c9c9c3",
        })
        txt = ET.SubElement(root, "{%s}text" % SVG_NS, {
            "x": str(label_w - 8), "y": str(y + 21), "font-size": "11",
            "text-anchor": "end", "fill": "#1a1a1a",
        })
        txt.text = c

    # 数值格
    for ri, c in enumerate(rows):
        y = title_h + head_h + ri * row_h
        for ci, col in enumerate(columns):
            x = label_w + ci * cell_w
            if not feasible.get(col):
                fill = "#c8c8c8"
                text_color = "#4a4a4a"
                value = "X"
            else:
                norm = normed.get(c, {}).get(col)
                if norm is None:
                    fill = "#eeeeee"
                    text_color = "#4a4a4a"
                    value = ""
                else:
                    fill, text_color = _score_color(norm)
                    value = str(scores.get(c, {}).get(col))
            ET.SubElement(root, "{%s}rect" % SVG_NS, {
                "x": str(x), "y": str(y), "width": str(cell_w),
                "height": str(row_h), "fill": fill, "stroke": "#c9c9c3",
            })
            txt = ET.SubElement(root, "{%s}text" % SVG_NS, {
                "x": str(x + cell_w / 2), "y": str(y + 21), "font-size": "12",
                "text-anchor": "middle", "fill": text_color,
            })
            txt.text = value

    # 加权分 + 可行性行
    meta_rows = [("weighted_score", matrix["weighted"]), ("feasible", feasible)]
    for mi, (label, mapping) in enumerate(meta_rows):
        y = title_h + head_h + (len(rows) + mi) * row_h
        r = ET.SubElement(root, "{%s}rect" % SVG_NS, {
            "x": "0", "y": str(y), "width": str(label_w), "height": str(row_h),
            "fill": "#f0f0ea", "stroke": "#c9c9c3",
        })
        txt = ET.SubElement(root, "{%s}text" % SVG_NS, {
            "x": str(label_w - 8), "y": str(y + 21), "font-size": "11",
            "text-anchor": "end", "fill": "#1a1a1a",
        })
        txt.text = label
        for ci, col in enumerate(columns):
            x = label_w + ci * cell_w
            ET.SubElement(root, "{%s}rect" % SVG_NS, {
                "x": str(x), "y": str(y), "width": str(cell_w),
                "height": str(row_h), "fill": "#fafafa", "stroke": "#c9c9c3",
            })
            txt = ET.SubElement(root, "{%s}text" % SVG_NS, {
                "x": str(x + cell_w / 2), "y": str(y + 21), "font-size": "12",
                "text-anchor": "middle", "fill": "#1a1a1a",
            })
            val = mapping.get(col)
            if label == "weighted_score" and isinstance(val, (int, float)):
                txt.text = "%.3f" % val
            else:
                txt.text = "yes" if val else "no"

    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n")


def _md_escape(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def write_markdown(path, title, matrix, selection, candidates, eliminated_reasons):
    columns = matrix["columns"]
    rows = matrix["rows"]
    scores = matrix["scores"]
    lines = []
    lines.append("# %s" % title)
    lines.append("")
    lines.append("> 本文件由脚本自动生成；中间表示与选择结论见 `result.json`。")
    lines.append("")
    lines.append("## 1. 五维评分矩阵")
    lines.append("")
    lines.append("| 维度 | 权重 | " + " | ".join(columns) + " |")
    lines.append("|---|:---:|" + ":---:|" * len(columns))
    for c in rows:
        cells = [str(scores.get(c, {}).get(p, "")) for p in columns]
        lines.append("| %s | %.3f | %s |" % (c, 0.0, " | ".join(cells)))
    lines.append("")
    weighted = matrix["weighted"]
    lines.append("| 加权分 | — | " + " | ".join(
        ("%.3f" % weighted[p] if isinstance(weighted.get(p), (int, float)) else "")
        for p in columns) + " |")
    feas = matrix["feasible"]
    lines.append("| 可行 | — | " + " | ".join("是" if feas.get(p) else "否" for p in columns) + " |")
    lines.append("")
    lines.append("## 2. 选择结论")
    lines.append("")
    chosen = selection.get("chosen")
    lines.append("- 选择：`%s`" % (chosen or "无（全部不可行或证据不足）"))
    lines.append("- 排序：`%s`" % " > ".join(selection.get("ranking") or []))
    lines.append("- 理由：%s" % (selection.get("rationale") or "（未填写）"))
    lines.append("")
    rejected = selection.get("rejected") or []
    if rejected:
        lines.append("### 舍弃理由")
        lines.append("")
        for item in rejected:
            lines.append("- `%s`：%s" % (_md_escape(item.get("plan_id")),
                                          _md_escape(item.get("reason", ""))))
        lines.append("")
    if eliminated_reasons:
        lines.append("### 硬阈值淘汰")
        lines.append("")
        for pid, reasons in eliminated_reasons.items():
            for r in reasons:
                lines.append("- `%s`：%s" % (pid, _md_escape(r.get("rule", ""))))
        lines.append("")
    sens = selection.get("sensitivity") or {}
    if sens:
        lines.append("### 敏感性")
        lines.append("")
        lines.append("- 基准排序：`%s`" % " > ".join(sens.get("base_ranking") or []))
        lines.append("- 首选方案在 ±0.05 权重扰动下是否稳定：`%s`" %
                     ("是" if sens.get("chosen_stable") else "否"))
        lines.append("")
    lines.append("## 3. 候选方案")
    lines.append("")
    for plan in candidates:
        lines.append("### %s — %s" % (plan.get("plan_id"), _md_escape(plan.get("name", ""))))
        seven = plan.get("seven_fields") or {}
        for field in SEVEN_FIELDS:
            val = (seven.get(field) or "").strip()
            if val:
                lines.append("- **%s**：%s" % (field, _md_escape(val)))
        cost = plan.get("cost_summary") or {}
        time_info = plan.get("time_summary") or {}
        lines.append("- **成本**：%s %s（方法=%s）" % (
            cost.get("total") if cost.get("total") is not None else "未定",
            cost.get("currency", "CNY"), cost.get("method", "computed")))
        lines.append("- **时间**：%s %s" % (
            time_info.get("total") if time_info.get("total") is not None else "未定",
            time_info.get("unit", "h")))
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")


def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _ascii(text):
    return str(text).encode("latin-1", "replace").decode("latin-1")


def write_minimal_pdf(path, title, lines):
    content_lines = []
    y = 760
    content_lines.append("BT /F1 11 Tf 50 %d Td (%s) Tj ET" % (y, _pdf_escape(_ascii(title))))
    y -= 24
    for ln in lines:
        y -= 13
        if y < 40:
            break
        content_lines.append("BT /F1 9 Tf 50 %d Td (%s) Tj ET" % (y, _pdf_escape(_ascii(ln))))
    stream = "\n".join(content_lines).encode("latin-1", "replace")

    def obj(num, body):
        return ("%d 0 obj\n%s\nendobj\n" % (num, body)).encode("latin-1", "replace")

    objs = [
        (1, "<< /Type /Catalog /Pages 2 0 R >>"),
        (2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        (4, "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"),
        (5, "<< /Length %d >>\nstream\n%s\nendstream"
            % (len(stream), stream.decode("latin-1"))),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num, body in objs:
        offsets[num] = out.tell()
        out.write(obj(num, body))
    xref_pos = out.tell()
    count = len(objs) + 1
    out.write(("xref\n0 %d\n" % count).encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for num in range(1, count):
        out.write(("%010d 00000 n \n" % offsets[num]).encode("ascii"))
    out.write(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
               % (count, xref_pos)).encode("ascii"))
    with open(path, "wb") as fh:
        fh.write(out.getvalue())


# --------------------------------------------------------------------------- #
# inspect：事实层 case_pack + result.json 骨架
# --------------------------------------------------------------------------- #

def run_inspect(input_dir, work_dir, output_dir):
    _ensure_dir(work_dir)
    _ensure_dir(output_dir)
    files = discover_files(input_dir)
    case_entry = find_case_json(files)
    task_entry = find_task_md(files)
    errors = []
    degradations = []

    case_data = None
    case_unreadable = False
    if case_entry is not None:
        case_data, jerrors = read_json_file(case_entry[0])
        if case_data is None:
            case_unreadable = True
            degradations.append({
                "code": "case_json_unreadable",
                "file": case_entry[1],
                "note": "; ".join(jerrors),
            })
        errors.extend({"code": "json", "file": case_entry[1], "note": e} for e in jerrors)

    # 材料索引（含大小与提取状态标记）
    material_index = []
    for abs_path, rel_path in files:
        if case_entry and abs_path == case_entry[0]:
            continue
        if task_entry and abs_path == task_entry[0]:
            continue
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            size = 0
        entry = {
            "path": "materials/" + rel_path.replace("\\", "/"),
            "kind": classify_material(abs_path),
            "size_bytes": size,
            "extracted": size <= MAX_FILE_BYTES,
            "note": "" if size <= MAX_FILE_BYTES else "over_2MB_skipped",
        }
        if entry["kind"] == "pdf":
            entry["note"] = "PDF：须用 pdf-text-tool / pdftotext 提取后再读原文，勿按标签臆造"
        material_index.append(entry)

    # output_contract 原样回填（无显式契约则给默认三组）
    contract = {}
    if isinstance(case_data, dict):
        raw_contract = case_data.get("output_contract")
        if isinstance(raw_contract, dict):
            contract = dict(raw_contract)
    if not contract:
        contract = {k: None for k in DEFAULT_CONTRACT_KEYS}

    case_id = _extract_str_case_id(case_data)
    candidates_raw = discover_candidates(case_data) if isinstance(case_data, dict) else []
    candidates_raw = assign_plan_ids(candidates_raw)

    if case_entry is None:
        status = "input_error"
    elif case_unreadable:
        status = "partial"
    elif not candidates_raw:
        status = "insufficient_evidence"
    else:
        status = "partial"

    fingerprint = input_fingerprint(files) if files else ""

    case_pack = {
        "schema": "case_pack",
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "status": status,
        "input_hash": fingerprint,
        "created_at": utcnow(),
        "input_dir": input_dir,
        "input_files": {
            "case": case_entry[1] if case_entry else None,
            "task": task_entry[1] if task_entry else None,
            "materials": [m["path"] for m in material_index],
        },
        "output_contract": contract,
        "candidates_raw": candidates_raw,
        "candidate_count": len(candidates_raw),
        "materials_index": material_index,
        "errors": errors,
        "degradations": degradations,
    }
    write_json(os.path.join(work_dir, "case_pack.json"), case_pack)

    skeleton = build_skeleton(case_pack)
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    return skeleton, case_pack


def build_skeleton(case_pack):
    contract = case_pack.get("output_contract") or {}
    skeleton = {
        "schema": "result",
        "schema_version": SCHEMA_VERSION,
        "case_id": case_pack.get("case_id"),
        "status": case_pack.get("status"),
        "output_contract": {k: (v if v is not None else None) for k, v in contract.items()},
        "research_question": None,
        "comparison_frame": {
            "objectives": [],
            "hard_constraints": [],
            "criteria": list(CRITERIA),
        },
        "candidates": [],
        "comparison_matrix": {
            "rows": list(CRITERIA),
            "columns": [],
            "scores": {},
            "normalized": {},
            "weighted": {},
            "feasible": {},
        },
        "selection": {
            "chosen": None, "ranking": [], "rationale": "",
            "rejected": [], "sensitivity": {},
        },
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(), "step": "inspect",
            "note": "已落盘合法结果骨架；后续步骤只做增量改进",
        }],
        "errors": list(case_pack.get("errors") or []),
        "degradations": list(case_pack.get("degradations") or []),
        "limitations": [],
        "runtime": {
            "started_at": case_pack.get("created_at"), "finished_at": None,
            "elapsed_seconds": None, "model_requests_used": None,
        },
        "artifacts": {},
        "intermediate_artifacts": {},
    }
    for key in contract:
        skeleton[key] = None
    return skeleton


# --------------------------------------------------------------------------- #
# init-models：方案层与比较层骨架
# --------------------------------------------------------------------------- #

def run_init_models(work_dir):
    _ensure_dir(work_dir)
    case_pack, _ = read_json_file(os.path.join(work_dir, "case_pack.json"))
    if case_pack is None:
        case_pack = {"candidates_raw": [], "case_id": None}
    candidates = case_pack.get("candidates_raw") or []
    plans = []
    for raw in candidates:
        pid = raw.get("plan_id") or ("P%d" % (len(plans) + 1))
        plans.append({
            "plan_id": pid,
            "name": raw.get("name") or raw.get("title") or pid,
            "version": raw.get("version") or "v1",
            "seven_fields": {f: "" for f in SEVEN_FIELDS},
            "dimension_ratings": {c: None for c in CRITERIA},
            "feasibility": {"equipment_available": None, "lead_time_ok": None, "notes": ""},
            "cost": {"method": "computed", "currency": "CNY", "total": None, "lines": []},
            "time": {"unit": "h", "override_total": None, "parts": {}},
            "risks": [],
            "evidence_refs": [],
            "infeasible_reasons": [],
        })
    plans_model = {
        "schema": "plans_model",
        "schema_version": SCHEMA_VERSION,
        "case_id": case_pack.get("case_id"),
        "candidates": plans,
        "evidence_registry": [],
    }
    comparison_model = {
        "schema": "comparison_model",
        "schema_version": SCHEMA_VERSION,
        "case_id": case_pack.get("case_id"),
        "comparison_frame": {
            "objectives": [],
            "hard_constraints": [],
            "criteria": list(CRITERIA),
        },
        "weights": dict(DEFAULT_WEIGHTS),
        "thresholds": [],
        "selection": {
            "chosen": None, "ranking": [], "rationale": "",
            "rejected": [], "sensitivity": {},
        },
        "confirmations": [],
        "uncertainty": [],
        "change_log": [],
    }
    write_json(os.path.join(work_dir, "plans_model.json"), plans_model)
    write_json(os.path.join(work_dir, "comparison_model.json"), comparison_model)
    return plans_model, comparison_model


# --------------------------------------------------------------------------- #
# assemble：汇总三层 IR 为最终 result.json 与矩阵产物
# --------------------------------------------------------------------------- #

def _load_model(work_dir, output_dir, name):
    for base in (work_dir, output_dir):
        path = os.path.join(base, name)
        if os.path.exists(path):
            obj, _ = read_json_file(path)
            if obj is not None:
                return obj
    return None


def build_matrix(plans, weights):
    columns = [p.get("plan_id") or ("P%d" % (i + 1)) for i, p in enumerate(plans)]
    scores = {c: {} for c in CRITERIA}
    normalized = {c: {} for c in CRITERIA}
    weighted = {}
    feasible = {}
    for p in plans:
        pid = p.get("plan_id") or "?"
        ratings = p.get("dimension_ratings") or {}
        for c in CRITERIA:
            val = ratings.get(c)
            scores[c][pid] = val
            normalized[c][pid] = (float(val) / 5.0) if isinstance(val, (int, float)) else None
        weighted[pid] = weighted_score(ratings, weights)
        feasible[pid] = bool(p.get("_feasible", True))
    return {
        "rows": list(CRITERIA),
        "columns": columns,
        "scores": scores,
        "normalized": normalized,
        "weighted": weighted,
        "feasible": feasible,
    }


def run_assemble(input_dir, work_dir, output_dir):
    _ensure_dir(work_dir)
    _ensure_dir(output_dir)
    case_pack = _load_model(work_dir, output_dir, "case_pack.json") or {}
    plans_model = _load_model(work_dir, output_dir, "plans_model.json") or {}
    comparison_model = _load_model(work_dir, output_dir, "comparison_model.json") or {}

    errors = list(case_pack.get("errors") or [])
    degradations = list(case_pack.get("degradations") or [])

    plans = plans_model.get("candidates") or []
    if not plans:
        plans = assign_plan_ids(case_pack.get("candidates_raw") or [])
    if not plans and case_pack.get("status") != "input_error":
        degradations.append({"code": "no_plans_model", "note": "plans_model 缺失，已回退 case_pack.candidates_raw"})

    # 成本/时间汇总
    for plan in plans:
        plan["cost_summary"] = compute_plan_cost(plan)
        plan["time_summary"] = compute_plan_time(plan)

    weights_raw = comparison_model.get("weights") or DEFAULT_WEIGHTS
    weights = normalize_weights(weights_raw)
    thresholds = comparison_model.get("thresholds") or []

    # 硬阈值淘汰
    eliminated_reasons = {}
    elimination_log = []
    for plan in plans:
        pid = plan.get("plan_id") or "?"
        aug = dict(plan)
        aug["cost_total"] = (plan.get("cost_summary") or {}).get("total")
        aug["time_hours"] = (plan.get("time_summary") or {}).get("total")
        feas = plan.get("feasibility") or {}
        aug["equipment_available"] = feas.get("equipment_available")
        aug["dimension_ratings"] = plan.get("dimension_ratings") or {}
        elim, log = apply_thresholds(aug, pid, thresholds)
        plan["_feasible"] = not elim
        if log:
            eliminated_reasons[pid] = log
            elimination_log.extend(log)

    feasible_plans = [p for p in plans if p.get("_feasible", True)]
    base_ranking = sorted(
        feasible_plans,
        key=lambda p: (weighted_score(p.get("dimension_ratings") or {}, weights) or -1e9),
        reverse=True,
    )
    base_ranking_ids = [p.get("plan_id") for p in base_ranking]

    # 敏感性：每个维度 ±0.05
    sensitivity = {"base_ranking": base_ranking_ids, "perturbations": []}
    chosen_stable = True
    for c in CRITERIA:
        for delta in (0.05, -0.05):
            w2 = {k: (weights[k] + (delta if k == c else 0.0)) for k in weights}
            w2 = {k: max(0.0, v) for k, v in w2.items()}
            w2 = normalize_weights(w2)
            ranking = sorted(
                feasible_plans,
                key=lambda p: (weighted_score(p.get("dimension_ratings") or {}, w2) or -1e9),
                reverse=True,
            )
            rids = [p.get("plan_id") for p in ranking]
            changed = rids != base_ranking_ids
            if changed:
                chosen_stable = False
            sensitivity["perturbations"].append({
                "criterion": c, "delta": delta, "ranking": rids, "changed": changed,
            })
    sensitivity["chosen_stable"] = chosen_stable

    # 证据注册表 → sources
    registry = plans_model.get("evidence_registry") or []
    sources = []
    for entry in registry:
        sources.append({
            "id": entry.get("id"),
            "claim": entry.get("claim"),
            "locator": entry.get("locator"),
            "quote": entry.get("quote"),
            "kind": entry.get("kind"),
        })

    selection = dict(comparison_model.get("selection") or {})
    chosen = selection.get("chosen")
    ranking = selection.get("ranking") or []
    if not ranking:
        ranking = base_ranking_ids
    if chosen not in ranking and ranking:
        chosen = ranking[0]
    if not ranking and chosen:
        ranking = [chosen]
    if chosen not in ranking:
        chosen = ranking[0] if ranking else None
    # 已淘汰方案不得作为首选
    if chosen in eliminated_reasons:
        selection.setdefault("_chosen_eliminated", True)
    else:
        selection.setdefault("_chosen_eliminated", False)
    selection["chosen"] = chosen
    selection["ranking"] = ranking
    selection["sensitivity"] = sensitivity
    rejected = selection.get("rejected") or []
    # 阈值淘汰自动并入 rejected
    for pid, log in eliminated_reasons.items():
        if not any(r.get("plan_id") == pid for r in rejected):
            rejected.append({
                "plan_id": pid,
                "reason": "; ".join(item.get("rule", "") for item in log),
                "source": "hard_threshold",
            })
    selection["rejected"] = rejected

    matrix = build_matrix(plans, weights)

    # 候选完整结构
    candidates_out = []
    for plan in plans:
        pid = plan.get("plan_id") or "?"
        candidates_out.append({
            "plan_id": pid,
            "name": plan.get("name"),
            "version": plan.get("version"),
            "seven_fields": plan.get("seven_fields") or {f: "" for f in SEVEN_FIELDS},
            "dimension_ratings": plan.get("dimension_ratings") or {},
            "feasibility": plan.get("feasibility") or {},
            "cost": plan.get("cost") or {},
            "cost_summary": plan.get("cost_summary") or {},
            "time": plan.get("time") or {},
            "time_summary": plan.get("time_summary") or {},
            "risks": plan.get("risks") or [],
            "evidence_refs": plan.get("evidence_refs") or [],
            "infeasible_reasons": plan.get("infeasible_reasons") or [],
            "eliminated_by_threshold": eliminated_reasons.get(pid) or [],
            "weighted_score": matrix["weighted"].get(pid),
        })

    confirmations = comparison_model.get("confirmations") or []
    uncertainty = comparison_model.get("uncertainty") or []
    pending_human = comparison_model.get("pending_human") or list(uncertainty)
    limitations = []
    for item in degradations:
        limitations.append({"kind": "degradation",
                            "note": item.get("note") or item.get("code") or str(item)})
    for item in pending_human:
        if isinstance(item, dict):
            limitations.append({"kind": "pending_human",
                                "note": item.get("item") or item.get("note") or str(item)})
        else:
            limitations.append({"kind": "pending_human", "note": str(item)})
    for item in uncertainty:
        if isinstance(item, dict):
            limitations.append({"kind": "uncertainty",
                                "note": item.get("item") or item.get("note") or str(item)})
    for plan in candidates_out:
        if (plan.get("cost_summary") or {}).get("method") == "not_computable":
            limitations.append({"kind": "not_computable",
                                "note": "%s 成本无法核算" % plan.get("plan_id")})
    change_log = list(comparison_model.get("change_log") or [])
    change_log.append({
        "time": utcnow(), "step": "assemble",
        "note": "已由三层 IR（case_pack/plans_model/comparison_model）汇总为 result.json 与矩阵产物",
    })

    # 汇总状态
    if case_pack.get("status") in ("input_error", "insufficient_evidence"):
        status = case_pack.get("status")
    else:
        hard_block = bool(errors) or bool(degradations)
        if chosen is None:
            status = "partial"
        elif hard_block or pending_human or confirmations:
            status = "partial"
        else:
            status = "ok"

    result = {
        "schema": "result",
        "schema_version": SCHEMA_VERSION,
        "case_id": case_pack.get("case_id") or plans_model.get("case_id"),
        "status": status,
        "output_contract": {},
        "research_question": comparison_model.get("research_question"),
        "comparison_frame": comparison_model.get("comparison_frame") or {
            "objectives": [], "hard_constraints": [], "criteria": list(CRITERIA),
        },
        "weights": weights,
        "thresholds": thresholds,
        "elimination_log": elimination_log,
        "candidates": candidates_out,
        "comparison_matrix": matrix,
        "selection": selection,
        "confirmations": confirmations,
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "runtime": {
            "started_at": case_pack.get("created_at"),
            "finished_at": utcnow(),
            "elapsed_seconds": None,
            "model_requests_used": None,
        },
        "artifacts": {},
        "intermediate_artifacts": {},
    }
    result = fill_output_contract(result, case_pack, plans_model, comparison_model)

    # 写中间表示到 output（可复核工件）
    intermediate = {}
    for name, obj in (("plans_model.json", plans_model), ("comparison_model.json", comparison_model)):
        dst = os.path.join(output_dir, name)
        write_json(dst, obj)
        intermediate[name] = {"path": name, "sha256": sha256_file(dst)}

    # 写矩阵与文档产物
    matrix_csv = os.path.join(output_dir, "comparison_matrix.csv")
    matrix_svg = os.path.join(output_dir, "comparison_matrix.svg")
    matrix_pdf = os.path.join(output_dir, "comparison_matrix.pdf")
    plan_md = os.path.join(output_dir, "plan_comparison.md")
    title = "实验方案比较矩阵（case %s）" % (result["case_id"] or "unknown")
    write_csv(matrix_csv, matrix, weights)
    write_svg(matrix_svg, title, matrix, weights)
    write_markdown(plan_md, title, matrix, selection, candidates_out, eliminated_reasons)
    pdf_lines = ["%s | %s | weighted=%s | feasible=%s" % (
        p.get("plan_id"), p.get("name"), p.get("weighted_score"),
        "yes" if matrix["feasible"].get(p.get("plan_id")) else "no")
        for p in candidates_out]
    pdf_lines.append("chosen=%s ranking=%s" % (chosen, ">".join(ranking or [])))
    pdf_lines.append("(ASCII preview; full matrix in comparison_matrix.csv/svg)")
    write_minimal_pdf(matrix_pdf, "Experiment Plan Comparison (ASCII preview)", pdf_lines)

    result["artifacts"] = {
        "result_json": "result.json",
        "comparison_matrix_csv": "comparison_matrix.csv",
        "comparison_matrix_svg": "comparison_matrix.svg",
        "comparison_matrix_pdf": "comparison_matrix.pdf",
        "plan_comparison_md": "plan_comparison.md",
    }
    result["intermediate_artifacts"] = intermediate

    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)
    return result


def fill_output_contract(result, case_pack, plans_model, comparison_model):
    contract = case_pack.get("output_contract") or {}
    if not contract:
        contract = {k: None for k in DEFAULT_CONTRACT_KEYS}
    filled = {}
    for key in contract:
        filled[key] = _resolve_contract_field(key, result, comparison_model)
    result["output_contract"] = filled
    for key, val in filled.items():
        result[key] = val
    return result


_CONTRACT_ALIASES = [
    ("experimental_plan", "candidates"),
    ("plan", "candidates"),
    ("plans", "candidates"),
    ("evidence_and_risks", "evidence_and_risks"),
    ("evidence", "sources"),
    ("risks", "risks"),
    ("confirmation_items", "confirmations"),
    ("confirmation", "confirmations"),
    ("comparison_matrix", "comparison_matrix"),
    ("matrix", "comparison_matrix"),
    ("comparison", "comparison_matrix"),
    ("selection", "selection"),
    ("uncertainty", "uncertainty"),
    ("sources", "sources"),
]


def _norm_key(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _contract_value(target, result):
    if target == "evidence_and_risks":
        return {
            "evidence": result.get("sources", []),
            "risks": [r for p in result.get("candidates", []) for r in (p.get("risks") or [])],
        }
    if target == "risks":
        return [r for p in result.get("candidates", []) for r in (p.get("risks") or [])]
    return result.get(target)


def _resolve_contract_field(key, result, comparison_model):
    norm = _norm_key(key)
    # 精确匹配优先
    for alias, target in _CONTRACT_ALIASES:
        if _norm_key(alias) == norm:
            return _contract_value(target, result)
    # 回退：前缀/包含匹配，最长别名优先
    for alias, target in sorted(_CONTRACT_ALIASES, key=lambda x: -len(x[0])):
        an = _norm_key(alias)
        if an and (norm.startswith(an) or an.startswith(norm) or an in norm):
            return _contract_value(target, result)
    for candidate in (key, str(key).lower()):
        if candidate in result:
            return result[candidate]
    return None


# --------------------------------------------------------------------------- #
# validate：结果合法性自检
# --------------------------------------------------------------------------- #

def run_validate(result_path):
    data, errs = read_json_file(result_path)
    issues = []
    if data is None:
        issues.append({"level": "error", "code": "json_unreadable", "message": "; ".join(errs)})
        return {"valid": False, "status": None, "issues": issues}

    for key in ("case_id", "status", "sources", "uncertainty", "change_log",
                "pending_human", "limitations", "candidates", "selection",
                "comparison_matrix"):
        if key not in data:
            issues.append({"level": "error", "code": "missing_key", "message": key})

    status = data.get("status")
    if status not in STATUSES:
        issues.append({"level": "error", "code": "bad_status", "message": str(status)})

    contract = data.get("output_contract") or {}
    for key in contract:
        if key not in data:
            issues.append({"level": "warning", "code": "contract_not_flattened",
                           "message": key})

    for i, src in enumerate(data.get("sources") or []):
        if not src.get("locator") or not src.get("quote"):
            issues.append({"level": "error", "code": "source_missing_evidence",
                           "message": "sources[%d] 缺 locator/quote" % i})

    for i, plan in enumerate(data.get("candidates") or []):
        seven = plan.get("seven_fields") or {}
        for f in SEVEN_FIELDS:
            if not (seven.get(f) or "").strip():
                issues.append({"level": "warning", "code": "seven_field_empty",
                               "message": "%s.%s" % (plan.get("plan_id", i), f)})
        ratings = plan.get("dimension_ratings") or {}
        for c in CRITERIA:
            val = ratings.get(c)
            if val is not None and (not isinstance(val, (int, float)) or val < 0 or val > 5):
                issues.append({"level": "error", "code": "rating_out_of_range",
                               "message": "%s.%s=%r" % (plan.get("plan_id", i), c, val)})

    artifacts = data.get("artifacts") or {}
    base_dir = os.path.dirname(os.path.abspath(result_path))
    for name, rel in artifacts.items():
        if not isinstance(rel, str):
            continue
        path = os.path.join(base_dir, rel)
        if not os.path.exists(path):
            issues.append({"level": "warning", "code": "artifact_missing", "message": rel})
            continue
        if name == "comparison_matrix_svg":
            try:
                ET.parse(path)
            except Exception as exc:
                issues.append({"level": "error", "code": "svg_unparseable",
                               "message": "%s: %s" % (rel, exc)})
        if name == "comparison_matrix_pdf":
            with open(path, "rb") as fh:
                head = fh.read(5)
                fh.seek(-6, 2)
                tail = fh.read(6)
            if head != b"%PDF-":
                issues.append({"level": "error", "code": "pdf_bad_magic", "message": rel})
            if b"%%EOF" not in tail:
                issues.append({"level": "warning", "code": "pdf_no_eof", "message": rel})

    errors = [i for i in issues if i["level"] == "error"]
    return {"valid": not errors, "status": status, "issues": issues}


# --------------------------------------------------------------------------- #
# demo：自包含示例（明确标注“示例”，非正式案例）
# --------------------------------------------------------------------------- #

DEMO_CASE = {
    "case_id": "demo-case-000",
    "research_question": "示例：给定目标与预算，比较三条候选实验路线并做可复核选择",
    "output_contract": {
        "experimental_plan": None,
        "evidence_and_risks": None,
        "confirmation_items": None,
    },
    "objectives": ["示例目标：在预算与时间窗内选出证据最强的路线"],
    "hard_constraints": ["总成本 ≤ 100000 CNY", "总时间 ≤ 400 h", "关键设备必须可得"],
    "candidates": [
        {"plan_id": "P1", "name": "路线 A（示例）",
         "seven_fields": {"assumptions": "示例假设", "variables": "示例变量",
                          "controls": "示例对照", "steps": "示例步骤",
                          "parameters": "示例参数", "resources": "示例资源",
                          "interpretation_criteria": "示例判读标准"},
         "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 3, "feasibility": 4, "evidence": 4},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes", "notes": ""},
         "cost": {"method": "computed", "currency": "CNY", "total": None,
                  "lines": [{"item": "机时", "kind": "machine_time", "unit_cost": 800, "quantity": 40},
                            {"item": "耗材", "kind": "consumable", "unit_cost": 12000, "quantity": 1}]},
         "time": {"unit": "h", "override_total": None,
                  "parts": {"prep": 20, "run": 60, "booking": 10, "rework": 10}},
         "risks": [], "evidence_refs": ["E1"], "infeasible_reasons": []},
        {"plan_id": "P2", "name": "路线 B（示例）",
         "seven_fields": {"assumptions": "示例假设", "variables": "示例变量",
                          "controls": "示例对照", "steps": "示例步骤",
                          "parameters": "示例参数", "resources": "示例资源",
                          "interpretation_criteria": "示例判读标准"},
         "dimension_ratings": {"goal_fit": 5, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 5},
         "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes", "notes": ""},
         "cost": {"method": "computed", "currency": "CNY", "total": None,
                  "lines": [{"item": "机时", "kind": "machine_time", "unit_cost": 700, "quantity": 30},
                            {"item": "耗材", "kind": "consumable", "unit_cost": 9000, "quantity": 1}]},
         "time": {"unit": "h", "override_total": None,
                  "parts": {"prep": 15, "run": 40, "booking": 5, "rework": 5}},
         "risks": [], "evidence_refs": ["E1", "E2"], "infeasible_reasons": []},
        {"plan_id": "P3", "name": "路线 C（示例）",
         "seven_fields": {"assumptions": "示例假设", "variables": "示例变量",
                          "controls": "示例对照", "steps": "示例步骤",
                          "parameters": "示例参数", "resources": "示例资源",
                          "interpretation_criteria": "示例判读标准"},
         "dimension_ratings": {"goal_fit": 3, "risk": 2, "cost": 2, "feasibility": 3, "evidence": 2},
         "feasibility": {"equipment_available": "no", "lead_time_ok": "no", "notes": "关键设备不可得"},
         "cost": {"method": "computed", "currency": "CNY", "total": None,
                  "lines": [{"item": "机时", "kind": "machine_time", "unit_cost": 1500, "quantity": 80}]},
         "time": {"unit": "h", "override_total": None,
                  "parts": {"prep": 40, "run": 120, "booking": 60, "rework": 40}},
         "risks": [], "evidence_refs": [], "infeasible_reasons": []},
    ],
    "evidence_registry": [
        {"id": "E1", "claim": "示例证据：通用流程", "locator": "materials/demo_quote.md#p1",
         "quote": "示例引用片段（非正式案例，仅演示证据挂载）", "kind": "literature"},
        {"id": "E2", "claim": "示例证据：设备手册", "locator": "materials/demo_quote.md#p2",
         "quote": "示例手册片段（非正式案例，仅演示证据挂载）", "kind": "manual"},
    ],
    "weights": {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
                "cost": 0.15, "risk": 0.15},
    "thresholds": [
        {"path": "cost_total", "op": "gt", "value": 100000, "action": "eliminate",
         "reason": "超过预算上限 100000 CNY"},
        {"path": "equipment_available", "op": "eq", "value": "no", "action": "eliminate",
         "reason": "关键设备不可得"},
    ],
}


def run_demo(output_dir=None):
    base = output_dir or tempfile.mkdtemp(prefix="sci-plan-demo-")
    _ensure_dir(base)
    input_dir = os.path.join(base, "input")
    work_dir = os.path.join(base, "work")
    out_dir = os.path.join(base, "output")
    _ensure_dir(input_dir)
    _ensure_dir(os.path.join(input_dir, "materials"))
    demo_case = json.loads(json.dumps(DEMO_CASE))
    write_json(os.path.join(input_dir, "case.json"), demo_case)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 实验方案比较与选择（示例）\n\n本文件为最小调用示例，非正式案例。\n")
    with open(os.path.join(input_dir, "materials", "demo_quote.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo_quote\n\np1: 示例引用片段（非正式案例，仅演示证据挂载）\n\n"
                 "p2: 示例手册片段（非正式案例，仅演示证据挂载）\n")

    run_inspect(input_dir, work_dir, out_dir)
    run_init_models(work_dir)
    plans_model = {
        "schema": "plans_model", "schema_version": SCHEMA_VERSION,
        "case_id": demo_case["case_id"],
        "candidates": demo_case["candidates"],
        "evidence_registry": demo_case["evidence_registry"],
    }
    comparison_model = {
        "schema": "comparison_model", "schema_version": SCHEMA_VERSION,
        "case_id": demo_case["case_id"],
        "comparison_frame": {"objectives": demo_case["objectives"],
                             "hard_constraints": demo_case["hard_constraints"],
                             "criteria": list(CRITERIA)},
        "weights": demo_case["weights"],
        "thresholds": demo_case["thresholds"],
        "selection": {"chosen": "P2", "ranking": ["P2", "P1", "P3"],
                      "rationale": "示例：P2 证据最强且成本/时间在约束内（示例结论）",
                      "rejected": [{"plan_id": "P3", "reason": "关键设备不可得（示例）"}],
                      "sensitivity": {}},
        "confirmations": [{"item": "示例：确认设备档期", "type": "equipment",
                           "status": "pending", "note": "示例待确认项"}],
        "uncertainty": [{"item": "示例：报价为占位数字", "level": "示例", "note": "非真实报价"}],
        "change_log": [{"time": utcnow(), "step": "demo",
                        "note": "示例数据（非正式案例），仅用于自检"}],
    }
    write_json(os.path.join(work_dir, "plans_model.json"), plans_model)
    write_json(os.path.join(work_dir, "comparison_model.json"), comparison_model)
    run_assemble(input_dir, work_dir, out_dir)
    validation = run_validate(os.path.join(out_dir, "result.json"))
    print("DEMO（示例，非正式案例）output_dir=%s" % out_dir)
    print("DEMO status=%s valid=%s" % (validation.get("status"), validation.get("valid")))
    return {"output_dir": out_dir, "validation": validation}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="实验方案比较与选择 Skill：三层 IR → result.json + 矩阵产物",
    )
    parser.add_argument("command", nargs="?", default="help",
                        choices=["inspect", "init-models", "assemble", "validate",
                                 "demo", "help"])
    parser.add_argument("--input", default=os.environ.get("SKILL_INPUT_DIR", "/input"))
    parser.add_argument("--work", default=os.environ.get("SKILL_WORK_DIR", "/app/work"))
    parser.add_argument("--output", default=os.environ.get("SKILL_OUTPUT_DIR", "/app/output"))
    parser.add_argument("positional", nargs="*")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.command
    if cmd in ("help", "--help", "-h"):
        parser.print_help()
        return 0
    try:
        if cmd == "inspect":
            run_inspect(args.input, args.work, args.output)
            return 0
        if cmd == "init-models":
            run_init_models(args.work)
            return 0
        if cmd == "assemble":
            run_assemble(args.input, args.work, args.output)
            return 0
        if cmd == "validate":
            path = args.positional[0] if args.positional else os.path.join(args.output, "result.json")
            result = run_validate(path)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "demo":
            target = args.positional[0] if args.positional else None
            run_demo(target)
            return 0
    except Exception as exc:  # 任何失败都不留空产物，也不让进程崩溃
        print("ERROR: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
