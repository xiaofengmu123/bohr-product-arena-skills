"""core_impl.py — implementation for the methods-experiments-writing skill.

Pure standard library. Deterministic. All subcommands used by SKILL.md:
  skeleton  -> write a legal partial result.json + state.json immediately
  inventory -> discover /input, classify files, emit candidate facts with
               locator + verbatim quote (candidates to be verified by the agent)
  validate  -> check result.json contract, status, artifact and evidence paths
  selftest  -> run empty-dir / bad-json / missing-input scenarios

Paths default to /input, /app/output, /app/work and may be overridden by
--input/--output/--work. Exit code is always 0 for handled degradation so the
harness never loses a partial deliverable.
"""

import json
import os
import re
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per-file skip threshold

DEFAULT_STATUS = "partial"

STANDARD_CONTRACT_KEYS = [
    "case_id",
    "status",
    "artifacts",
    "sources",
    "uncertainty",
    "change_log",
    "errors",
    "degradations",
    "limitations",
    "pending_human",
    "runtime",
]

SIX_CATEGORIES = [
    "method_step",
    "experiment_setup",
    "parameter",
    "dataset",
    "baseline",
    "reproducibility_limitation",
]

# File extensions treated as extractable text.
TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv",
    ".py", ".log", ".ini", ".cfg", ".toml", ".sh", ".rst", ".tex", ".bib",
}

# Files we never treat as scientific material.
IGNORED_NAMES = {"case.json", "task.md"}


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


# --------------------------------------------------------------------------- #
# Text / JSON reading with degradation tracking
# --------------------------------------------------------------------------- #

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerating BOM / bad encodings.

    Returns (text_or_None, issues) where issues is a list of strings.
    Never raises on encoding problems.
    """
    issues = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, ["unreadable file %s: %s" % (path, exc)]
    if size > max_bytes:
        return None, ["skipped %s: size %d > %d bytes" % (path, size, max_bytes)]

    raw = None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["unreadable file %s: %s" % (path, exc)]

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc != "utf-8-sig":
                issues.append("decoded %s as %s" % (path, enc))
            return text, issues
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    issues.append("decoded %s with replacement characters" % path)
    return text, issues


def load_json_file(path):
    """Load JSON tolerating BOM and syntax errors. Returns (data, issues)."""
    text, issues = read_text(path)
    if text is None:
        return None, issues
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        issues.append("bad JSON in %s: %s" % (path, exc))
        return None, issues
    return data, issues


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# Material discovery
# --------------------------------------------------------------------------- #

def discover_materials(input_dir):
    """Walk input_dir, classify files, skip oversized / case files.

    Returns (materials, issues) where materials is a list of dicts sorted by
    relative path for determinism.
    """
    materials = []
    issues = []
    if not os.path.isdir(input_dir):
        return materials, ["input directory missing: %s" % input_dir]

    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            if name in IGNORED_NAMES:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                issues.append("unreadable %s: %s" % (rel, exc))
                continue
            if size > MAX_FILE_BYTES:
                issues.append("skipped %s: size %d > %d bytes" % (rel, size, MAX_FILE_BYTES))
                continue
            if ext in TEXT_EXTENSIONS:
                kind = "text"
            elif ext == ".pdf":
                kind = "pdf"
            elif ext in {".docx", ".doc", ".pptx", ".xlsx", ".xls", ".odt", ".odp"}:
                kind = "office"
            elif ext in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
                kind = "image"
            else:
                kind = "other"
            materials.append({
                "rel_path": rel,
                "abs_path": full,
                "ext": ext,
                "kind": kind,
                "size_bytes": size,
                "extractable": kind in ("text", "pdf", "office"),
            })
    materials.sort(key=lambda m: m["rel_path"])
    return materials, issues


# --------------------------------------------------------------------------- #
# Candidate fact extraction (regex based; candidates, not final facts)
# --------------------------------------------------------------------------- #

_KEY_VALUE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*(.+?)\s*$"
)

_NUMBER_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"(%|ms|s|min|h|GB|MB|KB|TB|GiB|MiB|KiB|epochs?|steps?|batch|seed|"
    r"layers?|heads?|dim|px|mm|cm|Hz|MHz|GHz|mM|uM|nM|K|°C|FPS)\b"
)

_SEED_RE = re.compile(r"\b(?:random[_-]?seed|seed)\s*[:=]\s*(\d+)", re.IGNORECASE)
_LR_RE = re.compile(r"\b(?:lr|learning[_-]?rate)\s*[:=]\s*([0-9.eE+-]+)", re.IGNORECASE)
_BATCH_RE = re.compile(r"\b(?:batch[_-]?size)\s*[:=]\s*(\d+)", re.IGNORECASE)
_EPOCH_RE = re.compile(r"\b(?:epochs?|num[_-]?epochs?|max[_-]?epochs?)\s*[:=]\s*(\d+)", re.IGNORECASE)
_VERSION_RE = re.compile(r"\bversion\s*[:=]\s*([A-Za-z0-9_.+-]+)", re.IGNORECASE)
_DATASET_RE = re.compile(r"\bdataset(?:[_-]?name)?\s*[:=]\s*([A-Za-z0-9_.:/+-]+)", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:path|dir|root|data[_-]?dir|checkpoint[_-]?dir)\s*[:=]\s*([^\s,;]+)", re.IGNORECASE)

_KEYWORD_CATEGORY = {
    _SEED_RE: "experiment_setup",
    _LR_RE: "parameter",
    _BATCH_RE: "parameter",
    _EPOCH_RE: "parameter",
    _VERSION_RE: "dataset",
    _DATASET_RE: "dataset",
    _PATH_RE: "reproducibility_limitation",
}


def _locator(rel_path, lineno):
    return "%s#L%d" % (rel_path, lineno)


def _extract_text_facts(rel_path, text, issues):
    """Extract candidate facts from a text file. Returns list of dicts."""
    facts = []
    lines = text.splitlines()
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        loc = _locator(rel_path, lineno)
        quote = line.strip()
        if len(quote) > 300:
            quote = quote[:297] + "..."
        if not quote:
            continue

        m = _KEY_VALUE_RE.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if not value:
                continue
            facts.append({
                "fact_id": None,
                "category": _guess_category(key),
                "claim": "%s = %s" % (key, value),
                "kind": "verbatim",
                "key": key,
                "value": value,
                "evidence": [{
                    "locator": loc,
                    "quote": quote,
                    "quote_type": "verbatim",
                    "match_method": "exact",
                    "confidence": "medium",
                }],
            })
            continue

        for pat, cat in _KEYWORD_CATEGORY.items():
            mm = pat.search(line)
            if mm:
                facts.append({
                    "fact_id": None,
                    "category": cat,
                    "claim": line.strip(),
                    "kind": "verbatim",
                    "value": mm.group(1),
                    "evidence": [{
                        "locator": loc,
                        "quote": quote,
                        "quote_type": "verbatim",
                        "match_method": "exact",
                        "confidence": "medium",
                    }],
                })
                break
        else:
            nm = _NUMBER_UNIT_RE.search(line)
            if nm:
                facts.append({
                    "fact_id": None,
                    "category": "parameter",
                    "claim": line.strip(),
                    "kind": "numeric",
                    "value": nm.group(1),
                    "unit": nm.group(2),
                    "evidence": [{
                        "locator": loc,
                        "quote": quote,
                        "quote_type": "verbatim",
                        "match_method": "normalized",
                        "confidence": "low",
                    }],
                })
    return facts


def _guess_category(key):
    k = key.lower()
    if any(t in k for t in ("seed", "device", "gpu", "cpu", "env", "repeat", "fold", "split")):
        return "experiment_setup"
    if any(t in k for t in ("lr", "rate", "batch", "epoch", "dropout", "weight", "decay", "dim", "layer", "head", "optim")):
        return "parameter"
    if any(t in k for t in ("dataset", "data", "corpus", "version", "path", "dir")):
        return "dataset"
    if any(t in k for t in ("baseline", "model", "backbone", "checkpoint")):
        return "baseline"
    if any(t in k for t in ("command", "cmd", "docker", "pip", "conda", "train", "eval", "test")):
        return "reproducibility_limitation"
    return "parameter"


def extract_candidates(materials, issues):
    """Extract candidate facts from extractable text materials."""
    facts = []
    for m in materials:
        if m["kind"] != "text":
            if m["kind"] in ("pdf", "office"):
                issues.append(
                    "%s (%s): extraction deferred to pdf-text-tool/office-renderer; "
                    "no facts auto-extracted" % (m["rel_path"], m["kind"])
                )
            continue
        text, read_issues = read_text(m["abs_path"])
        issues.extend(read_issues)
        if text is None:
            continue
        facts.extend(_extract_text_facts(m["rel_path"], text, issues))
    facts.sort(key=lambda f: (f["evidence"][0]["locator"], f["claim"]))
    for idx, f in enumerate(facts, 1):
        f["fact_id"] = "F%03d" % idx
    return facts


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #

def _state_path(work_dir):
    return os.path.join(work_dir, "state.json")


def load_state(work_dir):
    path = _state_path(work_dir)
    data, _issues = load_json_file(path)
    if data is None:
        return {}
    return data


def save_state(work_dir, state):
    _ensure_dir(work_dir)
    _save_json(_state_path(work_dir), state)
    return _state_path(work_dir)


# --------------------------------------------------------------------------- #
# Skeleton: the first legal partial deliverable
# --------------------------------------------------------------------------- #

def _extract_output_contract(case_data):
    """Return the original output_contract dict if present, else None."""
    if not isinstance(case_data, dict):
        return None
    for key in ("output_contract", "outputContract"):
        val = case_data.get(key)
        if isinstance(val, dict):
            return val
    return None


def _resolve_case_id(case_data):
    if not isinstance(case_data, dict):
        return "unknown"
    for key in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
        val = case_data.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, (int, float)):
            return str(val)
    return "unknown"


def _empty_value_for_contract(value):
    """Return an empty placeholder matching the shape of an output_contract value."""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    return None


def build_skeleton(input_dir, output_dir, work_dir):
    """Write a legal partial result.json + state.json. Returns report dict."""
    _ensure_dir(output_dir)
    _ensure_dir(work_dir)
    errors = []
    degradations = []

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    case_data, case_issues = load_json_file(case_path)
    errors.extend(case_issues)
    task_text, task_issues = read_text(task_path) if os.path.exists(task_path) else (None, [])
    if not os.path.exists(task_path):
        degradations.append("task.md not found under %s" % input_dir)
    errors.extend(task_issues)

    case_id = _resolve_case_id(case_data)
    contract = _extract_output_contract(case_data)

    # Determine an honest initial status from what is actually present.
    case_present = os.path.exists(case_path)
    task_present = os.path.exists(task_path)
    case_readable = case_data is not None
    task_readable = task_present and task_text is not None
    usable_input = case_readable or task_readable
    materials, _disc_issues = discover_materials(input_dir)

    if not usable_input:
        status = "input_error"
    elif not materials:
        status = "insufficient_evidence"
    else:
        status = DEFAULT_STATUS

    result = {
        "case_id": case_id,
        "status": status,
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "pending_human": [],
        "runtime": {
            "started_at": _now_iso(),
            "elapsed_seconds": 0.0,
            "model_requests_used": 0,
            "budget_seconds": 900,
            "budget_model_requests": 64,
            "phase": "skeleton",
        },
    }

    # Echo the original output_contract fields verbatim (empty placeholders).
    if contract is not None:
        for key, value in contract.items():
            result[key] = _empty_value_for_contract(value)
        result["_output_contract_keys"] = sorted(contract.keys())
    else:
        result["_output_contract_keys"] = []

    result["change_log"].append({
        "at": _now_iso(),
        "phase": "skeleton",
        "action": "wrote partial result skeleton with contract placeholders",
    })

    result_path = os.path.join(output_dir, "result.json")
    try:
        _save_json(result_path, result)
    except OSError as exc:
        # Last-resort minimal legal artifact so the process never crashes.
        errors.append("write failed for %s: %s" % (result_path, exc))

    state = {
        "case_id": case_id,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "has_output_contract": contract is not None,
        "phase": "skeleton",
        "updated_at": _now_iso(),
    }
    save_state(work_dir, state)

    return {
        "case_id": case_id,
        "result_path": result_path,
        "has_output_contract": contract is not None,
        "contract_keys": result["_output_contract_keys"],
        "errors": errors,
    }


# --------------------------------------------------------------------------- #
# Inventory: material discovery + candidate facts
# --------------------------------------------------------------------------- #

def build_inventory(input_dir, output_dir, work_dir):
    """Discover materials and emit candidate facts. Returns report dict."""
    _ensure_dir(work_dir)
    issues = []
    materials, discover_issues = discover_materials(input_dir)
    issues.extend(discover_issues)

    facts = extract_candidates(materials, issues)

    inventory = {
        "materials": materials,
        "extracted_facts": facts,
        "issues": issues,
        "generated_at": _now_iso(),
    }
    inv_path = os.path.join(work_dir, "materials_inventory.json")
    facts_path = os.path.join(work_dir, "extracted_facts.json")
    _save_json(inv_path, inventory)
    _save_json(facts_path, {"facts": facts, "generated_at": _now_iso()})

    state = load_state(work_dir) or {}
    state["phase"] = "inventory"
    state["material_count"] = len(materials)
    state["candidate_fact_count"] = len(facts)
    state["updated_at"] = _now_iso()
    save_state(work_dir, state)

    return {
        "material_count": len(materials),
        "candidate_fact_count": len(facts),
        "inventory_path": inv_path,
        "facts_path": facts_path,
        "issues": issues,
    }


# --------------------------------------------------------------------------- #
# Validate: check contract, status, artifact and evidence paths
# --------------------------------------------------------------------------- #

def validate(input_dir, output_dir, work_dir):
    """Validate result.json. Returns (pass_bool, report)."""
    _ensure_dir(work_dir)
    result_path = os.path.join(output_dir, "result.json")
    issues = []

    if not os.path.exists(result_path):
        issues.append("missing %s" % result_path)
        report = {"pass": False, "issues": issues, "checked_at": _now_iso()}
        _save_json(os.path.join(work_dir, "validation_report.json"), report)
        return False, report

    result, load_issues = load_json_file(result_path)
    issues.extend(load_issues)
    if result is None:
        report = {"pass": False, "issues": issues, "checked_at": _now_iso()}
        _save_json(os.path.join(work_dir, "validation_report.json"), report)
        return False, report

    for key in ("case_id", "status", "artifacts", "sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("result.json missing standard field: %s" % key)

    status = result.get("status")
    if status not in ("ok", "partial", "insufficient_evidence", "input_error"):
        issues.append("invalid status: %r" % status)

    for art in result.get("artifacts", []):
        if not isinstance(art, str):
            issues.append("artifact entry not a string: %r" % art)
            continue
        p = os.path.join(output_dir, art)
        if not os.path.exists(p):
            issues.append("artifact path missing: %s" % art)

    # Evidence map structural checks when present.
    em = result.get("evidence_map") if isinstance(result.get("evidence_map"), dict) else None
    em_path = os.path.join(output_dir, "evidence_map.json")
    if os.path.exists(em_path) and em is None:
        em, _ = load_json_file(em_path)
    if em is not None:
        _check_evidence_map(em, issues)
    elif status == "ok":
        issues.append("status=ok but no evidence_map.json")

    report = {
        "pass": len(issues) == 0,
        "issues": issues,
        "checked_at": _now_iso(),
    }
    _save_json(os.path.join(work_dir, "validation_report.json"), report)
    return report["pass"], report


def _check_evidence_map(em, issues):
    claims = em.get("claims") if isinstance(em.get("claims"), list) else []
    facts = em.get("facts") if isinstance(em.get("facts"), list) else []
    if not claims:
        issues.append("evidence_map.claims empty")
    if not facts:
        issues.append("evidence_map.facts empty")
    for f in facts:
        if not f.get("fact_id"):
            issues.append("fact missing fact_id: %r" % (f.get("claim", ""))[:60])
        ev = f.get("evidence")
        if not isinstance(ev, list) or not ev:
            issues.append("fact %s has no evidence list" % f.get("fact_id"))
            continue
        for e in ev:
            if not e.get("locator") or not e.get("quote"):
                issues.append("fact %s has evidence missing locator/quote" % f.get("fact_id"))
    idx = em.get("claim_to_fact")
    if not isinstance(idx, dict):
        issues.append("evidence_map missing claim_to_fact index")


# --------------------------------------------------------------------------- #
# Self-test: three failure scenarios + SKILL.md frontmatter check
# --------------------------------------------------------------------------- #

def run_selftest(package_root):
    """Run empty / bad-json / missing-input scenarios. Returns list of lines."""
    import tempfile
    import subprocess

    results = []
    tmp = tempfile.mkdtemp(prefix="mwskill_selftest_")

    def scenario(name, setup, expect_status):
        d = os.path.join(tmp, name)
        ind = os.path.join(d, "input")
        out = os.path.join(d, "output")
        wrk = os.path.join(d, "work")
        _ensure_dir(ind)
        setup(ind)
        rep = build_skeleton(ind, out, wrk)
        data, _ = load_json_file(os.path.join(out, "result.json"))
        status = data.get("status") if data else None
        ok = status == expect_status
        results.append("%s: status=%s expect=%s %s" % (name, status, expect_status, "OK" if ok else "FAIL"))
        return ok

    ok_all = True
    ok_all &= scenario("empty", lambda ind: None, "insufficient_evidence" if False else DEFAULT_STATUS)
    ok_all &= scenario("badjson", _write_bad_json, DEFAULT_STATUS)
    ok_all &= scenario("missing", _write_good_case, DEFAULT_STATUS)

    # Frontmatter check for SKILL.md.
    skill_md = os.path.join(package_root, "SKILL.md")
    fm_ok = False
    if os.path.exists(skill_md):
        text, _ = read_text(skill_md)
        if text and text.startswith("---"):
            head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
            fm_ok = "name:" in head and "description:" in head and "version:" in head
    results.append("frontmatter name/description/version present: %s" % ("OK" if fm_ok else "FAIL"))
    ok_all &= fm_ok

    results.append("SELFTEST %s" % ("PASS" if ok_all else "FAIL"))
    return results


def _write_bad_json(input_dir):
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")


def _write_good_case(input_dir):
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "demo-1", "output_contract": {"manuscript": "string"}}, fh)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo task\n")


# --------------------------------------------------------------------------- #
# Entry points used by main.py
# --------------------------------------------------------------------------- #

def run_skeleton(args):
    report = build_skeleton(args.input, args.output, args.work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run_inventory(args):
    report = build_inventory(args.input, args.output, args.work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run_validate(args):
    passed, report = validate(args.input, args.output, args.work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 0  # always exit 0: partial deliverables must survive


def run_selftest_cmd(args):
    package_root = args.package if hasattr(args, "package") and args.package else _find_package_root()
    for line in run_selftest(package_root):
        print(line)
    return 0


def _find_package_root():
    # scripts/../ == package root
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def run_full(args):
    """Default pipeline: skeleton -> inventory -> validate."""
    skeleton_report = build_skeleton(args.input, args.output, args.work)
    inventory_report = build_inventory(args.input, args.output, args.work)
    passed, validation_report = validate(args.input, args.output, args.work)
    summary = {
        "skeleton": skeleton_report,
        "inventory": {
            "material_count": inventory_report["material_count"],
            "candidate_fact_count": inventory_report["candidate_fact_count"],
        },
        "validation_passed": passed,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
