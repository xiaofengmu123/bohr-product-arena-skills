#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 对 /app/output/result.json 及其产物做自检。

检查维度：契约完整性、意见覆盖、修改定位可核验、版本一致性、诚实性标记。
只依赖标准库；始终以 exit 0 结束并输出 JSON 报告，硬问题体现在 checks[].pass=False 与 ok=False。

用法：
    python scripts/selftest.py --result /app/output/result.json --out /app/output
"""
import argparse
import json
import os
import re
import sys

ALLOWED_STATUS = {"in_progress", "complete", "partial", "failed", "insufficient_evidence", "input_error"}
DECISIONS = {"accept", "partial", "reject", "not_applicable"}
TONES = {"accept", "clarify", "polite-rebut", "appeal"}
REQUIRED_TOP = [
    "case_id", "status", "deliverable", "fact_source_mapping", "version_boundary",
    "sources", "uncertainty", "change_log",
]
OVERCLAIM_RE = re.compile(
    r'(已补做|已完成(?:实验|分析|仿真)|实验已完成|已获得(?:新)?数据|已联网核验|已录用|已接收|已见刊|已发表)',
    re.IGNORECASE,
)


def read_text(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def load_result(path):
    if not os.path.exists(path):
        return None, "result.json not found: %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return json.load(f), None
    except Exception as e:
        return None, "result.json unreadable: %s" % e


def find_file(out_dir, role_or_name):
    candidates = []
    for fn in os.listdir(out_dir) if os.path.isdir(out_dir) else []:
        low = fn.lower()
        if role_or_name.lower() in low:
            candidates.append(os.path.join(out_dir, fn))
    return candidates


def check_anchor_in_text(anchor, text):
    if not anchor or not text:
        return False
    a = re.sub(r'\s+', '', anchor)
    t = re.sub(r'\s+', '', text)
    return a and a in t


def run(result_path, out_dir):
    result, err = load_result(result_path)
    checks = []

    if err:
        checks.append({"name": "result_json_load", "pass": False, "detail": err})
        report = {"ok": False, "case_id": None, "checks": checks, "summary": "result.json 无法读取"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # 1. 契约完整性
    missing = [k for k in REQUIRED_TOP if k not in result]
    checks.append({
        "name": "contract_keys",
        "pass": not missing,
        "detail": "缺少字段: %s" % ", ".join(missing) if missing else "顶层字段齐全",
    })
    checks.append({
        "name": "case_id",
        "pass": bool(result.get("case_id")),
        "detail": "case_id=%r" % result.get("case_id"),
    })
    checks.append({
        "name": "status_allowed",
        "pass": result.get("status") in ALLOWED_STATUS,
        "detail": "status=%r" % result.get("status"),
    })

    deliverable = result.get("deliverable") or {}
    files = deliverable.get("files") or []
    checks.append({
        "name": "deliverable_files_list",
        "pass": isinstance(files, list) and len(files) > 0,
        "detail": "deliverable.files 条目数=%d" % len(files),
    })

    # 2. 产物存在性
    exist_fail = []
    for f in files:
        p = f.get("path") if isinstance(f, dict) else None
        if p and not os.path.exists(p):
            exist_fail.append(os.path.basename(p))
    checks.append({
        "name": "artifact_files_exist",
        "pass": not exist_fail,
        "detail": "缺失产物: %s" % ", ".join(exist_fail) if exist_fail else "主要产物文件均存在",
    })

    # 3. 意见覆盖：每条 comments 是否在 revision_ledger 中有对应条目
    comments = result.get("comments") or []
    ledger = result.get("revision_ledger") or []
    comment_ids = set()
    for c in comments:
        if isinstance(c, dict) and c.get("id"):
            comment_ids.add(str(c["id"]))
    ledger_ids = set()
    for e in ledger:
        if isinstance(e, dict) and e.get("comment_id") is not None:
            ledger_ids.add(str(e["comment_id"]))
    uncovered = sorted(comment_ids - ledger_ids) if comment_ids else []
    checks.append({
        "name": "comment_coverage",
        "pass": (not comment_ids) or (not uncovered),
        "detail": "意见条目=%d，台账已覆盖=%d，未覆盖=%s" % (len(comment_ids), len(ledger_ids), uncovered or "无"),
    })

    # 4. 修改定位可核验：accept/partial 且 status 表示已改的条目，anchor/after 必须出现在修订稿
    revised_texts = {}
    for f in files:
        role = (f.get("role") or "").lower() if isinstance(f, dict) else ""
        if "manuscript" in role or "revised" in role:
            t = read_text(f.get("path"))
            if t:
                revised_texts[f.get("path")] = t
    loc_fail = []
    checked_locs = 0
    for e in ledger:
        if not isinstance(e, dict):
            continue
        decision = str(e.get("decision") or "").lower()
        status = str(e.get("status") or "").lower()
        if decision not in ("accept", "partial"):
            continue
        if status not in ("done", "modified", "implemented"):
            continue
        loc = e.get("location") or {}
        anchor = None
        if isinstance(loc, dict):
            anchor = loc.get("anchor") or loc.get("after") or loc.get("quote")
        after = e.get("after")
        anchor = anchor or after
        if not anchor:
            loc_fail.append("comment %s: 已改但无定位锚点" % e.get("comment_id"))
            continue
        checked_locs += 1
        found = False
        for p, t in revised_texts.items():
            if check_anchor_in_text(anchor, t):
                found = True
                break
        if not found:
            loc_fail.append("comment %s: 锚点未在修订稿中找到 -> %s" % (e.get("comment_id"), str(anchor)[:80]))
    checks.append({
        "name": "locator_verifiable",
        "pass": not loc_fail,
        "detail": "已核对定位 %d 条，失败 %d：%s" % (checked_locs, len(loc_fail), "；".join(loc_fail[:5]) if loc_fail else "全部可定位"),
    })

    # 5. 事实与来源映射
    fsm = result.get("fact_source_mapping") or []
    bad_fsm = [i for i, m in enumerate(fsm) if not isinstance(m, dict) or not m.get("claim")]
    checks.append({
        "name": "fact_source_mapping",
        "pass": isinstance(fsm, list) and not bad_fsm,
        "detail": "映射条目=%d，缺 claim=%d" % (len(fsm), len(bad_fsm)),
    })

    # 6. 诚实性：回复信/修订稿/证据清单不得出现未支持的"已补做/已录用"等越界表述
    over = []
    for role_name in ("response", "revised", "evidence", "manuscript"):
        for p in find_file(out_dir, role_name):
            t = read_text(p) or ""
            hits = OVERCLAIM_RE.findall(t)
            if hits:
                over.append("%s:%s" % (os.path.basename(p), "、".join(sorted(set(hits))[:3])))
    checks.append({
        "name": "no_fabricated_claims",
        "pass": not over,
        "detail": "越界表述: %s" % "；".join(over) if over else "未发现未支持的越界表述",
    })

    # 7. 版本边界字段
    vb = result.get("version_boundary") or {}
    vb_ok = isinstance(vb, dict) and all(k in vb for k in ("changes", "pending_human", "material_gaps", "anomalies"))
    checks.append({
        "name": "version_boundary_keys",
        "pass": vb_ok,
        "detail": "version_boundary 键=%s" % (sorted(vb.keys()) if isinstance(vb, dict) else type(vb).__name__),
    })

    ok = all(c["pass"] for c in checks)
    print(json.dumps({
        "ok": ok,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "checks": checks,
        "summary": "自检通过" if ok else "存在需修正项",
    }, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--out", default="/app/output")
    a = ap.parse_args()
    run(a.result, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
