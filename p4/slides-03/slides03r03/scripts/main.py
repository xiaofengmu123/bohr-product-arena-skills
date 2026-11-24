#!/usr/bin/env python3
"""Deterministic result.json lifecycle for the academic-slide-organizer Skill.

Subcommands
-----------
scaffold   Discover /input (case.json, task.md, materials), write a legal partial
           result.json skeleton. Idempotent: refuses to overwrite an existing
           result.json unless --force is given.
update     Merge a patch JSON file into result.json and append one change_log
           entry. This is the only mutation path after the skeleton is written.
finalize   Recompute the content-addressed run_id, canonicalize, validate, and
           write the final result.json.
check      Read-only schema/consistency check (exit 1 when errors are found).

Only the Python standard library is used. All paths are passed explicitly or
resolved relative to this file; the current working directory is never relied on.
The output is canonical JSON (sort_keys, compact separators, no NaN) so that the
same frozen case reproduces a byte-identical result.json.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CONTRACT_VERSION = "2.0.0"
MAX_SOURCE_BYTES = 2 * 1024 * 1024  # larger material files are skipped and recorded

# Envelope fields the Skill always emits on top of the case-provided contract.
ENVELOPE = {
    "case_id": None,
    "status": "partial",
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
    "run": {"run_id": None, "contract_version": CONTRACT_VERSION},
}

# Default contract used only when /input/case.json carries no output_contract.
DEFAULT_CONTRACT = {
    "report_goal": None,
    "audience": None,
    "narrative": None,
    "pages": [],
    "editable_deck": None,
    "evidence_map": [],
    "qa_prep": [],
    "rendering": None,
}

ALLOWED_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".csv", ".tex", ".bib", ".xml", ".html"}
MATERIAL_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".png", ".jpg", ".jpeg", ".svg", ".pptx", ".docx", ".xlsx", ".odp"}


def read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def canonical(obj):
    """Deterministic serialization: sorted keys, compact separators, no NaN."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    try:
        return json.loads(read_text(path)), None
    except Exception as exc:  # defensive: never crash on malformed input
        return None, "%s: %s" % (type(exc).__name__, exc)


def deep_merge(base, patch):
    """Merge patch into base; dicts merge recursively, lists/scalars replace."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = dict(base)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def compute_run_id(case_id, sources):
    """Content-addressed run identifier: stable across re-runs of the same frozen case."""
    h = hashlib.sha256()
    h.update((case_id or "").encode("utf-8"))
    for src in sorted(sources, key=lambda s: s.get("path", "")):
        h.update(b"\x00")
        h.update((src.get("path", "") or "").encode("utf-8"))
        h.update((src.get("sha256") or "").encode("ascii"))
    return "run-" + h.hexdigest()[:16]


def _extract_case_id(case, task_text):
    for key in ("id", "armiId", "case_id", "challenge_id"):
        if isinstance(case, dict) and case.get(key):
            return str(case[key])
    if task_text:
        m = re.search(r"challenge_id:\s*`?([A-Za-z0-9_-]+)`?", task_text)
        if m:
            return m.group(1)
    return None


def discover_input(input_dir, task_path):
    input_dir = Path(input_dir)
    found = {"case": None, "case_path": None, "task_text": None, "task_path": None,
             "materials": [], "errors": [], "degradations": []}
    case_path = None
    for name in ("case.json", "case.jsonl", "input.json"):
        candidate = input_dir / name
        if candidate.is_file():
            case_path = candidate
            break
    if case_path is None:
        for candidate in sorted(input_dir.glob("*.json")):
            case_path = candidate
            break
    if case_path is not None:
        found["case_path"] = case_path
        obj, err = load_json(case_path)
        if err:
            found["errors"].append("case json unreadable (%s): %s" % (case_path.name, err))
        else:
            found["case"] = obj if isinstance(obj, dict) else {}
    else:
        found["errors"].append("no case.json found under %s" % input_dir)

    task_candidates = []
    if task_path:
        p = Path(task_path)
        if p.is_file():
            task_candidates = [p]
    if not task_candidates:
        task_candidates = sorted(input_dir.glob("task.md"))
    if task_candidates:
        found["task_path"] = task_candidates[0]
        found["task_text"] = read_text(task_candidates[0])

    exclude = {found["case_path"], found["task_path"]}
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path in exclude:
            continue
        suffix = path.suffix.lower()
        if suffix not in MATERIAL_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entry = {"path": str(path.relative_to(input_dir)), "suffix": suffix, "size": size}
        if size > MAX_SOURCE_BYTES:
            entry["read"] = False
            entry["note"] = "skipped: over 2MB"
            found["degradations"].append("skipped oversized material: %s" % entry["path"])
        else:
            entry["read"] = True
            entry["sha256"] = sha256_file(path)
        found["materials"].append(entry)
    return found


def build_skeleton(found, input_dir):
    input_dir = Path(input_dir)
    case = found["case"] or {}
    case_id = _extract_case_id(case, found.get("task_text"))
    result = {}

    # 1) Preserve a case-provided output_contract verbatim (unknown keys included).
    provided = case.get("output_contract")
    if isinstance(provided, dict):
        result.update(json.loads(json.dumps(provided)))

    # 2) Fill the default contract for any field the case did not declare.
    for key, default in DEFAULT_CONTRACT.items():
        result.setdefault(key, default)

    # 3) Envelope fields.
    for key, default in ENVELOPE.items():
        result.setdefault(key, default)

    result["case_id"] = case_id

    sources = []
    if found.get("case_path") is not None:
        p = found["case_path"]
        sources.append({"path": str(p.relative_to(input_dir)), "type": "case", "read": True,
                        "size": p.stat().st_size, "sha256": sha256_file(p)})
    for m in found["materials"]:
        src = {"path": m["path"], "type": (m["suffix"].lstrip(".") or "material"),
               "read": m.get("read", False), "size": m.get("size")}
        if m.get("sha256"):
            src["sha256"] = m["sha256"]
        sources.append(src)
    if found.get("task_path") is not None:
        p = found["task_path"]
        sources.append({"path": str(p.relative_to(input_dir)), "type": "task", "read": True,
                        "size": p.stat().st_size, "sha256": sha256_file(p)})
    result["sources"] = sources

    if found.get("errors"):
        result["errors"] = found["errors"]
    if found.get("degradations"):
        result["degradations"] = found["degradations"]

    if not sources and not found.get("case_path"):
        result["status"] = "insufficient_evidence"
        result["uncertainty"] = ["no readable input sources were found"]
    elif not case_id or found.get("errors"):
        result["status"] = "input_error"
    else:
        result["status"] = "partial"

    result["run"]["run_id"] = compute_run_id(case_id, sources)
    result["change_log"] = [{
        "step": "1-scaffold",
        "action": "wrote partial result.json skeleton from discovered inputs",
        "scope": "result.json",
    }]
    return result


def validate(result):
    errors, warnings = [], []
    if not isinstance(result, dict):
        return ["result is not a JSON object"], []
    if not result.get("case_id"):
        errors.append("case_id missing or empty")
    if result.get("status") not in ALLOWED_STATUS:
        errors.append("invalid status: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if not isinstance(result.get(key), list):
            errors.append("%s must be a list" % key)
    for key in DEFAULT_CONTRACT:
        if key not in result:
            errors.append("missing contract field: %s" % key)
    pages = result.get("pages")
    if not isinstance(pages, list):
        errors.append("pages must be a list")
    else:
        seen = {}
        for i, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append("pages[%d] not an object" % i)
                continue
            claim = (page.get("single_claim") or "").strip()
            if not claim:
                warnings.append("pages[%d] missing single_claim" % i)
            elif claim in seen:
                warnings.append("pages[%d] repeats single_claim of pages[%d]" % (i, seen[claim]))
            else:
                seen[claim] = i
    em = result.get("evidence_map")
    if not isinstance(em, list):
        errors.append("evidence_map must be a list")
    else:
        for j, item in enumerate(em):
            if isinstance(item, dict):
                if not (item.get("locator") or "").strip():
                    errors.append("evidence_map[%d] missing locator" % j)
                if not (item.get("quote") or "").strip():
                    errors.append("evidence_map[%d] missing verbatim quote" % j)
    if result.get("editable_deck") is None:
        warnings.append("editable_deck is null (not yet produced)")
    if result.get("rendering") is None:
        warnings.append("rendering is null (no render/preview recorded)")
    if not result.get("run", {}).get("run_id"):
        warnings.append("run.run_id missing")
    return errors, warnings


def load_result(path):
    path = Path(path)
    if not path.is_file():
        return None, "result.json not found at %s" % path
    return load_json(path)


def write_result(output_dir, result):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "result.json"
    out_path.write_text(canonical(result), encoding="utf-8")
    return out_path


def cmd_scaffold(args):
    found = discover_input(args.input, args.task)
    result = build_skeleton(found, args.input)
    out_dir = Path(args.output)
    out_path = out_dir / "result.json"
    if out_path.exists() and not args.force:
        print(json.dumps({"ok": True, "skipped": True,
                          "reason": "result.json already exists; use update/finalize"},
                         ensure_ascii=False))
        return 0
    write_result(out_dir, result)
    print(json.dumps({"ok": True, "case_id": result.get("case_id"),
                      "status": result.get("status"), "run_id": result.get("run", {}).get("run_id")},
                     ensure_ascii=False))
    return 0


def cmd_update(args):
    patch, err = load_json(args.patch)
    if err:
        print(json.dumps({"ok": False, "error": "patch unreadable: %s" % err}, ensure_ascii=False))
        return 1
    result, err = load_result(Path(args.output) / "result.json")
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    result = deep_merge(result, patch)
    if args.action:
        step = "%d-update" % (len(result.get("change_log", [])) + 1)
        result.setdefault("change_log", []).append(
            {"step": step, "action": args.action, "scope": args.scope or "result.json"})
    write_result(args.output, result)
    print(json.dumps({"ok": True, "status": result.get("status")}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    result, err = load_result(Path(args.output) / "result.json")
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    for key, default in DEFAULT_CONTRACT.items():
        result.setdefault(key, default)
    for key, default in ENVELOPE.items():
        result.setdefault(key, default)
    if result.get("status") not in ALLOWED_STATUS:
        result["status"] = "partial"
    result["run"] = result.get("run") or {}
    result["run"]["contract_version"] = CONTRACT_VERSION
    result["run"]["run_id"] = compute_run_id(result.get("case_id"), result.get("sources", []))
    errors, warnings = validate(result)
    write_result(args.output, result)
    print(json.dumps({"ok": True, "status": result.get("status"),
                      "run_id": result.get("run", {}).get("run_id"),
                      "errors": len(errors), "warnings": len(warnings)}, ensure_ascii=False))
    for e in errors:
        print("ERROR: %s" % e, file=sys.stderr)
    for w in warnings:
        print("WARN: %s" % w, file=sys.stderr)
    return 0


def cmd_check(args):
    result, err = load_result(Path(args.output) / "result.json")
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    errors, warnings = validate(result)
    print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False))
    return 0 if not errors else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Deterministic result.json lifecycle for the Skill.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scaffold", help="write partial result.json skeleton")
    p.add_argument("--input", default="/input")
    p.add_argument("--task", default=None, help="path to task.md (defaults to INPUT/task.md)")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--force", action="store_true", help="overwrite an existing result.json")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("update", help="merge a patch JSON and append a change_log entry")
    p.add_argument("--patch", required=True, help="path to patch JSON file")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--action", default=None, help="human-readable change description")
    p.add_argument("--scope", default=None, help="affected field(s)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("finalize", help="recompute run_id, canonicalize, validate")
    p.add_argument("--output", default="/app/output")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("check", help="read-only schema/consistency check")
    p.add_argument("--output", default="/app/output")
    p.set_defaults(func=cmd_check)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
