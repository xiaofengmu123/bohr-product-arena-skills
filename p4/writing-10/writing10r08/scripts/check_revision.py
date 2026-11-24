#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四问一致性校验器（答得全 / 改得准 / 证得住 / 对得上）。

读取台账、回复信与修订稿，逐条校验并在 /app/output/check-report.json 输出报告。
退出码恒为 0（报告生成器，非门禁）；不可读文件会记录到 report.errors 而非崩溃。
"""
import argparse
import json
import os
import re
import sys
import time

ALLOWED_DECISION = {"accept", "partial", "reject", "clarify", None}
ALLOWED_TONE = {"accept", "clarify", "rebut", "appeal", None}
ALLOWED_EVIDENCE = {"done", "partial", "cannot_do", "planned", None}
EVIDENCE_TYPES = {"method", "data"}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    try:
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
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as e:
        return None, "bad json: %s" % e


def normalize(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", s).strip().lower()


def report_item(name, ok, message, details=None):
    return {"check": name,
            "result": "pass" if ok else "fail",
            "message": message,
            "details": details or []}


def check_coverage(entries, response_text):
    """答得全：每条意见有决策、语气、回应与至少一个子要求。"""
    details = []
    ok = True
    for e in entries:
        missing = []
        if not e.get("decision"):
            missing.append("decision")
        if not e.get("tone"):
            missing.append("tone")
        if not (e.get("response_summary") or "").strip():
            missing.append("response_summary")
        if not e.get("sub_requirements"):
            missing.append("sub_requirements")
        cid = e.get("comment_id", "?")
        # 回复信中是否出现该意见编号（结构覆盖）；response_text 已归一化小写
        in_response = (cid.lower() in response_text) if cid else False
        if not in_response:
            missing.append("response-letter heading")
        if missing:
            ok = False
            details.append({"comment_id": cid, "missing": missing})
    return report_item("coverage", ok,
                       "每条意见需有 decision/tone/response/sub_requirements 且回复信含其编号",
                       details)


def check_locality(entries, manuscript_norm):
    """改得准：accept/partial 的每条 change 的 after 或 anchor 能在修订稿中找到。"""
    details = []
    ok = True
    for e in entries:
        dec = e.get("decision")
        if dec not in ("accept", "partial"):
            continue
        changes = e.get("changes") or []
        if not changes:
            ok = False
            details.append({"comment_id": e.get("comment_id"),
                            "problem": "accept/partial 但无任何 change"})
            continue
        for ch in changes:
            after = normalize(ch.get("after"))
            anchor = normalize(ch.get("anchor"))
            hit = (after and after in manuscript_norm) or \
                  (anchor and anchor in manuscript_norm)
            if not hit:
                ok = False
                details.append({"comment_id": e.get("comment_id"),
                                "action": ch.get("action", ""),
                                "problem": "after/anchor 未在修订稿中定位到"})
    return report_item("locality", ok,
                       "accept/partial 的每条 change 必须在修订稿中有可检索锚点", details)


def check_evidence(entries):
    """证得住：方法/数据类意见需 evidence 到位状态与来源或无法补做原因。"""
    details = []
    ok = True
    for e in entries:
        needs = e.get("evidence", {}).get("needed", False) or \
            (e.get("comment_type") in EVIDENCE_TYPES)
        if not needs:
            continue
        ev = e.get("evidence") or {}
        if ev.get("status") not in ("done", "partial", "cannot_do", "planned"):
            ok = False
            details.append({"comment_id": e.get("comment_id"),
                            "problem": "evidence.status 缺失或非法"})
            continue
        if not (ev.get("source") or ev.get("cannot_do_reason")):
            ok = False
            details.append({"comment_id": e.get("comment_id"),
                            "problem": "需证据的意见既无 source 也无 cannot_do_reason"})
    return report_item("evidence", ok,
                       "需补证据的意见必须有到位状态 + 来源或无法补做原因", details)


def check_tone_and_consistency(entries):
    """语气合法 + 编号唯一 + 台账 entry 状态合法。"""
    details = []
    ok = True
    seen = set()
    for e in entries:
        cid = e.get("comment_id", "?")
        if cid in seen:
            ok = False
            details.append({"comment_id": cid, "problem": "comment_id 重复"})
        seen.add(cid)
        if e.get("decision") not in ALLOWED_DECISION:
            ok = False
            details.append({"comment_id": cid,
                            "problem": "非法 decision: %r" % e.get("decision")})
        if e.get("tone") not in ALLOWED_TONE:
            ok = False
            details.append({"comment_id": cid,
                            "problem": "非法 tone: %r" % e.get("tone")})
        if e.get("status") not in ("resolved", "pending", "rejected", None):
            ok = False
            details.append({"comment_id": cid,
                            "problem": "非法 status: %r" % e.get("status")})
    return report_item("tone_and_consistency", ok,
                       "语气/决策/状态枚举合法且 comment_id 唯一", details)


def check_version_boundary(entries, result):
    """对得上：result.version_boundary.changes 与台账改动一一对应。"""
    vb = (result or {}).get("version_boundary") or {}
    changes = vb.get("changes") or []
    ledger_changes = [ch for e in entries for ch in (e.get("changes") or [])]
    ok = len(changes) == len(ledger_changes)
    details = []
    if not ok:
        details.append({"version_boundary_changes": len(changes),
                        "ledger_changes": len(ledger_changes),
                        "problem": "changes 数量不一致，回复信/修改稿/台账未对齐"})
    return report_item("version_boundary", ok,
                       "result.version_boundary.changes 与台账改动一一对应", details)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="/app/output/revision-ledger.json")
    ap.add_argument("--manuscript", default="/app/output/revised-manuscript.md")
    ap.add_argument("--response", default="/app/output/response-letter.md")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    ledger, e1 = load_json_safe(a.ledger)
    manuscript, e2 = read_text(a.manuscript)
    response, e3 = read_text(a.response)
    result, e4 = load_json_safe(a.result)

    errors = []
    if e1:
        errors.append({"item": a.ledger, "reason": e1})
    if e2 and manuscript is None:
        errors.append({"item": a.manuscript, "reason": e2})
    if e3 and response is None:
        errors.append({"item": a.response, "reason": e3})
    if e4:
        errors.append({"item": a.result, "reason": e4})

    entries = (ledger or {}).get("entries") or []
    manuscript_norm = normalize(manuscript)
    response_norm = normalize(response)

    checks = [
        check_coverage(entries, response_norm),
        check_locality(entries, manuscript_norm),
        check_evidence(entries),
        check_tone_and_consistency(entries),
        check_version_boundary(entries, result),
    ]
    n_fail = sum(1 for c in checks if c["result"] == "fail")
    report = {
        "generated_at": now_ts(),
        "passed": n_fail == 0 and not errors,
        "n_checks": len(checks),
        "n_fail": n_fail,
        "checks": checks,
        "errors": errors,
    }
    report_path = a.report or os.path.join(os.path.dirname(os.path.abspath(a.ledger)),
                                           "check-report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except OSError as e:
        report["write_error"] = str(e)
    print(json.dumps({"passed": report["passed"], "n_fail": n_fail,
                      "checks": [c["check"] + ":" + c["result"] for c in checks],
                      "errors": len(errors)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
