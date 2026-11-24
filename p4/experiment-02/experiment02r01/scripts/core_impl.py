#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查：输入发现、证据索引、确定性算术与结果骨架。

子命令：
  init   扫描 /input，写 partial /app/output/result.json 与 work/casepack.json
  show   打印 locator 指向的原文（逐字引用的来源）
  grep   按关键词在输入材料中定位 locator（证据检索）
  check  批量校验 locator 是否可解析

仅依赖 Python 标准库（>=3.8）。assemble.py / selfcheck.py 导入本模块复用函数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

MAX_TEXT = 2 * 1024 * 1024
MAX_LEAVES = 20000
JSON_EXTS = {".json", ".jsonl"}
TEXT_EXTS = {".md", ".txt", ".csv", ".tsv", ".yaml", ".yml", ".log"}
PDF_EXTS = {".pdf"}

DIMS = ("assumptions_variables", "controls", "equipment_materials", "time_cost",
        "parameter_ranges", "risks_exceptions", "ethics_safety")
VERDICTS = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
RESOURCE_STATES = ("已具备", "可外协", "需采购", "不具备", "未知")
EVIDENCE_ROLES = ("supports", "contradicts", "defines", "context")
DEFAULT_CONTRACT = ("case_id", "status", "research_question", "experiment_plan",
                    "review", "plan_comparison", "evidence_and_risks",
                    "confirmation", "sources", "uncertainty")

TIME_UNITS = {
    "秒": 1 / 60, "s": 1 / 60, "sec": 1 / 60, "secs": 1 / 60, "second": 1 / 60, "seconds": 1 / 60,
    "分钟": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "小时": 60, "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "天": 1440, "d": 1440, "day": 1440, "days": 1440,
    "周": 10080, "week": 10080, "weeks": 10080,
}

_UNIT = (r"小时|hrs|hr|h|分钟|mins|min|天|days|day|d|周|weeks|week|秒|secs|sec|s|"
         r"µL|μL|uL|mL|L|ng|µg|μg|ug|mg|kg|g|°C|℃|rpm|%|次|份|例|只|孔|wells|well|cycles|cycle|"
         r"μmol|µmol|mmol|mM|µM|μM|nM|kHz|Hz|Gy|Sv|kPa|MPa|bar|V|mA|W")
QTY_RE = re.compile(r"(?<![\d.])(\d+(?:,\d{3})*(?:\.\d+)?)\s*(" + _UNIT + r")(?![A-Za-z0-9_])")
CUR_RE = re.compile(
    r"(?P<sym>[¥￥$€£])\s?(?P<amt>\d+(?:,\d{3})*(?:\.\d+)?)"
    r"|(?P<amt2>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<cur>元|美元|USD|CNY|RMB|EUR)")
DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
CUR_SYM = {"¥": "CNY", "￥": "CNY", "$": "USD", "€": "EUR", "£": "GBP"}
CUR_TXT = {"元": "CNY", "美元": "USD", "USD": "USD", "CNY": "CNY", "RMB": "CNY", "EUR": "EUR"}

PLAN_KEYS = {"plans", "candidateplans", "candidates", "versions", "planversions",
             "planvariants", "candidateschemes", "方案", "方案版本", "候选方案", "实验方案"}
STEP_KEYS = {"steps", "procedure", "protocol", "workflow", "procedures",
             "experimentalsteps", "步骤", "流程", "实验步骤"}
ASSUMPTION_KEYS = {"assumptions", "hypotheses", "hypothesis", "假设", "前提"}
VARIABLE_KEYS = {"variables", "variable", "factors", "变量", "因素"}
CONTROL_KEYS = {"controls", "control", "controlgroup", "对照组", "对照"}
READOUT_KEYS = {"readout", "readouts", "criteria", "interpretation", "successcriteria",
                "endpoints", "判读", "判读标准", "成功标准", "指标"}
RESOURCE_KEYS = {"resources", "equipment", "materials", "consumables", "资源", "设备", "材料"}
SAMPLE_KEYS = {"n", "samples", "samplesize", "samplecount", "nrequired", "requiredn",
               "navailable", "availablen", "样本量", "样本数", "例数", "npergroup"}

CONTRACT_KEYS = ("output_contract", "outputContract", "result_contract",
                 "output_schema", "required_fields", "output_fields")
SECTION_WORDS = {
    "equipment": ("equipment", "instruments", "devices", "设备", "仪器"),
    "consumables": ("consumables", "reagents", "materials", "耗材", "试剂"),
    "booking": ("booking", "bookings", "reservation", "schedule", "预约", "排期", "机时"),
    "pricing": ("pricing", "prices", "price_list", "quotes", "报价", "价格"),
    "samples": ("samples", "sample", "样本", "标本"),
    "ethics": ("ethics", "irb", "approval", "approvals", "伦理", "审批"),
    "safety": ("safety", "biosafety", "安全", "生物安全"),
}
SILENCE_KEYWORDS = {
    "assumptions_variables": ("variable", "variables", "assumption", "hypothesis", "变量", "假设"),
    "controls": ("control", "sham", "blank", "对照", "空白"),
    "equipment_materials": ("equipment", "device", "instrument", "reagent", "consumable",
                            "设备", "仪器", "耗材", "试剂"),
    "time_cost": ("duration", "time", "耗时", "时长", "周期", "等待", "sample", "样本", "n="),
    "parameter_ranges": ("parameter", "tolerance", "range", "参数", "容差", "范围"),
    "risks_exceptions": ("risk", "exception", "failure", "contingency", "风险", "异常", "故障"),
    "ethics_safety": ("ethics", "approval", "irb", "iacuc", "safety", "伦理", "审批", "安全"),
}
SILENCE_ALIAS = {
    "assumptions_variables": "assumptions_variables",
    "controls": "controls",
    "equipment_materials": "equipment_materials",
    "time_cost": "time_cost",
    "parameter_ranges": "parameter_ranges",
    "risks_exceptions": "risks_exceptions",
    "ethics_safety": "ethics_safety",
}

_JSON_CACHE = {}
_PDF_CACHE = {}


def sha1_8(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()[:8]


def norm_ws(text):
    return re.sub(r"[\s\u3000\u00a0]+", " ", str(text)).strip()


def quote_len_ok(quote):
    q = (quote or "").strip()
    return len(q) >= 3 or bool(re.fullmatch(r"-?\d+(?:\.\d+)?", q))


def esc(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def unescape(token):
    return str(token).replace("~1", "/").replace("~0", "~")


def norm_key(token):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(token).lower())


def _preview(obj, n=180):
    if isinstance(obj, str):
        s = obj
    else:
        try:
            s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            s = str(obj)
    return norm_ws(s)[:n]


def read_text(path, max_bytes=MAX_TEXT):
    if not os.path.isfile(path):
        return None, [f"missing_file: {path}"]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"unreadable: {path}: {exc}"]
    if size > max_bytes:
        return None, [f"oversize({size} > {max_bytes}): {path}"]
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), [f"decode_replaced: {path}"]


def load_json(path):
    key = (os.path.abspath(path), os.path.getmtime(path))
    if key in _JSON_CACHE:
        return _JSON_CACHE[key]
    text, warns = read_text(path)
    if text is None:
        _JSON_CACHE[key] = (None, warns)
        return None, warns
    try:
        data = json.loads(text)
        _JSON_CACHE[key] = (data, warns)
        return data, warns
    except json.JSONDecodeError as exc:
        if path.lower().endswith(".jsonl"):
            out, errs = [], [f"bad_json: {exc}"]
            for i, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e2:
                    errs.append(f"bad_jsonl_line {i}: {e2}")
            _JSON_CACHE[key] = (out, warns + errs)
            return out, warns + errs
        _JSON_CACHE[key] = (None, warns + [f"bad_json: {exc}"])
        return None, warns + [f"bad_json: {exc}"]


def file_sha1(path, cap=8 * 1024 * 1024):
    h = hashlib.sha1()
    remaining = cap
    try:
        with open(path, "rb") as fh:
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:8]


def discover(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            kind = ("json" if ext in JSON_EXTS else "text" if ext in TEXT_EXTS
                    else "pdf" if ext in PDF_EXTS else "binary")
            files.append({"rel": rel, "path": path, "name": name, "ext": ext,
                          "bytes": size, "kind": kind, "sha1_8": file_sha1(path)})
    return files


def walk_nodes(node, ptr="", depth=0, max_depth=12):
    yield ptr, node
    if depth >= max_depth:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_nodes(v, f"{ptr}/{esc(k)}", depth + 1, max_depth)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_nodes(v, f"{ptr}/{i}", depth + 1, max_depth)


def _leaves(node, base="", max_depth=12):
    stack = [(node, base, 0)]
    n = 0
    while stack:
        cur, p, d = stack.pop()
        if n > MAX_LEAVES:
            return
        if d > max_depth:
            continue
        if isinstance(cur, dict):
            for k in reversed(list(cur.keys())):
                stack.append((cur[k], f"{p}/{esc(k)}", d + 1))
        elif isinstance(cur, list):
            for i in range(len(cur) - 1, -1, -1):
                stack.append((cur[i], f"{p}/{i}", d + 1))
        else:
            n += 1
            yield p, cur


def json_pointer(data, pointer):
    if pointer in ("", "/"):
        return data, True
    cur = data
    for raw in pointer.lstrip("/").split("/"):
        token = unescape(raw)
        if isinstance(cur, list):
            if not re.fullmatch(r"-?\d+", token):
                return None, False
            idx = int(token)
            if idx < 0 or idx >= len(cur):
                return None, False
            cur = cur[idx]
        elif isinstance(cur, dict):
            if token not in cur:
                return None, False
            cur = cur[token]
        else:
            return None, False
    return cur, True


def bracket_to_pointer(frag):
    if not frag or frag.startswith("/"):
        return frag
    s = re.sub(r"\[(\d+)\]", r".\1", frag)
    return "/" + s.replace(".", "/")


def node_text(node, cap=200000):
    if isinstance(node, str):
        return node
    if isinstance(node, (int, float, bool)) or node is None:
        return json.dumps(node)
    try:
        text = json.dumps(node, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(node)
    return text[:cap]


def pdf_pages(path, work=None):
    key = os.path.abspath(path)
    if key in _PDF_CACHE:
        return _PDF_CACHE[key]
    pages = None
    cache = None
    if work:
        try:
            cache_dir = os.path.join(work, "pdf_text")
            os.makedirs(cache_dir, exist_ok=True)
            cache = os.path.join(cache_dir, sha1_8(key + str(os.path.getsize(path))) + ".json")
            if os.path.isfile(cache):
                with open(cache, encoding="utf-8") as fh:
                    pages = json.load(fh)
        except (OSError, ValueError):
            pages = None
    if pages is None:
        try:
            out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
                                 capture_output=True, timeout=120)
            if out.returncode == 0 and out.stdout:
                pages = out.stdout.decode("utf-8", "replace").split("\f")
        except Exception:
            pages = None
    if pages is None:
        for mod in ("pypdf", "PyPDF2"):
            try:
                m = __import__(mod)
                reader = m.PdfReader(path)
                pages = [(p.extract_text() or "") for p in reader.pages]
                break
            except Exception:
                continue
    if pages is None:
        _PDF_CACHE[key] = None
        return None
    if cache:
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump(pages, fh, ensure_ascii=False)
        except OSError:
            pass
    _PDF_CACHE[key] = pages
    return pages


def _heading(text, name):
    lines = text.splitlines()
    start, level = None, None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not m:
            continue
        if name and name in m.group(2).strip():
            start, level = i, len(m.group(1))
            break
    if start is None:
        return "", "heading_not_found"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return "\n".join(lines[start:end]), f"H:{name}"


def _resolve_text_frag(text, frag):
    if not frag:
        return text, "whole"
    m = re.fullmatch(r"L(\d+)(?:\s*-\s*L?(\d+))?", frag)
    if m:
        a = int(m.group(1))
        b = int(m.group(2) or a)
        lines = text.splitlines()
        if a < 1 or b < a:
            return "", "bad_range"
        return "\n".join(lines[a - 1:b]), f"L{a}-L{b}"
    m = re.fullmatch(r"p(\d+)", frag)
    if m:
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        i = int(m.group(1))
        if 1 <= i <= len(paras):
            return paras[i - 1], f"p{i}"
        return "", "bad_paragraph"
    if frag.startswith("H:"):
        return _heading(text, frag[2:].strip())
    return text, "whole"


def resolve_locator(loc, input_dir, work=None):
    loc = (loc or "").strip()
    if "#" in loc:
        rel, frag = loc.split("#", 1)
    else:
        rel, frag = loc, ""
    rel = rel.replace("\\", "/").strip()
    frag = frag.strip()
    path = os.path.normpath(os.path.join(input_dir, rel))
    if not os.path.isfile(path):
        return {"ok": False, "kind": "missing_file", "text": "", "detail": f"文件不存在: {rel}"}
    ext = os.path.splitext(path)[1].lower()
    if ext in JSON_EXTS:
        data, err = load_json(path)
        if data is None:
            return {"ok": False, "kind": "bad_json", "text": "", "detail": ";".join(err)}
        ptr = bracket_to_pointer(frag)
        node, ok = json_pointer(data, ptr)
        if not ok:
            return {"ok": False, "kind": "missing_pointer", "text": "", "detail": f"JSON 指针无法解析: {ptr}"}
        return {"ok": True, "kind": "json", "text": node_text(node), "detail": ptr or "/"}
    if ext in TEXT_EXTS:
        text, warns = read_text(path)
        if text is None:
            return {"ok": False, "kind": "unreadable", "text": "", "detail": ";".join(warns)}
        body, sel = _resolve_text_frag(text, frag)
        if not body and sel not in ("whole", "H:x"):
            return {"ok": False, "kind": sel, "text": "", "detail": f"文本定位无效: {frag}"}
        return {"ok": True, "kind": "text", "text": body, "detail": sel}
    if ext in PDF_EXTS:
        pages = pdf_pages(path, work)
        if pages is None:
            return {"ok": False, "kind": "pdf_unavailable", "text": "",
                    "detail": "pdftotext/pypdf 不可用，未能提取 PDF 文本"}
        m = re.fullmatch(r"p(\d+)", frag or "p1")
        if not m:
            return {"ok": False, "kind": "bad_page", "text": "", "detail": f"PDF 页码定位无效: {frag}"}
        i = int(m.group(1))
        if i < 1 or i > len(pages):
            return {"ok": False, "kind": "missing_page", "text": "", "detail": f"PDF 缺页: p{i}"}
        return {"ok": True, "kind": "pdf", "text": pages[i - 1], "detail": f"p{i}"}
    return {"ok": False, "kind": "unsupported", "text": "", "detail": f"不支持的文件类型: {ext}"}


def contract_fields(data):
    if not isinstance(data, dict):
        return {}, "none"
    for k in CONTRACT_KEYS:
        if k not in data:
            continue
        spec = data[k]
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError:
                continue
        if isinstance(spec, dict):
            props = spec.get("properties") if isinstance(spec.get("properties"), dict) else spec
            out = {}
            for f, s in props.items():
                if f in ("properties", "required", "type", "description", "$schema", "title"):
                    continue
                hint = (s.get("description") or s.get("type") or "") if isinstance(s, dict) else str(s)
                out[str(f)] = str(hint)[:160]
            if out:
                return out, k
        if isinstance(spec, list):
            out = {str(x): "" for x in spec if isinstance(x, (str, int))}
            if out:
                return out, k
    return {}, "none"


def default_for_hint(hint):
    h = str(hint).lower()
    if any(t in h for t in ("array", "list", "[]", "列表", "数组", "objects")):
        return []
    if any(t in h for t in ("object", "dict", "map", "对象")):
        return {}
    return None


def _case_id(data, files):
    if isinstance(data, dict):
        for k in ("case_id", "id", "challenge_id", "caseId", "armiId"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    for f in files:
        if f["name"].lower() in ("case.json", "case.jsonl"):
            return "derived:" + os.path.splitext(f["name"])[0]
    return "unknown"


def load_case(input_dir, files=None):
    files = files or discover(input_dir)
    cands = [f for f in files if f["name"].lower() in ("case.json", "case.jsonl")]
    cands.sort(key=lambda f: (f["rel"].count("/"), f["rel"]))
    for f in cands:
        data, err = load_json(f["path"])
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else {}
        if isinstance(data, dict):
            return data, f["rel"], err
        return {}, f["rel"], err
    for f in files:
        if f["ext"] in JSON_EXTS:
            data, err = load_json(f["path"])
            if isinstance(data, dict):
                return data, f["rel"], err
    return {}, "", ["no_case_json_found"]


def _entries(value, base):
    out = []
    if isinstance(value, list):
        for i, v in enumerate(value):
            if isinstance(v, (dict, str)):
                out.append((f"{base}/{i}", v))
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, str)):
                out.append((f"{base}/{esc(k)}", v))
    return out


def find_plans(data):
    for ptr, node in walk_nodes(data, max_depth=8):
        if isinstance(node, dict):
            for k, v in node.items():
                if norm_key(k) not in PLAN_KEYS:
                    continue
                entries = _entries(v, f"{ptr}/{esc(k)}")
                if len(entries) >= 1:
                    return {"container": f"{ptr}/{esc(k)}", "source": "key:" + str(k), "entries": entries}
    for ptr, node in walk_nodes(data, max_depth=8):
        if isinstance(node, list) and len(node) >= 2 and all(isinstance(x, dict) for x in node):
            shared = set(node[0]) & set(node[1])
            if any(norm_key(k) in STEP_KEYS for k in shared) or any("version" in norm_key(k) for k in shared):
                return {"container": ptr, "source": "heuristic:list",
                        "entries": [(f"{ptr}/{i}", v) for i, v in enumerate(node)]}
    return None


def _version(entry, ptr):
    if isinstance(entry, dict):
        for k in ("version", "version_id", "id", "name", "label", "label_zh", "方案", "版本", "title"):
            if k in entry and isinstance(entry[k], (str, int, float)):
                return str(entry[k])[:40]
    return unescape(ptr.rsplit("/", 1)[-1]) or "v?"


def _unit_from_key(key):
    k = norm_key(key)
    if k.endswith("min") or k.endswith("mins") or "minute" in k:
        return "min"
    if "hour" in k or k.endswith("hr") or k.endswith("hrs") or k.endswith("h"):
        return "h"
    if "day" in k or k.endswith("d"):
        return "d"
    if "week" in k:
        return "week"
    if "second" in k or k.endswith("sec"):
        return "s"
    return None


def _durations(entry, ptr):
    out = []
    for p, leaf in _leaves(entry, ptr):
        key = unescape(p.rsplit("/", 1)[-1])
        nk = norm_key(key)
        if isinstance(leaf, (int, float)) and not isinstance(leaf, bool):
            unit = _unit_from_key(key)
            if unit and any(t in nk for t in ("duration", "time", "耗时", "时长", "周期", "等待", "周转", "lead", "incub")):
                out.append({"locator": "case.json#" + p, "text": f"{key}={leaf}",
                            "value": float(leaf), "unit": unit,
                            "minutes": round(float(leaf) * TIME_UNITS[unit], 4)})
        elif isinstance(leaf, str):
            for m in QTY_RE.finditer(leaf):
                u = m.group(2)
                if u in TIME_UNITS:
                    val = float(m.group(1).replace(",", ""))
                    out.append({"locator": "case.json#" + p, "text": m.group(0), "value": val,
                                "unit": u, "minutes": round(val * TIME_UNITS[u], 4)})
    return out[:120]


def _costs(entry, ptr):
    out = []
    for p, leaf in _leaves(entry, ptr):
        key = unescape(p.rsplit("/", 1)[-1])
        if isinstance(leaf, str):
            for m in CUR_RE.finditer(leaf):
                if m.group("sym"):
                    out.append({"locator": "case.json#" + p, "text": m.group(0),
                                "amount": float(m.group("amt").replace(",", "")),
                                "currency": CUR_SYM[m.group("sym")]})
                else:
                    out.append({"locator": "case.json#" + p, "text": m.group(0),
                                "amount": float(m.group("amt2").replace(",", "")),
                                "currency": CUR_TXT.get(m.group("cur"), m.group("cur"))})
        elif isinstance(leaf, (int, float)) and not isinstance(leaf, bool) and norm_key(key) in \
                ("price", "cost", "fee", "费用", "价格", "报价", "单价", "总价"):
            out.append({"locator": "case.json#" + p, "text": f"{key}={leaf}", "amount": float(leaf),
                        "currency": "unspecified"})
    return out[:120]


def _samples(entry, ptr):
    out = {}
    for p, leaf in _leaves(entry, ptr):
        key = unescape(p.rsplit("/", 1)[-1])
        nk = norm_key(key)
        if isinstance(leaf, (int, float)) and not isinstance(leaf, bool) and nk in SAMPLE_KEYS:
            out[nk] = {"value": float(leaf), "locator": "case.json#" + p, "key": key}
        elif isinstance(leaf, str):
            for m in re.finditer(r"(?<![A-Za-z])(n|N)\s*=\s*(\d+)", leaf):
                out.setdefault("n", {"value": float(m.group(2)), "locator": "case.json#" + p, "key": "n="})
    return out


def _list_field(entry, ptr, words):
    for k, v in entry.items():
        if norm_key(k) not in words:
            continue
        base = f"case.json#{ptr}/{esc(k)}"
        if isinstance(v, list):
            return [{"text": _preview(x, 200), "locator": f"{base}/{i}"} for i, x in enumerate(v)]
        if isinstance(v, str):
            return [{"text": v[:300], "locator": base}]
        if isinstance(v, dict):
            return [{"text": _preview(v, 300), "locator": base}]
    return []


def find_domains(data):
    min_keys = {"min", "minimum", "lower", "lowerbound", "最小", "下限", "minvalue"}
    max_keys = {"max", "maximum", "upper", "upperbound", "最大", "上限", "maxvalue"}
    step_keys = {"step", "步长", "increment"}
    out = {}
    for ptr, node in walk_nodes(data, max_depth=10):
        if not isinstance(node, dict):
            continue
        norm = {norm_key(k): k for k in node}
        mk = next((norm[x] for x in min_keys if x in norm), None)
        xk = next((norm[x] for x in max_keys if x in norm), None)
        if not mk or not xk:
            continue
        try:
            mn, mx = float(node[mk]), float(node[xk])
        except (TypeError, ValueError):
            continue
        if mx <= mn:
            continue
        name = unescape(ptr.rsplit("/", 1)[-1]) or f"domain_{len(out) + 1}"
        step = None
        for sk in step_keys:
            if sk in norm and isinstance(node[norm[sk]], (int, float)):
                step = float(node[norm[sk]])
                break
        unit = ""
        for uk in ("unit", "单位", "units"):
            if uk in norm:
                unit = str(node[norm[uk]])[:20]
                break
        base = name
        i = 2
        while name in out:
            name = f"{base}_{i}"
            i += 1
        out[name] = {"min": mn, "max": mx, "step": step, "unit": unit, "locator": "case.json#" + ptr}
    return out


def _param_checks(params, domains):
    checks = {}
    for name, rec in params.items():
        dom = domains.get(name)
        if dom is None:
            for d, spec in domains.items():
                if norm_key(d) == norm_key(name):
                    dom = spec
                    break
        if not dom or not isinstance(rec.get("value"), (int, float)):
            continue
        v = float(rec["value"])
        ops = {"value": v, "min": dom["min"], "max": dom["max"]}
        res = dom["min"] <= v <= dom["max"]
        cid = "P-" + re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", name)
        checks[name] = {"id": cid, "kind": "parameter_range", "formula": "min <= value <= max",
                        "operands": ops, "result": bool(res), "unit": "bool",
                        "refs": [rec["locator"], dom["locator"]]}
    return checks


def _analyze_plan(ptr, entry, domains):
    rec = {"locator": "case.json#" + ptr, "keys": sorted(entry.keys()) if isinstance(entry, dict) else [],
           "version": _version(entry, ptr)}
    if not isinstance(entry, dict):
        rec["text"] = str(entry)[:300]
        return rec
    steps_key, steps_val = None, None
    for k, v in entry.items():
        if norm_key(k) in STEP_KEYS and isinstance(v, list):
            steps_key, steps_val = k, v
            break
    if steps_key is None:
        for k, v in entry.items():
            nk = norm_key(k)
            if isinstance(v, list) and any(t in nk for t in ("step", "步骤", "流程", "protocol", "procedure")):
                steps_key, steps_val = k, v
                break
    step_records = []
    if steps_key is not None:
        for i, s in enumerate(steps_val):
            loc = f"case.json#{ptr}/{esc(steps_key)}/{i}"
            if isinstance(s, dict):
                text = ""
                for tk in ("action", "step", "name", "title", "desc", "description", "操作", "内容", "步骤"):
                    if isinstance(s.get(tk), str):
                        text = s[tk]
                        break
                if not text:
                    strs = [v for v in s.values() if isinstance(v, str)]
                    text = strs[0] if strs else _preview(s, 120)
                step_records.append({"index": i + 1, "text": text[:300], "locator": loc, "fields": sorted(s.keys())})
            else:
                step_records.append({"index": i + 1, "text": str(s)[:300], "locator": loc, "fields": []})
    durations = _durations(entry, ptr)
    costs = _costs(entry, ptr)
    by_cur = {}
    for c in costs:
        by_cur.setdefault(c["currency"], []).append(c)
    samples = _samples(entry, ptr)
    params = {}
    for k, v in entry.items():
        if isinstance(v, dict) and norm_key(k) in ("parameters", "params", "参数", "参数设置", "parametersettings"):
            for pk, pv in v.items():
                if isinstance(pv, (int, float, str)) and not isinstance(pv, bool):
                    params[str(pk)] = {"value": pv, "locator": f"case.json#{ptr}/{esc(k)}/{esc(pk)}"}
    dmap = {norm_key(d): d for d in domains}
    for k, v in entry.items():
        nk = norm_key(k)
        if nk in dmap and isinstance(v, (int, float)) and not isinstance(v, bool):
            params.setdefault(dmap[nk], {"value": float(v), "locator": f"case.json#{ptr}/{esc(k)}"})
    rec.update({
        "steps": step_records,
        "durations": durations,
        "duration_total_minutes": round(sum(d["minutes"] for d in durations), 4) if durations else None,
        "costs": costs,
        "costs_by_currency": {c: round(sum(i["amount"] for i in items), 4) for c, items in by_cur.items()},
        "samples": samples,
        "parameters": params,
        "parameter_checks": _param_checks(params, domains),
        "assumptions": _list_field(entry, ptr, ASSUMPTION_KEYS),
        "variables": _list_field(entry, ptr, VARIABLE_KEYS),
        "controls": _list_field(entry, ptr, CONTROL_KEYS),
        "readouts": _list_field(entry, ptr, READOUT_KEYS),
        "resources": _list_field(entry, ptr, RESOURCE_KEYS),
    })
    return rec


def diff_values(a, b, ptr_a, ptr_b, out, limit=120):
    if len(out) >= limit:
        return
    if isinstance(a, (str, int, float, bool)) or isinstance(b, (str, int, float, bool)) or a is None or b is None \
            or type(a) is not type(b):
        if a != b:
            out.append({"a_locator": "case.json#" + ptr_a, "b_locator": "case.json#" + ptr_b,
                        "a": _preview(a, 160), "b": _preview(b, 160)})
        return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k in a and k in b:
                diff_values(a[k], b[k], f"{ptr_a}/{esc(k)}", f"{ptr_b}/{esc(k)}", out, limit)
            elif k in a:
                out.append({"a_locator": "case.json#" + ptr_a + "/" + esc(k), "b_locator": None,
                            "a": _preview(a[k], 160), "b": "<absent>"})
            else:
                out.append({"a_locator": None, "b_locator": "case.json#" + ptr_b + "/" + esc(k),
                            "a": "<absent>", "b": _preview(b[k], 160)})
    elif isinstance(a, list):
        for i in range(max(len(a), len(b))):
            if i < len(a) and i < len(b):
                diff_values(a[i], b[i], f"{ptr_a}/{i}", f"{ptr_b}/{i}", out, limit)
            elif i < len(a):
                out.append({"a_locator": "case.json#" + ptr_a + f"/{i}", "b_locator": None,
                            "a": _preview(a[i], 160), "b": "<absent>"})
            else:
                out.append({"a_locator": None, "b_locator": "case.json#" + ptr_b + f"/{i}",
                            "a": "<absent>", "b": _preview(b[i], 160)})


def find_section(data, words, max_depth=6):
    best = None
    for ptr, node in walk_nodes(data, max_depth=max_depth):
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            if norm_key(k) not in words:
                continue
            depth = ptr.count("/")
            if best is None or depth < best[0]:
                best = (depth, f"case.json#{ptr}/{esc(k)}", v)
    return (best[1], best[2]) if best else (None, None)


def _summarize(value, locator):
    if isinstance(value, dict):
        entries = [{"key": str(k), "locator": f"{locator}/{esc(k)}", "preview": _preview(v, 140)}
                   for k, v in list(value.items())[:30]]
        return {"locator": locator, "count": len(value), "entries": entries}
    if isinstance(value, list):
        entries = [{"index": i, "locator": f"{locator}/{i}", "preview": _preview(v, 140)}
                   for i, v in enumerate(value[:30])]
        return {"locator": locator, "count": len(value), "entries": entries}
    return {"locator": locator, "count": 1, "entries": [{"key": "", "locator": locator, "preview": _preview(value, 200)}]}


def _collect_facts(case, cap=200):
    out = []
    for ptr, leaf in _leaves(case):
        if len(out) >= cap:
            break
        if isinstance(leaf, str):
            for m in QTY_RE.finditer(leaf):
                out.append({"locator": "case.json#" + ptr, "kind": "quantity", "text": m.group(0),
                            "value": m.group(1), "unit": m.group(2)})
                if len(out) >= cap:
                    break
            for m in CUR_RE.finditer(leaf):
                out.append({"locator": "case.json#" + ptr, "kind": "currency", "text": m.group(0)})
                if len(out) >= cap:
                    break
            for m in DATE_RE.finditer(leaf):
                out.append({"locator": "case.json#" + ptr, "kind": "date", "text": m.group(0)})
                if len(out) >= cap:
                    break
    return out


def _silence_flags(plans, plan_container, extra_blob=""):
    flags = []
    if not plans:
        return flags
    blob = norm_ws(" ".join(_preview(p, 4000) for p in plans)).lower()
    for dim, words in SILENCE_KEYWORDS.items():
        hit_plan = any(w in blob for w in words)
        hit_extra = any(w in extra_blob for w in words)
        if not hit_plan and not hit_extra:
            flags.append({"dimension": dim, "keywords": list(words),
                          "note": "启发式关键词扫描：候选方案文本与合同/参数域章节内均未出现所列关键词，"
                                  "不等于确认缺失，需以阅读为准",
                          "locator": plan_container or "case.json#"})
    return flags


def _candidates(plans, domains):
    cands = []
    for p in plans:
        v = p.get("version") or "v"
        tag = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(v))
        durs = p.get("durations") or []
        if durs:
            ops = {f"d{i}": d["minutes"] for i, d in enumerate(durs)}
            cands.append({"id": f"T-{tag}", "kind": "duration_total",
                          "formula": " + ".join(ops.keys()),
                          "operands": ops, "result": round(sum(ops.values()), 4), "unit": "min",
                          "refs": [d["locator"] for d in durs],
                          "note": "按分钟归一后直接累加材料中出现的时长；未去重，需逐条核对 refs 并补等待/周转"})
        for cur, total in (p.get("costs_by_currency") or {}).items():
            items = [c for c in (p.get("costs") or []) if c["currency"] == cur]
            ops = {f"c{i}": it["amount"] for i, it in enumerate(items)}
            cands.append({"id": f"C-{tag}-{cur}", "kind": "cost_total",
                          "formula": " + ".join(ops.keys()),
                          "operands": ops, "result": round(sum(ops.values()), 4), "unit": cur,
                          "refs": [it["locator"] for it in items], "note": "同币种直接累加，未含未列明费用"})
        for name, chk in (p.get("parameter_checks") or {}).items():
            cands.append({"id": f"P-{tag}-{name}", "kind": "parameter_range", "formula": chk["formula"],
                          "operands": chk["operands"], "result": chk["result"], "unit": "bool",
                          "refs": chk["refs"]})
        smp = p.get("samples") or {}
        req = smp.get("nrequired") or smp.get("requiredn") or smp.get("n")
        avl = smp.get("navailable") or smp.get("availablen")
        if req and avl:
            ops = {"available": avl["value"], "required": req["value"]}
            cands.append({"id": f"S-{tag}", "kind": "sample_sufficiency",
                          "formula": "available - required", "operands": ops,
                          "result": round(ops["available"] - ops["required"], 4), "unit": "n",
                          "refs": [avl["locator"], req["locator"]],
                          "note": "仅比较两个声明数值；是否含复孔/损耗/分组倍数需另核"})
    return cands


def build_casepack(input_dir, work):
    files = discover(input_dir)
    case, case_rel, case_err = load_case(input_dir, files)
    contract, contract_src = contract_fields(case)
    case_id = _case_id(case, files)
    plans_raw = find_plans(case)
    domains = find_domains(case)
    plans = []
    plan_container = None
    if plans_raw:
        plan_container = plans_raw["container"]
        for ptr, entry in plans_raw["entries"]:
            if isinstance(entry, dict):
                plans.append(_analyze_plan(ptr, entry, domains))
    diffs = []
    if plans_raw and len(plans_raw["entries"]) >= 2:
        pairs = []
        for i in range(1, len(plans_raw["entries"])):
            pa, va = plans_raw["entries"][i - 1]
            pb, vb = plans_raw["entries"][i]
            diff = []
            if isinstance(va, dict) and isinstance(vb, dict):
                diff_values(va, vb, pa, pb, diff, 120)
            pairs.append({"prev": _version(va, pa), "curr": _version(vb, pb),
                          "prev_locator": "case.json#" + pa, "curr_locator": "case.json#" + pb,
                          "changed": len(diff), "changes": diff})
        diffs = pairs
    contracts = {}
    for sec, words in SECTION_WORDS.items():
        loc, val = find_section(case, words)
        if val is not None:
            contracts[sec] = _summarize(val, loc)
    gaps = []
    if not case_rel:
        gaps.append({"kind": "missing", "area": "case_file", "detail": "未发现 case.json/case.jsonl"})
    if not plans_raw:
        gaps.append({"kind": "missing", "area": "plans", "detail": "未发现候选方案（plans/candidates/versions）"})
    elif len(plans_raw["entries"]) < 2:
        gaps.append({"kind": "missing", "area": "plans", "detail": "版本化方案少于 2 版，无法做版本对比"})
    if not contract:
        gaps.append({"kind": "missing", "area": "output_contract",
                     "detail": "case.json 未声明 output_contract，回退到 skill 默认契约"})
    if "ethics" not in contracts:
        gaps.append({"kind": "missing", "area": "ethics_safety", "detail": "未发现 ethics/approval 章节"})
    if "samples" not in contracts:
        gaps.append({"kind": "missing", "area": "samples", "detail": "未发现样本章节"})
    if not domains:
        gaps.append({"kind": "missing", "area": "parameter_ranges", "detail": "未发现参数域（min/max）"})
    title = None
    for k in ("title", "标题", "name"):
        if isinstance(case.get(k), str):
            title = case[k]
            break
    rq = None
    for k in ("research_question", "objective", "goal", "研究目标", "研究问题"):
        if isinstance(case.get(k), str):
            rq = case[k]
            break
    extra_blob = norm_ws(" ".join(
        ["parameter_domains", "contracts"] + list(contracts.keys()) + list(domains.keys()) +
        [_preview(v, 800) for v in contracts.values()]
    )).lower()
    return {
        "generated_by": "main.py",
        "case_file": case_rel,
        "case_id": case_id,
        "title": title,
        "research_question": rq,
        "case_keys": sorted(case.keys()) if isinstance(case, dict) else [],
        "files": files,
        "plans": plans,
        "plan_container": plan_container,
        "plan_source": plans_raw["source"] if plans_raw else None,
        "plan_diff": diffs,
        "domains": domains,
        "contracts": contracts,
        "facts": _collect_facts(case),
        "candidates": _candidates(plans, domains),
        "silence_flags": _silence_flags(plans, plan_container, extra_blob),
        "gaps": gaps,
        "warnings": case_err,
        "contract_fields": contract,
        "contract_source": contract_src,
        "limits": {"max_text_bytes": MAX_TEXT, "facts_capped": 200},
    }


def empty_base():
    return {
        "challenge_id": None,
        "task_title": None,
        "overall_conclusion": {"verdict": None, "summary": None, "conditions": [], "blocks": [],
                               "recommended_plan": None, "notes": []},
        "experiment_plan": {"research_question": None, "assumptions": [], "variables": [],
                            "controls": [], "steps": [], "parameters": [], "resources": [],
                            "interpretation_criteria": []},
        "review": {d: [] for d in DIMS},
        "findings_index": [],
        "plan_comparison": [],
        "evidence_and_risks": {"evidence": [], "risks": [], "exception_handling": []},
        "confirmation": {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []},
        "arithmetic_ledger": [],
        "evidence_summary": {"items": 0, "verified": 0, "unverified": 0, "unique_locators": 0,
                             "map": "evidence_map.json"},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "contract_binding": {},
        "change_log": [],
        "runtime": {"started_at": None, "seconds": 0.0, "requests": None, "model": None},
        "artifacts": [],
    }


def write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return True, ""
    except (OSError, TypeError, ValueError) as exc:
        return False, str(exc)


def _skeleton_result(casepack, contract, case_id):
    result = {}
    for f, hint in (contract or {}).items():
        result[f] = default_for_hint(hint)
    base = empty_base()
    for k, v in base.items():
        result.setdefault(k, v)
    result["case_id"] = case_id
    no_material = (not casepack.get("case_file")) and not casepack.get("plans")
    result["status"] = "insufficient_evidence" if no_material else "partial"
    result["task_title"] = casepack.get("title")
    result["review_scope"] = {
        "plans": [p["version"] for p in casepack["plans"]],
        "dimensions": list(DIMS),
        "materials": [s["rel"] for s in casepack["files"]],
        "not_covered": (["未发现可读的案例/方案材料"] if no_material
                        else ["七类审查尚未逐条完成（S0 骨架）"]),
    }
    result["review"] = {d: [] for d in DIMS}
    result["sources"] = [{"locator": f["rel"], "kind": f["kind"], "bytes": f["bytes"],
                          "sha1_8": f["sha1_8"], "used_by": []} for f in casepack["files"]]
    result["uncertainty"] = [{"kind": "skeleton",
                              "detail": ("S0：未发现可读输入，材料不足"
                                         if no_material
                                         else "S0 骨架：仅完成输入发现与索引，审查结论尚未写入")}]
    result["limitations"] = list(casepack["gaps"])
    result["degradations"] = [w for w in casepack["warnings"] if w]
    result["contract_binding"] = {"source": casepack["contract_source"],
                                  "fields": list((contract or {}).keys()),
                                  "unfilled": list((contract or {}).keys())}
    result["change_log"] = [{"step": "S0", "action": "写 partial 骨架 + casepack",
                             "detail": f"发现 {len(casepack['files'])} 个输入文件、{len(casepack['plans'])} 个方案版本"}]
    result["runtime"] = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": 0.0,
                         "requests": None, "model": None}
    result["artifacts"] = [{"path": "/app/output/result.json", "kind": "json", "note": "partial 骨架"}]
    return result


def grep_inputs(input_dir, pattern, work, limit=40):
    pat = pattern.lower()
    hits = []
    for f in discover(input_dir):
        if len(hits) >= limit:
            break
        if f["ext"] in JSON_EXTS:
            data, _ = load_json(f["path"])
            if data is None:
                continue
            for ptr, node in walk_nodes(data):
                if isinstance(node, (str, int, float)) and pat in str(node).lower():
                    hits.append({"locator": f"{f['rel']}#{ptr}", "preview": _preview(node, 160)})
                if len(hits) >= limit:
                    break
        elif f["ext"] in TEXT_EXTS:
            text, _ = read_text(f["path"])
            if not text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pat in line.lower():
                    hits.append({"locator": f"{f['rel']}#L{i}", "preview": line.strip()[:200]})
                if len(hits) >= limit:
                    break
        elif f["ext"] in PDF_EXTS:
            pages = pdf_pages(f["path"], work)
            if pages:
                for i, page in enumerate(pages, 1):
                    if pat in page.lower():
                        hits.append({"locator": f"{f['rel']}#p{i}", "preview": _preview(page, 200)})
                    if len(hits) >= limit:
                        break
    return hits[:limit]


def _run_init(args):
    os.makedirs(args.work, exist_ok=True)
    casepack = build_casepack(args.input, args.work)
    contract = casepack["contract_fields"]
    case_id = casepack["case_id"]
    result = _skeleton_result(casepack, contract, case_id)
    out_result = os.path.join(args.output, "result.json")
    if os.path.isfile(out_result) and not args.force:
        try:
            with open(out_result, encoding="utf-8") as fh:
                old = json.load(fh)
            if old.get("evidence_summary", {}).get("items", 0) or old.get("findings_index"):
                out_result = os.path.join(args.output, "result.skeleton.json")
                print(f"[init] 已有已组装的 result.json，骨架另存为 {out_result}；如需覆盖加 --force")
        except (OSError, ValueError):
            pass
    ok, msg = write_json(out_result, result)
    if not ok:
        print(f"[init] 写盘失败 {out_result}: {msg}，降级到最小骨架（退出码仍为 0）")
        minimal = {"case_id": case_id, "status": "input_error", "errors": [msg], "uncertainty": []}
        write_json(os.path.join(args.output, "result.json"), minimal)
    ok2, msg2 = write_json(os.path.join(args.work, "casepack.json"), casepack)
    inv = {"input_dir": args.input, "files": casepack["files"], "case_file": casepack["case_file"],
           "warnings": casepack["warnings"]}
    write_json(os.path.join(args.work, "input_inventory.json"), inv)
    write_json(os.path.join(args.work, "run_state.json"),
               {"started_at": result["runtime"]["started_at"], "epoch": time.time()})
    print(f"[init] case_id={case_id} files={len(casepack['files'])} plans={len(casepack['plans'])} "
          f"contract_fields={len(contract)} gaps={len(casepack['gaps'])}")
    print(f"[init] result -> {out_result}")
    print(f"[init] casepack -> {os.path.join(args.work, 'casepack.json')}")
    if not ok2:
        print(f"[init] 警告: casepack 写盘失败: {msg2}")
    return 0


def _run_show(args):
    res = resolve_locator(args.locator, args.input, args.work)
    if not res["ok"]:
        print(f"[show] 无法解析 {args.locator}: {res['kind']} {res['detail']}")
        return 1
    text = res["text"]
    if args.max_chars:
        text = text[:args.max_chars]
    print(f"--- {args.locator} ({res['kind']}, {len(res['text'])} chars) ---")
    print(text)
    return 0


def _run_grep(args):
    hits = grep_inputs(args.input, args.pattern, args.work, args.limit)
    for h in hits:
        print(f"{h['locator']}\t{h['preview']}")
    print(f"[grep] {len(hits)} 命中（限制 {args.limit}）")
    return 0


def _run_check(args):
    fails = 0
    for loc in args.locators:
        res = resolve_locator(loc, args.input, args.work)
        if res["ok"]:
            print(f"OK   {loc} ({res['kind']})")
        else:
            fails += 1
            print(f"FAIL {loc} -> {res['kind']}: {res['detail']}")
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="实验方案可行性审查：输入发现与结果骨架")
    ap.add_argument("cmd", nargs="?", default="init", choices=["init", "show", "grep", "check"])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--locator", default="")
    ap.add_argument("--pattern", default="")
    ap.add_argument("--max-chars", type=int, default=0)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--locators", nargs="*", default=[])
    args = ap.parse_args(argv)
    if args.cmd == "init":
        return _run_init(args)
    if args.cmd == "show":
        return _run_show(args)
    if args.cmd == "grep":
        return _run_grep(args)
    return _run_check(args)


if __name__ == "__main__":
    sys.exit(main())
