#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研选题与创新性评估 Skill 的确定性脚手架（纯标准库）。

子命令：
  skeleton  写 result.json 骨架 + input_manifest.json（Phase 0，必须先跑）
  evidence  发现材料、抽 PDF 文字、刷新 sources（Phase 1）
  finalize  合并 fragments、契约校验、写 result.json 与 report.md（Phase 4）
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024
PDFTEXT_BIN = "pdftotext"
EXCERPT_CHARS = 300

TEXT_EXTS = {
    ".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".bib", ".ris", ".tex", ".log", ".rst",
}
PDF_EXTS = {".pdf"}
META_NAMES = {"case.json", "task.md", "result.json"}

REQUIRED_KEYS = [
    "case_id", "status", "deliverables", "output_contract",
    "research_question", "search_protocol", "prior_work", "novelty", "feasibility",
    "evidence_matrix", "conflicts", "synthesis", "sources", "uncertainty",
    "change_log", "pending_human", "errors", "degradations", "runtime",
]
DELIVERABLE_KEYS = ["research_question", "search_protocol", "prior_work", "novelty", "feasibility"]


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path):
    """容错读文本：utf-8-sig -> utf-8 -> gb18030 -> 降级。返回 (text, error)。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, str(exc)
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped_too_large"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "encoding_replaced"


def read_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json:%s" % exc


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def default_result(case_id, status="partial", output_contract=None):
    contract = output_contract if isinstance(output_contract, dict) else {}
    result = {
        "case_id": case_id,
        "status": status,
        "deliverables": {"result_json": "result.json", "report": "report.md"},
        "output_contract": contract,
        "research_question": None,
        "search_protocol": None,
        "prior_work": None,
        "novelty": None,
        "feasibility": None,
        "evidence_matrix": [],
        "conflicts": [],
        "synthesis": {"conclusions": [], "research_gaps": [], "boundary_statement": ""},
        "sources": [],
        "uncertainty": {"bias": [], "gaps": [], "unknowns": []},
        "change_log": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": now_iso(), "finished_at": None, "phases": {}},
    }
    for key in contract:
        if key not in result:
            result[key] = None
    return result


def resolve_case_id(case, input_dir):
    if isinstance(case, dict):
        for key in ("case_id", "id", "armiId"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    dig = hashlib.sha256(os.path.abspath(input_dir).encode("utf-8")).hexdigest()[:12]
    return "unresolved-" + dig


def cmd_skeleton(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(os.path.join(args.work, "fragments"), exist_ok=True)
    os.makedirs(os.path.join(args.work, "extracted"), exist_ok=True)

    case, case_err = None, None
    case_path = os.path.join(args.input, "case.json")
    if not os.path.isdir(args.input):
        case_id, status = "input_error", "input_error"
    elif os.path.isfile(case_path):
        case, case_err = read_json(case_path)
        case_id = resolve_case_id(case, args.input)
        status = "partial"
    else:
        case_id = resolve_case_id(None, args.input)
        status = "input_error"

    contract = case.get("output_contract") if isinstance(case, dict) else None
    result = default_result(case_id, status, contract)
    if case_err:
        result["degradations"].append("case.json read: %s" % case_err)
    if not os.path.isdir(args.input):
        result["errors"].append("input directory missing: %s" % args.input)
    elif not os.path.isfile(case_path):
        result["errors"].append("case.json missing in input directory")

    result["change_log"].append({"time": now_iso(), "action": "skeleton", "note": "partial skeleton written"})
    manifest = {
        "case_id": case_id,
        "input_dir": os.path.abspath(args.input),
        "output_dir": os.path.abspath(args.output),
        "work_dir": os.path.abspath(args.work),
        "case_json": {"path": case_path, "error": case_err},
        "output_contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else [],
        "created_at": now_iso(),
    }
    write_json(os.path.join(args.work, "input_manifest.json"), manifest)
    write_json(os.path.join(args.output, "result.json"), result)
    print("skeleton ok: case_id=%s status=%s contract_keys=%d" % (
        case_id, status, len(manifest["output_contract_keys"])))
    return 0


def scan_input(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name in META_NAMES:
                continue
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in PDF_EXTS:
                kind = "pdf"
            elif ext in TEXT_EXTS:
                kind = "text"
            else:
                kind = "other"
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            out.append({"path": path, "rel_path": rel, "kind": kind, "size": size})
    return out


def excerpt_file(path, chars=EXCERPT_CHARS):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(chars)
    except OSError:
        return None


def handle_pdf(rec, ext_dir, degradations):
    sid = rec["source_id"]
    txt_path = os.path.join(ext_dir, sid + ".txt")
    try:
        proc = subprocess.run(
            [PDFTEXT_BIN, "-layout", "-enc", "UTF-8", rec["path"], txt_path],
            capture_output=True, timeout=120)
    except FileNotFoundError:
        rec["status"] = "text_unavailable"
        rec["note"] = "pdftotext not found"
        degradations.append("pdftotext not found; PDF not extracted: %s" % rec["rel_path"])
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "text_unavailable"
        rec["note"] = "pdftotext error: %s" % exc
        degradations.append("pdftotext failed for %s: %s" % (rec["rel_path"], exc))
        return rec
    if proc.returncode != 0 or not os.path.isfile(txt_path) or os.path.getsize(txt_path) == 0:
        rec["status"] = "text_unavailable"
        rec["note"] = "pdftotext rc=%s" % proc.returncode
        degradations.append("pdftotext rc=%s for %s" % (proc.returncode, rec["rel_path"]))
        return rec
    rec["status"] = "text_ok"
    rec["text_path"] = txt_path
    rec["locator_prefix"] = rec["rel_path"]
    rec["pages"] = _count_pages(txt_path)
    rec["excerpt"] = excerpt_file(txt_path)
    return rec


def _count_pages(txt_path):
    try:
        with open(txt_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().count("\f") + 1
    except OSError:
        return None


def handle_text(rec, ext_dir, degradations):
    text, err = read_text(rec["path"])
    if text is None:
        rec["status"] = "read_error"
        rec["note"] = err
        degradations.append("read error %s: %s" % (rec["rel_path"], err))
        return rec
    sid = rec["source_id"]
    txt_path = os.path.join(ext_dir, sid + ".txt")
    try:
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        rec["status"] = "read_error"
        rec["note"] = "write extracted: %s" % exc
        degradations.append("write extracted failed %s: %s" % (rec["rel_path"], exc))
        return rec
    rec["status"] = "text_ok"
    rec["text_path"] = txt_path
    rec["locator_prefix"] = rec["rel_path"]
    rec["line_count"] = text.count("\n") + 1
    rec["excerpt"] = text[:EXCERPT_CHARS]
    if err:
        rec["note"] = err
        degradations.append("encoding degraded %s: %s" % (rec["rel_path"], err))
    return rec


def cmd_evidence(args):
    manifest, _ = read_json(os.path.join(args.work, "input_manifest.json"))
    if not manifest:
        manifest = {"case_id": "unknown", "input_dir": os.path.abspath(args.input)}
    input_dir = args.input if os.path.isdir(args.input) else manifest.get("input_dir", args.input)
    ext_dir = os.path.join(args.work, "extracted")
    os.makedirs(ext_dir, exist_ok=True)

    sources, degradations, readable = [], [], 0
    for idx, f in enumerate(scan_input(input_dir), 1):
        rec = {
            "source_id": "s%02d" % idx,
            "path": f["path"],
            "rel_path": f["rel_path"],
            "kind": f["kind"],
            "size": f["size"],
            "status": "unknown",
        }
        if f["size"] > MAX_FILE_BYTES:
            rec["status"] = "skipped_too_large"
            rec["note"] = "exceeds %d bytes" % MAX_FILE_BYTES
            degradations.append("skipped >2MB: %s" % f["rel_path"])
        elif f["kind"] == "pdf":
            rec = handle_pdf(rec, ext_dir, degradations)
        elif f["kind"] == "text":
            rec = handle_text(rec, ext_dir, degradations)
        else:
            rec["status"] = "unsupported"
            rec["note"] = "extension not in TEXT_EXTS/PDF_EXTS"
            degradations.append("unsupported extension: %s" % f["rel_path"])
        if rec["status"] == "text_ok":
            readable += 1
        sources.append(rec)

    inventory = {
        "case_id": manifest.get("case_id", "unknown"),
        "input_dir": os.path.abspath(input_dir),
        "scanned_at": now_iso(),
        "files": sources,
        "readable_count": readable,
        "total_count": len(sources),
    }
    write_json(os.path.join(args.work, "material_inventory.json"), inventory)

    result_path = os.path.join(args.output, "result.json")
    result, _ = read_json(result_path)
    if result is None:
        result = default_result(manifest.get("case_id", "unresolved"), "partial")
    result["sources"] = [_source_summary(s) for s in sources]
    for d in degradations:
        if d not in result.setdefault("degradations", []):
            result["degradations"].append(d)
    if readable == 0:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].setdefault("gaps", []).append("no readable material in input")
    result["runtime"]["phases"]["evidence"] = {"done_at": now_iso(), "files": len(sources), "readable": readable}
    write_json(result_path, result)

    print("evidence ok: files=%d readable=%d" % (len(sources), readable))
    return 0


def _source_summary(s):
    keep = ["source_id", "rel_path", "kind", "size", "status", "locator_prefix",
            "pages", "line_count", "note", "excerpt"]
    return {k: s.get(k) for k in keep if k in s}


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    result, _ = read_json(result_path)
    manifest, _ = read_json(os.path.join(args.work, "input_manifest.json"))
    if result is None:
        result = default_result(
            (manifest or {}).get("case_id", "unresolved"), "partial")

    frag_dir = os.path.join(args.work, "fragments")
    merged = 0
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if not name.endswith(".json"):
                continue
            frag, ferr = read_json(os.path.join(frag_dir, name))
            if ferr or not isinstance(frag, dict):
                result.setdefault("errors", []).append(
                    "bad fragment %s: %s" % (name, ferr or "not a JSON object"))
                continue
            for key, val in frag.items():
                result[key] = val
                merged += 1

    result["change_log"].append({
        "time": now_iso(), "action": "finalize", "merged_keys": merged})
    result["runtime"]["finished_at"] = now_iso()

    problems = _validate(result, manifest)
    for p in problems:
        result.setdefault("errors", []).append("validation: " + p)

    if problems:
        if result["status"] not in ("input_error", "insufficient_evidence"):
            result["status"] = "partial"
        write_json(result_path, result)
        write_report(args.output, result)
        print("finalize FAILED: %d problems" % len(problems), file=sys.stderr)
        for p in problems:
            print(" - " + p, file=sys.stderr)
        return 2

    if _is_complete(result):
        if result["status"] not in ("input_error", "insufficient_evidence"):
            result["status"] = "ok"
    elif result["status"] not in ("input_error", "insufficient_evidence"):
        result["status"] = "partial"

    write_json(result_path, result)
    write_report(args.output, result)
    print("finalize ok: status=%s merged_keys=%d" % (result["status"], merged))
    return 0


def _validate(result, manifest):
    problems = []
    for key in REQUIRED_KEYS:
        if key not in result:
            problems.append("missing top-level key: %s" % key)
    contract_keys = (manifest or {}).get("output_contract_keys", [])
    for key in contract_keys:
        if key not in result:
            problems.append("missing output_contract key: %s" % key)
    text = json.dumps(result, ensure_ascii=False, allow_nan=False) if _is_finite(result) else "NaN"
    if "NaN" in text or "Infinity" in text or "-Infinity" in text:
        problems.append("result contains non-finite number")
    for i, item in enumerate(result.get("evidence_matrix") or []):
        if not isinstance(item, dict):
            problems.append("evidence_matrix[%d] not object" % i)
            continue
        if not str(item.get("locator") or "").strip():
            problems.append("evidence_matrix[%d] empty locator" % i)
        if not str(item.get("quote") or "").strip():
            problems.append("evidence_matrix[%d] empty quote" % i)
    return problems


def _is_finite(obj):
    try:
        json.dumps(obj, allow_nan=False)
        return True
    except ValueError:
        return False


def _is_complete(result):
    for key in DELIVERABLE_KEYS:
        if not result.get(key):
            return False
    conclusions = (result.get("synthesis") or {}).get("conclusions")
    return bool(conclusions)


def _fmt_item(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def write_report(out_dir, result):
    lines = ["# 科研选题与创新性评估报告", "",
             "- case_id: %s" % result.get("case_id"),
             "- status: %s" % result.get("status"), ""]
    rq = result.get("research_question")
    if isinstance(rq, dict):
        lines += ["## 研究问题", "",
                  str(rq.get("testable_statement") or rq.get("statement") or "（未提供）"), ""]
    sections = [("search_protocol", "检索协议"), ("prior_work", "已有工作比较"),
                ("novelty", "创新点识别"), ("feasibility", "可行性判断")]
    for key, title in sections:
        val = result.get(key)
        if val:
            lines += ["## %s" % title, "", _fmt_item(val), ""]
    syn = result.get("synthesis") or {}
    for label, field in (("综合结论", "conclusions"), ("研究空白", "research_gaps")):
        items = syn.get(field) or []
        if items:
            lines += ["## %s" % label, ""]
            lines += ["- %s" % c for c in items]
            lines += [""]
    if syn.get("boundary_statement"):
        lines += ["## 边界说明", "", syn["boundary_statement"], ""]
    un = result.get("uncertainty") or {}
    unknowns = (un.get("unknowns") or []) + (un.get("gaps") or [])
    if unknowns:
        lines += ["## 不确定性", ""]
        lines += ["- %s" % u for u in unknowns]
        lines += [""]
    em = result.get("evidence_matrix") or []
    lines += ["## 证据矩阵（%d 条）" % len(em), ""]
    for i, e in enumerate(em, 1):
        lines.append("%d. [%s] %s" % (i, e.get("source_id", "?"), e.get("locator", "?")))
        lines.append("   > %s" % str(e.get("quote", "")).replace("\n", " "))
    lines += ["", "## 待人工确认", ""]
    ph = result.get("pending_human") or []
    lines += ["- %s" % p for p in ph] if ph else ["（无）"]
    lines += [""]
    write_text(os.path.join(out_dir, "report.md"), "\n".join(lines))


def build_parser():
    parser = argparse.ArgumentParser(description="科研选题评估 Skill 脚手架")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("skeleton", "evidence", "finalize"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=DEFAULT_INPUT)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
        sp.add_argument("--work", default=DEFAULT_WORK)
        sp.set_defaults(func=globals()["cmd_" + name])
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
