#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest.py — 输入摄取、契约绑定与结果骨架落盘（鲁棒性优先）。

职责：
* 在审查启动的最初时刻扫描 /input，立即把一份结构合法、内容诚实的 partial
  result.json 写到 /app/output/result.json，保证后续任何中途异常都不会留下空产物。
* 容错读取 BOM / UTF-16 / GB18030 / 坏 JSON / 截断 JSON / JSONL / 超大文件 / 二进制
  文件 / 符号链接 / 输出不可写，全部降级并在 result 的 errors/degradations 中记录。
* 从 case.json 中绑定 output_contract（原样回填全部字段），识别 case_id、研究目标、
  候选方案、资源合同、参数域、样本与伦理信息，产出供模型直接阅读的 brief.json。

用法：
    python3 scripts/ingest.py --input /input --output /app/output --work /app/work
    python3 scripts/ingest.py --input DIR --output DIR --work DIR --max-bytes 2097152

确定性：同一输入两次运行产生相同的 run_id 与骨架（不写墙钟时间戳）。
仅依赖 Python 标准库，Python >= 3.8。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

SKILL_NAME = "experiment-feasibility-audit"
SKILL_VERSION = "2.1.0"
SCHEMA_VERSION = "2.1"
MAX_FILE_BYTES = 2 * 1024 * 1024      # 单文本材料上限 2MB
MAX_FILES = 400
MAX_DEPTH = 6          # 目录深度上限
WALK_DEPTH = 12        # JSON 对象遍历深度上限
HEAD_RATIO = 0.65
MAX_WALK_NODES = 20000

TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml",
            ".tex", ".rst", ".log", ".ini", ".cfg", ".py", ".bib", ".tsv"}
BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".xlsx",
              ".xls", ".docx", ".pptx", ".zip", ".gz", ".bz2", ".7z", ".bin"}

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
SMART_MAP = str.maketrans({
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
    "\u2013": "-", "\u2014": "-", "\u3000": " ",
})

# 与评审口径一致的状态枚举。
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"


def norm_key(k):
    """键名归一化：小写、非字母数字与中文统一为下划线。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", str(k).strip().lower()).strip("_")


def base_result():
    """结果契约骨架。所有关键字段均先占位，缺省为 null/[] 而非省略。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
        "case_id": None,
        "challenge_id": None,
        "task_title": None,
        "status": STATUS_PARTIAL,
        "status_reason": "S0 骨架落盘，等待七维审查",
        "research_question": None,
        "run_id": None,
        "case_fingerprint": None,
        "input_mode": "none",
        "budget": {"seconds": 900, "model_requests": 64, "tokens_per_response": 16384},
        "output_contract": {"source": None, "spec": None, "echoed": [],
                            "kept_builtin": [], "unfilled": []},
        "experiment_plan": {
            "objective": None, "hypotheses": [], "assumptions": [], "variables": [],
            "controls": [], "steps": [], "parameters": [], "resources": [],
            "interpretation_criteria": [],
        },
        "review": {
            "assumptions_variables": [], "controls": [], "equipment_materials": [],
            "time_cost": [], "parameter_ranges": [], "risks_exceptions": [],
            "ethics_safety": [],
        },
        "plan_comparison": {"criteria": [], "plans": [], "ranking": [],
                            "recommended": None, "tie_break": None, "notes": []},
        "evidence_and_risks": {"evidence": [], "risks": [], "exception_handling": []},
        "confirmation": {"safety": [], "ethics": [], "equipment_availability": [],
                         "expert_confirmation": []},
        "overall_conclusion": {"verdict": None, "summary": None, "conditions": [],
                               "blockers": [], "notes": []},
        "sources": [], "uncertainty": [], "pending_human": [], "limitations": [],
        "errors": [], "degradations": [], "change_log": [],
        "runtime": {"started_at": None, "seconds": None, "model_requests_used": 0,
                    "budget_seconds": 900, "budget_requests": 64},
        "input_brief": {"inventory": None, "brief": None},
        "artifacts": [{"path": "result.json", "kind": "json", "status": "skeleton"},
                      {"path": "review_report.md", "kind": "markdown",
                       "status": "pending"}],
    }


# --------------------------------------------------------------------------- #
# 文件系统发现与容错读取
# --------------------------------------------------------------------------- #

def classify(rel, ext):
    name = os.path.basename(rel).lower()
    if name in ("case.json", "case.jsonl") or re.match(r"^case[._-].*\.jsonl?$", name):
        return "case"
    if name in ("task.md", "readme.md", "readme.txt"):
        return "task"
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        return "image"
    if ext in (".csv", ".tsv", ".xlsx", ".xls"):
        return "table"
    if rel.split("/", 1)[0] in ("materials", "sources") or "/materials/" in rel or "/sources/" in rel:
        if ext in (".json", ".jsonl"):
            return "material_json"
        return "material" if ext in TEXT_EXT else "material_binary"
    if ext in TEXT_EXT:
        return "text"
    return "other"


def walk_tree(root, max_files, max_depth):
    files, notes = [], []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        kept = []
        for d in dirnames:
            p = os.path.join(dirpath, d)
            if os.path.islink(p):
                notes.append({"code": "SYMLINK_SKIPPED",
                              "path": os.path.relpath(p, root).replace("\\", "/")})
            else:
                kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if os.path.islink(p):
                notes.append({"code": "SYMLINK_SKIPPED", "path": rel})
                continue
            try:
                size = os.path.getsize(p)
            except OSError as exc:
                notes.append({"code": "STAT_FAILED", "path": rel,
                              "detail": str(exc)})
                continue
            if len(files) >= max_files:
                notes.append({"code": "FILE_LIMIT_REACHED", "limit": max_files})
                return files, notes
            ext = os.path.splitext(name)[1].lower()
            files.append({"relpath": rel, "abspath": p, "size": size, "ext": ext,
                          "role": classify(rel, ext)})
    return files, notes


def read_bounded(path, max_bytes):
    """读取受控文件：<=max 全读；>max 按 head/tail 分段读取，绝不整体载入。"""
    try:
        total = os.path.getsize(path)
    except OSError as exc:
        return None, False, 0, [{"code": "READ_FAILED", "path": path,
                                 "detail": str(exc)}]
    try:
        if total <= max_bytes:
            with open(path, "rb") as fh:
                return fh.read(), False, total, []
        head_n = int(max_bytes * HEAD_RATIO)
        tail_n = max_bytes - head_n
        with open(path, "rb") as fh:
            head = fh.read(head_n)
            fh.seek(max(0, total - tail_n))
            tail = fh.read(tail_n)
        return head, True, total, [{"code": "TRUNCATED_READ",
                                    "path": path, "total_bytes": total,
                                    "head_bytes": len(head),
                                    "tail_bytes": len(tail)}]
    except OSError as exc:
        return None, False, total, [{"code": "READ_FAILED", "path": path,
                                     "detail": str(exc)}]


def looks_binary(data):
    if not data:
        return False
    if sniff_bom(data) in ("utf-16-le", "utf-16-be", "utf-32"):
        return False
    sample = data[:8192]
    return sample.count(0) / max(1, len(sample)) > 0.02


def sniff_bom(data):
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith(b"\xff\xfe\x00\x00") or data.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32"
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be"
    return None


def decode_text(data):
    """多编码链解码，绝不因编码问题抛异常。"""
    bom = sniff_bom(data)
    chain = [bom] if bom else []
    chain += ["utf-8", "gb18030", "big5", "cp1252"]
    if bom is None and data[:4096].count(0) / max(1, len(data[:4096])) > 0.2:
        chain = ["utf-16-le", "utf-16-be"] + chain
    for enc in chain:
        if enc is None:
            continue
        try:
            text = data.decode(enc)
            if enc == "utf-8" and "\x00" in text[:4096]:
                continue
            return text, enc, []
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8-replace", \
        [{"code": "DECODE_REPLACED", "detail": "fallback utf-8 with replacement"}]


# --------------------------------------------------------------------------- #
# 坏 JSON / 截断 JSON 修复
# --------------------------------------------------------------------------- #

def _scan_string_stack(s):
    stack = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "[{":
                stack.append("]" if ch == "[" else "}")
            elif ch in "]}":
                if stack:
                    stack.pop()
    return stack, in_str


def close_truncated(s):
    stack, in_str = _scan_string_stack(s)
    out = s
    if in_str:
        out += '"'
    out = re.sub(r"[,:]\s*$", "", out)
    out += "".join(reversed(stack))
    return TRAILING_COMMA_RE.sub(r"\1", out)


def cut_tail(s):
    """截去最后一个位于容器内的元素（保留到最后一个深度>=1的逗号）。"""
    in_str = False
    esc = False
    depth = 0
    last = -1
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth = max(0, depth - 1)
            elif ch == "," and depth >= 1:
                last = i
    return s[:last] if last > 0 else s


def repair_variants(text):
    variants = []
    v1 = close_truncated(text)
    variants.append(("close", v1))
    v2 = close_truncated(cut_tail(text))
    if v2 != v1:
        variants.append(("cut_tail", v2))
    v3 = re.sub(r'"[^"]*"\s*:\s*$', "", text)
    v3 = close_truncated(v3)
    if v3 not in (v1, v2):
        variants.append(("drop_key", v3))
    return variants


def parse_json_document(text):
    """严格 -> 规范化 -> 截断修复 -> JSONL 的解析链。返回 (obj, meta)。"""
    meta = {"mode": "none", "warnings": []}
    if text is None:
        meta["warnings"].append("EMPTY_TEXT")
        return None, meta
    s = text.strip().lstrip("\ufeff").strip()
    if not s:
        meta["warnings"].append("EMPTY_TEXT")
        return None, meta
    first_err = ""
    try:
        return json.loads(s), {"mode": "strict", "warnings": []}
    except Exception as exc:  # noqa: BLE001
        first_err = str(exc)[:160]

    cleaned = CONTROL_RE.sub(" ", s)
    try:
        return json.loads(cleaned), {"mode": "control-stripped",
                                     "warnings": ["JSON_CONTROL_CHARS"]}
    except Exception:  # noqa: BLE001
        pass

    norm = s.translate(SMART_MAP)
    norm = TRAILING_COMMA_RE.sub(r"\1", norm)
    try:
        return json.loads(norm), {"mode": "normalized",
                                  "warnings": ["JSON_NORMALIZED"]}
    except Exception:  # noqa: BLE001
        pass

    for tag, variant in repair_variants(norm):
        try:
            return json.loads(variant), {"mode": "repaired:" + tag,
                                         "warnings": ["JSON_REPAIRED",
                                                      "PARSE_ERROR:" + first_err]}
        except Exception:  # noqa: BLE001
            continue

    objs = []
    for line in norm.splitlines():
        line = line.strip().rstrip(",")
        if not line or line[0] not in "{[":
            continue
        try:
            o = json.loads(TRAILING_COMMA_RE.sub(r"\1", line))
            objs.append(o)
        except Exception:  # noqa: BLE001
            continue
    if len(objs) == 1:
        return objs[0], {"mode": "jsonl", "warnings": ["JSONL_SINGLE",
                                                       "PARSE_ERROR:" + first_err]}
    if len(objs) > 1:
        return {"records": objs, "_jsonl_count": len(objs)}, \
            {"mode": "jsonl", "warnings": ["JSONL_MULTI", "PARSE_ERROR:" + first_err]}
    meta["warnings"].append("PARSE_FAILED:" + first_err)
    return None, meta


# --------------------------------------------------------------------------- #
# 对象遍历与结构化信息提取
# --------------------------------------------------------------------------- #

def walk_obj(obj, path="", depth=0, budget=None):
    """生成 (path, key, value)。path 形如 plans[1].steps[2]；预算防爆栈。"""
    if budget is None:
        budget = [0]
    if budget[0] > MAX_WALK_NODES or depth > WALK_DEPTH:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            budget[0] += 1
            p = f"{path}.{k}" if path else str(k)
            yield p, k, v
            yield from walk_obj(v, p, depth + 1, budget)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:60]):
            budget[0] += 1
            p = f"{path}[{i}]"
            yield p, str(i), v
            yield from walk_obj(v, p, depth + 1, budget)


def _extract_case_id(case):
    for key in ("case_id", "id", "challenge_id", "caseId", "armiId", "case_uid"):
        if isinstance(case, dict) and key in case and isinstance(case[key], (str, int, float)):
            v = str(case[key]).strip()
            if v:
                return v
    for path, key, val in walk_obj(case):
        if norm_key(key) in ("case_id", "caseid", "challenge_id") and \
                isinstance(val, (str, int, float)):
            return str(val).strip()
    return None


PLAN_KEYS = {"plans", "plan_versions", "candidates", "designs", "options",
             "arms", "protocols", "versions", "方案", "候选方案", "实验方案",
             "计划", "候选"}
FIELD_HINTS = {"id", "name", "title", "steps", "step", "procedure", "duration",
               "time", "cost", "price", "budget", "samples", "sample_size",
               "parameters", "params", "controls", "equipment", "materials",
               "risks", "ethics", "variables", "hypotheses"}


def find_plans(case):
    """发现候选方案（1-3 版）及其所在路径。返回 (score, path, plans, via)。"""
    best = None
    for path, key, val in walk_obj(case):
        via = "list"
        items = None
        if isinstance(val, list) and 1 <= len(val) <= 12 and \
                all(isinstance(x, dict) for x in val):
            items = val
        elif isinstance(val, dict) and 1 <= len(val) <= 12 and \
                all(isinstance(v, dict) for v in val.values()):
            items = [dict(v, **{"_plan_key": k}) for k, v in val.items()]
            via = "dict"
        if not items:
            continue
        nk = norm_key(key)
        score = 0
        if nk in PLAN_KEYS:
            score += 6
        if any(t in nk for t in ("plan", "candidate", "design", "version", "option",
                                 "arm", "protocol", "方案", "候选")):
            score += 3
        keys = set()
        for it in items:
            keys |= {norm_key(k) for k in it if not k.startswith("_plan_key")}
        score += min(4, len(keys & FIELD_HINTS))
        score += 1 if len(items) >= 2 else 0
        if best is None or score > best[0]:
            best = (score, path, items, via)
    return best


def find_goal(case):
    """在常见键名中寻找研究目标文本（优先顶层，长于 8 字符）。"""
    for key in ("research_question", "goal", "objective", "research_goal",
                "question", "aim", "研究目标", "目标", "研究问题"):
        if isinstance(case, dict) and key in case and isinstance(case[key], str) \
                and len(case[key].strip()) >= 8:
            return case[key].strip(), key
    for path, key, val in walk_obj(case):
        if norm_key(key) in ("research_question", "goal", "objective",
                             "research_goal", "研究目标") and \
                isinstance(val, str) and len(val.strip()) >= 8:
            return val.strip(), path
    return None, None


def find_contract_spec(case):
    for path, key, val in walk_obj(case):
        if re.search(r"output[_-]?contract", norm_key(key)) and isinstance(val, dict):
            return val, path
    return None, None


def _clip(value, limit=1500):
    s = json.dumps(value, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + f"…[+{len(s) - limit} chars]"


def collect_by_regex(case, pattern, limit=60):
    out = []
    for path, key, val in walk_obj(case):
        if pattern.search(norm_key(key)):
            out.append({"locator": path, "key": str(key), "value": _clip(val)})
            if len(out) >= limit:
                break
    return out


CONTRACT_RE = re.compile(r"contract|price|quote|booking|vendor|supplier|lead_?time"
                         r"|tariff|budget|价格|合同|预约|货期|报价|采购")
DOMAIN_RE = re.compile(r"domain|grid|levels|range|param|tolerance|spec|水平|范围|参数|区间")
SAMPLE_RE = re.compile(r"sample|specimen|subject|cohort|stock|样本|受试|队列|库存|例数")
ETHICS_RE = re.compile(r"ethic|irb|iacuc|approval|consent|safety|biosafety|hazard"
                       r"|伦理|审批|知情|安全|危险|动物|人体")


def plan_summary(plans, via):
    out = []
    for i, p in enumerate(plans):
        pid = p.get("id") or p.get("plan_id") or p.get("name") or \
            p.get("_plan_key") or f"P{i + 1}"
        label = p.get("title") or p.get("name") or p.get("label") or pid
        if isinstance(label, (dict, list)):
            label = str(pid)
        step_count = None
        for k in ("steps", "procedures", "workflow", "tasks", "activities"):
            if isinstance(p.get(k), list):
                step_count = len(p[k])
                break
        out.append({"plan_id": str(pid), "label": str(label),
                    "step_count": step_count,
                    "keys": sorted({norm_key(k) for k in p})[:30]})
    return out


# --------------------------------------------------------------------------- #
# 契约绑定
# --------------------------------------------------------------------------- #

def field_default(spec):
    if isinstance(spec, dict):
        if "default" in spec:
            return copy.deepcopy(spec["default"])
        if "properties" in spec and isinstance(spec["properties"], dict):
            return {k: field_default(v) for k, v in spec["properties"].items()}
        t = str(spec.get("type", "")).strip().lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        if t in ("number", "integer", "float", "int", "boolean", "bool", "null"):
            return None
        return ""
    if isinstance(spec, (list, tuple)):
        return []
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        return None
    if isinstance(spec, str):
        s = spec.strip().lower()
        if any(w in s for w in ("array", "list", "列表", "数组", "[]")):
            return []
        if any(w in s for w in ("object", "dict", "map", "对象")):
            return {}
        if any(w in s for w in ("string", "text", "str", "字符串", "文本", "markdown")):
            return ""
        if any(w in s for w in ("number", "integer", "float", "int", "数值", "数字")):
            return None
        if any(w in s for w in ("boolean", "bool", "布尔")):
            return None
        return ""
    return None


def bind_contract(case, result):
    spec, loc = find_contract_spec(case)
    if spec is None:
        result["output_contract"] = {"source": None, "spec": None, "echoed": [],
                                     "kept_builtin": [], "unfilled": []}
        return
    fields = spec
    if "fields" in spec and isinstance(spec["fields"], dict):
        fields = spec["fields"]
    elif "properties" in spec and isinstance(spec["properties"], dict):
        fields = spec["properties"]
    echoed, kept, unfilled = [], [], []
    for k, v in fields.items():
        if k in result and k != "output_contract":
            kept.append(k)
            continue
        result[k] = field_default(v)
        echoed.append(k)
        unfilled.append(k)
    result["output_contract"] = {"source": "case.json#" + loc, "spec": fields,
                                 "echoed": echoed, "kept_builtin": kept,
                                 "unfilled": unfilled}


# --------------------------------------------------------------------------- #
# 写入与骨架落盘
# --------------------------------------------------------------------------- #

def write_json(path, obj):
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        sys.stderr.write(f"[ingest] write failed: {path}: {exc}\n")
        return False


def sha256_file(path, truncated):
    try:
        size = os.path.getsize(path)
        h = hashlib.sha256()
        if truncated:
            with open(path, "rb") as fh:
                h.update(fh.read(512 * 1024))
            with open(path, "rb") as fh:
                fh.seek(max(0, size - 512 * 1024))
                h.update(fh.read(512 * 1024))
            h.update(str(size).encode())
            return h.hexdigest(), "head_tail"
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), "full"
    except OSError:
        return "unreadable", "error"


def assess_status(case, readable_chars, gaps, n_files):
    if case is None:
        if n_files == 0:
            return STATUS_INSUFFICIENT, "输入目录为空，无任何可读材料"
        if readable_chars < 80:
            return STATUS_INPUT_ERROR, "未发现可解析的 case 输入且无任何可读文本"
        return STATUS_INSUFFICIENT, "存在文本但无可用 JSON 案例对象，材料不足以审查"
    if gaps.get("plans") and gaps.get("goal_missing"):
        return STATUS_INSUFFICIENT, "缺少候选方案与研究目标，材料不足以审查"
    return STATUS_PARTIAL, "骨架已落盘，等待七维审查与算术校验"


def pdf_to_text(path, work_dir, rel):
    exe = shutil.which("pdftotext")
    if not exe:
        return None, [{"code": "PDF_TOOL_UNAVAILABLE",
                       "detail": "pdftotext not found in PATH"}]
    safe = re.sub(r"[^\w.\-]+", "_", rel) + ".txt"
    out = os.path.join(work_dir, "pdf", safe)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        subprocess.run([exe, "-layout", "-enc", "UTF-8", path, out],
                       check=True, timeout=60, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(out, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        pages = text.count("\f") + 1 if text else 0
        return {"text_path": out, "chars": len(text), "pages": pages}, []
    except Exception as exc:  # noqa: BLE001
        return None, [{"code": "PDF_EXTRACT_FAILED", "path": rel,
                       "detail": type(exc).__name__ + ":" + str(exc)[:120]}]


def _fix_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv=None):
    _fix_stdio()
    ap = argparse.ArgumentParser(description="实验方案可开工性审计 — 输入摄取与骨架落盘")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--max-bytes", type=int, default=MAX_FILE_BYTES)
    args = ap.parse_args(argv)

    result = base_result()
    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.output)
    work_dir = os.path.abspath(args.work)

    if not os.path.isdir(in_dir):
        result["status"] = STATUS_INPUT_ERROR
        result["status_reason"] = "输入目录不存在: " + args.input
        result["errors"].append({"code": "INPUT_DIR_MISSING", "path": args.input})
        result["change_log"].append({"seq": 1, "step": "S0",
                                     "action": "输入目录缺失，写入 input_error 骨架",
                                     "reason": "输入目录不存在"})
        ok = write_json(os.path.join(out_dir, "result.json"), result)
        if not ok:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0

    files, notes = walk_tree(in_dir, MAX_FILES, MAX_DEPTH)
    readable_chars = 0
    case_candidates = []
    pdf_notes = []
    for item in files:
        rel = item["relpath"]
        data, truncated, total, warns = read_bounded(item["abspath"], args.max_bytes)
        item["truncated"] = truncated
        item["sha256"], item["hash_mode"] = sha256_file(item["abspath"], truncated)
        item["decode"] = None
        item["parse_mode"] = None
        item["status"] = "ok"
        for w in warns:
            w["relpath"] = rel
            result["degradations"].append(dict(w, code=w.get("code", "READ_WARN")))
        if item["role"] == "pdf":
            info, pnotes = pdf_to_text(item["abspath"], work_dir, rel)
            item["pdf"] = info
            pdf_notes += pnotes
            for n in pnotes:
                result["degradations"].append(dict(n, relpath=rel))
            if info:
                readable_chars += info.get("chars", 0)
            continue
        if data is None:
            item["status"] = "unreadable"
            continue
        if item["role"] in ("image", "other") or (item["role"] == "material_binary"):
            item["status"] = "binary"
            continue
        if item["role"] not in ("case", "task", "text", "material", "material_json",
                                "table") and item["ext"] in BINARY_EXT:
            item["status"] = "binary"
            continue
        if looks_binary(data):
            item["status"] = "binary"
            result["degradations"].append({"code": "BINARY_SKIPPED", "relpath": rel,
                                           "note": "NUL 密度过高，按二进制跳过"})
            continue
        text, enc, warns2 = decode_text(data)
        item["decode"] = enc
        for w in warns2:
            result["degradations"].append(dict(w, relpath=rel))
        readable_chars += len(text)
        if item["role"] == "case" or (item["ext"] in (".json", ".jsonl") and
                                      item["role"] in ("text", "material_json")):
            obj, meta = parse_json_document(text)
            item["parse_mode"] = meta["mode"]
            for w in meta["warnings"]:
                if "PARSE" in w or "JSON" in w:
                    result["degradations"].append({"code": w.split(":")[0],
                                                   "relpath": rel,
                                                   "detail": w})
            if obj is not None:
                case_candidates.append((item, obj, truncated))
        if item["truncated"]:
            result["degradations"].append({"code": "TRUNCATED_READ", "relpath": rel,
                                           "total_bytes": total,
                                           "strategy": "head_tail"})

    # 选择最像 case 的候选：优先 role=case，其次 plans 命中数最多的。
    chosen = None
    case_obj = None
    case_truncated = False
    case_meta = None
    for item, obj, truncated in case_candidates:
        score = 2 if item["role"] == "case" else 1
        fp = find_plans(obj)
        if fp and fp[0] > 0:
            score += fp[0]
        if chosen is None or score > chosen[0]:
            chosen = (score, item, obj, truncated)
    if chosen:
        _, case_item, case_obj, case_truncated = chosen
        case_meta = {"relpath": case_item["relpath"], "parse_mode":
                     case_item["parse_mode"], "truncated": case_truncated}

    # 指纹：由文件内容决定，保证同输入同 run_id。
    fp_lines = sorted(f"{f['relpath']}:{f['sha256']}:{f['hash_mode']}" for f in files)
    fingerprint = hashlib.sha256("\n".join(fp_lines).encode("utf-8")).hexdigest()
    if not files:
        fingerprint = "empty_input"
    result["case_fingerprint"] = fingerprint
    result["run_id"] = hashlib.sha256(
        (SKILL_VERSION + ":" + fingerprint).encode("utf-8")).hexdigest()[:16]

    gaps = {}
    goal_loc = None
    plans = None
    plan_loc = None
    via = None
    contracts = None
    domains = None
    samples = None
    ethics = None
    if case_obj is not None:
        result["case_id"] = _extract_case_id(case_obj)
        bind_contract(case_obj, result)
        goal, goal_loc = find_goal(case_obj)
        result["research_question"] = goal
        if goal is None:
            gaps["goal_missing"] = True
        plans_found = find_plans(case_obj)
        if plans_found:
            _, plan_loc, plans, via = plans_found
            result["experiment_plan"]["objective"] = goal
        else:
            gaps["plans"] = True
        contracts = collect_by_regex(case_obj, CONTRACT_RE)
        domains = collect_by_regex(case_obj, DOMAIN_RE)
        samples = collect_by_regex(case_obj, SAMPLE_RE)
        ethics = collect_by_regex(case_obj, ETHICS_RE)
        if not contracts:
            gaps["resource_contracts"] = True
        if not domains:
            gaps["parameter_domains"] = True
        if not samples:
            gaps["sample_info"] = True
        if not ethics:
            gaps["ethics_section"] = True
    else:
        result["case_id"] = "unknown"
        for f in files:
            base = f["relpath"].rsplit("/", 1)[-1]
            if base in ("case.json", "case.jsonl"):
                result["case_id"] = "derived:" + base.split(".")[0]
        gaps["case_json"] = True
        plans, plan_loc, via, contracts, domains, samples, ethics = (None,) * 7

    task_md = next((f for f in files if f["role"] == "task"), None)
    if task_md is None:
        gaps["task_file"] = True

    result["input_mode"] = "dir" if files else "empty_dir"
    result["status"], result["status_reason"] = assess_status(
        case_obj, readable_chars, gaps, len(files))
    result["change_log"].append({"seq": 1, "step": "S0",
                                 "action": "写入 partial 骨架",
                                 "reason": "执行流程规定：前 3 分钟内必须先落盘结果骨架，之后只做增量改进"})
    if case_truncated:
        result["limitations"].append("case.json 超大被分段读取，可能丢失尾部字段，"
                                     "关键字段需人工复核")

    # 简短 brief，降低模型后续阅读成本。
    brief = {
        "case_id": result["case_id"],
        "goal": result["research_question"],
        "goal_locator": goal_loc,
        "plans": plan_summary(plans, via) if plans else [],
        "plans_locator": plan_loc,
        "plans_via": via,
        "contract_count": len(contracts) if contracts else 0,
        "domain_count": len(domains) if domains else 0,
        "sample_count": len(samples) if samples else 0,
        "ethics_count": len(ethics) if ethics else 0,
        "gaps": {k: True for k, v in gaps.items() if v},
        "files": [{"relpath": f["relpath"], "size": f["size"], "role": f["role"],
                   "status": f["status"], "decode": f["decode"],
                   "parse": f["parse_mode"], "sha256": (f["sha256"] or "")[:12]}
                  for f in files],
        "next": [
            "读 brief.json 的 plans/contracts/domains/ethics，逐维取证",
            "用 python3 scripts/audit.py --calc 做工期/费用/参数/样本算术校验",
            "用 python3 scripts/audit.py --merge 增量写回 result.json",
            "S5 用 python3 scripts/selftest.py 自检并 --render-report 出报告",
        ],
    }
    inventory = {
        "generated_by": SKILL_NAME + "/" + SKILL_VERSION,
        "fingerprint": fingerprint,
        "case": case_meta,
        "counts": {"files": len(files), "case_candidates": len(case_candidates),
                   "readable_chars": readable_chars},
        "files": [{"relpath": f["relpath"], "size": f["size"], "role": f["role"],
                   "status": f["status"], "sha256": f["sha256"],
                   "hash_mode": f["hash_mode"], "decode": f["decode"],
                   "parse_mode": f["parse_mode"], "truncated": f["truncated"],
                   "pdf": f.get("pdf")} for f in files],
        "notes": notes,
        "gaps": {k: True for k, v in gaps.items() if v},
    }
    for n in pdf_notes:
        pass  # 已写入 degradations

    result["input_brief"] = {"inventory": "work/input_inventory.json",
                             "brief": "work/brief.json"}
    result["artifacts"][0]["status"] = "written"

    ok_r = write_json(os.path.join(out_dir, "result.json"), result)
    ok_i = write_json(os.path.join(work_dir, "input_inventory.json"), inventory)
    ok_b = write_json(os.path.join(work_dir, "brief.json"), brief)
    if not (ok_r and ok_i and ok_b):
        result["degradations"].append({"code": "WRITE_FALLBACK",
                                       "detail": "输出或工作目录不可写，已降级到 stdout"})
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")

    sys.stdout.write(
        f"[ingest] skill={SKILL_NAME}@{SKILL_VERSION} status={result['status']} "
        f"case_id={result['case_id']} files={len(files)} "
        f"plans={len(plan_summary(plans, via)) if plans else 0} "
        f"contracts={brief['contract_count']} domains={brief['domain_count']} "
        f"gaps={sorted(brief['gaps'])}\n")
    sys.stdout.write(f"[ingest] result -> {os.path.join(out_dir, 'result.json')}\n")
    sys.stdout.write(f"[ingest] brief  -> {os.path.join(work_dir, 'brief.json')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
