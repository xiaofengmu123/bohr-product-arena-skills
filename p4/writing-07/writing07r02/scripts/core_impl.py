"""Core implementation for the sci-intro-writing Skill runtime helpers.

Discovery, early-skeleton emission, budget/checkpoint accounting, validation and
self-tests. Standard library only (Python 3.9+). Deterministic apart from the
runtime measurements recorded in work/ files.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_INPUT = os.environ.get("INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("WORK_DIR", "/app/work")

MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# Standard result fields required by the runtime contract.
STANDARD_FIELDS = (
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "artifacts",
    "pending_human",
    "errors",
    "degradations",
    "runtime",
)

# Contract discovery keys, in priority order.
CONTRACT_KEYS = ("output_contract", "output", "expected_output", "fields", "required_fields")

# Phase budget: (key, display name, seconds, requests, deliverable).
PHASES = [
    ("P0_scan", "扫描", 90, 3, "work/input_manifest.json + work/case_normalized.json"),
    ("P1_skeleton", "骨架落盘", 90, 2, "output/result.json(v0) + work/budget_state.json"),
    ("P2_facts", "事实池", 180, 12, "work/facts.md"),
    ("P3_draft", "起草", 240, 20, "output/manuscript.md + evidence_map.json + change_log.json"),
    ("P4_verify", "核对回填", 180, 16, "result.json 合同字段回填"),
    ("P5_finalize", "校验封存", 120, 4, "work/validation_report.json + 最终 result.json"),
]
TOTAL_SECONDS = 900
TOTAL_REQUESTS = 64
SKELETON_DEADLINE_SECONDS = 180  # P0 + P1 must finish by T+180s.


# --------------------------------------------------------------------------- #
# File / JSON helpers
# --------------------------------------------------------------------------- #
def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_text(path):
    """Return (text, encoding, degraded). Never raises on decode errors."""
    raw = read_bytes(path)
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, False
        except UnicodeDecodeError:
            continue
    for enc in ("gb18030", "cp1252"):
        try:
            return raw.decode(enc), enc, True
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", True


def load_json(path):
    """Return (data, error, degraded). data is None on parse failure."""
    text, _enc, degraded = read_text(path)
    try:
        return json.loads(text), None, degraded
    except json.JSONDecodeError as exc:
        return None, "bad JSON: %s" % exc, degraded


def write_json(path, data, sort_keys=False):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=sort_keys)
    return str(path)


def classify(rel_path, name):
    low = name.lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".json"):
        return "json"
    if low.endswith((".md", ".txt", ".text", ".markdown")):
        return "text"
    if low.endswith(".bib"):
        return "bib"
    if low.endswith(".tex"):
        return "tex"
    if low.endswith((".csv", ".tsv")):
        return "table"
    if low.endswith((".docx", ".pptx", ".xlsx")):
        return "office"
    return "other"


def find_contract(obj, found=None):
    """Recursively collect (key, value) for keys in CONTRACT_KEYS."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in CONTRACT_KEYS:
                found.append((key, value))
            find_contract(value, found)
    elif isinstance(obj, list):
        for item in obj:
            find_contract(item, found)
    return found


def contract_fields_from(value):
    """Best-effort extraction of top-level contract field names."""
    if isinstance(value, dict):
        return list(value.keys()), value
    if isinstance(value, list):
        fields = []
        for item in value:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict) and "name" in item and isinstance(item["name"], str):
                fields.append(item["name"])
            elif isinstance(item, dict):
                fields.extend(item.keys())
        return fields, None
    return [], None


def extract_case_id(data):
    if isinstance(data, dict):
        for key in ("case_id", "id", "challenge_id", "task_id"):
            if key in data and data[key] is not None:
                return data[key]
    return None


def _is_task_doc(rel):
    base = rel.split("/")[-1].lower()
    return base in ("case.json", "task.md")


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def cmd_scan(args):
    inp = Path(args.input)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    manifest = {
        "input_dir": str(inp),
        "case_file": None,
        "case_parse_error": False,
        "case_id": None,
        "contract": None,
        "contract_fields": [],
        "contract_spec": None,
        "files": [],
        "degradations": [],
    }

    if not inp.exists() or not inp.is_dir():
        manifest["degradations"].append({"item": "input_dir", "reason": "missing or not a directory"})
    else:
        for root, dirs, files in sorted(os.walk(str(inp))):
            dirs.sort()
            for name in sorted(files):
                path = Path(root) / name
                rel = path.relative_to(inp).as_posix()
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    manifest["files"].append({
                        "path": rel, "size": None, "kind": "unknown",
                        "readable": False, "encoding": None, "notes": "stat failed: %s" % exc,
                    })
                    manifest["degradations"].append({"item": rel, "reason": "stat failed: %s" % exc})
                    continue

                entry = {"path": rel, "size": size, "kind": classify(rel, name),
                         "readable": True, "encoding": None, "notes": ""}
                if size > MAX_FILE_BYTES:
                    entry["readable"] = False
                    entry["notes"] = "skipped: exceeds 2MB"
                    manifest["degradations"].append({"item": rel, "reason": "skipped: exceeds 2MB"})
                else:
                    _text, enc, degraded = read_text(path)
                    entry["encoding"] = enc
                    if degraded:
                        entry["notes"] = "non-UTF8 encoding fallback to %s" % enc
                        manifest["degradations"].append(
                            {"item": rel, "reason": "encoding fallback to %s" % enc})
                    if name.lower() == "case.json":
                        manifest["case_file"] = rel
                        _apply_case_file(manifest, path, rel)

                manifest["files"].append(entry)

        # Fallback: no file named case.json; find first JSON carrying a contract.
        if manifest["case_file"] is None:
            for entry in manifest["files"]:
                if entry["kind"] == "json" and entry["readable"] and not _is_task_doc(entry["path"]):
                    candidate = inp / entry["path"]
                    data, _err, _deg = load_json(candidate)
                    if data is not None and find_contract(data):
                        manifest["case_file"] = entry["path"]
                        _apply_case_file(manifest, candidate, entry["path"])
                        break

    write_json(work / "input_manifest.json", manifest, sort_keys=True)
    normalized = {
        "case_id": manifest["case_id"],
        "contract": manifest["contract"],
        "contract_fields": manifest["contract_fields"],
        "contract_spec": manifest["contract_spec"],
    }
    write_json(work / "case_normalized.json", normalized, sort_keys=True)
    print("scan ok: %d files, case_id=%s, contract_fields=%s" % (
        len(manifest["files"]), manifest["case_id"], manifest["contract_fields"]))
    return 0


def _apply_case_file(manifest, path, rel):
    data, err, _deg = load_json(path)
    if data is None:
        manifest["case_parse_error"] = True
        manifest["degradations"].append({"item": rel, "reason": err})
        return
    manifest["case_id"] = extract_case_id(data)
    contracts = find_contract(data)
    if contracts:
        key, value = contracts[0]
        fields, spec = contract_fields_from(value)
        manifest["contract"] = value
        manifest["contract_fields"] = fields
        manifest["contract_spec"] = spec
        if len(contracts) > 1:
            manifest["degradations"].append(
                {"item": rel, "reason": "multiple contract keys found; using first (%s)" % key})


# --------------------------------------------------------------------------- #
# skeleton (early checkpoint)
# --------------------------------------------------------------------------- #
def _default_value(spec):
    if isinstance(spec, dict):
        return {k: _default_value(v) for k, v in spec.items()}
    if isinstance(spec, list):
        return []
    return None


def _standard_default(field):
    if field in ("runtime", "status"):
        return None
    return []


def _has_usable_materials(manifest):
    for entry in manifest.get("files", []):
        if entry.get("readable") and not _is_task_doc(entry["path"]):
            return True
    return False


def _build_result(case_id, contract_fields, contract_spec, status):
    result = {"case_id": case_id}
    for field in contract_fields or []:
        spec = contract_spec.get(field) if isinstance(contract_spec, dict) else None
        result[field] = _default_value(spec)
    for std in STANDARD_FIELDS:
        if std not in result:
            result[std] = _standard_default(std)
    # status is always honest, even if a contract field reused the name.
    result["status"] = status
    return result


def cmd_skeleton(args):
    work = Path(args.work)
    out = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    normalized_path = work / "case_normalized.json"
    if not normalized_path.exists():
        cmd_scan(args)

    normalized = json.loads(read_text(normalized_path)[0])
    case_id = normalized.get("case_id")
    contract_fields = normalized.get("contract_fields") or []
    contract_spec = normalized.get("contract_spec")

    manifest = {}
    manifest_path = work / "input_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(read_text(manifest_path)[0])

    if manifest.get("case_parse_error"):
        status = "input_error"
    elif case_id is None and not manifest.get("case_file"):
        status = "input_error"
    elif not _has_usable_materials(manifest):
        status = "insufficient_evidence"
    else:
        status = "partial"

    result = _build_result(case_id, contract_fields, contract_spec, status)
    write_json(out / "result.json", result)

    budget = {
        "case_id": case_id,
        "started_at_unix": time.time(),
        "skeleton_deadline_unix": time.time() + SKELETON_DEADLINE_SECONDS,
        "total_seconds": TOTAL_SECONDS,
        "total_requests": TOTAL_REQUESTS,
        "requests_used": 0,
        "phases": {
            key: {"planned_seconds": sec, "planned_requests": req,
                  "done": False, "at_unix": None, "requests_used": 0,
                  "deliverable": deliverable}
            for key, _name, sec, req, deliverable in PHASES
        },
        "warnings": [],
    }
    write_json(work / "budget_state.json", budget, sort_keys=True)
    print("skeleton ok: status=%s, case_id=%s, top_level_fields=%s" % (
        status, case_id, sorted(result.keys())))
    return 0


# --------------------------------------------------------------------------- #
# checkpoint / budget
# --------------------------------------------------------------------------- #
def cmd_checkpoint(args):
    work = Path(args.work)
    budget_path = work / "budget_state.json"
    if not budget_path.exists():
        print("budget_state.json missing; run `skeleton` first", file=sys.stderr)
        return 1
    budget = json.loads(read_text(budget_path)[0])
    phase = args.phase
    if phase not in budget.get("phases", {}):
        print("unknown phase %s" % phase, file=sys.stderr)
        return 1

    if args.requests is not None:
        budget["requests_used"] += args.requests
        budget["phases"][phase]["requests_used"] += args.requests
    budget["phases"][phase]["done"] = True
    budget["phases"][phase]["at_unix"] = time.time()

    if phase == "P1_skeleton" and budget["phases"]["P1_skeleton"]["at_unix"] > budget["skeleton_deadline_unix"]:
        budget["warnings"].append("skeleton checkpoint finished after the 180s deadline")
    write_json(budget_path, budget, sort_keys=True)

    used_seconds = time.time() - budget["started_at_unix"]
    remaining_seconds = max(0, TOTAL_SECONDS - used_seconds)
    remaining_requests = max(0, TOTAL_REQUESTS - budget["requests_used"])
    print("checkpoint %s ok; used_requests=%d, remaining=%d requests / ~%ds" % (
        phase, budget["requests_used"], remaining_requests, int(remaining_seconds)))
    return 0


def cmd_budget(args):
    work = Path(args.work)
    budget_path = work / "budget_state.json"
    if not budget_path.exists():
        print("no budget_state.json; run `skeleton` first")
        return 1
    budget = json.loads(read_text(budget_path)[0])
    used_seconds = time.time() - budget["started_at_unix"]
    remaining_seconds = max(0, TOTAL_SECONDS - used_seconds)
    remaining_requests = max(0, TOTAL_REQUESTS - budget["requests_used"])
    print("remaining: ~%ds / %d requests (used %d requests)" % (
        int(remaining_seconds), remaining_requests, budget["requests_used"]))
    for key, name, sec, req, deliverable in PHASES:
        state = budget["phases"][key]
        mark = "done" if state["done"] else "todo"
        print("  %s %s: %s  [%ds/%dr] -> %s" % (key, name, mark, sec, req, deliverable))
    if budget.get("warnings"):
        for warning in budget["warnings"]:
            print("  WARNING: %s" % warning)
    return 0


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #
def cmd_validate(args):
    work = Path(args.work)
    out = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    issues = []
    result = None

    result_path = out / "result.json"
    if not result_path.exists():
        issues.append("result.json missing")
    else:
        data, err, _deg = load_json(result_path)
        if data is None:
            issues.append("result.json not valid JSON: %s" % err)
        else:
            result = data
            if "case_id" not in data:
                issues.append("missing case_id")
            if data.get("status") not in ALLOWED_STATUS:
                issues.append("invalid status: %r" % data.get("status"))
            for std in ("sources", "uncertainty", "change_log"):
                if std not in data:
                    issues.append("missing standard field: %s" % std)
            for source in data.get("sources", []):
                if not isinstance(source, dict) or "locator" not in source or "quote" not in source:
                    issues.append("a sources entry lacks locator/quote")

            normalized_path = work / "case_normalized.json"
            if normalized_path.exists():
                normalized = json.loads(read_text(normalized_path)[0])
                for field in normalized.get("contract_fields") or []:
                    if field not in data:
                        issues.append("missing contract field: %s" % field)

    manuscript_path = out / "manuscript.md"
    if manuscript_path.exists():
        text, _enc, _deg = read_text(manuscript_path)
        if not text.strip():
            issues.append("manuscript.md is empty")
    elif result and result.get("status") not in ("insufficient_evidence", "input_error"):
        issues.append("manuscript.md missing for status %s" % result.get("status"))

    evidence_path = out / "evidence_map.json"
    if evidence_path.exists():
        evidence, err, _deg = load_json(evidence_path)
        if evidence is None:
            issues.append("evidence_map.json invalid: %s" % err)
        elif isinstance(evidence, dict):
            for key, value in evidence.items():
                if not isinstance(value, dict) or "locator" not in value or "quote" not in value:
                    issues.append("evidence_map entry %s lacks locator/quote" % key)

    report = {
        "pass": not issues,
        "issues": issues,
        "result_status": result.get("status") if isinstance(result, dict) else None,
    }
    write_json(work / "validation_report.json", report, sort_keys=True)
    if issues:
        print("validate: %d issue(s) found" % len(issues))
        for issue in issues:
            print("  - %s" % issue)
    else:
        print("validate: ok")
    return 0


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
def run_selftest():
    results = []
    tmp = Path(tempfile.mkdtemp(prefix="sciw_selftest_"))
    try:
        def run_case(label, inp, case_payload=None, material=None, raw_case=None):
            work = tmp / (label + "_work")
            out = tmp / (label + "_output")
            inp.mkdir(parents=True, exist_ok=True)
            if raw_case is not None:
                (inp / "case.json").write_text(raw_case, encoding="utf-8")
            elif case_payload is not None:
                (inp / "case.json").write_text(json.dumps(case_payload, ensure_ascii=False),
                                               encoding="utf-8")
            if material is not None:
                (inp / "materials").mkdir(parents=True, exist_ok=True)
                (inp / "materials" / "paper.md").write_text(material, encoding="utf-8")
            ns = argparse.Namespace(input=str(inp), work=str(work), output=str(out))
            code = cmd_scan(ns)
            code2 = cmd_skeleton(ns)
            code3 = cmd_validate(ns)
            result_path = out / "result.json"
            status = None
            if result_path.exists():
                data, _err, _deg = load_json(result_path)
                if data is not None:
                    status = data.get("status")
            ok = code == 0 and code2 == 0 and code3 == 0 and result_path.exists()
            results.append({"label": label, "exit_ok": ok, "status": status})
            print("  [%s] exit_ok=%s status=%s" % (label, ok, status))

        print("self-test begin")
        run_case("empty_input", tmp / "empty")
        run_case("bad_json", tmp / "badjson", raw_case="{not valid json")
        run_case("no_material", tmp / "nomaterial",
                 case_payload={"case_id": "case-nm", "output_contract": ["introduction", "related_work"]})
        run_case("missing_input", tmp / "missing" / "input")
        run_case("valid_case", tmp / "valid",
                 case_payload={"case_id": "case-v", "output_contract": {"intro": {"audience": None}, "related": []}},
                 material="## Methods\nWe measured X = 3.14.\n")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    passed = all(r["exit_ok"] for r in results)
    print("self-test %s: %d/%d cases produced valid result.json" % (
        "PASS" if passed else "FAIL", sum(r["exit_ok"] for r in results), len(results)))
    for r in results:
        print("  - %s: exit_ok=%s status=%s" % (r["label"], r["exit_ok"], r["status"]))
    return 0 if passed else 1


def cmd_selftest(args):
    return run_selftest()


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
def _add_io_flags(parser):
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)


def main(argv):
    parser = argparse.ArgumentParser(
        prog="main.py", description="sci-intro-writing runtime helpers (standard library only)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func in (("scan", cmd_scan), ("skeleton", cmd_skeleton),
                       ("validate", cmd_validate), ("selftest", cmd_selftest)):
        child = sub.add_parser(name)
        _add_io_flags(child)
        child.set_defaults(func=func)

    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--work", default=DEFAULT_WORK)
    checkpoint.add_argument("--phase", required=True)
    checkpoint.add_argument("--requests", type=int, default=None)
    checkpoint.set_defaults(func=cmd_checkpoint)

    budget = sub.add_parser("budget")
    budget.add_argument("--work", default=DEFAULT_WORK)
    budget.set_defaults(func=cmd_budget)

    args = parser.parse_args(argv)
    return args.func(args)
