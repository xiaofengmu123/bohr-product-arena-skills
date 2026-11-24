#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审稿回复与论文修改 Skill 入口脚本（scaffold）。

读取 /input 下的 case.json、task.md 与 materials/，容错解析后产出：
  /app/output/result.json            —— 诚实的 partial 骨架（含三要素契约字段）
  /app/output/revision-ledger.json   —— 按意见拆解的台账骨架（待 Agent 填决策）
  /app/output/revised-manuscript.md  —— 基线快照（供增量修订，非预写答案）
  /app/output/response-letter.md     —— 逐条回复信骨架（按意见编号预置标题）
  /app/work/comments.json            —— 结构化意见（编号/类型/严重度提示）
  /app/work/sections.json            —— 手稿章节索引（含行号，供定位锚点）

仅用标准库；确定性重复运行；任何单文件损坏都不让进程崩溃。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000

# 意见类型/严重度关键词提示（仅为提示，Agent 可覆盖）
TYPE_HINTS = {
    "method": ("method", "方法", "模型", "model", "analysis", "分析", "统计",
               "statistical", "样本", "sample", "算法", "algorithm", "simulation",
               "仿真", "计算", "计算过程", "公式", "equation"),
    "data": ("data", "数据", "实验", "experiment", "数据集", "dataset", "结果",
             "result", "reproduc", "可复现", "数值", "number", "补实验",
             "补数据", "figure", "图表", "table", "表"),
    "writing": ("写作", "writing", "clarity", "清晰", "语法", "grammar", "语言",
                "language", "结构", "structure", "标题", "title", "摘要",
                "abstract", "表述", "wording", "论证", "逻辑", "argument"),
    "format": ("格式", "format", "引用", "citation", "reference", "参考文献",
               "style", "规范", "template", "模板", "编号", "numbering", "行号"),
    "ethics": ("伦理", "ethic", "同意", "consent", "批准", "approval", "利益冲突",
               "conflict of interest", "数据可用", "availability", "irb", "知情"),
}
SEVERITY_HINTS = {
    "major": ("major", "主要", "重大", "严重", "关键", "必须", "essential",
              "important", "重要", "substantive", "核心", "致命", "影响结论"),
    "minor": ("minor", "次要", "轻微", "slight", "小问题", "建议性", "可改进"),
}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """读 UTF-8 文本，容错 BOM 与常见坏编码；超限返回 None。"""
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None, "file too large (%d bytes)" % os.path.getsize(path)
        raw = open(path, "rb").read()
    except OSError as e:
        return None, str(e)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "replaced bad encoding"


def load_json_safe(path):
    text, err = read_text(path)
    if err and text is None:
        return None, err
    if text is None:
        text, err2 = read_text(path)
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as e:
        return None, "bad json: %s" % e


def list_files(root, max_bytes=MAX_FILE_BYTES):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            try:
                if os.path.isfile(p) and os.path.getsize(p) <= max_bytes:
                    out.append(p)
            except OSError:
                continue
    return out


def discover(input_dir):
    """发现 case.json / task.md / materials，返回结构化信息与异常记录。"""
    files = list_files(input_dir)
    info = {"case_path": None, "task_path": None, "materials": [],
            "other_json": [], "errors": []}
    for p in files:
        base = os.path.basename(p)
        rel = os.path.relpath(p, input_dir)
        low = base.lower()
        if low == "case.json":
            info["case_path"] = p
        elif low == "task.md":
            info["task_path"] = p
        elif rel.replace("\\", "/").startswith("materials/"):
            info["materials"].append(p)
        elif low.endswith(".json") and info["case_path"] is None:
            info["case_path"] = p  # 回退：任意根级 json 视为 case
        elif low.endswith(".json"):
            info["other_json"].append(p)
    return info


def classify(text, table):
    if not text:
        return None
    low = text.lower()
    for key, words in table.items():
        for w in words:
            if w in low:
                return key
    return None


def _dedupe(seen, base):
    if base not in seen:
        seen.add(base)
        return base
    k = 2
    while ("%s_%d" % (base, k)) in seen:
        k += 1
    cand = "%s_%d" % (base, k)
    seen.add(cand)
    return cand


def parse_comments(text):
    """把自由格式审稿意见拆成编号条目，容错多种常见格式。"""
    lines = text.splitlines()
    items = []
    current = None
    seen = set()

    def flush():
        nonlocal current
        if current is not None:
            body = " ".join(x for x in current["lines"] if x).strip()
            if body:
                current["text"] = body
                items.append(current)
            current = None

    comment_hdr = re.compile(
        r'^\s*#{1,6}\s*(?:Comment|意见|评论)\s*(?:ID\s*)?\s*[:：#]?\s*(\d+)',
        re.IGNORECASE)
    rid = re.compile(
        r'^\s*(?:\[\s*)?R\s*(\d+)\s*[-_.:：]?\s*C\s*(\d+)\s*(?:\]\s*)?[:：\-]?\s*(.*)$',
        re.IGNORECASE)
    numbered = re.compile(r'^\s*(\d{1,3})\s*[\.\)、:：]\s+(.*)$')

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = comment_hdr.match(line)
        if m:
            flush()
            cid = _dedupe(seen, "C" + m.group(1))
            rest = line[m.end():].strip()
            rest = re.sub(r'^[:：\-]\s*', '', rest)
            current = {"id": cid, "raw_id": m.group(1), "line": i + 1,
                       "lines": [rest] if rest else []}
            i += 1
            continue
        m = rid.match(line)
        if m:
            flush()
            cid = _dedupe(seen, "R%s-C%s" % (m.group(1), m.group(2)))
            current = {"id": cid, "raw_id": "R%s-C%s" % (m.group(1), m.group(2)),
                       "line": i + 1, "lines": [m.group(3).strip()]}
            i += 1
            continue
        m = numbered.match(line)
        if m:
            flush()
            cid = _dedupe(seen, "C" + m.group(1))
            current = {"id": cid, "raw_id": m.group(1), "line": i + 1,
                       "lines": [m.group(2).strip()]}
            i += 1
            continue
        if current is not None:
            current["lines"].append(line.strip())
        i += 1
    flush()

    # 无任何标记时，按非空段落兜底拆分
    if not items:
        para = []
        start = 1
        for idx, line in enumerate(lines, 1):
            if line.strip():
                if not para:
                    start = idx
                para.append(line.strip())
            elif para:
                cid = _dedupe(seen, "C%d" % (len(items) + 1))
                items.append({"id": cid, "raw_id": str(len(items) + 1),
                              "line": start, "text": " ".join(para)})
                para = []
        if para:
            cid = _dedupe(seen, "C%d" % (len(items) + 1))
            items.append({"id": cid, "raw_id": str(len(items) + 1),
                          "line": start, "text": " ".join(para)})

    for it in items:
        it.pop("lines", None)
        it["hint_type"] = classify(it.get("text", ""), TYPE_HINTS)
        it["hint_severity"] = classify(it.get("text", ""), SEVERITY_HINTS)
    return items


def slugify(s):
    s = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', s.strip().lower())
    s = s.strip('-')
    return s or "sec"


def split_sections(text):
    lines = text.splitlines()
    heading_re = re.compile(r'^(#{1,6})\s+(.*)$')
    sections = []
    current = None
    for i, line in enumerate(lines, 1):
        m = heading_re.match(line)
        if m:
            if current:
                sections.append(current)
            level = len(m.group(1))
            heading = m.group(2).strip()
            current = {"id": "sec-%d" % len(sections), "slug": slugify(heading),
                       "level": level, "heading": heading,
                       "line_start": i, "line_end": i}
        else:
            if current is None:
                current = {"id": "sec-0", "slug": "preamble", "level": 0,
                           "heading": "", "line_start": 1, "line_end": 1}
            current["line_end"] = i
    if current:
        sections.append(current)
    return sections


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    a = ap.parse_args()

    out = os.path.abspath(a.output)
    work = os.path.abspath(a.work)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    t0 = time.time()
    errors = []
    degradations = []

    info = discover(a.input)
    errors += [{"item": e, "reason": "discover"} for e in info["errors"]]

    # 读 case
    case_id = None
    case_raw = None
    if info["case_path"]:
        case_raw, err = load_json_safe(info["case_path"])
        if err:
            errors.append({"item": info["case_path"], "reason": err})
        elif isinstance(case_raw, dict):
            case_id = case_raw.get("case_id") or case_raw.get("caseId") or \
                case_raw.get("id") or None
    else:
        errors.append({"item": "case.json", "reason": "not found"})

    # 读 task.md
    task_text = None
    if info["task_path"]:
        task_text, err = read_text(info["task_path"])
        if err:
            degradations.append({"item": info["task_path"], "reason": err})

    # 读 materials
    comments_text = None
    baseline_text = None
    comments_path = None
    baseline_path = None
    sources = []
    for p in info["materials"]:
        base = os.path.basename(p)
        rel = os.path.relpath(p, a.input).replace("\\", "/")
        low = base.lower()
        text, err = read_text(p)
        if err and text is None:
            degradations.append({"item": rel, "reason": err})
            sources.append({"id": "src-%d" % len(sources), "type": "material",
                            "ref": rel, "description": "unreadable"})
            continue
        if err:
            degradations.append({"item": rel, "reason": err})
        if low.startswith("reviewer") or "comment" in low or "review" in low or \
           "意见" in base:
            comments_text = text
            comments_path = rel
        elif low.startswith("manuscript") or "baseline" in low or "基线" in base:
            baseline_text = text
            baseline_path = rel
        else:
            sources.append({"id": "src-%d" % len(sources), "type": "material",
                            "ref": rel, "description": "additional material"})

    # 意见拆解
    comments = parse_comments(comments_text) if comments_text else []

    # 章节索引
    sections = split_sections(baseline_text) if baseline_text else []
    baseline_hash = (hashlib.sha256(baseline_text.encode("utf-8")).hexdigest()[:12]
                     if baseline_text else None)

    # 台账骨架
    ledger = {
        "version_id": 1,
        "case_id": case_id,
        "generated_at": now_ts(),
        "manuscript_baseline_hash": baseline_hash,
        "entries": [
            {
                "comment_id": c["id"],
                "comment_type": None,
                "severity": None,
                "hint_type": c.get("hint_type"),
                "hint_severity": c.get("hint_severity"),
                "comment_text": c.get("text", ""),
                "source_line": c.get("line"),
                "sub_requirements": [],
                "decision": None,
                "tone": None,
                "response_summary": "",
                "changes": [],
                "evidence": {"needed": False, "status": None, "source": None,
                             "cannot_do_reason": None},
                "status": "pending",
            }
            for c in comments
        ],
    }

    # 修订稿：复制基线为可编辑起点
    if baseline_text is not None:
        header = ("<!-- 本文件由基线快照生成，供逐条增量修订；\n"
                  "     请勿删除或改写各条修订锚点对应的原文，未改处保持与基线一致 -->\n\n")
        with open(os.path.join(out, "revised-manuscript.md"), "w",
                  encoding="utf-8") as f:
            f.write(header + baseline_text)
    else:
        with open(os.path.join(out, "revised-manuscript.md"), "w",
                  encoding="utf-8") as f:
            f.write("<!-- 基线缺失，无法生成修订稿；见 result.json 的 material_gaps -->\n")

    # 回复信骨架：按意见编号预置标题，结构上保证逐条覆盖
    rb = ["# 回复信草案（Reviewer Response）\n",
          "<!-- 本文件为骨架；逐条填写决策/回应/修改定位，勿删除任何条目标题 -->\n",
          "\n## 总体回应\n（待填写：本轮总体立场与关键修改概述）\n"]
    for c in comments:
        rb.append("\n## 意见 %s\n" % c["id"])
        rb.append("- 决策：待填写（accept / partial / reject）\n")
        rb.append("- 语气：待填写（accept / clarify / rebut / appeal）\n")
        rb.append("- 回应：待填写（逐条，不空话）\n")
        rb.append("- 修改定位：待填写（章节 + 段落 + 原文锚点）\n")
    rb.append("\n## 未到位证据清单\n（待填写：需补实验/数据/文献/重分析项，含到位状态或无法补做原因）\n")
    with open(os.path.join(out, "response-letter.md"), "w", encoding="utf-8") as f:
        f.write("".join(rb))

    # material_gaps 自动检测
    gaps = []
    if not info["case_path"]:
        gaps.append({"item": "case.json", "reason": "missing"})
    if not info["task_path"]:
        gaps.append({"item": "task.md", "reason": "missing"})
    if comments_text is None:
        gaps.append({"item": "materials/reviewer-comments", "reason": "not found"})
    if baseline_text is None:
        gaps.append({"item": "materials/manuscript-baseline", "reason": "not found"})

    result = {
        "case_id": case_id,
        "status": "partial",
        "deliverable": {
            "revised_manuscript": {"path": os.path.join(out, "revised-manuscript.md"),
                                   "format": "markdown", "status": "pending"},
            "response_letter": {"path": os.path.join(out, "response-letter.md"),
                                "format": "markdown", "status": "pending"},
            "revision_ledger": {"path": os.path.join(out, "revision-ledger.json"),
                                "format": "json", "status": "pending"},
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "version_id": 1,
            "changes": [],
            "pending_confirmation": [],
            "material_gaps": gaps,
            "anomalies": [],
        },
        "sources": [{"id": "src-case", "type": "case", "ref": "case.json",
                     "description": "case 元数据"},
                    {"id": "src-task", "type": "task", "ref": "task.md",
                     "description": "任务说明"}]
                   + ([{"id": "src-comments", "type": "comments",
                        "ref": comments_path or "materials/reviewer-comments.md",
                        "description": "审稿意见摘要"}]
                      if comments_path else [])
                   + ([{"id": "src-baseline", "type": "baseline",
                        "ref": baseline_path or "materials/manuscript-baseline.md",
                        "description": "基线摘要"}]
                      if baseline_path else [])
                   + sources,
        "uncertainty": [],
        "change_log": [{"ts": now_ts(), "action": "scaffold",
                        "detail": "parsed %d comments, %d sections; wrote partial skeleton"
                        % (len(comments), len(sections))}],
        "errors": errors,
        "degradations": degradations,
        "runtime": round(time.time() - t0, 3),
    }

    if case_id is None and (info["case_path"] is None):
        result["status"] = "insufficient_evidence" if not info["materials"] \
            else "partial"

    write_json(os.path.join(out, "result.json"), result)
    write_json(os.path.join(out, "revision-ledger.json"), ledger)
    write_json(os.path.join(work, "comments.json"),
               {"case_id": case_id, "comments": comments})
    write_json(os.path.join(work, "sections.json"),
               {"case_id": case_id, "baseline_hash": baseline_hash,
                "sections": sections})

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "n_comments": len(comments),
        "n_sections": len(sections),
        "status": result["status"],
        "errors": len(errors),
        "degradations": len(degradations),
        "output": out,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
