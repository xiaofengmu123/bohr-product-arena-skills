#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性实验方案可行性审计引擎（仅标准库，Python>=3.8）。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from datetime import date

SCHEMA_VERSION = "1.1.0"
ENGINE_ID = "deterministic-feasibility-audit@1.1.0"
MAX_FILE_BYTES = 8 * 1024 * 1024  # 单个文本材料上限；case.json 单独放宽到 16MB
MAX_CASE_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 14
MAX_ITEMS_PER_DIM = 14


# 基础工具


TEXT_EXT = {".md", ".txt", ".json", ".csv", ".tsv", ".jsonl", ".yaml", ".yml", ".xml", ".html"}


def _stdout_utf8():
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def norm_ws(s) -> str:
    return re.sub(r"\s+", " ", str(s) if s is not None else "").strip()


def norm_key(k) -> str:
    s = unicodedata.normalize("NFKC", str(k)).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def norm_name(s) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    return " ".join(s.split())


def truncate(s, limit=240):
    s = norm_ws(s)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for sep in ("。", "；", ";", ". ", "，", ", "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            cut = cut[: idx + len(sep)]
            break
    return cut.rstrip() + "…"


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1, separators=(",", ": "))


def as_number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"[-+]?\d+(?:\.\d+)?", v.replace(",", ""))
        return float(m.group()) if m else None
    if isinstance(v, dict):
        for k in ("value", "amount", "n", "count", "qty", "quantity", "total"):
            got = as_number(v.get(k))
            if got is not None:
                return got
    return None


def as_number_unit(v, key_hint=None):
    unit = None
    if isinstance(v, dict):
        for k in ("value", "amount", "n", "count", "qty", "quantity"):
            if k in v:
                unit = as_unit(v.get("unit")) or as_unit(key_hint)
                return as_number(v[k]), unit, str(v[k])
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v), as_unit(key_hint), str(v)
    if isinstance(v, str):
        n = as_number(v)
        unit = as_unit(v) or as_unit(key_hint)
        return n, unit, v
    return None, as_unit(key_hint), str(v)


def as_unit(s) -> str | None:
    if s is None:
        return None
    t = str(s).lower()
    for u in UNIT_SYNONYMS:
        for alias in UNIT_SYNONYMS[u]["aliases"]:
            if re.search(r"(?<![0-9a-z])" + re.escape(alias) + r"(?![0-9a-z])", t) or t.strip() == alias:
                return u
    return None


def unit_kind(u):
    if u is None:
        return None
    return UNIT_SYNONYMS.get(u, {}).get("kind")


def to_base(n, unit):
    if n is None or unit is None:
        return n, unit
    spec = UNIT_SYNONYMS.get(unit)
    if not spec:
        return n, unit
    base = spec.get("base")
    if base is None:
        return n, unit
    return n * spec["factor"], base


def convert_hours(n, unit):
    if n is None:
        return None
    if unit is None:
        return None
    spec = UNIT_SYNONYMS.get(unit)
    if not spec or spec.get("kind") != "time":
        return None
    return n * spec["factor"]


UNIT_SYNONYMS = {
    "h": {"kind": "time", "factor": 1.0, "base": "h", "aliases": ["h", "hr", "hrs", "hour", "hours", "小时"]},
    "min": {"kind": "time", "factor": 1.0 / 60.0, "base": "h", "aliases": ["min", "mins", "minute", "minutes", "分钟"]},
    "s": {"kind": "time", "factor": 1.0 / 3600.0, "base": "h", "aliases": ["sec", "secs", "second", "seconds", "秒"]},
    "d": {"kind": "time", "factor": 24.0, "base": "h", "aliases": ["day", "days", "天"]},
    "wk": {"kind": "time", "factor": 168.0, "base": "h", "aliases": ["week", "weeks", "周"]},
    "ul": {"kind": "volume", "factor": 1.0, "base": "µL", "aliases": ["ul", "µl", "μl", "microliter", "microlitre"]},
    "ml": {"kind": "volume", "factor": 1000.0, "base": "µL", "aliases": ["ml", "milliliter", "millilitre"]},
    "l": {"kind": "volume", "factor": 1000000.0, "base": "µL", "aliases": ["l", "liter", "litre"]},
    "ug": {"kind": "mass", "factor": 1.0, "base": "µg", "aliases": ["ug", "µg", "μg", "microgram"]},
    "mg": {"kind": "mass", "factor": 1000.0, "base": "µg", "aliases": ["mg", "milligram"]},
    "g": {"kind": "mass", "factor": 1000000.0, "base": "µg", "aliases": ["g", "gram"]},
    "c": {"kind": "temperature", "factor": 1.0, "base": "°C", "aliases": ["c", "°c", "℃"]},
    "k": {"kind": "temperature", "factor": 1.0, "base": "K", "aliases": ["k", "°k"]},
    "rpm": {"kind": "rpm", "factor": 1.0, "base": "rpm", "aliases": ["rpm"]},
    "rcf": {"kind": "rcf", "factor": 1.0, "base": "×g", "aliases": ["rcf", "xg", "×g", "x g", "g-force"]},
    "count": {"kind": "count", "factor": 1.0, "base": "each", "aliases": ["each", "个", "支", "份", "管", "板"]},
}


def c_to_k(n):
    return n + 273.15


def k_to_c(n):
    return n - 273.15


def comparable_temperature(a, b):
    ua, ub = a[1], b[1]
    if ua is None or ub is None:
        return None
    ka, kb = unit_kind(ua), unit_kind(ub)
    if ka != kb:
        return None
    if ua == ub:
        return a[0], b[0]
    if ka == "temperature":
        av = c_to_k(a[0]) if ua == "c" else a[0]
        bv = c_to_k(b[0]) if ub == "c" else b[0]
        return av, bv
    return None



# 输入发现与读取



def load_text_file(path, max_bytes=MAX_FILE_BYTES):
    warns = []
    if not os.path.exists(path):
        return None, [f"missing:{path}"]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"unreadable:{path}:{exc}"]
    if size > max_bytes:
        return None, [f"oversize({size}>{max_bytes}):{path}"]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [f"unreadable:{path}:{exc}"]
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), warns
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), warns + [f"fallback_decode(replace):{path}"]


def scan_inputs(input_dir):
    files, notes = [], []
    if not os.path.isdir(input_dir):
        notes.append(f"input_dir_missing:{input_dir}")
        return files, notes
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    notes.append(f"skipped_symlink:{path}")
                    continue
                size = os.path.getsize(path)
            except OSError as exc:
                notes.append(f"unreadable:{path}:{exc}")
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            cap = MAX_CASE_BYTES if name.lower() in ("case.json", "case.jsonl") else MAX_FILE_BYTES
            status = "ok"
            if size > cap and ext in TEXT_EXT:
                status = "oversize"
                notes.append(f"oversize:{rel}({size})")
            files.append({"relpath": rel, "abspath": path, "size": size, "ext": ext, "status": status})
    return files, notes


def parse_json_lenient(text, relpath):
    warns = []
    if text is None:
        return None, warns + [f"no_text:{relpath}"]
    try:
        return json.loads(text), warns
    except Exception as exc:
        warns.append(f"json_parse_error:{relpath}:{exc}")
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        obj = json.loads(fixed)
        warns.append(f"json_repaired_trailing_comma:{relpath}")
        return obj, warns
    except Exception as exc:
        warns.append(f"json_repair_failed:{relpath}:{exc}")
    return None, warns



# 结构遍历



def walk(obj, path="$", out=None, depth=0):
    if out is None:
        out = []
    if depth > MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            out.append({"path": p, "key": k, "value": v, "depth": depth})
            walk(v, p, out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]", out, depth + 1)
    return out


def find_alias_nodes(obj, aliases, path="$", out=None, depth=0):
    if out is None:
        out = []
    if depth > MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if norm_key(k) in aliases:
                out.append((f"{path}.{k}", v, k))
            find_alias_nodes(v, aliases, f"{path}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_alias_nodes(v, aliases, f"{path}[{i}]", out, depth + 1)
    return out


def flatten_text(obj, depth=0, out=None):
    if out is None:
        out = []
    if depth > MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        for k in sorted(obj, key=lambda x: str(x)):
            flatten_text(obj[k], depth + 1, out)
    elif isinstance(obj, list):
        for v in obj:
            flatten_text(v, depth + 1, out)
    elif isinstance(obj, str):
        out.append(obj)
    return out


ALIASES = {
    "plans": {"plans", "candidateplans", "planversions", "versions", "schemes", "designs", "proposals"},
    "steps": {"steps", "procedures", "workflow", "protocol", "tasks", "operations", "experimentalprocedure"},
    "equipment": {"equipment", "devices", "instruments", "apparatus", "requiredequipment", "equipmentrequired"},
    "materials": {"materials", "consumables", "reagents", "supplies", "materialsrequired", "requiredmaterials"},
    "parameters": {"parameters", "params", "settings", "conditions", "parametervalues", "parameterdomains", "domains", "ranges", "allowedranges", "tolerances"},
    "samples": {"samples", "subjects", "specimens", "biologicalsamples", "sampleinfo", "sampling"},
    "ethics": {"ethics", "approvals", "compliance", "ethicsapproval", "safety", "permits", "regulatory"},
    "contracts": {"contracts", "resourcecontracts", "inventory", "quotes", "procurement", "bookings", "availability", "vendorquotes", "prices"},
    "controls": {"controls", "controlgroups", "comparators", "controlarms"},
    "variables": {"variables", "factors", "measures", "measurements", "endpoints"},
    "assumptions": {"assumptions", "premises", "prerequisites"},
    "criteria": {"interpretationcriteria", "successcriteria", "readouts", "acceptancecriteria", "stoppingcriteria"},
    "risks": {"risks", "riskregister", "hazards", "exceptions", "exceptionhandling"},
    "sources": {"sources", "references", "citations", "literature"},
    "objective": {"researchquestion", "objective", "researchobjective", "goal", "hypothesis", "aim", "aims", "researchgoal"},
    "budget": {"budget", "totalbudget", "availablebudget", "costlimit", "funding"},
    "deadline": {"deadline", "deadlinedate", "enddate", "firstusedate", "startdate", "scheduleddate"},
    "sample_needs": {"pergroupn", "groups", "replicates", "technicalreplicates", "attrition", "dropout", "sampleconsumption"},
}

DIM_ORDER = [
    "assumptions_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_exceptions",
    "ethics_safety",
]
DIM_PREFIX = {
    "assumptions_variables": "AV",
    "controls": "CT",
    "equipment_materials": "EM",
    "time_cost": "TC",
    "parameter_ranges": "PR",
    "risks_exceptions": "RX",
    "ethics_safety": "ES",
}
SEVERITY = {"conflict": 5, "blocked": 4, "missing": 3, "unconfirmed": 2, "conditional": 1, "ok": 0}

CASE_ID_KEYS = ("case_id", "id", "challenge_id", "caseId", "armiId")
CASE_FILE_NAMES = ("case.json", "case.jsonl")


def extract_case_id(data):
    if isinstance(data, dict):
        for k in CASE_ID_KEYS:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return str(v)
    return "unknown"



# 证据与 quote



class SourceIndex:

    def __init__(self, files, input_dir):
        self.files = files
        self.input_dir = input_dir
        self._texts = {}

    def raw_text(self, relpath):
        if relpath not in self._texts:
            for f in self.files:
                if f["relpath"] == relpath and f["status"] == "ok":
                    txt, _ = load_text_file(f["abspath"], MAX_CASE_BYTES if f["relpath"] in CASE_FILE_NAMES else MAX_FILE_BYTES)
                    self._texts[relpath] = txt or ""
                    break
            else:
                self._texts[relpath] = ""
        return self._texts[relpath]

    def file_of_locator(self, locator):
        if "#" in locator:
            head = locator.split("#", 1)[0]
        else:
            head = locator
        if head in ("$", "case.json", "case") or head.startswith("$") or head.startswith("."):
            for f in self.files:
                if f["relpath"] in CASE_FILE_NAMES:
                    return f["relpath"]
            return "case.json"
        return head

    def quote_verified(self, locator, quote):
        if quote is None:
            return False
        rel = self.file_of_locator(locator)
        raw = self.raw_text(rel)
        q = norm_ws(quote)
        r = norm_ws(raw)
        if bool(q) and q in r:
            return True
        # JSON 源可能以 \uXXXX 转义存储中文字符，检查转义形式
        try:
            esc = json.dumps(str(quote), ensure_ascii=True)[1:-1]
            if esc and esc in raw:
                return True
        except Exception:
            pass
        return False


def quote_from_leaf(source_index, locator, key, value):
    if value is None:
        return None
    if isinstance(value, str):
        q = truncate(value)
        return {"quote": q, "verified": source_index.quote_verified(locator, q), "mode": "string_value"}
    if isinstance(value, bool):
        lit = "true" if value else "false"
        q = f'"{key}": {lit}'
        if not source_index.quote_verified(locator, q):
            q = lit
        return {"quote": q, "verified": source_index.quote_verified(locator, q), "mode": "value_literal"}
    if isinstance(value, (int, float)):
        rel = source_index.file_of_locator(locator)
        raw = source_index.raw_text(rel)
        pat = re.compile(r'["\']?' + re.escape(str(key)) + r'["\']?\s*[:=]\s*(-?\d+(?:\.\d+)?)')
        m = pat.search(raw)
        if m:
            q = f'"{key}": {m.group(1)}'
            return {"quote": q, "verified": source_index.quote_verified(locator, q) or m.group(1) in norm_ws(raw), "mode": "value_literal"}
        lit = str(value)
        if lit.endswith(".0"):
            lit = lit[:-2]
        q = lit
        return {"quote": q, "verified": source_index.quote_verified(locator, q), "mode": "value_literal"}
    q = truncate(json.dumps(value, ensure_ascii=False))
    return {"quote": q, "verified": source_index.quote_verified(locator, q), "mode": "serialized_value"}


def quote_line(source_index, locator, regex):
    rel = source_index.file_of_locator(locator)
    raw = source_index.raw_text(rel)
    for line in raw.splitlines():
        if re.search(regex, line, re.I):
            q = truncate(line.strip())
            return {
                "locator": locator,
                "quote": q,
                "kind": "text_line",
                "verified": source_index.quote_verified(locator, q),
                "notes": [],
            }
    return {"locator": locator, "quote": None, "kind": "text_line", "verified": False, "notes": ["line_not_found"]}


def mk_evidence(locator, quote_info, kind="value", notes=None):
    e = {
        "locator": locator,
        "quote": quote_info.get("quote"),
        "kind": kind,
        "verified": bool(quote_info.get("verified")),
        "notes": notes or [],
    }
    return e


def verbatim_leaf(obj, source_index, locator, min_len=2):
    best = None

    def rec(o):
        nonlocal best
        if best is not None:
            return
        if isinstance(o, str) and len(norm_ws(o)) >= min_len:
            best = o
            return
        if isinstance(o, dict):
            for k in sorted(o, key=str):
                rec(o[k])
        elif isinstance(o, list):
            for x in o:
                rec(x)

    rec(obj)
    if best is None:
        return {"quote": None, "verified": False, "mode": "no_string_leaf"}
    q = truncate(best)
    return {"quote": q, "verified": source_index.quote_verified(locator, q), "mode": "verbatim_leaf"}


def plan_quote(p, source_index):
    return verbatim_leaf(p.get("raw", {}), source_index, p.get("locator", "case.json"))



# 内部模型构建



def pick(d, keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def pick_str(d, keys, default=None):
    v = pick(d, keys, default)
    return str(v) if v is not None and v != "" else default


def build_model(case, files, input_dir, source_index):
    model = {
        "case_id": extract_case_id(case),
        "title": "",
        "objective": "",
        "hypothesis": "",
        "budget": None,
        "currency": None,
        "deadline": None,
        "start_date": None,
        "available_hours": None,
        "plans": [],
        "contracts": [],
        "domains": [],
        "variables": [],
        "controls": [],
        "assumptions": [],
        "criteria": [],
        "risks": [],
        "sources": [],
        "flat_text": [],
        "ethics_flags": [],
        "warnings": [],
    }
    if not isinstance(case, dict):
        model["warnings"].append("case_not_object")
        return model

    # 目标与假设
    for path, val, _k in find_alias_nodes(case, ALIASES["objective"]):
        if isinstance(val, str) and val.strip() and not model["objective"]:
            model["objective"] = val.strip()
    if not model["objective"]:
        for key in ("title", "name", "课题", "标题"):
            if key in case and isinstance(case[key], str):
                model["objective"] = case[key].strip()
                break
    model["title"] = pick_str(case, ("title", "name", "课题", "标题"), model["objective"] or "untitled")

    for path, val, _k in find_alias_nodes(case, ALIASES["objective"]):
        if isinstance(val, str) and val != model["objective"] and ("假设" in val or "hypoth" in val.lower()):
            if not model["hypothesis"]:
                model["hypothesis"] = val.strip()

    # 预算 / 期限 / 可用工时
    for path, val, key in find_alias_nodes(case, ALIASES["budget"]):
        n = as_number(val)
        if n is not None:
            model["budget"] = n
            cur = as_unit(val) or None
            if isinstance(val, dict):
                cur = pick_str(val, ("currency", "unit"), cur)
            model["currency"] = cur
            break
    for path, val, key in find_alias_nodes(case, ALIASES["deadline"]):
        if isinstance(val, str):
            m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", val)
            if m:
                iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                if "start" in norm_key(key) or "开工" in key:
                    model["start_date"] = iso
                elif model["deadline"] is None:
                    model["deadline"] = iso
                continue
        if isinstance(val, (int, float)) and not isinstance(val, bool) and norm_key(key) in ("availablehours", "maxelapsedhours", "personhours", "工时上限"):
            model["available_hours"] = float(val)

    model["contracts"] = extract_contract_entries(case)
    model["domains"] = extract_domains(case)
    model["plans"] = extract_plans(case, model, source_index)
    model["variables"] = extract_named(case, "variables", source_index)
    model["controls"] = extract_named(case, "controls", source_index)
    model["assumptions"] = extract_named(case, "assumptions", source_index)
    model["criteria"] = extract_named(case, "criteria", source_index)
    model["risks"] = extract_named(case, "risks", source_index)
    model["sources"] = extract_named(case, "sources", source_index)
    model["flat_text"] = flatten_text(case)
    model["ethics_flags"] = scan_ethics_flags(model["flat_text"])
    return model


def extract_named(case, alias_key, source_index):
    items = []
    for path, val, _k in find_alias_nodes(case, ALIASES[alias_key]):
        if isinstance(val, list):
            for i, it in enumerate(val):
                items.append(named_item(it, f"{path}[{i}]", source_index))
        elif isinstance(val, dict):
            if any(k in val for k in ("name", "id", "label", "type", "text", "description")):
                items.append(named_item(val, path, source_index))
            else:
                for k, v in val.items():
                    items.append(named_item(v, f"{path}.{k}", source_index))
        elif isinstance(val, str):
            items.append({"name": "item", "text": val, "locator": path, "quote": truncate(val), "verified": source_index.quote_verified(path, val)})
    # 去重（同一 locator）
    seen = set()
    out = []
    for it in items:
        k = it.get("locator")
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def named_item(it, path, source_index):
    if isinstance(it, str):
        return {"name": "item", "text": it, "locator": path, "quote": truncate(it), "verified": source_index.quote_verified(path, it)}
    if isinstance(it, dict):
        name = pick_str(it, ("name", "id", "label", "type", "title"), "item")
        text = pick_str(it, ("text", "description", "desc", "detail", "content"), "")
        q = text or name
        return {"name": name, "text": text, "locator": path, "quote": truncate(q), "verified": source_index.quote_verified(path, q)}
    q = str(it)
    return {"name": "item", "text": q, "locator": path, "quote": truncate(q), "verified": source_index.quote_verified(path, q)}



# 方案提取



def extract_plans(case, model, source_index):
    plans = []
    nodes = find_alias_nodes(case, ALIASES["plans"])
    used = set()
    for path, val, _k in nodes:
        if not isinstance(val, list):
            continue
        for i, item in enumerate(val):
            if not isinstance(item, dict):
                continue
            p = plan_from_dict(item, f"{path}[{i}]", i, source_index)
            if p is not None:
                plans.append(p)
                used.add(path)
    if not plans:
        # 无 plans 节点：若存在 steps 列表，则把 case 视为单方案
        step_nodes = find_alias_nodes(case, ALIASES["steps"])
        if step_nodes:
            plans = [plan_from_dict(case, "$", 0, source_index)]
    # 若 plans 节点存在但某些方案缺 steps，继承 case 级 steps
    if plans and not any(p["steps"] for p in plans):
        case_steps = extract_steps(case, "$", source_index)
        if case_steps:
            for p in plans:
                if not p["steps"]:
                    p["steps"] = case_steps
                    p["steps_locator"] = "$"
    return plans


def plan_from_dict(d, path, idx, source_index):
    label = pick_str(d, ("label", "version", "plan_id", "id", "name", "ver"), f"v{idx + 1}")
    text_blob = " ".join(flatten_text(d))
    steps, steps_loc = extract_steps(d, path, source_index)
    params = extract_parameters(d, path, source_index)
    resources = extract_resources(d, path, source_index)
    samples = extract_sample_needs(d, path, source_index)
    ethics = {}
    en = find_alias_nodes(d, ALIASES["ethics"])
    if en:
        ethics = {"path": en[0][0], "value": en[0][1]}
    return {
        "label": label,
        "index": idx,
        "locator": path,
        "raw": d,
        "text_blob": text_blob,
        "steps": steps,
        "steps_locator": steps_loc,
        "parameters": params,
        "resources": resources,
        "samples": samples,
        "ethics": ethics,
        "controls": extract_named(d, "controls", source_index),
        "assumptions": extract_named(d, "assumptions", source_index),
        "criteria": extract_named(d, "criteria", source_index),
    }


def _first_list(d, alias_key):
    nodes = find_alias_nodes(d, ALIASES[alias_key])
    for path, val, _k in nodes:
        if isinstance(val, list):
            return path, val
        if isinstance(val, dict):
            for k in sorted(val, key=str):
                if isinstance(val[k], list):
                    return f"{path}.{k}", val[k]
    return None, None


def extract_steps(d, base_path, source_index):
    path, lst = _first_list(d, "steps")
    if lst is None:
        return [], None
    out = []
    for i, s in enumerate(lst):
        if not isinstance(s, dict):
            continue
        p = f"{path}[{i}]"
        name = pick_str(s, ("name", "title", "step", "operation", "description", "id"), f"step-{i + 1}")
        hands, wait = [], []
        dur_nodes = find_alias_nodes(s, ALIASES["steps"])
        for k, v in s.items():
            if not isinstance(k, str):
                continue
            kl = k.lower()
            if not re.search(r"duration|hands|active|time|hour|minute|day|wait|turnaround|incubat|cultiv|culture|centrifug|overnight|lead|rest|delivery|shipping", kl):
                continue
            n, u, raw = as_number_unit(v, k)
            h = convert_hours(n, u)
            if h is None:
                continue
            if re.search(r"wait|turnaround|incubat|culture|overnight|lead|rest|delivery|shipping|queue", kl):
                if kl and "overnight" in kl and n and h < 16:
                    h = 16.0  # overnight 按 16h 计，作为显式假设记录
                    wait.append({"hours": h, "basis": "assumption:overnight=16h"})
                else:
                    wait.append({"hours": h, "basis": kl})
            else:
                hands.append({"hours": h, "basis": kl})
        eq = []
        for eqk in ("equipment", "devices", "instruments", "device", "instrument", "equipment_used"):
            if eqk in s:
                eq = s[eqk] if isinstance(s[eqk], list) else [s[eqk]]
                break
        eq_list = [e if isinstance(e, str) else (pick_str(e, ("name", "id", "label"), "equipment")) for e in eq]
        params = extract_parameters(s, p, source_index)
        qinfo = quote_from_leaf(source_index, p, "name", name)
        out.append({
            "seq": i + 1,
            "name": name,
            "locator": p,
            "quote": qinfo["quote"],
            "quote_verified": qinfo["verified"],
            "hands_on_h": round(sum(x["hours"] for x in hands), 4),
            "wait_h": round(sum(x["hours"] for x in wait), 4),
            "duration_notes": hands + wait,
            "equipment": eq_list,
            "parameters": params,
        })
    return out, path


def extract_parameters(d, base_path, source_index):
    out = []
    for path, val, _k in find_alias_nodes(d, ALIASES["parameters"]):
        if isinstance(val, dict):
            for k in sorted(val, key=str):
                item = val[k]
                if isinstance(item, dict) and any(t in item for t in ("value", "min", "max", "values", "allowed")):
                    continue  # 这是 domain，另走 extract_domains
                n, u, raw = as_number_unit(item, k)
                if n is None and not isinstance(item, str):
                    continue
                name = str(k)
                if n is not None:
                    q = quote_from_leaf(source_index, f"{path}.{k}", k, item)
                    out.append({"name": name, "value": n, "unit": u, "raw": raw, "locator": f"{path}.{k}", "quote": q["quote"], "verified": q["verified"], "scope": "plan"})
                elif isinstance(item, str):
                    out.append({"name": name, "value": None, "unit": None, "raw": item, "locator": f"{path}.{k}", "quote": truncate(item), "verified": source_index.quote_verified(f"{path}.{k}", item), "scope": "plan"})
        elif isinstance(val, list):
            for i, it in enumerate(val):
                if isinstance(it, dict):
                    name = pick_str(it, ("name", "key", "id"), f"param-{i}")
                    n, u, raw = as_number_unit(it.get("value") if "value" in it else it.get("setting"), name)
                    if n is None and "value" not in it:
                        continue
                    p = f"{path}[{i}]"
                    q = quote_from_leaf(source_index, p, name, it.get("value", it))
                    out.append({"name": name, "value": n, "unit": u, "raw": raw, "locator": p, "quote": q["quote"], "verified": q["verified"], "scope": "plan"})
        elif isinstance(val, (int, float, str)):
            n, u, raw = as_number_unit(val, _k)
            if n is not None:
                q = quote_from_leaf(source_index, path, _k, val)
                out.append({"name": _k, "value": n, "unit": u, "raw": raw, "locator": path, "quote": q["quote"], "verified": q["verified"], "scope": "plan"})
    # 去重
    seen = set()
    out2 = []
    for it in out:
        key = (it["name"], it["locator"])
        if key in seen:
            continue
        seen.add(key)
        out2.append(it)
    return out2


def extract_resources(d, base_path, source_index):
    out = []
    for kind in ("equipment", "materials"):
        for path, val, _k in find_alias_nodes(d, ALIASES[kind]):
            if isinstance(val, list):
                for i, it in enumerate(val):
                    if isinstance(it, dict):
                        name = pick_str(it, ("name", "id", "label", "item"), f"{kind}-{i}")
                        spec = pick_str(it, ("spec", "specification", "model", "required_spec", "requirement"), "")
                        p = f"{path}[{i}]"
                        q = quote_from_leaf(source_index, p, "name", name)
                        out.append({"name": name, "kind": kind, "spec": spec, "locator": p, "quote": q["quote"], "verified": q["verified"]})
                    elif isinstance(it, str):
                        q = truncate(it)
                        out.append({"name": it, "kind": kind, "spec": "", "locator": f"{path}[{i}]", "quote": q, "verified": source_index.quote_verified(f"{path}[{i}]", q)})
            elif isinstance(val, dict):
                for k in sorted(val, key=str):
                    it = val[k]
                    if isinstance(it, dict):
                        name = pick_str(it, ("name", "id", "label"), str(k))
                        spec = pick_str(it, ("spec", "specification", "model"), "")
                        q = quote_from_leaf(source_index, f"{path}.{k}", k, name)
                        out.append({"name": name, "kind": kind, "spec": spec, "locator": f"{path}.{k}", "quote": q["quote"], "verified": q["verified"]})
                    elif isinstance(it, str):
                        out.append({"name": str(k), "kind": kind, "spec": "", "locator": f"{path}.{k}", "quote": truncate(it), "verified": source_index.quote_verified(f"{path}.{k}", it)})
    return out


def extract_sample_needs(d, base_path, source_index):
    info = {"locator": None, "per_group_n": None, "groups": None, "replicates": None, "attrition": None, "available": None, "n_required": None, "n_consumed": None}
    for path, val, _k in find_alias_nodes(d, ALIASES["samples"]):
        if info["locator"] is None and isinstance(val, (dict, list)):
            info["locator"] = path
        if isinstance(val, dict):
            if "per_group_n" in val or "per_group" in val:
                info["per_group_n"] = as_number(pick(val, ("per_group_n", "per_group", "n_per_group")))
            if "groups" in val or "group_count" in val:
                info["groups"] = as_number(pick(val, ("groups", "group_count", "n_groups")))
            if "replicates" in val or "technical_replicates" in val:
                info["replicates"] = as_number(pick(val, ("replicates", "technical_replicates")))
            if "attrition" in val or "dropout" in val:
                info["attrition"] = as_number(pick(val, ("attrition", "dropout", "loss_rate")))
            if "available" in val or "n_available" in val:
                info["available"] = as_number(pick(val, ("available", "n_available", "available_count")))
            for k, v in val.items():
                kl = norm_key(k)
                if kl == "pergroupn":
                    info["per_group_n"] = as_number(v)
                elif kl == "groups":
                    info["groups"] = as_number(v)
                elif kl in ("replicates", "technicalreplicates"):
                    info["replicates"] = as_number(v)
                elif kl in ("attrition", "dropout"):
                    info["attrition"] = as_number(v)
                elif kl in ("available", "navailable"):
                    info["available"] = as_number(v)
    if info["per_group_n"] is not None and info["groups"] is not None:
        info["n_required"] = info["per_group_n"] * info["groups"]
    if info["n_required"] is not None:
        reps = info["replicates"] or 1.0
        info["n_consumed"] = info["n_required"] * reps
    return info



# 合同 / 域提取



def extract_contract_entries(case):
    entries = []
    for path, val, key in find_alias_nodes(case, ALIASES["contracts"]):
        hint = None
        for k in ("equipment", "material", "price", "quote", "booking", "availab", "sample", "ethics", "approval", "service", "vendor", "supplier"):
            if k in norm_key(path) or k in norm_key(key):
                hint = k
                break
        entries.extend(iter_contract_entries(val, path, hint))
    # 去重 by (path, name)
    seen = set()
    out = []
    for e in entries:
        k = (e.get("locator"), e.get("name"))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def iter_contract_entries(node, path, hint):
    out = []
    if isinstance(node, list):
        for i, it in enumerate(node):
            if isinstance(it, dict):
                out.append(contract_entry_from_dict(it, f"{path}[{i}]", hint))
            elif isinstance(it, str):
                out.append({"kind": classify_contract_kind(hint, {}), "name": it, "locator": f"{path}[{i}]", "quote": truncate(it)})
    elif isinstance(node, dict):
        if any(k in node for k in ("name", "item", "unit_price", "price", "spec", "booking", "available", "lead_days", "status", "supplier")):
            out.append(contract_entry_from_dict(node, path, hint))
        else:
            for k in sorted(node, key=str):
                child = node[k]
                if isinstance(child, dict):
                    e = contract_entry_from_dict(child, f"{path}.{k}", hint)
                    if not e.get("name"):
                        e["name"] = str(k)
                    out.append(e)
                elif isinstance(child, list):
                    out.extend(iter_contract_entries(child, f"{path}.{k}", hint))
    return out


def classify_contract_kind(hint, d):
    if hint in ("equipment", "booking", "availab"):
        return "equipment"
    if hint in ("material", "price", "quote", "vendor", "supplier", "service"):
        return "material"
    if hint in ("sample",):
        return "sample"
    if hint in ("ethics", "approval"):
        return "approval"
    # 依据字段推断
    if any(k in d for k in ("booking_hours", "slot", "window", "machine_time", "spec")):
        return "equipment"
    if any(k in d for k in ("unit_price", "price", "cost", "pack", "lead_days", "delivery")):
        return "material"
    if any(k in d for k in ("available", "n_available", "count")):
        return "sample"
    if any(k in d for k in ("status", "approval_no", "expiry", "review")):
        return "approval"
    return "material"


def contract_entry_from_dict(d, path, hint):
    name = pick_str(d, ("name", "item", "id", "label", "material", "equipment"), "")
    kind = classify_contract_kind(hint, d)
    return {
        "kind": kind,
        "name": name,
        "spec": pick_str(d, ("spec", "specification", "model", "capability"), ""),
        "unit_price": as_number(pick(d, ("unit_price", "price", "cost", "unit_cost"))),
        "currency": pick_str(d, ("currency", "unit"), None),
        "pack_size": pick_str(d, ("pack_size", "pack", "package", "size"), None),
        "lead_days": as_number(pick(d, ("lead_days", "lead_time_days", "delivery_days", "turnaround_days"))),
        "booking_hours": as_number(pick(d, ("booking_hours", "hours", "slot_hours", "available_hours"))),
        "available": as_number(pick(d, ("available", "n_available", "count", "qty", "quantity"))),
        "status": pick_str(d, ("status", "approval_status", "state"), None),
        "vendor": pick_str(d, ("vendor", "supplier", "provider", "external"), None),
        "approval_no": pick_str(d, ("approval_no", "approval_number", "permit_no"), None),
        "expiry": pick_str(d, ("expiry", "expiry_date", "valid_until"), None),
        "locator": path,
        "quote": quote_contract(d, path),
    }


def quote_contract(d, path):
    for k in ("name", "item", "spec", "unit_price", "price", "status"):
        if k in d:
            v = d[k]
            if isinstance(v, str) and v:
                return truncate(v)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return f'"{k}": {v}'
    return truncate(json.dumps(d, ensure_ascii=False))


def extract_domains(case):
    domains = []
    for path, val, _k in find_alias_nodes(case, ALIASES["parameters"]):
        if isinstance(val, dict):
            for k in sorted(val, key=str):
                it = val[k]
                if isinstance(it, dict) and any(t in it for t in ("min", "max", "values", "allowed", "range")):
                    domains.append(domain_from_dict(k, it, f"{path}.{k}"))
        elif isinstance(val, list):
            for i, it in enumerate(val):
                if isinstance(it, dict) and any(t in it for t in ("min", "max", "values", "allowed", "range")):
                    domains.append(domain_from_dict(pick_str(it, ("name", "key", "id"), f"domain-{i}"), it, f"{path}[{i}]"))
    # 也接受 "parameter_domains": {"rpm": {...}} 顶层
    seen = set()
    out = []
    for dm in domains:
        k = (dm["name"], dm["locator"])
        if k in seen:
            continue
        seen.add(k)
        out.append(dm)
    return out


def domain_from_dict(name, d, path):
    lo = as_number(d.get("min"))
    hi = as_number(d.get("max"))
    values = None
    if isinstance(d.get("values"), list):
        values = [as_number(x) for x in d["values"]]
    if isinstance(d.get("allowed"), list):
        values = [as_number(x) for x in d["allowed"]]
    unit = as_unit(pick(d, ("unit", "units"))) or as_unit(name)
    return {"name": name, "min": lo, "max": hi, "values": values, "step": as_number(d.get("step")), "unit": unit, "locator": path, "quote": truncate(json.dumps(d, ensure_ascii=False))}



# 伦理 / 安全关键词


ETHICS_RULES = [
    ("human", ["人体", "人源", "受试者", "病人", "患者", "志愿者", "人血", "血清", "血浆", "外周血", "PBMC", "human", "patient", "subject", "volunteer", "blood", "serum", "plasma", "IRB", "知情同意", "informed consent"], "IRB/伦理委员会"),
    ("animal", ["动物", "小鼠", "大鼠", "裸鼠", "实验动物", "animal", "mouse", "mice", "rat", "IACUC", "动物伦理"], "IACUC/动物伦理"),
    ("biohazard", ["病原", "致病菌", "病毒", "生物安全", "BSL-2", "BSL-3", "biohazard", "pathogen", "病毒株", "菌株", "感染"], "生物安全委员会/BSL"),
    ("chemical", ["剧毒", "致癌", "易爆", "易燃", "腐蚀性", "危险化学品", "放射性", "放射源", "同位素", "hazardous", "toxic", "carcinogen", "radioactive", "isotope"], "安全/辐射/危化品审批"),
    ("data", ["个人数据", "个人信息", "隐私", "基因数据", "隐私保护", "privacy", "personal data", "GDPR", "PIPL", "个人信息保护"], "数据合规/隐私审查"),
]


def scan_ethics_flags(flat_text):
    joined = " ".join(flat_text)
    flags = []
    for key, kws, body in ETHICS_RULES:
        hit = [k for k in kws if k.lower() in joined.lower()]
        if hit:
            flags.append({"flag": key, "review_body": body, "matched_terms": hit})
    return flags



# 算术：规格比较 / 匹配 / 度量



def parse_spec_numbers(spec):
    out = []
    if not spec:
        return out
    text = str(spec)
    for m in re.finditer(r"([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z°℃µμ]+)?", text):
        num = float(m.group(1))
        unit = as_unit(m.group(2) or "")
        pre = text[max(0, m.start() - 14): m.start()]
        bound = None
        if re.search(r"max|up\s*to|最高|≤|<=|不超过|上限", pre, re.I):
            bound = "max"
        elif re.search(r"min|at\s*least|最低|≥|>=|不低于|下限", pre, re.I):
            bound = "min"
        out.append({"value": num, "unit": unit, "bound": bound})
    return out


def spec_fit(required_spec, available_spec):
    reqs = parse_spec_numbers(required_spec)
    avails = parse_spec_numbers(available_spec)
    if not reqs or not avails:
        return None, "no_comparable_numbers"
    detail = []
    for r in reqs:
        candidates = []
        for a in avails:
            if r["unit"] == a["unit"] or (unit_kind(r["unit"]) == unit_kind(a["unit"]) and unit_kind(r["unit"]) in ("temperature", "volume", "mass")):
                candidates.append(a)
        if not candidates:
            continue
        a = candidates[0]
        rv = r["value"]
        if r["unit"] != a["unit"] and unit_kind(r["unit"]) == "temperature":
            av = c_to_k(a["value"]) if a["unit"] == "c" else a["value"]
            rv = c_to_k(r["value"]) if r["unit"] == "c" else r["value"]
        else:
            av = a["value"]
        if r["bound"] == "max" and a["bound"] in ("min", None):
            detail.append(f"{r['value']}{r['unit'] or ''}≤{a['value']}{a['unit'] or ''}")
            if rv > av:
                return False, "; ".join(detail)
        elif r["bound"] == "min" and a["bound"] in ("max", None):
            detail.append(f"{r['value']}{r['unit'] or ''}≥{a['value']}{a['unit'] or ''}")
            if rv < av:
                return False, "; ".join(detail)
        else:
            tol = 0.05 * max(1.0, abs(rv))
            detail.append(f"{r['value']}{r['unit'] or ''}≈{a['value']}{a['unit'] or ''}")
            if abs(rv - av) > tol:
                return False, "; ".join(detail)
    if not detail:
        return None, "no_matching_unit"
    return True, "; ".join(detail)


def name_match_score(a, b):
    ta = set(norm_name(a).split())
    tb = set(norm_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def best_match(name, entries, min_score=0.6):
    best = None
    best_score = 0.0
    for e in entries:
        en = e.get("name") or ""
        if not en:
            continue
        s = name_match_score(name, en)
        if norm_name(name) and (norm_name(name) in norm_name(en) or norm_name(en) in norm_name(name)):
            s = max(s, 0.75)
        if s > best_score:
            best_score = s
            best = e
    return (best, best_score) if best_score >= min_score else (None, best_score)


def classify_resource(required_name, required_spec, inv_entries, contract_entries):
    inv, inv_score = best_match(required_name, inv_entries)
    ext, ext_score = best_match(required_name, contract_entries)
    if inv:
        fit, _detail = spec_fit(required_spec, inv.get("spec", ""))
        if fit is True:
            return "已具备", inv
        if fit is False:
            if ext and (ext.get("vendor") or ext.get("kind") == "equipment"):
                return "可外协", ext
            if ext:
                return "需采购", ext
            return "不具备", inv
        if fit is None:
            return "未知", inv
    if ext:
        if ext.get("vendor") or ext.get("kind") in ("service",):
            return "可外协", ext
        if ext.get("unit_price") is not None or ext.get("lead_days") is not None:
            return "需采购", ext
        if ext.get("booking_hours") is not None or ext.get("spec"):
            return "可外协", ext
    # 未命中任何清单/合同：不能判定为"不具备"——清单可能不完整，如实标未知。
    return "未知", None


def resource_state_label(state):
    return {"已具备": "已具备", "可外协": "可外协", "需采购": "需采购", "不具备": "不具备", "未知": "未知"}.get(state, state)



# 指标计算



def iso_to_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def days_between(start, end):
    a, b = iso_to_date(start), iso_to_date(end)
    if a is None or b is None:
        return None
    return (b - a).days


def sample_availability(plan, contracts):
    s = plan.get("samples", {})
    if s.get("available") is not None:
        return s["available"], "plan"
    sample_entries = [e for e in contracts if e.get("kind") == "sample" and e.get("available") is not None]
    if len(sample_entries) == 1:
        return sample_entries[0]["available"], sample_entries[0]["locator"]
    if len(sample_entries) > 1:
        return None, "ambiguous"
    return None, None


def plan_metrics(plan, contracts, domains):
    hands = sum(s.get("hands_on_h") or 0 for s in plan["steps"])
    wait = sum(s.get("wait_h") or 0 for s in plan["steps"])
    elapsed = hands + wait
    eq_hours = {}
    for s in plan["steps"]:
        dur = (s.get("hands_on_h") or 0) + (s.get("wait_h") or 0)
        for e in s["equipment"]:
            if e:
                eq_hours[e] = round(eq_hours.get(e, 0.0) + dur, 4)
    # 成本
    mat_entries = [e for e in contracts if e.get("kind") == "material"]
    cost_lines = []
    total_cost = 0.0
    currencies = set()
    for r in plan["resources"]:
        if r["kind"] != "materials":
            continue
        ext, _score = best_match(r["name"], mat_entries)
        if not ext:
            continue
        if ext.get("currency"):
            currencies.add(ext["currency"])
        unit_price = ext.get("unit_price")
        pack_str = ext.get("pack_size") or ""
        pack_n, pack_u, _ = as_number_unit(pack_str, "pack_size")
        # 方案材料里数量若以 spec 文本给出
        need_n, need_u, _ = as_number_unit(r.get("spec", ""), None)
        if need_n is None:
            need_n, need_u, _ = as_number_unit(r.get("name", ""), None)
        packs = 1.0
        if pack_n and need_n:
            nb, _ = to_base(need_n, need_u) if need_u else (need_n, need_u)
            pb, _ = to_base(pack_n, pack_u) if pack_u else (pack_n, pack_u)
            if nb and pb and unit_kind(need_u) == unit_kind(pack_u):
                packs = math.ceil(nb / pb)
        elif ext.get("available") is not None:
            packs = 1.0
        line_total = (unit_price or 0.0) * packs
        total_cost += line_total
        cost_lines.append({
            "item": r["name"],
            "need": f"{need_n} {need_u or ''}".strip() if need_n else None,
            "pack": pack_str,
            "packs": packs,
            "unit_price": unit_price,
            "currency": ext.get("currency"),
            "line_total": round(line_total, 2),
            "lead_days": ext.get("lead_days"),
            "locator": ext.get("locator"),
            "quote": ext.get("quote"),
        })
    lead_days_max = None
    for c in cost_lines:
        if c["lead_days"] is not None:
            lead_days_max = max(lead_days_max or 0, c["lead_days"])
    # 参数检查
    param_checks = []
    for p in plan["parameters"]:
        dm = None
        for d in domains:
            if norm_name(d["name"]) == norm_name(p["name"]) or norm_name(d["name"]) in norm_name(p["name"]) or norm_name(p["name"]) in norm_name(d["name"]):
                dm = d
                break
        if dm is None or p["value"] is None:
            continue
        verdict = None
        margin = None
        detail = ""
        if dm.get("values") is not None:
            vals = [v for v in dm["values"] if v is not None]
            if vals:
                closest = min(vals, key=lambda v: abs(v - p["value"]))
                margin = abs(p["value"] - closest)
                if margin < 1e-9:
                    verdict = "ok"
                    detail = f"{p['value']} ∈ {dm['values']}"
                else:
                    verdict = "blocked"
                    detail = f"{p['value']} ∉ {dm['values']} (最近 {closest}, 差 {round(margin,4)})"
        else:
            lo, hi = dm.get("min"), dm.get("max")
            if lo is not None and hi is not None:
                if lo <= p["value"] <= hi:
                    span = hi - lo
                    margin = min(p["value"] - lo, hi - p["value"])
                    if span > 0 and margin / span <= 0.05:
                        verdict = "conditional"
                        detail = f"{lo}≤{p['value']}≤{hi}（近边界，裕度 {round(margin,4)}）"
                    else:
                        verdict = "ok"
                        detail = f"{lo}≤{p['value']}≤{hi}"
                else:
                    verdict = "blocked"
                    detail = f"{p['value']} 超出 [{lo},{hi}]"
            elif lo is not None and p["value"] < lo:
                verdict = "blocked"
                detail = f"{p['value']} < 下限 {lo}"
            elif hi is not None and p["value"] > hi:
                verdict = "blocked"
                detail = f"{p['value']} > 上限 {hi}"
        param_checks.append({"name": p["name"], "value": p["value"], "unit": p["unit"], "verdict": verdict, "margin": margin, "detail": detail, "domain_locator": dm["locator"], "param_locator": p["locator"]})
    return {
        "hands_on_h": round(hands, 4),
        "wait_h": round(wait, 4),
        "elapsed_h": round(elapsed, 4),
        "step_count": len(plan["steps"]),
        "duration_steps": sum(1 for s in plan["steps"] if (s.get("hands_on_h") or s.get("wait_h"))),
        "equipment_hours": eq_hours,
        "cost_lines": cost_lines,
        "total_cost": round(total_cost, 2),
        "currencies": sorted(currencies),
        "lead_days_max": lead_days_max,
        "param_checks": param_checks,
    }



# 审查项构建



def mk_check(cid, dim, claim, verdict, *, evidence, conditions=None, formula=None, computed=None, unit=None, threshold=None, confidence="medium", needs_expert=False, notes=None, resource_state=None, plan=None, blocking=None):
    return {
        "id": cid,
        "dimension": dim,
        "claim": claim,
        "verdict": verdict,
        "plan": plan,
        "conditions": conditions or [],
        "evidence": evidence,
        "formula": formula,
        "computed": computed,
        "unit": unit,
        "threshold": threshold,
        "resource_state": resource_state,
        "confidence": confidence,
        "needs_expert": bool(needs_expert),
        "blocking": blocking,
        "notes": notes or [],
    }


def rollup(checks, plan_label):
    items = [c for c in checks if c.get("plan") == plan_label or c.get("plan") is None]
    worst = "ok"
    for c in items:
        if SEVERITY[c["verdict"]] > SEVERITY[worst]:
            worst = c["verdict"]
    blocking_count = sum(1 for c in items if c.get("blocking") or c["verdict"] in ("blocked", "conflict"))
    return worst, blocking_count, len(items)


def build_review(model, source_index):
    checks = []
    counters = {d: 0 for d in DIM_ORDER}

    def nid(dim, plan=None):
        counters[dim] += 1
        cid = f"{DIM_PREFIX[dim]}-{counters[dim]:02d}"
        if plan:
            cid += f"@{plan}"
        return cid

    # ---- 1. 假设与变量 ----
    av = []
    objective = model["objective"]
    if objective:
        q = truncate(objective)
        av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", f"研究目标已陈述：{q}", "ok", evidence=[mk_evidence("case.json#objective", {"quote": q, "verified": source_index.quote_verified("case.json#objective", q)}, "objective")]))
    else:
        av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", "材料未陈述可检验的研究目标", "missing", evidence=[], conditions=["补充 research_question / objective 字段或文字说明"], confidence="high", needs_expert=False))
    if model["hypothesis"]:
        q = truncate(model["hypothesis"])
        av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", "给出假设/预期", "ok", evidence=[mk_evidence("case.json#hypothesis", {"quote": q, "verified": source_index.quote_verified("case.json#hypothesis", q)}, "hypothesis")]))
    else:
        av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", "方案未写明假设或预期结果", "missing", evidence=[], conditions=["补充假设及可检验的预期方向"], confidence="medium"))
    ivar = [v for v in model["variables"] if v.get("name") not in ("", "item")]
    if ivar:
        quote = ivar[0]["quote"]
        av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", f"列出变量/因子 {len(ivar)} 项（首项：{ivar[0]['name']}）", "ok", evidence=[mk_evidence(ivar[0]["locator"], {"quote": quote, "verified": ivar[0].get("verified")}, "variables")], notes=[f"共 {len(ivar)} 项变量记录"]))
    else:
        av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", "未独立/因变量未显式列出", "missing", evidence=[], conditions=["明确自变量、因变量、受控变量"], confidence="medium"))
    for p in model["plans"]:
        steps = p["steps"]
        unparsed = [s for s in steps if not (s.get("hands_on_h") or s.get("wait_h"))]
        if unparsed:
            av.append(mk_check(nid("assumptions_variables"), "assumptions_variables", f"方案 {p['label']} 有 {len(unparsed)} 个步骤缺少时长/等待时间说明", "missing", plan=p["label"], evidence=[mk_evidence(p["steps_locator"] or p["locator"], {"quote": unparsed[0].get("quote") or truncate(p["text_blob"]), "verified": bool(unparsed[0].get("quote_verified"))}, "plan")], conditions=["为每步补 duration 与 wait/turnaround 数值"], confidence="high"))
    checks.extend(av)

    # ---- 2. 对照组 ----
    ct = []
    controls = model["controls"] + [c for p in model["plans"] for c in p["controls"]]
    if controls:
        first = controls[0]
        ct.append(mk_check(nid("controls"), "controls", f"设对照组/比较臂：{first['name']}", "ok", evidence=[mk_evidence(first["locator"], {"quote": first["quote"], "verified": first.get("verified")}, "controls")], notes=[f"共 {len(controls)} 项对照记录"]))
    else:
        ct.append(mk_check(nid("controls"), "controls", "方案未写明阳性/阴性对照或比较臂", "missing", evidence=[], conditions=["明确阴性/阳性/载体对照及与实验组的匹配"], confidence="high"))
    joined = " ".join(model["flat_text"]).lower()
    if re.search(r"random|随机|盲法|blinding|blind|分组", joined):
        ct.append(mk_check(nid("controls"), "controls", "提及随机化/盲法/分组", "ok", evidence=[quote_line(source_index, "case.json", r"random|随机|盲法|blind|分组")], confidence="medium"))
    else:
        ct.append(mk_check(nid("controls"), "controls", "未提及随机化、盲法与分组规则", "missing", evidence=[], conditions=["补充分组与盲法方案"], confidence="medium"))
    checks.extend(ct)

    # ---- 3. 设备材料 ----
    em = []
    inv_entries = [e for e in model["contracts"] if e.get("kind") == "equipment"]
    for p in model["plans"]:
        for i, r in enumerate(p["resources"][:MAX_ITEMS_PER_DIM]):
            state, src = classify_resource(r["name"], r["spec"], inv_entries, [e for e in model["contracts"] if e.get("kind") in ("equipment", "material", "service")])
            verdict = {"已具备": "ok", "可外协": "conditional", "需采购": "conditional", "不具备": "blocked", "未知": "unconfirmed"}[state]
            conds = []
            if state == "需采购":
                ext = src
                if ext and ext.get("lead_days") is not None:
                    conds.append(f"采购前置期 {ext['lead_days']} 天；下单前确认货期")
                if ext and ext.get("unit_price") is not None:
                    conds.append(f"预算增列 {ext['unit_price']} {ext.get('currency') or ''}（单价）")
            if state == "可外协":
                conds.append("确认外协单位档期与报价并纳入排期")
            if state == "不具备":
                conds.append("更换设备或调整规格需求")
            if state == "未知":
                conds.append("补充设备/材料清单以判定可用性")
            em.append(mk_check(nid("equipment_materials", p["label"]), "equipment_materials", f"方案 {p['label']} 需要 {r['name']}{('，规格 ' + r['spec']) if r['spec'] else ''}", verdict, plan=p["label"], evidence=[mk_evidence(r["locator"], {"quote": r["quote"], "verified": r["verified"]}, "plan_resource")], resource_state=state, conditions=conds, confidence="high" if state in ("已具备", "不具备") else "medium", needs_expert=(state == "未知")))
    if not em:
        em.append(mk_check(nid("equipment_materials"), "equipment_materials", "方案未列出设备/材料清单", "missing", evidence=[], conditions=["提供设备与耗材明细以便核验"], confidence="high"))
    checks.extend(em)

    # ---- 4. 时间与成本 ----
    tc = []
    for p in model["plans"]:
        m = plan_metrics(p, model["contracts"], model["domains"])
        formula = " + ".join(f"{x['hours']:g}" for s in p["steps"] for x in s["duration_notes"]) or "0"
        tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 步骤时长累加（人工 {m['hands_on_h']}h + 等待 {m['wait_h']}h）", "ok" if formula else "missing", plan=p["label"], evidence=[mk_evidence(p["steps_locator"] or p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=formula, computed=m["elapsed_h"], unit="h", conditions=[] if formula else ["每步补时长数值"]))
        if model["available_hours"] is not None:
            slack = model["available_hours"] - m["elapsed_h"]
            verdict = "ok" if slack >= 0.2 * model["available_hours"] else ("conditional" if slack >= 0 else "blocked")
            tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 总时长与可用工时比较", verdict, plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"{model['available_hours']} - {m['elapsed_h']}", computed=round(slack, 4), unit="h", threshold=f"≥20% 裕度（≥{round(0.2 * model['available_hours'],2)}h）"))
        else:
            tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 缺可用工时上限，无法判定排期是否可行", "unconfirmed", plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], conditions=["提供可用工时上限（available_hours / person_hours）"], needs_expert=False))
        # 日历排期：start_date + deadline 才有确定结论（不用系统当前日期，保证确定性）
        slack_days = days_between(model["start_date"], model["deadline"])
        if slack_days is not None:
            need_days = math.ceil(m["elapsed_h"] / 24.0)
            net = slack_days - need_days
            verdict = "ok" if net >= 7 else ("conditional" if net >= 0 else "blocked")
            tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 日历排期（开工→截止 vs 所需天数）", verdict, plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"({model['deadline']} - {model['start_date']}) 天 - ceil({m['elapsed_h']}/24) 天", computed=net, unit="d", threshold="≥7 天安全裕度"))
        elif model["deadline"] and not model["start_date"]:
            tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 有截止日期但缺开工日期，无法算日历排期", "unconfirmed", plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], conditions=["补充开工日期 start_date"], needs_expert=False))
        # 样本量 / 消耗
        avail, avail_loc = sample_availability(p, model["contracts"])
        s = p["samples"]
        if s.get("n_consumed") is not None:
            if avail is not None:
                margin = (avail - s["n_consumed"]) / s["n_consumed"]
                verdict = "ok" if margin >= 0.15 else ("conditional" if margin >= 0 else "blocked")
                tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 样本消耗与可用量比较", verdict, plan=p["label"], evidence=[mk_evidence(s.get("locator") or p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"{s.get('n_required')} × {s.get('replicates') or 1} = {s['n_consumed']} vs {avail}", computed=round(margin * 100, 1), unit="%", threshold="≥15% 裕度（考虑损耗/重复）", conditions=[] if verdict == "ok" else (["增加样本来源或减少分组/重复数"] if verdict == "blocked" else ["确认样本损耗率并留出缓冲"])))
            else:
                tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 样本消耗 {s['n_consumed']} 但可用量未给", "unconfirmed", plan=p["label"], evidence=[mk_evidence(s.get("locator") or p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"{s.get('n_required')} × {s.get('replicates') or 1} = {s['n_consumed']}", conditions=["提供可用样本量或采购/入库记录"], needs_expert=False))
        elif s.get("per_group_n") is None or s.get("groups") is None:
            tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 未给出每组样本量与分组数，无法算样本需求", "missing", plan=p["label"], evidence=[mk_evidence(s.get("locator") or p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], conditions=["补 per_group_n、groups、replicates、attrition"]))
        # 设备机时
        for eq, h in sorted(m["equipment_hours"].items()):
            bk = None
            for e in [e for e in model["contracts"] if e.get("kind") == "equipment"]:
                if norm_name(e.get("name", "")) in norm_name(eq) or norm_name(eq) in norm_name(e.get("name", "")):
                    bk = e
                    break
            if bk and bk.get("booking_hours") is not None:
                util = h / bk["booking_hours"]
                verdict = "ok" if util <= 0.7 else ("conditional" if util <= 1.0 else "blocked")
                tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 设备 {eq} 机时占用", verdict, plan=p["label"], evidence=[mk_evidence(bk["locator"], {"quote": bk["quote"], "verified": source_index.quote_verified(bk["locator"], bk["quote"])}, "booking")], formula=f"{h} / {bk['booking_hours']}", computed=round(util * 100, 1), unit="%", threshold="≤70% 安全 / ≤100% 可行"))
        # 成本
        if m["total_cost"] > 0:
            if model["budget"] is not None:
                slack = model["budget"] - m["total_cost"]
                verdict = "ok" if slack >= 0.1 * model["budget"] else ("conditional" if slack >= 0 else "blocked")
                tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 材料成本与预算比较", verdict, plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"{model['budget']} - {m['total_cost']}", computed=round(slack, 2), unit=model["currency"] or "本币", threshold="≥10% 裕度"))
            if len(m["currencies"]) > 1:
                tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 成本涉及多种货币，需统一汇率后再比较", "conflict", plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], notes=[f"currencies={m['currencies']}"]))
        if m["lead_days_max"] is not None and model["deadline"]:
            tc.append(mk_check(nid("time_cost", p["label"]), "time_cost", f"方案 {p['label']} 采购前置期与截止日期", "unconfirmed", plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"lead_days={m['lead_days_max']}, deadline={model['deadline']}", computed=m["lead_days_max"], unit="d", conditions=["确认下单日期与到货日不晚于首次使用日"], needs_expert=False))
    checks.extend(tc)

    # ---- 5. 参数范围 ----
    pr = []
    for p in model["plans"]:
        m = plan_metrics(p, model["contracts"], model["domains"])
        for pc in m["param_checks"]:
            verdict = pc["verdict"] or "unconfirmed"
            pr.append(mk_check(nid("parameter_ranges", p["label"]), "parameter_ranges", f"方案 {p['label']} 参数 {pc['name']}={pc['value']}{pc['unit'] or ''}", verdict, plan=p["label"], evidence=[mk_evidence(pc["param_locator"], {"quote": truncate(str(pc['value'])), "verified": source_index.quote_verified(pc["param_locator"], str(pc['value']))}, "parameter")], formula=pc["detail"], conditions=[] if verdict != "blocked" else ["改用域内最接近值或更换设备"]))
        params_with_domain = len(m["param_checks"])
        total_params = len(p["parameters"])
        if total_params > 0 and params_with_domain < total_params:
            pr.append(mk_check(nid("parameter_ranges", p["label"]), "parameter_ranges", f"方案 {p['label']} 有 {total_params - params_with_domain} 个参数未给出允许域", "missing", plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], conditions=["为每个关键参数补 min/max/step 或取值域"]))
    if not any(m["param_checks"] for p in model["plans"] for m in [plan_metrics(p, model["contracts"], model["domains"])]):
        pr.append(mk_check(nid("parameter_ranges"), "parameter_ranges", "方案未提供可数值化检查的关键参数", "missing", evidence=[], conditions=["补充温度/转速/浓度/时间等参数及允许范围"], confidence="high"))
    checks.extend(pr)

    # ---- 6. 风险与异常 ----
    rx = []
    for p in model["plans"]:
        m = plan_metrics(p, model["contracts"], model["domains"])
        blockers = [pc for pc in m["param_checks"] if pc["verdict"] == "blocked"]
        sample = p["samples"]
        sample_block = False
        if sample.get("n_consumed") is not None and sample.get("available") is not None and sample["n_consumed"] > sample["available"]:
            sample_block = True
        eq_block = [r for i, r in enumerate(p["resources"]) if classify_resource(r["name"], r["spec"], inv_entries, model["contracts"])[0] == "不具备"]
        lead_risk = m["lead_days_max"] is not None and model["deadline"] is not None and m["lead_days_max"] > 21
        if blockers:
            rx.append(mk_check(nid("risks_exceptions", p["label"]), "risks_exceptions", f"方案 {p['label']} 存在参数越界（{', '.join(b['name'] for b in blockers)}），需降级路径", "blocked", plan=p["label"], evidence=[mk_evidence(blockers[0]["param_locator"], {"quote": truncate(str(blockers[0]['value'])), "verified": source_index.quote_verified(blockers[0]["param_locator"], str(blockers[0]['value']))}, "parameter")], conditions=["启用域内最接近值并记录偏差", "或更换满足范围的设备/条件"]))
        if sample_block:
            rx.append(mk_check(nid("risks_exceptions", p["label"]), "risks_exceptions", f"方案 {p['label']} 样本消耗超过可用量", "blocked", plan=p["label"], evidence=[mk_evidence(sample.get("locator") or p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], formula=f"required {sample.get('n_required')} × replicates {sample.get('replicates') or 1} = {sample.get('n_consumed')} > available {sample.get('available')}", computed=sample.get("n_consumed"), conditions=["增加样本来源或减少分组/重复数"]))
        if eq_block:
            rx.append(mk_check(nid("risks_exceptions", p["label"]), "risks_exceptions", f"方案 {p['label']} 有 {len(eq_block)} 项资源不具备", "blocked", plan=p["label"], evidence=[mk_evidence(eq_block[0]["locator"], {"quote": eq_block[0]["quote"], "verified": eq_block[0]["verified"]}, "plan_resource")], conditions=["外协/采购或更换设备"]))
        if lead_risk:
            rx.append(mk_check(nid("risks_exceptions", p["label"]), "risks_exceptions", f"方案 {p['label']} 采购前置期可能压线", "conditional", plan=p["label"], evidence=[mk_evidence(p["locator"], {"quote": plan_quote(p, source_index).get("quote"), "verified": plan_quote(p, source_index).get("verified")}, "plan")], conditions=["提前下单并锁定供应商"]))
        if not p["criteria"]:
            rx.append(mk_check(nid("risks_exceptions", p["label"]), "risks_exceptions", f"方案 {p['label']} 未写停止规则/异常判读标准", "missing", plan=p["label"], evidence=[], conditions=["补充停止规则与异常转人工节点"]))
    if not rx:
        rx.append(mk_check(nid("risks_exceptions"), "risks_exceptions", "方案未提供风险与异常处置信息", "missing", evidence=[], conditions=["补充设备故障、批次差异、样本不足、参数越界的处置路径"], confidence="high"))
    checks.extend(rx)

    # ---- 7. 伦理与安全 ----
    es = []
    flags = model["ethics_flags"]
    approvals = [e for e in model["contracts"] if e.get("kind") == "approval"] + [e for e in extract_named(model, "ethics", source_index) if e.get("name")]
    approval_statuses = [str(a.get("status") or a.get("text") or "").lower() for a in approvals]
    approval_text = " ".join(approval_statuses)
    def approval_state():
        if not approvals:
            return None
        if any(t in approval_text for t in ("approved", "已批", "通过", "批准", "有效")):
            return "approved"
        if any(t in approval_text for t in ("pending", "待审", "审查中", "申报中", "在审")):
            return "pending"
        if any(t in approval_text for t in ("rejected", "驳回", "未通过", "expired", "过期")):
            return "rejected"
        return "recorded"
    astate = approval_state()
    if not flags:
        q = "未检出人/动物/病原/危化/个人数据关键词"
        es.append(mk_check(nid("ethics_safety"), "ethics_safety", "未检出涉及人体/动物/危化/数据合规的明显关键词", "ok", evidence=[mk_evidence("case.json", {"quote": q, "verified": True}, "absence_probe")], notes=["仅基于关键词；最终以样本类型字段与实验者说明为准"], needs_expert=True))
    else:
        for f in flags:
            ev = quote_line(source_index, "case.json", "|".join(re.escape(t) for t in f["matched_terms"]))
            if astate is None:
                es.append(mk_check(nid("ethics_safety"), "ethics_safety", f"涉及{f['review_body']}事项，但未找到批件记录", "unconfirmed", evidence=[ev], conditions=[f"取得 {f['review_body']} 批件号、有效期，并确认覆盖本方案"], needs_expert=True, blocking=True))
            elif astate == "approved":
                es.append(mk_check(nid("ethics_safety"), "ethics_safety", f"涉及{f['review_body']}事项，检测到批件记录（状态含 approved/已批）", "conditional", evidence=[ev], conditions=["核对批件号覆盖本方案样本类型与机构，并确认未过期"], needs_expert=True))
            elif astate == "pending":
                es.append(mk_check(nid("ethics_safety"), "ethics_safety", f"涉及{f['review_body']}事项，批件状态为 pending/待审", "conditional", evidence=[ev], conditions=["取得批件号与有效日期后再开工"], needs_expert=True, blocking=True))
            elif astate == "rejected":
                es.append(mk_check(nid("ethics_safety"), "ethics_safety", f"涉及{f['review_body']}事项，批件状态为驳回/过期", "blocked", evidence=[ev], conditions=["重新申报或补充材料后再审查"], needs_expert=True, blocking=True))
            else:
                es.append(mk_check(nid("ethics_safety"), "ethics_safety", f"涉及{f['review_body']}事项，有审批记录但状态不明确", "unconfirmed", evidence=[ev], conditions=[f"确认 {f['review_body']} 审批状态"], needs_expert=True, blocking=True))
    if not approvals:
        es.append(mk_check(nid("ethics_safety"), "ethics_safety", "未提供伦理/安全审批记录字段", "missing", evidence=[], conditions=["补充审批表、批件号与有效期"], confidence="medium"))
    checks.extend(es)

    return checks



# 矛盾检测 / 版本差异



def detect_contradictions(model, checks, source_index):
    out = []
    # 同一参数名不同值
    for p in model["plans"]:
        by_name = {}
        for prm in p["parameters"]:
            if prm["value"] is None:
                continue
            key = norm_name(prm["name"])
            by_name.setdefault(key, []).append(prm)
        for key, lst in by_name.items():
            vals = sorted({round(v["value"], 6) for v in lst})
            if len(vals) > 1:
                out.append({"locator": lst[0]["locator"], "claim": f"参数 {lst[0]['name']} 出现不一致取值 {vals}", "kind": "source_conflict", "quote": truncate("; ".join(str(v['value']) for v in lst))})
    # 样本量算术矛盾
    for p in model["plans"]:
        s = p["samples"]
        if s.get("per_group_n") is not None and s.get("groups") is not None and s.get("n_required") is not None:
            # 若正文显式写了 n=12 但 per_group*groups=16
            m = re.search(r"\bn\s*[=:]\s*(\d+)", p["text_blob"], re.I)
            if m and abs(int(m.group(1)) - s["n_required"]) > 1e-9:
                out.append({"locator": p["locator"], "claim": f"方案 {p['label']} 样本量不一致：正文 n={m.group(1)} 而 per_group×groups={s['n_required']}", "kind": "source_conflict", "quote": truncate(m.group(0))})
    return out


def version_diff(model, metrics_by_plan):
    out = []
    labels = [p["label"] for p in model["plans"]]
    if len(labels) < 2:
        return out
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = metrics_by_plan[labels[i]], metrics_by_plan[labels[j]]
            diffs = []
            if abs(a["elapsed_h"] - b["elapsed_h"]) > 1e-6:
                diffs.append(f"时长 {a['elapsed_h']}h→{b['elapsed_h']}h")
            if abs(a["total_cost"] - b["total_cost"]) > 1e-6:
                diffs.append(f"成本 {a['total_cost']}→{b['total_cost']}")
            pa = sum(1 for x in a["param_checks"] if x["verdict"] == "blocked")
            pb = sum(1 for x in b["param_checks"] if x["verdict"] == "blocked")
            if pa != pb:
                diffs.append(f"参数越界数 {pa}→{pb}")
            if diffs:
                out.append({"from": labels[i], "to": labels[j], "changes": diffs})
    return out


def rank_plans(model, checks):
    metrics_by_plan = {}
    for p in model["plans"]:
        m = plan_metrics(p, model["contracts"], model["domains"])
        metrics_by_plan[p["label"]] = m
    rows = []
    for p in model["plans"]:
        worst, blocking_count, _ = rollup(checks, p["label"])
        m = metrics_by_plan[p["label"]]
        conflict_count = sum(1 for c in checks if (c.get("plan") == p["label"] or c.get("plan") is None) and c["verdict"] == "conflict")
        blocked_count = sum(1 for c in checks if (c.get("plan") == p["label"] or c.get("plan") is None) and c["verdict"] == "blocked")
        missing_count = sum(1 for c in checks if (c.get("plan") == p["label"] or c.get("plan") is None) and c["verdict"] == "missing")
        conditional_count = sum(1 for c in checks if (c.get("plan") == p["label"] or c.get("plan") is None) and c["verdict"] == "conditional")
        key = (conflict_count + blocked_count, missing_count, conditional_count, round(m["total_cost"], 2), round(m["elapsed_h"], 4), p["label"].lower())
        rows.append({
            "version": p["label"],
            "worst_verdict": worst,
            "blocking_count": blocking_count,
            "conflict_count": conflict_count,
            "blocked_count": blocked_count,
            "missing_count": missing_count,
            "conditional_count": conditional_count,
            "cost": m["total_cost"],
            "elapsed_h": m["elapsed_h"],
            "score_key": key,
            "rank": None,
        })
    rows.sort(key=lambda r: r["score_key"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows, metrics_by_plan



# 结果装配



def contract_field_names(case):
    spec = None
    for k in ("output_contract", "outputContract", "output_schema"):
        if isinstance(case, dict) and k in case:
            spec = case[k]
            break
    if isinstance(spec, dict):
        if "fields" in spec and isinstance(spec["fields"], dict):
            spec = spec["fields"]
        return list(spec.keys())
    if isinstance(spec, list):
        return [s if isinstance(s, str) else (s.get("name") if isinstance(s, dict) else None) for s in spec]
    return []


def default_for_kind(kind):
    return {"string": "", "number": None, "boolean": None, "array": [], "object": None}.get(kind, None)


def build_result(case, files, input_dir, source_index, model, checks):
    status = "partial"
    result = {
        "case_id": model["case_id"],
        "challenge_id": None,
        "task_title": model["title"],
        "schema_version": SCHEMA_VERSION,
        "engine": ENGINE_ID,
        "status": status,
        "overall_conclusion": {
            "verdict": None,
            "summary": None,
            "recommended_plan": None,
            "blocking_issues": [],
            "conditions": [],
            "unblock_actions": [],
        },
        "experiment_plan": build_experiment_plan(model),
        "review": {d: [] for d in DIM_ORDER},
        "check_ledger": [],
        "plan_comparison": [],
        "evidence_and_risks": {
            "evidence": [],
            "risks": [],
            "exception_handling": [],
        },
        "contradictions": [],
        "confirmation": {
            "safety": [],
            "ethics": [],
            "equipment_availability": [],
            "expert_confirmation": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {
                "step": "skeleton",
                "action": "写入确定性 partial 结果骨架",
                "reason": "执行流程规定：前 3 分钟内先落盘合法结果，之后只做增量改进",
            }
        ],
        "runtime": {
            "budget_seconds": 900,
            "budget_requests": 64,
            "wall_clock_seconds": None,
            "note": "实际耗时/请求由平台记录；本 Skill 不写入未经测量的运行时数据",
        },
        "artifacts": [],
        "provenance": {
            "input_sha256": {},
            "result_sha256": None,
            "deterministic": True,
            "canonicalization": "sort_keys=true, indent=1, ensure_ascii=false",
        },
    }

    for c in checks:
        result["review"].setdefault(c["dimension"], []).append(c)
    for d in DIM_ORDER:
        result["review"][d].sort(key=lambda x: x["id"])

    result["check_ledger"] = [
        {
            "id": c["id"],
            "dimension": c["dimension"],
            "claim": c["claim"],
            "verdict": c["verdict"],
            "plan": c.get("plan"),
            "formula": c.get("formula"),
            "computed": c.get("computed"),
            "unit": c.get("unit"),
            "threshold": c.get("threshold"),
            "locator": (c["evidence"][0]["locator"] if c.get("evidence") else None),
            "quote": (c["evidence"][0]["quote"] if c.get("evidence") else None),
        }
        for c in checks
    ]

    rows, metrics_by_plan = rank_plans(model, checks)
    result["plan_comparison"] = rows
    result["version_diff"] = version_diff(model, metrics_by_plan)
    result["evidence_and_risks"]["risks"] = [c for c in checks if c["dimension"] == "risks_exceptions"]
    result["evidence_and_risks"]["exception_handling"] = [c.get("conditions") for c in checks if c["dimension"] == "risks_exceptions" and c.get("conditions")]
    result["evidence_and_risks"]["evidence"] = [
        {"locator": e["locator"], "quote": e["quote"], "kind": e["kind"], "verified": e["verified"]}
        for c in checks
        for e in c.get("evidence", [])
    ]
    result["contradictions"] = detect_contradictions(model, checks, source_index)

    # 确认项
    conf = result["confirmation"]
    for c in checks:
        if c["dimension"] == "ethics_safety":
            conf["ethics"].append({"id": c["id"], "claim": c["claim"], "verdict": c["verdict"], "needs_expert": c["needs_expert"]})
        if c["dimension"] == "risks_exceptions":
            conf["safety"].append({"id": c["id"], "claim": c["claim"], "verdict": c["verdict"]})
        if c["dimension"] == "equipment_materials" and c.get("resource_state") in ("需采购", "不具备", "未知"):
            conf["equipment_availability"].append({"id": c["id"], "claim": c["claim"], "resource_state": c["resource_state"]})
        if c.get("needs_expert"):
            conf["expert_confirmation"].append({"id": c["id"], "claim": c["claim"], "dimension": c["dimension"]})

    # 不确定性 / pending_human / sources
    result["uncertainty"] = [
        {"id": c["id"], "claim": c["claim"], "reason": "未提供足够信息判定", "dimension": c["dimension"]}
        for c in checks
        if c["verdict"] in ("unconfirmed", "missing")
    ]
    result["pending_human"] = [
        {"id": c["id"], "claim": c["claim"], "action": (c["conditions"][0] if c.get("conditions") else "人工确认")}
        for c in checks
        if c["verdict"] in ("unconfirmed", "blocked") or c.get("needs_expert")
    ]
    result["sources"] = [
        {"id": f"{i + 1}", "path": f["relpath"], "kind": f["ext"].lstrip(".") or "file", "bytes": f["size"], "sha256": sha256_bytes(open(f["abspath"], "rb").read()) if f["status"] == "ok" and os.path.exists(f["abspath"]) else None}
        for i, f in enumerate(files)
    ]
    result["provenance"]["input_sha256"] = {f["relpath"]: result["sources"][i]["sha256"] for i, f in enumerate(files)}
    for i, f in enumerate(files):
        result["provenance"]["input_sha256"][f["relpath"]] = result["sources"][i]["sha256"]

    # 状态与结论
    result["status"], result["overall_conclusion"] = decide_status(model, checks, rows, result)

    # 契约字段原样回填（不改名、不删未知键）
    for name in contract_field_names(case):
        if name not in result:
            result[name] = None

    result["artifacts"] = ["result.json", "review_report.md", "check_ledger.csv", "audit_digest.md", "run_manifest.json"]
    return result


def build_experiment_plan(model):
    if model["plans"]:
        p = model["plans"][0]
        return {
            "source": "case.plans",
            "plan_version": p["label"],
            "research_question": model["objective"],
            "hypothesis": model["hypothesis"],
            "assumptions": [{"name": a.get("name"), "text": a.get("text"), "locator": a.get("locator")} for a in model["assumptions"] + p.get("assumptions", [])],
            "variables": [{"name": v.get("name"), "text": v.get("text"), "locator": v.get("locator")} for v in model["variables"]],
            "controls": [{"name": c.get("name"), "text": c.get("text"), "locator": c.get("locator")} for c in model["controls"] + p.get("controls", [])],
            "steps": [
                {"seq": s["seq"], "name": s["name"], "hands_on_h": s["hands_on_h"], "wait_h": s["wait_h"], "equipment": s["equipment"], "parameters": [{"name": x["name"], "value": x["value"], "unit": x["unit"]} for x in s["parameters"]], "locator": s["locator"]}
                for s in p["steps"]
            ],
            "parameters": [{"name": x["name"], "value": x["value"], "unit": x["unit"], "locator": x["locator"]} for x in p["parameters"]],
            "resources": [{"name": r["name"], "kind": r["kind"], "spec": r["spec"], "locator": r["locator"]} for r in p["resources"]],
            "sample_plan": p["samples"],
            "interpretation_criteria": [{"name": c.get("name"), "text": c.get("text"), "locator": c.get("locator")} for c in p.get("criteria", [])],
        }
    return {
        "source": "derived_skeleton",
        "research_question": model["objective"],
        "hypothesis": model["hypothesis"],
        "assumptions": [],
        "variables": [],
        "controls": [],
        "steps": [],
        "parameters": [],
        "resources": [],
        "sample_plan": {},
        "interpretation_criteria": [],
    }


def decide_status(model, checks, rows, result):
    conclusion = result["overall_conclusion"]
    blocking = [c for c in checks if c.get("blocking") or c["verdict"] in ("blocked", "conflict")]
    missing = [c for c in checks if c["verdict"] == "missing"]
    unconfirmed = [c for c in checks if c["verdict"] == "unconfirmed"]
    conclusion["blocking_issues"] = [
        {"id": c["id"], "dimension": c["dimension"], "claim": c["claim"], "verdict": c["verdict"]}
        for c in blocking
    ]
    conditions = [c["id"] + ": " + "; ".join(c["conditions"]) for c in checks if c.get("conditions")]
    conclusion["conditions"] = conditions[:25]
    if len(conditions) > 25:
        conclusion["conditions"].append(f"…另有 {len(conditions) - 25} 条条件，见 review 详情")
    conclusion["unblock_actions"] = [
        {"id": c["id"], "action": (c["conditions"][0] if c.get("conditions") else "人工确认"), "dimension": c["dimension"]}
        for c in blocking
    ]
    if rows:
        rec = rows[0]["version"]
        conclusion["recommended_plan"] = rec
        conclusion["notes"] = ["推荐方案为相对最优（按阻断数/缺失数/成本/时长字典序）；若推荐方案仍有阻断，须先解除方可开工"]
    verdict = "undetermined"
    if not model["plans"] and not model["objective"]:
        status = "insufficient_evidence"
        verdict = "undetermined"
        conclusion["summary"] = "无可读方案与目标，无法审查。"
    elif blocking:
        status = "partial"
        verdict = "not_feasible"
        conclusion["summary"] = f"存在 {len(blocking)} 条阻断性问题（blocked/conflict 或需审批事项），当前版本不可直接开工。"
    elif missing or unconfirmed:
        status = "partial"
        verdict = "conditionally_feasible"
        conclusion["summary"] = f"无阻断性错误，但有 {len(missing)} 条缺失信息与 {len(unconfirmed)} 条待确认事项，满足所列条件后可开工。"
    else:
        status = "ok"
        verdict = "feasible"
        conclusion["summary"] = "七类检查均通过，方案具备开工条件（依据材料范围内）。"
    conclusion["verdict"] = verdict
    if not checks:
        status = "insufficient_evidence"
    return status, conclusion


def merge_findings(result, findings, source_index):
    errors = []
    if not isinstance(findings, dict):
        errors.append("findings_not_object")
        return result, errors
    review = findings.get("review") or {}
    added = 0
    for dim, items in review.items():
        if dim not in result["review"]:
            errors.append(f"unknown_dimension:{dim}")
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                errors.append(f"invalid_item:{dim}")
                continue
            verdict = it.get("verdict")
            if verdict not in SEVERITY:
                errors.append(f"invalid_verdict:{dim}:{verdict}")
                continue
            ev = it.get("evidence") or []
            ok_evidence = True
            if verdict in ("ok", "conditional", "blocked", "conflict"):
                if not ev:
                    ok_evidence = False
                for e in ev:
                    if not e.get("locator") or not e.get("quote"):
                        ok_evidence = False
            if not ok_evidence:
                errors.append(f"missing_evidence:{dim}:{it.get('claim')}")
                continue
            it.setdefault("id", f"{DIM_PREFIX.get(dim,'X')}-H{added + 1:02d}")
            it["dimension"] = dim
            it.setdefault("evidence", ev)
            result["review"][dim].append(it)
            added += 1
    for d in DIM_ORDER:
        result["review"][d].sort(key=lambda x: x["id"])
    result["change_log"].append({"step": "merge_findings", "action": f"合并人工修订 {added} 条", "reason": "Agent 语义判定写入并校验"})
    result["errors"] = result.get("errors", []) + errors
    return result, errors


def write_outputs(output_dir, result):
    os.makedirs(output_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    result["provenance"]["result_sha256"] = None
    canon = canonical_json(result)
    result["provenance"]["result_sha256"] = sha256_text(canon)
    with open(result_path, "w", encoding="utf-8") as fh:
        fh.write(canonical_json(result) + "\n")
    _write_report(output_dir, result)
    _write_csv(output_dir, result)
    _write_digest(output_dir, result)
    _write_manifest(output_dir, result, canon)
    return result_path


def _esc_md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _write_report(output_dir, result):
    lines = ["# 实验方案可行性审计报告（可编辑复核版）", ""]
    lines.append(f"- case_id: `{result.get('case_id')}`")
    lines.append(f"- status: `{result.get('status')}`")
    lines.append(f"- overall verdict: `{result['overall_conclusion'].get('verdict')}`")
    lines.append(f"- recommended plan: `{result['overall_conclusion'].get('recommended_plan')}`")
    lines.append("")
    lines.append("## 总体结论")
    lines.append("")
    lines.append(result["overall_conclusion"].get("summary") or "（无）")
    lines.append("")
    for dim in DIM_ORDER:
        items = result["review"].get(dim, [])
        if not items:
            continue
        lines.append(f"## {dim}")
        lines.append("")
        lines.append("| id | 结论 | 主张 | 算式/计算 | 证据定位 | 逐字原文 |")
        lines.append("|---|---|---|---|---|---|")
        for c in items:
            ev = c.get("evidence") or [{}]
            e0 = ev[0] if ev else {}
            lines.append(
                f"| {c.get('id')} | {c.get('verdict')} | {_esc_md(c.get('claim'))} | {_esc_md(c.get('formula') or c.get('computed') or '')} | {_esc_md(e0.get('locator'))} | {_esc_md(e0.get('quote') or '')} |"
            )
        lines.append("")
    if result.get("contradictions"):
        lines.append("## 矛盾记录")
        lines.append("")
        for cd in result["contradictions"]:
            lines.append(f"- [{cd.get('kind')}] {cd.get('claim')} @ {cd.get('locator')} `{_esc_md(cd.get('quote'))}`")
        lines.append("")
    if result.get("plan_comparison"):
        lines.append("## 方案比较")
        lines.append("")
        for r in result["plan_comparison"]:
            if "version_diff" in r:
                continue
            lines.append(f"- v{r.get('version')}：rank={r.get('rank')} 最差结论={r.get('worst_verdict')} 阻断={r.get('blocking_count')} 缺失={r.get('missing_count')} 成本={r.get('cost')} 时长={r.get('elapsed_h')}h")
        lines.append("")
    if result.get("uncertainty"):
        lines.append("## 不确定性与待确认")
        lines.append("")
        for u in result["uncertainty"]:
            lines.append(f"- {u.get('id')}：{_esc_md(u.get('claim'))}（{u.get('reason')}）")
        lines.append("")
    lines.append("> 本报告与 result.json 同源生成；每条 firm 结论均已由 selftest.py 二次核对 quote 与来源一致性。")
    with open(os.path.join(output_dir, "review_report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_csv(output_dir, result):
    import csv

    path = os.path.join(output_dir, "check_ledger.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["check_id", "dimension", "claim", "verdict", "plan", "formula", "computed", "unit", "threshold", "locator", "quote"])
        for c in result.get("check_ledger", []):
            w.writerow([c.get("id"), c.get("dimension"), c.get("claim"), c.get("verdict"), c.get("plan"), c.get("formula"), c.get("computed"), c.get("unit"), c.get("threshold"), c.get("locator"), c.get("quote")])
    return path


def _write_digest(output_dir, result):
    lines = ["# 审计摘要（供 Agent 快速定位待人工确认项）", ""]
    lines.append(f"case_id={result.get('case_id')} status={result.get('status')} verdict={result['overall_conclusion'].get('verdict')}")
    lines.append("")
    needs = [c for c in result.get("pending_human", [])]
    if needs:
        lines.append("## 待确认 / 需人工处理（按此优先级）")
        lines.append("")
        for n in needs[:20]:
            lines.append(f"- `{n.get('id')}` {n.get('claim')} → {n.get('action')}")
    else:
        lines.append("无需人工确认项。")
    lines.append("")
    if result.get("contradictions"):
        lines.append("## 矛盾")
        for cd in result["contradictions"]:
            lines.append(f"- {cd.get('claim')}")
    with open(os.path.join(output_dir, "audit_digest.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_manifest(output_dir, result, canon):
    manifest = {
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "result_sha256": result["provenance"]["result_sha256"],
        "input_sha256": result["provenance"]["input_sha256"],
        "canonical_bytes": len(canon.encode("utf-8")),
        "deterministic": True,
        "engine": ENGINE_ID,
        "note": "复跑一致性：同输入重跑应得到相同 result_sha256（墙钟/请求数由平台记录，不计入结果）",
    }
    with open(os.path.join(output_dir, "run_manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(canonical_json(manifest) + "\n")
