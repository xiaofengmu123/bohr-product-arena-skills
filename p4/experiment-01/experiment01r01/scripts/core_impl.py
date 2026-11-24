#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — scix-plan-evidence 的实现核心。

职责：输入探测（input_probe）、骨架落盘（scaffold）、结果校验（validate）、
自检（selftest）。只依赖 Python 标准库；所有写盘均为原子写
（临时文件 + os.replace），保证中途异常不留下损坏产物；输出 JSON 固定键序，
重复运行结果一致。

子命令用法（由 scripts/main.py 分派）：
  input_probe --input /input --work /app/work
  scaffold    --input /input --output /app/output --work /app/work
  validate    --input /input --output /app/output --work /app/work
  selftest    [--work DIR]   （在临时目录生成合成输入跑自检）
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

FALLBACK_CONTRACT = {
    "plan": {
        "hypothesis_and_variables": {},
        "control_groups": {},
        "equipment_and_materials": {},
        "time_and_cost": {},
        "parameter_ranges": {},
        "risks_and_exceptions": {},
    },
    "evidence_and_risks": {},
    "confirmations": {},
}

SIX_ELEMENTS = [
    "hypothesis_and_variables",
    "control_groups",
    "equipment_and_materials",
    "time_and_cost",
    "parameter_ranges",
    "risks_and_exceptions",
]

SIX_ELEMENT_TITLES = {
    "hypothesis_and_variables": "1. 假设与变量",
    "control_groups": "2. 对照组",
    "equipment_and_materials": "3. 设备材料",
    "time_and_cost": "4. 时间成本",
    "parameter_ranges": "5. 参数范围",
    "risks_and_exceptions": "6. 风险与异常",
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
ENVELOPE_KEYS = [
    "status", "sources", "uncertainty", "change_log",
    "pending_human", "evidence_map", "artifacts", "errors", "degradations",
]
MAX_FILE_BYTES = 2 * 1024 * 1024

# 可疑材料扩展名（只用于分类提示，不据此下结论）
TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".tsv", ".xml"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """按 utf-8-sig → utf-8 → latin-1 顺序容错读取；全失败返回 None。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def load_json(path):
    """容错读 JSON，返回 (obj, error)；error 为 None 表示成功。"""
    raw = read_text(path)
    if raw is None:
        return None, "无法读取文件"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "JSON 解析失败: %s" % exc


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def atomic_write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def backfill(value):
    """把 output_contract 的占位结构回填为可写的空值：dict 递归、list 变 []、标量变 null。

    仅改值、不改键名，未知键一律保留。
    """
    if isinstance(value, dict):
        return {k: backfill(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


# --------------------------------------------------------------------------- #
# 输入探测
# --------------------------------------------------------------------------- #

def walk_input(inp):
    """扫描输入目录，返回按相对路径排序的文件清单。"""
    files = []
    if not os.path.isdir(inp):
        return files
    for root, _dirs, names in os.walk(inp):
        for name in names:
            full = os.path.join(root, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, inp).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({"rel": rel, "path": full, "size": size})
    files.sort(key=lambda f: f["rel"])
    return files


def classify_files(files):
    """分类：case.json / task.md / pdf / 文本材料 / 候选方案 / office。"""
    out = {
        "case_file": None,
        "task_file": None,
        "pdfs": [],
        "texts": [],
        "candidate_plans": [],
        "offices": [],
        "skipped": [],
    }
    for f in files:
        name = os.path.basename(f["rel"]).lower()
        ext = os.path.splitext(name)[1]
        if f["size"] > MAX_FILE_BYTES and ext in TEXT_EXTS | PDF_EXTS:
            out["skipped"].append(f)
            continue
        if name == "case.json":
            out["case_file"] = f
        elif name == "task.md":
            out["task_file"] = f
        elif ext in PDF_EXTS:
            out["pdfs"].append(f)
        elif ext in OFFICE_EXTS:
            out["offices"].append(f)
        elif ext in TEXT_EXTS:
            low = name
            if any(k in low for k in ("candidate", "方案", "plan_", "plan-", "_plan", "-plan")):
                out["candidate_plans"].append(f)
            else:
                out["texts"].append(f)
    return out


def extract_case_id(case_data, task_text):
    """从 case.json / task.md 提取 case_id，返回 (id, 来源)。"""
    if isinstance(case_data, dict):
        for key in ("case_id", "challenge_id", "challengeId", "id"):
            val = case_data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), "case.json." + key
    if task_text:
        m = re.search(r"challenge[_-]?id[\"'\s:]*`?([A-Za-z0-9][A-Za-z0-9._-]*)", task_text, re.I)
        if m:
            return m.group(1).strip(), "task.md"
        m = re.search(r"challenge_id[：:\s]+([A-Za-z0-9._-]+)", task_text, re.I)
        if m:
            return m.group(1).strip(), "task.md"
    return "unknown", None


def discover_contract(case_data):
    """发现 output_contract；缺省返回 fallback 结构。返回 (contract, source)。"""
    if isinstance(case_data, dict):
        for key in ("output_contract", "outputContract", "output_schema"):
            val = case_data.get(key)
            if isinstance(val, dict):
                return val, "case.json." + key
    return FALLBACK_CONTRACT, "fallback"


def cmd_input_probe(args):
    os.makedirs(args.work, exist_ok=True)
    files = walk_input(args.input)
    classified = classify_files(files)

    case_data = None
    case_err = None
    task_text = None
    task_err = None
    degradations = []
    errors = []

    if classified["case_file"] is not None:
        case_data, case_err = load_json(classified["case_file"]["path"])
        if case_err:
            errors.append({"scope": "case.json", "detail": case_err})
            degradations.append("case.json 解析失败，按缺失处理")
    if classified["task_file"] is not None:
        task_text = read_text(classified["task_file"]["path"])
        if task_text is None:
            task_err = "task.md 无法读取"
            errors.append({"scope": "task.md", "detail": task_err})
            degradations.append("task.md 无法读取，按缺失处理")
    if classified["skipped"]:
        degradations.append("跳过超限文件 %d 个" % len(classified["skipped"]))

    case_id, case_id_src = extract_case_id(case_data, task_text)
    contract, contract_src = discover_contract(case_data)

    usable = bool(classified["case_file"] or classified["task_file"])

    probe = {
        "input_dir": args.input,
        "case_id": case_id,
        "case_id_source": case_id_src,
        "output_contract": contract,
        "contract_source": contract_src,
        "files": files,
        "case_file": (classified["case_file"] or {}).get("rel"),
        "task_file": (classified["task_file"] or {}).get("rel"),
        "pdfs": [f["rel"] for f in classified["pdfs"]],
        "texts": [f["rel"] for f in classified["texts"]],
        "candidate_plans": [f["rel"] for f in classified["candidate_plans"]],
        "offices": [f["rel"] for f in classified["offices"]],
        "skipped": [f["rel"] for f in classified["skipped"]],
        "usable_inputs": usable,
        "errors": errors,
        "degradations": degradations,
        "probed_at": utcnow(),
    }
    atomic_write_json(os.path.join(args.work, "input_probe.json"), probe)
    return 0


# --------------------------------------------------------------------------- #
# 骨架落盘
# --------------------------------------------------------------------------- #

def render_plan_draft(case_id, status):
    lines = [
        "# 实验方案（骨架）",
        "",
        "case_id: %s" % case_id,
        "status: %s（增量填写中）" % status,
        "",
        "> 本文件为运行早期落盘的骨架。六要素与依据、确认项将在后续步骤增量填写；"
        "最终版与 result.json 一致。",
        "",
    ]
    for key in SIX_ELEMENTS:
        lines.append("## %s" % SIX_ELEMENT_TITLES[key])
        lines.append("")
        lines.append("[待填写：操作定义 / 单位 / 量程 / 判据；每条关键结论附 locator + 逐字 quote]")
        lines.append("")
    lines += [
        "## 依据与风险",
        "",
        "[待填写：每条依据带来源定位与引用；风险含失败模式 / 判据 / 止损点 / 替代路径]",
        "",
        "## 确认项",
        "",
        "[待填写：安全 / 伦理 / 设备可用性 / 专家确认；未确认不写成事实]",
        "",
    ]
    return "\n".join(lines)


def cmd_scaffold(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    probe_path = os.path.join(args.work, "input_probe.json")
    if not os.path.exists(probe_path):
        # 未探测则先探测一次
        probe_args = argparse.Namespace(input=args.input, work=args.work)
        cmd_input_probe(probe_args)
    probe, err = load_json(probe_path)
    if probe is None:
        probe = {
            "case_id": "unknown",
            "output_contract": FALLBACK_CONTRACT,
            "degradations": ["input_probe.json 读取失败: %s" % err],
        }

    case_id = probe.get("case_id", "unknown")
    contract = probe.get("output_contract", FALLBACK_CONTRACT)

    result = {"case_id": case_id}
    result.update(backfill(contract))

    if case_id == "unknown" and not probe.get("usable_inputs", True):
        status = "insufficient_evidence"
    else:
        status = "partial"
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["evidence_map"] = {
        "claims": [],
        "coverage": {"claims_total": 0, "claims_with_quote": 0},
    }
    result["artifacts"] = {"plan_md": "plan.md"}
    result["change_log"] = [
        {"ts": utcnow(), "event": "skeleton_written",
         "detail": "partial 骨架落盘；后续只做增量改进"}
    ]
    result["errors"] = probe.get("errors", [])
    result["degradations"] = probe.get("degradations", [])

    atomic_write_json(os.path.join(args.output, "result.json"), result)
    atomic_write_text(os.path.join(args.output, "plan.md"),
                      render_plan_draft(case_id, status))
    return 0


# --------------------------------------------------------------------------- #
# 结果校验
# --------------------------------------------------------------------------- #

def _deep_keys(obj, prefix=""):
    """递归产出 (路径, 值) 列表，用于结构比对。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = (prefix + "." + k) if prefix else k
            out.append((p, v))
            out.extend(_deep_keys(v, p))
    return out


def check_arithmetic(result, errors, warnings):
    """对 result 中可识别的预算明细做合计校验（仅当 total 与明细同时给出）。"""
    def num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    tc = result.get("plan", {})
    if isinstance(tc, dict):
        tc = tc.get("time_and_cost", {})
    if isinstance(tc, dict):
        budget = tc.get("budget")
        if isinstance(budget, dict):
            items = budget.get("line_items")
            total = budget.get("total")
            if isinstance(items, list) and num(total):
                s = 0.0
                complete = True
                for it in items:
                    if not isinstance(it, dict):
                        complete = False
                        continue
                    amt = None
                    for k in ("amount", "cost", "price", "unit_cost"):
                        if num(it.get(k)):
                            amt = it[k]
                            break
                    if amt is None:
                        complete = False
                        continue
                    qty = it.get("quantity")
                    s += float(amt) * (float(qty) if num(qty) else 1.0)
                if complete and abs(s - float(total)) > 1e-6:
                    errors.append("预算合计 %.4f 与明细之和 %.4f 不符" % (total, s))

    # 显式算术校验条目：{name, expected, actual, tolerance}
    for chk in result.get("arithmetic_checks", []) or []:
        if not isinstance(chk, dict):
            continue
        if "expected" not in chk or "actual" not in chk:
            continue
        exp = chk["expected"]; act = chk["actual"]
        tol = chk.get("tolerance", 1e-6)
        try:
            if abs(float(exp) - float(act)) > float(tol):
                errors.append("算术校验 '%s' 不符: expected=%s actual=%s"
                              % (chk.get("name", "?"), exp, act))
        except (TypeError, ValueError):
            warnings.append("算术校验 '%s' 含非数值" % (chk.get("name", "?")))


def cmd_validate(args):
    errors = []
    warnings = []
    result_path = os.path.join(args.output, "result.json")
    plan_path = os.path.join(args.output, "plan.md")
    probe_path = os.path.join(args.work, "input_probe.json")

    probe, probe_err = load_json(probe_path)
    if probe is None:
        probe = {"output_contract": FALLBACK_CONTRACT}
        warnings.append("input_probe.json 不可读: %s" % probe_err)

    if not os.path.exists(result_path):
        print("FATAL: 缺少 %s" % result_path)
        return 1
    result, err = load_json(result_path)
    if result is None:
        print("FATAL: result.json 不可解析: %s" % err)
        return 1
    if not isinstance(result, dict):
        print("FATAL: result.json 顶层必须是对象")
        return 1

    # 1. case_id
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        errors.append("缺少非空 case_id")
    # 2. status 合法
    if result.get("status") not in STATUSES:
        errors.append("status 必须是 %s 之一，实际 %r" % (sorted(STATUSES), result.get("status")))
    # 3. 契约键全覆盖（顶层）
    contract = probe.get("output_contract", FALLBACK_CONTRACT)
    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                errors.append("output_contract 顶层键缺失: %s" % key)
    # 4. 信封字段
    for key in ENVELOPE_KEYS:
        if key not in result:
            errors.append("信封字段缺失: %s" % key)
    if not isinstance(result.get("change_log"), list):
        errors.append("change_log 必须是列表")

    # 5. 六要素（仅对 fallback/plan 结构做内容提示）
    plan = result.get("plan")
    if isinstance(plan, dict):
        for key in SIX_ELEMENTS:
            if key not in plan:
                warnings.append("plan 缺少六要素键: %s" % key)
            elif not plan[key]:
                warnings.append("plan.%s 为空（骨架阶段可接受，最终版须填写）" % key)
    else:
        warnings.append("plan 不是对象，六要素不可检查")

    # 6. 证据溯源（本版强化）：每条 claim 必须有 locator + 逐字 quote
    em = result.get("evidence_map")
    if isinstance(em, dict):
        claims = em.get("claims")
        if isinstance(claims, list):
            for i, c in enumerate(claims):
                tag = "evidence_map.claims[%d]" % i
                if not isinstance(c, dict):
                    errors.append("%s 不是对象" % tag)
                    continue
                if not isinstance(c.get("statement"), str) or not c["statement"].strip():
                    errors.append("%s 缺少 statement" % tag)
                if not isinstance(c.get("locator"), str) or not c["locator"].strip():
                    errors.append("%s 缺少 locator" % tag)
                if not isinstance(c.get("quote"), str) or not c["quote"].strip():
                    errors.append("%s 缺少逐字 quote" % tag)
        else:
            warnings.append("evidence_map.claims 不是列表（骨架阶段可接受）")
        cov = em.get("coverage")
        if isinstance(cov, dict):
            total = cov.get("claims_total")
            withq = cov.get("claims_with_quote")
            if isinstance(total, int) and isinstance(withq, int):
                actual = len(em.get("claims", [])) if isinstance(em.get("claims"), list) else 0
                if total != actual:
                    warnings.append("coverage.claims_total=%d 与实际 %d 不符" % (total, actual))
    else:
        warnings.append("evidence_map 不是对象")

    # 7. 算术校验
    check_arithmetic(result, errors, warnings)

    # 8. plan.md 存在且含六要素标题
    if not os.path.exists(plan_path):
        errors.append("缺少主产物 plan.md")
    else:
        md = read_text(plan_path)
        if md is None:
            errors.append("plan.md 不可读")
        else:
            for key, title in SIX_ELEMENT_TITLES.items():
                if title not in md:
                    warnings.append("plan.md 缺少标题: %s" % title)
            if str(result.get("case_id", "")) not in md:
                warnings.append("plan.md 未出现 case_id，与 result.json 可能不一致")

    print("=== validate 报告 ===")
    print("errors:   %d" % len(errors))
    for e in errors:
        print("  [E] " + e)
    print("warnings: %d" % len(warnings))
    for w in warnings:
        print("  [W] " + w)

    if errors:
        print("RESULT: 必须修复（exit 1）")
        return 1
    print("RESULT: 可用（exit 0，%d 条警告）" % len(warnings))
    return 0


# --------------------------------------------------------------------------- #
# 自检（合成输入，非正式案例）
# --------------------------------------------------------------------------- #

def _run_probe_scaffold_validate(inp, out, work):
    ns = argparse.Namespace(input=inp, output=out, work=work)
    cmd_input_probe(ns)
    cmd_scaffold(ns)
    return cmd_validate(ns)


def cmd_selftest(args):
    tmp = tempfile.mkdtemp(prefix="scix_selftest_")
    passed = []
    failed = []

    def check(name, cond, note=""):
        if cond:
            passed.append(name)
        else:
            failed.append(name + ((" — " + note) if note else ""))

    try:
        # 场景 1：正常合成输入
        base = os.path.join(tmp, "normal")
        inp = os.path.join(base, "input")
        out = os.path.join(base, "output")
        work = os.path.join(base, "work")
        mats = os.path.join(inp, "materials")
        os.makedirs(mats)
        atomic_write_text(os.path.join(inp, "task.md"),
                          "# 合成自检任务\n\n- challenge_id: `selftest-000`\n")
        atomic_write_json(os.path.join(inp, "case.json"), {
            "__synthetic__": True,
            "case_id": "selftest-000",
            "output_contract": FALLBACK_CONTRACT,
        })
        atomic_write_text(os.path.join(mats, "paper.md"),
                          "## 合成材料\n\n本材料仅用于自检，不是正式案例结果。\n")
        code = _run_probe_scaffold_validate(inp, out, work)
        check("正常路径 validate 退出码 0", code == 0, "exit=%d" % code)
        r, _ = load_json(os.path.join(out, "result.json"))
        check("正常路径 result.json 存在且可解析", isinstance(r, dict))
        if isinstance(r, dict):
            check("正常路径 case_id 正确", r.get("case_id") == "selftest-000")
            check("正常路径契约键齐全",
                  all(k in r for k in FALLBACK_CONTRACT))
            check("正常路径 status=partial", r.get("status") == "partial")
            check("正常路径 plan.md 存在", os.path.exists(os.path.join(out, "plan.md")))

        # 场景 2：空输入目录 → insufficient_evidence
        base2 = os.path.join(tmp, "empty")
        inp2 = os.path.join(base2, "input")
        out2 = os.path.join(base2, "output")
        work2 = os.path.join(base2, "work")
        os.makedirs(inp2)
        code2 = _run_probe_scaffold_validate(inp2, out2, work2)
        r2, _ = load_json(os.path.join(out2, "result.json"))
        check("空输入 status=insufficient_evidence",
              isinstance(r2, dict) and r2.get("status") == "insufficient_evidence")

        # 场景 3：坏 JSON → 记录错误但不崩溃
        base3 = os.path.join(tmp, "badjson")
        inp3 = os.path.join(base3, "input")
        out3 = os.path.join(base3, "output")
        work3 = os.path.join(base3, "work")
        os.makedirs(inp3)
        atomic_write_text(os.path.join(inp3, "case.json"), "{ 这不是合法 JSON")
        atomic_write_text(os.path.join(inp3, "task.md"),
                          "# 合成自检任务\n\n- challenge_id: `selftest-bad`\n")
        code3 = _run_probe_scaffold_validate(inp3, out3, work3)
        check("坏 JSON 不崩溃（probe 记录错误）", code3 in (0, 1))
        r3, _ = load_json(os.path.join(out3, "result.json"))
        check("坏 JSON 仍产出骨架", isinstance(r3, dict))
        if isinstance(r3, dict):
            check("坏 JSON 记录到 errors/degradations",
                  r3.get("errors") or r3.get("degradations"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("=== selftest ===")
    print("PASS: %d" % len(passed))
    for p in passed:
        print("  [ok] " + p)
    print("FAIL: %d" % len(failed))
    for f in failed:
        print("  [FAIL] " + f)
    if failed:
        return 1
    print("SELFTEST PASS")
    return 0


# --------------------------------------------------------------------------- #
# 分派
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="scix-plan-evidence 实现")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("input_probe", "scaffold", "validate"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("selftest")
    sp.add_argument("--work", default="/app/work")
    return p


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    fn = {
        "input_probe": cmd_input_probe,
        "scaffold": cmd_scaffold,
        "validate": cmd_validate,
        "selftest": cmd_selftest,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
