# -*- coding: utf-8 -*-
"""引言与相关工作写作 Skill —— 核心实现（仅 Python 3.9+ 标准库）。

职责划分（正文写作由 Agent 完成，脚本只做发现 / 骨架 / 校验，保证任何中途异常
都不会留下空产物）：

  scan      发现输入材料、抽取输出契约、规范化 case
  skeleton  基于 work 目录写初始 result.json（诚实 status）与 plan.json 骨架
  validate  校验 result.json / manuscript.md / evidence_map.json / plan.json，
            写 validation_report.json
  selftest  用临时目录合成输入自检（空输入 / 坏 JSON / 坏编码 / 正常案例），
            不生成任何正式答案

设计取向（本版侧重点"中间表示与可复核"）：
  在成稿之前先落 `plan.json` 作为结构化中间表示，把"分组方案、比较维度、空白推导、
  贡献—证据对照"从正文中分离出来；正文、evidence_map、change_log 都回溯到该计划。
"""

import argparse
import json
import os
import sys
import tempfile
import shutil
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB
MAX_DEPTH = 6

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

STANDARD_FIELDS = {
    "status": "partial",
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "change_log": [],
    "errors": [],
    "degradations": [],
    "runtime": {},
    "artifacts": [],
}

MANDATORY_STANDARD = ("case_id", "status", "sources", "uncertainty", "change_log")

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".json", ".jsonl", ".bib", ".csv",
    ".tsv", ".tex", ".latex", ".yaml", ".yml", ".rst", ".xml",
}


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


# --------------------------------------------------------------------------- #
# 文本与 JSON 读写（容错）
# --------------------------------------------------------------------------- #

def read_text_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


def read_text_file(path: str):
    try:
        with open(path, "rb") as f:
            return read_text_bytes(f.read())
    except OSError:
        return None


def read_json(path: str):
    txt = read_text_file(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, ValueError):
        return None


def write_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# --------------------------------------------------------------------------- #
# scan：输入发现
# --------------------------------------------------------------------------- #

def guess_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".json":
        return "json"
    if ext in (".md", ".markdown", ".txt", ".text"):
        return "text"
    if ext == ".bib":
        return "bib"
    if ext in (".csv", ".tsv"):
        return "tabular"
    if ext in (".tex", ".latex"):
        return "latex"
    if ext in (".yaml", ".yml"):
        return "yaml"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        return "image"
    return "unknown"


def discover_inputs(input_dir: str) -> dict:
    manifest = {"input_dir": input_dir, "files": [], "degradations": [], "errors": []}
    if not os.path.isdir(input_dir):
        manifest["errors"].append({"item": "input_dir", "reason": "not a directory"})
        return manifest
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                manifest["errors"].append({"item": rel, "reason": "stat failed: %s" % exc})
                continue
            entry = {"path": rel, "bytes": size, "kind": guess_kind(name), "skipped": False}
            if size > MAX_FILE_BYTES:
                entry["skipped"] = True
                entry["skip_reason"] = "size exceeds 2MB"
                manifest["degradations"].append({"item": rel, "reason": "size exceeds 2MB, skipped"})
            manifest["files"].append(entry)
    return manifest


def _find_key_deep(node, target: str, depth: int = 0):
    if depth > MAX_DEPTH or not isinstance(node, dict):
        return None
    for k, v in node.items():
        if isinstance(k, str) and k.lower() == target.lower():
            return v
    for k, v in node.items():
        if isinstance(v, dict):
            found = _find_key_deep(v, target, depth + 1)
            if found is not None:
                return found
    return None


def contract_template(contract) -> dict:
    """把契约值归一化为 result.json 顶层字段模板（缺省 null / []，保留子结构）。"""
    template = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            template[str(k)] = _value_template(v)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                template[item] = None
            elif isinstance(item, dict):
                for k, v in item.items():
                    template[str(k)] = _value_template(v)
    elif isinstance(contract, str):
        template[contract] = None
    return template


def _value_template(v):
    if isinstance(v, dict):
        return {str(k): _value_template(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return []
    return None


def load_case(input_dir: str) -> dict:
    case_path = os.path.join(input_dir, "case.json")
    info = {"case_id": None, "contract": {}, "contract_template": {},
            "contract_source": None, "errors": [], "data_keys": []}
    if not os.path.isfile(case_path):
        info["errors"].append({"item": "case.json", "reason": "missing"})
        return info
    raw = None
    try:
        with open(case_path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        info["errors"].append({"item": "case.json", "reason": "read failed: %s" % exc})
        return info
    try:
        data = json.loads(read_text_bytes(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        info["errors"].append({"item": "case.json", "reason": "JSON decode failed: %s" % exc})
        return info
    if not isinstance(data, dict):
        info["errors"].append({"item": "case.json", "reason": "top-level is not an object"})
        return info
    info["data_keys"] = sorted(list(data.keys()))
    info["case_id"] = data.get("case_id") or data.get("caseId") or data.get("case") or data.get("id")
    source = None
    for key in ("output_contract", "output", "expected_output", "fields", "required_fields"):
        val = _find_key_deep(data, key)
        if val is not None:
            source = key
            break
    if source is not None:
        info["contract"] = val
        info["contract_template"] = contract_template(val)
        info["contract_source"] = source
    else:
        info["errors"].append({"item": "output_contract", "reason": "not found; contract fields unknown"})
    return info


def run_scan(input_dir: str, work_dir: str) -> int:
    manifest = discover_inputs(input_dir)
    case_info = load_case(input_dir)
    os.makedirs(work_dir, exist_ok=True)
    write_json(os.path.join(work_dir, "input_manifest.json"),
               {"manifest": manifest, "case": case_info})
    write_json(os.path.join(work_dir, "case_normalized.json"), {
        "case_id": case_info["case_id"],
        "contract_fields": list(case_info["contract_template"].keys()),
        "contract_template": case_info["contract_template"],
        "contract_source": case_info["contract_source"],
        "errors": case_info["errors"],
        "degradations": manifest["degradations"],
    })
    return 0


# --------------------------------------------------------------------------- #
# skeleton：早期 partial 骨架（前 3 分钟内必须落盘）
# --------------------------------------------------------------------------- #

def clone_placeholder(v):
    if isinstance(v, dict):
        return {k: clone_placeholder(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return []
    return None


def decide_skeleton_status(manifest: dict, case_info: dict) -> str:
    files = [f for f in manifest.get("files", []) if not f.get("skipped")]
    if not files:
        return "insufficient_evidence"
    case_errors = case_info.get("errors", [])
    if case_errors and case_errors[0].get("item") == "case.json":
        return "input_error"
    if not case_info.get("case_id") and case_info.get("contract_source") is None:
        return "input_error"
    return "partial"


def build_skeleton_result(manifest: dict, case_info: dict) -> dict:
    result = {}
    for k, v in case_info.get("contract_template", {}).items():
        result[k] = clone_placeholder(v)
    result["case_id"] = case_info.get("case_id")
    status = decide_skeleton_status(manifest, case_info)
    result["status"] = status
    std = dict(STANDARD_FIELDS)
    std["status"] = status
    std["errors"] = list(case_info.get("errors", []))
    std["degradations"] = list(manifest.get("degradations", []))
    std["runtime"] = {"started_at": iso_now(), "skeleton_at": iso_now(),
                      "model_requests_budget": 64, "time_budget_seconds": 900,
                      "requests_used": 0}
    conflicts = []
    for k, v in std.items():
        if k in result:
            if k in MANDATORY_STANDARD:
                result[k] = v
                if k in case_info.get("contract_template", {}):
                    conflicts.append(k)
            else:
                conflicts.append(k)
        else:
            result[k] = v
    if conflicts:
        result.setdefault("errors", [])
        result["errors"].append({"item": "field_conflicts",
                                 "reason": "contract fields collide with reserved keys: %s" % sorted(conflicts)})
    return result


def emit_plan_skeleton(output_dir: str, case_id):
    plan = {
        "case_id": case_id,
        "plan_version": "skeleton",
        "updated_at": iso_now(),
        "audience_and_format": {"audience": None, "venue": None, "language": None,
                                "citation_style": None, "length_constraint": None},
        "sections": {
            "background": {"purpose": None, "target_gap": None, "claims": []},
            "related_work": {
                "grouping_scheme": [],
                "comparison_dimensions": ["problem_setting", "method", "data", "evaluation"],
                "works": [],
            },
            "gap": {"derivation": None, "basis": None, "author_judgment": []},
            "contributions": [],
        },
        "evidence_budget": {"facts": [], "citations": []},
        "decision_log": [{"at": iso_now(), "decision": "skeleton created",
                          "reason": "early partial result; plan to be filled incrementally"}],
    }
    write_json(os.path.join(output_dir, "plan.json"), plan)
    return plan


def run_skeleton(work_dir: str, output_dir: str) -> int:
    manifest = read_json(os.path.join(work_dir, "input_manifest.json"))
    case_norm = read_json(os.path.join(work_dir, "case_normalized.json"))
    if manifest is None or case_norm is None:
        # 连扫描都没有完成：写出最小合法 result.json，不让进程崩溃
        result = dict(STANDARD_FIELDS)
        result["case_id"] = None
        result["status"] = "input_error"
        result["errors"] = [{"item": "work", "reason": "scan outputs missing; run scan first"}]
        result["runtime"] = {"started_at": iso_now(), "skeleton_at": iso_now()}
        os.makedirs(output_dir, exist_ok=True)
        write_json(os.path.join(output_dir, "result.json"), result)
        return 0
    mf = manifest.get("manifest", {})
    case_info = {
        "case_id": case_norm.get("case_id"),
        "contract_template": case_norm.get("contract_template", {}),
        "contract_source": case_norm.get("contract_source"),
        "errors": case_norm.get("errors", []),
    }
    result = build_skeleton_result(mf, case_info)
    os.makedirs(output_dir, exist_ok=True)
    write_json(os.path.join(output_dir, "result.json"), result)
    emit_plan_skeleton(output_dir, case_norm.get("case_id"))
    return 0


# --------------------------------------------------------------------------- #
# validate：校验与封存
# --------------------------------------------------------------------------- #

def run_validate(work_dir: str, output_dir: str) -> int:
    report = {"ok": True, "checks": [], "errors": [], "at": iso_now()}
    result = read_json(os.path.join(output_dir, "result.json"))
    if result is None:
        report["ok"] = False
        report["errors"].append({"check": "result.json", "detail": "missing or invalid JSON"})
        write_json(os.path.join(work_dir, "validation_report.json"), report)
        return 1
    report["checks"].append({"check": "result.json", "status": "pass", "detail": "valid JSON"})
    if not isinstance(result, dict):
        report["ok"] = False
        report["errors"].append({"check": "result.json", "detail": "not an object"})
    for key in MANDATORY_STANDARD:
        if key not in result:
            report["ok"] = False
            report["errors"].append({"check": "result.json.%s" % key, "detail": "missing"})
    if result.get("status") not in STATUS_VALUES:
        report["ok"] = False
        report["errors"].append({"check": "result.json.status",
                                 "detail": "invalid value %r" % result.get("status")})
    for i, s in enumerate(result.get("sources", [])):
        if not isinstance(s, dict):
            report["ok"] = False
            report["errors"].append({"check": "sources[%d]" % i, "detail": "not an object"})
            continue
        for f in ("key", "locator", "quote", "kind"):
            if f not in s:
                report["ok"] = False
                report["errors"].append({"check": "sources[%d].%s" % (i, f), "detail": "missing"})
    for i, u in enumerate(result.get("uncertainty", [])):
        if not isinstance(u, dict) or "item" not in u or "reason" not in u:
            report["ok"] = False
            report["errors"].append({"check": "uncertainty[%d]" % i, "detail": "needs item+reason"})
    for i, c in enumerate(result.get("change_log", [])):
        if not isinstance(c, dict) or "at" not in c or "target" not in c:
            report["ok"] = False
            report["errors"].append({"check": "change_log[%d]" % i, "detail": "needs at+target"})

    man_path = os.path.join(output_dir, "manuscript.md")
    man_txt = read_text_file(man_path)
    if man_txt is None:
        report["ok"] = False
        report["errors"].append({"check": "manuscript.md", "detail": "missing"})
    elif not man_txt.strip():
        report["ok"] = False
        report["errors"].append({"check": "manuscript.md", "detail": "empty"})
    else:
        report["checks"].append({"check": "manuscript.md", "status": "pass",
                                 "detail": "%d chars" % len(man_txt)})

    em = read_json(os.path.join(output_dir, "evidence_map.json"))
    if em is None:
        report["ok"] = False
        report["errors"].append({"check": "evidence_map.json", "detail": "missing or invalid JSON"})
    elif isinstance(em, dict):
        bad = 0
        for k, v in em.items():
            if not (isinstance(v, dict) and "locator" in v and "quote" in v):
                bad += 1
        if bad:
            report["ok"] = False
            report["errors"].append({"check": "evidence_map.json", "detail": "%d entries lack locator/quote" % bad})
        else:
            report["checks"].append({"check": "evidence_map.json", "status": "pass",
                                     "detail": "%d entries" % len(em)})
    else:
        report["ok"] = False
        report["errors"].append({"check": "evidence_map.json", "detail": "not an object"})

    plan = read_json(os.path.join(output_dir, "plan.json"))
    if plan is None:
        report["ok"] = False
        report["errors"].append({"check": "plan.json", "detail": "missing or invalid JSON"})
    else:
        report["checks"].append({"check": "plan.json", "status": "pass", "detail": "valid JSON"})
        # 语义自检：分组方案、空白依据、贡献证据（只在已填写时检查，骨架阶段不误报）
        if isinstance(plan, dict):
            sec = plan.get("sections", {}) if isinstance(plan.get("sections"), dict) else {}
            gs = sec.get("related_work", {}).get("grouping_scheme", []) if isinstance(sec.get("related_work"), dict) else []
            for i, g in enumerate(gs):
                if isinstance(g, dict):
                    missing = [f for f in ("criterion", "works", "comparison") if f not in g]
                    if missing:
                        report["ok"] = False
                        report["errors"].append({"check": "plan.json.grouping_scheme[%d]" % i,
                                                 "detail": "missing %s" % missing})
            gap = sec.get("gap", {}) if isinstance(sec.get("gap"), dict) else {}
            if gap.get("derivation") and not gap.get("basis"):
                report["ok"] = False
                report["errors"].append({"check": "plan.json.gap.basis", "detail": "derivation set but basis empty"})
            contribs = sec.get("contributions", []) if isinstance(sec.get("contributions"), list) else []
            for i, c in enumerate(contribs):
                if isinstance(c, dict) and "claim" in c and "evidence" not in c:
                    report["ok"] = False
                    report["errors"].append({"check": "plan.json.contributions[%d]" % i,
                                             "detail": "claim set but evidence missing"})

    if report["ok"]:
        report["checks"].append({"check": "overall", "status": "pass",
                                 "detail": "all required artifacts present and parseable"})
    write_json(os.path.join(work_dir, "validation_report.json"), report)
    return 0 if report["ok"] else 1


# --------------------------------------------------------------------------- #
# selftest：合成输入自检（不生成正式答案）
# --------------------------------------------------------------------------- #

def _write_case(dirpath, filename, content):
    os.makedirs(dirpath, exist_ok=True)
    full = os.path.join(dirpath, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def selftest_main() -> int:
    results = []
    tmp = tempfile.mkdtemp(prefix="sci_intro_selftest_")
    try:
        # 1) 空输入目录
        empty = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(empty, "input"), exist_ok=True)
        w1 = os.path.join(tmp, "w1"); o1 = os.path.join(tmp, "o1")
        run_scan(os.path.join(empty, "input"), w1)
        run_skeleton(w1, o1)
        r1 = read_json(os.path.join(o1, "result.json"))
        results.append(("empty_input_status", r1 and r1.get("status") == "insufficient_evidence"))

        # 2) 坏 JSON
        bad = os.path.join(tmp, "badjson")
        os.makedirs(os.path.join(bad, "input"), exist_ok=True)
        _write_case(os.path.join(bad, "input"), "case.json", "{not valid json")
        _write_case(os.path.join(bad, "input"), "task.md", "# task\n")
        w2 = os.path.join(tmp, "w2"); o2 = os.path.join(tmp, "o2")
        run_scan(os.path.join(bad, "input"), w2)
        run_skeleton(w2, o2)
        r2 = read_json(os.path.join(o2, "result.json"))
        results.append(("bad_json_status", r2 and r2.get("status") == "input_error"))

        # 3) 坏编码（非法 UTF-8 字节）与超限文件
        badenc = os.path.join(tmp, "badenc")
        os.makedirs(os.path.join(badenc, "input"), exist_ok=True)
        _write_case(os.path.join(badenc, "input"), "case.json",
                    json.dumps({"case_id": "demo-002", "output_contract": {"introduction": None}}))
        with open(os.path.join(badenc, "input", "notes.md"), "wb") as f:
            f.write(b"title\n\xff\xfe broken bytes\n")
        with open(os.path.join(badenc, "input", "big.md"), "wb") as f:
            f.write(b"x" * (MAX_FILE_BYTES + 1))
        w3 = os.path.join(tmp, "w3"); o3 = os.path.join(tmp, "o3")
        run_scan(os.path.join(badenc, "input"), w3)
        mf = read_json(os.path.join(w3, "input_manifest.json"))
        degraded = any(f.get("skipped") for f in mf["manifest"]["files"]) if mf else False
        results.append(("oversize_skipped", degraded))
        run_skeleton(w3, o3)
        r3 = read_json(os.path.join(o3, "result.json"))
        results.append(("bad_encoding_continues", r3 is not None and r3.get("status") == "partial"))

        # 4) 正常案例：契约字段回填 + plan 骨架
        norm = os.path.join(tmp, "norm")
        os.makedirs(os.path.join(norm, "input"), exist_ok=True)
        _write_case(os.path.join(norm, "input"), "case.json", json.dumps({
            "case_id": "demo-003",
            "output_contract": {
                "introduction": None,
                "related_work": {"groups": [], "citations": []},
                "references": [],
            },
        }))
        _write_case(os.path.join(norm, "input"), "task.md", "# 引言写作\n")
        _write_case(os.path.join(norm, "input"), "materials/paper.md",
                    "# Paper\n\n## Abstract\nA system reports 91.2% accuracy on test.\n")
        w4 = os.path.join(tmp, "w4"); o4 = os.path.join(tmp, "o4")
        run_scan(os.path.join(norm, "input"), w4)
        run_skeleton(w4, o4)
        r4 = read_json(os.path.join(o4, "result.json"))
        p4 = read_json(os.path.join(o4, "plan.json"))
        contract_ok = (r4.get("introduction") is None
                       and isinstance(r4.get("related_work"), dict)
                       and "groups" in r4.get("related_work", {})
                       and isinstance(r4.get("references"), list)
                       and r4.get("status") == "partial")
        results.append(("contract_backfill", contract_ok))
        results.append(("plan_skeleton", p4 is not None and "sections" in p4))

        # 5) validate 缺 manuscript 应诚实报错且不崩溃
        rc = run_validate(w4, o4)
        rep = read_json(os.path.join(w4, "validation_report.json"))
        results.append(("validate_reports_missing_manuscript",
                        rep is not None and rep.get("ok") is False and rc == 1))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    all_ok = True
    print("selftest results (synthetic fixtures; not formal case outputs):")
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        all_ok = all_ok and ok
    print("overall:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="引言与相关工作写作 Skill 的发现/骨架/校验工具（纯标准库）")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="扫描输入、抽取契约、规范化 case")
    ps.add_argument("--input", default=DEFAULT_INPUT)
    ps.add_argument("--work", default=DEFAULT_WORK)

    pk = sub.add_parser("skeleton", help="落盘早期 partial result.json 与 plan.json 骨架")
    pk.add_argument("--work", default=DEFAULT_WORK)
    pk.add_argument("--output", default=DEFAULT_OUTPUT)

    pv = sub.add_parser("validate", help="校验产物并写 validation_report.json")
    pv.add_argument("--work", default=DEFAULT_WORK)
    pv.add_argument("--output", default=DEFAULT_OUTPUT)

    sub.add_parser("selftest", help="合成输入自检")
    return p


def cli(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "scan":
        return run_scan(args.input, args.work)
    if args.cmd == "skeleton":
        return run_skeleton(args.work, args.output)
    if args.cmd == "validate":
        return run_validate(args.work, args.output)
    if args.cmd == "selftest":
        return selftest_main()
    return 2
