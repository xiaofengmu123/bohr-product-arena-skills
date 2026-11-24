"""core_impl.py - experiment plan skill engine: probe / scaffold / validate.

Standard library only. Deterministic JSON output (fixed key order, no random
numbers, no wall-clock timestamps inside content). The honesty envelope
(limitations / uncertainty / pending_human) and the no-fabrication gate are
first-class, not afterthoughts.

Commands (paths are relative to the Skill root):
  probe     scan /input, read case.json/task.md, discover output_contract,
            write work/input_probe.json
  scaffold  write a legal partial result.json + plan.md draft before analysis
  validate  structural / completeness / evidence / no-fabrication / arithmetic
            self-check; exit 0 = usable (warnings allowed), 1 = must fix
"""

import argparse
import json
import os
import re
import sys

ENGINE = "core_impl.py"
VERSION = "1.0.0"

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

SIX_ELEMENTS = [
    "hypothesis_and_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_and_anomalies",
]

FALLBACK_CONTRACT = {"plan": {}, "evidence_and_risks": {}, "confirmations": {}}

# Honest basis vocabulary. A claim that carries none of these is "unfounded".
HONEST_BASIS = {
    "case_given", "computed", "assumption",
    "not_reported", "pending_human", "literature", "device_manual",
}
# Basis values that additionally need a concrete locator / source id.
LOCATOR_REQUIRED = {"literature", "device_manual"}

BASIS_FIELDS = ("basis", "依据")
LOCATOR_FIELDS = ("locator", "source_id", "source", "page", "页码")

MAX_FILE_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read utf-8-sig / utf-8 / latin-1 with fallback. Returns (text, enc, err)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", "", "cannot stat %s: %s" % (path, exc)
    if size > max_bytes:
        return "", "", "file exceeds %d bytes, skipped" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc, ""
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return "", "", "cannot read %s: %s" % (path, exc)
    return "", "", "undecodable %s" % path


def read_json_tolerant(path):
    """Return (obj, err). Never raises on bad JSON / bad encoding."""
    text, _enc, err = read_text(path)
    if err:
        return None, err
    if not text.strip():
        return None, "empty file: %s" % os.path.basename(path)
    try:
        return json.loads(text), ""
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "invalid JSON in %s: %s" % (os.path.basename(path), exc)


def atomic_write_json(path, obj):
    """Write JSON via .tmp + os.replace. Deterministic key order (as built)."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def to_float(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_empty(value):
    if value is None:
        return True
    if isinstance(value, (str, list, dict)):
        return len(value) == 0
    return False


def element_is_empty(element):
    """A six-element block is empty when it is missing or all fields are empty."""
    if element is None:
        return True
    if not isinstance(element, dict):
        return is_empty(element)
    if not element:
        return True
    return all(is_empty(v) for v in element.values())


# --------------------------------------------------------------------------
# input discovery & contract
# --------------------------------------------------------------------------

def discover_input(input_dir):
    listing = []
    if not os.path.isdir(input_dir):
        return listing
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if os.path.isfile(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            listing.append({"name": name, "path": path, "size": size})
    return listing


def load_case(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    if not os.path.isfile(case_path):
        return None, "missing case.json", None
    case_obj, err = read_json_tolerant(case_path)
    return case_obj, err, case_path


def extract_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id"):
            value = case_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if task_text:
        match = re.search(
            r"challenge[-_ ]?id\s*[:：]\s*`?([A-Za-z0-9._\-]+)`?", task_text, re.I
        )
        if match:
            return match.group(1)
    return "unknown"


def extract_output_contract(case_obj):
    """Return (contract_dict, has_contract). Falls back to the canonical contract."""
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract")
        if isinstance(contract, dict) and contract:
            return contract, True
    return dict(FALLBACK_CONTRACT), False


def contract_placeholder(key):
    """Type placeholder for one output_contract key (unknown -> null)."""
    if key == "plan":
        return fallback_plan_template()
    if key == "evidence_and_risks":
        return {"evidence": [], "risks": []}
    if key == "confirmations":
        return []
    return None


def fallback_plan_template():
    return {
        "hypothesis_and_variables": {
            "hypothesis": "",
            "independent_variables": [],
            "dependent_variables": [],
            "confounders": [],
            "operational_definitions": [],
            "measurement_methods": [],
        },
        "controls": {
            "groups": [],
            "randomization": "",
            "blinding": "",
            "replicates": "",
        },
        "equipment_materials": {"items": [], "notes": ""},
        "time_cost": {
            "steps": [],
            "sample_count": "",
            "total_hours": "",
            "budget": "",
            "budget_items": [],
            "schedule_conflicts": [],
        },
        "parameter_ranges": {"parameters": []},
        "risks_and_anomalies": {"risks": [], "notes": ""},
    }


def build_envelope():
    return {
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "degradations": [],
        "errors": [],
        "change_log": [],
        "runtime": {"engine": ENGINE, "version": VERSION},
    }


# --------------------------------------------------------------------------
# command: probe
# --------------------------------------------------------------------------

def run_probe(args):
    os.makedirs(args.work, exist_ok=True)
    input_dir = args.input
    listing = discover_input(input_dir)
    case_obj, case_err, _ = load_case(input_dir)

    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        task_text, _, task_err = read_text(task_path)
    else:
        task_text, task_err = "", "missing task.md"

    case_id = extract_case_id(case_obj, task_text)
    contract, has_contract = extract_output_contract(case_obj)
    pdfs = [f["name"] for f in listing if f["name"].lower().endswith(".pdf")]
    others = [
        f["name"] for f in listing
        if f["name"] not in ("case.json", "task.md")
        and not f["name"].lower().endswith(".pdf")
    ]

    probe = {
        "input_dir": input_dir,
        "files": listing,
        "case_id": case_id,
        "case_json_readable": case_obj is not None,
        "case_json_error": case_err,
        "task_md_readable": bool(task_text),
        "task_md_error": task_err,
        "has_output_contract": has_contract,
        "output_contract_keys": sorted(contract.keys()),
        "pdfs": pdfs,
        "other_files": others,
        "degradations": [d for d in (case_err, task_err) if d],
    }
    atomic_write_json(os.path.join(args.work, "input_probe.json"), probe)
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# command: scaffold
# --------------------------------------------------------------------------

def build_scaffold_result(input_dir):
    case_obj, case_err, _ = load_case(input_dir)
    task_path = os.path.join(input_dir, "task.md")
    task_text = read_text(task_path)[0] if os.path.isfile(task_path) else ""

    case_id = extract_case_id(case_obj, task_text)
    contract, has_contract = extract_output_contract(case_obj)

    result = {"case_id": case_id}
    for key in sorted(contract.keys()):
        result[key] = contract_placeholder(key)
    result.update(build_envelope())

    if case_err and case_err != "missing case.json":
        status = "input_error"
    elif case_obj is None and not task_text.strip():
        status = "insufficient_evidence"
    else:
        status = "partial"
    result["status"] = status

    degradations = []
    if case_err:
        severity = "warning" if case_err == "missing case.json" else "error"
        degradations.append({
            "step": "input", "issue": case_err,
            "severity": severity,
            "fallback": "降级为任务文本/fallback 契约，缺失内容写入 pending_human",
        })
    result["degradations"] = degradations

    if not has_contract:
        result["limitations"] = [{
            "id": "contract_fallback",
            "scope": "output_contract",
            "limit": "case.json 未提供 output_contract，使用 fallback 结构",
            "reason": "缺契约时按 plan/evidence_and_risks/confirmations 组织",
        }]

    if case_id == "unknown":
        result["pending_human"] = [{
            "id": "case_id", "item": "确定 case_id",
            "reason": "case.json 与 task.md 均未提供", "blocking": False,
        }]

    result["change_log"] = [{
        "seq": 1, "event": "skeleton_written",
        "detail": "partial result scaffold; status=%s; contract=%s"
                  % (status, "case" if has_contract else "fallback"),
    }]
    return result, status


def build_plan_draft(result, status):
    lines = [
        "# 实验方案（草稿）",
        "",
        "> 本文件为部分结果骨架，尚未完成证据提取与方案生成；status: `%s`。" % status,
        "> 所有数值/型号/价格/引用须带 basis 标记，缺失写 not_reported。",
        "",
    ]
    headings = [
        ("hypothesis_and_variables", "假设与变量"),
        ("controls", "对照组"),
        ("equipment_materials", "设备材料"),
        ("time_cost", "时间成本"),
        ("parameter_ranges", "参数范围"),
        ("risks_and_anomalies", "风险与异常"),
    ]
    for _key, label in headings:
        lines += [
            "## %s" % label,
            "",
            "（待填充：操作定义、单位、量程、判据、依据与 basis 标记）",
            "",
        ]
    lines += [
        "## 依据与风险",
        "",
        "（每条依据：source_id + locator + 逐字 quote；风险含失败模式/判据/止损点/替代路径）",
        "",
        "## 确认项",
        "",
        "（安全 / 伦理 / 设备可用性 / 专家确认；未确认列 pending_human）",
        "",
        "## 不确定性与限制",
        "",
        "（uncertainty / limitations 显式化；缺失数据写 not_reported，不编造）",
        "",
    ]
    return "\n".join(lines)


def run_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    try:
        result, status = build_scaffold_result(args.input)
    except Exception as exc:  # never leave /app/output without result.json
        minimal = {
            "case_id": "unknown",
            "status": "input_error",
            "errors": [{"step": "scaffold", "message": "%s: %s" % (type(exc).__name__, exc)}],
            "change_log": [{"seq": 1, "event": "minimal_fallback"}],
        }
        try:
            atomic_write_json(os.path.join(args.output, "result.json"), minimal)
        except OSError:
            pass
        sys.stderr.write("scaffold degraded to minimal result: %s\n" % exc)
        return 1

    atomic_write_json(os.path.join(args.output, "result.json"), result)
    write_text(os.path.join(args.output, "plan.md"), build_plan_draft(result, status))
    print(json.dumps({
        "case_id": result["case_id"],
        "status": status,
        "result": os.path.join(args.output, "result.json"),
        "plan": os.path.join(args.output, "plan.md"),
    }, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# command: validate (pure function + file wrapper)
# --------------------------------------------------------------------------

def claim_basis(item):
    if not isinstance(item, dict):
        return ""
    for key in BASIS_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def has_locator(item):
    if not isinstance(item, dict):
        return False
    for key in LOCATOR_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def validate_result_object(result, contract_keys):
    errors = []
    warnings = []
    unfounded = []
    arithmetic_conflicts = []
    coverage = {}

    if not isinstance(result, dict):
        return {
            "ok": False,
            "errors": [{"path": "$", "message": "result 不是 JSON 对象"}],
            "warnings": [], "unfounded_claims": [],
            "arithmetic_conflicts": [], "coverage": {},
        }

    status = result.get("status")

    # 1. identity / status
    case_id = result.get("case_id")
    if not (isinstance(case_id, str) and case_id.strip()):
        errors.append({"path": "case_id", "message": "case_id 缺失或为空"})
    if status not in STATUSES:
        errors.append({
            "path": "status",
            "message": "status 非法，须为 %s" % sorted(STATUSES),
        })

    # 2. contract keys
    for key in contract_keys:
        if key not in result:
            errors.append({
                "path": key,
                "message": "output_contract 字段缺失（不得改名、删除未知键）",
            })

    # 3. envelope presence
    for key in ("sources", "uncertainty", "pending_human", "limitations", "change_log"):
        if key not in result:
            errors.append({"path": key, "message": "信封字段缺失"})

    # 4. six-element coverage (only against the canonical `plan` object)
    plan = result.get("plan")
    if isinstance(plan, dict) or plan is None:
        plan = plan or {}
        for element in SIX_ELEMENTS:
            present = element in plan
            filled = present and not element_is_empty(plan.get(element))
            coverage[element] = filled
            if not present:
                if status in ("partial", "ok"):
                    errors.append({
                        "path": "plan.%s" % element,
                        "message": "六要素之一缺失",
                    })
                else:
                    warnings.append({
                        "path": "plan.%s" % element,
                        "message": "六要素之一未提供（status=%s 允许）" % status,
                    })
            elif not filled:
                if status == "ok":
                    errors.append({
                        "path": "plan.%s" % element,
                        "message": "六要素之一为空，不得标 ok",
                    })
                elif status == "partial":
                    warnings.append({
                        "path": "plan.%s" % element,
                        "message": "六要素之一仍为空（partial 允许，但应尽快补齐）",
                    })

    # 5. evidence traceability
    ear = result.get("evidence_and_risks")
    if isinstance(ear, dict):
        for i, ev in enumerate(ear.get("evidence") or []):
            path = "evidence_and_risks.evidence[%d]" % i
            if not isinstance(ev, dict):
                errors.append({"path": path, "message": "证据项不是对象"})
                continue
            if not (isinstance(ev.get("locator"), str) and ev["locator"].strip()):
                errors.append({"path": path + ".locator", "message": "证据缺 locator"})
            if not (isinstance(ev.get("quote"), str) and ev["quote"].strip()):
                errors.append({"path": path + ".quote", "message": "证据缺逐字 quote"})
        for i, risk in enumerate(ear.get("risks") or []):
            path = "evidence_and_risks.risks[%d]" % i
            if not isinstance(risk, dict):
                errors.append({"path": path, "message": "风险项不是对象"})
                continue
            if not (isinstance(risk.get("failure_mode"), str) and risk["failure_mode"].strip()):
                errors.append({"path": path + ".failure_mode", "message": "风险缺失败模式"})
            if not (isinstance(risk.get("alternative_path"), str) and risk["alternative_path"].strip()):
                errors.append({"path": path + ".alternative_path", "message": "风险缺替代路径"})
            for field in ("detection", "threshold", "stop_point"):
                if not risk.get(field):
                    warnings.append({
                        "path": "%s.%s" % (path, field),
                        "message": "风险缺 %s（建议补齐）" % field,
                    })

    # 6. no-fabrication gate: every concrete claim needs an honest basis
    def check_claims(items, base_path):
        for i, item in enumerate(items or []):
            if not isinstance(item, dict):
                continue
            path = "%s[%d]" % (base_path, i)
            basis = claim_basis(item)
            if not basis:
                unfounded.append({
                    "path": path,
                    "message": "缺少 basis（case_given/computed/assumption/not_reported/pending_human/literature/device_manual）",
                })
            elif basis not in HONEST_BASIS:
                unfounded.append({
                    "path": path,
                    "message": "basis 取值 '%s' 不在允许集合内" % basis,
                })
            elif basis in LOCATOR_REQUIRED and not has_locator(item):
                unfounded.append({
                    "path": path,
                    "message": "basis=%s 但缺 locator/source_id" % basis,
                })

    if isinstance(plan, dict):
        pr = plan.get("parameter_ranges")
        if isinstance(pr, dict):
            check_claims(pr.get("parameters"), "plan.parameter_ranges.parameters")
        em = plan.get("equipment_materials")
        if isinstance(em, dict):
            check_claims(em.get("items"), "plan.equipment_materials.items")
        tc = plan.get("time_cost")
        if isinstance(tc, dict):
            check_claims(tc.get("budget_items"), "plan.time_cost.budget_items")

    for item in unfounded:
        errors.append({
            "path": item["path"],
            "message": "未证实声明：%s；请补 basis 或改写为 not_reported/pending_human"
                       % item["message"],
        })

    # 7. arithmetic checks
    if isinstance(plan, dict):
        tc = plan.get("time_cost")
        if isinstance(tc, dict):
            steps = tc.get("steps") or []
            if isinstance(steps, list) and steps:
                duration_sum = 0.0
                parsable = True
                for i, step in enumerate(steps):
                    dur = step.get("duration_hours") if isinstance(step, dict) else None
                    number = to_float(dur)
                    if number is None:
                        parsable = False
                        warnings.append({
                            "path": "plan.time_cost.steps[%d].duration_hours" % i,
                            "message": "步骤缺可解析时长（小时）",
                        })
                    else:
                        duration_sum += number
                total = to_float(tc.get("total_hours"))
                if parsable and total is not None and abs(total - duration_sum) > 0.5:
                    arithmetic_conflicts.append({
                        "path": "plan.time_cost.total_hours",
                        "message": "步骤时长之和 %.1f 与 total_hours %.1f 不一致"
                                   % (duration_sum, total),
                    })
        pr = plan.get("parameter_ranges")
        if isinstance(pr, dict):
            for i, param in enumerate(pr.get("parameters") or []):
                if not isinstance(param, dict):
                    continue
                path = "plan.parameter_ranges.parameters[%d]" % i
                pmin = to_float(param.get("allowed_min"))
                pmax = to_float(param.get("allowed_max"))
                if pmin is not None and pmax is not None and pmin > pmax:
                    arithmetic_conflicts.append({
                        "path": path,
                        "message": "allowed_min %.4g > allowed_max %.4g" % (pmin, pmax),
                    })
                emin = to_float(param.get("equipment_range_min"))
                emax = to_float(param.get("equipment_range_max"))
                if None not in (pmin, pmax, emin, emax):
                    if pmin < emin or pmax > emax:
                        arithmetic_conflicts.append({
                            "path": path,
                            "message": "[%.4g, %.4g] 超出设备量程 [%.4g, %.4g]，需降级或替换设备"
                                       % (pmin, pmax, emin, emax),
                        })
    for conflict in arithmetic_conflicts:
        errors.append({
            "path": conflict["path"],
            "message": "算术校验失败：%s；应修正数值或写入 uncertainty 并改 status"
                       % conflict["message"],
        })

    # 8. honesty of status
    if status == "ok":
        if result.get("pending_human"):
            errors.append({
                "path": "status",
                "message": "存在未决 pending_human 却标 ok",
            })
        if not (result.get("sources") or []):
            warnings.append({
                "path": "sources",
                "message": "status=ok 但 sources 为空，请补充来源或改为 not_reported",
            })
    elif status in ("partial", "insufficient_evidence") and not (result.get("limitations") or []):
        warnings.append({
            "path": "limitations",
            "message": "非 ok 状态建议显式列出 limitations（缺什么、为何缺）",
        })

    report = {
        "ok": not errors,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "unfounded_claims": unfounded,
        "arithmetic_conflicts": arithmetic_conflicts,
        "coverage": coverage,
    }
    return report


def run_validate(args):
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    report_path = os.path.join(args.work, "validate_report.json")

    if not os.path.isfile(result_path):
        report = {
            "ok": False,
            "errors": [{"path": "$", "message": "result.json 不存在"}],
            "warnings": [], "unfounded_claims": [],
            "arithmetic_conflicts": [], "coverage": {},
        }
        atomic_write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    result, err = read_json_tolerant(result_path)
    if err:
        report = {
            "ok": False,
            "errors": [{"path": "$", "message": err}],
            "warnings": [], "unfounded_claims": [],
            "arithmetic_conflicts": [], "coverage": {},
        }
        atomic_write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    case_obj, _, _ = load_case(args.input)
    contract, _ = extract_output_contract(case_obj)
    report = validate_result_object(result, sorted(contract.keys()))

    atomic_write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    for name in ("probe", "scaffold", "validate"):
        sub_parser = sub.add_parser(name)
        sub_parser.add_argument("--input", default="/input")
        sub_parser.add_argument("--output", default="/app/output")
        sub_parser.add_argument("--work", default="/app/work")
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "probe":
        return run_probe(args)
    if args.command == "scaffold":
        return run_scaffold(args)
    if args.command == "validate":
        return run_validate(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # last-resort guard: never dump a raw traceback
        sys.stderr.write("core_impl: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
