#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""methods-experiments-writing 的确定性基础设施（仅标准库）。

三个阶段子命令：

    python3 scripts/main.py skeleton  --input /input --output /app/output --work /app/work
    python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
    python3 scripts/main.py validate  --input /input --output /app/output --work /app/work

skeleton  立即落盘合法 partial result.json 与 state.json（先于任何分析）。
inventory 扫描 /input，分类材料并抽取候选事实（数值/单位、版本、种子、超参、
          数据集、路径、命令、划分标记），每条带 locator + 逐字 quote。
validate  校验 result.json 的契约字段、产物路径、证据映射与六要素覆盖，
          输出 validation_report.json。
"""
import argparse
import json
import os
import re
import time

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
SIX_ELEMENTS = ("steps", "setup", "params", "data", "baseline", "reproducibility")
MAX_FILE_BYTES = 2 * 1024 * 1024
CONTRACT_FILES = {"case.json", "task.md"}

BINARY_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".zip", ".gz", ".tar", ".npy", ".npz", ".pt", ".pth",
}

# 候选事实规则：inventory 只产出候选，Agent 必须对照材料核实后才采用。
FACT_PATTERNS = [
    (re.compile(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|GB|GiB|MB|MiB|KB|KiB|TB|ms|s|h|min|nm|μm|mm|cm|GHz|MHz|kHz|Hz|W|V|FPS|fps|epochs|layers|heads|dim|steps)\b", re.IGNORECASE), "numeric_unit"),
    (re.compile(r"\b(?:seed|random_state|random_seed|manual_seed)\s*[=:]\s*\d+\b", re.IGNORECASE), "seed"),
    (re.compile(r"\b(?:version|ver)\s*[=:.]?\s*\d+(?:\.\d+){1,3}\b", re.IGNORECASE), "version"),
    (re.compile(r"\bv\d+(?:\.\d+){1,3}\b", re.IGNORECASE), "version"),
    (re.compile(r"\b[a-zA-Z][\w.-]*(?:==|>=|<=|~=)\s*\d+(?:\.\d+){0,3}\b"), "package_version"),
    (re.compile(r"\b(?:lr|learning_rate|learning-rate|batch_size|batch-size|epochs|num_epochs|dropout|drop_rate|weight_decay|weight-decay|optimizer|hidden_size|embedding_dim|num_layers|num_heads|n_layers|n_heads)\s*[=:]\s*[^\s,;)\]]+", re.IGNORECASE), "hyperparam"),
    (re.compile(r"(?:[/\\]|\.{0,2}/)[\w./\\-]+\.(?:py|yaml|yml|json|csv|cfg|ini|toml|sh|md|txt|bib|tex)\b", re.IGNORECASE), "path"),
    (re.compile(r"\b(?:python3?|pip3?|conda|bash|sh|git|docker)\s+[^\n\r]{0,120}"), "command"),
    (re.compile(r"\b(train|validation|val|test|holdout|dev)\b", re.IGNORECASE), "split_marker"),
    (re.compile(r"\b(?:CIFAR-\d{1,3}|ImageNet-?\w*|MNIST|FashionMNIST|COCO|Cityscapes|PASCAL\s+VOC|SQuAD|GLUE|SuperGLUE|LibriSpeech|TIMIT)\b", re.IGNORECASE), "dataset"),
]


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json_tolerant(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def iter_files(input_dir):
    if not os.path.isdir(input_dir):
        return
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            if name.startswith("."):
                continue
            yield os.path.join(root, name)


def rel_path(path, base):
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = path
    return rel.replace("\\", "/")


def classify(path):
    return "binary" if os.path.splitext(path)[1].lower() in BINARY_EXTS else "text"


def locate_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "challengeId"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return "unknown"


def locate_output_contract(case):
    if not isinstance(case, dict):
        return {}
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        return oc
    for v in case.values():
        if isinstance(v, dict):
            oc = v.get("output_contract")
            if isinstance(oc, dict):
                return oc
    return {}


def contract_placeholders(contract):
    out = {}
    for k, v in contract.items():
        if isinstance(v, list):
            out[k] = []
        elif isinstance(v, dict):
            out[k] = {}
        else:
            out[k] = None
    return out


def scan_summary(input_dir):
    summary = {"total": 0, "text": 0, "binary": 0, "oversized": 0}
    for p in iter_files(input_dir):
        if os.path.basename(p) in CONTRACT_FILES:
            continue
        summary["total"] += 1
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        if size > MAX_FILE_BYTES:
            summary["oversized"] += 1
        if classify(p) == "binary":
            summary["binary"] += 1
        else:
            summary["text"] += 1
    return summary


def update_state(work, phase, **extra):
    state_path = os.path.join(work, "state.json")
    state = read_json_tolerant(state_path) if os.path.isfile(state_path) else {}
    if not isinstance(state, dict):
        state = {}
    phases = state.setdefault("phases", [])
    if phase not in phases:
        phases.append(phase)
    state.update(extra)
    state["updated_at"] = now_ts()
    write_json(state_path, state)
    return state


def phase_skeleton(args):
    """落盘合法 partial 结果骨架与状态文件。"""
    case_path = os.path.join(args.input, "case.json")
    task_path = os.path.join(args.input, "task.md")
    case = read_json_tolerant(case_path) if os.path.isfile(case_path) else None
    task_text = read_text(task_path) if os.path.isfile(task_path) else ""

    errors, degradations = [], []
    if case is None:
        status = "input_error"
        errors.append("case.json 缺失或无法解析（/input/case.json）")
        case_id = "unknown"
        contract = {}
    else:
        case_id = locate_case_id(case)
        contract = locate_output_contract(case)
        status = "partial"
        if not contract:
            degradations.append("case.json 未提供 output_contract，使用空契约回填")

    materials = scan_summary(args.input)
    if materials["total"] == 0 and status == "partial":
        status = "insufficient_evidence"

    result = {"case_id": case_id}
    result.update(contract_placeholders(contract))
    result.update({
        "status": status,
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": now_ts(), "phase": "skeleton",
            "summary": "落盘 partial 结果骨架；output_contract 字段以占位空值原样回填",
        }],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "coverage": {e: False for e in SIX_ELEMENTS},
        "runtime": {"seconds": 0.0, "requests": 0},
    })
    if status in ("insufficient_evidence", "input_error"):
        result["limitations"].append({
            "scope": "input",
            "failure_mode": "输入材料不足或契约缺失，无法完成方法与实验章节成稿",
        })

    write_json(os.path.join(args.output, "result.json"), result)
    update_state(args.work, "skeleton", case_id=case_id, status=status,
                 contract_keys=sorted(contract.keys()), materials=materials,
                 task_chars=len(task_text))
    return {"case_id": case_id, "status": status, "contract_fields": len(contract)}


def extract_facts(text, locator):
    found = {}
    for pat, category in FACT_PATTERNS:
        for m in pat.finditer(text):
            value = m.group(0).strip()
            if not value or len(value) > 160:
                continue
            line = text[:m.start()].count("\n") + 1
            s, e = max(0, m.start() - 60), min(len(text), m.end() + 60)
            ctx = " ".join(text[s:e].split())
            key = (locator, category, value)
            if key not in found:
                found[key] = {
                    "category": category,
                    "value": value,
                    "locator": f"{locator}#L{line}",
                    "quote": ctx,
                }
    return sorted(found.values(), key=lambda f: (f["locator"], f["category"], f["value"]))


def phase_inventory(args):
    """扫描材料，分类并抽取候选事实。"""
    materials, facts = [], []
    for p in iter_files(args.input):
        if os.path.basename(p) in CONTRACT_FILES:
            continue
        rel = rel_path(p, args.input)
        kind = classify(p)
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        entry = {"path": rel, "kind": kind, "bytes": size, "status": "ok", "note": ""}
        if size > MAX_FILE_BYTES:
            entry["status"] = "skipped"
            entry["note"] = "超过 2MB 上限，跳过内容抽取"
        elif kind == "binary":
            entry["status"] = "binary"
            entry["note"] = "二进制材料，交给 pdf-text-tool/office-renderer/ocr-tool 处理"
        else:
            try:
                text = read_text(p)
                entry["chars"] = len(text)
                facts.extend(extract_facts(text, rel))
            except Exception as exc:  # noqa: BLE001
                entry["status"] = "error"
                entry["note"] = f"读取失败：{exc}"
        materials.append(entry)

    write_json(os.path.join(args.work, "materials_inventory.json"), {
        "scanned_at": now_ts(),
        "materials": materials,
        "summary": {
            "total": len(materials),
            "text": sum(1 for m in materials if m["kind"] == "text"),
            "binary": sum(1 for m in materials if m["kind"] == "binary"),
            "skipped": sum(1 for m in materials if m["status"] == "skipped"),
        },
    })
    write_json(os.path.join(args.work, "extracted_facts.json"), {
        "extracted_at": now_ts(),
        "candidate_count": len(facts),
        "facts": facts,
    })
    update_state(args.work, "inventory", materials=len(materials), candidate_facts=len(facts))
    return {"materials": len(materials), "candidate_facts": len(facts)}


def phase_validate(args):
    """校验 result.json 契约与产物，输出 validation_report.json。"""
    issues = []
    result_path = os.path.join(args.output, "result.json")
    result = read_json_tolerant(result_path) if os.path.isfile(result_path) else None

    if not isinstance(result, dict):
        issues.append({"severity": "error", "item": "result.json", "msg": "缺失或不可解析"})
    else:
        case_path = os.path.join(args.input, "case.json")
        case = read_json_tolerant(case_path) if os.path.isfile(case_path) else None
        contract = locate_output_contract(case) if isinstance(case, dict) else {}
        for k in contract:
            if k not in result:
                issues.append({"severity": "error", "item": f"output_contract.{k}", "msg": "缺失字段"})
        if not result.get("case_id"):
            issues.append({"severity": "error", "item": "case_id", "msg": "缺失或为空"})
        if result.get("status") not in ALLOWED_STATUS:
            issues.append({"severity": "error", "item": "status", "msg": f"非法取值：{result.get('status')}"})
        for field in ("sources", "uncertainty", "change_log", "pending_human",
                      "errors", "degradations", "limitations", "artifacts"):
            if field not in result:
                issues.append({"severity": "error", "item": field, "msg": "缺失字段"})
        if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
            issues.append({"severity": "warning", "item": "change_log", "msg": "为空，无法回退"})

        cov = result.get("coverage")
        if isinstance(cov, dict):
            for e in SIX_ELEMENTS:
                if not cov.get(e):
                    issues.append({"severity": "warning", "item": f"coverage.{e}", "msg": "六类要素未覆盖"})
        else:
            issues.append({"severity": "warning", "item": "coverage", "msg": "缺失六要素覆盖标记"})

        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            for a in artifacts:
                p = a if os.path.isabs(a) else os.path.join(args.output, a)
                if not os.path.isfile(p):
                    issues.append({"severity": "error", "item": f"artifact:{a}", "msg": "产物路径不存在"})

        if result.get("status") == "ok":
            for f in ("manuscript.md", "evidence_map.json"):
                if not os.path.isfile(os.path.join(args.output, f)):
                    issues.append({"severity": "error", "item": f, "msg": "status=ok 但产物缺失"})

        ev = os.path.join(args.output, "evidence_map.json")
        if os.path.isfile(ev):
            emap = read_json_tolerant(ev)
            if isinstance(emap, dict):
                bad = 0
                for _sid, rec in emap.items():
                    if not isinstance(rec, dict) or not rec.get("locator") or not rec.get("quote"):
                        bad += 1
                if bad:
                    issues.append({"severity": "error", "item": "evidence_map", "msg": f"{bad} 条缺 locator/quote"})

    ok = not any(i["severity"] == "error" for i in issues)
    report = {"ok": ok, "issues": issues, "checked_at": now_ts()}
    write_json(os.path.join(args.work, "validation_report.json"), report)
    update_state(args.work, "validate", ok=ok, issue_count=len(issues))
    return {"ok": ok, "issue_count": len(issues)}


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="phase", required=True)
    helps = {"skeleton": phase_skeleton.__doc__, "inventory": phase_inventory.__doc__,
             "validate": phase_validate.__doc__}
    for name in ("skeleton", "inventory", "validate"):
        sp = sub.add_parser(name, help=helps[name])
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = {"skeleton": phase_skeleton, "inventory": phase_inventory, "validate": phase_validate}[args.phase]
    started = time.time()
    result = fn(args)
    elapsed = round(time.time() - started, 3)
    print(json.dumps({"phase": args.phase, "elapsed_s": elapsed, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
