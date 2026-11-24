#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robust entry point for the academic-bilingual-translator Skill.

Subcommands:
  init      Discover inputs and land a minimal legal result.json skeleton.
  extract   Build source inventories (numbers, citations, terms, segments).
  checks    Compare deliverables against source inventories; write report.
  add       Append one item to a list field of result.json (incremental).
  finalize  Set final status and validate the output contract.
  selftest  Run the robustness self-test (empty/bad/missing/oversized).

Robustness contract: empty input, bad JSON, bad encoding, missing parameters
and oversized files never crash the process. They exit with code 0 and produce
a legal result with an honest status plus recorded degradations. Only stdlib.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_BYTES = 8 * 1024 * 1024      # skip text files larger than this
MAX_PDF_BYTES = 80 * 1024 * 1024      # PDFs may be larger; still bounded
TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
            ".tex", ".xml", ".html", ".htm", ".yaml", ".yml", ".rst", ".log"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
           ".rtf", ".odt", ".epub"}
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
FINAL_STATUSES = {"success", "partial", "failed",
                  "insufficient_evidence", "input_error"}
# Task metadata and our own artifacts must never be treated as translatable
# source text or target deliverable text.
METADATA_NAMES = {
    "case.json", "task.md", "GITHUB_URL.txt",
    "case_brief.json", "source_map.json", "number_inventory.json",
    "citation_inventory.json", "term_glossary_scaffold.json",
    "consistency_report.json", "result.json", "glossary.json",
    "alignment.json",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dir(preferred):
    """Create a directory, falling back to a writable location on failure."""
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred, None
    except OSError as e:
        fb = tempfile.gettempdir()
        try:
            os.makedirs(fb, exist_ok=True)
            return fb, f"cannot create {preferred}: {e}; using {fb}"
        except OSError as e2:
            return ".", f"cannot create {preferred} or tempdir: {e2}"


def atomic_write_text(path, text):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Return (text|None, encoding, errors). Never raises."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, None, [f"stat failed: {e}"]
    if size > max_bytes:
        return None, None, [f"size {size} > limit {max_bytes}; skipped"]
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, None, [f"read failed: {e}"]
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc, []
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1", ["fell back to latin-1"]


def _parse_json(text, errs):
    try:
        return json.loads(text)
    except Exception as e:
        errs.append(f"json.loads failed: {type(e).__name__}: {e}")
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i = text.find(open_c)
        j = text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception as e2:
                errs.append(f"salvage {open_c}{close_c} failed: {type(e2).__name__}")
    return None


def read_json_any(path, max_bytes=MAX_TEXT_BYTES):
    """Return (obj|None, errors). Tries encoding fallbacks and bracket salvage."""
    text, enc, errs = read_text(path, max_bytes)
    if text is None:
        return None, errs
    if enc == "latin-1":
        errs.append("decoded with latin-1 fallback; content may be non-text")
    return _parse_json(text, errs), errs


def discover_inputs(input_dir):
    """Return (files list, errors). Never raises."""
    files, errs = [], []
    if not os.path.isdir(input_dir):
        return files, [f"input dir {input_dir} not found"]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        return files, [f"listdir failed: {e}"]
    for name in names:
        if name.startswith("."):
            continue
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        try:
            size = os.path.getsize(p)
        except OSError as e:
            files.append({"path": p, "name": name, "ext": ext, "size": 0,
                          "kind": "unknown", "skipped": True,
                          "reason": f"stat failed: {e}"})
            continue
        kind = "text" if ext in TEXT_EXT else ("document" if ext in DOC_EXT else "other")
        limit = MAX_PDF_BYTES if ext == ".pdf" else MAX_TEXT_BYTES
        if size > limit:
            files.append({"path": p, "name": name, "ext": ext, "size": size,
                          "kind": kind, "skipped": True,
                          "reason": f"size {size} > limit {limit}"})
        else:
            files.append({"path": p, "name": name, "ext": ext, "size": size,
                          "kind": kind, "skipped": False, "reason": None})
    return files, errs


def get_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "id", "caseId"):
            v = case.get(k)
            if v not in (None, ""):
                return v
    return None


def get_output_contract(case):
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        return dict(case["output_contract"])
    return {
        "deliverables": "array of {path,format,title,description}",
        "fact_source_map": "array of {item,kind,value,source,quote,note}",
        "version_boundary": "object with modifications/human_confirm/material_gaps/anomalies/editable",
    }


def make_skeleton(case, case_id, status="partial"):
    contract = get_output_contract(case)
    sk = {"case_id": case_id}
    for field in contract:
        if field == "deliverables":
            sk[field] = []
        elif field == "fact_source_map":
            sk[field] = []
        elif field == "version_boundary":
            sk[field] = {"modifications": [], "human_confirm": [],
                         "material_gaps": [], "anomalies": [], "editable": True}
        else:
            sk[field] = None
    sk["status"] = status
    sk["sources"] = []
    sk["uncertainty"] = []
    sk["pending_human"] = []
    sk["errors"] = []
    sk["degradations"] = []
    sk["change_log"] = []
    sk["runtime"] = {"started_at": now_iso(), "ended_at": None, "seconds": None}
    return sk


def append_change(result, action, key, detail):
    result.setdefault("change_log", []).append(
        {"time": now_iso(), "action": action, "key": key, "detail": detail})


def _load_case(input_dir):
    case, _ = read_json_any(os.path.join(input_dir, "case.json"))
    return case


def _touch_result(output_dir, work_dir, entry):
    p = os.path.join(output_dir, "result.json")
    if not os.path.exists(p):
        return
    result, _ = read_json_any(p)
    if result is None:
        return
    append_change(result, entry["action"], entry["key"], entry["detail"])
    atomic_write_json(p, result)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------
def cmd_init(args):
    files, errs = discover_inputs(args.input)
    case_path = os.path.join(args.input, "case.json")
    case, case_errs = read_json_any(case_path)
    case_id = get_case_id(case)
    has_case_file = any(f["name"] == "case.json" and not f["skipped"] for f in files)
    if case is None:
        status = "input_error" if has_case_file else "insufficient_evidence"
    else:
        status = "partial"
    result = make_skeleton(case, case_id, status)
    result["output_dir_actual"] = args.output
    result["work_dir_actual"] = args.work
    degradations = [{"kind": "input", "detail": e} for e in errs + case_errs]
    for f in files:
        role = "case_definition" if f["name"] in ("case.json", "task.md") else "material"
        result["sources"].append({"path": f["path"], "type": f["kind"], "role": role,
                                 "size": f["size"], "skipped": f["skipped"],
                                 "reason": f["reason"]})
        if f["skipped"]:
            degradations.append({"kind": "oversized_or_unreadable",
                                 "detail": f"{f['name']}: {f['reason']}"})
    if getattr(args, "output_note", None):
        degradations.append({"kind": "output_dir_fallback", "detail": args.output_note})
    if getattr(args, "work_note", None):
        degradations.append({"kind": "work_dir_fallback", "detail": args.work_note})
    result["degradations"] = degradations
    if case is None and case_errs:
        result["errors"] = [{"stage": "case.json", "detail": e} for e in case_errs]
    if not files:
        result["uncertainty"].append(
            {"item": "input_materials", "reason": "no input files found",
             "resolution": "pending_human"})
        result["pending_human"].append("确认输入目录 /input 是否为空或路径是否正确")
    if case is None and has_case_file:
        result["uncertainty"].append(
            {"item": "case_definition", "reason": "case.json unreadable or invalid",
             "resolution": "pending_human"})
    append_change(result, "init", "result.json",
                  f"landed skeleton; {len(files)} files discovered")
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    brief = {"case_id": case_id,
             "contract_fields": list(get_output_contract(case).keys()),
             "files": files, "degradations": degradations,
             "status": status}
    atomic_write_json(os.path.join(args.work, "case_brief.json"), brief)
    print(json.dumps({"ok": True, "status": status, "case_id": case_id,
                      "files": len(files),
                      "result": os.path.join(args.output, "result.json")},
                     ensure_ascii=False))
    return 0


def _dedup_terms(terms):
    seen, out = set(), []
    for t in terms:
        key = (t.get("abbr") or "") + "|" + (t.get("expansion") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(t, translation=None,
                        note="scaffold; lock translation in glossary.json"))
    return out


def _iter_material_texts(input_dir, work_dir):
    """Yield (path, text) for translatable source material text files only.

    Excludes task metadata and our own artifacts so inventories and checks are
    not contaminated by case definitions, file sizes or timestamps. PDF text is
    expected to have been extracted by the platform tool into work/source_text/.
    """
    yielded = set()
    if input_dir and os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            if name.startswith(".") or name in METADATA_NAMES:
                continue
            p = os.path.join(input_dir, name)
            if not os.path.isfile(p):
                continue
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            text, enc, rd = read_text(p, MAX_TEXT_BYTES)
            if text is None:
                continue
            yielded.add(p)
            yield p, text
    st = os.path.join(work_dir, "source_text") if work_dir else None
    if st and os.path.isdir(st):
        for name in sorted(os.listdir(st)):
            if name.startswith("."):
                continue
            p = os.path.join(st, name)
            if not os.path.isfile(p):
                continue
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            if p in yielded:
                continue
            text, enc, rd = read_text(p, MAX_TEXT_BYTES)
            if text is None:
                continue
            yielded.add(p)
            yield p, text


def _iter_target_texts(output_dir):
    """Yield (path, text) for target deliverable text files (excluding metadata)."""
    if not output_dir or not os.path.isdir(output_dir):
        return
    for name in sorted(os.listdir(output_dir)):
        if name.startswith(".") or name in METADATA_NAMES:
            continue
        p = os.path.join(output_dir, name)
        if not os.path.isfile(p):
            continue
        if os.path.splitext(name)[1].lower() not in TEXT_EXT:
            continue
        text, enc, rd = read_text(p, MAX_TEXT_BYTES)
        if text is None:
            continue
        yield p, text


def cmd_extract(args):
    files, errs = discover_inputs(args.input)
    degradations = [{"kind": "input", "detail": e} for e in errs]
    segments, numbers, citations, terms = [], [], [], []
    for path, text in _iter_material_texts(args.input, args.work):
        fname = os.path.basename(path)
        segments.extend(checks.segment_text(text, fname))
        numbers.extend(checks.extract_numbers(text))
        citations.extend(checks.extract_citations(text))
        terms.extend(checks.extract_terms(text))
    for f in files:
        if f["kind"] == "document" and not f["skipped"]:
            degradations.append({"kind": "unparsed_document",
                "detail": (f"{f['name']}: binary document; extract text with the "
                           "platform tool into work/source_text/, then re-run extract")})
    atomic_write_json(os.path.join(args.work, "source_map.json"),
                      {"segments": segments})
    atomic_write_json(os.path.join(args.work, "number_inventory.json"),
                      {"numbers": numbers})
    atomic_write_json(os.path.join(args.work, "citation_inventory.json"),
                      {"citations": citations})
    atomic_write_json(os.path.join(args.work, "term_glossary_scaffold.json"),
                      {"terms": _dedup_terms(terms)})
    _touch_result(args.output, args.work, {
        "action": "extract", "key": "inventories",
        "detail": f"{len(segments)} segments, {len(numbers)} numbers, "
                  f"{len(citations)} citations, {len(terms)} terms"})
    print(json.dumps({"ok": True, "segments": len(segments),
                      "numbers": len(numbers), "citations": len(citations),
                      "terms": len(terms), "degradations": len(degradations)},
                     ensure_ascii=False))
    return 0


def cmd_checks(args):
    report = {"checks": ["numbers", "citations", "structure"],
              "errors": [], "warnings": [], "degradations": []}
    src_texts = list(_iter_material_texts(args.input, args.work))
    tgt_texts = list(_iter_target_texts(args.output))
    if not src_texts:
        report["degradations"].append("no source text found for checks")
    if not tgt_texts:
        report["degradations"].append("no target deliverables found for checks")
    src_all = "\n".join(t for _, t in src_texts)
    tgt_all = "\n".join(t for _, t in tgt_texts)
    num = checks.compare_numbers(src_all, tgt_all)
    cite = checks.compare_citations(src_all, tgt_all)
    struct = checks.compare_structure(src_all, tgt_all)
    report["number_check"] = num
    report["citation_check"] = cite
    report["structure_check"] = struct
    if num["missing_in_target"]:
        report["warnings"].append({"check": "numbers",
            "detail": f"{len(num['missing_in_target'])} source numbers absent from target",
            "samples": num["missing_in_target"][:20]})
    if num["added_in_target"]:
        report["warnings"].append({"check": "numbers",
            "detail": f"{len(num['added_in_target'])} target numbers not found in source",
            "samples": num["added_in_target"][:20]})
    if cite["missing_in_target"]:
        report["warnings"].append({"check": "citations",
            "detail": f"{len(cite['missing_in_target'])} source citations absent",
            "samples": cite["missing_in_target"][:20]})
    if struct["mismatched_types"]:
        report["warnings"].append({"check": "structure",
            "detail": "segment type counts differ",
            "samples": struct["mismatched_types"]})
    atomic_write_json(os.path.join(args.work, "consistency_report.json"), report)
    _touch_result(args.output, args.work, {
        "action": "checks", "key": "consistency_report.json",
        "detail": f"warnings={len(report['warnings'])}, errors={len(report['errors'])}"})
    print(json.dumps({"ok": True, "warnings": len(report["warnings"]),
                      "errors": len(report["errors"]),
                      "report": os.path.join(args.work, "consistency_report.json")},
                     ensure_ascii=False))
    return 0


def cmd_add(args):
    p = os.path.join(args.output, "result.json")
    result, _ = read_json_any(p)
    if result is None:
        result = make_skeleton(_load_case(args.input), None, "partial")
    if not isinstance(result.get(args.field), list):
        result[args.field] = []
    try:
        item = json.loads(args.item)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad --item JSON: {e}"},
                         ensure_ascii=False))
        return 0
    result[args.field].append(item)
    summary = str(item.get("path") or item.get("item") or list(item)[:3]) \
        if isinstance(item, dict) else str(item)[:80]
    append_change(result, "add", args.field, summary)
    atomic_write_json(p, result)
    print(json.dumps({"ok": True, "field": args.field,
                      "count": len(result[args.field])}, ensure_ascii=False))
    return 0


def _elapsed_seconds(iso):
    try:
        st = datetime.fromisoformat(iso)
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - st.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None


def cmd_finalize(args):
    p = os.path.join(args.output, "result.json")
    result, errs = read_json_any(p)
    if result is None:
        case, case_errs = read_json_any(os.path.join(args.input, "case.json"))
        result = make_skeleton(case, get_case_id(case), args.status)
        result["errors"] = [{"stage": "finalize", "detail": e}
                            for e in errs + case_errs]
    result["status"] = args.status
    rt = result.get("runtime")
    if isinstance(rt, dict):
        rt["ended_at"] = now_iso()
        sec = _elapsed_seconds(rt.get("started_at"))
        if sec is not None:
            rt["seconds"] = round(sec, 1)
    contract = get_output_contract(_load_case(args.input))
    missing_fields = [f for f in contract if f not in result]
    for f in missing_fields:
        if f == "deliverables":
            result[f] = []
        elif f == "fact_source_map":
            result[f] = []
        elif f == "version_boundary":
            result[f] = {"modifications": [], "human_confirm": [],
                         "material_gaps": [], "anomalies": [], "editable": True}
        else:
            result[f] = None
    for f, default in (("case_id", None), ("status", "partial"),
                       ("sources", []), ("uncertainty", []),
                       ("pending_human", []), ("change_log", [])):
        if f not in result:
            result[f] = default
    append_change(result, "finalize", "status", args.status)
    atomic_write_json(p, result)
    missing_deliv = []
    for d in result.get("deliverables", []):
        if isinstance(d, dict) and d.get("path"):
            pp = d["path"]
            if not os.path.isabs(pp):
                pp = os.path.join(args.output, pp)
            if not os.path.exists(pp):
                missing_deliv.append(d["path"])
    print(json.dumps({"ok": True, "status": args.status,
                      "missing_contract_fields": missing_fields,
                      "missing_deliverable_files": missing_deliv},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    ap = argparse.ArgumentParser(
        prog="main.py", description="academic-bilingual-translator robust entry",
        parents=[common])
    sub = ap.add_subparsers(dest="cmd")
    sub.required = False
    sub.add_parser("init", help="discover inputs and land result skeleton",
                   parents=[common])
    sub.add_parser("extract", help="build source inventories",
                   parents=[common])
    sub.add_parser("checks", help="run consistency checks",
                   parents=[common])
    p_fin = sub.add_parser("finalize", help="set final status",
                           parents=[common])
    p_fin.add_argument("--status", default="partial",
                       choices=sorted(FINAL_STATUSES))
    p_add = sub.add_parser("add", help="append one item to a list field",
                           parents=[common])
    p_add.add_argument("--field", required=True)
    p_add.add_argument("--item", required=True)
    sub.add_parser("selftest", help="run robustness self-test",
                   parents=[common])

    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        if e.code in (0, None):
            return 0
        print(json.dumps({"ok": False,
                          "error": "invalid arguments; see --help"},
                         ensure_ascii=False))
        return 0
    if args.cmd is None:
        args.cmd = "init"

    args.output, args.output_note = ensure_dir(args.output)
    args.work, args.work_note = ensure_dir(args.work)

    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "extract":
        return cmd_extract(args)
    if args.cmd == "checks":
        return cmd_checks(args)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    if args.cmd == "selftest":
        import selftest
        return selftest.run()
    print(json.dumps({"ok": False, "error": f"unknown command {args.cmd}"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # last-resort: never leave a traceback to the Agent
        print(json.dumps({"ok": False, "fatal": type(e).__name__,
                          "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
