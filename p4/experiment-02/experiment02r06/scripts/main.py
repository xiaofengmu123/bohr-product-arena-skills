#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 实验方案可行性审查的编排入口。

用法
----
  python3 scripts/main.py --stage skeleton --input /input --output /app/output --work /app/work
  python3 scripts/main.py --stage full     --input /input --output /app/output --work /app/work \
        [--adjudication /app/work/adjudication.json]

阶段
----
skeleton : 只读案例主文件，立刻写出结构合法、状态诚实的 partial result.json 与
           input_inventory.json（对应 900 秒预算中的前 3 分钟硬规则）。
full     : 构建 plan_model → 运行确定性校验 → 合并 Agent 复核 → 生成
           result.json / plan_model.json / calc_trace.json / review_report.md。

约束：仅标准库；任何写盘失败都要降级为最小合法产物并以退出码 0 结束。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

BASE_KEYS = ("case_id", "status", "experiment_plan", "review", "sources",
             "uncertainty", "pending_human", "errors", "degradations",
             "change_log", "runtime")


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def write_text(path, text):
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        data = text.encode("utf-8")
        return {"path": path, "bytes": len(data), "sha256_12": C.sha12(data)}
    except OSError as exc:
        return {"path": path, "error": str(exc)}


def write_json(path, obj):
    return write_text(path, C.dumps(obj) + "\n")


def read_case_meta(input_dir):
    """骨架阶段的最小读取：只需要 case_id 与 output_contract。"""
    case_id, contract, warns = "unknown", None, []
    if not os.path.isdir(input_dir):
        return case_id, contract, ["input_dir_missing: %s" % input_dir]
    files = C.discover_files(input_dir)
    cands = [f for f in files if os.path.basename(f[0]).lower() in ("case.json", "case.jsonl")]
    cands.sort(key=lambda f: (f[0].count("/"), f[0]))
    if not cands:
        warns.append("missing_case")
        return case_id, contract, warns
    data, load_warns = C.load_json_file(cands[0][1])
    warns.extend(load_warns)
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "armiId"):
            val = data.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                case_id = str(val).strip()
                break
        if case_id == "unknown":
            _, nested = C.get_val(data, ("case", "case_info", "meta"))
            if isinstance(nested, dict):
                for key in ("case_id", "caseId", "id"):
                    if isinstance(nested.get(key), (str, int)):
                        case_id = str(nested[key]).strip()
                        break
        for key in ("output_contract", "outputContract", "result_contract", "输出契约"):
            if key in data:
                contract = data[key]
                break
        if contract is None:
            _, contract = C.get_val(data, ("output_contract", "result_contract", "输出契约"))
    return case_id, contract, warns


def skeleton_result(case_id, contract):
    result = {
        "case_id": case_id or "unknown",
        "challenge_id": None,
        "skill": "experiment-feasibility-review",
        "status": "partial",
        "overall_conclusion": {
            "verdict": None,
            "summary": "骨架已落盘：等待 plan_model 构建与七类约束校验",
            "conditions": [], "blocks": [], "notes": []},
        "experiment_plan": {
            "research_question": None, "assumptions": [], "variables": [],
            "controls": [], "steps": [], "parameters": [], "resources": [],
            "interpretation_criteria": []},
        "review": {dim: [] for dim in C.DIMENSIONS},
        "plan_comparison": {"ranking": [], "diffs": [], "recommended": None},
        "evidence_and_risks": {"evidence": [], "risks": [], "exception_handling": []},
        "confirmation": {"safety": [], "ethics": [],
                         "equipment_availability": [], "expert_confirmation": []},
        "sources": [], "uncertainty": [], "pending_human": [],
        "errors": [], "degradations": [],
        "change_log": [{"step": "skeleton",
                        "action": "写入初始 partial 骨架",
                        "reason": "900 秒预算的前 3 分钟必须先落盘，之后只做增量改进"}],
        "runtime": {"budget_seconds": 900, "budget_requests": 64,
                    "stages": ["skeleton"], "deterministic": True},
        "artifacts": [],
        "output_contract": contract,
    }
    if contract is not None:
        for key, _spec in C.contract_entries(contract):
            top = key.split(".")[0]
            if top not in result:
                result[top] = None
    return result


def inventory_of(input_dir):
    items = []
    for rel, path, size, ext in C.discover_files(input_dir):
        entry = {"path": rel, "bytes": size, "ext": ext,
                 "readable": os.access(path, os.R_OK)}
        if ext in C.TEXT_EXTS and size <= C.MAX_BYTES:
            text, warns = C.read_text(path)
            if text is not None:
                entry["sha256_12"] = C.sha12(text.encode("utf-8", "replace"))
                entry["preview"] = C.snip(text, 120)
            entry["warnings"] = warns
        items.append(entry)
    return {"input_dir": input_dir, "files": items}


def load_adjudication(path, warnings):
    if not path:
        return None
    data, warns = C.load_json_file(path)
    warnings.extend(warns)
    if not isinstance(data, dict):
        warnings.append("adjudication_not_object: %s" % path)
        return None
    return data


def apply_adjudication(engine, adj, warnings):
    if not adj:
        return
    by_id = {f["id"]: f for f in engine.findings}
    for idx, ov in enumerate(adj.get("overrides") or []):
        if not isinstance(ov, dict):
            continue
        fid = ov.get("finding_id") or ov.get("id")
        target = by_id.get(fid)
        if target is None:
            warnings.append("override_unknown_id: %s" % fid)
            continue
        verdict = ov.get("verdict")
        if verdict in C.VERDICTS:
            target["verdict"] = verdict
        for field in ("conditions", "notes", "claim", "confidence", "needs_expert"):
            if field in ov:
                target[field] = ov[field]
        target["notes"] = list(target.get("notes") or []) + ["Agent 复核覆盖"]
    seq = 0
    for idx, add in enumerate(adj.get("additions") or []):
        if not isinstance(add, dict):
            continue
        dim = add.get("dimension")
        if dim not in C.DIMENSIONS:
            warnings.append("addition_bad_dimension: %s" % dim)
            continue
        verdict = add.get("verdict") if add.get("verdict") in C.VERDICTS else "unconfirmed"
        evidence = add.get("evidence") or []
        notes = list(add.get("notes") or [])
        if verdict in ("ok", "conditional", "blocked", "conflict") and not evidence:
            verdict = "unconfirmed"
            notes.append("缺少 locator+quote，已降级为 unconfirmed")
        seq += 1
        engine.findings.append({
            "id": add.get("id") or "AGT-%02d" % seq,
            "dimension": dim,
            "plan_id": add.get("plan_id") or "reviewer",
            "subject": add.get("subject") or "Agent 补充",
            "claim": add.get("claim") or "",
            "verdict": verdict,
            "resource_state": add.get("resource_state"),
            "conditions": add.get("conditions") or [],
            "evidence": evidence,
            "calc_refs": add.get("calc_refs") or [],
            "confidence": add.get("confidence") or "medium",
            "needs_expert": bool(add.get("needs_expert")),
            "notes": notes + ["由 Agent 复核追加"]})


def report_markdown(model, engine, result, comparison):
    lines = []
    title = model.get("title") or "实验方案可行性审查"
    lines.append("# %s" % title)
    lines.append("")
    lines.append("- case_id：`%s`" % result.get("case_id"))
    lines.append("- 状态：`%s`" % result.get("status"))
    lines.append("- 中间表示：`plan_model.json`（schema %s）；算术追踪：`calc_trace.json`"
                 % C.SCHEMA_VERSION)
    lines.append("- 说明：本报告中的数值全部由 `calc_trace.json` 的公式复算得到；"
                 "判断均带 locator，可与 `case.json` 原文对照。")
    lines.append("")
    oc = result.get("overall_conclusion") or {}
    lines.append("## 1. 结论")
    lines.append("")
    lines.append("- 总体判定：**%s**" % (oc.get("verdict") or "未定"))
    lines.append("- 摘要：%s" % (oc.get("summary") or ""))
    if oc.get("recommended_plan"):
        lines.append("- 推荐版本：`%s`（排序口径：可行性惩罚分 → 关键路径 → 成本）"
                     % oc["recommended_plan"])
    counts = oc.get("counts") or {}
    if counts:
        lines.append("")
        lines.append("| 判定 | ok | conditional | unconfirmed | missing | conflict | blocked |")
        lines.append("|---|---|---|---|---|---|---|")
        lines.append("| 条数 | %d | %d | %d | %d | %d | %d |" % tuple(
            counts.get(k, 0) for k in ("ok", "conditional", "unconfirmed",
                                       "missing", "conflict", "blocked")))
    lines.append("")
    lines.append("## 2. 计划模型摘要（中间表示）")
    lines.append("")
    lines.append("| 方案 | 步骤 | 顺序时长(min) | 关键路径(min) | 人工(min) | 已标价成本(CNY) | 样本需求 |")
    lines.append("|---|---|---|---|---|---|---|")
    for plan in model.get("plans") or []:
        m = plan.get("metrics") or {}
        t = m.get("time") or {}
        c = m.get("cost") or {}
        s = m.get("samples") or {}
        lines.append("| `%s` | %d | %s | %s | %s | %s | %s |" % (
            plan["plan_id"], len(plan.get("steps") or []),
            _fmt(t.get("sequential_min")), _fmt(t.get("critical_min")),
            _fmt(t.get("operator_min")), _fmt(c.get("total")),
            _fmt(s.get("required_n"))))
    lines.append("")
    lines.append("## 3. 七类约束逐项审查")
    lines.append("")
    for dim in C.DIMENSIONS:
        items = [f for f in engine.findings if f["dimension"] == dim]
        lines.append("### 3.%d %s（%d 条）" % (C.DIMENSIONS.index(dim) + 1, dim, len(items)))
        lines.append("")
        if not items:
            lines.append("（无记录：该维度未形成判断，需补证据）")
            lines.append("")
            continue
        lines.append("| ID | 方案 | 对象 | 判定 | 结论要点 | 条件/缺口 | 证据 |")
        lines.append("|---|---|---|---|---|---|---|")
        for f in items:
            ev = f.get("evidence") or []
            ev_txt = "; ".join("`%s`：%s" % (e.get("locator"), C.snip(e.get("quote"), 60))
                               for e in ev[:2])
            cond = "；".join(f.get("conditions") or []) or "—"
            lines.append("| %s | %s | %s | **%s** | %s | %s | %s |" % (
                f["id"], f.get("plan_id") or "—", C.snip(f.get("subject"), 30),
                f["verdict"], C.snip(f.get("claim"), 90), C.snip(cond, 70),
                ev_txt or "（缺失类判断：见 scope）"))
        lines.append("")
    lines.append("## 4. 版本比较与差异")
    lines.append("")
    ranking = (comparison or {}).get("ranking") or []
    if ranking:
        lines.append("| 排名 | 方案 | 惩罚分 | 关键路径(min) | 成本(CNY) | 判定 |")
        lines.append("|---|---|---|---|---|---|")
        for item in ranking:
            lines.append("| %s | `%s` | %s | %s | %s | %s |" % (
                item.get("rank"), item.get("plan_id"), item.get("penalty"),
                _fmt(item.get("wall_min")), _fmt(item.get("cost")), item.get("verdict")))
        lines.append("")
    for diff in (comparison or {}).get("diffs") or []:
        lines.append("- `%s` → `%s`：新增步骤 %s；删除步骤 %s；参数变化 %s；"
                     "已解决项 %d；新引入项 %d" % (
                         diff["from"], diff["to"], diff.get("steps_added") or "无",
                         diff.get("steps_removed") or "无",
                         "、".join("%s %s→%s" % (c["name"], c["from"], c["to"])
                                  for c in diff.get("params_changed") or []) or "无",
                         len(diff.get("issues_resolved") or []),
                         len(diff.get("issues_introduced") or [])))
    lines.append("")
    lines.append("## 5. 算术追踪（节选）")
    lines.append("")
    lines.append("| calc id | 公式 | 输入 | 结果 | 方法 |")
    lines.append("|---|---|---|---|---|")
    for calc in engine.calcs[:24]:
        lines.append("| `%s` | %s | %s | %s %s | %s |" % (
            calc["id"], calc["formula"], C.snip(calc["inputs"], 60),
            calc["value"], calc["unit"], calc["method"]))
    lines.append("")
    lines.append("## 6. 缺口、不确定性与待人工确认")
    lines.append("")
    for item in (result.get("uncertainty") or [])[:16]:
        lines.append("- [%s] %s：%s" % (item.get("impact"), item.get("topic"), item.get("detail")))
    for item in (result.get("pending_human") or [])[:10]:
        lines.append("- 待确认（%s）：%s" % (item.get("who"), item.get("why")))
    lines.append("")
    lines.append("## 7. 复跑方式")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 scripts/main.py --stage skeleton --input /input --output /app/output")
    lines.append("python3 scripts/main.py --stage full --input /input --output /app/output "
                 "--work /app/work --adjudication /app/work/adjudication.json")
    lines.append("python3 scripts/selftest.py")
    lines.append("```")
    lines.append("")
    lines.append("相同 input + 相同 adjudication 下，`plan_model.json` 与 `calc_trace.json` "
                 "内容一致；`result.json` 的 `integrity.payload_sha256_12` 可用于比对。")
    return "\n".join(lines) + "\n"


def _fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def run_skeleton(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    case_id, contract, warns = read_case_meta(args.input)
    result = skeleton_result(case_id, contract)
    result["degradations"] = [{"kind": "read", "detail": w} for w in warns]
    written = write_json(os.path.join(args.output, "result.json"), result)
    # 骨架写盘失败时再退到 work 目录，绝不崩溃
    if "error" in written:
        write_json(os.path.join(args.work, "result.json"), result)
    inv = inventory_of(args.input)
    write_json(os.path.join(args.work, "input_inventory.json"), inv)
    write_json(os.path.join(args.output, "input_inventory.json"), inv)
    if not args.quiet:
        print("skeleton: case_id=%s status=partial files=%d" % (
            result["case_id"], len(inv["files"])))
    return 0


def run_full(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    warnings = []
    builder = C.Builder(args.input)
    model = builder.build()
    warnings.extend(i["detail"] for i in model["issues"])

    contract = None
    if isinstance(builder.root, dict):
        for key in ("output_contract", "outputContract", "result_contract", "输出契约"):
            if key in builder.root:
                contract = builder.root[key]
                break
        if contract is None:
            _, contract = C.get_val(builder.root, ("output_contract", "result_contract", "输出契约"))

    engine = C.Engine(model)
    for plan in model["plans"]:
        C.check_plan(engine, model, plan)

    adj = load_adjudication(args.adjudication, warnings)
    apply_adjudication(engine, adj, warnings)

    comparison = C.compare_plans(model, engine) if model["plans"] else {
        "ranking": [], "diffs": [], "recommended": None, "summary": {}}
    result = C.assemble_result(model, engine, comparison, contract,
                               adjudication=adj, work_dir=args.work)
    result["degradations"].extend({"kind": "adjudication", "detail": w} for w in warnings
                                  if w not in {i["detail"] for i in model["issues"]})

    # 状态与契约回填联动：未知契约字段保留 null 并降为 partial
    unfilled = (result.get("contract_fill_report") or {}).get("unfilled") or []
    if unfilled and result["status"] == "ok":
        result["status"] = "partial"
        result["change_log"].append({
            "step": "contract", "action": "契约存在未匹配字段，状态降为 partial",
            "reason": "未匹配字段以 null 保留，避免编造"})

    report = report_markdown(model, engine, result, comparison)

    calc_trace = {"case_id": result["case_id"],
                  "schema_version": C.SCHEMA_VERSION,
                  "calcs": engine.calcs,
                  "count": len(engine.calcs)}
    plan_model_text = C.dumps(model) + "\n"
    calc_text = C.dumps(calc_trace) + "\n"
    report_bytes = len(report.encode("utf-8"))

    result["artifacts"] = [
        {"path": "plan_model.json", "bytes": len(plan_model_text.encode("utf-8")),
         "role": "结构化中间表示（审查的单一事实来源）"},
        {"path": "calc_trace.json", "bytes": len(calc_text.encode("utf-8")),
         "role": "派生数值的公式与输入追踪"},
        {"path": "review_report.md", "bytes": report_bytes,
         "role": "可编辑人工复核报告"},
    ]
    payload = dict(result)
    payload.pop("integrity", None)
    result["integrity"] = {
        "payload_sha256_12": C.sha12(C.dumps(payload)),
        "note": "对除 integrity 外的 result 内容取 SHA-256 前 12 位；相同输入应可复现",
    }

    written = []
    written.append(write_json(os.path.join(args.output, "plan_model.json"), model))
    written.append(write_text(os.path.join(args.output, "calc_trace.json"), calc_text))
    written.append(write_text(os.path.join(args.output, "review_report.md"), report))
    written.append(write_json(os.path.join(args.output, "result.json"), result))
    if any("error" in w for w in written):
        # 主输出目录不可写时退到 work，仍保留合法 JSON
        for item in written:
            if "error" in item:
                name = os.path.basename(item["path"])
                write_json(os.path.join(args.work, name + ".fallback"),
                           {"case_id": result.get("case_id"),
                            "status": result.get("status"),
                            "error": item["error"],
                            "files": [os.path.basename(w["path"]) for w in written]})
    inventory = inventory_of(args.input)
    write_json(os.path.join(args.work, "input_inventory.json"), inventory)

    if not args.quiet:
        counts = result["overall_conclusion"].get("counts") or {}
        print("full: case_id=%s status=%s plans=%d findings=%d calcs=%d verdict=%s" % (
            result["case_id"], result["status"], len(model["plans"]),
            len(engine.findings), len(engine.calcs),
            result["overall_conclusion"].get("verdict")))
        print("review: " + ", ".join("%s=%d" % (k, v) for k, v in counts.items()))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="实验方案可行性审查 Skill")
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", DEFAULT_INPUT))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", DEFAULT_WORK))
    parser.add_argument("--stage", choices=("skeleton", "full"), default="full")
    parser.add_argument("--adjudication", default=None,
                        help="Agent 复核 patch（overrides/additions）JSON 路径")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.stage == "skeleton":
            return run_skeleton(args)
        return run_full(args)
    except Exception as exc:  # 任何未预期异常都不留空产物
        fallback = {
            "case_id": "unknown", "status": "input_error",
            "errors": [{"kind": "unexpected", "detail": "%s: %s" % (type(exc).__name__, exc)}],
            "change_log": [{"step": "fallback", "action": "异常降级",
                            "reason": "编排异常，写入最小合法产物"}],
        }
        wrote = write_json(os.path.join(args.output, "result.json"), fallback)
        if "error" in wrote:
            write_json(os.path.join(args.work, "result.json"), fallback)
        print("degraded: %s" % exc, file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
