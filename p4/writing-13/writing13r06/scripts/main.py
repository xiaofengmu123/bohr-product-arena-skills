#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fund-proposal-writing-verifier 的执行与自检脚手架。

仅依赖 Python 标准库。子命令：
  bootstrap  落盘 result.json / plan.json / run_meta.json 骨架（Step 0，崩溃安全）
  scan       盘点 /input 材料，生成 source_manifest.json（大小 + SHA256）
  plan-init  (重新)写 plan.json 模板
  ledger-add 追加一条事实台账（claim/kind/ref/loc/quote/status）
  ledger-export  把 evidence_ledger.json 导出为 fact_source_map.json
  verify     交叉校验 result.json / plan.json / 台账，输出 verify_report.json
  finalize   把台账、清单、边界合并进 result.json 并设置 status
路径：绝对默认 /input、/app/work、/app/output；本地调试自动回退到 input/、work/、output/。
"""
import argparse
import calendar
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)

SCHEMA_VERSION = "1.0.0"
SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性",
]

# 无法核验的营销式修饰：一旦出现必须给出可核验比较对象，否则标 pending_human。
HARD_BAN = ["填补空白", "国际首创", "世界首创", "世界第一", "国内外首创", "全球领先",
            "国际空白", "国内空白", "举世无双", "绝无仅有"]
REVIEW_FLAG = ["首次", "国际领先", "国内领先", "突破性", "颠覆性", "遥遥领先",
               "唯一", "首创", "率先", "引领"]

ALLOWED_STATUS = {"in_progress", "done", "partial", "failed"}
ALLOWED_LEDGER_STATUS = {"mapped", "unmapped", "conflict"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB，超限跳过并记录


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def read_json(path, default=None):
    if not path or not os.path.isfile(path):
        return default
    try:
        return json.loads(_read_text(path))
    except (ValueError, OSError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def resolve_dir(default, local, create=False):
    """绝对默认优先；不存在时回退到 cwd 下的本地目录。"""
    if default and os.path.isabs(default):
        if os.path.isdir(default) or create:
            os.makedirs(default, exist_ok=True)
            return default
    cand = os.path.abspath(local)
    if os.path.isdir(cand):
        return cand
    if create:
        os.makedirs(cand, exist_ok=True)
        return cand
    return cand if os.path.isdir(cand) else (default or cand)


def result_path(out_dir):
    if out_dir.endswith(".json"):
        return out_dir
    return os.path.join(out_dir, "result.json")


def find_case(input_dir):
    for name in ("case.json", "task.json"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return p
    return None


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "challenge_id", "challengeId", "caseId"):
        if isinstance(case.get(key), str) and case.get(key):
            return case[key]
    return None


def plan_template(case_id):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id or "",
        "task_type": "fund_proposal_section",
        "section_target": "",
        "genre": "", "audience": "", "format": "", "word_budget": "",
        "citation_style": "", "language": "",
        "seven_elements": {e: {"status": "pending", "claims": []} for e in SEVEN_ELEMENTS},
        "alignment_matrix": [],
        "done_vs_planned": {"done": [], "planned": []},
        "novelty_claims": [],
        "material_gaps": [],
        "manual_confirm": [],
        "anomalies": [],
    }


def ledger_template():
    return {"schema_version": SCHEMA_VERSION, "entries": []}


# ---------------------------------------------------------------- bootstrap
def cmd_bootstrap(args):
    input_dir = resolve_dir(args.input, "input", create=False)
    work_dir = resolve_dir(args.work, "work", create=True)
    out_dir = resolve_dir(args.out, "output", create=True)

    case = read_json(find_case(input_dir), {})
    case_id = extract_case_id(case) or (args.case_id or "unknown")
    contract_hint = None
    if isinstance(case, dict):
        contract_hint = case.get("output_contract") or case.get("output_schema") or case.get("required_fields")

    t0 = time.time()
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "artifact": {"path": "", "genre": "", "audience": "", "format": ""},
        "fact_source_map": [],
        "version_boundary": {"modification_diff": [], "manual_confirm": [],
                             "material_gaps": [], "anomalies": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": now_iso(), "action": "bootstrap",
            "reason": "先落盘一版合法 partial 骨架，此后增量改进",
            "impact": "创建 result.json / plan.json / run_meta.json"}],
        "errors": [], "degradations": [], "runtime": {"started_at": now_iso(), "seconds": 0},
    }
    if contract_hint is not None:
        result["contract_hint"] = contract_hint
    if contract_hint and isinstance(contract_hint, dict):
        for k, v in contract_hint.items():
            if k not in result and isinstance(v, (dict, list)):
                result[k] = v if isinstance(v, (dict, list)) else v
    # 若题目给出 required_fields 列表，逐一并入（缺值 null/[]）
    if isinstance(contract_hint, dict) and isinstance(contract_hint.get("required_fields"), list):
        for f in contract_hint["required_fields"]:
            result.setdefault(f, None)

    write_json(result_path(out_dir), result)
    write_json(os.path.join(work_dir, "plan.json"), plan_template(case_id))
    write_json(os.path.join(work_dir, "evidence_ledger.json"), ledger_template())
    write_json(os.path.join(work_dir, "run_meta.json"), {
        "case_id": case_id, "input_dir": input_dir, "work_dir": work_dir,
        "out_dir": out_dir, "bootstrap_seconds": round(time.time() - t0, 2),
        "started_at": now_iso(),
    })
    print(json.dumps({"ok": True, "case_id": case_id, "result": result_path(out_dir),
                      "plan": os.path.join(work_dir, "plan.json")}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- scan
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_scan(args):
    input_dir = resolve_dir(args.input, "input", create=False)
    work_dir = resolve_dir(args.work, "work", create=True)
    manifest = {"schema_version": SCHEMA_VERSION, "scanned_at": now_iso(),
                "input_dir": input_dir, "files": [], "skipped": []}
    if not os.path.isdir(input_dir):
        manifest["skipped"].append({"path": input_dir, "reason": "input_dir_missing"})
    else:
        for root, _, files in os.walk(input_dir):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                try:
                    size = os.path.getsize(p)
                except OSError as exc:
                    manifest["skipped"].append({"path": p, "reason": str(exc)})
                    continue
                rel = os.path.relpath(p, input_dir)
                if size > MAX_FILE_SIZE:
                    manifest["skipped"].append({"path": rel, "reason": "too_large", "size": size})
                    continue
                try:
                    manifest["files"].append({
                        "path": rel, "size": size,
                        "sha256": sha256_file(p),
                        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(p))),
                    })
                except OSError as exc:
                    manifest["skipped"].append({"path": rel, "reason": str(exc)})
    write_json(os.path.join(work_dir, "source_manifest.json"), manifest)
    print(json.dumps({"ok": True, "files": len(manifest["files"]),
                      "skipped": len(manifest["skipped"])}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- plan-init
def cmd_plan_init(args):
    work_dir = resolve_dir(args.work, "work", create=True)
    case_id = args.case_id
    if not case_id:
        meta = read_json(os.path.join(work_dir, "run_meta.json"), {})
        case_id = (meta or {}).get("case_id", "")
    write_json(os.path.join(work_dir, "plan.json"), plan_template(case_id))
    print(json.dumps({"ok": True, "plan": os.path.join(work_dir, "plan.json")}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- ledger
def _load_ledger(work_dir):
    p = os.path.join(work_dir, "evidence_ledger.json")
    lg = read_json(p, None)
    if not isinstance(lg, dict) or not isinstance(lg.get("entries"), list):
        lg = ledger_template()
    return p, lg


def cmd_ledger_add(args):
    work_dir = resolve_dir(args.work, "work", create=True)
    p, lg = _load_ledger(work_dir)
    status = args.status if args.status in ALLOWED_LEDGER_STATUS else "unmapped"
    if not args.claim:
        print(json.dumps({"ok": False, "error": "claim required"}, ensure_ascii=False))
        return 2
    entry = {
        "claim": args.claim, "kind": args.kind or "claim", "ref": args.ref or "",
        "loc": args.loc or "", "quote": args.quote or "", "status": status,
        "note": args.note or "", "added_at": now_iso(),
    }
    lg["entries"].append(entry)
    write_json(p, lg)
    print(json.dumps({"ok": True, "entries": len(lg["entries"])}, ensure_ascii=False))
    return 0


def cmd_ledger_export(args):
    work_dir = resolve_dir(args.work, "work", create=True)
    _, lg = _load_ledger(work_dir)
    out = [{
        "claim": e.get("claim"), "kind": e.get("kind", "claim"),
        "source_ref": {"file": e.get("ref", ""), "loc": e.get("loc", ""),
                       "quote": e.get("quote", "")},
        "status": e.get("status", "unmapped"), "note": e.get("note", ""),
    } for e in lg["entries"]]
    write_json(os.path.join(work_dir, "fact_source_map.json"),
               {"schema_version": SCHEMA_VERSION, "map": out})
    print(json.dumps({"ok": True, "entries": len(out)}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- verify
def _scan_text_for_flags(text):
    hits = []
    if not text:
        return hits
    for w in HARD_BAN:
        if w in text:
            hits.append({"phrase": w, "level": "hard_ban"})
    for w in REVIEW_FLAG:
        if w in text:
            hits.append({"phrase": w, "level": "review_flag"})
    return hits


def cmd_verify(args):
    work_dir = resolve_dir(args.work, "work", create=True)
    out_dir = resolve_dir(args.out, "output", create=True)
    result = read_json(result_path(out_dir), {})
    plan = read_json(os.path.join(work_dir, "plan.json"), {})
    manifest = read_json(os.path.join(work_dir, "source_manifest.json"), {})
    _, lg = _load_ledger(work_dir)

    issues, warnings, info = [], [], []

    if not result.get("case_id"):
        issues.append("result.json 缺 case_id")
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("result.json status 非法: %r" % result.get("status"))
    art = result.get("artifact") or {}
    if not art.get("path"):
        warnings.append("artifact.path 为空（若 status=done 则必须指向真实文件）")
    elif not os.path.isfile(art["path"]) and result.get("status") == "done":
        issues.append("artifact.path 指向不存在的文件: %s" % art["path"])
    elif not os.path.isfile(art["path"]):
        warnings.append("artifact.path 指向不存在的文件: %s" % art["path"])

    fsmap = result.get("fact_source_map") or []
    no_quote = [i for i, e in enumerate(fsmap) if not (e.get("source_ref") or {}).get("quote")]
    if no_quote:
        issues.append("fact_source_map 有 %d 条缺逐字 quote（索引 %s）" % (len(no_quote), no_quote[:5]))
    no_loc = [i for i, e in enumerate(fsmap) if not ((e.get("source_ref") or {}).get("loc") and (e.get("source_ref") or {}).get("file"))]
    if no_loc:
        warnings.append("fact_source_map 有 %d 条缺 locator（索引 %s）" % (len(no_loc), no_loc[:5]))

    # plan 校验
    elems = plan.get("seven_elements") or {}
    missing_elems = [e for e in SEVEN_ELEMENTS if e not in elems]
    if missing_elems:
        issues.append("plan.json 缺七要素: %s" % ",".join(missing_elems))
    draft_elems = [e for e, v in elems.items() if isinstance(v, dict) and v.get("status") == "draft"]
    info.append("七要素已起草 %d/%d" % (len(draft_elems), len(SEVEN_ELEMENTS)))

    matrix = plan.get("alignment_matrix") or []
    empty_rows = [i for i, r in enumerate(matrix)
                  if not (isinstance(r, dict) and r.get("objective") and r.get("content")
                          and r.get("route_steps") and r.get("indicator"))]
    if empty_rows:
        issues.append("alignment_matrix 有 %d 行不完整（目标/内容/路线/指标缺一，索引 %s）"
                      % (len(empty_rows), empty_rows[:5]))
    info.append("对齐矩阵行数: %d" % len(matrix))

    done_planned = plan.get("done_vs_planned") or {}
    if not (done_planned.get("done") or done_planned.get("planned")):
        warnings.append("plan.json 未标注已完成/拟开展（查得到维度需要）")

    # 材料清单与来源
    files = manifest.get("files") or []
    if result.get("sources") and not files:
        warnings.append("result.sources 非空但 source_manifest 无文件，请先运行 scan")

    # 禁止修饰语扫描（正文 + 事实台账 + 对齐矩阵文本）
    blob = json.dumps(result.get("fact_source_map") or [], ensure_ascii=False) + \
        json.dumps(plan.get("novelty_claims") or [], ensure_ascii=False)
    art_text = ""
    if art.get("path") and os.path.isfile(art["path"]):
        art_text = _read_text(art["path"])
    flag_hits = _scan_text_for_flags(art_text + "\n" + blob)
    hard = [h for h in flag_hits if h["level"] == "hard_ban"]
    soft = [h for h in flag_hits if h["level"] == "review_flag"]
    if hard:
        issues.append("正文/台账含无法核验修饰语: %s（须删除或改为具体比较对象）"
                      % ",".join(sorted({h["phrase"] for h in hard})))
    if soft:
        warnings.append("含需核验修饰语（须给可核验比较对象或删除）: %s"
                        % ",".join(sorted({h["phrase"] for h in soft})))

    report = {
        "schema_version": SCHEMA_VERSION, "verified_at": now_iso(),
        "pass": (len(issues) == 0), "issues": issues, "warnings": warnings, "info": info,
        "ledger_entries": len(lg.get("entries") or []),
        "seven_drafted": len(draft_elems), "matrix_rows": len(matrix),
    }
    write_json(os.path.join(work_dir, "verify_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


# ---------------------------------------------------------------- finalize
def cmd_finalize(args):
    work_dir = resolve_dir(args.work, "work", create=True)
    out_dir = resolve_dir(args.out, "output", create=True)
    rp = result_path(out_dir)
    result = read_json(rp, {})
    plan = read_json(os.path.join(work_dir, "plan.json"), {})
    manifest = read_json(os.path.join(work_dir, "source_manifest.json"), {})
    _, lg = _load_ledger(work_dir)

    fsmap = [{
        "claim": e.get("claim"), "kind": e.get("kind", "claim"),
        "source_ref": {"file": e.get("ref", ""), "loc": e.get("loc", ""),
                       "quote": e.get("quote", "")},
        "status": e.get("status", "unmapped"), "note": e.get("note", ""),
    } for e in lg.get("entries", [])]
    if fsmap:
        result["fact_source_map"] = fsmap

    result["sources"] = [{
        "path": f.get("path"), "size": f.get("size"), "sha256": f.get("sha256"),
        "type": os.path.splitext(f.get("path", ""))[1].lstrip(".") or "unknown",
    } for f in manifest.get("files", [])]

    vb = result.setdefault("version_boundary", {})
    vb.setdefault("material_gaps", [])
    vb.setdefault("manual_confirm", [])
    vb.setdefault("anomalies", [])
    for key in ("material_gaps", "manual_confirm", "anomalies"):
        extra = plan.get(key) or []
        if isinstance(extra, list) and extra:
            vb[key] = list({json.dumps(x, ensure_ascii=False, sort_keys=True): x for x in vb[key] + extra}.values())

    if plan.get("done_vs_planned"):
        result.setdefault("done_vs_planned", plan["done_vs_planned"])

    status = args.status
    if status not in ALLOWED_STATUS:
        status = result.get("status") or "partial"
    result["status"] = status
    if status != "done":
        result.setdefault("limitations", [])

    rt = result.setdefault("runtime", {})
    try:
        rt["seconds"] = round(time.time() - calendar.timegm(
            time.strptime(rt.get("started_at", now_iso()), "%Y-%m-%dT%H:%M:%SZ")), 1)
    except (ValueError, OverflowError):
        rt["seconds"] = 0

    result.setdefault("pending_human", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("change_log", []).append({
        "time": now_iso(), "action": "finalize", "reason": "合并台账/清单/边界并定稿",
        "impact": "status=%s; fact_source_map=%d; sources=%d" % (status, len(fsmap), len(result["sources"]))})

    write_json(rp, result)
    print(json.dumps({"ok": True, "result": rp, "status": status,
                      "facts": len(fsmap), "sources": len(result["sources"])}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="基金申请书写作可复核脚手架")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--work", default="/app/work")
    common.add_argument("--out", default="/app/output")
    common.add_argument("--case-id", default="")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--case-id", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", parents=[common], help="落盘骨架 result.json/plan.json/run_meta.json")
    sub.add_parser("scan", parents=[common], help="盘点材料生成 source_manifest.json")
    sub.add_parser("plan-init", parents=[common], help="(重新)写 plan.json 模板")

    p_la = sub.add_parser("ledger-add", parents=[common], help="追加事实台账")
    p_la.add_argument("--claim", required=True)
    p_la.add_argument("--kind", default="claim")
    p_la.add_argument("--ref", default="")
    p_la.add_argument("--loc", default="")
    p_la.add_argument("--quote", default="")
    p_la.add_argument("--status", default="unmapped")
    p_la.add_argument("--note", default="")

    sub.add_parser("ledger-export", parents=[common], help="导出 fact_source_map.json")
    sub.add_parser("verify", parents=[common], help="交叉校验并写 verify_report.json")

    p_fin = sub.add_parser("finalize", parents=[common], help="合并进 result.json")
    p_fin.add_argument("--status", default="", help="done|partial|failed|in_progress")

    args = ap.parse_args(argv)
    handlers = {
        "bootstrap": cmd_bootstrap, "scan": cmd_scan, "plan-init": cmd_plan_init,
        "ledger-add": cmd_ledger_add, "ledger-export": cmd_ledger_export,
        "verify": cmd_verify, "finalize": cmd_finalize,
    }
    try:
        return handlers[args.cmd](args)
    except Exception as exc:  # 任何异常都不应让进程崩溃，返回可读错误
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
