"""core_impl.py — 实验方案 Skill 流水线：probe → scaffold → validate。

架构（与常见“三脚本各写各的”不同）：
- 以 /app/work/ir.json 为唯一中间表示（IR），result.json 与 plan.md 是 IR 的两个投影，
  二者一致性由“同源派生”保证，而非事后人工对齐。
- 证据以 claim_id 台账记录（locator + 逐字 quote + 冲突标记），六要素只引用 claim_id。
- 校验顺序固定：结构 → 类型 → 六要素完整性 → 算术 → 证据 → 一致性；
  退出码 0=可用（含警告），1=必须修复（结构/类型级）。

仅用 Python 标准库；重复运行确定（固定键序、无随机、时间戳除外）。
"""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

PDFTOTEXT = shutil.which("pdftotext")

SIX_ELEMENTS = [
    "hypotheses_and_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_anomalies",
]

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

ENVELOPE_LIST_KEYS = ["sources", "uncertainty", "pending_human", "change_log"]

FALLBACK_CONTRACT = {
    "plan": {
        "hypotheses_and_variables": {},
        "controls": {},
        "equipment_materials": {},
        "time_cost": {},
        "parameter_ranges": {},
        "risks_anomalies": {},
    },
    "evidence_and_risks": {
        "evidence_ledger": {},
        "risk_register": [],
        "arithmetic_checks": [],
    },
    "confirmations": {
        "safety": [],
        "ethics": [],
        "equipment_availability": [],
        "expert_review": [],
    },
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _scan_tree(root):
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            files.append({"path": p, "rel": rel, "size": size})
    return files


def _kind_of(rel):
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    if ext == "pdf":
        return "pdf"
    if ext in ("md", "txt", "rst"):
        return "text"
    if ext == "json":
        return "json"
    if ext in ("csv", "tsv"):
        return "tabular"
    if ext in ("docx", "xlsx", "pptx"):
        return "office"
    return "other"


def _extract_pdf(pdf_path, out_path):
    if not PDFTOTEXT:
        return False, "pdftotext 不可用"
    try:
        r = subprocess.run(
            [PDFTOTEXT, "-layout", pdf_path, out_path],
            capture_output=True, timeout=180,
        )
        ok = r.returncode == 0 and os.path.exists(out_path)
        return ok, ("" if ok else (r.stderr or b"").decode("utf-8", "replace")[:200])
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _extract_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for k in ("case_id", "id", "challenge_id"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    m = re.search(r"challenge_id:\s*`?([^\s`]+)`?", task_text or "")
    if m:
        return m.group(1)
    return None


def _probe(args):
    ir = {
        "ir_version": 1,
        "created_at": _ts(),
        "input_dir": args.input,
        "work_dir": args.work,
        "output_dir": args.output,
        "case_id": None,
        "challenge_id": None,
        "output_contract": None,
        "has_contract": False,
        "files": [],
        "materials": [],
        "candidate_plans": [],
        "contracts": [],
        "case_json_keys": [],
        "degradations": [],
        "errors": [],
    }
    os.makedirs(args.work, exist_ok=True)
    text_dir = os.path.join(args.work, "texts")
    os.makedirs(text_dir, exist_ok=True)

    files = _scan_tree(args.input)
    ir["files"] = [{"rel": f["rel"], "size": f["size"], "kind": _kind_of(f["rel"])} for f in files]
    if not files:
        ir["degradations"].append("输入目录为空或不可读")

    task_text = None
    case_obj = None
    case_file = None

    # task.md
    for f in files:
        if f["rel"].split("/")[-1].lower() == "task.md" and f["size"] <= MAX_FILE_BYTES:
            task_text = _read_text(f["path"])
            m = re.search(r"challenge_id:\s*`?([^\s`]+)`?", task_text)
            if m:
                ir["challenge_id"] = m.group(1)
            break

    # case.json（优先精确名，否则取含 output_contract/case_id 的顶层 json）
    exact = [f for f in files if f["rel"].split("/")[-1].lower() == "case.json" and f["size"] <= MAX_FILE_BYTES]
    case_file_rel = exact[0]["rel"] if exact else None
    candidates = exact or [f for f in files if f["rel"].count("/") == 0 and _kind_of(f["rel"]) == "json"]
    for f in candidates:
        if f["size"] <= 0 or f["size"] > MAX_FILE_BYTES:
            continue
        try:
            obj = json.loads(_read_text(f["path"]))
        except json.JSONDecodeError as e:
            ir["errors"].append("case.json 解析失败: %s" % e)
            continue
        if isinstance(obj, dict) and ("output_contract" in obj or "case_id" in obj or "id" in obj or "goal" in obj or "task" in obj):
            case_obj = obj
            case_file = f
            break
    if case_obj is None and case_file_rel:
        # 精确 case.json 无法解析时也记录，并在材料扫描中排除该文件
        ir["errors"].append("case.json 无法解析或格式不符，降级为无契约")

    if case_obj is not None:
        ir["case_id"] = _extract_case_id(case_obj, task_text)
        ir["case_json_keys"] = list(case_obj.keys())
        contract = case_obj.get("output_contract")
        if isinstance(contract, dict) and contract:
            ir["output_contract"] = copy.deepcopy(contract)
            ir["has_contract"] = True
        # 候选方案可能内嵌在 case.json
        for key, val in case_obj.items():
            if re.search(r"candidate|plan|version|方案|候选", key, re.I) and isinstance(val, (list, dict)):
                ir["candidate_plans"].append({
                    "rel": "%s#%s" % (case_file["rel"], key),
                    "source": "case_json",
                    "text": json.dumps(val, ensure_ascii=False, indent=2)[:100000],
                })
    if not ir["case_id"]:
        ir["case_id"] = ir.get("challenge_id")

    # 材料 / 合同 / 候选方案文件
    for f in files:
        if f["size"] > MAX_FILE_BYTES:
            ir["degradations"].append("跳过超大文件(>2MB): %s" % f["rel"])
            continue
        kind = _kind_of(f["rel"])
        rel = f["rel"]
        low = rel.lower()
        is_case = f["rel"] == case_file_rel
        is_task = rel.split("/")[-1].lower() == "task.md"
        if is_case or is_task:
            continue
        if kind == "pdf":
            base = os.path.splitext(os.path.basename(rel))[0]
            out_txt = os.path.join(text_dir, base + ".txt")
            ok, err = _extract_pdf(f["path"], out_txt)
            entry = {"rel": rel, "kind": kind, "text_path": out_txt, "extracted": ok, "pages": 0}
            if ok and os.path.exists(out_txt):
                txt = _read_text(out_txt)
                entry["pages"] = txt.count("\f") + 1 if "\f" in txt else 1
                entry["char_len"] = len(txt)
            else:
                ir["degradations"].append("PDF 文本提取失败: %s (%s)" % (rel, err))
            ir["materials"].append(entry)
            if re.search(r"contract|price|booking|quote|resource|合同|价格|排期|报价|资源", low):
                ir["contracts"].append({"rel": rel, "text_path": out_txt})
            if re.search(r"candidate|plan|version|v[123]|方案|候选", low):
                ir["candidate_plans"].append({"rel": rel, "source": "file", "text": (_read_text(out_txt) if ok and os.path.exists(out_txt) else "")[:100000]})
        elif kind in ("text", "tabular", "office", "json"):
            content = ""
            if kind in ("text", "tabular", "json"):
                content = _read_text(f["path"])
            ir["materials"].append({"rel": rel, "kind": kind, "text_path": None, "char_len": len(content)})
            if re.search(r"contract|price|booking|quote|resource|合同|价格|排期|报价|资源", low):
                ir["contracts"].append({"rel": rel, "text": content[:100000]})
            if re.search(r"candidate|plan|version|v[123]|方案|候选", low):
                ir["candidate_plans"].append({"rel": rel, "source": "file", "text": content[:100000]})
        else:
            ir["materials"].append({"rel": rel, "kind": kind, "text_path": None})

    ir["case_id"] = ir.get("case_id") or "unknown"
    _write_json(os.path.join(args.work, "ir.json"), ir)
    print(json.dumps({
        "case_id": ir["case_id"],
        "has_contract": ir["has_contract"],
        "materials": len(ir["materials"]),
        "candidate_plans": len(ir["candidate_plans"]),
        "contracts": len(ir["contracts"]),
        "degradations": len(ir["degradations"]),
        "ir": os.path.join(args.work, "ir.json"),
    }, ensure_ascii=False))
    return 0


def _build_sources(ir):
    sources = []
    for m in ir.get("materials", []):
        src = {"id": "S%d" % (len(sources) + 1), "locator": m.get("rel"), "kind": m.get("kind")}
        if m.get("text_path"):
            src["text_path"] = m["text_path"]
        sources.append(src)
    if ir.get("challenge_id"):
        sources.append({"id": "S_task", "locator": "task.md", "kind": "task"})
    return sources


def _scaffold(args):
    ir_path = os.path.join(args.work, "ir.json")
    if not os.path.exists(ir_path):
        return _scaffold_fallback(args)
    ir = json.loads(_read_text(ir_path))
    os.makedirs(args.output, exist_ok=True)
    case_id = ir.get("case_id") or "unknown"
    result = {"case_id": case_id}

    contract = ir.get("output_contract")
    if isinstance(contract, dict) and contract:
        for k in contract:
            result[k] = None
    else:
        result.update(copy.deepcopy(FALLBACK_CONTRACT))

    status = "partial"
    substantive = case_id != "unknown" or bool(contract) or bool(ir.get("candidate_plans")) or bool(ir.get("challenge_id"))
    if not substantive:
        status = "insufficient_evidence"
    result["status"] = status
    result["artifacts"] = [{"path": "/app/output/plan.md", "role": "main_plan", "format": "markdown"}]
    result["sources"] = _build_sources(ir)
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{"ts": _ts(), "event": "skeleton_written", "detail": "partial 骨架已落盘；契约键已回填，待增量填写"}]
    result["errors"] = list(ir.get("errors", []))
    result["degradations"] = list(ir.get("degradations", []))
    result["runtime"] = {"probe_ts": ir.get("created_at"), "scaffold_ts": _ts()}

    _write_json(os.path.join(args.output, "result.json"), result)
    _write_plan_draft(args.output, case_id, ir)
    print(json.dumps({"case_id": case_id, "status": status, "result": os.path.join(args.output, "result.json")}, ensure_ascii=False))
    return 0


def _scaffold_fallback(args):
    os.makedirs(args.output, exist_ok=True)
    result = {"case_id": "unknown"}
    result.update(copy.deepcopy(FALLBACK_CONTRACT))
    result["status"] = "insufficient_evidence"
    result["artifacts"] = [{"path": "/app/output/plan.md", "role": "main_plan", "format": "markdown"}]
    result["sources"] = []
    result["uncertainty"] = [{"item": "输入缺失", "detail": "未找到 /input/case.json 或 task.md，无法判定案例", "needs_expert": False}]
    result["pending_human"] = ["请提供研究目标、材料与约束，再重跑 probe/scaffold"]
    result["change_log"] = [{"ts": _ts(), "event": "skeleton_written", "detail": "空输入降级骨架"}]
    result["errors"] = ["输入目录为空或不可读"]
    result["degradations"] = []
    result["runtime"] = {"scaffold_ts": _ts()}
    _write_json(os.path.join(args.output, "result.json"), result)
    _write_plan_draft(args.output, "unknown", {})
    print(json.dumps({"case_id": "unknown", "status": "insufficient_evidence"}, ensure_ascii=False))
    return 0


def _write_plan_draft(out_dir, case_id, ir):
    headings = [
        ("假设与变量", "hypotheses_and_variables"),
        ("对照组", "controls"),
        ("设备材料", "equipment_materials"),
        ("时间成本", "time_cost"),
        ("参数范围", "parameter_ranges"),
        ("风险与异常", "risks_anomalies"),
    ]
    lines = ["# 实验方案（草稿）", "", "case_id: %s" % case_id, ""]
    for zh, _key in headings:
        lines.append("## %s" % zh)
        lines.append("")
        lines.append("_待填写：操作定义、单位、量程、判据、依据（claim_id → 证据台账）。_")
        lines.append("")
    lines.append("## 依据与风险")
    lines.append("")
    lines.append("_待填写：证据台账（locator + 逐字 quote）、风险登记、算术校验。_")
    lines.append("")
    lines.append("## 确认项")
    lines.append("")
    lines.append("_待填写：安全、伦理、设备可用性、专家确认；未确认不得写成事实。_")
    lines.append("")
    path = os.path.join(out_dir, "plan.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def _walk(obj, path="$"):
    for key, val in obj.items() if isinstance(obj, dict) else []:
        p = "%s.%s" % (path, key)
        yield p, key, val
        if isinstance(val, dict):
            yield from _walk(val, p)
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, dict):
                    yield from _walk(item, "%s[%d]" % (p, i))


def _count_evidence(result):
    n = 0
    for _p, _k, v in _walk(result):
        if isinstance(v, dict) and isinstance(v.get("quote"), str) and v.get("quote").strip():
            n += 1
    return n


def _recheck_arithmetic(entry):
    """对 {left, right, relation, expected} 的算术检查做确定性复核。"""
    try:
        if not isinstance(entry, dict):
            return None
        left = entry.get("left")
        right = entry.get("right")
        rel = entry.get("relation", "eq")
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            return None
        if rel in ("eq", "sum_eq", "le", "leq", "<="):
            return abs(left - right) < 1e-9 if rel in ("eq", "sum_eq") else left <= right + 1e-9
        if rel in ("lt", "<"):
            return left < right - 1e-9
        if rel in ("ge", "geq", ">="):
            return left >= right - 1e-9
        return None
    except Exception:  # noqa: BLE001
        return None


def _validate(args):
    result_path = os.path.join(args.output, "result.json")
    ir_path = os.path.join(args.work, "ir.json")
    report = {"checks": [], "errors": 0, "warnings": 0, "usable": True}
    ir = {}
    if os.path.exists(ir_path):
        try:
            ir = json.loads(_read_text(ir_path))
        except Exception:  # noqa: BLE001
            ir = {}

    if not os.path.exists(result_path):
        report["checks"].append({"name": "result.json 存在", "ok": False, "severity": "error", "detail": "缺失"})
        report["errors"] += 1
        report["usable"] = False
        _write_json(os.path.join(args.work, "validation_report.json"), report)
        print(json.dumps(report, ensure_ascii=False))
        return 1

    try:
        result = json.loads(_read_text(result_path))
    except json.JSONDecodeError as e:
        report["checks"].append({"name": "result.json 合法", "ok": False, "severity": "error", "detail": str(e)})
        report["errors"] += 1
        report["usable"] = False
        _write_json(os.path.join(args.work, "validation_report.json"), report)
        print(json.dumps(report, ensure_ascii=False))
        return 1

    def add(name, ok, severity, detail=""):
        report["checks"].append({"name": name, "ok": ok, "severity": severity, "detail": detail})
        if not ok:
            report["errors" if severity == "error" else "warnings"] += 1
            if severity == "error":
                report["usable"] = False

    # 1) 结构
    add("case_id 非空", bool(result.get("case_id")), "error", "case_id=%r" % result.get("case_id"))
    add("status 合法", result.get("status") in VALID_STATUS, "error", "status=%r" % result.get("status"))
    for key in ENVELOPE_LIST_KEYS:
        add("信封 %s 为列表" % key, isinstance(result.get(key), list), "error", "type=%s" % type(result.get(key)).__name__)

    # 2) 契约键存在（缺键为 error，键在但为 null 为 warning=骨架未填）
    contract = ir.get("output_contract")
    if isinstance(contract, dict) and contract:
        for k in contract:
            if k not in result:
                add("契约键存在: %s" % k, False, "error", "缺失")
            elif result.get(k) is None:
                add("契约键已填: %s" % k, False, "warning", "仍为 null，需填写")
            else:
                add("契约键已填: %s" % k, True, "info", "")

    # 3) 六要素完整性
    plan = result.get("plan")
    if isinstance(plan, dict):
        for el in SIX_ELEMENTS:
            v = plan.get(el)
            if el not in plan:
                add("六要素存在: %s" % el, False, "warning", "缺失")
            elif v in (None, "", {}, []):
                add("六要素非空: %s" % el, False, "warning", "为空")
            else:
                add("六要素非空: %s" % el, True, "info", "")
    elif contract and "plan" not in result:
        add("六要素（无 plan 键）", False, "warning", "输出契约未提供 plan 键，六要素需映射到契约字段")

    # 4) 算术复核
    checks = []
    eir = result.get("evidence_and_risks")
    if isinstance(eir, dict):
        checks = eir.get("arithmetic_checks", [])
    elif isinstance(result.get("arithmetic_checks"), list):
        checks = result.get("arithmetic_checks", [])
    if checks:
        for c in checks:
            r = _recheck_arithmetic(c)
            if r is None:
                add("算术复核: %s" % c.get("id", "?"), True, "info", "无可复核数值")
            else:
                add("算术复核: %s" % (c.get("id", "?")), r, "error" if not r else "info",
                    "expected=%s" % c.get("expected"))
    else:
        add("算术校验记录", False, "warning", "未提供 arithmetic_checks；预算/排期/量程须由模型显式校验")

    # 5) 证据
    n = _count_evidence(result)
    if n == 0:
        add("证据溯源（locator+quote）", False, "warning", "未发现带 quote+locator 的证据条目")
    else:
        add("证据溯源（locator+quote）", True, "info", "%d 条" % n)

    # 6) 一致性：plan.md 存在且含 case_id
    plan_md = os.path.join(args.output, "plan.md")
    if os.path.exists(plan_md):
        txt = _read_text(plan_md)
        add("plan.md 存在且含 case_id", str(result.get("case_id", "")) in txt, "warning", "%d bytes" % len(txt))
    else:
        add("plan.md 存在", False, "warning", "缺失主产物 plan.md")

    report["result_case_id"] = result.get("case_id")
    report["result_status"] = result.get("status")
    _write_json(os.path.join(args.work, "validation_report.json"), report)
    print(json.dumps({"usable": report["usable"], "errors": report["errors"], "warnings": report["warnings"],
                      "report": os.path.join(args.work, "validation_report.json")}, ensure_ascii=False))
    return 0 if report["usable"] else 1


def _parse_args(argv):
    class A:
        pass
    a = A()
    a.input = "/input"
    a.output = "/app/output"
    a.work = "/app/work"
    i = 0
    while i < len(argv):
        tok = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if tok == "--input" and nxt:
            a.input = nxt; i += 2
        elif tok == "--output" and nxt:
            a.output = nxt; i += 2
        elif tok == "--work" and nxt:
            a.work = nxt; i += 2
        else:
            i += 1
    return a


def _usage():
    print("usage: main.py {probe|scaffold|validate|all} [--input DIR] [--output DIR] [--work DIR]")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h", "help"):
        _usage()
        return 0
    cmd = argv[0]
    args = _parse_args(argv[1:])
    os.makedirs(args.work, exist_ok=True)
    if cmd == "probe":
        return _probe(args)
    if cmd == "scaffold":
        return _scaffold(args)
    if cmd == "validate":
        return _validate(args)
    if cmd == "all":
        rc = _probe(args)
        if rc == 0:
            rc = _scaffold(args)
        if rc == 0:
            rc = _validate(args)
        return rc
    _usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
