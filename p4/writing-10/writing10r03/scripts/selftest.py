#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审稿回复 Skill 自检脚本（只依赖标准库）。

校验项（对应评分维度）：
  C1 输出契约：output_contract 三字段 + case_id/status/sources/uncertainty/change_log 齐备
  C2 意见覆盖（答得全）：每条源意见在 response_ledger 有且仅有一条对应条目
  C3 定位可核验（改得准/对得上）：accept/partial 且已修改条目的改后锚点能在修改稿中被检索
  C4 证据（证得住）：需补证据条目有 evidence.status；"无法补做"必须有 reason
  C5 事实映射（可回溯）：每条 claim 有 source_ref；traced 项必须有逐字 quote
  C6 版本边界（对得上）：version_boundary.changes 与台账 comment_id 一一对应
  C7 确定性：同一输入两次构建骨架 canonical 后一致；content_hash 与重算一致

用法：
  python scripts/selftest.py --input /input --out /app/output [--baseline materials/manuscript-baseline.md]
  python scripts/selftest.py --demo    # 用临时合成 fixture 验证脚本可运行，退出码 0
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import main as M  # noqa: E402

VOLATILE_KEYS = {"ts", "timestamp", "start_ts", "end_ts", "finished_ts",
                 "elapsed", "duration", "created_at", "updated_at"}
DONE_STATUS = {"done", "complete", "completed", "revised", "modified", "已修改",
               "已补充", "到位"}


def _msg(level, code, text):
    return {"level": level, "code": code, "text": text}


def load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def check_contract(r):
    msgs = []
    for f in M.OUTPUT_CONTRACT_FIELDS:
        if f not in r:
            msgs.append(_msg("FAIL", "contract." + f, "缺少 output_contract 字段 %s" % f))
        elif r[f] is None:
            msgs.append(_msg("WARN", "contract." + f, "字段 %s 为 null" % f))
    for f in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if f not in r:
            msgs.append(_msg("FAIL", "contract." + f, "缺少必需字段 %s" % f))
    return msgs


def check_fact_mapping(r):
    msgs = []
    fsm = r.get("fact_source_mapping", [])
    if not isinstance(fsm, list) or not fsm:
        msgs.append(_msg("WARN", "mapping.empty", "fact_source_mapping 为空"))
        return msgs
    for i, item in enumerate(fsm):
        if not isinstance(item, dict):
            msgs.append(_msg("FAIL", "mapping.item", "第 %d 项非对象" % i))
            continue
        if not item.get("claim"):
            msgs.append(_msg("FAIL", "mapping.claim", "第 %d 项缺 claim" % i))
        if not item.get("source_ref"):
            msgs.append(_msg("WARN", "mapping.ref", "第 %d 项缺 source_ref" % i))
        if item.get("status") == "traced" and not item.get("quote"):
            msgs.append(_msg("FAIL", "mapping.quote", "第 %d 项 status=traced 但缺逐字 quote" % i))
    return msgs


def check_coverage(r, comment_blocks):
    """答得全：源意见与台账一一对应。"""
    msgs = []
    src_ids = [cid for cid, _raw in comment_blocks]
    ledger = r.get("response_ledger", [])
    if not isinstance(ledger, list):
        return [_msg("FAIL", "ledger.missing", "response_ledger 缺失或非数组")]
    led_ids = [e.get("comment_id") for e in ledger if isinstance(e, dict)]
    missing = [i for i in src_ids if i not in led_ids]
    extra = [i for i in led_ids if i not in src_ids]
    dup = sorted({i for i in led_ids if led_ids.count(i) > 1})
    if missing:
        msgs.append(_msg("FAIL", "coverage.missing", "未回应的意见: %s" % ", ".join(missing)))
    if extra:
        msgs.append(_msg("WARN", "coverage.extra", "台账中存在源材料外的意见: %s" % ", ".join(extra)))
    if dup:
        msgs.append(_msg("FAIL", "coverage.dup", "台账条目重复: %s" % ", ".join(dup)))
    for e in ledger:
        if not isinstance(e, dict):
            continue
        raw = e.get("quote", "")
        if e.get("disposition") == "pending":
            msgs.append(_msg("WARN", "ledger.pending",
                             "%s 仍为 pending，尚未给出处置结论" % e.get("comment_id")))
        if (e.get("disposition") in ("accept", "partial") and not e.get("action")):
            msgs.append(_msg("FAIL", "ledger.action",
                             "%s 采纳/部分采纳但缺具体 action" % e.get("comment_id")))
        if raw and e.get("sub_requirements") in (None, []) and raw.count("\n") >= 1:
            msgs.append(_msg("WARN", "ledger.subreq",
                             "%s 可能含多个子要求但未拆解 sub_requirements" % e.get("comment_id")))
    return msgs


def check_locators(r, manuscript_text, baseline_text):
    """改得准/对得上：改后锚点能在修改稿中检索到。"""
    msgs = []
    if not manuscript_text:
        return [_msg("WARN", "locator.nomsg", "未找到修改稿文本，无法核验定位")]
    ledger = r.get("response_ledger", [])
    for e in ledger:
        if not isinstance(e, dict):
            continue
        loc = e.get("locator")
        if not isinstance(loc, dict):
            if e.get("disposition") in ("accept", "partial") and str(e.get("status", "")).lower() in DONE_STATUS:
                msgs.append(_msg("FAIL", "locator.missing",
                                 "%s 声称已修改但缺 locator" % e.get("comment_id")))
            continue
        anchor = loc.get("anchor_new") or loc.get("anchor") or ""
        if anchor and anchor not in manuscript_text:
            msgs.append(_msg("FAIL", "locator.notfound",
                             "%s 的改后锚点未在修改稿中找到: %s" % (e.get("comment_id"), anchor[:40])))
        old = loc.get("anchor_old", "")
        if old and baseline_text and old not in baseline_text:
            msgs.append(_msg("WARN", "locator.oldnotfound",
                             "%s 的原锚点未在基线中找到: %s" % (e.get("comment_id"), old[:40])))
        if not anchor:
            msgs.append(_msg("WARN", "locator.noanchor",
                             "%s 的 locator 缺少 anchor_new/anchor" % e.get("comment_id")))
    return msgs


def check_evidence(r):
    msgs = []
    ledger = r.get("response_ledger", [])
    for e in ledger:
        if not isinstance(e, dict):
            continue
        ev = e.get("evidence")
        if not isinstance(ev, dict):
            if e.get("disposition") in ("accept", "partial"):
                msgs.append(_msg("WARN", "evidence.missing",
                                 "%s 采纳但未提供 evidence 清单" % e.get("comment_id")))
            continue
        if not ev.get("status"):
            msgs.append(_msg("FAIL", "evidence.status", "%s 的 evidence 缺 status" % e.get("comment_id")))
        if ev.get("status") in ("无法补做", "not_feasible", "cannot", "无法"):
            if not ev.get("reason"):
                msgs.append(_msg("FAIL", "evidence.reason",
                                 "%s 标为无法补做但缺 reason" % e.get("comment_id")))
    return msgs


def check_version_boundary(r):
    msgs = []
    vb = r.get("version_boundary", {})
    if not isinstance(vb, dict):
        return [_msg("FAIL", "vb.missing", "version_boundary 缺失或非对象")]
    changes = vb.get("changes", [])
    ledger_ids = {e.get("comment_id") for e in r.get("response_ledger", []) if isinstance(e, dict)}
    for c in changes:
        if not isinstance(c, dict):
            msgs.append(_msg("FAIL", "vb.change", "changes 含非对象项"))
            continue
        cid = c.get("comment_id")
        if cid and cid not in ledger_ids:
            msgs.append(_msg("WARN", "vb.orphan",
                             "change 引用了台账外的 comment_id: %s" % cid))
    done = [e for e in r.get("response_ledger", [])
            if isinstance(e, dict) and e.get("disposition") in ("accept", "partial")
            and str(e.get("status", "")).lower() in DONE_STATUS]
    if done and not changes:
        msgs.append(_msg("WARN", "vb.empty", "台账有已修改条目但 version_boundary.changes 为空"))
    return msgs


def canonicalize(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in VOLATILE_KEYS or k in ("runtime", "change_log"):
                continue
            out[k] = canonicalize(v)
        return out
    if isinstance(obj, list):
        return [canonicalize(x) for x in obj]
    return obj


def check_determinism(case_id, comment_blocks, source_refs, parse_note, errors, r):
    msgs = []
    a = M.build_skeleton(case_id, comment_blocks, source_refs, parse_note, errors)
    b = M.build_skeleton(case_id, comment_blocks, source_refs, parse_note, errors)
    if canonicalize(a) != canonicalize(b):
        msgs.append(_msg("FAIL", "determinism.build", "同输入两次构建骨架不一致"))
    payload = {k: v for k, v in r.items() if k != "runtime"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    if r.get("runtime", {}).get("content_hash") != digest:
        msgs.append(_msg("FAIL", "determinism.hash", "content_hash 与重算不一致（内容被改动但未更新指纹）"))
    return msgs


def run_checks(r, comment_blocks, manuscript_text, baseline_text, case_id,
               source_refs, parse_note, errors):
    msgs = []
    msgs += check_contract(r)
    msgs += check_fact_mapping(r)
    msgs += check_coverage(r, comment_blocks)
    msgs += check_locators(r, manuscript_text, baseline_text)
    msgs += check_evidence(r)
    msgs += check_version_boundary(r)
    msgs += check_determinism(case_id, comment_blocks, source_refs, parse_note, errors, r)
    return msgs


def make_demo_fixture(root):
    """运行时生成的合成 fixture（非正式案例，仅验证脚本可运行）。"""
    os.makedirs(os.path.join(root, "materials"), exist_ok=True)
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "demo-001"}, f)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\nSYNTHETIC DEMO, not a real case.\n")
    with open(os.path.join(root, "materials", "manuscript-baseline.md"), "w",
              encoding="utf-8") as f:
        f.write("# Demo Baseline (SYNTHETIC)\n\nWe enrolled N=120 samples and used "
                "a significance threshold of 0.05.\n")
    with open(os.path.join(root, "materials", "reviewer-comments.md"), "w",
              encoding="utf-8") as f:
        f.write("1. Please report the final sample size.\n"
                "2. Clarify the statistical threshold used.\n"
                "3. Add a limitation sentence.\n")


def demo():
    tmp = tempfile.mkdtemp(prefix="revision-skill-demo-")
    make_demo_fixture(tmp)
    in_dir = tmp
    out_dir = os.path.join(tmp, "output")
    os.makedirs(out_dir, exist_ok=True)
    # 构造与真实运行一致的输入并落盘骨架
    found = M.discover_input(in_dir, exclude_dirs=[out_dir])
    case_obj, _ = M.load_case(found["case"])
    case_id = str(case_obj.get("case_id", "demo-001"))
    reviewer_text = ""
    for m in found["materials"]:
        if re.search(r"review|comment", os.path.basename(m).lower()):
            reviewer_text = M.read_text(m)
    blocks, detected = M.parse_reviewer_comments(reviewer_text)
    source_refs = [os.path.relpath(m, in_dir).replace("\\", "/")
                   for m in sorted(found["materials"])]
    r = M.build_skeleton(case_id, blocks, source_refs, None, [])
    # 完成一条台账（模拟 Agent 的增量修改，锚点来自合成基线）
    r["deliverable"]["path"] = "revised-manuscript.md"
    for e in r["response_ledger"]:
        if e["comment_id"] == "C1":
            e.update({"disposition": "accept", "tone": "accept",
                      "action": "在 Methods 报告最终样本量",
                      "locator": {"section": "Methods", "anchor_new": "N=140 samples"},
                      "status": "done"})
    revised = "# Demo Revised (SYNTHETIC)\n\nWe enrolled N=140 samples.\n"
    with open(os.path.join(out_dir, "revised-manuscript.md"), "w", encoding="utf-8") as f:
        f.write(revised)
    r["version_boundary"]["changes"].append(
        {"comment_id": "C1", "action": "更新样本量", "locator": "Methods", "status": "done"})
    payload = {k: v for k, v in r.items() if k != "runtime"}
    r["runtime"]["content_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    msgs = run_checks(r, blocks, revised, "", case_id, source_refs, None, [])
    fails = [m for m in msgs if m["level"] == "FAIL"]
    print(json.dumps({
        "demo": True, "fixture": "SYNTHETIC, not a real case",
        "checks": len(msgs), "fail": len(fails),
        "messages": [{"level": m["level"], "code": m["code"]} for m in msgs],
    }, ensure_ascii=False, indent=2))
    return 0 if not fails else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="审稿回复 Skill 自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)
    if a.demo:
        return demo()

    result_path = os.path.join(a.out, "result.json")
    r = load_json(result_path)
    if r is None:
        print(json.dumps({"ok": False, "error": "result.json 缺失或不可读: %s" % result_path},
                         ensure_ascii=False))
        return 2

    found = M.discover_input(a.input, exclude_dirs=[a.out])
    reviewer_text = ""
    baseline_text = ""
    for m in found["materials"]:
        base = os.path.basename(m).lower()
        if re.search(r"review|comment|意见|审稿", base):
            reviewer_text = reviewer_text or M.read_text(m)
        if re.search(r"manuscript|baseline|稿件", base):
            baseline_text = baseline_text or M.read_text(m)
    if a.baseline and os.path.isfile(a.baseline):
        baseline_text = M.read_text(a.baseline) or baseline_text
    blocks, _detected = M.parse_reviewer_comments(reviewer_text)

    # 修改稿文本：优先 deliverable.path，回退 output 目录下常见名
    manuscript_text = ""
    dp = (r.get("deliverable") or {}).get("path", "")
    cand = os.path.join(a.out, dp) if dp else None
    if not (cand and os.path.isfile(cand)):
        for name in ("revised-manuscript.md", "manuscript.md", "revised.md"):
            if os.path.isfile(os.path.join(a.out, name)):
                cand = os.path.join(a.out, name)
                break
    if cand and os.path.isfile(cand):
        manuscript_text = M.read_text(cand)

    source_refs = [os.path.relpath(m, a.input).replace("\\", "/")
                   for m in sorted(found["materials"])]
    case_id = r.get("case_id", "unknown")
    parse_note = (r.get("uncertainty") or [{}])[0].get("reason") if r.get("uncertainty") else None
    msgs = run_checks(r, blocks, manuscript_text, baseline_text, case_id,
                      source_refs, parse_note, r.get("errors", []))
    fails = [m for m in msgs if m["level"] == "FAIL"]
    warns = [m for m in msgs if m["level"] == "WARN"]
    print(json.dumps({
        "ok": len(fails) == 0,
        "status": r.get("status"),
        "case_id": case_id,
        "checks": len(msgs), "fail": len(fails), "warn": len(warns),
        "failures": [{"code": m["code"], "text": m["text"]} for m in fails],
        "warnings": [{"code": m["code"], "text": m["text"]} for m in warns],
    }, ensure_ascii=False, indent=2))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
