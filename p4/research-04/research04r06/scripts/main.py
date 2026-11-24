#!/usr/bin/env python3
"""research-frontier-audit — 阶段1：输入发现 + plan.json + result.json 骨架。

只依赖 Python 标准库。缺省路径遵循运行时契约，可用 --input/--output/--work 覆盖。
退出码 0：成功落盘骨架；退出码非 0：无法写入（保留错误信息到 stderr）。
"""
import os
import sys
import json
import re
import argparse
import datetime
import hashlib

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}
MAX_BYTES = 2 * 1024 * 1024
MAX_FILES = 200

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml",
            ".yaml", ".yml", ".bib", ".ris", ".tex", ".tsv"}
PDF_EXT = {".pdf"}

STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on",
             "with", "by", "is", "are", "was", "were", "be", "been", "as",
             "at", "from", "that", "this", "these", "those", "we", "our",
             "their", "its", "it", "not", "no", "can", "may", "will",
             "which", "who", "whom", "how", "what", "when", "where", "but",
             "if", "then", "than", "so", "such", "into", "over", "under",
             "about", "between", "through", "during", "using", "based",
             "also", "has", "have", "had", "do", "does", "did",
             "however", "although", "though", "despite", "therefore", "thus",
             "while", "because", "since", "whereas", "still", "yet", "among",
             "within", "without", "other", "more", "most", "one", "two",
             "three", "new", "used", "use", "results", "result", "data",
             "paper", "study", "method", "methods", "show", "shows", "shown",
             "first", "second", "third", "well", "abstract", "introduction",
             "conclusion", "related", "work", "works"}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bytes(path, max_bytes=MAX_BYTES):
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None, "oversize>%dMB" % (max_bytes // (1024 * 1024))
        return data, None
    except OSError as e:
        return None, "read_error:" + str(e)


def decode_text(data):
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace", "decode_partial"


def read_text_safe(path, max_bytes=MAX_BYTES):
    data, err = read_bytes(path, max_bytes)
    if err:
        return None, None, err
    return decode_text(data)


def load_json_safe(path):
    text, _enc, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "bad_json:" + str(e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def extract_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for k in ("case_id", "id", "challenge_id"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    t = task_text or ""
    m = re.search(r"challenge[-_]?id[:\s`\"']+([A-Za-z0-9_-]+)", t, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(?:case|id)[:\s`\"']+([A-Za-z0-9_-]{6,})", t, re.I)
    if m:
        return m.group(1)
    return "unknown"


def backfill_contract(case_obj):
    """原样回填 output_contract 全部字段；缺值给 null，不改名不删键。"""
    oc = {}
    if isinstance(case_obj, dict) and isinstance(case_obj.get("output_contract"), dict):
        for k, v in case_obj["output_contract"].items():
            oc[k] = v
    return oc


def empty_result(case_id, contract, started):
    return {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
        "research_question": {"question": "", "scope": "", "sub_questions": [], "domain_note": ""},
        "protocol": {"inclusion_criteria": [], "exclusion_criteria": [], "search_scope": "", "date_definition_note": ""},
        "time_trend": {"unit_note": "", "distribution": [], "windows": [], "claims": []},
        "topic_evolution": {"stage_divisions": [], "term_mapping": [], "events": [], "claims": []},
        "representative_works": {"criteria": [], "stages": []},
        "research_gaps": {"gaps": [], "excluded_not_gaps": []},
        "evidence_map": [],
        "conflicts": [],
        "artifacts": {
            "result": "result.json",
            "report": "report.md",
            "evidence_map": "evidence_map.json",
            "tables": ["tables/documents.csv", "tables/year_distribution.csv"],
            "plan": "plan.json",
            "figure_model": "figure_model.json",
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": started,
            "action": "skeleton",
            "reason": "前3分钟落盘 partial 骨架，保证中断不丢产物",
            "before": None,
            "after": "result.json 骨架 + plan.json + manifest.json",
        }],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": started, "updated_at": started, "seconds_elapsed": 0, "checkpoint": "skeleton"},
    }


def build_plan(case_id, started):
    return {
        "schema": "plan-v1",
        "case_id": case_id,
        "generated_at": started,
        "frozen": True,
        "research_question": {"question": "", "scope": "", "sub_questions": [], "domain_note": ""},
        "protocol": {"inclusion_criteria": [], "exclusion_criteria": [], "search_scope": "", "date_definition_note": ""},
        "term_grouping_rules": {
            "normalize": "lowercase; 去标点; 空白折叠",
            "synonym_map": {},
            "stopwords": sorted(STOPWORDS),
            "note": "机械抽取术语候选后须人工归并同义词/变体再判演化",
        },
        "window_rules": {
            "min_window": 2,
            "max_window": 5,
            "split_gap": 3,
            "note": "按年度分布自然空档(>=3年)切窗，否则等宽4年；不得凭空切段",
        },
        "budget": {
            "total_seconds": 900,
            "model_requests": 64,
            "tokens_per_response": 16384,
            "checkpoints": [
                {"stage": 1, "deadline_seconds": 180, "artifact": "result.json skeleton + plan.json"},
                {"stage": 2, "deadline_seconds": 360, "artifact": "figure_model.json + tables + report"},
                {"stage": 3, "deadline_seconds": 720, "artifact": "四类深度分析"},
                {"stage": 4, "deadline_seconds": 820, "artifact": "selfcheck 通过"},
                {"stage": 5, "deadline_seconds": 900, "artifact": "定稿"},
            ],
        },
        "change_log": [{"time": started, "action": "freeze", "reason": "阶段1落盘后只读，改计划需记 change_log"}],
    }


def discover_files(input_dir):
    files = []
    errors = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing:" + input_dir]
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            if rel in ("case.json", "task.md"):
                continue  # 输入契约文件，不作为科研材料
            ext = os.path.splitext(name)[1].lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                errors.append("stat_error:%s:%s" % (rel, e))
                continue
            if ext in PDF_EXT:
                kind = "pdf"
            elif ext in TEXT_EXT or name in ("task.md", "case.json", "README", "README.md"):
                kind = "text"
            else:
                kind = "other"
            files.append({"rel": rel, "abs": p, "ext": ext, "size": size, "kind": kind})
    return files, errors


def normalize_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def build_manifest(input_dir, work_dir, errors, degradations):
    files, errs = discover_files(input_dir)
    errors.extend(errs)
    if len(files) > MAX_FILES:
        degradations.append("file_count_exceeded:%d>%d, sampled first %d by sorted path" % (len(files), MAX_FILES, MAX_FILES))
        files = files[:MAX_FILES]

    materials_dir = os.path.join(work_dir, "materials")
    os.makedirs(materials_dir, exist_ok=True)

    manifest = {"input_dir": input_dir, "materials_dir": "materials", "files": [], "counts": {}}
    # 稳定编号：按相对路径排序，保证重复运行 doc_id 一致
    ordered = sorted(files, key=lambda x: x["rel"].lower())
    for i, f in enumerate(ordered, 1):
        mat_rel = "materials/mat_%03d.txt" % i
        entry = {
            "index": i,
            "mat_id": "mat_%03d" % i,
            "doc_id": "D%03d" % i,
            "rel": f["rel"],
            "abs": f["abs"],
            "kind": f["kind"],
            "ext": f["ext"],
            "size": f["size"],
            "material_file": mat_rel,
            "status": "read",
            "encoding": None,
            "note": None,
        }
        if f["kind"] == "text":
            text, enc, err = read_text_safe(f["abs"])
            if err:
                entry["status"] = "skipped"
                entry["note"] = err
                errors.append("skip:%s:%s" % (f["rel"], err))
            elif text is None:
                entry["status"] = "skipped"
                entry["note"] = "empty_or_unreadable"
            else:
                text = normalize_newlines(text)
                write_text(os.path.join(work_dir, mat_rel), text)
                entry["encoding"] = enc
                entry["char_count"] = len(text)
        elif f["kind"] == "pdf":
            entry["status"] = "needs_extraction"
            entry["note"] = "PDF 需先经平台 pdf-text-tool 或 pdftotext 抽取文本到 /app/work/materials/mat_%03d.txt" % i
        else:
            entry["status"] = "ignored"
            entry["note"] = "非文本/非 PDF，不计入文献"
        manifest["files"].append(entry)

    counts = {"total": len(ordered), "text": 0, "pdf": 0, "other": 0,
              "read": 0, "needs_extraction": 0, "skipped": 0, "ignored": 0}
    for e in manifest["files"]:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    manifest["counts"] = counts
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="阶段1：输入发现与落骨架")
    ap.add_argument("--input", default=DEFAULTS["input"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    ap.add_argument("--force", action="store_true", help="覆盖既有 result.json（默认保留并增量）")
    args = ap.parse_args(argv)

    started = utcnow()
    errors, degradations, limitations = [], [], []

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    has_case = os.path.isfile(os.path.join(args.input, "case.json"))
    has_task = os.path.isfile(os.path.join(args.input, "task.md"))

    case_obj = {}
    case_err = None
    if has_case:
        case_obj, case_err = load_json_safe(os.path.join(args.input, "case.json"))
        if case_err:
            errors.append("case.json:" + case_err)
            case_obj = {}

    task_text = None
    if has_task:
        task_text, _enc, terr = read_text_safe(os.path.join(args.input, "task.md"))
        if terr:
            errors.append("task.md:" + terr)

    case_id = extract_case_id(case_obj, task_text or "")
    contract = backfill_contract(case_obj)

    manifest = build_manifest(args.input, args.work, errors, degradations)
    write_json(os.path.join(args.work, "manifest.json"), manifest)

    # 诚实状态：坏契约且无 task.md -> input_error；无任何材料 -> insufficient_evidence；其余 -> partial
    if has_case and case_err and not has_task:
        status_hint = "input_error"
    elif manifest["counts"]["total"] == 0:
        status_hint = "insufficient_evidence"
        limitations.append("输入目录无科研材料，无法区分空白与遗漏")
    else:
        status_hint = "partial"

    if has_case and case_err and isinstance(case_obj, dict) and not case_obj:
        limitations.append("case.json 不可解析，仅依赖 task.md 文本")

    result = empty_result(case_id, contract, started)
    result["status"] = status_hint
    for e in errors:
        result["errors"].append(e)
    for d in degradations:
        result["degradations"].append(d)
    for l in limitations:
        result["limitations"].append(l)

    plan = build_plan(case_id, started)

    # 从 task.md 中若含明确研究问题/检索范围/纳排则回填骨架（原文字段，非臆测）
    if task_text:
        m = re.search(r"(?:研究问题|research[_ ]?question)[:：]\s*(.{0,300})", task_text, re.I)
        if m:
            result["research_question"]["question"] = m.group(1).strip()
        m = re.search(r"(?:检索范围|search[_ ]?scope)[:：]\s*(.{0,300})", task_text, re.I)
        if m:
            result["protocol"]["search_scope"] = m.group(1).strip()

    result_path = os.path.join(args.output, "result.json")
    plan_path = os.path.join(args.output, "plan.json")
    existing_result, _ = load_json_safe(result_path)
    existing_plan, _ = load_json_safe(plan_path)

    # --force 重置骨架；否则保留既有 result.json/plan.json，仅刷新 manifest（增量改进，不覆盖人工结果）
    if args.force or existing_result is None:
        write_json(result_path, result)
        final_status = result["status"]
    else:
        final_status = existing_result.get("status", "partial") if isinstance(existing_result, dict) else result["status"]

    if args.force or existing_plan is None:
        write_json(plan_path, plan)

    write_json(os.path.join(args.work, "prepare_summary.json"), {
        "case_id": case_id, "status": final_status,
        "files_total": manifest["counts"]["total"], "files_read": manifest["counts"]["read"],
        "files_needs_extraction": manifest["counts"]["needs_extraction"],
        "errors": errors, "degradations": degradations,
    })

    print(json.dumps({
        "ok": True, "case_id": case_id, "status": final_status,
        "manifest": os.path.join(args.work, "manifest.json"),
        "plan": plan_path, "result": result_path,
        "counts": manifest["counts"],
        "errors": errors, "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
