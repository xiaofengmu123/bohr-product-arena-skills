#!/usr/bin/env python3
"""research-frontier-audit — 阶段4：自检。

校验 result.json 契约字段、枚举、四类产物齐全、quote 可定位、evidence_ids 可解析、
中间表示存在、change_log 完整；并把逐项 PASS/FAIL/WARN 写入 /app/work/selfcheck.json。
退出码：0 = 通过或有 WARN；1 = 存在 FAIL（需修复）；2 = result.json 缺失/不可解析。
只依赖 Python 标准库。
"""
import os
import sys
import re
import json
import argparse

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
SUPPORT_ENUM = {"reported", "not_reported", "inferred"}
EVIDENCE_STATUS_ENUM = {"reported", "not_reported", "source_conflict", "inferred"}
WORK_KIND_ENUM = {"origin", "method", "milestone", "benchmark"}
EVENT_TYPE_ENUM = {"emergence", "merge", "split", "decline", "terminology_drift"}
GAP_TYPE_ENUM = {"open_question", "method_failure", "unresolved_conflict", "coverage_absence"}

REQUIRED_FIELDS = ["case_id", "status", "output_contract", "research_question", "protocol",
                   "time_trend", "topic_evolution", "representative_works", "research_gaps",
                   "evidence_map", "conflicts", "artifacts", "sources", "uncertainty",
                   "pending_human", "change_log", "limitations", "errors", "degradations", "runtime"]


def read_text_safe(path):
    try:
        with open(path, "rb") as f:
            data = f.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            return None, "oversize"
    except OSError as e:
        return None, "read_error:" + str(e)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "decode_partial"


def load_json_safe(path):
    text, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "bad_json:" + str(e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def find_quote_in_file(file_path, quote):
    """返回 (located, level)：exact / normalized / not_located。"""
    text, err = read_text_safe(file_path)
    if text is None:
        return False, "file_unreadable:" + str(err)
    q = (quote or "").strip()
    if not q:
        return False, "empty_quote"
    if q in text:
        return True, "exact"
    qn = norm(q)
    tn = norm(text)
    if qn and qn in tn:
        return True, "normalized"
    return False, "not_located"


def main(argv=None):
    ap = argparse.ArgumentParser(description="阶段4：自检")
    ap.add_argument("--input", default=DEFAULTS["input"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    args = ap.parse_args(argv)

    result_path = os.path.join(args.output, "result.json")
    result, rerr = load_json_safe(result_path)
    if rerr:
        write_json(os.path.join(args.work, "selfcheck.json"),
                   {"ok": False, "fatal": "result.json unreadable:" + str(rerr),
                    "passes": [], "fails": ["result.json_unreadable"], "warns": []})
        print("FATAL: result.json unreadable: %s" % rerr, file=sys.stderr)
        return 2
    if not isinstance(result, dict):
        write_json(os.path.join(args.work, "selfcheck.json"),
                   {"ok": False, "fatal": "result.json 非对象", "passes": [], "fails": ["result.json_not_dict"], "warns": []})
        print("FATAL: result.json 非 JSON 对象", file=sys.stderr)
        return 2

    passes, fails, warns = [], [], []

    def chk(cond, tag, level="fail", msg=""):
        if cond:
            passes.append(tag)
        elif level == "warn":
            warns.append(tag + ((": " + msg) if msg else ""))
        else:
            fails.append(tag + ((": " + msg) if msg else ""))

    # 1. 必需字段
    for f in REQUIRED_FIELDS:
        chk(f in result, "field_%s" % f)

    # 2. case_id / status / output_contract
    chk(isinstance(result.get("case_id"), str) and result["case_id"], "case_id_nonempty")
    chk(result.get("status") in STATUS_ENUM, "status_enum", msg=str(result.get("status")))
    chk(isinstance(result.get("output_contract"), dict), "output_contract_dict")
    for k in ("sources", "uncertainty", "pending_human", "change_log", "limitations", "errors", "degradations"):
        chk(isinstance(result.get(k), list), "list_%s" % k, level="warn")

    # 3. 四类产物齐全
    tt = result.get("time_trend") or {}
    chk(isinstance(tt.get("distribution"), list) and len(tt["distribution"]) > 0,
        "time_trend.distribution_nonempty", msg="年度分布为空（年份缺失时须 unit_note + pending_human 说明）")
    chk(isinstance(tt.get("unit_note"), str) and tt.get("unit_note"), "time_trend.unit_note", level="warn")

    te = result.get("topic_evolution") or {}
    chk((isinstance(te.get("term_mapping"), list) and len(te["term_mapping"]) > 0)
        or (isinstance(te.get("stage_divisions"), list) and len(te["stage_divisions"]) > 0),
        "topic_evolution_nonempty")

    rw = result.get("representative_works") or {}
    chk(isinstance(rw.get("stages"), list) and len(rw["stages"]) > 0, "representative_works.stages_nonempty")
    chk(isinstance(rw.get("criteria"), list) and len(rw["criteria"]) > 0, "representative_works.criteria", level="warn")

    rg = result.get("research_gaps") or {}
    chk(isinstance(rg.get("gaps"), list) and len(rg["gaps"]) > 0, "research_gaps.gaps_nonempty",
        msg="空白为空且未在 limitations 说明材料不足")

    # 4. 枚举校验
    for ev in result.get("evidence_map", []):
        if not isinstance(ev, dict):
            continue
        if "status" in ev:
            chk(ev["status"] in EVIDENCE_STATUS_ENUM, "evidence_status_enum", level="warn", msg=str(ev.get("status")))
    for c in tt.get("claims", []):
        if isinstance(c, dict) and "support" in c:
            chk(c["support"] in SUPPORT_ENUM, "claim_support_enum", level="warn", msg=str(c.get("support")))
    for ev in te.get("events", []):
        if isinstance(ev, dict) and "type" in ev:
            chk(ev["type"] in EVENT_TYPE_ENUM, "event_type_enum", level="warn", msg=str(ev.get("type")))
    for s in rw.get("stages", []):
        for w in s.get("works", []) if isinstance(s, dict) else []:
            if isinstance(w, dict) and "kind" in w:
                chk(w["kind"] in WORK_KIND_ENUM, "work_kind_enum", level="warn", msg=str(w.get("kind")))
    for g in rg.get("gaps", []):
        if isinstance(g, dict) and "type" in g:
            chk(g["type"] in GAP_TYPE_ENUM, "gap_type_enum", level="warn", msg=str(g.get("type")))

    # 5. 中间表示存在
    plan, perr = load_json_safe(os.path.join(args.output, "plan.json"))
    chk(perr is None and isinstance(plan, dict) and plan.get("frozen") is True, "plan.json_frozen")
    fm, ferr = load_json_safe(os.path.join(args.output, "figure_model.json"))
    chk(ferr is None and isinstance(fm, dict), "figure_model.json_valid")
    if isinstance(fm, dict):
        for k in ("documents", "term_index", "candidates", "windows"):
            chk(isinstance(fm.get(k), list), "figure_model.%s_list" % k)

    # 6. quote 可定位（读 manifest 判断 JSON 语料 -> 软失败）
    manifest, _merr = load_json_safe(os.path.join(args.work, "manifest.json"))
    json_materials = set()
    if isinstance(manifest, dict):
        for e in manifest.get("files", []):
            if e.get("ext") == ".json":
                json_materials.add(e.get("material_file"))
    ev_ids = set()
    for ev in result.get("evidence_map", []):
        if not isinstance(ev, dict):
            warns.append("evidence_entry_not_dict")
            continue
        ev_ids.add(ev.get("id"))
        q = ev.get("quote")
        if not isinstance(q, str) or not q.strip():
            if ev.get("status") == "reported":
                warns.append("evidence_%s_empty_quote" % ev.get("id"))
            continue
        src = ev.get("source_file") or (ev.get("locator", "").split("#")[0] if ev.get("locator") else "")
        if not src:
            fails.append("evidence_%s_no_source_file" % ev.get("id"))
            continue
        fp = os.path.join(args.work, src)
        located, level = find_quote_in_file(fp, q)
        if located:
            passes.append("evidence_%s_quote_located(%s)" % (ev.get("id"), level))
        else:
            soft = src in json_materials
            chk(False, "evidence_%s_quote_not_located" % ev.get("id"),
                level=("warn" if soft else "fail"),
                msg="%s 中未找到逐字 quote（%s）" % (src, level))

    # 7. evidence_ids 可解析
    for c in tt.get("claims", []):
        if isinstance(c, dict):
            for eid in c.get("evidence_ids", []):
                if eid and eid not in ev_ids:
                    warns.append("claim_evidence_missing_%s" % eid)
    for g in rg.get("gaps", []):
        if isinstance(g, dict):
            for eid in g.get("evidence_ids", []):
                if eid and eid not in ev_ids:
                    warns.append("gap_evidence_missing_%s" % eid)

    # 8. change_log
    cl = result.get("change_log") or []
    chk(isinstance(cl, list) and len(cl) >= 2, "change_log_nonempty", level="warn",
        msg="至少应含 skeleton 与 machine_baseline/人工核验两条")
    chk(any(isinstance(x, dict) and x.get("action") == "skeleton" for x in cl),
        "change_log_has_skeleton", level="warn")

    # 9. 空白排除证据（人工核验后应填写）
    for g in rg.get("gaps", []):
        if isinstance(g, dict) and not g.get("exclusion_evidence"):
            warns.append("gap_%s_no_exclusion_evidence" % g.get("id"))

    # 10. 疑似伪造分数/预置答案检测（仅告警，不判失败）
    def scan_keys(obj, path=""):
        bad = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if re.search(r"benchmark[_ ]?score|whiteboard|gold[_ ]?answer|fake[_ ]?score|预置|白板", str(k), re.I):
                    bad.append(path + "/" + str(k))
                bad += scan_keys(v, path + "/" + str(k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:200]):
                bad += scan_keys(v, path + "/%d" % i)
        return bad
    for b in scan_keys(result)[:10]:
        warns.append("suspicious_key_%s" % b)

    ok = len(fails) == 0
    report = {"ok": ok, "passes": passes, "fails": fails, "warns": warns,
              "summary": {"pass": len(passes), "fail": len(fails), "warn": len(warns)}}
    write_json(os.path.join(args.work, "selfcheck.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
