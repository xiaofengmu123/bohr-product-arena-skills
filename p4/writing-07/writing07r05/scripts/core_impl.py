"""Shared, deterministic helpers for the introduction-and-related-work skill.

This module is imported by scripts/main.py and scripts/selftest.py. It uses
only the Python 3.9+ standard library and performs the non-writing parts of
the pipeline:

* discover inputs (case.json, task.md, material files);
* extract the output_contract wherever it lives in case.json;
* build a typed backfill template whose fields line up one-to-one with the
  contract (nested objects recurse, arrays default to [], scalar leaves to
  null);
* emit a valid partial result.json skeleton;
* validate the final result against the contract and evidence files.

Nothing here authors scientific prose or facts; the agent writes manuscript.md,
evidence_map.json and change_log.json between the skeleton and validate steps.
All list/dict construction is insertion-ordered and free of randomness so that
two runs on the same input produce the same structure (the only time-varying
field is the recorded ``runtime``/timestamp metric).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"

PIPELINE_VERSION = "1.1.0"

# Files larger than this are skipped from material scanning and recorded in
# degradations so the agent never chokes on a huge PDF.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Priority order for locating the output contract in case.json. A later key is
# only used when every earlier key is absent/empty.
CONTRACT_KEY_PRIORITY = [
    "output_contract",
    "output",
    "expected_output",
    "fields",
    "required_fields",
]

# Names that look like a JSON-schema-ish field spec rather than a nested data
# object. Only strong schema markers are used so that natural output-field
# names such as "format", "title" or "description" are not misread as specs.
SPEC_DICT_KEYS = {
    "type", "required", "items", "properties", "enum", "default",
    "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength",
    "pattern",
}

# Standard fields that every result.json carries, in addition to case_id and
# the contract fields. The contract always wins on name collisions.
STANDARD_FIELDS: Dict[str, Any] = {
    "status": "partial",
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "change_log": [],
    "artifacts": [],
    "errors": [],
    "degradations": [],
    "runtime": 0.0,
}

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


# --------------------------------------------------------------------------- #
# filesystem / text helpers
# --------------------------------------------------------------------------- #

def ensure_dir(path: str) -> str:
    """Create a directory (idempotent) and return its normalized path."""
    os.makedirs(path, exist_ok=True)
    return path


def now_iso() -> str:
    """UTC ISO-8601 timestamp for the recorded runtime metric."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path: str) -> Tuple[str, Optional[str]]:
    """Return (text, encoding_warning). Never raises on decode failure."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", f"read_error:{exc}"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    # latin-1 maps every byte; flag that the content may be lossy.
    return raw.decode("latin-1", errors="replace"), "non-utf8-fallback-latin1"


def load_json(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """Return (value, error). Tolerates missing files and malformed JSON."""
    if not os.path.isfile(path):
        return None, f"missing:{os.path.basename(path)}"
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.msg}:line{exc.lineno}"
    except (UnicodeDecodeError, OSError) as exc:
        return None, f"read_error:{exc}"


def write_json(path: str, data: Any, indent: int = 2) -> None:
    """Write JSON with UTF-8 and BOM-safe decoding downstream; sort_keys off
    so the top-level contract order stays human-readable and deterministic."""
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=indent)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# input discovery
# --------------------------------------------------------------------------- #

def discover_case_id(case: Any) -> Optional[str]:
    """Best-effort case id lookup, including one level of nesting."""
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "challenge_id", "caseId"):
        val = case.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val).strip()
    for key in ("task", "meta", "metadata", "info", "case"):
        val = case.get(key)
        if isinstance(val, dict):
            cid = discover_case_id(val)
            if cid:
                return cid
    return None


def _looks_like_contract_schema(value: Dict[str, Any]) -> bool:
    """True when a dict's values are all strings/bools or spec dicts, i.e. it
    reads as ``{field: type-or-spec}`` rather than wrapper metadata."""
    if not value:
        return False
    for v in value.values():
        if isinstance(v, (str, bool)):
            continue
        if isinstance(v, dict) and _is_spec_dict(v):
            continue
        return False
    return True


def find_contract_root(case: Dict[str, Any]) -> Tuple[Optional[str], Any]:
    """Locate the output contract key and value.

    Returns (key_path, value). key_path uses dotted notation when the contract
    is nested (e.g. ``output.output_contract``). Explicit keys are preferred;
    wrapper objects are searched recursively before being treated as a direct
    schema.
    """
    if not isinstance(case, dict):
        return None, None
    # 1. Explicit contract/schema keys at this level.
    for key in ("output_contract", "fields", "required_fields"):
        val = case.get(key)
        if val not in (None, "", [], {}):
            return key, val
    # 2. Wrapper objects that may contain the contract one level down.
    for outer in ("output", "expected_output", "result", "response",
                  "deliverable", "submission"):
        val = case.get(outer)
        if isinstance(val, dict):
            inner_key, inner_val = find_contract_root(val)
            if inner_key is not None:
                return f"{outer}.{inner_key}", inner_val
            if _looks_like_contract_schema(val):
                return outer, val
    return None, None


def _is_spec_dict(value: Dict[str, Any]) -> bool:
    """True when a dict looks like a field spec (has schema-ish keys)."""
    if not value:
        return False
    return any(k in SPEC_DICT_KEYS for k in value)


def skeleton_for(value: Any) -> Any:
    """Return a typed empty placeholder for one contract node.

    * list -> [] (unknown cardinality);
    * dict spec with type array/object -> [] or nested {..:null};
    * dict spec with default/example -> that literal;
    * plain nested object -> recurse, leaves become null;
    * scalar/str/other -> null.
    """
    if isinstance(value, dict):
        if _is_spec_dict(value):
            t = str(value.get("type", "")).lower()
            if t in ("array", "list"):
                return []
            if t in ("object", "dict", "map"):
                props = value.get("properties") or value.get("fields")
                if isinstance(props, dict):
                    return {str(k): skeleton_for(v) for k, v in props.items()}
                return {}
            if "default" in value:
                return value["default"]
            if "example" in value:
                return value["example"]
            return None
        return {str(k): skeleton_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    # bool / int / float / str / None leaves carry no invented value
    return None


def normalize_contract(contract: Any) -> Tuple[List[str], Dict[str, Any]]:
    """Normalize any contract shape into (top_level_fields, backfill_template).

    Supported shapes (the same shapes the runtime cases may use):

    * string          -> single field name
    * [str, ...]      -> field names
    * [{name/...}, ]  -> field names with spec-derived typed defaults
    * {field: spec}   -> schema-like dict: keys are fields
    * {field: sub}    -> nested data object: recurse into sub-keys
    * wrapper dicts ``fields``/``required_fields``/``properties``/``schema``
    """
    if isinstance(contract, str):
        return [contract], {contract: None}
    if isinstance(contract, list):
        names: List[str] = []
        template: Dict[str, Any] = {}
        for item in contract:
            if isinstance(item, str):
                names.append(item)
                template[item] = None
            elif isinstance(item, dict):
                name = (item.get("name") or item.get("key")
                        or item.get("field") or item.get("id"))
                if name:
                    names.append(str(name))
                    template[str(name)] = skeleton_for(item)
        if not names and contract:
            names = [str(x) for x in contract if not isinstance(x, dict)]
            template = {n: None for n in names}
        return names, template
    if isinstance(contract, dict):
        for wrapper in ("fields", "required_fields", "properties", "schema"):
            if wrapper in contract and isinstance(contract[wrapper], (dict, list)):
                return normalize_contract(contract[wrapper])
        if not contract:
            return [], {}
        spec_vals = [v for v in contract.values() if isinstance(v, dict)]
        if spec_vals and all(_is_spec_dict(v) for v in spec_vals):
            names = [str(k) for k in contract.keys()]
            template = {str(k): skeleton_for(v) for k, v in contract.items()}
            return names, template
        # Nested data object (values are not specs): preserve sub-structure.
        names = [str(k) for k in contract.keys()]
        template = {str(k): skeleton_for(v) for k, v in contract.items()}
        return names, template
    return [str(contract)], {str(contract): None}


def _type_hint(value: Any) -> str:
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def scan_materials(input_dir: str) -> List[Dict[str, Any]]:
    """Enumerate material files under input_dir (excluding case.json/task.md
    which are reported separately). Large files are skipped, not dropped
    silently."""
    items: List[Dict[str, Any]] = []
    if not os.path.isdir(input_dir):
        return items
    for root, _dirs, files in sorted(os.walk(input_dir)):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                items.append({"path": rel, "bytes": None, "skipped": True,
                              "reason": f"stat_error:{exc}"})
                continue
            if rel in ("case.json", "task.md"):
                continue
            if size > MAX_FILE_BYTES:
                items.append({"path": rel, "bytes": size, "skipped": True,
                              "reason": "exceeds_2MB"})
                continue
            items.append({"path": rel, "bytes": size, "skipped": False})
    return items


def build_manifest(input_dir: str) -> Dict[str, Any]:
    """Full discovery of one case input. Never raises."""
    errors: List[str] = []
    degradations: List[str] = []

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    case, case_err = load_json(case_path)
    if case_err:
        errors.append(f"case.json:{case_err}")

    case_id = discover_case_id(case) if isinstance(case, dict) else None
    if case_id is None and case_err is None and os.path.isfile(case_path):
        degradations.append("case_id not found in case.json")

    contract_key: Optional[str] = None
    fields: List[str] = []
    template: Dict[str, Any] = {}
    if isinstance(case, dict):
        contract_key, contract = find_contract_root(case)
        if contract is not None:
            fields, template = normalize_contract(contract)
        else:
            degradations.append("output_contract not found in case.json")

    materials = scan_materials(input_dir)
    skipped = [m for m in materials if m.get("skipped")]
    for m in skipped:
        degradations.append(f"skipped:{m['path']}:{m.get('reason')}")

    task_text, task_warn = read_text(task_path) if os.path.isfile(task_path) else ("", "missing:task.md")
    if task_warn:
        degradations.append(f"task.md:{task_warn}")

    contract_types = [
        {"field": f, "default_type": _type_hint(template.get(f))}
        for f in fields
    ]

    return {
        "pipeline_version": PIPELINE_VERSION,
        "case_id": case_id,
        "input_dir": input_dir,
        "case_json_present": os.path.isfile(case_path),
        "case_parse_error": case_err,
        "contract_key": contract_key,
        "contract_fields": fields,
        "contract_types": contract_types,
        "contract_template": template,
        "materials": materials,
        "material_count": sum(1 for m in materials if not m.get("skipped")),
        "task_md_present": os.path.isfile(task_path),
        "errors": errors,
        "degradations": degradations,
        "scanned_at": now_iso(),
    }


def build_case_normalized(manifest: Dict[str, Any],
                          case: Any) -> Dict[str, Any]:
    """A compact copy of case.json the agent must verify before backfilling."""
    if not isinstance(case, dict):
        case = {}
    return {
        "case_id": manifest.get("case_id"),
        "contract_key": manifest.get("contract_key"),
        "contract_fields": manifest.get("contract_fields"),
        "contract_types": manifest.get("contract_types"),
        "contract_template": manifest.get("contract_template"),
        # Keep only small, obviously-safe metadata for the agent to read; the
        # full case.json stays at /input and is read directly when needed.
        "case_meta_keys": sorted(k for k in case.keys()),
        "errors": manifest.get("errors"),
        "degradations": manifest.get("degradations"),
    }


def build_skeleton(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the first honest partial result.json.

    Contract fields are filled with typed empty placeholders (never invented
    content); standard fields are appended without clobbering contract names.
    """
    result: Dict[str, Any] = {}
    result["case_id"] = manifest.get("case_id")

    for key, val in manifest.get("contract_template", {}).items():
        result[key] = val

    for key, val in STANDARD_FIELDS.items():
        if key not in result:
            result[key] = val

    errors = list(manifest.get("errors") or [])
    degradations = list(manifest.get("degradations") or [])
    case_present = bool(manifest.get("case_json_present"))
    case_err = manifest.get("case_parse_error")

    if case_present and case_err:
        result["status"] = "input_error"
    elif not case_present:
        result["status"] = "insufficient_evidence"
    else:
        result["status"] = "partial"

    if errors:
        result["errors"] = errors

    if degradations:
        result["degradations"] = degradations

    result["runtime"] = 0.0
    return result


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

def _check(report: Dict[str, Any], passed: bool, name: str, detail: str) -> None:
    report["checks"].append({"name": name, "passed": bool(passed),
                             "detail": detail})
    if not passed:
        report["passed"] = False
        report["errors"].append(f"{name}: {detail}")


def validate_output(output_dir: str, work_dir: str) -> Dict[str, Any]:
    """Check result.json and the editable/evidence artifacts. Never raises."""
    report: Dict[str, Any] = {"passed": True, "checks": [], "errors": []}

    manifest_path = os.path.join(work_dir, "input_manifest.json")
    manifest, man_err = load_json(manifest_path)
    if man_err or not isinstance(manifest, dict):
        manifest = {}
        _check(report, False, "manifest", f"cannot read input_manifest.json: {man_err}")

    result_path = os.path.join(output_dir, "result.json")
    result, res_err = load_json(result_path)
    if res_err or not isinstance(result, dict):
        _check(report, False, "result_json", f"invalid or missing: {res_err}")
        return report

    case_id = result.get("case_id")
    _check(report, case_id is not None, "case_id", "present" if case_id is not None else "missing")

    contract_fields = manifest.get("contract_fields") or []
    for field in contract_fields:
        _check(report, field in result, f"contract:{field}",
               "backfilled" if field in result else "missing from result.json")

    for field in STANDARD_FIELDS:
        _check(report, field in result, f"standard:{field}",
               "present" if field in result else "missing")

    status = result.get("status")
    _check(report, status in VALID_STATUS, "status",
           f"{status!r}" if status in VALID_STATUS else f"invalid {status!r}")

    change_log = result.get("change_log")
    _check(report, isinstance(change_log, list), "change_log:list",
           "list" if isinstance(change_log, list) else "not a list")
    if isinstance(change_log, list):
        for i, entry in enumerate(change_log):
            if not isinstance(entry, dict):
                _check(report, False, f"change_log[{i}]", "not an object")
                continue
            for req in ("at", "target", "before", "after", "reason"):
                _check(report, req in entry, f"change_log[{i}].{req}",
                       "present" if req in entry else "missing")

    manuscript = os.path.join(output_dir, "manuscript.md")
    if os.path.isfile(manuscript):
        text, _warn = read_text(manuscript)
        _check(report, bool(text.strip()), "manuscript",
               "non-empty" if text.strip() else "empty")
    else:
        _check(report, False, "manuscript", "manuscript.md missing")

    evidence = os.path.join(output_dir, "evidence_map.json")
    ev, ev_err = load_json(evidence)
    if ev_err or not isinstance(ev, dict):
        _check(report, False, "evidence_map", f"invalid or missing: {ev_err}")
    else:
        _check(report, True, "evidence_map:json", "parses")
        for key, item in ev.items():
            if not isinstance(item, dict):
                _check(report, False, f"evidence:{key}", "not an object")
                continue
            for req in ("locator", "quote"):
                _check(report, req in item and bool(item.get(req)),
                       f"evidence:{key}.{req}",
                       "present" if item.get(req) else "missing/empty")

    cl_path = os.path.join(output_dir, "change_log.json")
    if os.path.isfile(cl_path):
        cl, cl_err = load_json(cl_path)
        _check(report, cl_err is None, "change_log.json", "parses" if cl_err is None else cl_err)
    else:
        _check(report, isinstance(result.get("change_log"), list) and bool(result.get("change_log")),
               "change_log.json", "file missing but result.change_log present" if result.get("change_log") else "no change log")

    return report
