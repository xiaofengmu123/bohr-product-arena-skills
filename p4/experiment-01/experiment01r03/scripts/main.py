#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scix-experiment-plan-v2 确定性流水线：输入发现、结果骨架落盘、自检。

设计目标（本变体的主线）：**同输入两次运行，产物逐字节一致**。
实现手段：
- 所有 JSON 一律 `json.dumps(sort_keys=True, ensure_ascii=False)` 序列化，与
  dict 插入顺序无关；
- 不使用墙钟时间：`change_log` 的时间戳用逻辑步号 `L0001`、`L0002`…；
- 顶层写 `input_fingerprint`（输入目录内容 SHA-256），可证明“同一输入”；
- 文件写盘走 `.tmp + os.replace`，保证失败时旧文件不被破坏、成功时原子落盘。

仅依赖 Python 标准库。所有结论必须来自 /input 真实材料，本脚本不生成任何
科学结论，只做发现、骨架与结构/类型/确定性校验。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

SKILL_NAME = "scix-experiment-plan-v2"
SKILL_VERSION = "2.0.0"

# 题目六类要素（任务完成度 / 完整性评分维度）。
SIX_ELEMENTS = [
    "hypothesis_and_variables",
    "controls",
    "equipment_and_materials",
    "time_and_cost",
    "parameter_ranges",
    "risks_and_anomalies",
]

# 无 output_contract 时的回退主结构；有契约时以契约键为准，逐键原样回填。
FALLBACK_CONTRACT = {
    "plan": {
        "hypothesis_and_variables": None,
        "controls": None,
        "equipment_and_materials": None,
        "time_and_cost": None,
        "parameter_ranges": None,
        "risks_and_anomalies": None,
        "steps": [],
        "readout_criteria": None,
    },
    "evidence_and_risks": {
        "evidence_map": [],
        "risks": [],
        "assumptions": [],
    },
    "confirmations": {
        "safety": [],
        "ethics": [],
        "equipment_availability": [],
        "expert_review": [],
    },
}

_CHANGE_LOG_TS = re.compile(r"^L\d{4}$")


def canonical_json(obj):
    """确定性 JSON 序列化：sort_keys + 统一缩进 + 单尾换行。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def atomic_write_text(path, text):
    """原子写 UTF-8 文本：先写 .tmp 再 os.replace，LF 换行。"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json_utf8(path):
    """utf-8-sig 容错读 JSON；失败返回 (None, err)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001 - 必须吞掉并降级，不崩溃
        return None, "{}: {}".format(type(exc).__name__, exc)


def read_text_utf8(path):
    """utf-8-sig 容错读文本。"""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def input_fingerprint(input_dir):
    """按遍历顺序确定性地散列输入目录内容；同一输入必然得到同一指纹。"""
    hasher = hashlib.sha256()
    if not os.path.isdir(input_dir):
        hasher.update(b"<no-input-dir>")
        return hasher.hexdigest()
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(root, name), input_dir).replace("\\", "/")
            hasher.update(rel.encode("utf-8", "replace"))
            hasher.update(b"\x00")
            try:
                with open(os.path.join(root, name), "rb") as fh:
                    hasher.update(fh.read())
            except OSError:
                hasher.update(b"<unreadable>")
            hasher.update(b"\x00")
    return hasher.hexdigest()


def list_input_files(input_dir):
    """扫描输入目录，跳过超大文件并记录降级原因。"""
    files = []
    degradations = []
    if not os.path.isdir(input_dir):
        degradations.append("输入目录不存在或不可读: {}".format(input_dir))
        return files, degradations
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                degradations.append("无法读取 {}: {}".format(rel, exc))
                continue
            if size > MAX_FILE_BYTES:
                files.append({"path": rel, "size": size, "skipped": True,
                              "reason": ">2MB, 跳过正文抽取"})
                degradations.append("跳过超大文件 {} ({} bytes)".format(rel, size))
                continue
            files.append({"path": rel, "size": size, "skipped": False, "reason": None})
    return files, degradations


def classify_file(path):
    lower = path.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".md") or lower.endswith(".txt"):
        return "text"
    if lower.endswith((".docx", ".xlsx", ".pptx")):
        return "office"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    return "other"


def extract_pdf_text(path):
    """用 pdftotext 抽 PDF 正文；缺工具/失败返回 (None, err)。"""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except FileNotFoundError:
        return None, "pdftotext 不可用"
    except subprocess.TimeoutExpired:
        return None, "pdftotext 超时"
    except OSError as exc:
        return None, "pdftotext 调用失败: {}".format(exc)
    if proc.returncode != 0:
        return None, "pdftotext 退出码 {}: {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "replace")[:300])
    return proc.stdout.decode("utf-8", "replace"), None


def find_output_contract(obj):
    """在任意嵌套 JSON 中定位名为 output_contract 的 dict/list。"""
    if isinstance(obj, dict):
        if "output_contract" in obj and isinstance(obj["output_contract"], dict):
            return obj["output_contract"]
        for value in obj.values():
            found = find_output_contract(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_output_contract(value)
            if found is not None:
                return found
    return None


def blank_from_contract(contract):
    """把契约模板变成同键占位结构：dict 递归、list→[]、标量→None。"""
    if isinstance(contract, dict):
        out = {}
        for key, value in contract.items():
            if isinstance(value, dict):
                out[key] = blank_from_contract(value)
            elif isinstance(value, list):
                out[key] = []
            else:
                out[key] = None
        return out
    return {}


def extract_case_id(case_json, task_text):
    """解析 case_id：case.json 显式字段 → task 文本 challenge_id → unknown。"""
    if isinstance(case_json, dict):
        for key in ("case_id", "challenge_id"):
            value = case_json.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if task_text:
        match = re.search(
            r"challenge[-_ ]?id\s*[:：]?\s*[`\"']?([A-Za-z0-9][A-Za-z0-9_.-]*)",
            task_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return "unknown"


def run_probe(input_dir, work_dir):
    """第 1 步：发现输入，写 work/input_probe.json。"""
    files, degradations = list_input_files(input_dir)
    case_path = None
    task_path = None
    for item in files:
        if item["skipped"]:
            continue
        base = os.path.basename(item["path"]).lower()
        if base == "case.json" and case_path is None:
            case_path = item["path"]
        elif base == "task.md" and task_path is None:
            task_path = item["path"]

    case_json = None
    task_text = None
    errors = []
    if case_path:
        case_json, err = read_json_utf8(os.path.join(input_dir, case_path))
        if err:
            errors.append("case.json 解析失败: {}".format(err))
    if task_path:
        task_text = read_text_utf8(os.path.join(input_dir, task_path))

    contract = find_output_contract(case_json) if case_json is not None else None
    contract_source = "case.output_contract" if contract is not None else "fallback"
    case_id = extract_case_id(case_json, task_text)

    pdfs = []
    for item in files:
        if item["skipped"] or classify_file(item["path"]) != "pdf":
            continue
        text, err = extract_pdf_text(os.path.join(input_dir, item["path"]))
        entry = {"path": item["path"], "text": text, "error": err}
        pdfs.append(entry)
        if err:
            degradations.append("PDF {} 正文提取失败: {}".format(item["path"], err))

    probe = {
        "case_id": case_id,
        "output_contract": contract,
        "contract_source": contract_source,
        "files": files,
        "pdfs": pdfs,
        "errors": errors,
        "degradations": degradations,
        "input_fingerprint": input_fingerprint(input_dir),
    }
    atomic_write_text(os.path.join(work_dir, "input_probe.json"), canonical_json(probe))
    return probe


def _initial_status(probe):
    """按材料可用性给出诚实初始状态：无任何可用材料 → insufficient_evidence。"""
    files = probe.get("files", []) if probe else []
    if not any(not item.get("skipped") for item in files):
        return "insufficient_evidence"
    return "partial"


def run_scaffold(input_dir, output_dir, work_dir, probe=None):
    """第 2 步：先落盘一版合法 partial 骨架（前 3 分钟内必须完成）。"""
    if probe is None:
        probe = run_probe(input_dir, work_dir)
    contract = probe.get("output_contract")
    if isinstance(contract, dict) and contract:
        payload = blank_from_contract(contract)
        contract_source = "case.output_contract"
    else:
        payload = blank_from_contract(FALLBACK_CONTRACT)
        contract_source = "fallback"

    result = {
        "case_id": probe.get("case_id", "unknown"),
    }
    result.update(payload)
    result.update({
        "status": _initial_status(probe),
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": "L0001",
            "event": "skeleton_written",
            "detail": "partial 骨架已原子落盘；后续仅做增量改进",
        }],
        "errors": list(probe.get("errors", [])),
        "degradations": list(probe.get("degradations", [])),
        "determinism": {
            "policy": "sort_keys + logical_clock + sha256_fingerprint + atomic_write",
            "input_fingerprint": probe.get("input_fingerprint", ""),
            "contract_source": contract_source,
        },
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
    })
    atomic_write_text(os.path.join(output_dir, "result.json"), canonical_json(result))
    atomic_write_text(os.path.join(output_dir, "plan.md"),
                      render_plan_draft(probe.get("case_id", "unknown"), contract_source))
    return result


def render_plan_draft(case_id, contract_source):
    """plan.md 草稿：六要素占位 + 诚实性提示，等 Agent 增量填写。"""
    lines = [
        "# 实验方案（草稿，待依据 /input 材料填写）",
        "",
        "- case_id: `{}`".format(case_id),
        "- 契约来源: `{}`".format(contract_source),
        "- 状态: `partial`（骨架）",
        "",
        "> 本文件是骨架。任何数值、型号、价格、排期都必须在 /input 材料中有",
        "> 逐字依据；没有依据的一律写 `not_reported` 或列入“确认项”，不得编造。",
        "",
        "## 假设与变量",
        "_待填写：可检验假设；自变量/因变量/混杂因素的操作定义、单位、量程、测量方式。_",
        "",
        "## 对照组",
        "_待填写：空白/阳性/阴性/剂量梯度等对照；每个对照排除哪类混杂。_",
        "",
        "## 设备材料",
        "_待填写：步骤绑定的设备型号/量程、耗材规格、试剂；可替代与不可替代项。_",
        "",
        "## 时间成本",
        "_待填写：步骤时长、并行关系、样本量、预算与排期算术。_",
        "",
        "## 参数范围",
        "_待填写：关键参数、单位、允许区间、越界处置；依据来自手册/预实验/文献。_",
        "",
        "## 风险与异常",
        "_待填写：失败模式、判据、止损点、替代路径。_",
        "",
        "## 依据与风险",
        "_待填写：每条依据带 locator + 逐字 quote；冲突信息两处都记并标 source_conflict。_",
        "",
        "## 确认项",
        "_待填写：安全、伦理、设备可用性、专家确认；未确认不得写成事实。_",
        "",
    ]
    return "\n".join(lines)


def _contract_top_keys(probe):
    """返回 result.json 顶层必须具备的契约键。"""
    contract = probe.get("output_contract") if probe else None
    if isinstance(contract, dict) and contract:
        return list(contract.keys())
    return list(FALLBACK_CONTRACT.keys())


def _has_min_max_violation(obj, path=""):
    """递归检查数值 min/max 颠倒的参数范围（越界即为可复核性错误）。"""
    violations = []
    if isinstance(obj, dict):
        low = obj.get("min")
        high = obj.get("max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low > high:
            violations.append("{} 参数范围 min({}) > max({})".format(path or "$", low, high))
        for key, value in obj.items():
            violations.extend(_has_min_max_violation(value, path + "." + str(key)))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            violations.extend(_has_min_max_violation(value, path + "[{}]".format(idx)))
    return violations


def run_validate(input_dir, output_dir, work_dir):
    """第 5/6 步：结构、类型、六要素、证据、状态诚实性、确定性自检。"""
    probe = None
    probe_path = os.path.join(work_dir, "input_probe.json")
    if os.path.isfile(probe_path):
        probe, _ = read_json_utf8(probe_path)
    if probe is None:
        probe = run_probe(input_dir, work_dir)

    result_path = os.path.join(output_dir, "result.json")
    errors, warnings = [], []
    result = None
    if not os.path.isfile(result_path):
        errors.append("result.json 不存在: {}".format(result_path))
    else:
        result, err = read_json_utf8(result_path)
        if err or not isinstance(result, dict):
            errors.append("result.json 解析失败或非对象: {}".format(err))

    if result is not None:
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append("case_id 缺失或为空")

        status = result.get("status")
        if status not in ALLOWED_STATUS:
            errors.append("status 非法: {!r}（允许 {}）".format(status, sorted(ALLOWED_STATUS)))

        missing_top = [k for k in _contract_top_keys(probe) if k not in result]
        if missing_top:
            errors.append("输出契约顶层键缺失: {}".format(missing_top))

        plan = result.get("plan")
        if isinstance(plan, dict):
            empty_elems = [k for k in SIX_ELEMENTS
                           if k not in plan or plan.get(k) in (None, "", [], {})]
            if empty_elems:
                warnings.append("六要素未填全（允许 partial，但完整性扣分）: {}".format(empty_elems))
        else:
            warnings.append("plan 缺失或非对象（若契约自定义了主结构，请人工核对六要素是否内嵌）")

        evidence = None
        if isinstance(result.get("evidence_and_risks"), dict):
            evidence = result["evidence_and_risks"].get("evidence_map")
        if isinstance(evidence, list):
            for idx, item in enumerate(evidence):
                if not isinstance(item, dict):
                    warnings.append("evidence_map[{}] 非对象".format(idx))
                    continue
                if not isinstance(item.get("locator"), str) or not item["locator"].strip():
                    warnings.append("evidence_map[{}] 缺 locator".format(idx))
                if not isinstance(item.get("quote"), str) or not item["quote"].strip():
                    warnings.append("evidence_map[{}] 缺逐字 quote".format(idx))

        for violation in _has_min_max_violation(result):
            warnings.append(violation)

        change_log = result.get("change_log")
        if not isinstance(change_log, list) or not change_log:
            errors.append("change_log 缺失或为空")
        else:
            ts_list = []
            for entry in change_log:
                if not isinstance(entry, dict) or not _CHANGE_LOG_TS.match(str(entry.get("ts", ""))):
                    warnings.append("change_log 存在非逻辑时间戳（应为 Lnnnn，禁止墙钟）")
                    break
                ts_list.append(entry["ts"])
            if ts_list != sorted(ts_list):
                warnings.append("change_log 时间戳非递增")

        if status == "ok" and not evidence and warnings:
            warnings.append("status=ok 但存在缺口，请确认是否应为 partial/insufficient_evidence")
        if status == "insufficient_evidence" and not any(
                w.startswith("六要素未填全") for w in warnings):
            warnings.append("status=insufficient_evidence 但六要素已填，状态与内容不一致")

    report = {"errors": errors, "warnings": warnings, "exit_code": 1 if errors else 0}
    atomic_write_text(os.path.join(work_dir, "validation_report.json"),
                      canonical_json(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--stage", default="all",
                        choices=["probe", "scaffold", "validate", "all"])
    args = parser.parse_args(argv)

    try:
        if args.stage in ("probe", "all"):
            probe = run_probe(args.input, args.work)
        else:
            probe = None
        if args.stage in ("scaffold", "all"):
            run_scaffold(args.input, args.output, args.work, probe=probe)
        if args.stage in ("validate", "all"):
            report = run_validate(args.input, args.output, args.work)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return report["exit_code"]
        return 0
    except Exception as exc:  # noqa: BLE001 - 写盘失败也要给最小合法产物，不崩溃
        try:
            minimal = {
                "case_id": "unknown",
                "status": "input_error",
                "errors": ["{}: {}".format(type(exc).__name__, exc)],
                "degradations": [],
                "change_log": [{"ts": "L0001", "event": "unhandled_error",
                                "detail": "降级为最小合法结果"}],
                "determinism": {"policy": "sort_keys + logical_clock + sha256_fingerprint",
                                "input_fingerprint": ""},
                "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
            }
            atomic_write_text(os.path.join(args.output, "result.json"), canonical_json(minimal))
        except OSError:
            pass
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
