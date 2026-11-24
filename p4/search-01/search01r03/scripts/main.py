#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 主入口（纯标准库）。

子命令：
  run        骨架流水线：skeleton -> parse -> plan -> assemble（不执行检索）
  skeleton   写一版合法的 partial 骨架 result.json（务必在启动后 3 分钟内落盘）
  parse      解析 /input -> 工作区 case_summary.json
  plan       由 case_summary 生成查询计划 -> 工作区 plan.json
  import     导入候选文献 -> 工作区 records.json（DOI/标题去重 upsert）
  evidence   追加查询/来源证据 -> 工作区 evidence.json
  assemble   去重+打分+排序，写 /app/output/result.json 与 candidates.md
  selfcheck  校验 result.json 是否满足输出契约

确定性说明：所有写入 result.json 的内容均不含墙钟时间，时间顺序用单调
seq 表达；assemble 是 (输入目录 + 工作区状态) 的纯函数，重复运行产物一致。
"""

import argparse
import json
import os
import sys

# 让子进程/管道输出固定为 UTF-8，避免 Windows 控制台 GBK 与评测端编码不一致。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as core

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

# 标准输出字段（与题目 output_contract 字段同层回填；同名冲突时保留在 contract 内）
STANDARD_KEYS = {
    "case_id", "status", "research_question", "search_scope", "results",
    "evidence", "sources", "uncertainty", "pending_human", "change_log",
    "status_notes", "limitations", "errors", "degradations", "runtime",
    "determinism", "outputs", "contract",
}


# ---------------------------------------------------------------------------
# 稳健 IO
# ---------------------------------------------------------------------------

def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """读文本，容错 BOM/坏编码/坏 JSON；返回 (text, degradations)。"""
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:" + str(path) + ":" + str(size)]
    try:
        data = open(path, "rb").read()
    except OSError as e:
        return "", ["read_failed:" + str(path) + ":" + str(e)[:80]]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), ["fallback_decode:" + str(path)]


def read_json(path, default=None, max_bytes=MAX_FILE_BYTES):
    if default is None:
        default = {}
    text, deg = read_text(path, max_bytes)
    if deg:
        return default, deg
    if not text.strip():
        return default, ["empty:" + str(path)]
    try:
        return json.loads(text), []
    except Exception as e:
        return default, ["bad_json:" + str(path) + ":" + str(e)[:120]]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_text failed:", path, str(e)[:200], file=sys.stderr)
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_json failed:", path, str(e)[:200], file=sys.stderr)
        return False


def work_path(work_dir, name):
    return os.path.join(work_dir, name)


def load_json_list(work_dir, name):
    obj, _ = read_json(work_path(work_dir, name), [])
    return obj if isinstance(obj, list) else []


def append_change(work_dir, action, reason, status="partial"):
    """追加确定性 change_log：seq 单调递增，无墙钟时间。"""
    path = work_path(work_dir, "change_log.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "seq": len(entries),
        "action": action,
        "reason": reason,
        "status": status,
    })
    write_json(path, entries)


# ---------------------------------------------------------------------------
# 输入发现与解析
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    out = []
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            out.append(os.path.join(root, n))
    return out


def find_case_file(files):
    for f in files:
        if os.path.basename(f).lower() == "case.json":
            return f
    for f in files:
        b = os.path.basename(f).lower()
        if b.startswith("case") and b.endswith(".json"):
            return f
    return None


def find_task_file(files):
    for f in files:
        if os.path.basename(f).lower() == "task.md":
            return f
    for f in files:
        b = os.path.basename(f).lower()
        if b.endswith(".md") and "task" in b:
            return f
    for f in files:
        if f.lower().endswith(".md"):
            return f
    return None


def parse_case(input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    summary = {
        "case_path": case_path,
        "task_path": task_path,
        "files": files,
        "case_id": core.extract_case_id(case_data, task_text, case_path),
        "research_question": core.extract_research_question(case_data, task_text),
        "contract": core.extract_contract(case_data),
        "degradations": degradations,
        "has_materials": any(os.path.basename(f).lower().endswith(".pdf")
                             for f in files),
    }
    write_json(work_path(work_dir, "case_summary.json"), summary)
    append_change(work_dir, "parse",
                  "case_id=%s question_len=%d" % (summary["case_id"],
                                                  len(summary["research_question"])),
                  "ok" if not degradations else "partial")
    return summary


def make_plan(work_dir):
    summary, _ = read_json(work_path(work_dir, "case_summary.json"), {})
    rq = summary.get("research_question") or ""
    plan = core.build_plan(rq)
    write_json(work_path(work_dir, "plan.json"), plan)
    append_change(work_dir, "plan",
                  "生成 %d 个查询种子、%d 个概念" % (len(plan["queries"]),
                                                   len(plan["concepts"])), "ok")
    return plan


# ---------------------------------------------------------------------------
# 各子命令
# ---------------------------------------------------------------------------

def cmd_skeleton(args):
    """写一版合法 partial 骨架，务必在启动后 3 分钟内落盘。"""
    ensure_dir(args.work)
    ensure_dir(args.output)
    # 若已有更完整的产物，不覆盖（只做增量改进）
    existing = os.path.join(args.output, "result.json")
    if os.path.isfile(existing):
        return 0
    result = {
        "case_id": "unknown",
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "results": [],
        "evidence": [],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [],
                        "coverage_gaps": [], "notes": []},
        "pending_human": [],
        "change_log": [{"seq": 0, "action": "skeleton", "reason": "启动骨架落盘", "status": "partial"}],
        "status_notes": ["骨架结果：候选集尚未检索，status 暂为 partial。"],
        "limitations": ["尚未执行检索；结果、证据与来源均为空。"],
        "errors": [],
        "degradations": [],
        "runtime": {"budget_seconds": 900, "model_requests_budget": 64,
                    "deterministic": True, "note": "墙钟时间由平台记录，本文件不含墙钟时间"},
        "determinism": {"fingerprint": None, "excluded_fields": []},
        "outputs": {"result": "result.json", "candidates": "candidates.md"},
        "contract": {},
    }
    result["determinism"]["fingerprint"] = core.build_fingerprint(result)
    write_json(existing, result)
    append_change(args.work, "skeleton", "写入 partial 骨架", "partial")
    return 0


def cmd_parse(args):
    ensure_dir(args.work)
    parse_case(args.input, args.work)
    return 0


def cmd_plan(args):
    ensure_dir(args.work)
    summary_path = work_path(args.work, "case_summary.json")
    if not os.path.isfile(summary_path):
        parse_case(args.input, args.work)
    make_plan(args.work)
    return 0


def cmd_import(args):
    ensure_dir(args.work)
    raw, deg = read_json(args.file, [])
    if deg:
        append_change(args.work, "import", "导入失败: %s" % ";".join(deg), "partial")
        return 0
    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("records") or raw.get("candidates") or []
    if not isinstance(raw, list):
        append_change(args.work, "import", "导入文件结构不是列表", "partial")
        return 0
    existing = load_json_list(args.work, "records.json")
    merged = core.dedup_records(existing + raw)
    write_json(work_path(args.work, "records.json"), merged)
    append_change(args.work, "import",
                  "导入 %d 条，去重后 %d 条" % (len(raw), len(merged)), "ok")
    return 0


def cmd_evidence(args):
    ensure_dir(args.work)
    try:
        item = json.loads(args.inline)
    except Exception as e:
        print("evidence --inline 不是合法 JSON:", str(e)[:160], file=sys.stderr)
        return 0
    if not isinstance(item, dict):
        return 0
    entries = load_json_list(args.work, "evidence.json")
    entries.append({
        "seq": len(entries),
        "query": item.get("query"),
        "source": item.get("source"),
        "database": item.get("database"),
        "rationale": item.get("rationale"),
        "kind": item.get("kind", "query"),
        "scope": item.get("scope"),
    })
    write_json(work_path(args.work, "evidence.json"), entries)
    append_change(args.work, "evidence", "追加证据 seq=%d" % (len(entries) - 1), "ok")
    return 0


def render_candidates_md(result, output_dir):
    lines = [
        "# 学术文献检索候选清单（可编辑）",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- research_question: %s" % (result.get("research_question") or "（未提供）"),
        "- 说明：本文件由 result.json 确定性渲染，供人工复核；修改结果请改 result.json。",
        "",
    ]
    results = result.get("results") or []
    if not results:
        lines.append("（候选为空：status=insufficient_evidence，未补造文献。）")
    else:
        for r in results:
            authors = ", ".join(r.get("authors") or []) or "（缺作者）"
            lines.append("## %d. %s" % (r.get("rank"), r.get("title") or "（缺标题）"))
            lines.append("- 作者: %s" % authors)
            lines.append("- 年份: %s | 期刊: %s" % (r.get("year") or "（缺）",
                                                   r.get("journal") or "（缺）"))
            lines.append("- DOI: %s" % (r.get("doi") or "（缺）"))
            lines.append("- URL: %s" % (r.get("url") or "（缺）"))
            lines.append("- 库: %s | 相关性 %.2f | 得分 %.4f" % (
                r.get("database") or "unknown",
                core.relevance_value(r), r.get("score") or 0.0))
            lines.append("- 相关性依据: %s" % (r.get("relevance_reason") or "（缺）"))
            ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
            if ev.get("locator") or ev.get("quote"):
                lines.append("- 证据 locator: %s" % (ev.get("locator") or "（缺）"))
                lines.append("- 证据 quote: %s" % (ev.get("quote") or "（缺）"))
            lines.append("")
    write_text(os.path.join(output_dir, "candidates.md"), "\n".join(lines))


def cmd_assemble(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    summary, _ = read_json(work_path(args.work, "case_summary.json"), {})
    if not summary:
        summary = parse_case(args.input, args.work)
    plan, _ = read_json(work_path(args.work, "plan.json"), {})
    records = load_json_list(args.work, "records.json")
    evidence = load_json_list(args.work, "evidence.json")

    ranked = core.rank_records(core.dedup_records(records))
    status = core.decide_status(ranked)

    # 先追加本次 assemble 变更，再读取完整 change_log，保证结果自含本步骤
    append_change(args.work, "assemble", "组装完成，status=%s，结果 %d 条"
                  % (status, len(ranked)), status)
    change_log = load_json_list(args.work, "change_log.json")

    result = {
        "case_id": summary.get("case_id", "unknown"),
        "status": status,
        "research_question": summary.get("research_question") or None,
        "search_scope": summary.get("search_scope"),
        "results": ranked,
        "evidence": evidence,
        "sources": core.merge_sources(ranked, evidence),
        "uncertainty": _build_uncertainty(ranked, evidence),
        "pending_human": _build_pending(ranked),
        "change_log": change_log,
        "status_notes": _build_status_notes(ranked, evidence, summary),
        "limitations": _build_limitations(ranked, evidence, summary),
        "errors": summary.get("errors", []),
        "degradations": summary.get("degradations", []),
        "runtime": {"budget_seconds": 900, "model_requests_budget": 64,
                    "deterministic": True,
                    "note": "墙钟时间由平台记录；本文件不含墙钟时间以保证确定性"},
        "outputs": {"result": "result.json", "candidates": "candidates.md",
                    "plan": "plan.json"},
    }

    # 回填题目 output_contract 字段：不覆盖标准字段，冲突保留在 contract 内
    contract = summary.get("contract") or {}
    result["contract"] = contract
    for k, v in contract.items():
        if k not in STANDARD_KEYS and k not in result:
            result[k] = v

    result["determinism"] = {
        "fingerprint": core.build_fingerprint(result),
        "excluded_fields": [],
        "repeat_check": "python3 scripts/selftest.py --quick",
    }
    write_json(os.path.join(args.output, "result.json"), result)
    render_candidates_md(result, args.output)
    return 0


def _build_uncertainty(records, evidence):
    missing = []
    for r in (records or []):
        for k in ("title", "authors", "year", "journal", "doi"):
            v = r.get(k)
            if v in (None, "", [], {}):
                missing.append("results[%d].%s" % (r.get("rank"), k))
    return {
        "missing_metadata": missing,
        "conflicts": [],
        "coverage_gaps": ["若检索为空或关键研究方向未覆盖，需在此补充说明（不补造文献）。"],
        "notes": ["同指标多来源不一致时按 source_conflict 记录全部值，不擅自取舍。"],
    }


def _build_pending(records):
    out = []
    for r in (records or []):
        if r.get("doi") in (None, "") and r.get("url") in (None, ""):
            out.append("results[%d] 缺 DOI/URL，需人工核验来源" % r.get("rank"))
        ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
        if not ev.get("quote"):
            out.append("results[%d] 缺逐字证据 quote" % r.get("rank"))
    return out


def _build_status_notes(records, evidence, summary):
    notes = []
    if not records:
        notes.append("候选为空：status=insufficient_evidence；下一步建议按 plan.json 执行检索后 import。")
    if not evidence:
        notes.append("尚无查询证据：每次检索调用都应通过 evidence 命令记录 query/source/rationale。")
    if summary.get("degradations"):
        notes.append("输入存在降级项：%s" % ";".join(summary["degradations"]))
    return notes


def _build_limitations(records, evidence, summary):
    limits = []
    if not records:
        limits.append("未检索到候选文献，本结果仅为骨架，不构成完整检索交付。")
    if not summary.get("research_question"):
        limits.append("未能从输入解析出研究问题，查询计划为空。")
    return limits


def cmd_selfcheck(args):
    """校验 result.json 是否满足输出契约；PASS/FAIL 清单，FAIL 时退出码 1。"""
    result, deg = read_json(os.path.join(args.output, "result.json"), {})
    checks = []
    checks.append(("result.json 存在且为合法 JSON", not deg and bool(result)))
    checks.append(("case_id 非空", bool(result.get("case_id"))))
    checks.append(("status 合法", result.get("status") in
                   ("ok", "partial", "insufficient_evidence", "input_error")))
    checks.append(("results 为列表", isinstance(result.get("results"), list)))
    checks.append(("evidence 为列表", isinstance(result.get("evidence"), list)))
    checks.append(("sources 为列表", isinstance(result.get("sources"), list)))
    checks.append(("uncertainty 为对象", isinstance(result.get("uncertainty"), dict)))
    checks.append(("change_log 为列表", isinstance(result.get("change_log"), list)))
    checks.append(("pending_human 为列表", isinstance(result.get("pending_human"), list)))
    checks.append(("determinism.fingerprint 非空", bool((result.get("determinism") or {}).get("fingerprint"))))
    # 每条结果必须有 title 与（doi 或 url）；有结果时需 relevance_reason 与证据
    for i, r in enumerate(result.get("results") or []):
        ok = bool(r.get("title")) and bool(r.get("doi") or r.get("url"))
        checks.append(("results[%d] 有 title + doi/url" % i, ok))
        if result.get("status") == "ok":
            checks.append(("results[%d] 有 relevance_reason" % i,
                           bool(r.get("relevance_reason"))))
    passed = all(v for _, v in checks)
    for name, ok in checks:
        print(("PASS" if ok else "FAIL") + " | " + name)
    print("selfcheck:", "PASS" if passed else "FAIL")
    write_json(work_path(args.work, "selfcheck.json"),
               {"passed": passed, "checks": [{"name": n, "ok": bool(o)}
                                             for n, o in checks]})
    return 0 if passed else 1


def reset_work_state(work_dir):
    """清空工作区状态文件，保证 `run` 幂等：同一输入、同一目录重复运行产物一致。"""
    ensure_dir(work_dir)
    for name in ("change_log.json", "records.json", "evidence.json",
                 "case_summary.json", "plan.json", "selfcheck.json"):
        p = work_path(work_dir, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def cmd_run(args):
    """骨架流水线：skeleton -> parse -> plan -> assemble（不执行检索）。

    `run` 会先清空工作区状态与旧产物，保证重复运行（如评测的多次重复）
    逐字节一致；运行期增量改进请用 import / evidence / assemble，不要重复调用 run。
    """
    reset_work_state(args.work)
    for name in ("result.json", "candidates.md"):
        p = os.path.join(args.output, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    cmd_skeleton(args)
    cmd_parse(args)
    cmd_plan(args)
    return cmd_assemble(args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)
    p = argparse.ArgumentParser(
        prog="main.py",
        description="学术文献检索 Skill：稳健 IO、确定性去重/打分/排序与可复核交付。",
        parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("run", "skeleton", "parse", "plan", "assemble", "selfcheck"):
        sub.add_parser(name, parents=[common])

    imp = sub.add_parser("import", parents=[common])
    imp.add_argument("--file", required=True)
    ev = sub.add_parser("evidence", parents=[common])
    ev.add_argument("--inline", required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    dispatch = {
        "run": cmd_run, "skeleton": cmd_skeleton, "parse": cmd_parse,
        "plan": cmd_plan, "import": cmd_import, "evidence": cmd_evidence,
        "assemble": cmd_assemble, "selfcheck": cmd_selfcheck,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
