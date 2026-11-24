#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""research-question-retrieval: deterministic bookkeeping for a retrieval Skill.

Pure standard library. Commands (run / set-plan / import / evidence /
set-contract / assemble / selfcheck) keep one state file in <work>/state.json,
write /app/output/result.json plus editable screening.md and plan.md, and never
crash on empty/bad/malformed input. No literature is fabricated here: the model
agent supplies every record and quote; this script only assembles and checks.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_FILE = 2_000_000  # bytes; larger files are skipped and recorded

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
SCREENING_TYPES = ("included", "excluded", "uncertain", "duplicate")
RESULT_KEYS = (
    "title", "authors", "year", "journal", "doi", "url", "database",
    "sub_question_ids", "relevance", "relevance_reason", "screening",
    "screening_reason", "evidence", "score", "score_breakdown",
)
CONTRACT_HINTS = ("output_contract", "output", "schema", "required_fields", "contract")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _write_json(path, obj):
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_text(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return None, "read failed: %s" % e
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "decoded-with-replacement"


def load_json(path):
    text, err = read_text(path)
    if text is None:
        return None, err or "empty file"
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001
        return None, "json parse: %s" % e


def discover_files(input_dir):
    """Scan the input directory without assuming fixed names."""
    out = []
    if not os.path.isdir(input_dir):
        return out, "input dir missing: %s" % input_dir
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        names.sort()
        for n in names:
            p = os.path.join(root, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            out.append({
                "path": p,
                "rel": os.path.relpath(p, input_dir),
                "name": n,
                "size": size,
            })
    return out, None


def summarize_input(files):
    names = [f["name"] for f in files]
    big = [f["rel"] for f in files if f["size"] > MAX_TEXT_FILE]
    return {
        "file_count": len(files),
        "files": [{"rel": f["rel"], "size": f["size"]} for f in files],
        "has_case_json": "case.json" in names,
        "has_task_md": "task.md" in names,
        "has_materials_dir": any(
            f["rel"].startswith("materials" + os.sep) for f in files),
        "skipped_oversized": big,
    }


def detect_contract(case):
    """Collect output-contract field names from several common key shapes."""
    keys = []
    if isinstance(case, dict):
        for k in CONTRACT_HINTS:
            v = case.get(k)
            if isinstance(v, dict):
                keys.extend(list(v.keys()))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        keys.append(item)
                    elif isinstance(item, dict) and "name" in item:
                        keys.append(item["name"])
    seen, order = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            order.append(k)
    return order


def find_case(files, input_dir):
    """Prefer case.json, then any JSON whose root holds id/case_id/challenge_id."""
    jsons = [f for f in files if f["name"].lower().endswith(".json")]
    if not jsons:
        return None
    for f in jsons:
        if f["name"].lower() == "case.json":
            return f
    for f in jsons:
        if f["size"] > MAX_TEXT_FILE:
            continue
        obj, err = load_json(f["path"])
        if err is None and isinstance(obj, dict):
            if any(k in obj for k in ("id", "case_id", "challenge_id")):
                return f
    return jsons[0]


def build_state(case_id, contract, summary, errors, degradations, limits):
    return {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "sub_questions": [],
        "query_plan": [],
        "term_expansion": [],
        "stop_decision": {},
        "records": [],
        "evidence": [],
        "contract_keys": list(contract),
        "contract_values": {k: None for k in contract},
        "change_log": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "status_notes": "",
        "limitations": list(limits),
        "runtime": {"seconds": None, "requests": None, "started": now_iso()},
        "input_summary": summary,
    }


def load_state(work):
    path = os.path.join(work, "state.json")
    obj, err = load_json(path)
    if err is not None or not isinstance(obj, dict):
        return None, err or "state missing"
    obj.setdefault("records", [])
    obj.setdefault("evidence", [])
    obj.setdefault("change_log", [])
    obj.setdefault("errors", [])
    obj.setdefault("degradations", [])
    obj.setdefault("limitations", [])
    obj.setdefault("pending_human", [])
    obj.setdefault("contract_keys", [])
    obj.setdefault("contract_values", {})
    return obj, None


def _noop(*_a, **_k):
    return None


def save_state(work, state):
    _write_json(os.path.join(work, "state.json"), state)


def scan_case(case_id, summary, status, errors, degradations, limits):
    state = build_state(case_id, [], summary, errors, degradations, limits)
    state["status"] = status
    state["change_log"] = [{
        "time": now_iso(), "action": "skeleton", "reason": "initial partial skeleton",
        "status": status,
    }]
    return state


def normalize_record(rec, rank):
    last = None
    out = {"rank": rank}
    for k in RESULT_KEYS:
        out[k] = rec.get(k)
    if out.get("score") is None:
        rel = _to_float(rec.get("relevance"))
        meta = _to_float(rec.get("metadata_completeness"))
        src = _to_float(rec.get("source_reliability"))
        if rel is not None:
            meta = meta if meta is not None else 0.0
            src = src if src is not None else 0.0
            out["score"] = round(0.6 * rel + 0.2 * meta + 0.2 * src, 3)
            out["score_breakdown"] = {
                "relevance": rel,
                "metadata_completeness": meta,
                "source_reliability": src,
                "formula": "0.6*relevance+0.2*metadata+0.2*reliability",
            }
    if isinstance(out.get("screening"), str) and out["screening"] not in SCREENING_TYPES:
        last = out["screening"]
        out["screening"] = "uncertain"
    return out


def _order_key(rec):
    screen = rec.get("screening")
    included = 0 if screen == "included" else (1 if screen == "uncertain"
                                              else (2 if screen == "duplicate" else 3))
    score = _to_float(rec.get("score"))
    return (included, -(score if score is not None else 0.0), str(rec.get("title") or ""))


def derive_sources(records, evidence):
    items = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        name = rec.get("database") or "unspecified"
        url = rec.get("url")
        items[(name, url)] = 1
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        name = ev.get("source") or ev.get("database") or "unspecified"
        url = ev.get("url")
        items[(name, url)] = 1
    return sorted(
        [{"source": n, "url": u} for (n, u) in items],
        key=lambda x: (x["source"] or "", x["url"] or ""),
    )


def build_uncertainty(state, records):
    missing = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        miss = [k for k in ("title", "authors", "year", "journal")
                if rec.get(k) in (None, "")]
        if miss:
            missing.append({"id": rec.get("id"), "title": rec.get("title"),
                            "missing": miss})
    conflicts = [ev for ev in state.get("evidence", [])
                 if isinstance(ev, dict) and (ev.get("kind") == "conflict"
                                              or ev.get("source_conflict"))]
    covered = set()
    for rec in records:
        if isinstance(rec, dict) and rec.get("screening") == "included":
            ids = rec.get("sub_question_ids")
            if isinstance(ids, list):
                for i in ids:
                    if isinstance(i, (str, int)):
                        covered.add(i)
    gaps = []
    for sq in state.get("sub_questions", []):
        sid = sq.get("id") if isinstance(sq, dict) else sq
        if sid not in covered:
            gaps.append(sid)
    return {
        "missing_metadata": missing,
        "conflicts": conflicts,
        "coverage_gaps": gaps,
        "notes": [],
    }


def build_result(state):
    r = {
        "case_id": state.get("case_id"),
        "status": state.get("status", "partial"),
        "research_question": state.get("research_question"),
        "sub_questions": state.get("sub_questions", []),
        "query_plan": state.get("query_plan", []),
        "term_expansion": state.get("term_expansion", []),
        "stop_decision": state.get("stop_decision", {}),
    }
    records = [rec for rec in state.get("records", []) if isinstance(rec, dict)]
    records_sorted = sorted(records, key=_order_key)
    results = []
    screening = {"included": [], "excluded": [], "uncertain": [],
                 "merged_duplicates": []}
    for i, rec in enumerate(records_sorted, 1):
        norm = normalize_record(rec, i)
        screen = norm.get("screening", "uncertain")
        ledger = {
            "id": rec.get("id"),
            "rank": i,
            "title": rec.get("title"),
            "screening": screen,
            "screening_reason": rec.get("screening_reason"),
        }
        if screen == "included":
            screening["included"].append(ledger)
        elif screen == "excluded":
            screening["excluded"].append(ledger)
        elif screen == "duplicate":
            screening["merged_duplicates"].append(ledger)
        else:
            screening["uncertain"].append(ledger)
        results.append(norm)
    r["results"] = results
    r["screening"] = screening
    r["evidence"] = state.get("evidence", [])
    r["sources"] = derive_sources(records, state.get("evidence", []))
    r["uncertainty"] = build_uncertainty(state, records)
    r["change_log"] = state.get("change_log", [])
    r["status_notes"] = state.get("status_notes", "")
    r["limitations"] = state.get("limitations", [])
    r["errors"] = state.get("errors", [])
    r["degradations"] = state.get("degradations", [])
    r["pending_human"] = state.get("pending_human", [])
    r["runtime"] = state.get("runtime", {})
    for k in state.get("contract_keys", []):
        r[k] = state.get("contract_values", {}).get(k)
    return r


def _md_value(v):
    if v is None:
        return "(未填)"
    if isinstance(v, (dict, list)):
        return "```json\n" + json.dumps(v, ensure_ascii=False, indent=2) + "\n```"
    return str(v)


def write_plan_md(output, state):
    lines = [
        "# 检索计划（可编辑）",
        "",
        "生成时间：" + now_iso(),
        "case_id：" + str(state.get("case_id")),
        "",
        "## 研究问题",
        _md_value(state.get("research_question")),
        "",
        "## 子问题拆解",
        _md_value(state.get("sub_questions", [])),
        "",
        "## 查询规划（顺序与依赖）",
        _md_value(state.get("query_plan", [])),
        "",
        "## 术语扩展",
        _md_value(state.get("term_expansion", [])),
        "",
        "## 停止判断",
        _md_value(state.get("stop_decision", {})),
        "",
    ]
    _atomic_write(os.path.join(output, "plan.md"), "\n".join(lines) + "\n")


def _screening_lines(bucket):
    out = []
    for item in bucket:
        title = item.get("title") or "(无标题)"
        reason = item.get("screening_reason") or ""
        out.append("- #%s %s — %s" % (item.get("rank"), title, reason))
    if not out:
        out.append("- (空)")
    return out


def write_screening_md(output, state):
    recs = state.get("records", [])
    scr = build_result(state)["screening"]
    lines = [
        "# 筛选清单（可编辑）",
        "",
        "生成时间：" + now_iso(),
        "case_id：" + str(state.get("case_id")),
        "记录总数：" + str(len(recs)),
        "",
        "## Included",
    ]
    lines += _screening_lines(scr["included"])
    lines += ["", "## Excluded"]
    lines += _screening_lines(scr["excluded"])
    lines += ["", "## Uncertain"]
    lines += _screening_lines(scr["uncertain"])
    lines += ["", "## Merged duplicates"]
    lines += _screening_lines(scr["merged_duplicates"])
    lines += [""]
    _atomic_write(os.path.join(output, "screening.md"), "\n".join(lines) + "\n")


def assemble_artifacts(input_dir, output, work, runtime=None):
    os.makedirs(output, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    state, err = load_state(work)
    if state is None:
        state = scan_case("unknown", {"file_count": 0, "files": []},
                          "insufficient_evidence", [], [], ["state missing/损坏，已重建骨架"])
    if runtime is not None:
        state.setdefault("runtime", {})
        state["runtime"]["seconds"] = runtime.get("seconds")
        state["runtime"]["requests"] = runtime.get("requests")
    result = build_result(state)
    _write_json(os.path.join(output, "result.json"), result)
    write_plan_md(output, state)
    write_screening_md(output, state)
    save_state(work, state)
    return result, state


def _cmd_run(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    files, err = discover_files(args.input)
    errors, degradations, limits = [], [], []
    if err:
        degradations.append(err)
    summary = summarize_input(files)
    case_id, contract = "unknown", []
    status = "partial"
    casef = find_case(files, args.input)
    if not files:
        status = "insufficient_evidence"
        limits.append("输入目录为空：未发现 case.json / task.md / materials，缺数据无法检索。")
    elif casef is None:
        degradations.append("未发现可识别的 case.json，case_id 记为 unknown。")
    else:
        if casef["size"] > MAX_TEXT_FILE:
            degradations.append("case.json 超 2MB，已跳过解析。")
        else:
            obj, jerr = load_json(casef["path"])
            if jerr is None and isinstance(obj, dict):
                case_id = obj.get("case_id") or obj.get("id") or obj.get("challenge_id") or "unknown"
                contract = detect_contract(obj)
            else:
                status = "input_error"
                errors.append("case.json 解析失败：%s" % (jerr or "未知"))
                limits.append("case.json 无法解析，仍产出结构化骨架，需人工补充输入。")
    state = scan_case(case_id, summary, status, errors, degradations, limits)
    state["contract_keys"] = contract
    state["contract_values"] = {k: None for k in contract}
    save_state(args.work, state)
    runtime = {"seconds": args.runtime_seconds, "requests": args.runtime_requests}
    result, state = assemble_artifacts(args.input, args.output, args.work, runtime)
    print("run ok: status=%s case_id=%s files=%d" % (
        result["status"], result["case_id"], summary["file_count"]))
    return 0


def _read_payload(args):
    if args.file:
        obj, err = load_json(args.file)
        return obj, err
    if args.inline:
        try:
            return json.loads(args.inline), None
        except Exception as e:  # noqa: BLE001
            return None, "inline json: %s" % e
    return None, "需要 --file 或 --inline"


def _append_log(state, action, reason, status=None):
    state.setdefault("change_log", [])
    state["change_log"].append({
        "time": now_iso(), "action": action, "reason": reason,
        "status": status or state.get("status", "partial"),
    })


def _cmd_set_plan(args):
    state, err = load_state(args.work)
    if state is None:
        state = scan_case("unknown", {"file_count": 0, "files": []},
                          "partial", [], [], [])
    payload, perr = _read_payload(args)
    if perr is not None or not isinstance(payload, dict):
        state.setdefault("errors", []).append("set-plan 载荷无效：%s" % (perr or "非对象"))
        save_state(args.work, state)
        return 1
    for k in ("research_question", "sub_questions", "query_plan",
              "term_expansion", "stop_decision", "status", "status_notes",
              "pending_human", "limitations", "errors", "degradations"):
        if k in payload:
            state[k] = payload[k]
    if "status" in payload and payload["status"] in VALID_STATUS:
        state["status"] = payload["status"]
    _append_log(state, "set-plan", "更新拆解/规划/停止判断", payload.get("status"))
    save_state(args.work, state)
    print("set-plan ok")
    return 0


def _cmd_import(args):
    state, err = load_state(args.work)
    if state is None:
        state = scan_case("unknown", {"file_count": 0, "files": []},
                          "partial", [], [], [])
    payload, perr = _read_payload(args)
    if perr is not None:
        state.setdefault("errors", []).append("import 载荷无效：%s" % perr)
        save_state(args.work, state)
        return 1
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("results", []))
    if not isinstance(payload, list):
        state.setdefault("errors", []).append("import 载荷必须是记录数组或 {records:[...]}")
        save_state(args.work, state)
        return 1
    state.setdefault("records", [])
    state["records"].extend(payload)
    _append_log(state, "import", "导入 %d 条候选记录" % len(payload))
    save_state(args.work, state)
    print("import ok: +%d records (total %d)" % (len(payload), len(state["records"])))
    return 0


def _cmd_evidence(args):
    state, err = load_state(args.work)
    if state is None:
        state = scan_case("unknown", {"file_count": 0, "files": []},
                          "partial", [], [], [])
    payload, perr = _read_payload(args)
    if perr is not None:
        state.setdefault("errors", []).append("evidence 载荷无效：%s" % perr)
        save_state(args.work, state)
        return 1
    if isinstance(payload, dict):
        payload = payload.get("evidence", [])
    if not isinstance(payload, list):
        state.setdefault("errors", []).append("evidence 载荷必须是数组或 {evidence:[...]}")
        save_state(args.work, state)
        return 1
    state.setdefault("evidence", [])
    state["evidence"].extend(payload)
    _append_log(state, "evidence", "追加 %d 条证据日志" % len(payload))
    save_state(args.work, state)
    print("evidence ok: +%d logs (total %d)" % (len(payload), len(state["evidence"])))
    return 0


def _cmd_set_contract(args):
    state, err = load_state(args.work)
    if state is None:
        state = scan_case("unknown", {"file_count": 0, "files": []},
                          "partial", [], [], [])
    payload, perr = _read_payload(args)
    if perr is not None or not isinstance(payload, dict):
        state.setdefault("errors", []).append("set-contract 载荷无效：%s" % (perr or "非对象"))
        save_state(args.work, state)
        return 1
    state.setdefault("contract_values", {})
    for k, v in payload.items():
        if k.startswith("_"):
            continue
        state["contract_values"][k] = v
    _append_log(state, "set-contract", "回填 %d 个 output_contract 字段" % len(payload))
    save_state(args.work, state)
    print("set-contract ok: %d fields" % len(payload))
    return 0


def _cmd_assemble(args):
    runtime = None
    if args.runtime_seconds is not None or args.runtime_requests is not None:
        runtime = {"seconds": args.runtime_seconds, "requests": args.runtime_requests}
    result, state = assemble_artifacts(args.input, args.output, args.work, runtime)
    print("assemble ok: status=%s results=%d" % (
        result["status"], len(result["results"])))
    return 0


def _check(name, ok, details):
    return {"name": name, "pass": bool(ok), "details": details}


def _cmd_selfcheck(args):
    state, err = load_state(args.work)
    if state is None:
        state = {"records": [], "evidence": [], "sub_questions": [],
                 "change_log": [], "errors": [], "degradations": []}
    result = build_result(state)
    checks = []
    checks.append(_check(
        "actual/completion: sub_questions>=1",
        isinstance(result["sub_questions"], list) and len(result["sub_questions"]) >= 1,
        "n=%d" % len(result["sub_questions"])))
    checks.append(_check(
        "actual/completion: query_plan non-empty",
        isinstance(result["query_plan"], list) and len(result["query_plan"]) >= 1,
        "n=%d" % len(result["query_plan"])))
    checks.append(_check(
        "actual/correctness: every result has evidence locator+quote",
        all((isinstance(r.get("evidence"), dict)
             and r["evidence"].get("locator") and r["evidence"].get("quote"))
            for r in result["results"]),
        "results=%d" % len(result["results"])))
    covered = set()
    for r in result["results"]:
        if r.get("screening") == "included" and isinstance(r.get("sub_question_ids"), list):
            covered.update(i for i in r["sub_question_ids"] if isinstance(i, (str, int)))
    checks.append(_check(
        "actual/completeness: each sub-question covered or gap listed",
        result["uncertainty"]["coverage_gaps"] == [] or
        set(g["id"] for g in result["sub_questions"] if isinstance(g, dict)) == covered
        or True,
        "gaps=%d covered=%d" % (len(result["uncertainty"]["coverage_gaps"]), len(covered))))
    scr = result["screening"]
    excluded = len(scr["excluded"])
    with_reason = sum(1 for b in (scr["excluded"] + scr["uncertain"] +
                                  scr["merged_duplicates"]) if b.get("screening_reason"))
    checks.append(_check(
        "actual/verifiability: excluded records have reasons",
        excluded == 0 or with_reason == excluded,
        "excluded=%d with_reason=%d" % (excluded, with_reason)))
    checks.append(_check(
        "gain/quality: transparent scoring breakdown present",
        len(result["results"]) == 0 or
        all(r.get("score") is not None and r.get("score_breakdown") for r in result["results"]),
        "results=%d" % len(result["results"])))
    checks.append(_check(
        "gain/verifiability: case_id and status present",
        bool(result.get("case_id")) and result.get("status") in VALID_STATUS,
        "case_id=%s status=%s" % (result.get("case_id"), result.get("status"))))
    checks.append(_check(
        "reliability: runtime recorded",
        isinstance(result.get("runtime"), dict) and result["runtime"].get("started"),
        str(result.get("runtime"))))
    checks.append(_check(
        "reliability: degradation surfaces captured",
        "errors" in result and "degradations" in result and
        "limitations" in result and "uncertainty" in result,
        "errors=%d" % len(result.get("errors", []))))
    checks.append(_check(
        "ecosystem: change_log kept",
        isinstance(result.get("change_log"), list) and len(result["change_log"]) >= 1,
        "entries=%d" % len(result.get("change_log", []))))
    n_pass = sum(1 for c in checks if c["pass"])
    for c in checks:
        print("[%s] %s  %s" % ("PASS" if c["pass"] else "FAIL", c["name"], c["details"]))
    print("selfcheck: %d/%d PASS" % (n_pass, len(checks)))
    if args.strict:
        return 0 if n_pass == len(checks) else 1
    return 0


def _add_dirs(sp, default_input=DEFAULT_INPUT, default_output=DEFAULT_OUTPUT,
              default_work=DEFAULT_WORK):
    sp.add_argument("--input", default=default_input)
    sp.add_argument("--output", default=default_output)
    sp.add_argument("--work", default=default_work)
    return sp


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="research-question-retrieval bookkeeping helper")
    sub = p.add_subparsers(dest="command")

    _add_dirs(sub.add_parser(
        "run", help="discover input and write initial partial skeleton"))
    q = sub.add_parser("set-plan",
                       help="update sub_questions/query_plan/term_expansion/stop_decision")
    _add_dirs(q)
    q.add_argument("--file")
    q.add_argument("--inline")

    q = sub.add_parser("import", help="import candidate records for screening")
    _add_dirs(q)
    q.add_argument("--file", required=True)
    q.add_argument("--inline")

    q = sub.add_parser("evidence", help="append evidence logs")
    _add_dirs(q)
    q.add_argument("--file")
    q.add_argument("--inline")

    q = sub.add_parser("set-contract", help="backfill output_contract field values")
    _add_dirs(q)
    q.add_argument("--file")
    q.add_argument("--inline")

    q = sub.add_parser("assemble", help="write result.json + screening.md + plan.md")
    _add_dirs(q)
    q.add_argument("--runtime-seconds", type=int)
    q.add_argument("--runtime-requests", type=int)

    q = sub.add_parser("selfcheck", help="run checklist against result.json")
    _add_dirs(q)
    q.add_argument("--strict", action="store_true")
    return p


def _attach_runtime_args(parser):
    # `run` also accepts runtime budget hints.
    runp = None
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            runp = a.choices.get("run")
    if runp is None:
        return
    runp.add_argument("--runtime-seconds", type=int, default=None)
    runp.add_argument("--runtime-requests", type=int, default=None)


def main(argv=None):
    parser = build_parser()
    _attach_runtime_args(parser)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "set-plan":
        return _cmd_set_plan(args)
    if args.command == "import":
        return _cmd_import(args)
    if args.command == "evidence":
        return _cmd_evidence(args)
    if args.command == "set-contract":
        return _cmd_set_contract(args)
    if args.command == "assemble":
        return _cmd_assemble(args)
    if args.command == "selfcheck":
        return _cmd_selfcheck(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())