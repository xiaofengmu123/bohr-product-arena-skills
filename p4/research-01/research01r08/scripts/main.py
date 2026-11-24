#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deep-research skill runtime helper (standard library only).

Subcommands
-----------
discover   print the input inventory as JSON
skeleton   write an early valid result.json + run_state.json
merge      deep-merge a payload JSON into result.json (incremental update)
finalize   assemble result.json, report.md, evidence_map.json and copy work notes
selfcheck  validate result.json and report six-point + rubric coverage

Common options (defaults match the runtime contract):
  --input INPUT_DIR    default /input
  --output OUTPUT_DIR  default /app/output
  --work WORK_DIR      default /app/work

Run from the skill root (the directory containing SKILL.md). All paths are
resolved with os.path, so the current working directory does not matter.
"""

import argparse
import copy
import json
import os
import re
import shutil
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

MAX_TEXT_BYTES = 2 * 1024 * 1024

MATERIAL_EXTS = (
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
    ".htm", ".tex", ".bib", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".docx", ".pptx", ".xlsx", ".rtf",
)

WORK_NOTES = ("decomposition.md", "search_plan.md", "synthesis.md")


# --------------------------------------------------------------------------
# time / fs helpers
# --------------------------------------------------------------------------

def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def epoch_now():
    return int(time.time())


def ensure_dir(path):
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def print_json(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    except (UnicodeEncodeError, UnicodeDecodeError):
        sys.stdout.write(json.dumps(obj, ensure_ascii=True, indent=2) + "\n")


def read_bytes(path, max_bytes=None, degs=None):
    try:
        with open(path, "rb") as f:
            data = f.read(None if max_bytes is None else max_bytes + 1)
    except OSError as e:
        if degs is not None:
            degs.append("cannot read %s: %s" % (path, e))
        return b""
    if max_bytes is not None and len(data) > max_bytes:
        if degs is not None:
            degs.append("truncated read of %s at %d bytes" % (path, max_bytes))
        data = data[:max_bytes]
    return data


def decode_text(data, path="", degs=None):
    try:
        return data.decode("utf-8-sig")
    except (UnicodeDecodeError, LookupError):
        pass
    if degs is not None:
        degs.append("non-UTF-8 text in %s decoded with fallback" % path)
    for enc in ("gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("latin-1", errors="replace")


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    errs, degs = [], []
    if not path or not os.path.isfile(path):
        return "", ["missing file: %s" % str(path)], []
    data = read_bytes(path, max_bytes, degs)
    return decode_text(data, path, degs), errs, degs


def load_json(path):
    obj, errs, degs = None, [], []
    if not path or not os.path.isfile(path):
        return None, ["missing file: %s" % str(path)], []
    data = read_bytes(path, MAX_TEXT_BYTES, degs)
    text = decode_text(data, path, degs)
    try:
        obj = json.loads(text)
    except Exception as e:
        errs.append("bad JSON in %s: %s" % (path, e))
    return obj, errs, degs


def write_json(path, obj):
    ensure_dir(os.path.dirname(path))
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return []
    except OSError as e:
        return ["cannot write %s: %s" % (path, e)]


def write_text(path, text):
    ensure_dir(os.path.dirname(path))
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return []
    except OSError as e:
        return ["cannot write %s: %s" % (path, e)]


# --------------------------------------------------------------------------
# input discovery / contract
# --------------------------------------------------------------------------

def discover_input(input_dir):
    inv = {
        "exists": False, "input_dir": input_dir, "total_files": 0,
        "files": [], "materials": [], "case_json": None, "task_md": None,
    }
    if not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = -1
            entry = {"path": p, "rel": rel, "bytes": sz}
            inv["files"].append(entry)
            inv["total_files"] += 1
            low = fn.lower()
            if low == "case.json":
                inv["case_json"] = p
            elif low == "task.md":
                inv["task_md"] = p
            elif low.endswith(MATERIAL_EXTS):
                inv["materials"].append(entry)
    return inv


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for k in ("case_id", "caseId", "id", "armiId"):
        v = obj.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v:
            return v
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, dict) and v:
            return str(v.get("value") or v.get("id") or list(v.values())[0] if v else "")
    return ""


def build_contract(case_obj):
    """Echo the evaluator's output_contract so its fields are never dropped."""
    if isinstance(case_obj, dict):
        for k in ("output_contract", "output_schema", "result_schema"):
            v = case_obj.get(k)
            if isinstance(v, dict) and v:
                return copy.deepcopy(v)
            if isinstance(v, list) and v:
                return {str(x): None for x in v if isinstance(x, (str, int, float))}
    return {}


def empty_rubric_coverage():
    return {
        "six_key_points": {
            "decomposition": False,
            "search_coverage": False,
            "evidence_backlink": False,
            "conflict_evidence": False,
            "synthesis": False,
            "research_gap": False,
        },
        "scoring_dimensions": {
            "actual_effect_40": False,
            "use_gain_25": False,
            "innovation_15": False,
            "reliability_10": False,
            "ecosystem_10": False,
        },
        "metrics": {
            "subquestions": 0,
            "conclusions": 0,
            "evidence_rows": 0,
            "backlink_rate": 0.0,
            "locator_quote_rate": 0.0,
            "conflicts": 0,
        },
    }


def new_skeleton(case_id, contract, note=""):
    r = {}
    for k in (contract or {}):
        r[k] = None
    core = {
        "case_id": case_id or "",
        "status": STATUS_PARTIAL,
        "research_product": {
            "type": None, "question": "", "summary": "", "conclusions": [],
        },
        "evidence_matrix": [],
        "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
        "sources": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "rubric_coverage": empty_rubric_coverage(),
        "runtime_seconds": 0,
    }
    r.update(core)
    r["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    return r


def deep_merge(base, patch):
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# --------------------------------------------------------------------------
# subcommand helpers
# --------------------------------------------------------------------------

def add_common(parser, suppress=False):
    """Register --input/--output/--work on a parser.

    With suppress=True (subparsers) the defaults are SUPPRESS so a value parsed
    by the main parser is never overwritten by the subparser defaults. This
    makes both ``main.py skeleton --input X`` and ``main.py --input X skeleton``
    work identically.
    """
    def val(name, fallback):
        return argparse.SUPPRESS if suppress else os.environ.get(name, fallback)
    parser.add_argument("--input", default=val("INPUT_DIR", "/input"),
                        help="input directory (default /input)")
    parser.add_argument("--output", default=val("OUTPUT_DIR", "/app/output"),
                        help="output directory (default /app/output)")
    parser.add_argument("--work", default=val("WORK_DIR", "/app/work"),
                        help="work directory (default /app/work)")


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def load_run_state(args):
    state, _, _ = load_json(work_path(args, "run_state.json"))
    return state if isinstance(state, dict) else None


def cmd_discover(args):
    print_json(discover_input(args.input))


def cmd_skeleton(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    inv = discover_input(args.input)
    case_obj, errs, degs = (None, [], [])
    if inv.get("case_json"):
        case_obj, errs, degs = load_json(inv["case_json"])
    case_id = extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": list(errs), "degradations": list(degs)}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = load_json(result_path)
        if not isinstance(existing, dict):
            result = new_skeleton(case_id, contract, note="repaired unreadable result.json")
            summary["wrote"] = not bool(write_json(result_path, result))
    else:
        note = "skeleton before research begins"
        if not inv["exists"] or inv["total_files"] == 0:
            note = "empty input directory; no materials found"
        result = new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            result["status"] = STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["wrote"] = not bool(write_json(result_path, result))

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_at": epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
    }
    summary["errors"].extend(write_json(work_path(args, "run_state.json"), state))
    print_json(summary)


def cmd_merge(args):
    if not args.payload:
        print_json({"ok": False, "errors": ["--payload required"]})
        return
    payload, errs, degs = load_json(args.payload)
    if not isinstance(payload, dict):
        print_json({"ok": False, "errors": errs or ["payload not a JSON object"],
                    "degradations": degs})
        return
    result_path = out_path(args, "result.json")
    result, e2, d2 = load_json(result_path)
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        contract = state.get("contract") if isinstance(state, dict) else {}
        case_id = (state.get("case_id") if isinstance(state, dict) else "") \
            or extract_case_id(payload)
        result = new_skeleton(case_id, contract, note="created during merge")
        e2 = ["result.json missing; created during merge"]
    result = deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(write_json(result_path, result))
    print_json({"ok": not errs, "errors": errs, "degradations": degs + d2})


def render_report(result):
    rp = result.get("research_product") or {}
    unc = result.get("uncertainty") or {}
    lines = ["# 深度研究报告", "",
             "- case_id: `%s`" % (result.get("case_id") or ""),
             "- status: `%s`" % (result.get("status") or ""),
             "- 研究类型: `%s`" % (rp.get("type") or ""), "",
             "## 研究问题", "", str(rp.get("question") or "（未提供）"), "",
             "## 综合结论", "", str(rp.get("summary") or "（未提供）"), "",
             "## 结论清单", ""]
    for c in (rp.get("conclusions") or []):
        lines.append("- **%s** [%s]: %s"
                     % (c.get("id"), c.get("strength", "conjectural"), c.get("text")))
        lines.append("  - 证据: %s" % ", ".join(c.get("evidence_ids") or [] or ["（无）"]))
    lines += ["", "## 冲突与不确定性", ""]
    for x in (unc.get("conflicts") or []):
        lines.append("- 冲突 %s: `%s` 与 `%s` 并列，解决：%s"
                     % (x.get("id", "?"), x.get("source_a", "?"),
                        x.get("source_b", "?"), x.get("resolution", "?")))
    for g in (unc.get("gaps") or []):
        lines.append("- 空白: %s" % g)
    lines += ["", "## 待人工确认", ""]
    for p in (result.get("pending_human") or []):
        lines.append("- %s" % p)
    lines += ["", "## 限制", ""]
    for l in (result.get("limitations") or []):
        lines.append("- %s" % l)
    lines += [""]
    return "\n".join(lines)


def cmd_finalize(args):
    result_path = out_path(args, "result.json")
    result, errs, degs = load_json(result_path)
    if not isinstance(result, dict):
        result = new_skeleton("", {}, note="finalize created empty skeleton")
        errs.append("result.json missing; created empty skeleton")
    state = load_run_state(args)
    if isinstance(state, dict) and state.get("started_at"):
        try:
            result["runtime_seconds"] = max(0, epoch_now() - int(state["started_at"]))
        except (TypeError, ValueError):
            result.setdefault("runtime_seconds", 0)
    else:
        result.setdefault("runtime_seconds", 0)

    copied = []
    for name in WORK_NOTES:
        src = work_path(args, name)
        if os.path.isfile(src):
            try:
                shutil.copyfile(src, out_path(args, name))
                copied.append(name)
            except OSError as e:
                errs.append("cannot copy %s: %s" % (name, e))

    emap = {
        "case_id": result.get("case_id") or "",
        "conclusions": (result.get("research_product") or {}).get("conclusions") or [],
        "evidence_matrix": result.get("evidence_matrix") or [],
        "sources": result.get("sources") or [],
        "uncertainty": result.get("uncertainty") or {},
    }
    errs.extend(write_json(out_path(args, "evidence_map.json"), emap))
    errs.extend(write_text(out_path(args, "report.md"), render_report(result)))

    result.setdefault("change_log", []).append({
        "ts": iso_now(), "action": "finalize",
        "detail": "copied notes: %s" % ", ".join(copied or ["(none)"]),
    })
    errs.extend(write_json(result_path, result))
    print_json({"ok": not errs, "errors": errs, "degradations": degs,
                "copied": copied})


def compute_coverage(result, texts, contract):
    rp = result.get("research_product") or {}
    conclusions = [c for c in (rp.get("conclusions") or []) if isinstance(c, dict)]
    em = [e for e in (result.get("evidence_matrix") or []) if isinstance(e, dict)]
    unc = result.get("uncertainty") or {}
    conflicts = [x for x in (unc.get("conflicts") or []) if isinstance(x, dict)]
    gaps = unc.get("gaps") or []
    ph = result.get("pending_human") or []

    ev_ids = {e.get("id") for e in em if e.get("id")}
    backlink_ok = sum(
        1 for c in conclusions
        if c.get("evidence_ids") and all(i in ev_ids for i in c["evidence_ids"]))
    backlink_rate = (backlink_ok / len(conclusions)) if conclusions else 0.0

    loc_ok = sum(1 for e in em if e.get("locator") and e.get("quote"))
    loc_rate = (loc_ok / len(em)) if em else 0.0

    conflict_covered = all(
        x.get("source_a") and x.get("source_b") and x.get("resolution")
        for x in conflicts)

    decomp_text = texts.get("decomposition.md", "")
    sq_ids = re.findall(r"SQ\d+", decomp_text)
    decomp_ok = len(set(sq_ids)) >= 2 and bool(
        re.search(r"依赖|→|->|depends|前置", decomp_text))

    sp_text = texts.get("search_plan.md", "")
    search_ok = all(k in sp_text for k in ("顺序", "停止", "来源"))

    synthesis_ok = bool(conclusions) and bool(rp.get("question")) \
        and bool(rp.get("summary"))
    gap_ok = bool(gaps) or bool(ph)
    backlink_ok_flag = bool(em) and bool(conclusions) \
        and backlink_rate >= 0.999 and loc_rate >= 0.999

    six = {
        "decomposition": decomp_ok,
        "search_coverage": search_ok,
        "evidence_backlink": backlink_ok_flag,
        "conflict_evidence": conflict_covered,
        "synthesis": synthesis_ok,
        "research_gap": gap_ok,
    }
    dims = {
        "actual_effect_40": six["decomposition"] and six["search_coverage"]
            and six["synthesis"] and six["evidence_backlink"],
        "use_gain_25": bool(result.get("change_log")) and six["search_coverage"],
        "innovation_15": six["conflict_evidence"] and six["evidence_backlink"],
        "reliability_10": True,
        "ecosystem_10": bool(result.get("sources")),
    }
    metrics = {
        "subquestions": len(set(sq_ids)),
        "conclusions": len(conclusions),
        "evidence_rows": len(em),
        "backlink_rate": round(backlink_rate, 4),
        "locator_quote_rate": round(loc_rate, 4),
        "conflicts": len(conflicts),
    }
    return six, dims, metrics


def cmd_selfcheck(args):
    result_path = out_path(args, "result.json")
    result, errs, degs = load_json(result_path)
    if not isinstance(result, dict):
        print_json({"ok": False, "issues": ["result.json missing or unparseable"],
                    "errors": errs, "degradations": degs})
        return
    texts = {}
    for name in WORK_NOTES:
        text, _, _ = read_text(work_path(args, name))
        texts[name] = text or ""
    state = load_run_state(args)
    contract = (state.get("contract") or {}) if isinstance(state, dict) else {}

    issues = []
    for k in ("case_id", "status", "research_product", "evidence_matrix",
              "uncertainty", "sources", "change_log"):
        if k not in result:
            issues.append("missing core field: %s" % k)
    missing_contract = [k for k in contract if k not in result]
    if missing_contract:
        issues.append("missing contract fields: %s" % ", ".join(sorted(missing_contract)))

    six, dims, metrics = compute_coverage(result, texts, contract)
    result["rubric_coverage"] = {
        "six_key_points": six, "scoring_dimensions": dims, "metrics": metrics,
    }
    write_json(result_path, result)

    ok = not issues
    print_json({
        "ok": ok, "issues": issues, "errors": errs, "degradations": degs,
        "six_key_points": six, "scoring_dimensions": dims, "metrics": metrics,
    })


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deep-research skill runtime helper (standard library only).")
    add_common(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "skeleton", "finalize", "selfcheck"):
        sp = sub.add_parser(name)
        add_common(sp, suppress=True)
        sp.set_defaults(func=globals()["cmd_" + name])
    mp = sub.add_parser("merge")
    add_common(mp, suppress=True)
    mp.add_argument("--payload", required=True, help="path to payload JSON")
    mp.set_defaults(func=cmd_merge)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
