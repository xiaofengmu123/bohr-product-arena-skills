#!/usr/bin/env python3
"""科研成果转化写作 Skill 编排器（仅标准库）。

先落骨架、后增量：任何阶段中断都不留下不完整 result.json。

用法：
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --mode skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --mode finalize --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract as EX

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".tex"}
PDF_SUFFIXES = {".pdf"}

REQUIRED_FIELDS = ["case_id", "status", "deliverable", "evidence_map",
                   "terminology", "change_log", "sources", "uncertainty",
                   "pending_human", "errors", "degradations", "limitations",
                   "runtime"]

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


# ---------------------------------------------------------------- 发现
def discover(input_dir):
    root = Path(input_dir)
    res = {"case_json": None, "task_md": None, "materials": [], "errors": []}
    if not root.exists():
        res["errors"].append("input dir not found: %s" % input_dir)
        return res
    try:
        entries = sorted(root.rglob("*"))
    except OSError as e:
        res["errors"].append("cannot scan input: %s" % e)
        return res
    for p in entries:
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "case.json":
            res["case_json"] = p
        elif name == "task.md":
            res["task_md"] = p
        elif p.suffix.lower() in PDF_SUFFIXES or p.suffix.lower() in TEXT_SUFFIXES:
            res["materials"].append(p)
    return res


# ---------------------------------------------------------------- 读取
def parse_json_lenient(text):
    try:
        return json.loads(text), []
    except json.JSONDecodeError as e:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0)), ["json_recovered: %s" % e]
            except Exception:
                pass
        return None, ["json_error: %s" % e]


def get_case_id(data, input_dir):
    if isinstance(data, dict):
        for k in ("case_id", "id", "challenge_id", "caseId"):
            v = data.get(k)
            if v:
                return str(v)
    return Path(input_dir).name or "unknown"


def get_contract(data):
    if isinstance(data, dict):
        oc = data.get("output_contract")
        if isinstance(oc, dict):
            return dict(oc)
        if isinstance(oc, list):
            return {str(k): None for k in oc}
    return {}


def echo_contract(result, contract):
    for k in contract:
        if k not in result:
            result[k] = None
    return result


def try_pdftotext(pdf_path, work_dir):
    out = Path(work_dir) / (Path(pdf_path).stem + ".pdf.txt")
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(out)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
        if r.returncode == 0 and out.exists():
            txt = out.read_text(encoding="utf-8", errors="replace")
            if txt.strip():
                return txt
    except Exception:
        pass
    return None


def read_material(path, work_dir):
    """返回 (text, name, notes)；text 为 None 表示不可用。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        txt = EX.read_text(p)
        if txt is None:
            return None, p.name, ["oversize_or_unreadable"]
        return txt, p.name, []
    if suffix in PDF_SUFFIXES:
        txt = try_pdftotext(p, work_dir)
        if txt:
            return txt, p.name, ["pdf->text"]
        return None, p.name, ["pdf_parse_failed"]
    return None, p.name, ["skipped"]


# ---------------------------------------------------------------- 骨架
DEFAULT_TEMPLATE = [
    ("# 成果转化改稿", "（本文件是骨架；请按 references/genre-mapping.md 的体裁结构补齐正文）"),
    ("## 一句话结论", "（待补：直接可用的一句话，含关键数据与限定）"),
    ("## 目标读者与体裁", "（读者背景、阅读目的、篇幅上限、文体）"),
    ("## 正文", "（按体裁结构改写；保留关键结论/数据/引用/限定，删冗余不删证据）"),
    ("## 术语说明", "（缩写首次展开；同义表达收敛为唯一说法）"),
    ("## 边界与不确定性", "（源文未支持/冲突/待人工确认项）"),
    ("## 来源与改动记录", "（每条删改 before/after/reason；关键数字回指源文）"),
]


def build_skeleton(case_id, case_data):
    lines = ["# 成果转化改稿", ""]
    if case_id:
        lines.append("case_id: `%s`" % case_id)
    lines.append("")
    audience = ""
    if isinstance(case_data, dict):
        for k in ("audience", "target_audience", "读者", "受众"):
            if case_data.get(k):
                audience = str(case_data[k])
                break
    lines.append("目标读者：%s" % (audience or "（见 case.json/task.md）"))
    lines.append("")
    for heading, body in DEFAULT_TEMPLATE[2:]:
        lines.append(heading)
        lines.append("")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 汇总
def build_terminology(evidence):
    terms = {}
    for ev in evidence:
        if ev.get("type") != "term":
            continue
        key = ev.get("abbr", "").strip().lower()
        if not key:
            continue
        if key not in terms:
            terms[key] = {"canonical": ev.get("abbr"), "abbr": ev.get("abbr"),
                          "expansion": ev.get("expansion"), "variants": [],
                          "first_use": ev.get("locator")}
        else:
            exp = ev.get("expansion")
            if exp and exp not in terms[key]["variants"]:
                terms[key]["variants"].append(exp)
    return [v for v in terms.values()]


_NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def check_consistency(deliverable_text, source_texts):
    """找出改稿中在源文找不到的、看起来像科学数据的数字。"""
    src = " ".join(t for _, t in source_texts)
    issues = []
    for m in _NUM_TOKEN.finditer(deliverable_text):
        num = m.group(0)
        if num not in src and (len(num) >= 3 or "." in num):
            issues.append(num)
    return sorted(set(issues))


# ---------------------------------------------------------------- 主流程
def compute_initial_status(case_data, has_case_file, has_materials):
    if not has_case_file and not has_materials:
        return "insufficient_evidence"
    if case_data is None and has_case_file:
        return "input_error"
    return "partial"


def main():
    ap = argparse.ArgumentParser(description="科研成果转化写作编排器")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--mode", choices=["skeleton", "finalize"], default="skeleton")
    ap.add_argument("--case-id", default=None)
    args = ap.parse_args()

    t0 = time.time()
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(json.dumps({"status": "input_error", "error": "cannot mkdir: %s" % e},
                         ensure_ascii=False))
        sys.exit(1)

    disc = discover(args.input)
    errors = list(disc["errors"])
    degradations = []

    case_data = None
    if disc["case_json"]:
        txt = EX.read_text(disc["case_json"])
        if txt is None:
            errors.append("case.json unreadable or oversize")
        else:
            case_data, cerr = parse_json_lenient(txt)
            errors.extend(cerr)
    else:
        errors.append("case.json missing")

    case_id = args.case_id or get_case_id(case_data, args.input)
    contract = get_contract(case_data)

    # ---- Phase A：先落骨架（必须在重活之前写出合法 result.json）----
    initial_status = compute_initial_status(
        case_data,
        has_case_file=disc["case_json"] is not None,
        has_materials=bool(disc["materials"]),
    )
    skeleton_text = build_skeleton(case_id, case_data)
    deliv_path = out_dir / "deliverable.md"
    # 只有 skeleton 模式，或 finalize 时改稿尚不存在/为空，才写骨架；
    # finalize 不得覆盖模型已写好的 deliverable.md。
    need_skeleton = (args.mode == "skeleton"
                     or not deliv_path.exists()
                     or deliv_path.stat().st_size == 0)
    if need_skeleton:
        try:
            deliv_path.write_text(skeleton_text, encoding="utf-8")
        except OSError as e:
            errors.append("cannot write deliverable skeleton: %s" % e)

    result = echo_contract({
        "case_id": case_id,
        "status": initial_status,
        "deliverable": "deliverable.md",
        "artifacts": ["deliverable.md"],
        "evidence_map": [],
        "terminology": [],
        "change_log": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": list(degradations),
        "limitations": [],
        "runtime": {"mode": args.mode, "seconds": 0.0},
    }, contract)
    write_result(result, out_dir)

    # ---- Phase B：增量抽取证据并回填 ----
    sources = []
    evidence = []
    all_texts = []
    if disc["case_json"]:
        sources.append({"path": "case.json", "kind": "case"})
    if disc["task_md"]:
        sources.append({"path": "task.md", "kind": "task"})
    for mp in disc["materials"]:
        txt, name, notes = read_material(mp, work_dir)
        sources.append({"path": name, "kind": "material"})
        if txt is None:
            degradations.extend("%s: %s" % (name, n) for n in notes)
            continue
        degradations.extend("%s: %s" % (name, n) for n in notes)
        all_texts.append((name, txt))
        evidence.extend(EX.extract_all(txt, name))

    try:
        (work_dir / "facts.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        degradations.append("cannot write facts.json: %s" % e)

    result["evidence_map"] = evidence
    result["terminology"] = build_terminology(evidence)
    result["sources"] = sources
    result["degradations"] = list(dict.fromkeys(degradations))
    result["errors"] = list(dict.fromkeys(errors))

    # ---- Phase C：finalize 时基于改稿复核 ----
    if args.mode == "finalize":
        deliv = out_dir / "deliverable.md"
        deliv_text = ""
        if deliv.exists():
            deliv_text = deliv.read_text(encoding="utf-8", errors="replace")
        if len(deliv_text.strip()) < 200 or "（待补" in deliv_text:
            result["status"] = "partial"
            result["limitations"].append("deliverable still skeleton or too short")
        else:
            unmatched = check_consistency(deliv_text, all_texts)
            if unmatched:
                result["status"] = "partial"
                result["uncertainty"].append(
                    {"item": "numbers_not_found_in_sources",
                     "values": unmatched,
                     "note": "改稿中出现源文未找到的数字，请核对是否改写/四舍五入/新增"})
                result["pending_human"].append(
                    "verify or remove numbers not present in sources: %s" % ", ".join(unmatched))
            else:
                result["status"] = "ok"
        if result["status"] == "ok" and result["errors"]:
            result["status"] = "partial"
    else:
        # skeleton 模式：partial 且无任何可读材料 → insufficient_evidence；
        # input_error（坏 JSON 等）必须保留，不能降级为“材料不足”。
        if result["status"] == "partial" and not all_texts:
            result["status"] = "insufficient_evidence"
            result["limitations"].append("no readable material text found")

    result["runtime"]["seconds"] = round(time.time() - t0, 1)
    write_result(result, out_dir)

    print(json.dumps({
        "status": result["status"],
        "case_id": result["case_id"],
        "evidence_count": len(evidence),
        "sources": len(sources),
        "result": str(out_dir / "result.json"),
        "deliverable": str(out_dir / "deliverable.md"),
    }, ensure_ascii=False))


def write_result(result, out_dir):
    path = Path(out_dir) / "result.json"
    try:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        # 写盘失败也要留下最小合法产物
        try:
            path.write_text(json.dumps(
                {"case_id": result.get("case_id", "unknown"),
                 "status": "input_error",
                 "deliverable": "deliverable.md",
                 "sources": [], "uncertainty": [], "change_log": [],
                 "errors": ["result.json write failed"],
                 "degradations": [], "limitations": []},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


if __name__ == "__main__":
    main()
