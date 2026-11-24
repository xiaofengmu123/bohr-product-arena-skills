"""core_impl.py - implementation for the experiment-plan-optimizer Skill.

Pure Python standard library. Provides probe / scaffold / patch / validate / run
subcommands with deterministic behaviour and honest degradation.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

RESERVED_ENVELOPE = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "contract_coverage",
}

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

# Candidate schema key aliases for the six required elements (used as a
# heuristic presence check only; the authoritative schema is output_contract).
SIX_ELEMENT_ALIASES = {
    "hypotheses_variables": ["hypothes", "variable", "assumption", "假设", "变量"],
    "controls": ["control", "对照", "control_group"],
    "equipment_materials": ["equipment", "material", "reagent", "设备", "材料", "试剂"],
    "time_cost": ["time_cost", "cost", "budget", "schedule", "时间", "成本", "预算", "排期"],
    "parameter_ranges": ["parameter", "range", "参数", "量程", "范围"],
    "risks_abnormalities": ["risk", "abnormal", "failure", "风险", "异常", "失败"],
}


# --------------------------------------------------------------------------- #
# JSON / filesystem helpers
# --------------------------------------------------------------------------- #

def load_json(path, errors=None):
    """Read JSON with utf-8-sig, then latin-1 fallback. Never raises."""
    errs = errors if errors is not None else []
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            errs.append({"file": path, "error": "decode_failed: %s" % exc})
            return None
    except json.JSONDecodeError as exc:
        errs.append({"file": path, "error": "json_decode_failed: %s" % exc})
        return None
    except OSError as exc:
        errs.append({"file": path, "error": "read_failed: %s" % exc})
        return None


def atomic_write_json(path, obj):
    """Write JSON atomically (tmp + os.replace), fixed key order, utf-8."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def atomic_write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #

def find_case_json(input_dir, degradations):
    """Locate case.json; prefer exact name, then any *.json containing a
    research goal or output_contract."""
    exact = os.path.join(input_dir, "case.json")
    if os.path.isfile(exact):
        return exact
    candidates = []
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            if name.lower().endswith(".json"):
                candidates.append(os.path.join(root, name))
    if candidates:
        return candidates[0]
    degradations.append({"scope": "input", "detail": "case.json not found"})
    return None


def read_text_limited(path, limit=2 * 1024 * 1024):
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > limit:
        return None, "size_exceeds_%d" % limit
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, OSError):
            continue
    return None, "read_failed"


def extract_output_contract(obj):
    """Recursively find the first 'output_contract' key in a JSON structure."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "output_contract" and isinstance(val, dict):
                return val
        for val in obj.values():
            found = extract_output_contract(val)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = extract_output_contract(item)
            if found is not None:
                return found
    return None


def fallback_contract():
    """Fallback schema when the case has no output_contract."""
    return {
        "plan": {
            "hypotheses_and_variables": {},
            "controls": [],
            "equipment_materials": [],
            "time_cost": {},
            "parameter_ranges": [],
            "risks_and_abnormalities": [],
        },
        "evidence_and_risks": {"evidence_map": [], "risks": []},
        "confirmations": [],
    }


def extract_pdf_text(pdf_path, work_dir, degradations):
    """Extract PDF text with pdftotext if available; else degrade honestly."""
    tool = shutil.which("pdftotext")
    if not tool:
        degradations.append({"scope": "pdf", "detail": "pdftotext not available; PDF text not extracted"})
        return None
    out_dir = os.path.join(work_dir, "pdftotext")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = os.path.join(out_dir, base + ".txt")
    try:
        import subprocess
        proc = subprocess.run(
            [tool, "-layout", pdf_path, out_path],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not os.path.isfile(out_path):
            degradations.append({"scope": "pdf", "detail": "pdftotext failed for %s" % pdf_path})
            return None
        text, err = read_text_limited(out_path)
        if err:
            degradations.append({"scope": "pdf", "detail": "pdftotext output %s" % err})
        return text
    except Exception as exc:  # noqa: BLE001
        degradations.append({"scope": "pdf", "detail": "pdftotext error: %s" % exc})
        return None


def scan_input(input_dir, work_dir):
    """Produce an input_probe dict. Never raises on bad input."""
    probe = {
        "input_dir": input_dir,
        "case_json": None,
        "task_md": None,
        "case_id": None,
        "goal": None,
        "output_contract": None,
        "contract_source": None,
        "materials": [],
        "pdfs": [],
        "candidate_plans": [],
        "other_files": [],
        "errors": [],
        "degradations": [],
        "probe_ts": now_iso(),
    }
    if not os.path.isdir(input_dir):
        probe["errors"].append({"scope": "input", "detail": "input dir missing: %s" % input_dir})
        return probe

    case_json = find_case_json(input_dir, probe["degradations"])
    probe["case_json"] = case_json
    case_obj = None
    if case_json:
        errs = []
        case_obj = load_json(case_json, errs)
        probe["errors"].extend(errs)
        if case_obj is None:
            probe["degradations"].append({"scope": "case.json", "detail": "case.json unreadable/invalid; treated as absent"})

    if isinstance(case_obj, dict):
        probe["case_id"] = case_obj.get("case_id") or case_obj.get("id")
        probe["goal"] = case_obj.get("goal") or case_obj.get("objective") or case_obj.get("research_goal")
        contract = extract_output_contract(case_obj)
        if contract is not None:
            probe["output_contract"] = contract
            probe["contract_source"] = "case.json#output_contract"

    # task.md for challenge_id / runtime notes
    task_md = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_md):
        probe["task_md"] = task_md
        text, err = read_text_limited(task_md)
        if err:
            probe["degradations"].append({"scope": "task.md", "detail": err})
        elif text:
            m = re.search(r"challenge_id[：:]\s*`?([A-Za-z0-9_-]+)", text)
            if m and not probe["case_id"]:
                probe["case_id"] = m.group(1)

    if not probe["case_id"]:
        probe["case_id"] = "unknown"
        probe["errors"].append({"scope": "case_id", "detail": "case_id/challenge_id not found"})

    # walk for materials
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            if path == case_json or path == task_md:
                continue
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            if ext == ".pdf":
                text = extract_pdf_text(path, work_dir, probe["degradations"])
                probe["pdfs"].append({"path": rel, "text_extracted": text is not None,
                                      "text_file": os.path.join("pdftotext", os.path.splitext(name)[0] + ".txt") if text is not None else None})
            elif "materials" in rel.split("/") or ext in (".md", ".txt", ".csv", ".tsv"):
                _text, err = read_text_limited(path)
                probe["materials"].append({"path": rel, "readable": err is None})
                if err:
                    probe["degradations"].append({"scope": rel, "detail": err})
            else:
                probe["other_files"].append({"path": rel})

    if probe["output_contract"] is None:
        probe["output_contract"] = fallback_contract()
        probe["contract_source"] = "fallback"
        probe["degradations"].append({"scope": "output_contract", "detail": "no output_contract in case; using fallback schema"})

    return probe


# --------------------------------------------------------------------------- #
# Contract materialisation / coverage
# --------------------------------------------------------------------------- #

def materialize_contract(contract):
    """Echo every contract key; fill missing with null/[] without renaming."""
    if isinstance(contract, dict):
        return {k: materialize_contract(v) for k, v in contract.items()}
    if isinstance(contract, list):
        return []
    return contract


def filled_status(value):
    if value is None or value == "" or value == [] or value == {}:
        return "empty"
    return "filled"


def contract_coverage(contract, payload):
    """Flat map key_path -> filled|empty, over every contract key node."""
    out = {}

    def walk(c, p, path):
        if isinstance(c, dict):
            out[path or "(root)"] = filled_status(p)
            for k, v in c.items():
                sub = path + "/" + k if path else k
                walk(v, p.get(k) if isinstance(p, dict) else None, sub)
        elif isinstance(c, list):
            out[path or "(root)"] = filled_status(p)
        else:
            out[path or "(root)"] = filled_status(p)

    walk(contract, payload, "")
    return out


def contract_key_paths(contract):
    paths = []

    def walk(c, path):
        if isinstance(c, dict):
            for k, v in c.items():
                sub = path + "/" + k if path else k
                paths.append(sub)
                walk(v, sub)
        elif isinstance(c, list):
            paths.append(path)

    walk(contract, "")
    return paths


# --------------------------------------------------------------------------- #
# Scaffold
# --------------------------------------------------------------------------- #

def _probe_or_load(args, degrade):
    probe_path = os.path.join(args.work, "input_probe.json")
    if os.path.isfile(probe_path):
        probe = load_json(probe_path)
        if probe is not None:
            return probe
    probe = scan_input(args.input, args.work)
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(probe_path, probe)
    return probe


def build_result(probe):
    contract = probe.get("output_contract") or fallback_contract()
    payload = materialize_contract(contract)
    # strip reserved envelope keys from payload (they are ours)
    payload = {k: v for k, v in payload.items() if k not in RESERVED_ENVELOPE}
    result = {
        "case_id": probe.get("case_id") or "unknown",
    }
    result.update(payload)
    has_evidence = bool(probe.get("case_json") or probe.get("goal")
                        or probe.get("materials") or probe.get("pdfs"))
    status = "partial" if has_evidence else "insufficient_evidence"
    result.update({
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": list(probe.get("errors") or []),
        "degradations": list(probe.get("degradations") or []),
        "runtime": {"started": probe.get("probe_ts") or now_iso(), "finished": None},
        "contract_coverage": contract_coverage(contract, payload),
        "change_log": [{"ts": now_iso(), "event": "skeleton_written",
                        "detail": "contract keys materialised from %s" % probe.get("contract_source")}],
    })
    return result, contract, payload


def write_plan_skeleton(output_dir, probe, contract):
    lines = [
        "# 实验方案（草稿）",
        "",
        "- case_id: `%s`" % probe.get("case_id"),
        "- contract_source: `%s`" % probe.get("contract_source"),
        "- status: partial（骨架，待增量填写）",
        "",
        "## 待填要素",
        "",
    ]
    paths = contract_key_paths(contract)
    if paths:
        lines.append("契约键路径（需逐条填满并在 result.json 中保持一致）：")
        for p in paths:
            lines.append("- `%s`" % p)
    else:
        lines.append("（无契约键路径）")
    lines += ["", "> 本文件为占位骨架；正式方案需覆盖六要素、依据与风险、确认项。"]
    text = "\n".join(lines) + "\n"
    os.makedirs(output_dir, exist_ok=True)
    atomic_write_text(os.path.join(output_dir, "plan.md"), text)


def cmd_scaffold(args):
    degrade = []
    try:
        probe = _probe_or_load(args, degrade)
        result, contract, payload = build_result(probe)
        os.makedirs(args.output, exist_ok=True)
        atomic_write_json(os.path.join(args.output, "result.json"), result)
        write_plan_skeleton(args.output, probe, contract)
        os.makedirs(args.work, exist_ok=True)
        atomic_write_json(os.path.join(args.work, "contract_map.json"),
                          {"source": probe.get("contract_source"),
                           "key_paths": contract_key_paths(contract),
                           "coverage": result["contract_coverage"]})
        print("scaffold: wrote result.json + plan.md (status=partial)")
        return 0
    except Exception as exc:  # noqa: BLE001
        # Last-resort minimal legal result; never crash.
        try:
            os.makedirs(args.output, exist_ok=True)
            atomic_write_json(os.path.join(args.output, "result.json"), {
                "case_id": "unknown", "status": "input_error",
                "errors": [{"scope": "scaffold", "detail": str(exc)}],
                "change_log": [{"ts": now_iso(), "event": "scaffold_failed", "detail": str(exc)}],
            })
        except OSError:
            pass
        print("scaffold: degraded to minimal result (%s)" % exc)
        return 1


# --------------------------------------------------------------------------- #
# Patch (deep merge into result.json)
# --------------------------------------------------------------------------- #

def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch


def cmd_patch(args):
    result_path = os.path.join(args.output, "result.json")
    result = load_json(result_path)
    if result is None:
        print("patch: result.json missing/invalid; run scaffold first")
        return 1
    patch = load_json(args.patch)
    if patch is None:
        print("patch: patch file missing/invalid")
        return 1
    if not isinstance(patch, dict):
        print("patch: patch must be a JSON object")
        return 1
    # protect envelope keys from accidental overwrite
    protected = {k: v for k, v in patch.items() if k in RESERVED_ENVELOPE}
    payload_keys = {k: v for k, v in patch.items() if k not in RESERVED_ENVELOPE}
    for k in protected:
        print("patch: ignoring reserved envelope key '%s'; edit via validate/manual" % k)
    result = deep_merge(result, payload_keys)
    result.setdefault("change_log", [])
    result["change_log"].append({
        "ts": now_iso(), "event": "patched",
        "detail": "merged top-level keys: %s" % (", ".join(sorted(payload_keys)) or "(none)"),
    })
    # keep coverage current against the known contract (if available)
    probe_path = os.path.join(args.work, "input_probe.json")
    probe = load_json(probe_path) if os.path.isfile(probe_path) else None
    contract = (probe or {}).get("output_contract") or fallback_contract()
    result["contract_coverage"] = contract_coverage(contract, result)
    atomic_write_json(result_path, result)
    print("patch: merged %d top-level key(s) into result.json" % len(payload_keys))
    return 0


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def find_six_elements(payload):
    """Heuristic location of six elements by key aliases. Returns dict element -> list of paths."""
    found = {k: [] for k in SIX_ELEMENT_ALIASES}

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                sub = path + "/" + k if path else k
                kl = k.lower()
                for elem, aliases in SIX_ELEMENT_ALIASES.items():
                    if any(a in kl for a in aliases):
                        found[elem].append(sub)
                walk(v, sub)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, "%s[%d]" % (path, i))

    walk(payload, "")
    return found


def safe_eval(expr):
    """Evaluate arithmetic expressions with numbers and + - * / ( ) only."""
    tree = ast.parse(expr, mode="eval")
    ops = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.USub: lambda a: -a,
        ast.UAdd: lambda a: a,
    }

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        raise ValueError("unsupported expression node")

    return ev(tree)


def check_numeric_invariants(payload, issues):
    """Generic checks: min<=max, non-negative durations/costs, arithmetic_checks."""
    def walk(node, path):
        if isinstance(node, dict):
            if "min" in node and "max" in node:
                mn, mx = node["min"], node["max"]
                if isinstance(mn, (int, float)) and isinstance(mx, (int, float)) and mn > mx:
                    issues.append({"level": "error", "path": path, "detail": "min > max"})
            for k in ("duration_min", "cost", "budget", "time_h", "amount"):
                if k in node and isinstance(node[k], (int, float)) and node[k] < 0:
                    issues.append({"level": "warning", "path": path + "/" + k, "detail": "negative value"})
            for k, v in node.items():
                walk(v, path + "/" + k if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, "%s[%d]" % (path, i))

    walk(payload, "")

    checks = payload.get("arithmetic_checks") if isinstance(payload, dict) else None
    if isinstance(checks, list):
        for i, chk in enumerate(checks):
            if not isinstance(chk, dict):
                continue
            expr = chk.get("expression") or chk.get("expr")
            expected = chk.get("expected")
            if not isinstance(expr, str) or not isinstance(expected, (int, float)):
                issues.append({"level": "warning", "path": "arithmetic_checks[%d]" % i,
                               "detail": "missing expression/expected"})
                continue
            try:
                got = safe_eval(expr)
            except Exception as exc:  # noqa: BLE001
                issues.append({"level": "warning", "path": "arithmetic_checks[%d]" % i,
                               "detail": "cannot evaluate %r: %s" % (expr, exc)})
                continue
            if abs(got - expected) > 1e-9:
                issues.append({"level": "error", "path": "arithmetic_checks[%d]" % i,
                               "detail": "%r = %r != expected %r" % (expr, got, expected)})


def check_evidence(evidence_map, issues):
    if not isinstance(evidence_map, list):
        return
    for i, ev in enumerate(evidence_map):
        if not isinstance(ev, dict):
            issues.append({"level": "warning", "path": "evidence_map[%d]" % i, "detail": "not an object"})
            continue
        loc = ev.get("locator") or ev.get("location")
        quote = ev.get("quote")
        if not loc or not isinstance(loc, str) or not loc.strip():
            issues.append({"level": "error", "path": "evidence_map[%d]" % i, "detail": "missing locator"})
        if not quote or not isinstance(quote, str) or not quote.strip():
            issues.append({"level": "error", "path": "evidence_map[%d]" % i, "detail": "missing quote"})


def cmd_validate(args):
    result_path = os.path.join(args.output, "result.json")
    result = load_json(result_path)
    issues = []
    if result is None:
        issues.append({"level": "error", "path": "result.json", "detail": "missing or invalid JSON"})
        atomic_write_json(os.path.join(args.work, "validate_report.json"),
                          {"issues": issues, "verdict": "must_fix", "ts": now_iso()})
        print("validate: MUST_FIX (result.json missing/invalid)")
        return 1

    # re-load contract (probe may be stale after patches; use scaffold contract)
    probe_path = os.path.join(args.work, "input_probe.json")
    probe = load_json(probe_path) if os.path.isfile(probe_path) else None
    contract = (probe or {}).get("output_contract") or fallback_contract()

    if "case_id" not in result or not result.get("case_id"):
        issues.append({"level": "error", "path": "case_id", "detail": "missing"})
    status = result.get("status")
    if status not in VALID_STATUS:
        issues.append({"level": "error", "path": "status", "detail": "invalid status %r" % status})

    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if key not in result:
            issues.append({"level": "error", "path": key, "detail": "missing envelope field"})
    if isinstance(result.get("change_log"), list) and not result["change_log"]:
        issues.append({"level": "error", "path": "change_log", "detail": "empty"})

    # contract completeness: every key path present in result
    for p in contract_key_paths(contract):
        node = result
        ok = True
        for part in p.split("/"):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if not ok:
            issues.append({"level": "error", "path": p, "detail": "contract key missing"})

    # refresh coverage
    coverage = contract_coverage(contract, result)
    result["contract_coverage"] = coverage
    atomic_write_json(result_path, result)

    # payload-level checks (exclude envelope)
    payload = {k: v for k, v in result.items() if k not in RESERVED_ENVELOPE}
    check_numeric_invariants(payload, issues)
    evidence_map = _find_evidence_map(payload)
    check_evidence(evidence_map, issues)

    # six-element heuristic presence (warnings only)
    six = find_six_elements(payload)
    for elem, paths in six.items():
        if not paths:
            issues.append({"level": "warning", "path": "six_elements/" + elem,
                           "detail": "no key matched element '%s'; verify the element is filled" % elem})

    plan_md = os.path.join(args.output, "plan.md")
    if not os.path.isfile(plan_md) or os.path.getsize(plan_md) == 0:
        issues.append({"level": "warning", "path": "plan.md", "detail": "missing or empty"})

    blocking = [i for i in issues if i["level"] == "error"]
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "validate_report.json"),
                      {"issues": issues, "verdict": "must_fix" if blocking else "usable",
                       "coverage": coverage, "ts": now_iso()})
    print("validate: %s (%d errors, %d warnings)" %
          ("MUST_FIX" if blocking else "USABLE", len(blocking), len(issues) - len(blocking)))
    for i in issues:
        print("  [%s] %s: %s" % (i["level"], i["path"], i["detail"]))
    return 1 if blocking else 0


def _find_evidence_map(payload):
    """Recursively locate a list named evidence_map/evidence."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in ("evidence_map", "evidence") and isinstance(v, list):
                return v
        for v in payload.values():
            found = _find_evidence_map(v)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_evidence_map(item)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- #
# Run (probe + scaffold + validate)
# --------------------------------------------------------------------------- #

def cmd_run(args):
    print("run: probe")
    probe = scan_input(args.input, args.work)
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "input_probe.json"), probe)
    print("run: scaffold")
    rc1 = cmd_scaffold(args)
    print("run: validate")
    rc2 = cmd_validate(args)
    return 1 if (rc1 or rc2) else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="experiment-plan-optimizer CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("probe", "scan input and write input_probe.json"),
        ("scaffold", "write partial result.json + plan.md skeleton"),
        ("patch", "deep-merge a JSON patch into result.json"),
        ("validate", "structural/type/arithmetic/evidence self-check"),
        ("run", "probe + scaffold + validate"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)
        if name == "patch":
            p.add_argument("--patch", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "probe":
        probe = scan_input(args.input, args.work)
        os.makedirs(args.work, exist_ok=True)
        atomic_write_json(os.path.join(args.work, "input_probe.json"), probe)
        print("probe: case_id=%s contract_source=%s" % (probe["case_id"], probe["contract_source"]))
        print("probe: %d material(s), %d pdf(s)" % (len(probe["materials"]), len(probe["pdfs"])))
        return 0
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "patch":
        return cmd_patch(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
