#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""experiment-plan-ir: intermediate-representation driven experiment plan generator.

Deterministic, standard-library-only pipeline:

    probe    -> inventory /input (text + PDF extraction, output_contract discovery,
                case_id / goal / candidates / constraints seeding)
    scaffold -> early valid partial result.json + plan.ir.json skeleton + plan.md
    build    -> normalize IR, run structural/arithmetic/evidence audits, render
                result.json / plan.md / plan.ir.json / checks.json
    validate -> re-check written artifacts (structure, contract fidelity, arithmetic,
                evidence quote verification, cross-file consistency)

No network, no third-party packages, no fabricated scientific data or scores.
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

IR_VERSION = "1.0"
MAX_TEXT_BYTES = 2 * 1024 * 1024
CORPUS_CHAR_CAP = 400_000
TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".log", ".py",
    ".tex", ".html", ".htm", ".xml", ".rst", ".ini", ".cfg", ".bib",
}
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
DEFAULT_CONTRACT = {"plan": {}, "evidence_and_risks": {}, "confirmations": {}}

LIST_NODE_KEYS = [
    "hypotheses", "variables", "controls", "steps", "parameters", "resources",
    "readout_criteria", "risks", "confirmations", "candidates", "evidence",
    "assumptions", "conflicts",
]
NODE_PREFIX = {
    "hypotheses": "H", "variables": "V", "controls": "C", "steps": "S",
    "parameters": "P", "resources": "R", "readout_criteria": "RC",
    "risks": "RSK", "confirmations": "CF", "candidates": "CAND",
    "evidence": "E", "assumptions": "A", "conflicts": "X",
}
VARIABLE_ROLES = ("independent", "dependent", "controlled", "covariate", "confounder")
CONTROL_TYPES = ("blank", "positive", "negative", "vehicle", "sham", "dose_gradient", "technical", "biological")
PARAM_SOURCES = ("manual", "preliminary", "literature", "organizer_contract", "vendor", "derived", "unknown")
RESOURCE_KINDS = ("equipment", "consumable", "reagent", "software", "personnel", "facility")
CONFIRM_CATEGORIES = ("safety", "ethics", "equipment_availability", "expert", "resource", "data", "budget", "other")
CONFIRM_STATUSES = ("pending", "confirmed", "not_applicable", "unknown")


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #

def _now_iso():
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.lstrip("-").isdigit():
        try:
            return datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc).isoformat(timespec="seconds")
        except (ValueError, OSError, OverflowError):
            pass
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _setup_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def decode_bytes(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8-replace"


def read_text_file(path):
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, {"error": "read_failed", "detail": str(exc)}
    if len(data) > MAX_TEXT_BYTES:
        return None, {"error": "too_large", "bytes": len(data)}
    text, enc = decode_bytes(data)
    return text, {"encoding": enc, "bytes": len(data)}


def load_json_loose(path):
    data = None
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, {"error": "read_failed", "detail": str(exc)}
    if len(data) > MAX_TEXT_BYTES:
        return None, {"error": "too_large", "bytes": len(data)}
    text, enc = decode_bytes(data)
    try:
        return json.loads(text), {"encoding": enc, "bytes": len(data)}
    except json.JSONDecodeError as exc:
        return None, {"error": "json_decode_error",
                      "detail": "%s at line %s col %s" % (exc.msg, exc.lineno, exc.colno),
                      "encoding": enc}


def load_json(path):
    if not path.exists():
        return None
    obj, meta = load_json_loose(path)
    return obj


def json_canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ir_digest(ir):
    stripped = {k: v for k, v in ir.items() if k != "_meta"}
    return hashlib.sha256(json_canonical(stripped).encode("utf-8")).hexdigest()


def sha256_of_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def write_json_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def as_dict(v):
    if isinstance(v, dict):
        return v
    return {}


def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.strip())
        if m:
            return float(m.group())
    return None


def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_compact(s):
    return re.sub(r"\s+", "", (s or ""))


def _cell(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (list, tuple)):
        if not v:
            return "—"
        return "、".join(_cell(x) for x in v)
    if isinstance(v, dict):
        parts = []
        for k, vv in v.items():
            if vv not in (None, "", [], {}):
                parts.append("%s=%s" % (k, _cell(vv)))
        return "; ".join(parts) if parts else "—"
    return str(v).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_cell(c) for c in r) + " |")
    return "\n".join(out)


def _display_range(allow, unit):
    if isinstance(allow, (list, tuple)) and len(allow) == 2:
        lo, hi = allow
        return "%s–%s %s" % (lo if lo is not None else "?", hi if hi is not None else "?", unit or "")
    return "%s %s" % (allow, unit or "") if allow is not None else "—"


# --------------------------------------------------------------------------- #
# Input discovery (probe)
# --------------------------------------------------------------------------- #

def input_signature(input_dir):
    items = []
    if input_dir.is_dir():
        try:
            for p in sorted(input_dir.rglob("*")):
                if p.is_file():
                    st = p.stat()
                    items.append([p.relative_to(input_dir).as_posix(), st.st_size, st.st_mtime_ns])
        except OSError:
            return None
    return items


def _looks_text(data):
    return b"\x00" not in data[:4096]


def extract_pdf_text(path, degradations):
    exe = shutil.which("pdftotext")
    rel = path.name
    if not exe:
        degradations.append({"code": "pdftotext_unavailable", "path": rel,
                             "detail": "pdftotext 不可用，无法提取 PDF 正文"})
        return "", None, "unavailable"
    try:
        proc = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        degradations.append({"code": "pdftotext_failed", "path": rel, "detail": str(exc)})
        return "", None, "failed"
    if proc.returncode != 0:
        tail = proc.stderr[-300:].decode("utf-8", "replace") if proc.stderr else ""
        degradations.append({"code": "pdftotext_failed", "path": rel,
                             "detail": "exit=%s %s" % (proc.returncode, tail)})
        return "", None, "failed"
    text, enc = decode_bytes(proc.stdout)
    pages = text.split("\f")
    return text, pages, "pdftotext"


def _find_key(obj, names, max_depth=4, _path=""):
    if not isinstance(obj, dict) or max_depth <= 0:
        return None, None
    for k, v in obj.items():
        cur = (_path + "." + k).strip(".")
        if isinstance(k, str) and k in names and isinstance(v, dict):
            return v, cur
    for k, v in obj.items():
        cur = (_path + "." + k).strip(".")
        if isinstance(v, dict):
            found, p = _find_key(v, names, max_depth - 1, cur)
            if found is not None:
                return found, p
    return None, None


def _pick_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip(), "case." + k
    if task_text:
        m = re.search(r"challenge_id\s*[:：]\s*`?([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1), "task.md"
        m = re.search(r"\bcase[-_ ]?id\s*[:：]\s*`?([A-Za-z0-9._-]+)", task_text, re.I)
        if m:
            return m.group(1), "task.md"
    return None, None


def _pick_goal(case, task_text):
    if isinstance(case, dict):
        for key in ("goal", "research_goal", "objective", "aim", "question"):
            v = case.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip(), "case." + key
        for key in ("goal", "objective"):
            found, p = _find_key(case, (key,), 3)
            if found:
                for inner in ("statement", "text", "description", "goal"):
                    v = found.get(inner) if isinstance(found, dict) else None
                    if isinstance(v, str) and v.strip():
                        return v.strip(), p + "." + inner
    if task_text:
        m = re.search(r"^#\s+(.+)$", task_text, re.M)
        if m:
            return m.group(1).strip(), "task.md"
    return None, None


def _find_list_of_dicts(case, names):
    if not isinstance(case, dict):
        return None, None
    for k, v in case.items():
        if k in names and isinstance(v, list):
            return v, "case." + k
    for k, v in case.items():
        if isinstance(v, dict):
            found, p = _find_list_of_dicts(v, names)
            if found is not None:
                return found, (k + "." + p).strip(".")
    return None, None


def _seed_candidates(case):
    cands, path = _find_list_of_dicts(case, ("candidates", "candidate_plans", "plans", "options", "versions"))
    if not cands:
        return [], None
    out = []
    for idx, raw in enumerate(cands, 1):
        if not isinstance(raw, dict):
            continue
        item = {
            "id": str(raw.get("id") or raw.get("name") or raw.get("label") or "CAND%d" % idx),
            "label": raw.get("label") or raw.get("name") or raw.get("title") or raw.get("id") or ("CAND%d" % idx),
            "summary": raw.get("summary") or raw.get("description") or raw.get("note"),
            "parameter_choices": as_dict(raw.get("parameter_choices") or raw.get("parameters")),
            "cost_total": to_num(raw.get("cost_total") or raw.get("cost")),
            "wall_clock_minutes": to_num(raw.get("wall_clock_minutes") or raw.get("duration_min")),
            "source": path,
            "status": "seeded",
        }
        try:
            rtxt = json.dumps(raw, ensure_ascii=False)
            if len(rtxt) <= 2000:
                item["raw"] = raw
            else:
                item["raw_ref"] = path
        except (TypeError, ValueError):
            item["raw_ref"] = path
        out.append(item)
    return out, path


def _seed_constraints(case):
    out, src = {}, {}
    if not isinstance(case, dict):
        return out, src

    def num_from(keys, subkeys=("value", "cap", "limit", "max", "amount")):
        for k in keys:
            v = case.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v), "case." + k
            if isinstance(v, dict):
                for sk in subkeys:
                    vv = v.get(sk)
                    if isinstance(vv, (int, float)) and not isinstance(vv, bool):
                        return float(vv), "case.%s.%s" % (k, sk)
        return None, None

    v, p = num_from(("budget_cap", "budget", "max_cost", "cost_limit", "total_budget"))
    if v is not None:
        out["budget_cap"] = v
        src["budget_cap"] = p
    v, p = num_from(("time_window_minutes", "time_limit_min", "max_minutes"))
    if v is not None:
        out["time_window_minutes"] = v
        src["time_window_minutes"] = p
    v, p = num_from(("time_window_hours", "time_limit_hours"))
    if v is not None:
        out["time_window_minutes"] = v * 60.0
        src["time_window_minutes"] = p + "(hours→min)"
    cur = case.get("currency") or (case.get("budget") or {}).get("currency") if isinstance(case.get("budget"), dict) else None
    if isinstance(cur, str):
        out["currency"] = cur
        src["currency"] = "case.currency"
    for k in ("parameter_domains", "parameter_ranges", "domains"):
        d = case.get(k)
        if isinstance(d, dict):
            out["parameter_domains"] = d
            src["parameter_domains"] = "case." + k
            break
    for k in ("unavailable_resources", "unavailable", "out_of_stock"):
        d = case.get(k)
        if isinstance(d, list):
            out["unavailable_resources"] = d
            src["unavailable_resources"] = "case." + k
            break
    return out, src


def probe(input_dir, work_dir, signature):
    work_dir.mkdir(parents=True, exist_ok=True)
    errors, degradations = [], []
    corpus = {}
    files = []

    case_path = input_dir / "case.json"
    case, case_meta = (None, {"error": "case_json_missing"})
    if case_path.exists():
        case, case_meta = load_json_loose(case_path)
    task_path = input_dir / "task.md"
    task_text, task_meta = (None, {"error": "task_md_missing"})
    if task_path.exists():
        task_text, task_meta = read_text_file(task_path)

    if input_dir.is_dir():
        try:
            candidates = sorted(input_dir.rglob("*"))
        except OSError as exc:
            candidates = []
            errors.append({"code": "input_scan_failed", "detail": str(exc)})
        for p in candidates:
            if not p.is_file():
                continue
            rel = p.relative_to(input_dir).as_posix()
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            ext = p.suffix.lower()
            entry = {"path": rel, "bytes": size, "kind": "unknown", "chars": 0,
                     "extraction": "skipped", "sha256": None}
            if size < 0:
                entry["extraction"] = "stat_failed"
                files.append(entry)
                continue
            if size > MAX_TEXT_BYTES:
                entry["extraction"] = "too_large_skipped"
                degradations.append({"code": "file_too_large", "path": rel, "bytes": size})
                files.append(entry)
                continue
            if ext == ".pdf":
                text, pages, mode = extract_pdf_text(p, degradations)
                entry["kind"] = "pdf"
                entry["extraction"] = mode
                entry["text"] = text[:CORPUS_CHAR_CAP]
                if pages:
                    total = 0
                    capped_pages = []
                    for pg in pages:
                        if total >= CORPUS_CHAR_CAP:
                            break
                        piece = pg[:CORPUS_CHAR_CAP - total]
                        capped_pages.append(piece)
                        total += len(piece)
                    entry["pages"] = capped_pages
                entry["chars"] = len(text)
                entry["sha256"] = sha256_of_file(p)
            elif ext in TEXT_EXTS or ext == "":
                data = None
                try:
                    data = p.read_bytes()
                except OSError:
                    entry["extraction"] = "read_failed"
                    files.append(entry)
                    continue
                if ext == "" and not _looks_text(data):
                    entry["extraction"] = "binary_skipped"
                    files.append(entry)
                    continue
                text, enc = decode_bytes(data)
                entry["kind"] = ext.lstrip(".") or "text"
                entry["extraction"] = "text"
                entry["encoding"] = enc
                entry["text"] = text[:CORPUS_CHAR_CAP]
                entry["chars"] = len(text)
                entry["sha256"] = sha256_of_file(p)
            else:
                entry["extraction"] = "binary_skipped"
                files.append(entry)
                continue
            corpus[rel] = entry
            files.append(entry)

    contract, contract_path = (None, None)
    if isinstance(case, dict):
        contract, contract_path = _find_key(case, ("output_contract", "outputContract"), 4)
        if contract is None:
            for k in ("output_contract", "outputContract"):
                if isinstance(case.get(k), dict):
                    contract, contract_path = case[k], "case." + k
    if contract is None:
        contract = {}
        contract_path = None
    contract_keys = list(contract.keys()) if isinstance(contract, dict) else []

    case_id, case_id_source = _pick_case_id(case, task_text)
    goal, goal_source = _pick_goal(case, task_text)
    cands, cand_path = _seed_candidates(case)
    cons, cons_src = _seed_constraints(case)

    raw_constraint_keys = []
    if isinstance(case, dict):
        for k in ("constraints", "limits", "budget", "equipment", "resources", "parameters",
                  "parameter_domains", "time_window", "bookings", "prices", "contracts"):
            if k in case:
                raw_constraint_keys.append("case." + k)

    material_index = []
    for rel, e in sorted(corpus.items()):
        if rel in ("case.json", "task.md"):
            continue
        material_index.append({"path": rel, "kind": e.get("kind"), "chars": e.get("chars"),
                               "sha256": e.get("sha256"), "extraction": e.get("extraction")})

    input_state = "ok"
    has_any_file = False
    if input_dir.is_dir():
        try:
            has_any_file = any(p.is_file() for p in input_dir.rglob("*"))
        except OSError:
            has_any_file = False
    if not input_dir.exists() or not has_any_file:
        input_state = "empty"
    elif isinstance(case_meta, dict) and case_meta.get("error") in ("json_decode_error", "read_failed") and not material_index:
        input_state = "case_invalid_no_materials"
    elif isinstance(case_meta, dict) and case_meta.get("error") in ("json_decode_error", "read_failed"):
        input_state = "case_invalid"

    if case_meta.get("error") == "json_decode_error":
        errors.append({"code": "case_json_invalid", "detail": case_meta.get("detail")})

    report = {
        "probe_version": IR_VERSION,
        "signature": signature,
        "input_dir": str(input_dir),
        "work_dir": str(work_dir),
        "input_state": input_state,
        "case": {"found": case_path.exists(), "parsed": isinstance(case, dict),
                 "meta": case_meta},
        "case_id": case_id, "case_id_source": case_id_source,
        "goal": {"statement": goal, "source": goal_source},
        "contract": {"found": contract_path is not None, "path": contract_path,
                     "keys": contract_keys, "value": contract},
        "seed": {
            "candidates": cands, "candidates_path": cand_path,
            "constraints": cons, "constraints_sources": cons_src,
            "material_index": material_index,
            "raw_constraint_keys": raw_constraint_keys,
        },
        "files": files,
        "errors": errors, "degradations": degradations,
        "generated_at": _now_iso(),
    }
    write_json_atomic(work_dir / "input_probe.json", report)
    write_json_atomic(work_dir / "corpus.json",
                      {"input_dir": str(input_dir), "entries": corpus,
                       "generated_at": _now_iso()})
    return report


def get_probe(input_dir, work_dir, refresh=False):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    sig = input_signature(input_dir)
    cache_p = work_dir / "input_probe.json"
    corpus_p = work_dir / "corpus.json"
    if not refresh and cache_p.exists() and corpus_p.exists():
        rep = load_json(cache_p)
        if isinstance(rep, dict) and rep.get("signature") == sig:
            return rep
    return probe(input_dir, work_dir, sig)


def load_corpus(work_dir):
    obj = load_json(Path(work_dir) / "corpus.json")
    if isinstance(obj, dict):
        return obj.get("entries", {})
    return {}


# --------------------------------------------------------------------------- #
# IR seeding / normalization
# --------------------------------------------------------------------------- #

def empty_ir(case_id="unknown"):
    return {
        "ir_version": IR_VERSION,
        "case_id": case_id,
        "state": "skeleton",
        "goal": {"statement": None, "testable_question": None, "success_criteria": []},
        "assumptions": [],
        "hypotheses": [], "variables": [], "controls": [], "steps": [],
        "parameters": [], "resources": [], "readout_criteria": [],
        "candidates": [], "selection": {"chosen": None, "rationale": None, "selection_source": None},
        "constraints": {"budget_cap": None, "time_window_minutes": None, "currency": None,
                        "parameter_domains": {}, "unavailable_resources": []},
        "budget_schedule": {"sample_count": None, "replicates": None, "total_runs": None,
                            "wall_clock_minutes": None, "cost_items": [], "cost_total": None,
                            "currency": None, "sample_size_justification": None},
        "risks": [], "confirmations": [],
        "evidence": [], "conflicts": [],
        "intake": {"materials": [], "raw_constraints": {}},
        "notes": [],
    }


def seed_ir(rep):
    seed = rep.get("seed", {})
    ir = empty_ir(rep.get("case_id") or "unknown")
    goal = rep.get("goal", {})
    ir["goal"]["statement"] = goal.get("statement")
    ir["candidates"] = seed.get("candidates", [])
    cons = as_dict(seed.get("constraints"))
    ir["constraints"].update({k: cons[k] for k in ir["constraints"] if k in cons})
    ir["budget_schedule"]["currency"] = cons.get("currency")
    ir["intake"]["materials"] = seed.get("material_index", [])
    ir["intake"]["raw_constraints"] = {
        "keys": seed.get("raw_constraint_keys", []),
        "sources": as_dict(seed.get("constraints_sources")),
    }
    ir["notes"] = [{"ts": _now_iso(),
                    "event": "seeded_from_probe",
                    "detail": "case_id_source=%s; candidates=%d; materials=%d" % (
                        rep.get("case_id_source"), len(ir["candidates"]),
                        len(ir["intake"]["materials"]))}]
    return ir


def normalize_ir(ir):
    if not isinstance(ir, dict):
        ir = {}
    ir = dict(ir)
    ir.setdefault("ir_version", IR_VERSION)
    ir.setdefault("case_id", "unknown")
    ir.setdefault("state", "built")
    ir.setdefault("goal", {})
    ir["goal"] = as_dict(ir.get("goal"))
    ir["goal"].setdefault("statement", None)
    ir["goal"].setdefault("testable_question", None)
    ir["goal"].setdefault("success_criteria", [])

    for key in LIST_NODE_KEYS:
        ir[key] = [x for x in as_list(ir.get(key)) if isinstance(x, dict)]

    ir.setdefault("selection", {})
    ir["selection"] = as_dict(ir.get("selection"))
    ir["selection"].setdefault("chosen", None)
    ir["selection"].setdefault("rationale", None)
    ir["selection"].setdefault("selection_source", None)

    ir.setdefault("constraints", {})
    ir["constraints"] = as_dict(ir.get("constraints"))
    for k, d in (("budget_cap", None), ("time_window_minutes", None), ("currency", None)):
        ir["constraints"].setdefault(k, d)
    ir["constraints"].setdefault("parameter_domains", {})
    ir["constraints"].setdefault("unavailable_resources", [])

    ir.setdefault("budget_schedule", {})
    bs = as_dict(ir.get("budget_schedule"))
    for k in ("sample_count", "replicates", "total_runs", "wall_clock_minutes", "cost_total"):
        bs.setdefault(k, None)
    bs.setdefault("cost_items", [])
    bs.setdefault("currency", None)
    bs.setdefault("sample_size_justification", None)
    ir["budget_schedule"] = bs

    ir.setdefault("intake", {})
    ir["intake"] = as_dict(ir.get("intake"))
    ir["intake"].setdefault("materials", [])
    ir["intake"].setdefault("raw_constraints", {})
    ir.setdefault("notes", [])

    # assign stable ids
    used = set()
    for key, pref in NODE_PREFIX.items():
        for i, node in enumerate(ir[key], 1):
            nid = str(node.get("id") or "").strip()
            if not nid or nid in used:
                base = "%s%d" % (pref, i)
                nid = base
                while nid in used:
                    nid = base + "x"
            node["id"] = nid
            used.add(nid)

    # normalize field shapes (best effort, tolerant)
    for v in ir["variables"]:
        v.setdefault("role", None)
        m = v.setdefault("measurement", {})
        if not isinstance(m, dict):
            m = {"method": m}
            v["measurement"] = m
        m.setdefault("instrument", None)
        m.setdefault("method", None)
        m.setdefault("unit", None)
        v.setdefault("levels", None)
        v.setdefault("range", None)
    for p in ir["parameters"]:
        p.setdefault("value", None)
        p.setdefault("unit", None)
        p.setdefault("allowed", None)
        p.setdefault("source", None)
    for s in ir["steps"]:
        s.setdefault("duration_min", None)
        s.setdefault("depends_on", [])
        s.setdefault("equipment", [])
        s.setdefault("materials", [])
        s.setdefault("parameters", [])
        s.setdefault("acceptance", None)
    for c in ir["controls"]:
        c.setdefault("type", None)
        c.setdefault("excludes_confounder", None)
    for r in ir["risks"]:
        r.setdefault("detection_criterion", None)
        r.setdefault("fallback_path", None)
    for cf in ir["confirmations"]:
        cf.setdefault("category", None)
        cf.setdefault("status", "pending")
    for e in ir["evidence"]:
        e.setdefault("locator", None)
        e.setdefault("quote", None)
    for cand in ir["candidates"]:
        cand.setdefault("parameter_choices", {})
    return ir


def collect_gaps(ir):
    gaps = []
    g = ir.get("goal", {})
    if not (g.get("statement") or "").strip():
        gaps.append("goal.statement 为空：从 case.json / task.md 摘取研究目标原句")
    if not (g.get("testable_question") or "").strip():
        gaps.append("goal.testable_question 为空：把目标改写为可证伪的问题")
    if not ir.get("hypotheses"):
        gaps.append("hypotheses 为空：至少 1 条可证伪假设，附 falsifiable_by")
    if not ir.get("variables"):
        gaps.append("variables 为空：补自变量/因变量，含操作定义、单位、量程或水平")
    if not ir.get("controls"):
        gaps.append("controls 为空：补空白/阳性/阴性对照，写明排除哪类混杂")
    if not ir.get("steps"):
        gaps.append("steps 为空：补绑定设备/试剂/时长/验收判据的步骤")
    if not ir.get("parameters"):
        gaps.append("parameters 为空：补关键参数、单位、允许区间与来源")
    if not ir.get("resources"):
        gaps.append("resources 为空：补设备/耗材规格、产能与可替代项")
    if not ir.get("readout_criteria"):
        gaps.append("readout_criteria 为空：补判读标准（指标/阈值/决策）")
    if not ir.get("risks"):
        gaps.append("risks 为空：补失败模式、判据、止损点、替代路径")
    if not ir.get("confirmations"):
        gaps.append("confirmations 为空：补安全/伦理/设备可用性/专家确认项")
    if not ir.get("evidence"):
        gaps.append("evidence 为空：补 locator + 逐字 quote 的关键依据")
    bs = ir.get("budget_schedule", {})
    if bs.get("cost_total") is None:
        gaps.append("budget_schedule.cost_total 为空：补预算估算")
    if bs.get("wall_clock_minutes") is None:
        gaps.append("budget_schedule.wall_clock_minutes 为空：补排期估算")
    if not ir.get("candidates"):
        gaps.append("candidates 为空：案例常含 2–3 个版本化候选方案，未发现则显式记为 not_reported")
    sel = ir.get("selection", {})
    if ir.get("candidates") and not sel.get("chosen"):
        gaps.append("selection.chosen 为空：在候选方案中做可行性比较后选一个并给理由")
    return gaps


# --------------------------------------------------------------------------- #
# Audits
# --------------------------------------------------------------------------- #

def _resolve_locator(locator, corpus):
    raw = (locator or "").strip().replace("\\", "/")
    raw = re.sub(r"^/input/", "", raw)
    raw = re.sub(r"^input/", "", raw)
    raw = re.sub(r"^\./", "", raw)
    path_part, _, anchor = raw.partition("#")
    anchor = anchor.strip()
    entry = corpus.get(path_part)
    resolved = path_part if entry is not None else None
    if entry is None and path_part:
        # strip leading directories until match
        for key, val in corpus.items():
            if key == path_part or key.endswith("/" + path_part):
                entry, resolved = val, key
                break
    if entry is None and path_part:
        base = path_part.split("/")[-1]
        matches = [k for k in corpus if k.split("/")[-1] == base]
        if len(matches) == 1:
            entry, resolved = corpus[matches[0]], matches[0]
    page = None
    m = re.search(r"^p(?:age)?[-_]?(\d+)$", anchor)
    if m and entry and entry.get("pages"):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(entry["pages"]):
            page = idx + 1
    return entry, resolved, page


def verify_evidence(ir, corpus):
    out = []
    for e in ir.get("evidence", []):
        eid = e.get("id")
        quote = e.get("quote")
        locator = e.get("locator")
        rec = {"id": eid, "locator": locator, "status": None, "match_mode": None,
               "resolved_path": None, "page": None}
        if not quote or not str(quote).strip():
            rec["status"] = "incomplete"
            out.append(rec)
            continue
        if not locator:
            rec["status"] = "missing_locator"
            out.append(rec)
            continue
        entry, resolved, page = _resolve_locator(locator, corpus)
        rec["resolved_path"] = resolved
        rec["page"] = page
        if entry is None:
            rec["status"] = "source_missing"
            out.append(rec)
            continue
        target = entry.get("text") or ""
        if page is not None and entry.get("pages"):
            target = entry["pages"][page - 1]
        q = str(quote)
        if q in target:
            rec["status"], rec["match_mode"] = "verified", "exact"
        elif norm_ws(q) in norm_ws(target):
            rec["status"], rec["match_mode"] = "verified", "normalized"
        elif norm_compact(q) in norm_compact(target):
            rec["status"], rec["match_mode"] = "verified", "compact"
        else:
            rec["status"] = "not_found"
        out.append(rec)
    return out


def _topo_steps(steps):
    ids = [s.get("id") for s in steps if s.get("id")]
    dur = {}
    for s in steps:
        n = to_num(s.get("duration_min"))
        dur[s.get("id")] = n if n is not None else None
    deps = {}
    for s in steps:
        raw = as_list(s.get("depends_on"))
        deps[s.get("id")] = [d for d in raw if d in ids]
    indeg = {i: 0 for i in ids}
    for i in ids:
        for d in deps[i]:
            indeg[i] += 1
    order = []
    q = [i for i in ids if indeg[i] == 0]
    while q:
        n = q.pop(0)
        order.append(n)
        for i in ids:
            if n in deps[i]:
                indeg[i] -= 1
                if indeg[i] == 0:
                    q.append(i)
    cycle = len(order) != len(ids)
    return ids, dur, deps, order, cycle


def audit_arithmetic(ir, add):
    arith = {"steps": {}, "budget": {}, "parameters": {}, "candidates": []}
    steps = ir.get("steps", [])
    ids, dur, deps, order, cycle = _topo_steps(steps)
    if cycle:
        add("step_cycle", "error", "steps", "步骤依赖存在环，无法排期", ids)
    unknown_deps = []
    for s in steps:
        for d in as_list(s.get("depends_on")):
            if d not in ids:
                unknown_deps.append("%s→%s" % (s.get("id"), d))
    if unknown_deps:
        add("step_unknown_dependency", "error", "steps",
            "依赖了不存在的步骤：%s" % "、".join(unknown_deps), unknown_deps)

    cp_path, cp_len = None, 0.0
    if order and not cycle:
        dist = {i: (dur[i] if dur[i] is not None else 0.0) for i in ids}
        prev = {}
        for i in order:
            for d in deps[i]:
                if d in dist and (dur[i] or 0.0) + dist[d] > dist[i]:
                    dist[i] = dist[d] + (dur[i] or 0.0)
                    prev[i] = d
        end = max(ids, key=lambda i: dist[i]) if ids else None
        cp_len = dist[end] if end else 0.0
        path = []
        while end:
            path.append(end)
            end = prev.get(end)
        cp_path = list(reversed(path))
    groups = {}
    any_group = False
    for s in steps:
        g = s.get("parallel_group")
        if g not in (None, ""):
            any_group = True
            groups.setdefault(str(g), 0.0)
            groups[str(g)] = max(groups[str(g)], (dur.get(s.get("id")) or 0.0))
    if cp_path:
        computed = cp_len
        mode = "critical_path"
    elif any_group:
        computed = sum(groups.values())
        mode = "parallel_groups"
    else:
        computed = sum((dur.get(i) or 0.0) for i in ids)
        mode = "sum_durations"
    arith["steps"] = {"mode": mode, "computed_wall_clock_minutes": round(computed, 3),
                      "critical_path": cp_path, "unknown_deps": unknown_deps}
    declared = to_num(ir.get("budget_schedule", {}).get("wall_clock_minutes"))
    if declared is not None and ids:
        tol = max(1.0, abs(declared) * 0.05)
        if abs(declared - computed) > tol:
            add("wall_clock_mismatch", "error", "steps",
                "声明排期 %s min 与步骤推算 %s min（%s）不一致" % (declared, round(computed, 3), mode))
    arith["steps"]["declared_wall_clock_minutes"] = declared

    # parameters
    params = ir.get("parameters", [])
    p_index = {}
    for p in params:
        p_index[p.get("id")] = p
        p_index[str(p.get("name"))] = p
    doms = as_dict(ir.get("constraints", {}).get("parameter_domains"))
    arith["parameters"] = []
    for p in params:
        allow = p.get("allowed")
        lo = hi = None
        if isinstance(allow, (list, tuple)) and len(allow) == 2:
            lo, hi = to_num(allow[0]), to_num(allow[1])
        elif p.get("id") in doms and isinstance(doms[p.get("id")], (list, tuple)):
            lo, hi = to_num(doms[p.get("id")][0]), to_num(doms[p.get("id")][1])
        val = to_num(p.get("value"))
        verdict = {"id": p.get("id"), "value": val, "allowed": allow, "in_range": None, "source": p.get("source")}
        if val is not None and lo is not None and hi is not None:
            ok = lo <= val <= hi
            verdict["in_range"] = ok
            if not ok:
                add("parameter_out_of_range", "error", "parameters",
                    "参数 %s=%s %s 超出允许区间 [%s, %s]" % (p.get("id"), val, p.get("unit") or "", lo, hi), [p.get("id")])
        elif val is None:
            add("parameter_missing_value", "warn", "parameters", "参数 %s 缺取值" % p.get("id"), [p.get("id")])
        elif lo is None or hi is None:
            add("parameter_missing_range", "warn", "parameters",
                "参数 %s 缺允许区间，无法做量程校验" % p.get("id"), [p.get("id")])
            verdict["in_range"] = "unknown"
        if p.get("source") not in PARAM_SOURCES:
            add("parameter_source_unknown", "warn", "parameters",
                "参数 %s 来源未标明（manual/preliminary/literature/organizer_contract/vendor）" % p.get("id"), [p.get("id")])
        arith["parameters"].append(verdict)

    # budget
    bs = ir.get("budget_schedule", {})
    items = as_list(bs.get("cost_items"))
    total = 0.0
    item_rows = []
    budget_ok = True
    for it in items:
        if not isinstance(it, dict):
            continue
        qty = to_num(it.get("qty")) or 0.0
        unit = to_num(it.get("unit_cost"))
        subtotal = to_num(it.get("total"))
        if unit is None and subtotal is None:
            budget_ok = False
            add("budget_item_incomplete", "warn", "budget_schedule",
                "预算项 %s 缺少数量×单价或小计" % (it.get("item") or it.get("id")), [it.get("id")])
            continue
        if subtotal is None:
            subtotal = qty * unit
        total += subtotal
        item_rows.append({"item": it.get("item") or it.get("id"), "qty": qty,
                          "unit_cost": unit, "total": round(subtotal, 4)})
    declared_cost = to_num(bs.get("cost_total"))
    if declared_cost is not None and item_rows:
        if abs(declared_cost - total) > max(0.01, abs(total) * 0.005 + 0.01):
            add("budget_sum_mismatch", "error", "budget_schedule",
                "预算合计 %s 与逐项求和 %s 不一致" % (declared_cost, round(total, 4)))
    arith["budget"] = {"items": item_rows, "computed_total": round(total, 4),
                       "declared_total": declared_cost, "ok": budget_ok}

    cap = to_num(ir.get("constraints", {}).get("budget_cap"))
    if cap is not None and total > 0:
        if total > cap:
            add("budget_cap_exceeded", "error", "budget_schedule",
                "预算合计 %s 超过上限 %s" % (round(total, 4), cap))
    window = to_num(ir.get("constraints", {}).get("time_window_minutes"))
    if window is not None and computed > 0:
        if computed > window:
            add("time_window_exceeded", "error", "steps",
                "推算总时长 %s min 超过时间窗 %s min" % (round(computed, 3), window))

    # sample / runs
    sample = to_num(bs.get("sample_count"))
    repl = to_num(bs.get("replicates"))
    runs = to_num(bs.get("total_runs"))
    if sample is not None and repl is not None:
        expect = sample * repl
        if runs is not None and abs(runs - expect) > 0.5:
            add("run_count_mismatch", "warn", "budget_schedule",
                "总运行数 %s 与 样本数×重复(%s×%s=%s) 不一致" % (runs, sample, repl, expect))
        elif runs is None:
            add("run_count_missing", "warn", "budget_schedule",
                "total_runs 为空，可按 %s×%s=%s 补全" % (sample, repl, expect))
        if sample is not None and sample < 3 and not (bs.get("sample_size_justification") or "").strip():
            add("low_sample_size", "warn", "budget_schedule",
                "样本量 <3 且未给样本量依据", None)
    return arith


def audit_ir(ir, corpus, rep):
    checks = []
    seq = [0]

    def add(code, level, element, message, refs=None):
        seq[0] += 1
        checks.append({"id": "CHK%03d" % seq[0], "code": code, "level": level,
                       "element": element, "message": message, "refs": refs or []})

    g = ir.get("goal", {})
    if not (g.get("statement") or "").strip():
        add("goal_missing", "error", "goal", "研究目标缺失", None)
    if not (g.get("testable_question") or "").strip():
        add("testable_question_missing", "warn", "goal", "未给出可检验问题", None)

    # hypotheses
    for h in ir.get("hypotheses", []):
        if not (h.get("statement") or "").strip():
            add("hypothesis_statement_missing", "error", "hypotheses", "假设 %s 缺陈述" % h.get("id"), [h.get("id")])
        if not (h.get("falsifiable_by") or "").strip():
            add("hypothesis_not_falsifiable", "error", "hypotheses",
                "假设 %s 缺可证伪判据" % h.get("id"), [h.get("id")])
    if not ir.get("hypotheses"):
        add("hypotheses_empty", "error", "hypotheses", "没有假设，任务未完成")

    # variables
    for v in ir.get("variables", []):
        role = v.get("role")
        if role not in VARIABLE_ROLES:
            add("variable_role_invalid", "error", "variables",
                "变量 %s 角色无效或缺失（应属 %s）" % (v.get("id"), "/".join(VARIABLE_ROLES)), [v.get("id")])
        if not (v.get("operational_definition") or "").strip():
            add("variable_operational_definition_missing", "error", "variables",
                "变量 %s 缺操作定义" % v.get("id"), [v.get("id")])
        m = v.get("measurement") or {}
        if not (m.get("method") or "").strip() or not (m.get("unit") or "").strip():
            add("variable_measurement_missing", "error", "variables",
                "变量 %s 缺测量方式或单位" % v.get("id"), [v.get("id")])
        if v.get("range") is None and not v.get("levels"):
            add("variable_range_missing", "warn", "variables",
                "变量 %s 缺量程或水平" % v.get("id"), [v.get("id")])

    # controls
    for c in ir.get("controls", []):
        if c.get("type") not in CONTROL_TYPES:
            add("control_type_invalid", "warn", "controls",
                "对照 %s 类型无效或缺失（%s）" % (c.get("id"), "/".join(CONTROL_TYPES)), [c.get("id")])
        if not (c.get("excludes_confounder") or "").strip():
            add("control_no_confounder", "error", "controls",
                "对照 %s 未说明排除哪类混杂" % c.get("id"), [c.get("id")])
    if not ir.get("controls"):
        add("controls_empty", "error", "controls", "没有对照组")

    # steps
    for s in ir.get("steps", []):
        d = to_num(s.get("duration_min"))
        if d is None or d <= 0:
            add("step_duration_missing", "error", "steps",
                "步骤 %s 缺时长（分钟）" % s.get("id"), [s.get("id")])
        if not as_list(s.get("equipment")) and not as_list(s.get("materials")) and not (s.get("method") or "").strip():
            add("step_resource_unbound", "warn", "steps",
                "步骤 %s 未绑定设备/试剂/方法" % s.get("id"), [s.get("id")])
        if not (s.get("acceptance") or "").strip():
            add("step_acceptance_missing", "warn", "steps",
                "步骤 %s 缺验收/合格判据" % s.get("id"), [s.get("id")])
    if not ir.get("steps"):
        add("steps_empty", "error", "steps", "没有步骤")

    # parameters
    if not ir.get("parameters"):
        add("parameters_empty", "error", "parameters", "没有参数")

    # resources
    for r in ir.get("resources", []):
        if r.get("kind") not in RESOURCE_KINDS:
            add("resource_kind_invalid", "warn", "resources",
                "资源 %s 类别无效或缺失" % r.get("id"), [r.get("id")])
        if not (r.get("spec") or "").strip():
            add("resource_spec_missing", "warn", "resources",
                "资源 %s 缺型号/规格" % r.get("id"), [r.get("id")])
    if not ir.get("resources"):
        add("resources_empty", "error", "resources", "没有设备材料")

    # readout criteria
    for rc in ir.get("readout_criteria", []):
        if not (rc.get("metric") or "").strip() or rc.get("threshold") is None or not (rc.get("decision") or "").strip():
            add("readout_incomplete", "warn", "readout_criteria",
                "判读标准 %s 缺指标/阈值/决策之一" % rc.get("id"), [rc.get("id")])
    if not ir.get("readout_criteria"):
        add("readout_empty", "error", "readout_criteria", "没有判读标准")

    # risks
    for r in ir.get("risks", []):
        if not (r.get("detection_criterion") or "").strip():
            add("risk_no_criterion", "error", "risks",
                "风险 %s 缺检测判据" % r.get("id"), [r.get("id")])
        if not (r.get("fallback_path") or "").strip():
            add("risk_no_fallback", "error", "risks",
                "风险 %s 缺替代路径" % r.get("id"), [r.get("id")])
    if not ir.get("risks"):
        add("risks_empty", "error", "risks", "没有风险与异常处置")

    # confirmations
    for cf in ir.get("confirmations", []):
        if cf.get("category") not in CONFIRM_CATEGORIES:
            add("confirmation_category_invalid", "warn", "confirmations",
                "确认项 %s 类别无效" % cf.get("id"), [cf.get("id")])
        if cf.get("status") not in CONFIRM_STATUSES:
            add("confirmation_status_invalid", "warn", "confirmations",
                "确认项 %s 状态无效" % cf.get("id"), [cf.get("id")])
    if not ir.get("confirmations"):
        add("confirmations_empty", "warn", "confirmations",
            "没有确认项（至少应覆盖安全/伦理/设备可用性）")

    # assumptions
    for a in ir.get("assumptions", []):
        if not (a.get("statement") or "").strip() or not (a.get("impact_if_wrong") or "").strip():
            add("assumption_incomplete", "warn", "assumptions",
                "假设前提 %s 缺陈述或失效影响" % a.get("id"), [a.get("id")])

    # evidence refs resolve
    ev_ids = {e.get("id") for e in ir.get("evidence", []) if e.get("id")}
    for key in ("hypotheses", "variables", "controls", "steps", "parameters",
                "resources", "readout_criteria", "risks"):
        for node in ir.get(key, []):
            for ref in as_list(node.get("evidence")):
                if ref not in ev_ids:
                    add("evidence_ref_missing", "error", key,
                        "节点 %s 引用了不存在的证据 %s" % (node.get("id"), ref), [node.get("id"), ref])

    arith = audit_arithmetic(ir, add)
    ev_results = verify_evidence(ir, corpus)

    for ev in ev_results:
        if ev["status"] == "not_found":
            add("evidence_quote_not_found", "error", "evidence",
                "证据 %s 的 quote 未在 %s 中找到（逐字回查失败）" % (ev["id"], ev.get("locator")), [ev["id"]])
        elif ev["status"] == "source_missing":
            add("evidence_source_missing", "warn", "evidence",
                "证据 %s 的定位 %s 找不到对应材料" % (ev["id"], ev.get("locator")), [ev["id"]])
        elif ev["status"] in ("incomplete", "missing_locator"):
            add("evidence_incomplete", "error", "evidence",
                "证据 %s 缺 locator 或逐字 quote" % ev["id"], [ev["id"]])

    # conflicts (explicit + auto from evidence metric/value)
    conflicts = list(ir.get("conflicts", []))
    groups = {}
    for e in ir.get("evidence", []):
        metric = e.get("metric")
        value = e.get("value")
        if metric and value not in (None, ""):
            groups.setdefault(str(metric), []).append((e.get("id"), json_canonical(value)))
    auto_conflicts = []
    for metric, items in groups.items():
        distinct = {v for _, v in items}
        if len(distinct) > 1:
            auto_conflicts.append({"id": "X-auto-" + metric, "topic": metric,
                                   "sources": [i for i, _ in items],
                                   "values": [v for _, v in items],
                                   "resolution": None, "auto": True})
    if auto_conflicts:
        add("source_conflict", "warn", "evidence",
            "同一指标 %s 存在相互冲突的取值，需两处都保留并说明" % "、".join(sorted({c['topic'] for c in auto_conflicts})))

    coverage = {}
    coverage["假设与变量"] = _coverage_pair(ir, "hypotheses", "variables")
    coverage["对照组"] = _coverage_pair(ir, "controls", None)
    coverage["设备材料"] = _coverage_pair(ir, "resources", None)
    coverage["时间成本"] = {"present": bool(ir.get("steps")) or ir.get("budget_schedule", {}).get("cost_total") is not None,
                            "count": len(ir.get("steps", []))}
    coverage["参数范围"] = _coverage_pair(ir, "parameters", None)
    coverage["风险和异常"] = _coverage_pair(ir, "risks", None)

    # candidates
    cand_eval = evaluate_candidates(ir, add)

    return {"checks": checks, "coverage": coverage, "arithmetic": arith,
            "evidence": ev_results, "candidates": cand_eval,
            "conflicts": conflicts + auto_conflicts}


def _coverage_pair(ir, key, key2):
    n = len(ir.get(key, [])) + (len(ir.get(key2, [])) if key2 else 0)
    return {"present": n > 0, "count": n}


def evaluate_candidates(ir, add):
    params = ir.get("parameters", [])
    p_index = {}
    for p in params:
        p_index[p.get("id")] = p
        if p.get("name"):
            p_index[str(p.get("name"))] = p
    doms = as_dict(ir.get("constraints", {}).get("parameter_domains"))
    cap = to_num(ir.get("constraints", {}).get("budget_cap"))
    window = to_num(ir.get("constraints", {}).get("time_window_minutes"))
    evals = []
    for cand in ir.get("candidates", []):
        cid = cand.get("id")
        reasons = []
        feasible = True
        unknown = False
        choices = as_dict(cand.get("parameter_choices"))
        for pk, val in choices.items():
            p = p_index.get(pk)
            allow = None
            if isinstance(p, dict):
                allow = p.get("allowed")
            elif pk in doms:
                allow = doms.get(pk)
            lo = hi = None
            if isinstance(allow, (list, tuple)) and len(allow) == 2:
                lo, hi = to_num(allow[0]), to_num(allow[1])
            v = to_num(val)
            if v is None:
                unknown = True
                reasons.append("参数 %s 取值不可解析" % pk)
                continue
            if lo is not None and hi is not None:
                if not (lo <= v <= hi):
                    feasible = False
                    reasons.append("参数 %s=%s 超出 [%s,%s]" % (pk, v, lo, hi))
            else:
                unknown = True
                reasons.append("参数 %s 无允许区间可校验" % pk)
        cost = to_num(cand.get("cost_total"))
        time = to_num(cand.get("wall_clock_minutes"))
        if cost is not None and cap is not None and cost > cap:
            feasible = False
            reasons.append("成本 %s 超预算 %s" % (cost, cap))
        elif cost is not None and cap is None:
            unknown = True
            reasons.append("无预算上限可校验")
        if time is not None and window is not None and time > window:
            feasible = False
            reasons.append("时长 %s 超时间窗 %s" % (time, window))
        elif time is not None and window is None:
            unknown = True
            reasons.append("无时间窗可校验")
        if unknown and feasible:
            status = "unknown"
        else:
            status = "feasible" if feasible else "infeasible"
        evals.append({"id": cid, "status": status, "cost": cost, "wall_clock_minutes": time,
                      "reject_reasons": reasons})
    if evals:
        add("candidate_evaluation", "info", "candidates",
            "候选方案可行性已按参数区间/预算/时间窗校验", None)

    # selection consistency
    sel = ir.get("selection", {})
    chosen = sel.get("chosen")
    chosen_rec = next((e for e in evals if e["id"] == chosen), None)
    if chosen is not None and chosen_rec is None:
        add("selection_unknown", "error", "candidates",
            "选择方案 %s 不在候选列表中" % chosen, [chosen])
    elif chosen is not None and chosen_rec and chosen_rec["status"] == "infeasible":
        add("selection_infeasible", "error", "candidates",
            "选中的方案 %s 经算术校验不可行" % chosen, [chosen])
    elif chosen is not None and chosen_rec and chosen_rec["status"] == "unknown":
        add("selection_unverified", "warn", "candidates",
            "选中的方案 %s 可行性尚未完全验证（缺区间/预算/时间窗）" % chosen, [chosen])
    return evals


def compute_status(rep, ir, audit):
    if rep.get("input_state") in ("empty", "case_invalid_no_materials"):
        return "insufficient_evidence", "inputs_empty_or_invalid"
    has_science = any(ir.get(k) for k in
                      ("hypotheses", "variables", "controls", "steps", "parameters",
                       "resources", "risks", "readout_criteria"))
    if not has_science:
        return "insufficient_evidence", "ir_empty"
    if any(c["level"] == "error" for c in audit["checks"]):
        return "partial", "errors_present"
    blocking = [cf for cf in ir.get("confirmations", [])
                if cf.get("status") in ("pending", "unknown") and cf.get("blocking")]
    if blocking:
        return "partial", "blocking_confirmations"
    if any(c["level"] == "warn" for c in audit["checks"]):
        return "partial", "warnings_present"
    return "ok", "all_checks_passed"


# --------------------------------------------------------------------------- #
# Bundle / contract projection
# --------------------------------------------------------------------------- #

def build_bundles(ir, audit, status, rep):
    counts = {"error": 0, "warn": 0, "info": 0}
    for c in audit["checks"]:
        if c["level"] == "error":
            counts["error"] += 1
        elif c["level"] == "warn":
            counts["warn"] += 1
        else:
            counts["info"] += 1
    ev = audit["evidence"]
    ev_counts = {"total": len(ev), "verified": sum(1 for x in ev if x["status"] == "verified"),
                 "not_found": sum(1 for x in ev if x["status"] == "not_found"),
                 "source_missing": sum(1 for x in ev if x["status"] == "source_missing"),
                 "incomplete": sum(1 for x in ev if x["status"] in ("incomplete", "missing_locator"))}

    plan_bundle = {
        "goal": ir.get("goal"),
        "assumptions": ir.get("assumptions"),
        "hypotheses": ir.get("hypotheses"),
        "variables": ir.get("variables"),
        "controls": ir.get("controls"),
        "steps": ir.get("steps"),
        "parameters": ir.get("parameters"),
        "resources": ir.get("resources"),
        "readout_criteria": ir.get("readout_criteria"),
        "schedule_and_budget": ir.get("budget_schedule"),
        "candidate_selection": ir.get("selection"),
    }
    evidence_bundle = {
        "evidence": ir.get("evidence"),
        "evidence_checks": ev,
        "risks": ir.get("risks"),
        "risk_policy": {
            "stop_loss_rule": "任一风险命中检测判据即触发止损，记录并切换替代路径；连续两次同因失败暂停排期。",
            "escalation": "无法判断或涉及安全/伦理时升级专家，不写为已确认事实。",
        },
        "source_conflicts": audit.get("conflicts"),
        "limitations": [],  # filled by caller
    }
    cand_bundle = {"candidates": ir.get("candidates"), "selection": ir.get("selection"),
                   "evaluation": audit.get("candidates")}
    checks_bundle = {"counts": counts, "checks": audit["checks"], "coverage": audit["coverage"],
                     "evidence_counts": ev_counts}
    summary_bundle = {"status": status, "case_id": ir.get("case_id"),
                      "goal": ir.get("goal"), "node_counts": _node_counts(ir)}
    return {
        "plan": plan_bundle, "evidence_and_risks": evidence_bundle,
        "confirmations": ir.get("confirmations"), "candidates": cand_bundle,
        "checks": checks_bundle, "summary": summary_bundle,
    }


def _node_counts(ir):
    return {k: len(ir.get(k, [])) for k in LIST_NODE_KEYS}


def _bundle_for(path, bundles, ir):
    k = (path[-1] if path else "").lower()
    exact = {
        "plan": bundles["plan"], "experiment_plan": bundles["plan"],
        "design": bundles["plan"], "protocol": bundles["plan"], "experiment": bundles["plan"],
        "方案": bundles["plan"],
        "evidence_and_risks": bundles["evidence_and_risks"],
        "evidence_risks": bundles["evidence_and_risks"],
        "依据与风险": bundles["evidence_and_risks"],
        "confirmations": bundles["confirmations"], "confirm": bundles["confirmations"],
        "确认": bundles["confirmations"], "确认项": bundles["confirmations"],
        "candidates": bundles["candidates"], "candidate_comparison": bundles["candidates"],
        "candidate": bundles["candidates"], "候选": bundles["candidates"],
        "checks": bundles["checks"], "summary": bundles["summary"], "overview": bundles["summary"],
        "摘要": bundles["summary"], "结论": bundles["summary"],
    }
    if k in exact:
        return exact[k]
    if "hypothes" in k or "假设" in k:
        return ir.get("hypotheses")
    if "variable" in k or "变量" in k:
        return ir.get("variables")
    if "control" in k or "对照" in k:
        return ir.get("controls")
    if "step" in k or "步骤" in k:
        return ir.get("steps")
    if "readout" in k or "判读" in k or "criteri" in k:
        return ir.get("readout_criteria")
    if "parameter" in k or "参数" in k:
        return ir.get("parameters")
    if "resource" in k or "equipment" in k or "material" in k or "设备" in k or "材料" in k:
        return ir.get("resources")
    if "risk" in k or "风险" in k:
        return ir.get("risks")
    if "evidence" in k or "依据" in k:
        return ir.get("evidence")
    if "assumption" in k or "前提" in k:
        return ir.get("assumptions")
    if "budget" in k or "schedule" in k or "cost" in k or "预算" in k or "排期" in k:
        return ir.get("budget_schedule")
    if "selection" in k or "选择" in k:
        return ir.get("selection")
    if "goal" in k or "objective" in k or "目标" in k:
        return ir.get("goal")
    if "check" in k or "校验" in k:
        return bundles["checks"]
    return None


def project_contract(contract, bundles, ir):
    if not isinstance(contract, dict) or not contract:
        contract = DEFAULT_CONTRACT
    unmapped = []

    def proj(spec, path):
        if isinstance(spec, dict):
            if not spec:
                b = _bundle_for(path, bundles, ir)
                return b if b is not None else {}
            out = {}
            for kk, vv in spec.items():
                out[kk] = proj(vv, path + [kk])
            return out
        if isinstance(spec, list):
            b = _bundle_for(path, bundles, ir)
            if isinstance(b, list):
                return b
            if b is None:
                return []
            return b
        b = _bundle_for(path, bundles, ir)
        if b is None:
            unmapped.append(".".join(path))
            return None
        return b

    projected = proj(contract, [])
    return projected, unmapped


# --------------------------------------------------------------------------- #
# Derived envelope content
# --------------------------------------------------------------------------- #

def derive_pending(ir, audit, rep, unmapped):
    out = []
    n = [0]

    def add(item, category, blocking, why):
        n[0] += 1
        out.append({"id": "PH%02d" % n[0], "item": item, "category": category,
                    "blocking": bool(blocking), "why": why})

    if not rep.get("case_id"):
        add("case_id 缺失，无法建立输入标识", "data", True, "case.json 与 task.md 均未提供")
    for cf in ir.get("confirmations", []):
        if cf.get("status") in ("pending", "unknown"):
            add(cf.get("item") or cf.get("id"), cf.get("category") or "other",
                bool(cf.get("blocking")), "确认项状态为 %s" % cf.get("status"))
    for conflict in audit.get("conflicts", []):
        add("解决来源冲突：%s" % conflict.get("topic", "?"), "data", False,
            "冲突取值 %s 来自 %s" % (conflict.get("values"), conflict.get("sources")))
    for ev in audit["evidence"]:
        if ev["status"] == "not_found":
            add("替换或修正证据 %s 的 quote/locator" % ev["id"], "data", False, "逐字回查失败")
        elif ev["status"] == "source_missing":
            add("提供证据 %s 的原始材料" % ev["id"], "data", False, "定位文件缺失")
    if not ir.get("goal", {}).get("statement"):
        add("补充研究目标", "data", True, "goal.statement 为空")
    sel = ir.get("selection", {})
    if ir.get("candidates") and not sel.get("chosen"):
        add("在候选方案中做选择并说明理由", "expert", False, "selection.chosen 为空")
    if sel.get("selection_source") == "rule_min_cost_then_time":
        add("复核规则自动选择的方案", "expert", False, "自动选择仅按成本/时间排序，需人工确认科学合理性")
    for u in unmapped:
        add("明确 output_contract 未映射键 %s 的语义" % u, "contract", False,
            "契约键未被识别，内容仍保存在 canonical/plan.ir.json 与 output_contract 镜像块")
    return out


def derive_uncertainty(ir, audit, rep, status, reason):
    out = []
    n = [0]

    def add(topic, detail, impact, resolution):
        n[0] += 1
        out.append({"id": "U%02d" % n[0], "topic": topic, "detail": detail,
                    "impact": impact, "resolution": resolution})

    if status != "ok":
        add("整体状态", "status=%s，reason=%s" % (status, reason),
            "方案可能不能直接执行", "按 pending_human 逐项补全后重新 build/validate")
    for a in ir.get("assumptions", []):
        add("假设前提", a.get("statement"),
            a.get("impact_if_wrong") or "未说明", "验证或降级为待确认")
    for conflict in audit.get("conflicts", []):
        add("来源冲突", "topic=%s，values=%s" % (conflict.get("topic"), conflict.get("values")),
            "结论依赖的取值不确定", "两处来源都保留，标注 source_conflict 并请专家裁决")
    for ev in audit["evidence"]:
        if ev["status"] == "source_missing":
            add("证据不可得", "locator=%s" % ev.get("locator"), "该依据无法复核",
                "补充原始材料或降级该结论为假设")
        elif ev["status"] == "not_found":
            add("引用失真", "证据 %s 的 quote 未命中原文" % ev["id"], "依据链不可信",
                "回到原文摘录逐字 quote")
    for p in ir.get("parameters", []):
        if p.get("source") not in PARAM_SOURCES:
            add("参数依据", "参数 %s 来源未知" % p.get("id"), "量程依据不可复核",
                "标注 manual/preliminary/literature/organizer_contract/vendor")
    cand_unknown = [c for c in audit.get("candidates", []) if c.get("status") == "unknown"]
    if cand_unknown:
        add("候选可行性", "候选 %s 缺区间/预算/时间窗，可行性未定" % "、".join(c["id"] for c in cand_unknown),
            "选择理由不充分", "补全约束后重新 evaluate")
    if ir.get("selection", {}).get("selection_source") == "rule_min_cost_then_time":
        add("自动选择", "未人工指定 selection.chosen，按最小成本、次小时间排序",
            "选择可能偏离科学目标", "人工复核并改写 rationale")
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_plan_md(ir, audit, status, reason, meta):
    L = []
    g = ir.get("goal", {})
    title = (g.get("statement") or "")[:80] or ("case_" + str(ir.get("case_id")))
    L.append("# 实验方案：%s" % title)
    L.append("")
    L.append("- case_id：`%s`" % ir.get("case_id"))
    L.append("- 状态：`%s`（%s）" % (status, reason))
    L.append("- 生成方式：由 `plan.ir.json`（IR v%s）机器渲染；错误 %d / 警告 %d / 提示 %d" % (
        ir.get("ir_version"),
        sum(1 for c in audit["checks"] if c["level"] == "error"),
        sum(1 for c in audit["checks"] if c["level"] == "warn"),
        sum(1 for c in audit["checks"] if c["level"] == "info")))
    L.append("- IR 摘要：%s" % (meta.get("ir_sha256") or "")[:16] + "…")
    L.append("")

    gaps = collect_gaps(ir)
    if gaps:
        L.append("## 0. 缺口与限制（尚未补全）")
        L.append("")
        for gap in gaps:
            L.append("- 待补：%s" % gap)
        L.append("")

    L.append("## 1. 研究目标与可检验问题")
    L.append("")
    L.append("- 目标：%s" % (g.get("statement") or "—"))
    L.append("- 可检验问题：%s" % (g.get("testable_question") or "—"))
    L.append("- 成功标准：%s" % ("；".join(str(x) for x in as_list(g.get("success_criteria"))) or "—"))
    L.append("")

    if ir.get("assumptions"):
        L.append("## 2. 假设前提")
        L.append("")
        L.append(md_table(["ID", "陈述", "失效影响"],
                          [[a.get("id"), a.get("statement"), a.get("impact_if_wrong")] for a in ir["assumptions"]]))
        L.append("")

    L.append("## 3. 假设")
    L.append("")
    if ir.get("hypotheses"):
        L.append(md_table(["ID", "陈述", "可证伪判据", "状态", "证据"],
                          [[h.get("id"), h.get("statement"), h.get("falsifiable_by"),
                            h.get("status") or "assumed", "、".join(as_list(h.get("evidence")))]
                           for h in ir["hypotheses"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 4. 变量")
    L.append("")
    if ir.get("variables"):
        rows = []
        for v in ir["variables"]:
            m = v.get("measurement") or {}
            rows.append([v.get("id"), v.get("role"), v.get("operational_definition"),
                         "%s / %s" % (m.get("method") or "—", m.get("instrument") or "—"),
                         "%s" % (m.get("unit") or "—"),
                         _cell(v.get("levels")) if v.get("levels") is not None else _display_range(v.get("range"), None)])
        L.append(md_table(["ID", "角色", "操作定义", "测量方式/仪器", "单位", "水平/量程"], rows))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 5. 对照组")
    L.append("")
    if ir.get("controls"):
        L.append(md_table(["ID", "类型", "目的", "排除的混杂", "预期读数"],
                          [[c.get("id"), c.get("type"), c.get("purpose"), c.get("excludes_confounder"),
                            c.get("expected_readout")] for c in ir["controls"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 6. 步骤")
    L.append("")
    if ir.get("steps"):
        L.append(md_table(["ID", "名称", "依赖", "时长(min)", "并行组", "设备", "材料", "参数", "验收判据"],
                          [[s.get("id"), s.get("name"), "、".join(as_list(s.get("depends_on"))),
                            s.get("duration_min"), s.get("parallel_group") or "—",
                            "、".join(as_list(s.get("equipment"))), "、".join(as_list(s.get("materials"))),
                            "、".join(as_list(s.get("parameters"))), s.get("acceptance")] for s in ir["steps"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 7. 参数范围")
    L.append("")
    if ir.get("parameters"):
        L.append(md_table(["ID", "名称", "取值", "单位", "允许区间", "来源", "在范围内"],
                          [[p.get("id"), p.get("name"), p.get("value"), p.get("unit"),
                            _display_range(p.get("allowed"), None), p.get("source"),
                            _cell(_param_in_range(p))] for p in ir["parameters"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 8. 设备材料")
    L.append("")
    if ir.get("resources"):
        L.append(md_table(["ID", "类别", "名称", "型号/规格", "产能/量程", "可替代"],
                          [[r.get("id"), r.get("kind"), r.get("name"), r.get("spec"),
                            r.get("capacity") or r.get("range") or "—",
                            _cell(r.get("substitutable"))] for r in ir["resources"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 9. 时间与预算")
    L.append("")
    bs = ir.get("budget_schedule", {})
    ar = audit.get("arithmetic", {}).get("steps", {})
    bu = audit.get("arithmetic", {}).get("budget", {})
    L.append("- 样本量：%s；重复：%s；总运行数：%s" % (bs.get("sample_count"), bs.get("replicates"), bs.get("total_runs")))
    L.append("- 时长推算：%s = %s min（模式 %s；关键路径 %s）" % (
        ar.get("computed_wall_clock_minutes", "—"), ar.get("computed_wall_clock_minutes", "—"),
        ar.get("mode"), " → ".join(ar.get("critical_path") or []) or "—"))
    L.append("- 预算：逐项求和 = %s %s；声明合计 = %s %s" % (
        bu.get("computed_total"), bs.get("currency") or "",
        bu.get("declared_total"), bs.get("currency") or ""))
    for it in bu.get("items", []):
        L.append("  - %s × %s @ %s = %s" % (it.get("item"), it.get("qty"), it.get("unit_cost"), it.get("total")))
    cons = ir.get("constraints", {})
    if cons.get("budget_cap") is not None:
        L.append("- 预算上限：%s %s" % (cons.get("budget_cap"), cons.get("currency") or ""))
    if cons.get("time_window_minutes") is not None:
        L.append("- 时间窗：%s min" % cons.get("time_window_minutes"))
    L.append("")

    L.append("## 10. 判读标准")
    L.append("")
    if ir.get("readout_criteria"):
        L.append(md_table(["ID", "指标", "阈值", "决策", "关联变量"],
                          [[rc.get("id"), rc.get("metric"), rc.get("threshold"), rc.get("decision"),
                            "、".join(as_list(rc.get("linked_variable")))] for rc in ir["readout_criteria"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 11. 候选方案比较与选择")
    L.append("")
    if ir.get("candidates"):
        L.append(md_table(["ID", "摘要", "可行性", "成本", "时长(min)", "拒绝/保留原因"],
                          [[c.get("id"), c.get("summary"), _cell(c.get("status") or "seeded"),
                            c.get("cost_total"), c.get("wall_clock_minutes"),
                            "；".join(c.get("reject_reasons") or []) or "—"]
                           for c in audit.get("candidates", [])]))
        sel = ir.get("selection", {})
        L.append("")
        L.append("- 选中：%s（依据 %s）" % (sel.get("chosen") or "未定", sel.get("selection_source") or "未标注"))
        L.append("- 理由：%s" % (sel.get("rationale") or "—"))
    else:
        L.append("（无候选方案信息，记为 not_reported）")
    L.append("")

    L.append("## 12. 风险与异常")
    L.append("")
    if ir.get("risks"):
        L.append(md_table(["ID", "失败模式", "检测判据", "止损点", "缓解", "替代路径", "关联步骤"],
                          [[r.get("id"), r.get("failure_mode"), r.get("detection_criterion"),
                            r.get("stop_loss") or "—", r.get("mitigation") or "—", r.get("fallback_path"),
                            "、".join(as_list(r.get("linked_steps")))] for r in ir["risks"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 13. 确认项")
    L.append("")
    if ir.get("confirmations"):
        L.append(md_table(["ID", "类别", "事项", "状态", "阻塞"],
                          [[cf.get("id"), cf.get("category"), cf.get("item"),
                            cf.get("status"), _cell(cf.get("blocking"))] for cf in ir["confirmations"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 14. 证据附录（逐字回查）")
    L.append("")
    vmap = {e["id"]: e for e in audit.get("evidence", [])}
    if ir.get("evidence"):
        L.append(md_table(["ID", "定位", "逐字引用", "回查结果"],
                          [[e.get("id"), e.get("locator"),
                            (e.get("quote") or "")[:120], _evidence_status(vmap.get(e.get("id")))]
                           for e in ir["evidence"]]))
    else:
        L.append("（无，待补）")
    L.append("")

    L.append("## 15. 来源冲突")
    L.append("")
    for x in audit.get("conflicts", []):
        L.append("- %s：sources=%s；values=%s" % (x.get("topic"), x.get("sources"), x.get("values")))
    if not audit.get("conflicts"):
        L.append("- 未检测到冲突")
    L.append("")

    L.append("## 16. 复核方式")
    L.append("")
    L.append("```bash")
    L.append("cd /skills/weekly-skill")
    L.append("python3 scripts/main.py build --input /input --output /app/output --work /app/work")
    L.append("python3 scripts/main.py validate --input /input --output /app/output --work /app/work")
    L.append("```")
    L.append("")
    return "\n".join(L)


def _param_in_range(p):
    allow = p.get("allowed")
    if not isinstance(allow, (list, tuple)) or len(allow) != 2:
        return None
    lo, hi = to_num(allow[0]), to_num(allow[1])
    val = to_num(p.get("value"))
    if val is None or lo is None or hi is None:
        return None
    return lo <= val <= hi


def _evidence_status(rec):
    if not rec:
        return "—"
    if rec.get("status") == "verified":
        return "通过(%s)" % (rec.get("match_mode") or "exact")
    return rec.get("status")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _resolve_ir_path(args, work, out):
    if getattr(args, "ir", None):
        return Path(args.ir)
    a = work / "plan.ir.json"
    b = out / "plan.ir.json"
    if a.exists():
        return a
    if b.exists():
        return b
    return None


def _build_sources(rep, corpus):
    sources = []
    seen = set()
    for rel in ("case.json", "task.md"):
        if rel in corpus:
            e = corpus[rel]
            sources.append({"id": "src_" + rel.replace(".", "_"), "path": rel, "kind": e.get("kind"),
                            "chars": e.get("chars"), "sha256": e.get("sha256"),
                            "extraction": e.get("extraction")})
            seen.add(rel)
    for rel in sorted(corpus):
        if rel in seen:
            continue
        e = corpus[rel]
        sources.append({"id": "src_%02d" % (len(sources) + 1), "path": rel,
                        "kind": e.get("kind"), "chars": e.get("chars"),
                        "sha256": e.get("sha256"), "extraction": e.get("extraction")})
    return sources


def cmd_probe(args):
    rep = get_probe(Path(args.input), Path(args.work), refresh=True)
    print(json.dumps({
        "input_state": rep["input_state"], "case_id": rep["case_id"],
        "goal": rep["goal"].get("statement"), "contract_keys": rep["contract"]["keys"],
        "candidates_seeded": len(rep["seed"]["candidates"]),
        "materials": len(rep["seed"]["material_index"]),
        "files": len(rep["files"]), "errors": rep["errors"], "degradations": rep["degradations"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_scaffold(args):
    inp, out, work = Path(args.input), Path(args.output), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    rep = get_probe(inp, work)
    corpus = load_corpus(work)

    ir_path = work / "plan.ir.json"
    if ir_path.exists() and not args.force:
        ir = load_json(ir_path)
        if not isinstance(ir, dict):
            ir = seed_ir(rep)
            write_json_atomic(ir_path, ir)
    else:
        ir = seed_ir(rep)
        write_json_atomic(ir_path, ir)

    ir = normalize_ir(ir)
    audit = audit_ir(ir, corpus, rep)
    status, reason = compute_status(rep, ir, audit)

    res_path = out / "result.json"
    if res_path.exists() and not args.force:
        prev = load_json(res_path)
        if isinstance(prev, dict) and len(as_list(prev.get("change_log"))) > 2:
            print("result.json 已存在且非骨架，跳过覆盖（--force 可强制）")
        else:
            result, digest = _compose_result(ir, audit, status, reason, rep, corpus,
                                             prev, state="skeleton")
            _write_side_artifacts(out, ir, audit, digest, result, status, reason, "skeleton")
            write_json_atomic(res_path, result)
    else:
        result, digest = _compose_result(ir, audit, status, reason, rep, corpus,
                                         None, state="skeleton")
        _write_side_artifacts(out, ir, audit, digest, result, status, reason, "skeleton")
        write_json_atomic(res_path, result)

    write_json_atomic(work / "todo.json", {"gaps": collect_gaps(ir), "generated_at": _now_iso()})
    print("scaffold done: case_id=%s status=%s gaps=%d" % (ir.get("case_id"), status, len(collect_gaps(ir))))
    return 0


def _compose_result(ir, audit, status, reason, rep, corpus, prev_result, state):
    bundles = build_bundles(ir, audit, status, rep)
    contract = rep.get("contract", {}).get("value") or DEFAULT_CONTRACT
    projected, unmapped = project_contract(contract, bundles, ir)
    pending = derive_pending(ir, audit, rep, unmapped)
    uncertainty = derive_uncertainty(ir, audit, rep, status, reason)
    limits = []
    for d in rep.get("degradations", []):
        limits.append("降级：%s（%s）" % (d.get("code"), d.get("path", "")))
    if status != "ok":
        limits.append("状态 %s（%s）：结果存在未闭合缺口，见 pending_human" % (status, reason))
    if not rep.get("seed", {}).get("material_index"):
        limits.append("未发现 case.json/task.md 之外的科研材料，方案依据受限")

    sources = _build_sources(rep, corpus)
    digest = ir_digest(ir)
    prev_log = as_list((prev_result or {}).get("change_log"))
    seq = max([x.get("seq") for x in prev_log if isinstance(x, dict) and isinstance(x.get("seq"), int)] or [0])
    log = list(prev_log)

    def log_evt(event, detail):
        nonlocal seq
        seq += 1
        log.append({"seq": seq, "ts": _now_iso(), "event": event, "detail": detail})

    if state == "skeleton":
        log_evt("skeleton_written", "partial 骨架先行落盘")
    else:
        log_evt("ir_loaded", "case_id=%s" % ir.get("case_id"))
        log_evt("audits_done", "errors=%d warn=%d" % (
            sum(1 for c in audit["checks"] if c["level"] == "error"),
            sum(1 for c in audit["checks"] if c["level"] == "warn")))
        log_evt("artifacts_rendered", "result.json/plan.md/plan.ir.json/checks.json")

    ev_counts = {"total": len(audit["evidence"]),
                 "verified": sum(1 for x in audit["evidence"] if x["status"] == "verified"),
                 "not_found": sum(1 for x in audit["evidence"] if x["status"] == "not_found"),
                 "source_missing": sum(1 for x in audit["evidence"] if x["status"] == "source_missing")}

    result = {}
    result["case_id"] = ir.get("case_id")
    for k, v in projected.items():
        result[k] = v
    result["output_contract"] = projected
    result["status"] = status
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending
    result["change_log"] = log
    result["errors"] = rep.get("errors", [])
    result["degradations"] = rep.get("degradations", [])
    result["limitations"] = limits
    result["design_ir"] = {"path": "plan.ir.json", "ir_version": IR_VERSION, "state": state,
                           "sha256": digest, "node_counts": _node_counts(ir)}
    result["checks_summary"] = {"errors": sum(1 for c in audit["checks"] if c["level"] == "error"),
                                "warnings": sum(1 for c in audit["checks"] if c["level"] == "warn"),
                                "info": sum(1 for c in audit["checks"] if c["level"] == "info"),
                                "status_reason": reason,
                                "coverage": audit["coverage"],
                                "evidence_counts": ev_counts,
                                "candidate_checks": audit.get("candidates")}
    result["artifacts"] = {"result": "result.json", "plan_md": "plan.md",
                           "plan_ir": "plan.ir.json", "checks": "checks.json"}
    result["runtime"] = {"started_at": _now_iso(), "python": platform.python_version(),
                         "platform": platform.platform(), "model_requests_used": None,
                         "note": "请求数由平台记录，本字段不编造"}
    return result, digest


def _write_side_artifacts(out, ir, audit, digest, result, status, reason, state):
    ir_out = dict(ir)
    ir_out["_meta"] = {"state": state, "ir_sha256": digest, "rendered_at": _now_iso(),
                       "evidence_counts": result["checks_summary"]["evidence_counts"]}
    write_json_atomic(out / "plan.ir.json", ir_out)
    write_json_atomic(out / "checks.json", {
        "state": state, "ir_sha256": digest,
        "counts": {"error": result["checks_summary"]["errors"],
                   "warn": result["checks_summary"]["warnings"],
                   "info": result["checks_summary"]["info"]},
        "coverage": audit["coverage"], "arithmetic": audit["arithmetic"],
        "evidence": audit["evidence"], "candidates": audit["candidates"],
        "checks": audit["checks"], "generated_at": _now_iso()})
    plan_md = render_plan_md(ir, audit, status, reason, {"ir_sha256": digest})
    (out / "plan.md").write_text(plan_md, encoding="utf-8")


def cmd_build(args):
    inp, out, work = Path(args.input), Path(args.output), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    rep = get_probe(inp, work)
    corpus = load_corpus(work)
    ir_path = _resolve_ir_path(args, work, out)
    if ir_path is None:
        ir = seed_ir(rep)
        ir_path = work / "plan.ir.json"
        write_json_atomic(ir_path, ir)
    else:
        ir = load_json(ir_path)
        if not isinstance(ir, dict):
            ir = seed_ir(rep)
    ir = normalize_ir(ir)
    audit = audit_ir(ir, corpus, rep)
    status, reason = compute_status(rep, ir, audit)

    prev = load_json(out / "result.json") if (out / "result.json").exists() else None
    result, digest = _compose_result(ir, audit, status, reason, rep, corpus,
                                     prev, state="built")
    _write_side_artifacts(out, ir, audit, digest, result, status, reason, "built")
    write_json_atomic(out / "result.json", result)

    print("build done: case_id=%s status=%s reason=%s errors=%d warnings=%d evidence=%d/%d" % (
        ir.get("case_id"), status, reason,
        result["checks_summary"]["errors"], result["checks_summary"]["warnings"],
        result["checks_summary"]["evidence_counts"]["verified"],
        result["checks_summary"]["evidence_counts"]["total"]))
    return 0


def cmd_validate(args):
    inp, out, work = Path(args.input), Path(args.output), Path(args.work)
    rep = get_probe(inp, work)
    corpus = load_corpus(work)
    issues = []

    def add(level, code, msg):
        issues.append({"level": level, "code": code, "message": msg})

    res_path = out / "result.json"
    if not res_path.exists():
        add("error", "result_missing", "result.json 不存在")
        _finish_validate(issues, work)
        return 1
    result = load_json(res_path)
    if not isinstance(result, dict):
        add("error", "result_invalid_json", "result.json 无法解析")
        _finish_validate(issues, work)
        return 1

    if not result.get("case_id"):
        add("error", "case_id_missing", "result.case_id 缺失")
    if result.get("status") not in STATUSES:
        add("error", "status_invalid", "result.status 非法：%s" % result.get("status"))
    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if key not in result:
            add("error", "%s_missing" % key, "信封字段 %s 缺失" % key)

    contract = rep.get("contract", {}).get("value") or DEFAULT_CONTRACT
    for k in contract:
        if k not in result and k not in result.get("output_contract", {}):
            add("error", "contract_key_missing", "output_contract 键 %s 缺失" % k)

    artifacts = as_dict(result.get("artifacts"))
    for name, rel in (("plan.md", "plan.md"), ("plan.ir.json", "plan.ir.json"), ("checks.json", "checks.json")):
        p = out / rel
        if not p.exists():
            add("error", "artifact_missing_%s" % name, "产物 %s 缺失" % rel)
    plan_md_p = out / "plan.md"
    if plan_md_p.exists():
        md = plan_md_p.read_text(encoding="utf-8", errors="replace")
        if len(md) < 400:
            add("warn", "plan_md_too_short", "plan.md 过短，可能未完成")
        for marker in ("## 3. 假设", "## 4. 变量", "## 5. 对照组", "## 12. 风险与异常"):
            if marker not in md:
                add("warn", "plan_md_section_missing", "plan.md 缺少章节 %s" % marker)

    ir_p = out / "plan.ir.json"
    ir = load_json(ir_p) if ir_p.exists() else None
    if not isinstance(ir, dict):
        add("error", "ir_invalid", "plan.ir.json 无法解析")
        _finish_validate(issues, work)
        return 1
    if ir.get("case_id") != result.get("case_id"):
        add("error", "case_id_mismatch", "plan.ir.json 与 result.json 的 case_id 不一致")

    digest = ir_digest(ir)
    stated = (result.get("design_ir") or {}).get("sha256")
    if stated and stated != digest:
        add("error", "ir_digest_mismatch", "design_ir.sha256 与 plan.ir.json 不一致（%s vs %s）" % (stated[:8], digest[:8]))

    audit = audit_ir(normalize_ir(ir), corpus, rep)
    err = sum(1 for c in audit["checks"] if c["level"] == "error")
    warn = sum(1 for c in audit["checks"] if c["level"] == "warn")
    for ev in audit["evidence"]:
        if ev["status"] == "not_found":
            add("error", "evidence_not_found", "证据 %s 的 quote 未命中原文" % ev["id"])
        elif ev["status"] == "source_missing":
            add("warn", "evidence_source_missing", "证据 %s 的定位文件缺失" % ev["id"])

    checks_p = out / "checks.json"
    if checks_p.exists():
        checks = load_json(checks_p)
        if isinstance(checks, dict):
            cs = checks.get("counts", {})
            rs = result.get("checks_summary", {})
            if cs.get("error") != rs.get("errors") or cs.get("warn") != rs.get("warnings"):
                add("warn", "checks_drift", "checks.json 与 result.json 的计数不一致，可能产物过期")
    if err > 0:
        add("warn", "audit_errors_present", "IR 审计仍有 %d 个错误，%d 个警告" % (err, warn))

    _finish_validate(issues, work)
    return 1 if any(i["level"] == "error" for i in issues) else 0


def _finish_validate(issues, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    write_json_atomic(work / "validation_report.json", {
        "generated_at": _now_iso(), "errors": sum(1 for i in issues if i["level"] == "error"),
        "warnings": sum(1 for i in issues if i["level"] == "warn"),
        "issues": issues})
    errs = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    print("validate: errors=%d warnings=%d" % (len(errs), len(warns)))
    for i in errs:
        print("  [E] %s: %s" % (i["code"], i["message"]))
    for i in warns:
        print("  [W] %s: %s" % (i["code"], i["message"]))


def cmd_todo(args):
    inp, work = Path(args.input), Path(args.work)
    rep = get_probe(inp, work)
    ir_path = work / "plan.ir.json"
    ir = load_json(ir_path) if ir_path.exists() else None
    if not isinstance(ir, dict):
        ir = seed_ir(rep)
    ir = normalize_ir(ir)
    print(json.dumps({"case_id": ir.get("case_id"), "gaps": collect_gaps(ir)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_all(args):
    code = cmd_scaffold(args)
    if code != 0:
        return code
    code = cmd_build(args)
    if code != 0:
        return code
    return cmd_validate(args)


def main(argv=None):
    _setup_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="experiment-plan-ir", description=__doc__)
    sub = p.add_subparsers(dest="command")

    def add_common(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")
        sp.add_argument("--ir", default=None)

    for name, help_ in (("probe", "扫描输入并发现契约"), ("scaffold", "落盘 partial 骨架"),
                        ("build", "从 IR 渲染全部产物"), ("validate", "复核产物"),
                        ("todo", "列出未闭合缺口"), ("all", "probe+scaffold+build+validate")):
        sp = sub.add_parser(name, help=help_)
        add_common(sp)
        if name == "scaffold":
            sp.add_argument("--force", action="store_true")

    args = p.parse_args(argv)
    if not args.command:
        p.print_help()
        return 2
    fn = {"probe": cmd_probe, "scaffold": cmd_scaffold, "build": cmd_build,
          "validate": cmd_validate, "todo": cmd_todo, "all": cmd_all}[args.command]
    try:
        return int(fn(args) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort guard, never crash silently
        print("fatal: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
