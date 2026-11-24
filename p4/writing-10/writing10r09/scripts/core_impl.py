#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — 读取 /input 下 case.json、task.md 与 materials/，立即写出一版合法 result.json 骨架。

设计目标：只依赖 Python 标准库；任何输入缺失 / 坏 JSON / 坏编码都不崩溃，始终写出合法
result.json 并给出诚实 status；3 分钟内可调用，作为后续增量改进的起点（先 partial 后完善）。

用法：
    python scripts/scaffold.py --case /input/case.json --task /input/task.md \
        --out /app/output --work /app/work
"""
import argparse
import json
import os
import re
import sys
import time

ALLOWED_STATUS = {"in_progress", "complete", "partial", "failed", "insufficient_evidence", "input_error"}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def read_json(path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def size_ok(path, limit=2 * 1024 * 1024):
    try:
        return os.path.getsize(path) <= limit
    except Exception:
        return True


def discover(root):
    """返回 (case_path, task_path, material_files)。仅扫描一层与 materials/，不递归全盘。"""
    case_path = task_path = None
    materials = []
    if not root or not os.path.isdir(root):
        return case_path, task_path, materials
    try:
        entries = os.listdir(root)
    except Exception:
        return case_path, task_path, materials
    for name in entries:
        p = os.path.join(root, name)
        low = name.lower()
        if os.path.isfile(p):
            if low == "case.json" or (low.endswith(".json") and "case" in low):
                case_path = case_path or p
            elif low == "task.md" or low.endswith("task.md"):
                task_path = task_path or p
        elif os.path.isdir(p) and low == "materials":
            for sub, _, files in os.walk(p):
                for fn in files:
                    if fn.lower().endswith((".md", ".txt", ".json", ".tex", ".pdf", ".docx")):
                        materials.append(os.path.join(sub, fn))
    return case_path, task_path, materials


COMMENT_RE = re.compile(
    r'^\s*(?:意见|评论|审稿(?:人)?\s*意见|comment|reviewer|review|referee|editor|major|minor)\s*'
    r'[#：:．.\-]?\s*(\d{1,3})?\s*[.、:：)）]?\s*(.*)$',
    re.IGNORECASE,
)


def split_comments(text):
    """尽力把意见摘要拆成条目，仅作骨架种子；深度拆分由 Agent 完成。"""
    if not text:
        return []
    items = []
    cur = None
    matched = False
    for ln in text.splitlines():
        m = COMMENT_RE.match(ln)
        if m and (m.group(1) or m.group(2).strip()):
            matched = True
            cid = (m.group(1) or str(len(items) + 1)).strip()
            cur = {"id": cid, "raw": (m.group(2) or "").strip()}
            items.append(cur)
        elif cur is not None:
            cur["raw"] = (cur["raw"] + "\n" + ln).strip()
    if not items:
        items = [{"id": "1", "raw": text.strip()}]
    # 去掉只含空白的条目
    items = [it for it in items if it.get("raw")]
    if not items:
        items = [{"id": "1", "raw": text.strip()[:2000]}]
    return items


def classify_material(path):
    base = os.path.basename(path).lower()
    if "comment" in base or "review" in base or "referee" in base:
        return "comments"
    if "manuscript" in base or "baseline" in base or "draft" in base or "paper" in base or "abstract" in base:
        return "baseline"
    return "other"


def build(case_path, task_path, material_paths, out_dir, work_dir):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case = read_json(case_path) if case_path else None
    case_id = ""
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or ""
    if not case_id and case_path:
        case_id = os.path.splitext(os.path.basename(case_path))[0]

    comments_raw = ""
    baseline_raw = ""
    sources = []
    material_gaps = []
    if case_path:
        sources.append({"id": "case", "type": "json", "ref": case_path, "description": "case metadata & constraints"})
    if task_path:
        sources.append({"id": "task", "type": "markdown", "ref": task_path, "description": "task description & scoring dimensions"})

    for p in material_paths:
        base = os.path.basename(p)
        if not size_ok(p):
            material_gaps.append({"item": "material skipped (size > 2MB)", "path": p})
            sources.append({"id": "mat-" + os.path.splitext(base)[0], "type": "material", "ref": p, "description": base + " (skipped, too large)"})
            continue
        kind = classify_material(p)
        text = read_text(p)
        if text is None:
            material_gaps.append({"item": "material unreadable", "path": p, "kind": kind})
            text = ""
        else:
            if kind == "comments":
                comments_raw = comments_raw or text
            elif kind == "baseline":
                baseline_raw = baseline_raw or text
            else:
                material_gaps.append({"item": "material read but not auto-classified", "path": p, "kind": kind})
        sources.append({"id": "mat-" + os.path.splitext(base)[0], "type": "material", "ref": p, "description": base + " (kind=%s)" % kind})

    parsed_comments = split_comments(comments_raw)

    if not baseline_raw and not comments_raw:
        status = "insufficient_evidence"
    else:
        status = "in_progress"

    result = {
        "case_id": case_id,
        "status": status,
        "deliverable": {
            "path": os.path.join(out_dir, "revised_manuscript.md"),
            "format": "markdown",
            "title": "修订稿（评审意见基线改写）",
            "status": "draft",
            "files": [
                {"role": "revised_manuscript", "path": os.path.join(out_dir, "revised_manuscript.md"), "format": "markdown"},
                {"role": "response_letter", "path": os.path.join(out_dir, "response_letter.md"), "format": "markdown"},
                {"role": "revision_ledger", "path": os.path.join(out_dir, "revision_ledger.json"), "format": "json"},
                {"role": "evidence_checklist", "path": os.path.join(out_dir, "evidence_checklist.md"), "format": "markdown"},
            ],
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [],
            "pending_human": [],
            "material_gaps": material_gaps,
            "anomalies": [],
        },
        "comments": parsed_comments,
        "revision_ledger": [],
        "sources": sources,
        "uncertainty": [],
        "change_log": [
            {"ts": now_ts(), "action": "scaffold", "detail": "result.json skeleton written; status=%s" % status}
        ],
        "runtime": {"created": now_ts(), "script": "scaffold.py"},
    }

    out_path = os.path.join(out_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return out_path, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args()

    root = os.path.dirname(os.path.abspath(a.case)) if a.case else "/input"
    case_path, task_path, materials = discover(root)
    case_path = case_path or (a.case if os.path.exists(a.case) else None)
    task_path = task_path or (a.task if os.path.exists(a.task) else None)

    try:
        out_path, _ = build(case_path, task_path, materials, a.out, a.work)
        print(json.dumps({"ok": True, "result": out_path, "status": "written"}, ensure_ascii=False))
    except Exception as e:  # 兜底：写最小合法产物，不抛异常
        try:
            os.makedirs(a.out, exist_ok=True)
            minimal = {
                "case_id": "",
                "status": "input_error",
                "deliverable": {"path": "", "format": "markdown", "title": "", "status": "failed", "files": []},
                "fact_source_mapping": [],
                "version_boundary": {
                    "changes": [], "pending_human": [],
                    "material_gaps": [{"item": "scaffold crashed", "path": "", "detail": str(e)}],
                    "anomalies": [],
                },
                "sources": [], "uncertainty": [],
                "change_log": [{"ts": now_ts(), "action": "scaffold_failed", "detail": str(e)}],
            }
            with open(os.path.join(a.out, "result.json"), "w", encoding="utf-8") as f:
                json.dump(minimal, f, ensure_ascii=False, indent=2)
            print(json.dumps({"ok": False, "error": str(e), "result": os.path.join(a.out, "result.json")}, ensure_ascii=False))
        except Exception:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
