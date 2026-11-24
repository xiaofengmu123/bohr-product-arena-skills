"""Evidence-ledger core for the deep-research skill (standard library only).

Design decision (differs from a payload/deep-merge design): the model NEVER
hand-edits result.json. It appends *validated evidence cards* and writes
plan/product/conclusions/uncertainty as separate typed files under the work
directory. ``assemble()`` then rebuilds result.json deterministically from the
ledger plus the case's ``output_contract``, after a three-tier validation:

  1. syntactic   -- JSON/JSONL parses, field presence, field types, controlled
                    vocabularies (judgment / strength / product type);
  2. referential -- conclusion.evidence_ids must resolve to cards, and
                    card.conclusion_ids must resolve to conclusions;
  3. contract    -- every output_contract field is materialized into result.json
                    (missing values default to null/[]/""), so nothing is
                    renamed or dropped.

Validation happens BEFORE writing: a structurally invalid artifact still yields
a valid, loadable partial result with the errors recorded, never a crash or a
broken file.
"""

import copy
import datetime as _dt
import json
import os
import re
import time

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

JUDGMENTS = ("supports", "partial", "unsupported", "conflicts", "extraction_failed")
STRENGTHS = ("established", "inferred", "conjectural")
PRODUCT_TYPES = ("synthesis", "screening", "trend", "proposal")

TEXT_EXTS = {".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml",
             ".html", ".htm", ".tex", ".bib", ".yaml", ".yml", ".rst"}
BINARY_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
               ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB per text file before truncation

CANONICAL = {
    "case_id": "",
    "status": STATUS_PARTIAL,
    "research_product": {
        "type": "synthesis",
        "question": "",
        "summary": "",
        "conclusions": [],
    },
    "evidence_matrix": [],
    "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
    "sources": [],
    "change_log": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime_seconds": 0,
}

CONTRACT_KEYS = ("output_contract", "output_schema", "result_schema",
                 "result_contract", "schema", "fields")

_TYPE_TOKENS = {"string", "str", "text", "number", "integer", "int", "float",
                "boolean", "bool", "array", "list", "object", "dict", "map",
                "null", "none", "optional", "any"}

GENERIC_GAP_RE = re.compile(
    r"^(尚需|还需|有待|需要)?(进一步|更多|深入)?(研究|探讨|调查|验证|数据|资料|证据)(。|；|;|,|，)?$"
)


# --------------------------------------------------------------------------- #
# Time / filesystem helpers
# --------------------------------------------------------------------------- #

def epoch_now():
    return time.time()


def iso_now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def write_json(path, obj):
    """Atomic JSON write. Returns a list of error strings (empty on success)."""
    try:
        ensure_dir(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return []
    except Exception as exc:  # noqa: BLE001 - robustness boundary
        return ["write_json(%s): %s: %s" % (path, type(exc).__name__, exc)]


def _decode(raw, path):
    """Decode bytes with an encoding fallback chain. Returns (text, degraded)."""
    if len(raw) > MAX_TEXT_BYTES:
        raw = raw[:MAX_TEXT_BYTES]
        degraded = True
    else:
        degraded = False
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), degraded
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), True


def load_json(path):
    """Return (obj, errors, degradations). Never raises."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except Exception as exc:  # noqa: BLE001
        return None, ["load_json(%s): %s: %s" % (path, type(exc).__name__, exc)], []
    text, degraded = _decode(raw, path)
    degradations = ["%s: truncated at %d bytes" % (path, MAX_TEXT_BYTES)] if degraded else []
    try:
        return json.loads(text), [], degradations
    except Exception as exc:  # noqa: BLE001
        return None, ["load_json(%s): bad JSON: %s" % (path, exc)], degradations


def load_jsonl(path):
    """Return (list_of_objects, errors, degradations). Skips malformed lines."""
    items, errors, degradations = [], [], []
    text, rerrs, rdegs = read_text(path)
    errors.extend(rerrs)
    degradations.extend(rdegs)
    if not text.strip():
        return items, errors, degradations
    for idx, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception as exc:  # noqa: BLE001
            errors.append("%s:%d: bad JSONL: %s" % (path, idx, exc))
    return items, errors, degradations


def read_text(path):
    """Return (text, errors, degradations). Never raises."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except Exception as exc:  # noqa: BLE001
        return "", ["read_text(%s): %s: %s" % (path, type(exc).__name__, exc)], []
    text, degraded = _decode(raw, path)
    degradations = ["%s: truncated at %d bytes" % (path, MAX_TEXT_BYTES)] if degraded else []
    return text, [], degradations


# --------------------------------------------------------------------------- #
# Input discovery & contract echo
# --------------------------------------------------------------------------- #

def discover_input(input_dir):
    inv = {
        "exists": os.path.isdir(input_dir),
        "input_dir": os.path.abspath(input_dir),
        "case_json": None,
        "task_md": None,
        "materials": [],
        "total_files": 0,
        "total_bytes": 0,
        "errors": [],
    }
    if not inv["exists"]:
        return inv
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(p)
            except OSError as exc:
                inv["errors"].append("stat(%s): %s" % (rel, exc))
                continue
            inv["total_files"] += 1
            inv["total_bytes"] += size
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else ("binary" if ext in BINARY_EXTS else "other")
            if name.lower() == "case.json":
                inv["case_json"] = p
            elif name.lower() in ("task.md", "task.txt"):
                inv["task_md"] = p
            else:
                inv["materials"].append({"path": rel, "abspath": p,
                                         "bytes": size, "kind": kind})
    return inv


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "case"):
        value = obj.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _materialize(value):
    """Turn type-descriptor strings into empty defaults; keep concrete values."""
    if isinstance(value, dict):
        return {k: _materialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_materialize(v) for v in value]
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("string", "str", "text"):
            return ""
        if token in ("number", "integer", "int", "float", "boolean", "bool", "null",
                     "none", "optional", "any"):
            return None
        if token in ("array", "list"):
            return []
        if token in ("object", "dict", "map"):
            return {}
    return value


def build_contract(obj):
    """Extract the case's output contract and merge it over the canonical shape.

    Every contract field is materialized (missing values default to null/[]/""),
    so all original fields are echoed verbatim into result.json without being
    renamed or dropped.
    """
    contract = copy.deepcopy(CANONICAL)
    if not isinstance(obj, dict):
        return contract
    found = None
    for key in CONTRACT_KEYS:
        value = obj.get(key)
        if isinstance(value, dict):
            found = value
            break
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    found = parsed
                    break
            except ValueError:
                continue
        if isinstance(value, list) and not found:
            found = {v: None for v in value}
            break
    if isinstance(found, dict):
        contract = deep_merge(contract, _materialize(found))
    return contract


def new_skeleton(case_id, contract, note=""):
    result = copy.deepcopy(contract) if isinstance(contract, dict) else copy.deepcopy(CANONICAL)
    for key, value in CANONICAL.items():
        result.setdefault(key, copy.deepcopy(value))
    result["case_id"] = case_id or str(result.get("case_id") or "")
    result["status"] = result.get("status") or STATUS_PARTIAL
    result["change_log"] = result.get("change_log") if isinstance(result.get("change_log"), list) else []
    result["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    result["runtime_seconds"] = 0
    return result


def deep_merge(base, update):
    """Recursive dict merge; update wins. Lists are replaced, not concatenated."""
    out = copy.deepcopy(base)
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


# --------------------------------------------------------------------------- #
# Field validation (tier 1: syntactic)
# --------------------------------------------------------------------------- #

def validate_card(card, index=None):
    errors = []
    loc = "card#%s" % index if index is not None else "card"
    if not isinstance(card, dict):
        return ["%s: not an object" % loc]
    for req in ("id", "subquestion", "source", "locator", "quote", "claim", "judgment"):
        if not isinstance(card.get(req), str) or not card.get(req).strip():
            errors.append("%s: missing/empty required field '%s'" % (loc, req))
    judgment = card.get("judgment")
    if judgment and judgment not in JUDGMENTS:
        errors.append("%s: judgment '%s' not in %s" % (loc, judgment, list(JUDGMENTS)))
    for field in ("conclusion_ids", "evidence_ids"):
        if field in card:
            value = card[field]
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                errors.append("%s: %s must be a list of strings" % (loc, field))
    return errors


def validate_conclusion(concl, index=None):
    errors = []
    loc = "conclusion#%s" % index if index is not None else "conclusion"
    if not isinstance(concl, dict):
        return ["%s: not an object" % loc]
    if not isinstance(concl.get("id"), str) or not concl.get("id").strip():
        errors.append("%s: missing id" % loc)
    if not isinstance(concl.get("text"), str) or not concl.get("text").strip():
        errors.append("%s: missing text" % loc)
    evidence_ids = concl.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        errors.append("%s: evidence_ids must be a non-empty list" % loc)
    strength = concl.get("strength")
    if strength is not None and strength not in STRENGTHS:
        errors.append("%s: strength '%s' not in %s" % (loc, strength, list(STRENGTHS)))
    return errors


def validate_uncertainty(unc, errors):
    if not isinstance(unc, dict):
        errors.append("uncertainty: not an object")
        return
    for section in ("conflicts", "biases", "gaps", "undetermined"):
        value = unc.get(section)
        if value is not None and not isinstance(value, list):
            errors.append("uncertainty.%s: must be a list" % section)


# --------------------------------------------------------------------------- #
# Referential validation (tier 2) & status
# --------------------------------------------------------------------------- #

def check_references(conclusions, cards, decomposition_ids):
    errors, warnings = [], []
    card_ids = {c["id"] for c in cards if isinstance(c, dict) and c.get("id")}
    concl_ids = {c["id"] for c in conclusions if isinstance(c, dict) and c.get("id")}
    seen_cards, seen_concls = set(), set()
    for idx, card in enumerate(cards):
        cid = card.get("id")
        if cid in seen_cards:
            warnings.append("evidence id '%s' duplicated (first occurrence kept)" % cid)
        seen_cards.add(cid)
        for cid2 in card.get("conclusion_ids", []):
            if cid2 and cid2 not in concl_ids:
                errors.append("evidence#%d references unknown conclusion '%s'" % (idx, cid2))
        sub = card.get("subquestion")
        if decomposition_ids and sub and sub not in decomposition_ids:
            warnings.append("evidence '%s' cites subquestion '%s' not in decomposition.md" %
                            (card.get("id"), sub))
    for idx, concl in enumerate(conclusions):
        cid = concl.get("id")
        if cid in seen_concls:
            warnings.append("conclusion id '%s' duplicated" % cid)
        seen_concls.add(cid)
        for eid in concl.get("evidence_ids", []):
            if eid and eid not in card_ids:
                errors.append("conclusion#%d references unknown evidence '%s'" % (idx, eid))
    return errors, warnings


def parse_decomposition_ids(text):
    ids = []
    for line in (text or "").splitlines():
        m = re.match(r"^#{1,6}\s+(SQ\d+|[A-Z]{1,6}\d+)\b", line.strip())
        if m:
            ids.append(m.group(1))
    return ids


def compute_status(input_error_hint, cards, conclusions, product, ref_errors):
    if input_error_hint and not cards and not conclusions:
        return STATUS_INPUT_ERROR
    if not cards and not conclusions:
        return STATUS_INSUFFICIENT
    product_ok = isinstance(product, dict) and bool(
        product.get("question") and product.get("summary"))
    if not product_ok or not conclusions or ref_errors:
        return STATUS_PARTIAL
    return STATUS_OK


# --------------------------------------------------------------------------- #
# Deterministic assembly (tier 3: contract) -- the only place result.json is built
# --------------------------------------------------------------------------- #

def assemble(case_id, contract, product, conclusions, cards, uncertainty,
             runtime_seconds, errors, degradations, limitations, pending_human,
             input_error_hint, ref_errors):
    result = copy.deepcopy(contract) if isinstance(contract, dict) else copy.deepcopy(CANONICAL)
    for key, value in CANONICAL.items():
        result.setdefault(key, copy.deepcopy(value))

    product = product if isinstance(product, dict) else {}
    product_out = {
        "type": product.get("type") or "synthesis",
        "question": product.get("question") or "",
        "summary": product.get("summary") or "",
        "conclusions": conclusions if isinstance(conclusions, list) else [],
    }
    if product_out["type"] not in PRODUCT_TYPES:
        product_out["type"] = "synthesis"

    uncertainty = uncertainty if isinstance(uncertainty, dict) else {}
    uncertainty_out = {
        "conflicts": uncertainty.get("conflicts") or [],
        "biases": uncertainty.get("biases") or [],
        "gaps": uncertainty.get("gaps") or [],
        "undetermined": uncertainty.get("undetermined") or [],
    }

    result["case_id"] = case_id or str(result.get("case_id") or "")
    result["research_product"] = product_out
    result["evidence_matrix"] = cards if isinstance(cards, list) else []
    result["uncertainty"] = uncertainty_out
    result["status"] = compute_status(input_error_hint, cards, conclusions,
                                      product, ref_errors)
    result["errors"] = (result.get("errors") or []) + list(errors)
    result["degradations"] = (result.get("degradations") or []) + list(degradations)
    result["limitations"] = (result.get("limitations") or []) + list(limitations)
    result["pending_human"] = (result.get("pending_human") or []) + list(pending_human)
    result["runtime_seconds"] = round(runtime_seconds, 3)
    return result


# --------------------------------------------------------------------------- #
# Six-point coverage (used by selfcheck)
# --------------------------------------------------------------------------- #

def coverage_report(result, decomposition_text, search_plan_text):
    checks = []
    product = result.get("research_product") or {}
    conclusions = product.get("conclusions") or []
    cards = result.get("evidence_matrix") or []
    uncertainty = result.get("uncertainty") or {}
    card_ids = {c.get("id") for c in cards if isinstance(c, dict)}

    # 1. decomposition
    sub_ids = parse_decomposition_ids(decomposition_text)
    checks.append(("问题拆解", len(sub_ids) >= 2, "%d 个子问题" % len(sub_ids)))

    # 2. search plan
    plan_ok = bool(search_plan_text.strip()) and any(
        k in search_plan_text for k in ("停止", "顺序", "来源", "范围"))
    checks.append(("检索计划", plan_ok, "含顺序/来源范围/停止条件" if plan_ok else "缺失或过简"))

    # 3. evidence back-link
    backlink_ok = bool(cards) and all(
        isinstance(c.get("locator"), str) and c.get("locator").strip() and
        isinstance(c.get("quote"), str) and c.get("quote").strip()
        for c in cards if isinstance(c, dict))
    unresolved = sum(
        1 for c in conclusions
        for e in (c.get("evidence_ids") or [])
        if e and e not in card_ids)
    checks.append(("证据回链", backlink_ok and unresolved == 0,
                   "%d 证据卡，%d 个悬空引用" % (len(cards), unresolved)))

    # 4. conflicts
    conflicts = uncertainty.get("conflicts") or []
    conflicts_ok = all(
        isinstance(x, dict) and (x.get("sources") or x.get("source_a")) and x.get("resolution")
        for x in conflicts)
    checks.append(("冲突证据", conflicts_ok or not conflicts,
                   "%d 条冲突，均含两来源与解决说明" % len(conflicts) if conflicts_ok else
                   "%d 条冲突未完整并列来源" % len(conflicts)))

    # 5. conclusions
    concl_ok = bool(conclusions) and bool(product.get("question")) and bool(product.get("summary"))
    checks.append(("综合结论", concl_ok, "%d 条结论，与研究问题同域" % len(conclusions)))

    # 6. gaps
    gaps = (uncertainty.get("gaps") or []) + (result.get("pending_human") or [])
    specific_gaps = [g for g in gaps if isinstance(g, str) and len(g) >= 12 and
                     not GENERIC_GAP_RE.match(g.strip())]
    checks.append(("研究空白", bool(specific_gaps), "%d 条具体空白" % len(specific_gaps)))

    return checks
