#!/usr/bin/env python3
"""library-qa skill entry point.

Pipeline (subcommand `--action`):
  scaffold   -> read /input/case.json + task.md, write a valid PARTIAL result skeleton
                (stage 0; must run within the first 3 minutes), plus <work>/case_summary.json
  inventory  -> six-dimension file catalogue via inventory.py
  finalize   -> merge answers + inventory into a complete result.json: echo every
                output_contract key verbatim, run the contract-fidelity audit, verify every
                evidence quote against the source file, then set an honest status
  run        -> scaffold -> inventory -> finalize in one pass
  selftest   -> delegate to selftest.py (validation + degradation drills)

Standard library only. Deterministic: walks and lists are sorted, JSON order is stable.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

try:
    import inventory as inventory_mod  # same directory
except ImportError:  # allow running via `python scripts/main.py` when scripts/ is not on sys.path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import inventory as inventory_mod

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human", "change_log",
    "errors", "degradations", "failures_recovery", "runtime", "contract_fidelity",
    "answers", "library", "evidence_map",
}
MAX_TEXT_FILE = 2_000_000


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
    return path


def read_json(path):
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as exc:
        return None, "parse_error:%s" % exc


def read_text_robust(path, max_bytes=MAX_TEXT_FILE):
    try:
        st = os.stat(path)
    except OSError as exc:
        return None, "stat_failed:%s" % exc
    if st.st_size > max_bytes:
        return None, "oversized_%d" % st.st_size
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return None, "read_failed:%s" % exc
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1-fallback"


def normalize_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def sha256_of(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 256), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def first_nonnull(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None and obj[k] != "":
            return obj[k]
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def walk_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", ".svn", "__pycache__"))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.isfile(full):
                out.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    return sorted(out)


# ----------------------------------------------------------- contract echoing
def echo_contract(contract, provided):
    """Echo every contract key verbatim; agent-provided leaves override placeholders.
    Reserved framework keys are skipped here (their framework value wins)."""
    contract = contract if isinstance(contract, dict) else {}
    provided = provided if isinstance(provided, dict) else {}
    out = {}
    for k, v in contract.items():
        if k in RESERVED_KEYS:
            continue
        if isinstance(v, dict):
            sub = provided.get(k) if isinstance(provided.get(k), dict) else {}
            out[k] = echo_contract(v, sub)
        elif isinstance(v, list):
            out[k] = provided.get(k) if isinstance(provided.get(k), list) else []
        else:
            out[k] = provided.get(k) if k in provided else None
    return out


def contract_fidelity(contract, result, provided):
    """Audit that every contract key survived into the result, unchanged in name."""
    contract = contract if isinstance(contract, dict) else {}
    provided = provided if isinstance(provided, dict) else {}
    items = []
    for k, v in contract.items():
        present = k in result
        filled = k in provided and provided.get(k) not in (None, {}, [])
        if k in RESERVED_KEYS:
            source = "reserved_collision"
        elif filled:
            source = "agent"
        elif present:
            source = "echoed"
        else:
            source = "missing"
        items.append({
            "key": k,
            "in_contract": True,
            "in_result": present,
            "filled_by_agent": filled,
            "source": source,
            "contract_type": type(v).__name__,
            "result_type": type(result.get(k)).__name__ if k in result else None,
        })
    collisions = sorted(k for k in contract if k in RESERVED_KEYS)
    return {
        "schema": "contract_fidelity.v1",
        "total_keys": len(contract),
        "preserved_keys": sum(1 for it in items if it["source"] in ("agent", "echoed")),
        "filled_by_agent": sum(1 for it in items if it["filled_by_agent"]),
        "echoed": sum(1 for it in items if it["source"] == "echoed"),
        "missing_keys": [it["key"] for it in items if it["source"] == "missing"],
        "reserved_collisions": collisions,
        "items": items,
        "note": "reserved framework keys keep their framework value and are listed as reserved_collisions",
    }


# ---------------------------------------------------------------- evidence
def pdf_page_text(path, page):
    if page is not None:
        cmd = ["pdftotext", "-f", str(page), "-l", str(page), path, "-"]
    else:
        cmd = ["pdftotext", path, "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return None, "pdftotext_exit_%d" % proc.returncode
        return proc.stdout, None
    except FileNotFoundError:
        return None, "pdftotext_unavailable"
    except Exception as exc:
        return None, "pdftotext_error:%s" % exc


def extract_segment(path, locator, frag):
    """Return (segment_text, method) or (None, reason)."""
    if frag and re.match(r"^p\d+$", frag):
        text, reason = pdf_page_text(path, int(frag[1:]))
        if text is None:
            return None, reason
        return text, "pdf-page"
    text, enc = read_text_robust(path)
    if text is None:
        return None, enc  # reason
    if frag and re.match(r"^L\d+(?:-L?\d+)?$", frag):
        nums = re.findall(r"\d+", frag)
        start = int(nums[0])
        stop = int(nums[1]) if len(nums) > 1 else start
        lines = text.splitlines()
        if start < 1 or start > stop:
            return None, "invalid_line_range"
        seg = "\n".join(lines[start - 1:stop]) if start - 1 < len(lines) else ""
        return seg, "line-range"
    return text, "whole-file"


def verify_evidence(ev, input_dir):
    out = dict(ev)
    quote = str(out.get("quote", "") or "")
    locator = str(out.get("locator", "") or "")
    rel = out.get("file")
    if not normalize_ws(quote):
        out["verified"] = False
        out["verify_reference"] = {"method": "none", "note": "empty_quote"}
        return out
    if not rel:
        out["verified"] = None
        out["verify_reference"] = {"method": "none", "note": "no_file_field"}
        return out
    path = os.path.join(input_dir, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        out["verified"] = None
        out["verify_reference"] = {"method": "none", "note": "file_missing"}
        return out
    # hash cross-check: the recorded sha256 must match the file actually at this path
    if out.get("sha256"):
        actual = sha256_of(path)
        if actual is None:
            out["sha256_match"] = None
        elif actual.lower() != str(out["sha256"]).lower():
            out["sha256_match"] = False
            out["verified"] = False
            out["verify_reference"] = {"method": "none", "note": "sha256_mismatch", "expected": out["sha256"], "actual": actual}
            return out
        else:
            out["sha256_match"] = True
    frag = locator.split("#", 1)[1] if "#" in locator else None
    segment, ctx = extract_segment(path, locator, frag)
    if segment is None:
        out["verified"] = None
        out["verify_reference"] = {"method": "attempted", "note": ctx}
        return out
    q = normalize_ws(quote)
    s = normalize_ws(segment)
    out["verified"] = q in s
    out["verify_reference"] = {
        "method": ctx,
        "note": "substring_match_normalized" if out["verified"] else "no_substring_match",
    }
    return out


# ----------------------------------------------------------------- scaffold
def action_scaffold(args):
    inp, out, work = args.input, args.output, args.work
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    t0 = time.time()
    change_log = []
    errors = []
    degradations = []

    case, case_err = read_json(os.path.join(inp, "case.json"))
    case_id = None
    contract = {}
    questions = []
    if case is None:
        if case_err == "missing":
            errors.append("case.json_missing")
        else:
            errors.append(case_err)
    elif not isinstance(case, dict):
        errors.append("case.json_not_object")
    else:
        case_id = first_nonnull(case, "case_id", "id", "challenge_id")
        contract = case.get("output_contract")
        if not isinstance(contract, dict):
            if contract is not None:
                degradations.append("output_contract_not_object; using built-in schema")
            contract = {}
        questions = case.get("questions")
        if questions is None:
            questions = []
        if not isinstance(questions, list):
            questions = [questions]

    task_present = os.path.isfile(os.path.join(inp, "task.md"))
    files = walk_files(inp)

    case_summary = {
        "case_id": case_id,
        "case_json_present": case is not None,
        "case_json_error": case_err,
        "task_md_present": task_present,
        "contract": contract,
        "contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
        "questions_count": len(questions),
        "input_file_count": len(files),
        "input_files": [p for p, _ in files],
        "scaffolded_at": now_iso(),
    }
    write_json(os.path.join(work, "case_summary.json"), case_summary)

    change_log.append({
        "step": "scaffold", "ts": now_iso(), "action": "read_inputs",
        "status": "ok" if case is not None else "degraded",
        "detail": "case.json=%s task.md=%s files=%d" % (
            "present" if case is not None else "missing", "present" if task_present else "missing", len(files)),
    })
    change_log.append({
        "step": "scaffold", "ts": now_iso(), "action": "write_skeleton",
        "status": "ok", "detail": "valid partial result.json written early (status=partial)",
    })

    echoed = echo_contract(contract, {})
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "_skeleton": True,
    }
    skeleton.update(echoed)
    skeleton.update({
        "answers": [],
        "library": {},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": change_log,
        "evidence_map": {},
        "contract_fidelity": contract_fidelity(contract, {k: True for k in contract}, {}),
        "errors": errors,
        "degradations": degradations,
        "failures_recovery": [],
        "runtime": {
            "started_at": now_iso(),
            "scaffold_elapsed_s": round(time.time() - t0, 3),
        },
    })
    write_json(os.path.join(out, "result.json"), skeleton)
    # Also mirror into work/ so later components can find the skeleton when paths differ.
    write_json(os.path.join(work, "skeleton.json"), skeleton)
    return skeleton


# ------------------------------------------------------------------ finalize
def compute_status(summary, answers, files_exist, has_bad, has_null_evidence):
    # Truly empty input: nothing to inventory, nothing to answer.
    if summary.get("input_file_count", 0) == 0:
        return "insufficient_evidence"
    # Contract violation: unparsable or missing case.json.
    if summary.get("case_json_error"):
        return "input_error"
    if not summary.get("case_json_present"):
        return "input_error"
    if not answers:
        return "partial" if files_exist else "insufficient_evidence"
    if has_bad or has_null_evidence:
        return "partial"
    return "ok"


def action_finalize(args):
    inp, out, work = args.input, args.output, args.work
    t0 = time.time()
    summary, _ = read_json(os.path.join(work, "case_summary.json"))
    summary = summary if isinstance(summary, dict) else {}
    inventory, _ = read_json(os.path.join(work, "inventory.json"))
    inventory = inventory if isinstance(inventory, dict) else {}
    answers_doc, _ = read_json(os.path.join(work, "answers.json"))
    answers_doc = answers_doc if isinstance(answers_doc, dict) else {}

    case_id = summary.get("case_id") or first_nonnull(summary, "case_id", "id", "challenge_id")
    contract = summary.get("contract") if isinstance(summary.get("contract"), dict) else {}
    case_present = summary.get("case_json_present", True)

    # preserve prior change_log when present
    prior, _ = read_json(os.path.join(out, "result.json"))
    change_log = list(prior.get("change_log", [])) if isinstance(prior, dict) and isinstance(prior.get("change_log"), list) else []
    errors = list(prior.get("errors", [])) if isinstance(prior, dict) else []
    degradations = list(prior.get("degradations", [])) if isinstance(prior, dict) else []

    provided = answers_doc.get("output") if isinstance(answers_doc.get("output"), dict) else {}
    echoed = echo_contract(contract, provided)

    raw_answers = answers_doc.get("answers") if isinstance(answers_doc.get("answers"), list) else []
    answers = []
    has_bad = has_null = False
    for a in raw_answers:
        if not isinstance(a, dict):
            continue
        ai = dict(a)
        evs = ai.get("evidence") if isinstance(ai.get("evidence"), list) else []
        verified_evs = []
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            vev = verify_evidence(ev, inp)
            if vev.get("verified") is False:
                has_bad = True
                degradations.append("evidence_lookup_failed:" + str(vev.get("file", "")))
            if "verified" in vev and vev["verified"] is None:
                has_null = True
                degradations.append("evidence_unverifiable:" + str(vev.get("file", "")))
            verified_evs.append(vev)
        ai["evidence"] = verified_evs
        answers.append(ai)

    # ---- sources from inventory
    files = inventory.get("files") if isinstance(inventory.get("files"), list) else []
    sources = []
    for f in files:
        if not isinstance(f, dict):
            continue
        base, toks = inventory_mod.version_family_of(f.get("path", ""))
        sources.append({
            "file": f.get("path"),
            "sha256": f.get("sha256"),
            "type": f.get("type"),
            "size": f.get("size"),
            "version_tokens": toks,
            "source": f.get("source", "task_input"),
            "status": f.get("status"),
        })
    for e in inventory.get("empty_files", []) if isinstance(inventory.get("empty_files"), list) else []:
        sources.append({"file": e.get("path"), "sha256": e.get("sha256"), "note": "empty"})

    # ---- uncertainty: agent-supplied plus automatic flags
    uncertainty = []
    if isinstance(answers_doc.get("uncertainty"), list):
        uncertainty.extend(answers_doc["uncertainty"])
    for f in files:
        if isinstance(f, dict) and f.get("status") == "unreadable":
            uncertainty.append({
                "level": "unreadable",
                "item": f.get("path"),
                "reason": f.get("unreadable_reason", "no_text_layer"),
            })
    seen = set()
    dedup_unc = []
    for u in uncertainty:
        key = json.dumps(u, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            dedup_unc.append(u)
    uncertainty = dedup_unc

    pending_human = []
    if isinstance(answers_doc.get("pending_human"), list):
        pending_human.extend(answers_doc["pending_human"])
    for f in files:
        if isinstance(f, dict) and f.get("status") == "unreadable":
            pending_human.append({
                "item": f.get("path"),
                "reason": f.get("unreadable_reason", "no_text_layer"),
                "action": "inspect manually or run OCR",
            })

    change_log.append({
        "step": "finalize", "ts": now_iso(), "action": "merge",
        "status": "ok", "detail": "answers=%d sources=%d" % (len(answers), len(sources)),
    })

    result = {
        "case_id": case_id,
        "status": compute_status(summary, answers, bool(files), has_bad, has_null),
    }
    result.update(echoed)
    result.update({
        "answers": answers,
        "library": inventory.get("summary", {}),
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "change_log": change_log,
        "evidence_map": {a.get("question_id") or ("q%d" % i): [e.get("file") for e in a.get("evidence", []) if isinstance(e, dict)] for i, a in enumerate(answers)},
        "contract_fidelity": contract_fidelity(contract, result, provided),
        "errors": errors,
        "degradations": degradations,
        "failures_recovery": inventory.get("failures_recovery", []) if isinstance(inventory.get("failures_recovery"), list) else [],
        "runtime": {
            "started_at": (prior.get("runtime", {}).get("started_at") if isinstance(prior, dict) and isinstance(prior.get("runtime"), dict) else now_iso()),
            "finished_at": now_iso(),
            "finalize_elapsed_s": round(time.time() - t0, 3),
        },
    })
    change_log = result["change_log"]
    change_log.append({
        "step": "finalize", "ts": now_iso(), "action": "set_status",
        "status": result["status"], "detail": "honest status; no fabricated evidence",
    })
    result["change_log"] = change_log
    if "_skeleton" in result:
        del result["_skeleton"]
    write_json(os.path.join(out, "result.json"), result)
    return result


# ---------------------------------------------------------------- pipeline
def action_run(args):
    action_scaffold(args)
    inventory_mod.run(input_dir=args.input, work_dir=args.work)
    answers_path = os.path.join(args.work, "answers.json")
    if not os.path.isfile(answers_path):
        # ensure a valid empty answers.json so finalize can merge deterministically
        write_json(answers_path, {"output": {}, "answers": [], "uncertainty": [], "pending_human": []})
    return action_finalize(args)


def action_selftest(args):
    selftest_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.py")
    if not os.path.isfile(selftest_py):
        print("selftest.py missing", file=sys.stderr)
        return 2
    proc = subprocess.run(
        [sys.executable, selftest_py, "--input", args.input, "--output", args.output, "--work", args.work],
        capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description="library-qa skill pipeline.")
    p.add_argument("--action", choices=["scaffold", "inventory", "finalize", "run", "selftest"], default="run")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    if a.action == "scaffold":
        action_scaffold(a)
    elif a.action == "inventory":
        inventory_mod.run(input_dir=a.input, work_dir=a.work)
    elif a.action == "finalize":
        action_finalize(a)
    elif a.action == "run":
        action_run(a)
    elif a.action == "selftest":
        return action_selftest(a)
    print("done:", a.action)
    return 0


if __name__ == "__main__":
    sys.exit(main())