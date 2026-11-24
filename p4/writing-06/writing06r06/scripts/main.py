#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文结构与逻辑优化 —— 确定性中间表示流水线（仅 Python 标准库）。

职责：把原始稿件切成带稳定 locator 的句子/段落，抽取"论断—论据—结论"候选锚点，
检测跨章节近重复句与相关性/因果措辞，生成可复核的中间产物（/app/work/*.json），
并落盘 result.json 骨架与校验报告。语义判断由 Agent 依据这些锚点完成，脚本不编造结论。

用法：
  python scripts/main.py skeleton            # 先落盘合法 result.json 骨架（<3 分钟）
  python scripts/main.py analyze             # 材料盘点 + 分段 + 论点/冗余抽取 → /app/work/*.json
  python scripts/main.py verify              # 校验 result.json 契约与可回溯性 → verify_report.json
  python scripts/main.py                     # 依次执行 skeleton -> analyze -> verify
环境变量可覆盖默认路径：CASE_JSON / TASK_MD / INPUT_DIR / WORK_DIR / OUTPUT_DIR / RESULT_JSON。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

CASE_JSON = os.environ.get("CASE_JSON", "/input/case.json")
TASK_MD = os.environ.get("TASK_MD", "/input/task.md")
INPUT_DIR = os.environ.get("INPUT_DIR", "/input")
WORK_DIR = os.environ.get("WORK_DIR", "/app/work")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/output")
RESULT_JSON = os.environ.get("RESULT_JSON", os.path.join(OUTPUT_DIR, "result.json"))

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".tex", ".csv", ".tsv", ".json", ".rst", ".latex", ".markdown"}
BINARY_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".docx", ".pptx", ".xlsx"}
ALLOWED_STATUS = {"complete", "partial", "failed", "in_progress", "insufficient_evidence", "input_error"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 默认输出契约：case.json 未显式给出 output_contract 时使用，对齐任务的三类产物。
# ---------------------------------------------------------------------------
DEFAULT_CONTRACT = {
    "case_id": None,
    "status": "in_progress",
    "diagnosis": None,              # 目标产物：六维结构诊断（可编辑 Markdown 文本）
    "editing_plan": [],             # 改得动：可执行编辑动作列表
    "claim_map": [],                # 链得通：论点—论据映射
    "source_map": [],               # 说得清：事实与来源映射（locator + quote）
    "version_boundary": {           # 版本与边界
        "summary": None,
        "diffs": [],
        "material_gaps": [],
        "pending_human": [],
    },
    "uncertainty": [],
    "pending_human": [],
    "change_log": [],
    "errors": [],
    "degradations": [],
    "runtime": {"started_at": None, "finished_at": None, "elapsed_seconds": None},
}


def log_change(result, action, status="done"):
    entry = {"time": now_iso(), "action": action, "status": status}
    if "change_log" not in result or not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    result["change_log"].append(entry)
    return result


# ---------------------------------------------------------------------------
# 通用安全 IO
# ---------------------------------------------------------------------------
def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """按 utf-8-sig -> utf-8 -> latin-1 降级读文本；超限/二进制返回 None + 说明。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, f"stat failed: {e}"
    if size > max_bytes:
        return None, f"skipped: file too large ({size} bytes > {max_bytes})"
    raw = None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, f"read failed: {e}"
    if b"\x00" in raw[:4096]:
        return None, "skipped: binary (NUL bytes)"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), "ok"
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "ok(replace)"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def load_json_safe(path):
    if not os.path.exists(path):
        return None, "not found"
    text, note = read_text_safe(path)
    if text is None:
        return None, note
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as e:
        return None, f"bad JSON: {e}"


# ---------------------------------------------------------------------------
# 输入发现与案例解析
# ---------------------------------------------------------------------------
def discover_inputs(input_dir):
    """扫描输入目录：区分元文件(case.json/task.md)与材料文件，不假设固定文件名。"""
    files = []
    if not os.path.isdir(input_dir):
        return {"exists": False, "dir": input_dir, "meta": [], "materials": []}
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({"name": name, "rel": rel, "path": full, "size": size,
                          "ext": os.path.splitext(name)[1].lower()})
    meta = [f for f in files if f["name"] in ("case.json", "task.md")]
    materials = [f for f in files if f not in meta]
    return {"exists": True, "dir": input_dir, "meta": meta, "materials": materials}


def extract_case_id(discovery, case_json_path):
    case_id = None
    task_text = None
    obj, note = load_json_safe(case_json_path)
    if obj is not None and isinstance(obj, dict):
        case_id = obj.get("case_id") or obj.get("id") or obj.get("challenge_id") or obj.get("challengeId")
    # task.md 里可能带 challenge_id
    task_path = None
    for m in discovery.get("meta", []):
        if m["name"] == "task.md":
            task_path = m["path"]
            break
    if task_path:
        task_text, _ = read_text_safe(task_path)
    if case_id is None and task_text:
        m = re.search(r"challenge[_\-]?id[:：]\s*[`\"']?([A-Za-z0-9\-_]+)", task_text, re.I)
        if m:
            case_id = m.group(1)
    return case_id or "unknown"


# ---------------------------------------------------------------------------
# 文本分段：稳定 locator（P<n> = 段落，P<n>.S<m> = 句子，H<n> = 标题）
# ---------------------------------------------------------------------------
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
NUMBERED_HEADING_RE = re.compile(
    r"^((?:\d{1,2}(?:\.\d{1,2}){0,3})[\.、\s]+)([A-Za-z\u4e00-\u9fff][^\n]{0,60})$")
SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.．])\s+(?=[A-Z0-9])")


def _is_heading(line):
    s = line.strip()
    if not s:
        return False
    if HEADING_RE.match(s):
        return True
    if NUMBERED_HEADING_RE.match(s) and len(s) < 120 and not s.endswith((".", "。", "?", "？")):
        return True
    return False


def split_sentences(text):
    parts = SENT_SPLIT_RE.split(text)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    if not out and text.strip():
        out.append(text.strip())
    return out


def segment_text(source, text):
    """把单个文本文件切成 headings + paragraphs + sentences，稳定且可复核。"""
    lines = text.splitlines()
    headings = []
    paragraphs = []
    cur_heading = None
    cur_lines = []
    para_idx = 0

    def flush():
        nonlocal cur_lines, para_idx
        body = "\n".join(cur_lines).strip()
        cur_lines = []
        if not body:
            return
        para_idx += 1
        sents = split_sentences(body)
        paragraphs.append({
            "id": f"P{para_idx}",
            "heading": cur_heading,
            "text": body,
            "sentences": [{"id": f"P{para_idx}.S{j + 1}", "text": s}
                          for j, s in enumerate(sents)],
        })

    for ln in lines:
        if _is_heading(ln):
            flush()
            headings.append({
                "id": f"H{len(headings) + 1}",
                "text": ln.strip(),
                "para_range_start": para_idx + 1,
            })
            cur_heading = f"H{len(headings)}"
        else:
            if ln.strip() == "":
                flush()
            else:
                cur_lines.append(ln)
    flush()
    # 记录每个标题覆盖的段落范围（到下一标题前）
    for i, h in enumerate(headings):
        end = paragraphs[-1]["id"][1:] if paragraphs else "0"
        if i + 1 < len(headings):
            end = headings[i + 1]["para_range_start"] - 1
        h["para_range_end"] = end
    return {"source": source, "headings": headings, "paragraphs": paragraphs,
            "n_paragraphs": len(paragraphs),
            "n_sentences": sum(len(p["sentences"]) for p in paragraphs)}


# ---------------------------------------------------------------------------
# 论点 / 论据 / 结论 候选锚点抽取
# ---------------------------------------------------------------------------
MARKERS = {
    "assertion": ["表明", "显示", "证实", "说明", "揭示", "发现", "提示", "证明",
                  "demonstrate", "demonstrates", "show", "shows", "shown", "indicate",
                  "indicates", "reveal", "reveals", "suggest", "suggests", "confirm",
                  "confirm", "find", "found", "support", "prove"],
    "conclusion": ["因此", "所以", "综上", "总之", "可见", "据此", "由此", "综上所述",
                   "由此可见", "结论", "therefore", "thus", "hence", "consequently",
                   "in conclusion", "overall", "in summary"],
    "causal": ["导致", "引起", "使得", "从而", "因为", "由于", "促使", "诱导", "驱动",
               "引发", "造成", "原因", "决定", "决定性", "cause", "caused", "lead to", "leads to",
               "due to", "because", "drive", "driven", "induce", "trigger", "result in",
               "attribute", "determine", "determines", "determinant", "reason"],
    "overclaim": ["必然", "一定", "所有", "全部", "首次", "首创", "彻底", "完全", "普遍",
                  "突破", "prove", "proves", "definitely", "always", "never", "universally",
                  "for the first time", "novel", "state-of-the-art", "crucial", "first-ever"],
    "hedge": ["可能", "或许", "似乎", "倾向于", "初步", "有待", "尚需", "提示", "可能地",
              "may", "might", "suggest", "suggests", "likely", "potentially", "tentative",
              "preliminary", "perhaps"],
}


def _flag(sentence_lower, sentence_orig):
    flags = {}
    matched = {}
    for key, words in MARKERS.items():
        hits = [w for w in words if w in sentence_lower]
        flags[key] = bool(hits)
        if hits:
            matched[key] = hits[:6]
    return flags, matched


def extract_claims(text_models):
    """从分段结果中抽取论断/结论/因果/过度措辞候选锚点（只标候选，不做语义定论）。"""
    claims = []
    for model in text_models:
        for p in model["paragraphs"]:
            for s in p["sentences"]:
                low = s["text"].lower()
                if len(s["text"].strip()) < 8:
                    continue
                flags, matched = _flag(low, s["text"])
                if any(flags.values()):
                    claims.append({
                        "locator": f"{model['source']}#{s['id']}",
                        "text": s["text"],
                        "flags": flags,
                        "matched_markers": matched,
                    })
    return claims


# ---------------------------------------------------------------------------
# 跨章节近重复检测（token Jaccard）
# ---------------------------------------------------------------------------
TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z][A-Za-z0-9]*|[0-9]+")


def _tokens(text):
    return set(t.lower() for t in TOKEN_RE.findall(text))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def detect_redundancy(text_models, threshold=0.55):
    """句级近重复对（含跨章节），供 Agent 判断合并/去重，不自动删稿。"""
    pairs = []
    sentences = []
    for model in text_models:
        for p in model["paragraphs"]:
            for s in p["sentences"]:
                t = s["text"].strip()
                if len(t) < 8:
                    continue
                sentences.append((f"{model['source']}#{s['id']}", t))
    if len(sentences) > 4000:
        sentences = sentences[:4000]
    toks = [_tokens(t) for _, t in sentences]
    for i in range(len(sentences)):
        for j in range(i + 1, len(sentences)):
            sim = _jaccard(toks[i], toks[j])
            if sim >= threshold:
                pairs.append({
                    "a": {"locator": sentences[i][0], "text": sentences[i][1]},
                    "b": {"locator": sentences[j][0], "text": sentences[j][1]},
                    "jaccard": round(sim, 3),
                })
    return {"threshold": threshold, "n_pairs": len(pairs), "pairs": pairs}


def chapter_balance(text_models):
    """按标题统计每章字符数占比，辅助叙事顺序/篇幅分配判断。"""
    rows = []
    for model in text_models:
        for h in model["headings"]:
            start = int(h.get("para_range_start") or 1)
            end = int(h.get("para_range_end") or 0)
            chars = 0
            for p in model["paragraphs"]:
                pidx = int(p["id"][1:])
                if start <= pidx <= (end if end >= start else start):
                    chars += len(p["text"])
            rows.append({"source": model["source"], "heading_id": h["id"],
                         "heading": h["text"], "para_range": [start, end],
                         "chars": chars})
    return rows


# ---------------------------------------------------------------------------
# 结果骨架 / 增量写入
# ---------------------------------------------------------------------------
def build_skeleton(case_id, contract=None):
    """始终从 DEFAULT_CONTRACT 起步，再把 case.json 的 output_contract 字段并入：
    不改名、不删未知键、缺值给 null/[]。"""
    skeleton = json.loads(json.dumps(DEFAULT_CONTRACT))
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k in ("case_id", "status"):
                continue
            if k not in skeleton:
                if isinstance(v, list):
                    skeleton[k] = []
                elif isinstance(v, dict):
                    skeleton[k] = {}
                else:
                    skeleton[k] = None
    skeleton["case_id"] = case_id
    skeleton["status"] = "in_progress"
    skeleton["runtime"]["started_at"] = now_iso()
    log_change(skeleton, "skeleton: result.json 骨架落盘", "done")
    return skeleton


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def verify_result(result):
    report = {"passed": [], "failed": [], "warn": [], "summary": {}}
    if not isinstance(result, dict):
        report["failed"].append("result.json 不是 JSON 对象")
        return report
    for field in ("case_id", "status", "diagnosis", "source_map", "uncertainty", "change_log"):
        if field not in result:
            report["failed"].append(f"缺少必需字段: {field}")
        else:
            report["passed"].append(f"字段存在: {field}")
    if result.get("status") not in ALLOWED_STATUS:
        report["warn"].append(f"status 取值非推荐值: {result.get('status')}")
    if not result.get("case_id"):
        report["warn"].append("case_id 为空")
    sm = result.get("source_map")
    if isinstance(sm, list) and sm:
        bad = [i for i, e in enumerate(sm) if not isinstance(e, dict)
               or not e.get("locator") or not e.get("quote")]
        if bad:
            report["failed"].append(f"source_map 有 {len(bad)} 条缺少 locator 或 quote")
        else:
            report["passed"].append(f"source_map {len(sm)} 条均含 locator+quote")
    else:
        report["warn"].append("source_map 为空（若材料缺失请注明）")
    ep = result.get("editing_plan")
    if isinstance(ep, list) and ep:
        missing = [i for i, e in enumerate(ep) if not e.get("action_type") or not e.get("target_locator")]
        if missing:
            report["failed"].append(f"editing_plan 有 {len(missing)} 条缺少 action_type 或 target_locator")
        else:
            report["passed"].append(f"editing_plan {len(ep)} 条含 action_type+target_locator")
    n_pass = len(report["passed"])
    n_fail = len(report["failed"])
    n_warn = len(report["warn"])
    report["summary"] = {"passed": n_pass, "failed": n_fail, "warn": n_warn,
                         "verdict": "FAIL" if n_fail else ("WARN" if n_warn else "PASS")}
    return report


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_skeleton():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    discovery = discover_inputs(INPUT_DIR)
    if not discovery.get("exists"):
        result = json.loads(json.dumps(DEFAULT_CONTRACT))
        result["case_id"] = "unknown"
        result["status"] = "insufficient_evidence"
        result["uncertainty"] = ["输入目录不存在或不可读"]
        result["runtime"]["started_at"] = now_iso()
        result["runtime"]["finished_at"] = now_iso()
        result["runtime"]["elapsed_seconds"] = 0
        log_change(result, "skeleton: 输入目录缺失，标记 insufficient_evidence", "done")
        write_json(RESULT_JSON, result)
        print(json.dumps({"ok": True, "status": "insufficient_evidence",
                          "result": RESULT_JSON}, ensure_ascii=False))
        return 0
    case_id = extract_case_id(discovery, CASE_JSON)
    contract = None
    case_obj, note = load_json_safe(CASE_JSON)
    errors = []
    degradations = []
    if case_obj is None:
        if note == "not found":
            degradations.append("case.json 未找到，case_id 取自 task.md 或置为 unknown")
        else:
            degradations.append(f"case.json 读取异常: {note}")
            errors.append(f"case.json: {note}")
        case_obj = {}
    elif not isinstance(case_obj, dict):
        degradations.append("case.json 顶层不是 JSON 对象，按默认契约处理")
        errors.append(f"case.json: 顶层类型 {type(case_obj).__name__}")
        case_obj = {}
    else:
        contract = case_obj.get("output_contract")

    n_materials = len(discovery.get("materials", []))
    n_meta = len(discovery.get("meta", []))
    if n_meta == 0 and n_materials == 0:
        # 目录存在但既无任务说明也无材料：无事实依据，不可诊断
        skeleton = build_skeleton(case_id, contract)
        skeleton["status"] = "insufficient_evidence"
        skeleton["uncertainty"] = ["输入目录为空：无 case.json、无 task.md、无材料文件"]
        skeleton["errors"] = errors
        skeleton["degradations"] = degradations
        skeleton["runtime"]["finished_at"] = now_iso()
        skeleton["runtime"]["elapsed_seconds"] = 0
        log_change(skeleton, "skeleton: 空输入，标记 insufficient_evidence", "done")
        write_json(RESULT_JSON, skeleton)
        print(json.dumps({"ok": True, "case_id": case_id, "status": "insufficient_evidence",
                          "result": RESULT_JSON}, ensure_ascii=False))
        return 0

    skeleton = build_skeleton(case_id, contract)
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations
    write_json(RESULT_JSON, skeleton)
    print(json.dumps({"ok": True, "case_id": case_id, "status": "in_progress",
                      "result": RESULT_JSON}, ensure_ascii=False))
    return 0


def cmd_analyze():
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    plan = {"started_at": now_iso(), "budget": {"seconds": 900, "model_requests": 64},
            "steps": [{"name": "skeleton", "status": "done"},
                      {"name": "inventory", "status": "running"},
                      {"name": "segment", "status": "pending"},
                      {"name": "extract", "status": "pending"},
                      {"name": "redundancy", "status": "pending"},
                      {"name": "verify", "status": "pending"}]}
    write_json(os.path.join(WORK_DIR, "plan.json"), plan)

    discovery = discover_inputs(INPUT_DIR)
    inventory = {"input_dir": INPUT_DIR, "exists": discovery.get("exists", False),
                 "materials": [], "unreadable": []}
    text_models = []
    if discovery.get("exists"):
        for m in discovery.get("materials", []):
            ext = m["ext"]
            entry = {"rel": m["rel"], "path": m["path"], "size": m["size"], "ext": ext,
                     "kind": "text" if ext in TEXT_EXTS else
                     ("binary" if ext in BINARY_EXTS else "unknown"),
                     "read_status": None}
            if ext in BINARY_EXTS:
                entry["read_status"] = "skipped: 需外部工具(pdf-text-tool/pdf-render-tool/ocr-tool)"
                inventory["materials"].append(entry)
                continue
            text, note = read_text_safe(m["path"])
            entry["read_status"] = note
            if text is not None:
                model = segment_text(m["rel"], text)
                text_models.append(model)
                entry["chars"] = len(text)
                entry["n_paragraphs"] = model["n_paragraphs"]
            else:
                inventory["unreadable"].append({"rel": m["rel"], "reason": note})
            inventory["materials"].append(entry)
    write_json(os.path.join(WORK_DIR, "inventory.json"), inventory)
    plan["steps"][1]["status"] = "done"
    plan["steps"][2]["status"] = "done"
    write_json(os.path.join(WORK_DIR, "plan.json"), plan)

    write_json(os.path.join(WORK_DIR, "text_model.json"), text_models)

    claims = extract_claims(text_models)
    claim_model = {"n_claims": len(claims), "claims": claims,
                   "note": "候选锚点：断言/结论/因果/过度措辞/谨慎措辞仅由关键词提示，语义判断由 Agent 完成"}
    write_json(os.path.join(WORK_DIR, "claim_model.json"), claim_model)
    plan["steps"][3]["status"] = "done"

    redundancy = detect_redundancy(text_models)
    write_json(os.path.join(WORK_DIR, "redundancy.json"), redundancy)

    chapters = chapter_balance(text_models)
    chapter_model = {"rows": chapters,
                     "note": "章节功能拆解与叙事顺序判断由 Agent 基于此结构完成"}
    write_json(os.path.join(WORK_DIR, "chapter_model.json"), chapter_model)
    plan["steps"][4]["status"] = "done"
    plan["steps"][5]["status"] = "pending"
    write_json(os.path.join(WORK_DIR, "plan.json"), plan)

    print(json.dumps({
        "ok": True,
        "files": inventory.get("materials", []),
        "text_models": len(text_models),
        "claims": len(claims),
        "redundant_pairs": redundancy["n_pairs"],
        "chapters": len(chapters),
        "work_dir": WORK_DIR,
    }, ensure_ascii=False))
    return 0


def cmd_verify():
    result, note = load_json_safe(RESULT_JSON)
    if result is None:
        report = {"passed": [], "failed": [f"result.json 不可读: {note}"],
                  "warn": [], "summary": {"verdict": "FAIL"}}
    else:
        report = verify_result(result)
    report["checked_at"] = now_iso()
    report["result_path"] = RESULT_JSON
    write_json(os.path.join(OUTPUT_DIR, "verify_report.json"), report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["summary"]["verdict"] != "FAIL" else 1


def cmd_run():
    rc = cmd_skeleton()
    if rc != 0:
        return rc
    rc = cmd_analyze()
    if rc != 0:
        return rc
    return cmd_verify()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "skeleton", "analyze", "verify"])
    args = ap.parse_args(argv)
    if args.command == "skeleton":
        return cmd_skeleton()
    if args.command == "analyze":
        return cmd_analyze()
    if args.command == "verify":
        return cmd_verify()
    return cmd_run()


if __name__ == "__main__":
    sys.exit(main())
