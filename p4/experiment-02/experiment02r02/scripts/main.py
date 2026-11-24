#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 版本化实验方案可行性审查 Skill 入口。

三个阶段可分别调用（保证"前 3 分钟先落盘"可真正执行）：
skeleton(输入发现+契约回填, 写 partial result.json) / analyze(七类审查+台账+排序) /
finalize(自检+报告+CSV+状态收口)。仅标准库，Python>=3.8，退出码恒为 0。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import review_core as core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_BYTES = 2 * 1024 * 1024
TEXTUAL_EXT = (".json", ".jsonl", ".md", ".txt", ".csv", ".yaml", ".yml", ".tsv", ".log")

BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64

DIM_LABELS = {
    "assumptions_variables": "假设与变量",
    "controls": "对照组",
    "equipment_materials": "设备与材料",
    "time_cost": "时间与成本",
    "parameter_ranges": "参数范围",
    "risks_exceptions": "风险与异常",
    "ethics_safety": "伦理与安全",
}


def _stdout_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


# ---
# IO
# ---

def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """读取文本；BOM/坏编码/超限/缺失均降级，不抛异常。"""
    if not os.path.exists(path):
        return None, [f"missing:{path}"]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"unreadable:{path}:{exc}"]
    if size > max_bytes:
        return None, [f"oversize:{path}:{size}>{max_bytes}"]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [f"unreadable:{path}:{exc}"]
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), [f"fallback_decode:{path}"]


def load_json(path):
    text, warns = read_text(path)
    if text is None:
        return None, None, warns + [f"json_unavailable:{path}"]
    try:
        return json.loads(text), text, warns
    except json.JSONDecodeError as exc:
        return None, text, warns + [f"bad_json:{path}:{exc.msg}@line{exc.lineno}"]


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ---
# 输入发现
# ---

def discover(input_dir):
    files, notes = [], []
    if not os.path.isdir(input_dir):
        notes.append(f"input_dir_missing:{input_dir}")
        return files, notes
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            if os.path.islink(path):
                notes.append(f"skipped_symlink:{path}")
                continue
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                notes.append(f"unreadable:{path}:{exc}")
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            item = {"relpath": rel, "abspath": path, "size": size, "ext": ext,
                    "status": "ok", "readable_text": ext in TEXTUAL_EXT}
            if ext in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"):
                item["status"] = "needs_agent_extraction"
                item["note"] = "二进制材料：须由 Agent 调用平台 PDF/OCR 工具取文本后引用，脚本不臆测其内容"
            if size > MAX_TEXT_BYTES and ext in TEXTUAL_EXT:
                item["status"] = "oversize"
                notes.append(f"oversize:{rel}:{size}")
            files.append(item)
    return files, notes


def find_case(files):
    for f in files:
        if f["relpath"].rsplit("/", 1)[-1].lower() in ("case.json", "case.jsonl") and f["status"] == "ok":
            return f
    return None


# ---
# 契约回填
# ---

def contract_source(case):
    if not isinstance(case, dict):
        return None
    for key in ("output_contract", "outputContract", "contract", "output_schema"):
        if isinstance(case.get(key), (dict, list)):
            return case[key]
    return None


def default_for(spec):
    if isinstance(spec, dict):
        t = str(spec.get("type") or spec.get("kind") or "").lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        if t in ("boolean", "bool"):
            return None
        if t in ("number", "integer", "float"):
            return None
        return None
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s in ("array", "list"):
            return []
        if s in ("object", "dict", "map"):
            return {}
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        return {}
    return None


def contract_fields(case):
    """返回 OrderedDict{字段名: 默认值}，保持契约原顺序。"""
    src = contract_source(case)
    fields = []
    if isinstance(src, dict):
        inner = src.get("fields") or src.get("properties")
        if isinstance(inner, (dict, list)):
            src = inner
    if isinstance(src, dict):
        for k, spec in src.items():
            if isinstance(spec, dict) and "default" in spec:
                fields.append((k, spec["default"]))
            else:
                fields.append((k, default_for(spec)))
    elif isinstance(src, list):
        for item in src:
            if isinstance(item, str):
                fields.append((item, None))
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    fields.append((str(name), item.get("default", default_for(item))))
    return fields


# ---
# 结果骨架
# ---

def base_result(case, case_id, files):
    res = {
        "schema_name": core.SCHEMA_NAME,
        "schema_version": core.SCHEMA_VERSION,
        "case_id": case_id,
        "challenge_id": None,
        "task_title": None,
        "status": "partial",
        "review_mode": None,
        "contract_fields": {},
        "executive_summary": {"verdict": "未完成", "recommended_plan": None,
                              "blocking_count": None, "top_conditions": [], "confidence": "low"},
        "research_question": {"question": None, "notes": []},
        "plan_profiles": [],
        "review": {d: [] for d in core.DIMENSIONS},
        "plan_comparison": {"weights": {}, "scores": [], "ranking": [], "recommended": None,
                            "sensitivity": [], "sensitivity_flips": "not_evaluated", "method": None},
        "arithmetic_ledger": [],
        "evidence_index": [],
        "confirmation": {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"budget_seconds": BUDGET_SECONDS, "budget_requests": BUDGET_REQUESTS,
                    "stage": "skeleton", "wall_seconds": None, "requests": None,
                    "note": "运行时长与请求次数由平台记录，本文件不臆测"},
        "input_inventory": [],
        "artifacts": [],
        "change_log": [],
    }
    for k, v in contract_fields(case):
        res["contract_fields"][k] = {"status": "default_null", "type": type(v).__name__}
        if k not in res:
            res[k] = v
    res["task_title"] = (case.get("title") or case.get("name")) if isinstance(case, dict) else None
    res["challenge_id"] = (case.get("challenge_id") or case.get("challengeId")) if isinstance(case, dict) else None
    return res


def skeleton(input_dir, output_dir, work_dir):
    files, notes = discover(input_dir)
    case_file = find_case(files)
    case, _, warn = (None, None, [])
    if case_file:
        case, _, warn = load_json(case_file["abspath"])
    case_id = None
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "caseId"):
            if case.get(k) not in (None, ""):
                case_id = str(case[k])
                break
    if not case_id:
        case_id = "derived:" + (os.path.splitext(case_file["relpath"].rsplit("/", 1)[-1])[0] if case_file else "unknown")

    res = base_result(case, case_id, files)
    res["input_inventory"] = [
        {"relpath": f["relpath"], "bytes": f["size"], "status": f["status"]}
        for f in files
    ]
    res["errors"].extend(warn)
    res["errors"].extend(n for n in notes if n.startswith(("bad_json", "oversize", "unreadable")))
    if not case_file:
        res["status"] = "insufficient_evidence"
        res["limitations"].append("未发现 case.json：无法获得方案、合同与参数域，仅保留输入清单")
        res["pending_human"].append({"item": "case.json", "who": "题目组织方/提交方",
                                     "what": "确认输入文件路径与命名", "order": 1})
    elif case is None:
        res["status"] = "input_error"
        res["limitations"].append("case.json 无法解析为 JSON：结果保留契约字段与输入清单，不推断方案内容")
    res["change_log"].append({
        "stage": "skeleton", "action": "写入合法 partial 骨架",
        "reason": "执行预算硬规则：前 3 分钟内必须落盘一版结果，之后只做增量改进",
    })
    write_json(os.path.join(output_dir, "result.json"), res)
    write_json(os.path.join(work_dir, "input_inventory.json"),
               {"input_dir": input_dir, "files": files, "notes": notes})
    return res


# ---
# 审查主装配
# ---

def merge_notes(result, notes, docs):
    """把 Agent 补充的判断合并进结果；证据不通过校验的条目降级为 unconfirmed。"""
    errs = []
    if not isinstance(notes, dict):
        return [{"kind": "notes_not_object"}]
    added = 0
    for dim, items in (notes.get("review") or {}).items():
        if dim not in core.DIMENSIONS or not isinstance(items, list):
            errs.append({"kind": "notes_bad_dimension", "dimension": dim})
            continue
        for it in items:
            if not isinstance(it, dict) or not it.get("check"):
                errs.append({"kind": "notes_bad_item", "dimension": dim})
                continue
            it.setdefault("id", f"N-{dim}-{added+1}")
            it.setdefault("dimension", dim)
            it.setdefault("confidence", "medium")
            it.setdefault("needs_expert", False)
            it.setdefault("conditions", [])
            it.setdefault("notes", [])
            ev = []
            for e in it.get("evidence") or []:
                loc = e.get("locator") or ""
                fname = loc.split("#")[0]
                quote = e.get("quote")
                doc = docs.get(fname)
                ok = core.quote_verified(doc, quote) if doc else False
                ev.append({"locator": loc, "quote": quote, "quote_verified": ok,
                           "kind": e.get("kind", "agent")})
                if not ok:
                    it["verdict"] = "unconfirmed"
                    it["notes"].append({"kind": "quote_unverified", "locator": loc})
            it["evidence"] = ev
            if it.get("verdict") not in core.VERDICTS:
                it["verdict"] = "unconfirmed"
            bucket = result["review"][dim]
            for i, old in enumerate(bucket):
                if old.get("id") == it["id"]:
                    bucket[i] = it
                    break
            else:
                bucket.append(it)
            added += 1
    for key in ("pending_human", "uncertainty", "sources", "limitations"):
        val = notes.get(key)
        if isinstance(val, list):
            result[key].extend(val)
    result["change_log"].append({"stage": "merge_notes", "action": f"合并 Agent 补充条目 {added} 条",
                                 "reason": "可选：把内容判断写回结构化结果，证据仍按引文校验"})
    return errs


def build_confirmation(result):
    conf = {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []}
    for dim, items in result["review"].items():
        for it in items:
            if it.get("verdict") in ("blocked", "conflict") and dim in ("equipment_materials", "parameter_ranges"):
                conf["equipment_availability"].append({
                    "item": it.get("check"), "ref": it.get("id"),
                    "conditions": it.get("conditions", []), "owner": "设备/平台管理方"})
            if dim == "ethics_safety":
                conf["ethics"].append({"item": it.get("check"), "ref": it.get("id"),
                                       "conditions": it.get("conditions", []), "owner": "伦理/安全委员会"})
            if it.get("needs_expert"):
                conf["expert_confirmation"].append({"item": it.get("check"), "ref": it.get("id"),
                                                    "what": "、".join(it.get("conditions", [])) or "确认该判断",
                                                    "owner": "领域专家"})
    return conf


def summarise(result):
    items = [it for lst in result["review"].values() for it in lst]
    blocks = [it for it in items if it.get("verdict") in ("blocked", "conflict")]
    missing = [it for it in items if it.get("verdict") in ("missing", "unconfirmed")]
    top = []
    for it in blocks:
        for c in it.get("conditions", [])[:1]:
            if c not in top:
                top.append(c)
    if result["status"] == "insufficient_evidence":
        verdict = "材料不足，无法给出可开工判断"
    elif blocks:
        verdict = "不可直接开工：存在阻断项，需先落实条件"
    elif missing:
        verdict = "有条件可行：无硬阻断，但仍有未确认项需补材料"
    else:
        verdict = "七类约束均未发现阻断项（仍受引用材料范围限制）"
    return {
        "verdict": verdict,
        "recommended_plan": result["plan_comparison"].get("recommended"),
        "blocking_count": len(blocks),
        "unconfirmed_count": len(missing),
        "top_conditions": top[:8],
        "confidence": "medium" if result["status"] == "ok" else "low",
    }


def build_uncertainty(result):
    """把可计算的缺口整理成 uncertainty，避免"看起来都查过了"。"""
    out = []
    for e in result["arithmetic_ledger"]:
        if e.get("status") != "computed":
            out.append({"item": e.get("title"), "reason": "台账项不可计算",
                        "impact": "成本/时长/样本量结论只能标为未确认",
                        "resolve": "补齐该台账项缺失的输入数值后重跑 analyze"})
    for it in (x for lst in result["review"].values() for x in lst):
        if it.get("verdict") == "unconfirmed" and it.get("notes"):
            pass
    quoted = result.get("counts", {}).get("evidence_quoted", 0)
    total = result.get("counts", {}).get("evidence_total", 0)
    if total and quoted < total:
        out.append({"item": "证据引文校验", "reason": f"{total - quoted}/{total} 条引文未能在原文中逐字命中",
                    "impact": "这些条目只能作为线索，不能作为复核依据",
                    "resolve": "按 locator 回到原文核对后重写 quote"})
    return out


def build_pending(result):
    pend = []
    for it in (x for lst in result["review"].values() for x in lst):
        if it.get("verdict") in ("missing", "unconfirmed", "blocked", "conflict"):
            pend.append({"item": it.get("check"), "ref": it.get("id"),
                         "who": "方案撰写人" if it.get("dimension") in ("assumptions_variables", "controls", "risks_exceptions") else "课题组/平台",
                         "what": "；".join(it.get("conditions", [])) or "补充材料",
                         "order": 3 if it.get("verdict") == "blocked" else 5})
    pend.sort(key=lambda x: x["order"])
    return pend[:40]


def analyze_stage(input_dir, output_dir, work_dir, notes_path=None):
    files, notes = discover(input_dir)
    case_file = find_case(files)
    res = skeleton(input_dir, output_dir, work_dir) if not os.path.exists(os.path.join(output_dir, "result.json")) else None
    case, case_text, warn = (None, None, [])
    if case_file:
        case, case_text, warn = load_json(case_file["abspath"])

    if res is None:
        prev, _, _ = load_json(os.path.join(output_dir, "result.json"))
        res = prev if isinstance(prev, dict) else base_result(None, "unknown", files)
    kept = [e for e in res.get("errors", []) if not str(e).startswith("bad_json")]
    seen_err = set()
    errs = []
    for e in kept + warn:
        key = json.dumps(e, ensure_ascii=False, sort_keys=True)
        if key not in seen_err:
            seen_err.add(key)
            errs.append(e)
    res["errors"] = errs
    docs = {}
    if case_text is not None:
        docs["case.json"] = core.Doc("case.json", case_text)
    md_path = os.path.join(input_dir, "task.md")
    md_text, _ = read_text(md_path)
    if md_text:
        docs["task.md"] = core.Doc("task.md", md_text)
    res["input_inventory"] = [{"relpath": f["relpath"], "bytes": f["size"], "status": f["status"]}
                              for f in files]

    if not isinstance(case, dict):
        res["status"] = "input_error" if case_file else "insufficient_evidence"
        res["limitations"].append("case.json 缺失或不可解析：不推断方案内容")
        res["executive_summary"] = summarise(res)
        res["runtime"]["stage"] = "analyze"
        write_json(os.path.join(output_dir, "result.json"), res)
        return res

    try:
        analysis = core.analyze(case, docs.get("case.json"))
    except Exception as exc:  # noqa: BLE001
        res["errors"].append({"kind": "analyze_exception", "error": repr(exc),
                              "trace": traceback.format_exc().splitlines()[-6:]})
        res["status"] = "partial"
        res["limitations"].append("确定性分析中断：保留骨架与已有证据，缺失部分见 errors")
        write_json(os.path.join(output_dir, "result.json"), res)
        return res

    res.update({
        "review_mode": analysis["review_mode"],
        "plan_profiles": analysis["plan_profiles"],
        "review": analysis["review"],
        "arithmetic_ledger": analysis["arithmetic_ledger"],
        "plan_comparison": analysis["plan_comparison"],
        "evidence_index": analysis["evidence_index"],
        "counts": analysis["counts"],
    })
    res["research_question"] = {
        "question": (analysis["plan_profiles"][0]["goal"] if analysis["plan_profiles"] else None),
        "notes": ["研究问题取方案声明的目标字段；未声明时置 null，不代写"],
    }

    if notes_path and os.path.exists(notes_path):
        n_data, _, n_warn = load_json(notes_path)
        res["errors"].extend(n_warn)
        res["errors"].extend(merge_notes(res, n_data, docs) if n_data is not None else [])
    else:
        res["limitations"].append("未提供 agent_notes.json：内容型判断（假设合理性、对照充分性）仅做结构性核查")

    res["confirmation"] = build_confirmation(res)
    res["pending_human"] = build_pending(res)
    res["uncertainty"] = build_uncertainty(res)

    # 状态判定
    dims_ok = all(len(res["review"].get(d) or []) > 0 for d in core.DIMENSIONS)
    c = res.get("counts", {})
    if not res["plan_profiles"]:
        res["status"] = "insufficient_evidence"
        res["limitations"].append("未识别到候选方案（plans/versions/variants 均为空）")
    elif dims_ok and c.get("computed_ledger", 0) >= 3 and c.get("evidence_quoted", 0) >= 5:
        res["status"] = "ok"
    else:
        res["status"] = "partial"
        if not dims_ok:
            res["limitations"].append("部分审查维度无条目：输入缺少对应字段，未以假设填充")
        if c.get("computed_ledger", 0) < 3:
            res["limitations"].append("可计算的算术台账少于 3 项：成本/时长/样本量结论不完整")

    res["executive_summary"] = summarise(res)
    res["runtime"]["stage"] = "analyze"
    res["change_log"].append({"stage": "analyze", "action": "完成七类审查、算术台账与版本排序",
                              "reason": f"plan_count={c.get('plans')} ledger={c.get('ledger_entries')} "
                                        f"computed={c.get('computed_ledger')}"})
    write_json(os.path.join(output_dir, "result.json"), res)
    return res


# ---
# 产物
# ---

def write_csv(path, rows, header):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def finalize_stage(input_dir, output_dir, work_dir, notes_path=None):
    res = analyze_stage(input_dir, output_dir, work_dir, notes_path)

    report = core.render_report(res)
    report += "\n" + core.render_ledger_md(res)
    report += "\n## 版本排序\n\n"
    pc = res.get("plan_comparison", {})
    for s in pc.get("scores", []):
        report += (f'- **{s["plan_id"]}** 加权总分 {s["weighted_total"]}；'
                   f'子项 {s["subscores"]}；硬阻断 {s["blocks"]}\n')
    report += f'\n- 推荐：`{pc.get("recommended")}`；权重敏感性：`{pc.get("sensitivity_flips")}`\n'
    report += "\n## 待人工确认\n\n"
    for p in res.get("pending_human", [])[:20]:
        report += f'- [{p.get("ref")}] {p.get("item")} → {p.get("who")}：{p.get("what")}\n'
    report += "\n## 不确定性\n\n"
    for u in res.get("uncertainty", []):
        report += f'- {u.get("item")}：{u.get("reason")}；影响：{u.get("impact")}\n'
    report += "\n> 本报告与 result.json 同源生成，可直接编辑；引用定位格式 `文件#JSON路径`。\n"
    write_text(os.path.join(output_dir, "review_report.md"), report)

    write_csv(os.path.join(output_dir, "arithmetic_ledger.csv"),
              [[e.get("id"), e.get("kind"), e.get("title"), e.get("expr"), e.get("result"),
                e.get("unit"), e.get("status"), e.get("recompute_ok")] for e in res["arithmetic_ledger"]],
              ["id", "kind", "title", "expr", "result", "unit", "status", "recompute_ok"])
    write_csv(os.path.join(output_dir, "plan_score_matrix.csv"),
              [[s["plan_id"]] + [s["subscores"].get(k) for k in core.SCORE_WEIGHTS] + [s["weighted_total"], s["blocks"]]
               for s in pc.get("scores", [])],
              ["plan_id"] + list(core.SCORE_WEIGHTS) + ["weighted_total", "hard_blocks"])
    res["artifacts"] = [
        {"path": os.path.join(output_dir, "review_report.md"), "kind": "markdown", "editable": True},
        {"path": os.path.join(output_dir, "arithmetic_ledger.csv"), "kind": "csv", "editable": True},
        {"path": os.path.join(output_dir, "plan_score_matrix.csv"), "kind": "csv", "editable": True},
    ]

    checks = verify(res)
    res["selfcheck"] = {"pass": all(c["pass"] for c in checks if c.get("hard")),
                        "checks": checks}
    if not res["selfcheck"]["pass"]:
        res["status"] = "partial" if res["status"] == "ok" else res["status"]
    res["runtime"]["stage"] = "finalize"
    res["change_log"].append({"stage": "finalize", "action": "运行不变量自检并落盘可编辑产物",
                              "reason": f"hard_checks_pass={res['selfcheck']['pass']}"})
    write_json(os.path.join(output_dir, "result.json"), res)
    return res


# ---
# 自检
# ---

def verify(res):
    checks = []

    def add(name, ok, detail="", hard=True):
        checks.append({"name": name, "pass": bool(ok), "detail": detail, "hard": hard})

    add("case_id_present", bool(res.get("case_id")), str(res.get("case_id")))
    add("status_enum", res.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"),
        str(res.get("status")))
    contract = res.get("contract_fields") or {}
    missing = [k for k in contract if k not in res]
    add("contract_fields_echoed", not missing, f"missing={missing}")
    add("all_dimensions_present", all(d in (res.get("review") or {}) for d in core.DIMENSIONS),
        ",".join(core.DIMENSIONS))
    # 每条 ok/conditional/blocked/conflict 必须有证据
    bad = []
    for dim, items in (res.get("review") or {}).items():
        for it in items:
            v = it.get("verdict")
            if v in ("ok", "conditional", "blocked", "conflict") and not it.get("evidence"):
                bad.append(it.get("id"))
            if v == "conditional" and not it.get("conditions"):
                bad.append(it.get("id") + ":no-conditions")
            if v == "blocked" and not it.get("conditions"):
                bad.append(it.get("id") + ":no-conditions")
    add("verdicts_have_evidence_and_conditions", not bad, ",".join(bad[:10]))
    # 台账复算
    bad_l = [e.get("id") for e in res.get("arithmetic_ledger", [])
             if e.get("status") == "computed" and not e.get("recompute_ok")]
    add("ledger_recomputes", not bad_l, ",".join(bad_l[:10]))
    # 引文逐字校验
    bad_q = [e.get("locator") for e in res.get("evidence_index", []) if e.get("quote") and not e.get("quote_verified")]
    add("quotes_verbatim", not bad_q, ",".join(str(x) for x in bad_q[:5]), hard=False)
    # 排序确定性（同分不同序时按 plan_id 兜底）
    ranked = res.get("plan_comparison", {}).get("ranking") or []
    add("ranking_covers_plans", len(ranked) == len(res.get("plan_profiles") or []),
        f"{len(ranked)}/{len(res.get('plan_profiles') or [])}")
    # 未把"没写"当"满足"
    missing_dims = [d for d in core.DIMENSIONS if not (res.get("review") or {}).get(d)]
    add("no_empty_dimension", not missing_dims, ",".join(missing_dims), hard=False)
    add("uncertainty_or_reason", bool(res.get("uncertainty")) or res.get("status") == "ok",
        f"n={len(res.get('uncertainty') or [])}", hard=False)
    add("has_artifacts", bool(res.get("artifacts")), f"n={len(res.get('artifacts') or [])}", hard=False)
    add("change_log_present", bool(res.get("change_log")), f"n={len(res.get('change_log') or [])}")
    return checks


# ---
# CLI
# ---

def main(argv=None):
    _stdout_utf8()
    ap = argparse.ArgumentParser(description="版本化实验方案可行性审查 Skill")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--stage", default="all",
                    choices=("skeleton", "analyze", "finalize", "verify", "all"))
    ap.add_argument("--notes", default=None, help="可选：Agent 补充判断的 JSON")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    notes = args.notes or os.path.join(args.work, "agent_notes.json")

    try:
        if args.stage == "skeleton":
            res = skeleton(args.input, args.output, args.work)
        elif args.stage == "analyze":
            res = analyze_stage(args.input, args.output, args.work, notes)
        elif args.stage == "finalize":
            res = finalize_stage(args.input, args.output, args.work, notes)
        elif args.stage == "verify":
            data, _, _ = load_json(os.path.join(args.output, "result.json"))
            if not isinstance(data, dict):
                print("FAIL: result.json 缺失或不可解析")
                return 0
            checks = verify(data)
            for c in checks:
                print("%s %-40s %s" % ("PASS" if c["pass"] else "FAIL", c["name"], c["detail"]))
            return 0
        else:
            skeleton(args.input, args.output, args.work)
            res = finalize_stage(args.input, args.output, args.work, notes)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("degraded: %r\n" % (exc,))
        try:
            fallback = base_result(None, "unknown", [])
            fallback["status"] = "partial"
            fallback["errors"].append({"kind": "fatal", "error": repr(exc)})
            fallback["limitations"].append("发生未捕获异常：已写出最小合法产物，细节见 errors")
            write_json(os.path.join(args.output, "result.json"), fallback)
        except Exception:  # noqa: BLE001
            pass
        return 0

    if not args.quiet:
        print("stage=%s status=%s plans=%s items=%s ledger=%s" % (
            res.get("runtime", {}).get("stage"), res.get("status"),
            (res.get("counts") or {}).get("plans"), (res.get("counts") or {}).get("review_items"),
            (res.get("counts") or {}).get("ledger_entries")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
