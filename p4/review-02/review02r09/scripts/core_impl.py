# -*- coding: utf-8 -*-
"""citation-fact-audit Skill 实现（仅 Python 标准库）。

子命令：discover / scaffold / finalize / selftest。
路径可经 --input/--output/--work 覆盖，缺省为 /input、/app/output、/app/work。
设计目标：确定性（无随机数、按 id 排序）、可恢复（先骨架后增量）、诚实降级。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

VERSION = "3.0.0"
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
VERDICTS = ("pass", "issues_found", "needs_review", "unchecked", "insufficient")
CATEGORIES = (
    "citation_support",
    "fact_source",
    "evidence_location",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
    "method_design",
)
SEVERITIES = ("critical", "major", "minor", "suggestion")
REQUIRED_FINDING_KEYS = (
    "id", "category", "locator", "quote", "verdict", "severity", "suggestion",
)
MAX_FILE_BYTES = 2_000_000
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".tsv", ".tex"}


# --------------------------------------------------------------------------
# 基础 IO（全部 utf-8-sig 容错，坏编码/坏 JSON 不崩溃）
# --------------------------------------------------------------------------
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def read_text(path, max_bytes=MAX_FILE_BYTES):
    try:
        if os.path.getsize(path) > max_bytes:
            return None, "文件超过大小上限，已跳过"
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read(), None
    except Exception as exc:  # noqa: BLE001
        return None, "读取失败：%s" % exc


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def normalize(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


# --------------------------------------------------------------------------
# discover：扫描输入、抽取 PDF、写 manifest
# --------------------------------------------------------------------------
def add_page_markers(raw):
    pages = [p for p in raw.split("\f")]
    if pages and not pages[-1].strip():
        pages.pop()
    n = len(pages)
    if n == 0:
        return "----- PAGE 0 / 0 -----\n（未抽取到文本，疑似扫描版）", 0
    parts = []
    for i, page in enumerate(pages, 1):
        body = page.strip()
        if body:
            parts.append("----- PAGE %d / %d -----\n%s" % (i, n, body))
    if not parts:
        return "----- PAGE 0 / 0 -----\n（未抽取到文本，疑似扫描版）", 0
    return "\n\n".join(parts), n


def extract_pdf(pdf_path, out_path):
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, out_path],
            capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError:
        return 0, "pdftotext 不可用，该 PDF 标 needs_ocr"
    except Exception as exc:  # noqa: BLE001
        return 0, "PDF 抽取异常：%s" % exc
    if proc.returncode != 0:
        return 0, "pdftotext 退出码 %s：%s" % (proc.returncode, proc.stderr.strip()[:200])
    raw, err = read_text(out_path)
    if raw is None:
        return 0, err
    text, n = add_page_markers(raw)
    write_text(out_path, text)
    if n == 0 or len(text.strip()) < 40:
        return 0, "未抽取到有效文本，该 PDF 标 needs_ocr"
    return n, None


def cmd_discover(input_dir, work_dir):
    texts_dir = os.path.join(work_dir, "texts")
    os.makedirs(texts_dir, exist_ok=True)
    manifest = {
        "input_dir": input_dir,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "texts": [],
        "errors": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        manifest["errors"].append("输入目录不存在：%s" % input_dir)
        write_json(os.path.join(work_dir, "manifest.json"), manifest)
        print("discover: 输入目录不存在，已写空 manifest")
        return 0

    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        if load_json(case_path) is None:
            manifest["errors"].append("case.json 不是合法 JSON")
        else:
            manifest["case_json"] = case_path
    else:
        manifest["errors"].append("缺少 case.json")

    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        manifest["task_md"] = task_path

    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path) or name in ("case.json", "task.md"):
            continue
        ext = os.path.splitext(name)[1].lower()
        rec = {"path": path, "name": name, "ext": ext, "bytes": os.path.getsize(path)}
        if ext == ".pdf":
            out = os.path.join(texts_dir, name[:-4] + ".txt")
            pages, err = extract_pdf(path, out)
            if err:
                rec["status"] = "needs_ocr" if "needs_ocr" in err else "error"
                rec["error"] = err
                (manifest["degradations"] if rec["status"] == "needs_ocr" else manifest["errors"]).append(
                    "%s：%s" % (name, err)
                )
            else:
                rec["status"] = "ok"
                rec["pages"] = pages
                rec["text"] = out
                manifest["texts"].append({"material": name, "text": out, "pages": pages})
        elif ext in TEXT_EXTS:
            text, err = read_text(path)
            if text is None:
                rec["status"] = "error"
                rec["error"] = err
                manifest["errors"].append("%s：%s" % (name, err))
            else:
                out = os.path.join(texts_dir, name + ".txt")
                write_text(out, text)
                rec["status"] = "ok"
                rec["text"] = out
                manifest["texts"].append({"material": name, "text": out, "pages": 1})
        else:
            rec["status"] = "unsupported"
            manifest["degradations"].append("%s：不支持的文件类型 %s" % (name, ext or "无扩展名"))
        manifest["materials"].append(rec)

    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    print("discover: materials=%d texts=%d errors=%d degradations=%d" % (
        len(manifest["materials"]), len(manifest["texts"]),
        len(manifest["errors"]), len(manifest["degradations"]),
    ))
    return 0


# --------------------------------------------------------------------------
# scaffold：先落盘合法 partial 骨架
# --------------------------------------------------------------------------
def empty_counts():
    counts = {"total": 0, "critical": 0, "major": 0, "minor": 0, "suggestion": 0, "by_category": {}}
    for cat in CATEGORIES:
        counts["by_category"][cat] = 0
    return counts


def build_skeleton(case_id, case):
    result = {
        "case_id": case_id,
        "status": "partial",
        "summary": {"verdict": "unchecked", "counts": empty_counts()},
        "review_report": {"findings": []},
        "evidence": {"evidence_map": [], "integrity": {"checked": False, "mismatches": []}},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": now_iso(), "finished_at": None, "elapsed_sec": 0.0, "version": VERSION},
    }
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(contract, dict):
        for key, value in contract.items():
            result.setdefault(key, value)  # 标准字段优先，未知键原样回填
    result["change_log"].append({"seq": 0, "stage": "scaffold", "summary": "落盘 partial 结果骨架"})
    return result


def input_insufficient(manifest):
    errors = manifest.get("errors") or []
    materials = manifest.get("materials") or []
    if any("case.json" in str(e) for e in errors):
        return "缺少或损坏 case.json"
    if not materials:
        return "输入目录无科研材料"
    return None


def cmd_scaffold(input_dir, work_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    case = load_json(os.path.join(input_dir, "case.json"), {})
    if not isinstance(case, dict):
        case = {}
    case_id = case.get("case_id") or case.get("id") or "unknown"
    manifest = load_json(os.path.join(work_dir, "manifest.json"), {})
    if not isinstance(manifest, dict):
        manifest = {}
    result = build_skeleton(case_id, case)
    gap = input_insufficient(manifest)
    if gap:
        result["status"] = "insufficient_evidence"
        result["summary"]["verdict"] = "insufficient"
        result["limitations"] = [gap]
        result["change_log"].append({"seq": 1, "stage": "scaffold", "summary": "输入不足，进入降级"})
    write_json(os.path.join(work_dir, "state.json"), {"case_id": case_id, "status": result["status"], "stage": "scaffold"})
    write_json(os.path.join(output_dir, "result.json"), result)
    print("scaffold: result.json 已落盘 status=%s" % result["status"])
    return 0


# --------------------------------------------------------------------------
# finalize：合并 findings、渲染 report、计算状态
# --------------------------------------------------------------------------
def collect_findings(work_dir):
    directory = os.path.join(work_dir, "findings")
    findings = []
    errors = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            obj = load_json(os.path.join(directory, name))
            if isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        findings.append(item)
            elif isinstance(obj, dict):
                findings.append(obj)
            else:
                errors.append("findings/%s 不是 JSON 对象" % name)
    findings.sort(key=lambda f: str(f.get("id", "")))
    return findings, errors


def compute_counts(findings):
    counts = empty_counts()
    for f in findings:
        counts["total"] += 1
        if f.get("severity") in SEVERITIES:
            counts[f["severity"]] += 1
        cat = f.get("category")
        if cat:
            counts["by_category"][cat] = counts["by_category"].get(cat, 0) + 1
    return counts


def build_evidence_map(findings):
    return [
        {
            "finding_id": f.get("id"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis"),
            "source_ref": f.get("source_ref"),
        }
        for f in findings
    ]


def load_extracted_texts(work_dir):
    texts = {}
    directory = os.path.join(work_dir, "texts")
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if name.endswith(".txt"):
                text, _ = read_text(os.path.join(directory, name))
                if text is not None:
                    texts[name] = normalize(text)
    return texts


def verify_quotes(findings, work_dir):
    texts = load_extracted_texts(work_dir)
    problems = []
    for f in findings:
        fid = f.get("id", "?")
        quote = normalize(f.get("quote"))
        if not quote:
            problems.append("%s：缺少逐字 quote" % fid)
        elif texts and not any(quote in t for t in texts.values()):
            problems.append("%s：quote 未在抽取文本中逐字命中" % fid)
    return problems


def resolve_status(result, meta, findings):
    value = meta.get("status") if isinstance(meta, dict) else None
    if value in STATUSES:
        return value
    if result.get("errors"):
        return "input_error"
    if findings:
        return "ok"
    return result.get("status", "partial")


def resolve_verdict(result, meta, findings):
    value = meta.get("verdict") if isinstance(meta, dict) else None
    if value in VERDICTS:
        return value
    if findings:
        return "issues_found"
    return (result.get("summary") or {}).get("verdict", "unchecked")


def render_report(result, findings, output_dir):
    summary = result.get("summary") or {}
    lines = [
        "# 引用与事实核验报告",
        "",
        "## 概览",
        "",
        "- case_id：%s" % result.get("case_id"),
        "- status：%s" % result.get("status"),
        "- verdict：%s" % summary.get("verdict"),
        "- 统计：%s" % json.dumps(summary.get("counts", {}), ensure_ascii=False),
        "",
    ]
    for title, key in (("局限", "limitations"), ("已核验来源", "sources"),
                       ("不确定性", "uncertainty"), ("待人工复核", "pending_human")):
        items = result.get(key)
        if items:
            lines += ["## %s" % title, ""]
            lines += ["- %s" % (json.dumps(x, ensure_ascii=False) if isinstance(x, (dict, list)) else x) for x in items]
            lines += [""]
    lines += ["## 发现清单", ""]
    if not findings:
        lines += ["（暂无 finding；若审查仍在进行，status 应保持 partial）", ""]
    for f in findings:
        lines += [
            "### %s · %s · %s" % (f.get("id", "?"), f.get("category", "?"), f.get("severity", "?")),
            "",
            "- 判定：%s（%s）" % (f.get("verdict", "?"), f.get("finding_type", "?")),
            "- 位置：%s" % f.get("locator", "?"),
            "- 原文摘录：%s" % f.get("quote", ""),
            "- 转述：%s" % f.get("claim", ""),
            "- 依据：%s" % f.get("basis", ""),
            "- 来源：%s" % f.get("source_ref", ""),
            "- 建议：%s" % f.get("suggestion", ""),
            "- 复核状态：%s" % f.get("review_status", "needs_review"),
            "",
        ]
    write_text(os.path.join(output_dir, "report.md"), "\n".join(lines))


def cmd_finalize(input_dir, work_dir, output_dir):
    case = load_json(os.path.join(input_dir, "case.json"), {})
    if not isinstance(case, dict):
        case = {}
    case_id = case.get("case_id") or case.get("id") or "unknown"
    result = load_json(os.path.join(output_dir, "result.json"))
    if not isinstance(result, dict):
        result = build_skeleton(case_id, case)
    result.setdefault("case_id", case_id)

    findings, collect_errors = collect_findings(work_dir)
    meta = load_json(os.path.join(work_dir, "meta.json"), {})
    if not isinstance(meta, dict):
        meta = {}

    result["review_report"] = {"findings": findings}
    summary = result.setdefault("summary", {})
    summary["counts"] = compute_counts(findings)
    for key in ("sources", "uncertainty", "pending_human", "limitations", "errors", "degradations"):
        value = meta.get(key)
        if isinstance(value, list):
            result[key] = list(value)
    result["errors"] = list(result.get("errors") or []) + collect_errors
    summary["verdict"] = resolve_verdict(result, meta, findings)
    result["status"] = resolve_status(result, meta, findings)
    result.setdefault("evidence", {})["evidence_map"] = build_evidence_map(findings)
    result["evidence"]["integrity"] = {"checked": True, "mismatches": verify_quotes(findings, work_dir)}

    if not any(e.get("stage") == "finalize" for e in result.get("change_log", [])):
        result.setdefault("change_log", []).append(
            {"seq": len(result["change_log"]), "stage": "finalize", "summary": "合并 findings 并渲染 report"}
        )

    started = (result.get("runtime") or {}).get("started_at") or now_iso()
    result["runtime"] = {"started_at": started, "finished_at": now_iso(), "elapsed_sec": 0.0, "version": VERSION}

    write_json(os.path.join(output_dir, "result.json"), result)
    render_report(result, findings, output_dir)
    write_json(os.path.join(work_dir, "state.json"), {
        "case_id": case_id, "status": result["status"], "stage": "finalize", "findings": len(findings),
    })
    print("finalize: findings=%d status=%s verdict=%s" % (
        len(findings), result["status"], summary["verdict"],
    ))
    return 0


# --------------------------------------------------------------------------
# selftest：校验 result.json 契约与逐字 quote
# --------------------------------------------------------------------------
def run_checks(output_dir, work_dir):
    problems = []
    result = load_json(os.path.join(output_dir, "result.json"))
    if not isinstance(result, dict):
        return ["result.json 不存在或不是合法 JSON"]
    for key in ("case_id", "status", "sources", "uncertainty", "pending_human", "change_log"):
        if key not in result:
            problems.append("result.json 缺顶层字段：%s" % key)
    if result.get("status") not in STATUSES:
        problems.append("status 非法：%s" % result.get("status"))
    findings = (result.get("review_report") or {}).get("findings") or []
    for f in findings:
        fid = f.get("id", "?")
        for key in REQUIRED_FINDING_KEYS:
            if key not in f or f.get(key) in (None, ""):
                problems.append("%s 缺必填键或为空：%s" % (fid, key))
        if f.get("severity") not in SEVERITIES:
            problems.append("%s severity 非法：%s" % (fid, f.get("severity")))
        if f.get("category") not in CATEGORIES:
            problems.append("%s category 非法：%s" % (fid, f.get("category")))
    problems += verify_quotes(findings, work_dir)
    return problems


def cmd_selftest(output_dir, work_dir):
    problems = run_checks(output_dir, work_dir)
    for p in problems:
        print("FAIL: %s" % p)
    if problems:
        return 1
    print("selftest: OK")
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def print_help():
    print("usage: main.py {discover|scaffold|finalize|selftest} [--input DIR] [--output DIR] [--work DIR]")
    print("defaults: --input /input --output /app/output --work /app/work")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("--help", "-h", "help"):
        print_help()
        return 0
    parser = argparse.ArgumentParser(prog="main.py", description="citation-fact-audit Skill 工具（仅标准库）")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("discover", "扫描输入并抽取 PDF 文本"),
        ("scaffold", "落盘 partial result.json 骨架"),
        ("finalize", "合并 findings 并渲染 report.md"),
        ("selftest", "校验 result.json 契约"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--input", default=argparse.SUPPRESS)
        sp.add_argument("--output", default=argparse.SUPPRESS)
        sp.add_argument("--work", default=argparse.SUPPRESS)
    ns = parser.parse_args(argv)
    if ns.command == "discover":
        return cmd_discover(ns.input, ns.work)
    if ns.command == "scaffold":
        return cmd_scaffold(ns.input, ns.work, ns.output)
    if ns.command == "finalize":
        return cmd_finalize(ns.input, ns.work, ns.output)
    if ns.command == "selftest":
        return cmd_selftest(ns.output, ns.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
