#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared implementation for the systematic-review skill runtime helper.

Standard library only. Content fields are deterministic (``sort_keys=True``
gives a stable key order); timestamps and runtime are explicit trace fields and
are the only intentionally variable parts of the output.
"""

import copy
import json
import os
import time
from datetime import datetime, timezone

ENCODINGS = ("utf-8-sig", "utf-16", "gb18030", "latin-1")
MAX_TEXT_BYTES = 2 * 1024 * 1024  # per-file read cap before truncation

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUS_VALUES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# Fields every result must carry regardless of the case output_contract.
HONEST_FIELDS = {
    "case_id": "string",
    "status": "string",
    "artifacts": "array",
    "sources": "array",
    "uncertainty": "object",
    "change_log": "array",
    "limitations": "array",
    "errors": "array",
    "degradations": "array",
    "pending_human": "array",
    "runtime_seconds": "number",
}

# Domain contract used when case.json carries no explicit output_contract.
DEFAULT_CONTRACT = {
    "review_question": "string",
    "subquestions": "array",
    "search_strategy": {
        "type": "object",
        "fields": {
            "databases": "array",
            "query_blocks": "array",
            "search_date": "string",
            "time_range": "string",
            "language_limits": "array",
            "total_hits": "number",
        },
    },
    "inclusion_criteria": "array",
    "exclusion_criteria": "array",
    "screening_records": "array",
    "prisma_flow": {
        "type": "object",
        "fields": {
            "identified": "number",
            "deduplicated": "number",
            "screened_title_abstract": "number",
            "excluded_title_abstract": "number",
            "screened_fulltext": "number",
            "excluded_fulltext": "number",
            "included": "number",
            "notes": "string",
        },
    },
    "quality_appraisal": "array",
    "synthesis": {
        "type": "object",
        "fields": {
            "conclusions": "array",
            "conflicts": "array",
            "gaps": "array",
        },
    },
    "evidence_map": "array",
}

CONTRACT_KEYS = (
    "output_contract", "outputContract", "output_contract_schema",
    "output_schema", "result_contract", "result_schema", "contract",
)

TEXT_EXTS = {".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml",
             ".html", ".htm", ".tex", ".bib", ".tsv", ".yaml", ".yml"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
              ".bmp", ".svg"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls", ".odt", ".ods"}


def epoch_now():
    return int(time.time())


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def load_text_file(path):
    """Read text with encoding fallback; never raises on ordinary failures."""
    errors, degradations = [], []
    if not os.path.exists(path):
        return "", ["missing file: %s" % path], degradations
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", ["stat failed %s: %s" % (path, exc)], degradations
    if size > MAX_TEXT_BYTES:
        degradations.append("file >%d bytes truncated: %s" % (MAX_TEXT_BYTES, path))
    last = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                data = fh.read(MAX_TEXT_BYTES + 1)
            if len(data) > MAX_TEXT_BYTES:
                data = data[:MAX_TEXT_BYTES]
            return data, errors, degradations
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # pragma: no cover - defensive
            last = exc
            break
    errors.append("encoding read failed %s: %s" % (path, last))
    return "", errors, degradations


def load_json(path):
    if not os.path.exists(path):
        return None, ["missing JSON: %s" % path], []
    text, errors, degradations = load_text_file(path)
    if errors:
        return None, errors, degradations
    if not text.strip():
        return None, ["empty JSON: %s" % path], degradations
    try:
        return json.loads(text), errors, degradations
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append("JSON parse failed %s: %s" % (path, exc))
        return None, errors, degradations


def read_jsonl(path):
    records, errors, degradations = [], [], []
    if not os.path.exists(path):
        return records, errors, degradations
    text, errors, degradations = load_text_file(path)
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append("%s:%d bad JSON line: %s" % (path, lineno, exc))
            continue
        records.append(rec if isinstance(rec, dict) else {"value": rec})
    return records, errors, degradations


def write_json(path, obj):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        return []
    except Exception as exc:  # pragma: no cover - defensive
        return ["write failed %s: %s" % (path, exc)]


def classify_ext(ext):
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    return "other"


def _walk(root, depth, max_depth):
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            if depth < max_depth:
                for item in _walk(path, depth + 1, max_depth):
                    yield item
        else:
            yield path


def discover_input(input_dir):
    inv = {"input_dir": input_dir, "exists": False, "total_files": 0,
           "case_json": None, "task_md": None, "materials": [],
           "errors": [], "degradations": []}
    if not os.path.isdir(input_dir):
        inv["errors"].append("input dir not found: %s" % input_dir)
        return inv
    inv["exists"] = True
    for path in _walk(input_dir, 0, 3):
        inv["total_files"] += 1
        base = os.path.basename(path)
        low = base.lower()
        if low == "case.json":
            inv["case_json"] = path
            continue
        if low == "task.md":
            inv["task_md"] = path
            continue
        ext = os.path.splitext(low)[1]
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        inv["materials"].append({"path": path, "kind": classify_ext(ext),
                                 "size": size, "name": base})
    return inv


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId"):
        value = case_obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def normalize_spec(value):
    """Normalize an output_contract value into a type spec (string or nested)."""
    if value is None:
        return "any"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("str", "text", "string", "varchar"):
            return "string"
        if v in ("int", "float", "number", "numeric", "integer"):
            return "number"
        if v in ("bool", "boolean"):
            return "boolean"
        if v in ("list", "array", "seq"):
            return "array"
        if v in ("dict", "map", "object", "obj"):
            return "object"
        return "any"
    if isinstance(value, list):
        item = value[0] if value else "any"
        return {"type": "array", "items": normalize_spec(item)}
    if isinstance(value, dict):
        if "type" in value:
            t = str(value["type"]).strip().lower()
            if t in ("object", "obj", "dict", "map"):
                fields = value.get("fields") or value.get("properties") or {}
                if isinstance(fields, dict):
                    return {"type": "object",
                            "fields": {k: normalize_spec(v) for k, v in fields.items()}}
                return {"type": "object", "fields": {}}
            if t in ("array", "list", "seq"):
                items = value.get("items") or value.get("item")
                return {"type": "array", "items": normalize_spec(items) if items else "any"}
            return normalize_spec(t) if isinstance(t, str) else "any"
        return {"type": "object",
                "fields": {k: normalize_spec(v) for k, v in value.items()}}
    return "any"


def contract_from_raw(raw):
    """Turn a discovered output_contract value into field -> spec mapping."""
    if isinstance(raw, dict):
        if isinstance(raw.get("fields"), dict):
            raw = raw["fields"]
        elif isinstance(raw.get("properties"), dict):
            raw = raw["properties"]
        elif "type" in raw and str(raw.get("type")).strip().lower() not in (
                "object", "obj", "dict", "map", "array", "list", "seq"):
            return {"_contract": normalize_spec(raw)}
        return {k: normalize_spec(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if isinstance(item, str):
                out[item] = "any"
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    out[str(name)] = normalize_spec(item.get("type", item.get("spec", "any")))
        return out
    return {}


def _find_contract(node, depth=0):
    if depth > 3 or not isinstance(node, (dict, list)):
        return None
    if isinstance(node, dict):
        for key in CONTRACT_KEYS:
            if key in node and isinstance(node[key], (dict, list)):
                return node[key]
        for value in node.values():
            found = _find_contract(value, depth + 1)
            if found is not None:
                return found
        return None
    for item in node:
        found = _find_contract(item, depth + 1)
        if found is not None:
            return found
    return None


def merge_spec(base, patch):
    if (isinstance(base, dict) and isinstance(patch, dict)
            and base.get("type") == "object" and patch.get("type") == "object"):
        fields = dict(base.get("fields") or {})
        fields.update(patch.get("fields") or {})
        return {"type": "object", "fields": fields}
    return copy.deepcopy(patch)


def build_contract(case_obj):
    contract = copy.deepcopy(DEFAULT_CONTRACT)
    if isinstance(case_obj, dict):
        raw = _find_contract(case_obj)
        if raw is not None:
            parsed = contract_from_raw(raw)
            for field, spec in parsed.items():
                if field in contract:
                    contract[field] = merge_spec(contract[field], spec)
                else:
                    contract[field] = spec
    return contract


def default_value(spec):
    if isinstance(spec, dict):
        if spec.get("type") == "object":
            fields = spec.get("fields") or {}
            return {k: default_value(v) for k, v in fields.items()}
        if spec.get("type") == "array":
            return []
        return None
    if spec == "array":
        return []
    if spec == "object":
        return {}
    return None


def type_matches(value, spec):
    if value is None:
        return True
    if isinstance(spec, dict):
        t = spec.get("type")
        if t == "object":
            if not isinstance(value, dict):
                return False
            for sub, sub_spec in (spec.get("fields") or {}).items():
                if sub in value and not type_matches(value[sub], sub_spec):
                    return False
            return True
        if t == "array":
            if not isinstance(value, list):
                return False
            items = spec.get("items")
            if items and items != "any":
                return all(type_matches(v, items) for v in value)
            return True
        return True
    if spec == "string":
        return isinstance(value, str)
    if spec == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec == "boolean":
        return isinstance(value, bool)
    if spec == "array":
        return isinstance(value, list)
    if spec == "object":
        return isinstance(value, dict)
    return True


def spec_name(spec):
    if isinstance(spec, dict):
        t = spec.get("type", "object")
        if t == "object":
            return "object{%s}" % ",".join(sorted(spec.get("fields") or {}))
        if t == "array":
            return "array[%s]" % spec_name(spec.get("items") or "any")
        return str(t)
    return str(spec)


def filled_value(value):
    if value is None:
        return False
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def new_skeleton(case_id, contract, note="skeleton"):
    result = {}
    for field, spec in contract.items():
        result[field] = default_value(spec)
    for field, spec in HONEST_FIELDS.items():
        if field not in result:
            result[field] = default_value(spec)
    result["case_id"] = (case_id or "unknown").strip() or "unknown"
    result["status"] = STATUS_PARTIAL
    result["change_log"] = [{
        "ts": iso_now(), "action": "skeleton", "detail": note,
        "fields": ["status", "change_log"],
    }]
    result["runtime_seconds"] = None
    return result


def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = {}
        for key in base:
            out[key] = (deep_merge(base[key], patch[key])
                        if key in patch else copy.deepcopy(base[key]))
        for key in patch:
            if key not in base:
                out[key] = copy.deepcopy(patch[key])
        return out
    return copy.deepcopy(patch)


def changed_top_keys(before, after):
    keys = sorted(set(before) | set(after))
    return [k for k in keys
            if json.dumps(before.get(k), sort_keys=True, default=str)
            != json.dumps(after.get(k), sort_keys=True, default=str)]


def _norm_stage(stage):
    s = str(stage or "").strip().lower().replace(" ", "_")
    if s in ("title_abstract", "title", "abstract", "title/abstract",
             "title_and_abstract", "screening1", "screen_1", "titles_abstracts"):
        return "title_abstract"
    if s in ("full_text", "fulltext", "full-text", "full_text", "screening2", "screen_2"):
        return "full_text"
    return s


def recompute_prisma(records):
    counts = {
        "identified": 0, "deduplicated": 0,
        "screened_title_abstract": 0, "excluded_title_abstract": 0,
        "screened_fulltext": 0, "excluded_fulltext": 0,
        "included": 0, "undetermined": 0,
    }
    for rec in records:
        if not isinstance(rec, dict):
            continue
        counts["identified"] += 1
        if rec.get("duplicate_of"):
            counts["deduplicated"] += 1
            continue
        stage = _norm_stage(rec.get("stage"))
        decision = str(rec.get("decision") or "").strip().lower()
        if stage == "title_abstract":
            counts["screened_title_abstract"] += 1
            if decision == "exclude":
                counts["excluded_title_abstract"] += 1
        elif stage == "full_text":
            counts["screened_fulltext"] += 1
            if decision == "exclude":
                counts["excluded_fulltext"] += 1
            elif decision == "include":
                counts["included"] += 1
        if decision == "undetermined":
            counts["undetermined"] += 1
    return counts


def audit_screening(records):
    issues = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        decision = str(rec.get("decision") or "").strip().lower()
        if decision == "exclude":
            if not str(rec.get("reason") or "").strip():
                issues.append("record %d: exclude missing reason" % i)
            if not str(rec.get("criterion_id") or "").strip():
                issues.append("record %d: exclude missing criterion_id" % i)
        if decision in ("include", "exclude") and not str(rec.get("judge") or "").strip():
            issues.append("record %d: %s missing judge" % (i, decision))
    return issues


def audit_evidence(cards):
    issues = []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        if not str(card.get("locator") or "").strip():
            issues.append("evidence %d: missing locator" % i)
        if not str(card.get("quote") or "").strip():
            issues.append("evidence %d: missing quote" % i)
    return issues


def audit_synthesis(synthesis, evidence_cards):
    issues = []
    if not isinstance(synthesis, dict):
        return ["synthesis not an object"]
    ids = set()
    for i, card in enumerate(evidence_cards):
        if isinstance(card, dict):
            eid = card.get("evidence_id") or card.get("id")
            ids.add(str(eid if eid is not None else i))
    for kind in ("conclusions", "conflicts", "gaps"):
        items = synthesis.get(kind) or []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            refs = item.get("evidence_ids") or item.get("evidence") or []
            for eid in refs:
                if str(eid) not in ids:
                    issues.append("%s[%d]: evidence_id %r not in evidence_map"
                                  % (kind, i, eid))
    return issues


def validate_contract(result, contract):
    problems = []
    if not isinstance(result, dict):
        return ["result not a JSON object"]
    for field, spec in contract.items():
        if field not in result:
            problems.append("missing contract field: %s" % field)
            continue
        if not type_matches(result[field], spec):
            problems.append("wrong type for %s: got %s, want %s"
                            % (field, type(result[field]).__name__, spec_name(spec)))
    return problems


def build_contract_report(result, contract):
    rows = []
    for field, spec in contract.items():
        present = field in result
        ok = present and type_matches(result.get(field), spec)
        filled = present and filled_value(result.get(field))
        rows.append({"field": field, "spec": spec_name(spec), "present": present,
                     "type_ok": ok, "filled": filled})
    return {
        "case_id": result.get("case_id") if isinstance(result, dict) else None,
        "rows": rows,
        "missing": [r["field"] for r in rows if not r["present"]],
        "unfilled": [r["field"] for r in rows if not r["filled"]],
        "type_mismatch": [r["field"] for r in rows if not r["type_ok"]],
    }


def _fmt_item(item):
    if isinstance(item, dict):
        for key in ("text", "criterion", "statement", "claim", "description"):
            if item.get(key):
                return str(item[key])
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item)


def format_review_md(result):
    result = result if isinstance(result, dict) else {}
    out = ["# 系统性文献综述（自动组装稿）", "",
           "- case_id: `%s`" % result.get("case_id", "unknown"),
           "- status: `%s`" % result.get("status", "unknown"), ""]
    rq = result.get("review_question")
    if rq:
        out += ["## 研究问题", "", str(rq), ""]
    subs = result.get("subquestions") or []
    if subs:
        out += ["## 子问题", ""] + ["- %s" % _fmt_item(s) for s in subs] + [""]
    inc = result.get("inclusion_criteria") or []
    exc = result.get("exclusion_criteria") or []
    if inc or exc:
        out.append("## 纳排标准")
        out.append("")
        if inc:
            out.append("**纳入**")
            out.append("")
            out += ["- %s" % _fmt_item(x) for x in inc]
            out.append("")
        if exc:
            out.append("**排除**")
            out.append("")
            out += ["- %s" % _fmt_item(x) for x in exc]
            out.append("")
    pf = result.get("prisma_flow") or {}
    if pf:
        out += ["## 筛选计数", "", "| 阶段 | 数量 |", "|---|---|"]
        labels = [("identified", "检索命中（去重前）"),
                  ("deduplicated", "去重"),
                  ("screened_title_abstract", "题录/摘要筛选"),
                  ("excluded_title_abstract", "题录/摘要排除"),
                  ("screened_fulltext", "全文筛选"),
                  ("excluded_fulltext", "全文排除"),
                  ("included", "纳入")]
        for key, label in labels:
            if key in pf:
                out.append("| %s | %s |" % (label, pf[key]))
        out.append("")
    synth = result.get("synthesis") or {}
    for kind, title in (("conclusions", "综合结论"),
                        ("conflicts", "冲突证据"),
                        ("gaps", "研究空白")):
        items = synth.get(kind) or []
        if items:
            out += ["## %s" % title, ""]
            for item in items:
                if isinstance(item, dict):
                    text = _fmt_item(item.get("claim") or item.get("text") or item)
                    refs = item.get("evidence_ids") or []
                    out.append("- %s" % text)
                    if refs:
                        out.append("  - 证据回链: %s" % ", ".join(str(r) for r in refs))
                else:
                    out.append("- %s" % _fmt_item(item))
            out.append("")
    unc = result.get("uncertainty") or {}
    if isinstance(unc, dict) and unc:
        out += ["## 不确定性", ""]
        for key, value in unc.items():
            out.append("- **%s**: %s" % (key, _fmt_item(value)))
        out.append("")
    src = result.get("sources") or []
    if src:
        out += ["## 来源", ""]
        for s in src:
            out.append("- %s" % _fmt_item(s))
        out.append("")
    lim = result.get("limitations") or []
    if lim:
        out += ["## 局限与缺口", ""] + ["- %s" % _fmt_item(x) for x in lim] + [""]
    if not out[-1] == "":
        out.append("")
    return "\n".join(out).rstrip() + "\n"
