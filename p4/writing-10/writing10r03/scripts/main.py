#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审稿回复 Skill 入口：发现输入 -> 解析意见 -> 落盘 result.json 骨架（确定性）。

用法：
  python scripts/main.py --case /input/case.json --task /input/task.md \
      --out /app/output --work /app/work

只依赖标准库；同一输入多次运行产出的骨架内容一致（不含墙钟时间）。
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 题目 output_contract 的三个字段（与 task.md 一致，不可改名/删键）
OUTPUT_CONTRACT_FIELDS = ("deliverable", "fact_source_mapping", "version_boundary")
SCHEMA_VERSION = "1.0.0"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件上限 2MB，超限跳过并记录

NUM_RE = re.compile(r"^\s*\(?\[?\s*(\d{1,3})\s*\]?\)?\s*[.)、:：\-]\s*(.*)$")
HEAD_RE = re.compile(r"^\s*(#{1,6})\s*(.*)$")
# group1=英文关键词, group2=中文关键词, group3=编号, group4=其余
KEY_RE = re.compile(
    r"^\s*(?:(comment|reviewer|referee|review)|(意见|审稿意见|评论))"
    r"\s*#?\s*(\d{1,3})?\s*[:：.、\-]?\s*(.*)$"
)


def read_text(path):
    """utf-8-sig 容错读取；坏编码/超大文件/IO 错误都不崩溃，返回空串。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size > MAX_FILE_BYTES:
        return ""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def discover_input(input_dir, exclude_dirs=None):
    """递归扫描输入目录，不假设固定文件名/键名。返回 {case, task, materials, errors}。

    exclude_dirs：需排除的绝对路径（如 /app/output、/app/work），避免把产物当材料。
    """
    found = {"case": None, "task": None, "materials": [], "errors": []}
    if not os.path.isdir(input_dir):
        found["errors"].append("input dir missing: %s" % input_dir)
        return found
    exclude_dirs = [os.path.abspath(d) for d in (exclude_dirs or [])]

    def _excluded(p):
        pabs = os.path.abspath(p)
        for d in exclude_dirs:
            try:
                if os.path.commonpath([pabs, d]) == d:
                    return True
            except ValueError:
                pass
        return False

    for dirpath, dirnames, filenames in os.walk(input_dir):
        dirnames[:] = [d for d in dirnames if not _excluded(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _excluded(full):
                continue
            low = name.lower()
            if low == "case.json" or (low.endswith(".json") and "case" in low):
                if found["case"] is None:
                    found["case"] = full
            elif low == "task.md" or (low.endswith(".md") and "task" in low):
                if found["task"] is None:
                    found["task"] = full
            elif low.endswith((".md", ".txt", ".pdf", ".tex")):
                found["materials"].append(full)
    return found


def load_case(path):
    """解析 case.json；坏 JSON 记录错误后返回空 dict，不崩溃。返回 (obj, error|None)。"""
    if not path or not os.path.isfile(path):
        return {}, "case.json missing"
    text = read_text(path)
    if not text:
        return {}, "case.json empty or unreadable"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return {}, "bad json: %s" % exc


def _is_start(line):
    """判定一行是否为一条意见的起点（编号 / 带编号的关键词 / 带数字的标题）。"""
    s = line.strip()
    if not s:
        return False
    m = NUM_RE.match(s)
    if m and len(m.group(1)) <= 3:
        return True
    m = KEY_RE.match(s)
    if m and (m.group(1) or m.group(2)) and m.group(3):
        return True
    m = HEAD_RE.match(s)
    if m and re.search(r"\d", m.group(2)) and re.search(
        r"(?i)comment|reviewer|referee|意见|审稿|评论", m.group(2)
    ):
        return True
    return False


def parse_reviewer_comments(text):
    """确定性拆解意见：识别编号/关键词起止行。

    返回 (blocks, detected_count)：blocks 为 [(comment_id, raw), ...]；
    detected_count 为识别到的起始行数，0 表示退化为单条 fallback（C1）。
    """
    if not text or not text.strip():
        return [], 0
    lines = text.splitlines()
    starts = []
    for i, line in enumerate(lines):
        if not _is_start(line):
            continue
        cid = None
        m = NUM_RE.match(line.strip())
        if m and len(m.group(1)) <= 3:
            cid = "C" + m.group(1)
        else:
            m = KEY_RE.match(line.strip())
            if m and m.group(3):
                cid = "C" + m.group(3)
        starts.append((i, cid))
    # 按行号去重，保持顺序
    seen = set()
    dedup = []
    for idx, cid in starts:
        if idx in seen:
            continue
        seen.add(idx)
        dedup.append((idx, cid))
    if not dedup:
        return [("C1", text.strip())], 0
    blocks = []
    used = {}
    for k, (idx, cid) in enumerate(dedup):
        end = dedup[k + 1][0] if k + 1 < len(dedup) else len(lines)
        raw = "\n".join(lines[idx:end]).strip()
        if not cid:
            cid = "C%d" % (k + 1)
        base = cid
        n = used.get(base, 0) + 1
        used[base] = n
        if n > 1:
            cid = "%s.%d" % (base, n)
        blocks.append((cid, raw))
    return blocks, len(dedup)


def build_skeleton(case_id, comment_blocks, source_refs, parse_note, errors):
    """构建 result.json 骨架；同一输入产生相同内容（确定性）。"""
    deliverable = {
        "path": "revised-manuscript.md",
        "format": "markdown",
        "title": "",
        "sections": [],
        "status": "in_progress",
        "previews": [],
    }
    fact_source_mapping = []
    for cid, raw in comment_blocks:
        first_line = (raw.splitlines() or [""])[0][:200]
        fact_source_mapping.append({
            "claim": "审稿意见 %s 已识别并进入台账" % cid,
            "source_ref": "materials/reviewer-comments.md",
            "location": "comment block %s" % cid,
            "quote": first_line,
            "status": "traced",
        })
    version_boundary = {
        "changes": [],
        "pending_confirmation": [],
        "material_gaps": [],
        "anomalies": [],
    }
    if not source_refs:
        version_boundary["material_gaps"].append(
            "未发现 materials 材料；稿件基线/意见清单缺失，须人工补充"
        )
    response_ledger = []
    for cid, raw in comment_blocks:
        response_ledger.append({
            "comment_id": cid,
            "sub_requirements": [],
            "type": "unclassified",
            "severity": "unclassified",
            "disposition": "pending",
            "tone": "neutral",
            "action": "",
            "locator": None,
            "evidence": None,
            "status": "pending",
            "quote": (raw.splitlines() or [""])[0][:200],
        })
    sources = [
        {"id": "src-%d" % (i + 1), "type": "material", "ref": ref,
         "description": "输入材料"} for i, ref in enumerate(source_refs)
    ]
    skeleton = {
        "case_id": case_id,
        "deliverable": deliverable,
        "fact_source_mapping": fact_source_mapping,
        "version_boundary": version_boundary,
        "response_ledger": response_ledger,
        "status": "in_progress",
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": [e for e in errors],
        "degradations": [],
        "change_log": [
            {"step": 0, "action": "scaffold",
             "detail": "落盘结果骨架（status=in_progress，意见台账已预填）", "ts": ""}
        ],
        "runtime": {
            "budget_seconds": 900,
            "budget_requests": 64,
            "schema_version": SCHEMA_VERSION,
            "content_hash": "",
        },
    }
    if parse_note:
        skeleton["uncertainty"].append({
            "item": "意见自动拆解",
            "reason": parse_note,
            "disposition": "人工复核台账边界后覆盖",
        })
    # 内容指纹：除 runtime 外的全部字段的 sha256（列表顺序即构建顺序，确定性）
    payload = {k: v for k, v in skeleton.items() if k != "runtime"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    skeleton["runtime"]["content_hash"] = digest
    return skeleton


def main(argv=None):
    ap = argparse.ArgumentParser(description="审稿回复 Skill：落盘 result.json 骨架")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    found = discover_input(a.input, exclude_dirs=[a.out, a.work])
    case_path = a.case if os.path.isfile(a.case) else found["case"]
    task_path = a.task if os.path.isfile(a.task) else found["task"]
    errors = list(found["errors"])

    case_id = ""
    if case_path:
        case_obj, err = load_case(case_path)
        if err:
            errors.append(err)
        case_id = str(case_obj.get("case_id") or case_obj.get("id") or "")
    task_text = read_text(task_path) if task_path else ""
    if not case_id and task_text:
        m = re.search(r"challenge[-_]?[0-9A-Za-z]+", task_text)
        if m:
            case_id = m.group(0)
        else:
            m = re.search(r"case[-_]?[0-9A-Za-z]+", task_text)
            if m:
                case_id = m.group(0)
    if not case_id:
        case_id = "unknown"

    reviewer_text = ""
    baseline_text = ""
    for m in found["materials"]:
        base = os.path.basename(m).lower()
        if re.search(r"review|comment|意见|审稿", base):
            reviewer_text = reviewer_text or read_text(m)
        if re.search(r"manuscript|baseline|稿件|draft|revision", base):
            baseline_text = baseline_text or read_text(m)

    parse_note = None
    if reviewer_text:
        comment_blocks, detected = parse_reviewer_comments(reviewer_text)
        if detected == 0:
            parse_note = "未识别到编号/关键词意见条目，已将全文视为单条 C1"
    else:
        comment_blocks = []
        parse_note = "未发现 reviewer-comments 材料；意见台账为空，需人工补充"

    source_refs = [os.path.relpath(m, a.input).replace("\\", "/")
                   for m in sorted(found["materials"])]
    skeleton = build_skeleton(case_id, comment_blocks, source_refs, parse_note, errors)

    out_path = os.path.join(a.out, "result.json")
    tmp_path = out_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, out_path)
    except OSError as exc:
        # 降级：重试直接写一次；再失败仅打印错误，不崩溃
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(skeleton, f, ensure_ascii=False, indent=2)
        except OSError as exc2:
            print(json.dumps({"ok": False, "case_id": case_id,
                              "out": out_path, "error": str(exc2)},
                             ensure_ascii=False))
            return 1

    print(json.dumps({
        "ok": True, "case_id": case_id, "out": out_path,
        "comments": len(comment_blocks), "materials": len(found["materials"]),
        "content_hash": skeleton["runtime"]["content_hash"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
