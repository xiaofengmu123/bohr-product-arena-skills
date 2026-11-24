#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — sci-library-organizer 编排入口（library 第 3 题，变体：时间预算与早落盘）。

仅依赖 Python 3.8+ 标准库 + 同目录 core_impl.py。核心承诺：
  * 启动后立刻写 /app/output/result.json 的 partial 骨架（phase 1_skeleton，
    硬截止 180s，远低于总预算 900s）；之后任何阶段只增量覆盖，任何中断都保留
    至少一份合法 JSON；
  * 每阶段把耗时/事件写回 change_log 与 runtime.budget/actual，使预算可复核；
  * 契约键原样回填（case.json.output_contract 或默认契约），未知键不改名不删除；
  * 绝不修改 /input；复制件只落到 /app/output/library/，写 rollback.json 供回滚；
  * 绝不编造作者/年份/DOI：缺证据写 not_reported 并列入 pending_human。

用法:
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
        [--decisions /app/work/decisions.json] [--hierarchy document_type,topic] \
        [--apply] [--budget 900] [--skeleton-deadline 180]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import core_impl as core

__version__ = "2.0.0"

# 默认契约：从题面「Skill 运行后的输出要求」三组内容推导（缺 output_contract 时使用）。
DEFAULT_CONTRACT = {
    "library": {"catalog": [], "hierarchy": {}, "metadata": {}, "versions": []},
    "evidence": {"hashes": {}, "permissions": {}, "text": {}, "locators": []},
    "operations": [],
    "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                 "duplicates": [], "recoveries": []},
}

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
CONTRACT_KEYS = ("output_contract", "outputContract", "output_schema",
                 "result_contract", "contract", "result_schema", "output")
SUPPORTED_DIMS = ("document_type", "topic", "year", "ext", "source", "type",
                  "date", "project", "subject", "doctype")

# Agent 层阶段预算（总 900 秒 / 64 次模型请求）。脚本本身不做 LLM 调用，
# 此处把请求预算与墙钟预算固化为可复核数据；前 3 分钟必须完成 0+1 两步。
AGENT_PHASE_PLAN = [
    ("0_read_task", 60, 3),
    ("1_skeleton", 30, 1),
    ("2_inventory", 90, 3),
    ("3_extract_text", 180, 8),
    ("4_semantics", 270, 12),
    ("5_plan_review", 90, 2),
    ("6_apply", 60, 1),
    ("7_selfcheck", 120, 3),
]


class Budget:
    """墙钟预算跟踪：总 900s；骨架必须在 skeleton_deadline 秒内落盘。"""

    def __init__(self, total_seconds=900, skeleton_deadline=180):
        self.total = total_seconds
        self.skeleton_deadline = skeleton_deadline
        self.t0 = time.monotonic()
        self.phases = []
        self.skeleton_elapsed_s = None

    def elapsed(self):
        return time.monotonic() - self.t0

    def remaining(self):
        return max(0.0, self.total - self.elapsed())

    def begin(self, name):
        return Phase(self, name)

    def mark_skeleton(self):
        self.skeleton_elapsed_s = round(self.elapsed(), 2)

    def snapshot(self):
        return {
            "total_seconds": self.total,
            "max_model_requests": 64,
            "skeleton_deadline_seconds": self.skeleton_deadline,
            "skeleton_elapsed_seconds": self.skeleton_elapsed_s,
            "elapsed_seconds": round(self.elapsed(), 2),
            "remaining_seconds": round(self.remaining(), 2),
            "phase_plan": [{"phase": n, "planned_seconds": s, "model_requests": r}
                           for n, s, r in AGENT_PHASE_PLAN],
            "phases": self.phases,
            "note": "64 model requests is the Agent-level budget staged above; "
                    "this script records wall-clock only",
        }


class Phase:
    def __init__(self, budget, name):
        self.budget = budget
        self.name = name
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.budget.phases.append({
            "phase": self.name, "elapsed_s": round(time.monotonic() - self.t0, 2)})


# --------------------------------------------------------------------------- #
# 契约与状态
# --------------------------------------------------------------------------- #

def _find_contract(case):
    if not isinstance(case, dict):
        return None
    for k in CONTRACT_KEYS:
        v = case.get(k)
        if isinstance(v, dict) and v:
            return v
    for sub in ("rules", "output", "result", "spec"):
        subv = case.get(sub)
        if isinstance(subv, dict):
            for k in CONTRACT_KEYS:
                v = subv.get(k)
                if isinstance(v, dict) and v:
                    return v
    return None


def read_case_context(input_dir):
    degradations = []
    case_id = "unknown"
    contract = DEFAULT_CONTRACT
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        case_raw = core.read_json(case_path)
        if isinstance(case_raw, dict):
            case_id = (case_raw.get("case_id") or case_raw.get("id")
                       or case_raw.get("challenge_id") or case_raw.get("armiId") or "unknown")
            found = _find_contract(case_raw)
            if found:
                contract = found
        else:
            degradations.append("case.json unreadable or invalid; contract falls back to default")
    else:
        degradations.append("case.json missing; contract falls back to default")
    if not os.path.isfile(os.path.join(input_dir, "task.md")):
        degradations.append("task.md missing")
    return case_id, contract, degradations


def deep_placeholder(v):
    if isinstance(v, dict):
        return {k: deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def new_state(case_id, contract):
    state = {"case_id": case_id}
    for k in contract:
        state[k] = deep_placeholder(contract[k])
    state.update({
        "status": "partial", "sources": [], "uncertainty": {},
        "change_log": [], "errors": [], "degradations": [],
        "pending_human": [], "runtime": {},
    })
    return state


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def log_change(state, phase, event, detail=None):
    state["change_log"].append({
        "ts": core.now_iso(), "phase": phase, "event": event, "detail": detail})


# --------------------------------------------------------------------------- #
# 结果装配
# --------------------------------------------------------------------------- #

def build_sources(catalog):
    by_source = {}
    for r in catalog:
        for ev in r.get("evidence") or []:
            by_source.setdefault(r["source"], []).append(ev)
    out = []
    for src in sorted(by_source):
        out.append({"source": src, "sha256": None, "locator": src,
                    "quotes": by_source[src]})
    return out


def build_evidence(catalog):
    hashes = {r["source"]: r["sha256"] for r in catalog}
    perms = {r["source"]: r["permissions"] for r in catalog}
    text = {r["source"]: {"has_text_layer": bool(r.get("has_text_layer")),
                          "char_count": r.get("text_len", 0)}
            for r in catalog}
    locators = []
    for r in catalog:
        for ev in r.get("evidence") or []:
            locators.append({"source": r["source"], "field": ev["field"],
                             "locator": ev["locator"], "quote": ev["quote"]})
    return {"hashes": hashes, "permissions": perms, "text": text, "locators": locators}


def build_uncertainty(catalog):
    unclassified = sum(1 for r in catalog
                       if r.get("file_type") in ("unknown", "document", "unclassified"))
    no_text = sum(1 for r in catalog if not r.get("has_text_layer"))
    missing = {"title": 0, "authors": 0, "year": 0, "journal": 0, "doi": 0}
    for r in catalog:
        m = r.get("metadata") or {}
        for k in missing:
            if not m.get(k):
                missing[k] += 1
    return {"unclassified_files": unclassified,
            "files_without_text_layer": no_text,
            "missing_metadata_fields": missing}


def decide_status(state, file_count, input_error):
    if input_error:
        return "input_error"
    if file_count == 0:
        return "insufficient_evidence"
    failures = state.get("failures") or {}
    if failures.get("skipped") or failures.get("permission_errors") or failures.get("conflicts"):
        return "partial"
    if state.get("pending_human"):
        return "partial"
    unc = state.get("uncertainty") or {}
    if unc.get("unclassified_files") or unc.get("files_without_text_layer"):
        return "partial"
    return "ok"


def paper_like(file_type):
    return file_type in ("journal_article", "conference_paper", "preprint",
                         "thesis", "supplementary", "document")


def run(args):
    t_start = time.time()
    budget = Budget(total_seconds=args.budget, skeleton_deadline=args.skeleton_deadline)
    input_dir, output_dir, work_dir = args.input, args.output, args.work
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    input_error = not os.path.isdir(input_dir)
    out_json = os.path.join(output_dir, "result.json")

    # ---- 1_skeleton：读题 + 先落 partial 骨架（硬截止前 3 分钟）----
    with budget.begin("1_skeleton"):
        case_id, contract, ctx_degrad = read_case_context(input_dir)
        state = new_state(case_id, contract)
        state["degradations"].extend(ctx_degrad)
        state["sources"] = [{
            "source": os.path.join(input_dir, "case.json").replace(os.sep, "/"),
            "role": "case_spec",
            "readable": os.path.isfile(os.path.join(input_dir, "case.json"))},
            {"source": os.path.join(input_dir, "task.md").replace(os.sep, "/"),
             "role": "task_spec",
             "readable": os.path.isfile(os.path.join(input_dir, "task.md"))}]
        if input_error:
            state["status"] = "input_error"
            state["errors"].append("input directory not found: %s" % input_dir)
        budget.mark_skeleton()
        state["runtime"] = {
            "budget": budget.snapshot(),
            "actual": {"skeleton_written_at": core.now_iso(),
                       "elapsed_seconds": round(budget.elapsed(), 2), "phases": []},
        }
        log_change(state, "1_skeleton", "partial skeleton written before analysis",
                   {"elapsed_s": budget.skeleton_elapsed_s,
                    "deadline_s": budget.skeleton_deadline})
        write_atomic(out_json, state)

    if input_error:
        state["runtime"]["actual"]["phases"] = budget.phases
        write_atomic(out_json, state)
        return 0

    # ---- 2_inventory：盘点 ----
    with budget.begin("2_inventory"):
        inv, texts = core.build_inventory(input_dir, work_dir)
        state["errors"].extend(inv.get("errors", []))
        state["runtime"]["actual"]["phases"] = budget.phases
        log_change(state, "2_inventory", "%d files inventoried" % len(inv.get("files", [])))
        write_atomic(out_json, state)

    if not inv.get("files"):
        state["status"] = "insufficient_evidence"
        state["uncertainty"] = {"reason": "no material files found under input directory"}
        state["runtime"]["actual"]["phases"] = budget.phases
        log_change(state, "2_inventory", "no material files found")
        write_atomic(out_json, state)
        return 0

    # ---- 3_analyze：元数据/分类/去重/版本/命名（含 decisions 合并）----
    with budget.begin("3_analyze"):
        dims = tuple(d for d in args.hierarchy.split(",")
                     if d.strip() in SUPPORTED_DIMS) or ("document_type", "topic")
        decisions, dec_deg = core.load_decisions(args.decisions)
        if dec_deg:
            state["degradations"].append(dec_deg)
        catalog, hierarchy = core.build_catalog(inv["files"], texts, dims)
        pending = core.merge_decisions(catalog, decisions)
        for r in catalog:
            r["category_path"] = core.category_path(r, dims)
            r["suggested_name"] = core.suggest_name(r, os.path.basename(r["source"]))
            r["version_role"] = core.version_role(os.path.basename(r["source"]),
                                                  r.get("text_preview") or "",
                                                  r.get("metadata") or {})
        core.assign_versions(catalog)
        hierarchy = core.build_tree(catalog)
        versions = core.build_versions(catalog)

        # 论文类文件缺元数据 → pending_human（诚实标注，不臆造）
        for r in catalog:
            if paper_like(r.get("file_type")):
                m = r.get("metadata") or {}
                for f in ("title", "authors", "year"):
                    if not m.get(f):
                        pending.append({"source": r["source"], "field": f,
                                        "reason": "not extracted from text/BibTeX; "
                                                  "provide via decisions.json"})

        library = {"catalog": catalog, "hierarchy": hierarchy,
                   "metadata": {r["source"]: r.get("metadata") for r in catalog},
                   "versions": versions}
        if "library" in state:
            state["library"] = library
        if "evidence" in state:
            state["evidence"] = build_evidence(catalog)
        state["uncertainty"] = build_uncertainty(catalog)
        state["pending_human"] = pending
        core.write_json(os.path.join(work_dir, "organization.json"),
                        {"hierarchy": list(dims), "versions": versions,
                         "catalog": catalog})
        state["runtime"]["actual"]["phases"] = budget.phases
        log_change(state, "3_analyze", "classification/dedup/version/naming resolved")

    # ---- 4_plan / 5_apply：操作计划（干跑）或实际复制 ----
    rollback = []
    conflicts = []
    with budget.begin("4_apply"):
        if args.apply:
            operations, rollback, conflicts = core.apply_operations(catalog, output_dir, work_dir)
            state["degradations"].append(
                "apply copied canonical files (non-destructive): /input untouched; "
                "rollback.json lists every created target")
        else:
            operations, _ = core.plan_operations(catalog, output_dir)
        if "operations" in state:
            state["operations"] = operations
        state["runtime"]["actual"]["phases"] = budget.phases
        log_change(state, "4_apply", "operation plan" + (" applied" if args.apply else " dry-run"))
        write_atomic(out_json, state)

    # ---- 5_finalize：失败汇总 + 定稿 ----
    with budget.begin("5_finalize"):
        failures = core.compute_failures(catalog, len(rollback) if args.apply else 0)
        failures["conflicts"] = conflicts
        if "failures" in state:
            state["failures"] = failures
        state["sources"] = build_sources(catalog)
        state["status"] = decide_status(state, len(inv.get("files", [])), input_error)
        state["runtime"] = {
            "budget": budget.snapshot(),
            "actual": {"skeleton_written_at": state["runtime"].get("actual", {}).get(
                           "skeleton_written_at"),
                       "elapsed_seconds": round(time.time() - t_start, 2),
                       "phases": budget.phases},
        }
        log_change(state, "5_finalize", "final status=%s" % state["status"])
        if state["status"] not in STATUS_VALUES:
            state["status"] = "partial"
        write_atomic(out_json, state)
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研资料整理编排：先落 partial 骨架，再按阶段预算增量整理。")
    p.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    p.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    p.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    p.add_argument("--decisions", default="/app/work/decisions.json",
                   help="Agent 语义决策 JSON（可选）")
    p.add_argument("--hierarchy", default="document_type,topic",
                   help="目录层级维度，逗号分隔（document_type,topic,year,ext,source）")
    p.add_argument("--apply", action="store_true", help="实际复制到 library/；默认干跑")
    p.add_argument("--budget", type=int, default=900, help="总墙钟预算秒（默认 900）")
    p.add_argument("--skeleton-deadline", type=int, default=180,
                   help="骨架落盘硬截止秒（默认 180）")
    p.add_argument("--version", action="store_true", help="打印版本")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    try:
        return run(args)
    except Exception as e:  # 任何异常都不留空产物
        output_dir = getattr(args, "output", "/app/output")
        os.makedirs(output_dir, exist_ok=True)
        existing = core.read_json(os.path.join(output_dir, "result.json"))
        if not isinstance(existing, dict):
            write_atomic(os.path.join(output_dir, "result.json"), {
                "case_id": "unknown", "status": "input_error", "sources": [],
                "uncertainty": {}, "change_log": [],
                "errors": ["%s: %s" % (type(e).__name__, e)],
                "degradations": [], "pending_human": [], "runtime": {}})
        return 0


if __name__ == "__main__":
    sys.exit(main())
