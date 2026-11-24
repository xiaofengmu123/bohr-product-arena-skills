# -*- coding: utf-8 -*-
"""core_impl.py — 可行性审查的规范化中间表示与确定性校验引擎。

设计要点
--------
1. 先把散落在 case.json 里的版本化方案、资源清单、价格/预约合同、参数域
   规范化为 plan-model/1.0（`plan_model.json`），再在校验引擎里只读该模型。
   任何人拿到 plan_model.json 即可复算全部数值，不需要重读自然语言。
2. 每个派生数值都写入 calc 记录：formula / inputs / value / unit / method /
   locators。报告中的数字只能来自 calc，不允许临场"感觉一个数"。
3. 所有判断都以 finding 形式落盘，带 locator + 逐字 quote（缺失类判断给出
   检索范围而不是伪造引文），并被 result.json、review_report.md 共用。
4. 仅依赖 Python 标准库（>=3.8）。

本文件不执行 I/O 编排；编排在 main.py。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re

MAX_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".json", ".jsonl", ".md", ".txt", ".csv", ".tsv", ".yaml", ".yml"}

SCHEMA_VERSION = "plan-model/1.0"
DIMENSIONS = (
    "assumptions_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_exceptions",
    "ethics_safety",
)
VERDICTS = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
RESOURCE_STATES = ("available", "outsource", "purchase", "unavailable", "unknown")
STATE_ZH = {
    "available": "已具备",
    "outsource": "可外协",
    "purchase": "需采购",
    "unavailable": "不具备",
    "unknown": "未知",
}

# ---------------------------------------------------------------- 文本与 JSON


def read_text(path, max_bytes=MAX_BYTES):
    """读取文本，返回 (text, warnings)；BOM/坏编码/超限/缺失均降级。"""
    if not path or not os.path.exists(path):
        return None, ["missing: %s" % path]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, ["unreadable: %s: %s" % (path, exc)]
    if size > max_bytes:
        return None, ["oversize(%d>%d): %s" % (size, max_bytes, path)]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["unreadable: %s: %s" % (path, exc)]
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ["fallback_decode: %s" % path]


def load_json_file(path):
    text, warns = read_text(path)
    if text is None:
        return None, warns
    text = text.lstrip("\ufeff").strip()
    try:
        return json.loads(text), warns
    except ValueError as exc:
        warns.append("json_error: %s: %s" % (path, exc))
        return None, warns


def sha12(text):
    if isinstance(text, str):
        text = text.encode("utf-8", "replace")
    return hashlib.sha256(text or b"").hexdigest()[:12]


def dumps(obj, indent=2):
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=str)


def snip(value, limit=180):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = dumps(value, 0)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---------------------------------------------------------------- 键名归一

def nk(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def key_match(key, aliases):
    a = nk(key)
    if not a:
        return False
    for alias in aliases:
        b = nk(alias)
        if not b:
            continue
        if a == b:
            return True
        if len(b) >= 4 and (a.endswith(b) or a.startswith(b)):
            return True
    return False


def get_val(mapping, aliases):
    if not isinstance(mapping, dict):
        return None, None
    for key, value in mapping.items():
        if key_match(key, aliases):
            return key, value
    return None, None


PLAN_KEYS = ("plans", "candidates", "plan_versions", "versions", "options",
             "schemes", "proposals", "designs", "alternatives", "方案", "候选方案")
STEP_KEYS = ("steps", "procedure", "protocol_steps", "workflow", "activities",
             "stages", "tasks", "operations", "schedule", "流程", "步骤")
DUR_KEYS = ("duration", "duration_min", "duration_minutes", "estimated_min",
            "time_min", "elapsed_min", "estimated_duration", "时长", "耗时", "预计时长")
OPER_KEYS = ("operator_min", "hands_on_min", "labor_min", "attended_min",
             "hands_on", "人工时长", "操作时长", "人工")
WAIT_KEYS = ("waiting_min", "incubation_min", "wait_min", "waiting",
             "等待时长", "孵育时长", "等待")
EQ_KEYS = ("equipment", "equipment_ids", "devices", "instruments", "device",
           "equipment_required", "设备", "仪器")
MAT_KEYS = ("materials", "reagents", "consumables", "supplies", "kits",
            "材料", "试剂", "耗材")
PARAM_KEYS = ("parameters", "params", "settings", "conditions", "parameter_values",
              "参数", "条件")
SAMPLE_KEYS = ("samples", "sample_use", "specimens", "sample_ids", "sample_plan",
               "样本", "样本用量")
DEP_KEYS = ("depends_on", "dependencies", "predecessors", "after", "prerequisite",
            "前置", "依赖")
CONTROL_KEYS = ("control_group", "controls", "control_arm", "control_condition",
                "vehicle", "sham", "对照", "对照组")
ASSUMP_KEYS = ("assumptions", "assumption", "premises", "前提假设", "假设条件")
HYPOTHESIS_KEYS = ("hypothesis", "hypotheses", "research_hypothesis", "假设", "研究假设")
QUESTION_KEYS = ("research_question", "question", "objective", "aim", "goal",
                 "purpose", "研究问题", "研究目标", "目标")
VAR_KEYS = ("variables", "independent_variables", "factors", "independent",
            "dependent_variables", "outcomes", "endpoints", "变量", "指标")
RISK_KEYS = ("risks", "risk_register", "failure_modes", "hazards", "risk_items",
             "风险", "风险清单")
MITIG_KEYS = ("mitigation", "mitigations", "contingency", "fallback", "control_measure",
              "应对", "处置", "降级", "缓解")
ETHICS_KEYS = ("ethics", "ethics_approval", "approvals", "irb", "iacuc", "compliance",
               "ethic", "伦理", "审批")
SAMPLE_SRC_KEYS = ("sample_inventory", "samples_available", "sample_source",
                   "sample_bank", "cohort", "participants", "animals", "biospecimens",
                   "样本来源", "样本库存", "受试者")
BUDGET_KEYS = ("budget", "budget_cny", "cost_limit", "funding", "经费", "预算")
START_KEYS = ("planned_start", "start_date", "schedule_start", "earliest_start",
              "计划开始", "开始日期")
DECISION_KEYS = ("decision_date", "order_date", "today", "review_date", "决策日期", "下单日期")
DOMAIN_KEYS = ("parameter_domains", "domains", "parameter_space", "limits",
               "ranges", "spec_limits", "允许范围", "参数域")
REG_KEYS = ("resources", "resource_registry", "inventory", "lab_resources",
            "facilities", "catalog", "resource_inventory", "资源", "资源清单")
CONTRACT_KEYS = ("contracts", "resource_contracts", "quotes", "pricing", "booking",
                 "procurement", "supplier_contracts", "service_contracts",
                 "价格合同", "预约合同", "采购")
CRITERIA_KEYS = ("success_criteria", "acceptance_criteria", "interpretation",
                 "decision_rule", "endpoints_success", "判读标准", "成功标准")
PERSONNEL_KEYS = ("personnel", "staff", "operators", "team", "人员", "人力")


def as_items(value):
    """把 list / mapping / 单个对象统一成 [(label, dict)]。"""
    if isinstance(value, list):
        return [(str(i), v) for i, v in enumerate(value) if isinstance(v, (dict, str))]
    if isinstance(value, dict):
        if not value:
            return []
        leaf_hints = ("name", "id", "duration", "value", "status", "price", "step")
        if any(key_match(k, leaf_hints) for k in value):
            return [("", value)]
        items = [(str(k), v) for k, v in value.items() if isinstance(v, (dict, list))]
        return items
    return []


def scalar_items(value):
    """把标量（str）或 list/tuple 统一成元素列表，供逐个处理。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# ---------------------------------------------------------------- 单位与数值

def unit_key(unit):
    if not isinstance(unit, str):
        return ""
    s = unit.strip().lower().replace("μ", "µ").replace("℃", "°c")
    s = re.sub(r"\s+", "", s).strip(".")
    if s.endswith("s") and s[:-1] in ("minute", "hour", "day", "week", "second",
                                      "sample", "well", "piece", "pack"):
        s = s[:-1]
    return s


UNIT_FACTORS = {}


def _reg(dim, mapping):
    for key, factor in mapping.items():
        UNIT_FACTORS[unit_key(key)] = (dim, factor)


_reg("time", {"s": 1 / 60.0, "sec": 1 / 60.0, "second": 1 / 60.0, "秒": 1 / 60.0,
              "min": 1.0, "minute": 1.0, "分": 1.0, "分钟": 1.0, "mins": 1.0,
              "h": 60.0, "hr": 60.0, "hour": 60.0, "小时": 60.0, "时": 60.0,
              "d": 1440.0, "day": 1440.0, "天": 1440.0, "日": 1440.0,
              "wk": 10080.0, "week": 10080.0, "周": 10080.0, "星期": 10080.0})
_reg("volume", {"ul": 1.0, "µl": 1.0, "microl": 1.0, "微升": 1.0,
                "ml": 1000.0, "毫升": 1000.0,
                "l": 1e6, "升": 1e6})
_reg("mass", {"ng": 1e-6, "µg": 1e-3, "ug": 1e-3, "微克": 1e-3,
              "mg": 1.0, "毫克": 1.0, "g": 1000.0, "克": 1000.0,
              "kg": 1e6, "千克": 1e6})
_reg("money", {"cny": 1.0, "rmb": 1.0, "yuan": 1.0, "元": 1.0, "¥": 1.0,
               "￥": 1.0, "块": 1.0, "万": 10000.0})
_reg("count", {"个": 1.0, "支": 1.0, "管": 1.0, "片": 1.0, "只": 1.0, "次": 1.0,
               "孔": 1.0, "well": 1.0, "sample": 1.0, "份": 1.0, "例": 1.0,
               "pack": 1.0, "盒": 1.0, "板": 1.0, "plate": 1.0})
_reg("temp", {"°c": 1.0, "c": 1.0, "度": 1.0, "摄氏度": 1.0})
_reg("pct", {"%": 1.0, "percent": 1.0})
_reg("length", {"mm": 1.0, "cm": 10.0, "m": 1000.0, "µm": 1e-3, "um": 1e-3})

QTY_RE = re.compile(
    r"(?P<num>[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*"
    r"(?P<unit>[A-Za-zµμ°℃%]+|[\u4e00-\u9fff]{1,3})?"
)


def unit_info(unit):
    return UNIT_FACTORS.get(unit_key(unit), (None, 1.0))


def parse_quantity(value, default_unit=None):
    """解析 "1.5 h" / "90 min" / "¥1,200" / 24 / "2 h 30 min" 为基准单位。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        dim, factor = unit_info(default_unit or "")
        return {"value": float(value) * factor,
                "raw": str(value),
                "unit": (default_unit or "").strip(),
                "dim": dim or "unknown",
                "mixed": False,
                "assumed_unit": not bool(default_unit)}
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    parts = []
    for match in QTY_RE.finditer(s):
        try:
            num = float(match.group("num").replace(",", ""))
        except ValueError:
            continue
        unit = match.group("unit") or ""
        dim, factor = unit_info(unit)
        parts.append((num * factor, dim, unit))
    if not parts:
        return None
    dims = {p[1] for p in parts if p[1]}
    money_mark = ("¥" in s) or ("￥" in s)
    if money_mark and not dims:
        dims = {"money"}
    if len(dims) == 1 and None not in {p[1] for p in parts}:
        dim = dims.pop()
        total = sum(p[0] for p in parts)
    else:
        total, dim = parts[0][0], parts[0][1]
    return {"value": total,
            "raw": s,
            "unit": parts[0][2].strip(),
            "dim": dim or "unknown",
            "mixed": len(dims) > 1,
            "assumed_unit": not bool(parts[0][2])}


RANGE_RE = re.compile(
    r"(?P<a>[-+]?\d+(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到|to)\s*"
    r"(?P<b>[-+]?\d+(?:\.\d+)?)\s*(?P<u>[A-Za-zµμ°℃%/]+|[\u4e00-\u9fff]{0,2})?"
)
LOWER_RE = re.compile(r"(?:≥|>=|不低于|不小于|至少)\s*(?P<a>[-+]?\d+(?:\.\d+)?)\s*(?P<u>[A-Za-zµμ°℃%/]+|[\u4e00-\u9fff]{0,2})?")
UPPER_RE = re.compile(r"(?:≤|<=|不高于|不大于|至多)\s*(?P<b>[-+]?\d+(?:\.\d+)?)\s*(?P<u>[A-Za-zµμ°℃%/]+|[\u4e00-\u9fff]{0,2})?")


def parse_range(value, default_unit=None):
    """解析 "20–25 °C" / "20 to 25" / "≥20" / {"min":..,"max":..}；返回统一结构。"""
    if isinstance(value, dict):
        lo = value.get("min", value.get("lower", value.get("from")))
        hi = value.get("max", value.get("upper", value.get("to")))
        unit = value.get("unit", default_unit)
        out = {"min": None, "max": None, "unit": unit, "allowed": None,
               "raw": snip(value), "locator": None}
        if lo is not None:
            q = parse_quantity(lo, unit)
            out["min"] = q["value"] if q else None
        if hi is not None:
            q = parse_quantity(hi, unit)
            out["max"] = q["value"] if q else None
        if value.get("allowed") or value.get("values"):
            out["allowed"] = list(value.get("allowed") or value.get("values"))
        if value.get("fixed") is not None:
            q = parse_quantity(value.get("fixed"), unit)
            out["min"] = out["max"] = q["value"] if q else None
        return out
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            q = parse_quantity(value[0], default_unit)
            if q:
                return {"min": q["value"], "max": q["value"], "unit": q["unit"],
                        "allowed": None, "raw": snip(value), "locator": None}
        if len(value) == 2:
            q1 = parse_quantity(value[0], default_unit)
            q2 = parse_quantity(value[1], default_unit)
            if q1 and q2:
                return {"min": min(q1["value"], q2["value"]),
                        "max": max(q1["value"], q2["value"]),
                        "unit": q1["unit"] or default_unit, "allowed": None,
                        "raw": snip(value), "locator": None}
        return {"min": None, "max": None, "unit": default_unit,
                "allowed": [str(v) for v in value], "raw": snip(value), "locator": None}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        q = parse_quantity(value, default_unit)
        return {"min": q["value"], "max": q["value"], "unit": q["unit"],
                "allowed": None, "raw": str(value), "locator": None}
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    m = RANGE_RE.search(s)
    if m:
        unit = m.group("u") or default_unit
        return {"min": float(m.group("a")), "max": float(m.group("b")),
                "unit": unit, "allowed": None, "raw": s, "locator": None}
    m = LOWER_RE.search(s)
    if m:
        return {"min": float(m.group("a")), "max": None,
                "unit": m.group("u") or default_unit, "allowed": None,
                "raw": s, "locator": None}
    m = UPPER_RE.search(s)
    if m:
        return {"min": None, "max": float(m.group("b")),
                "unit": m.group("u") or default_unit, "allowed": None,
                "raw": s, "locator": None}
    q = parse_quantity(s, default_unit)
    if q:
        return {"min": q["value"], "max": q["value"], "unit": q["unit"],
                "allowed": None, "raw": s, "locator": None}
    return None


def parse_date(value):
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", s)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def to_state(text):
    s = nk(text) if isinstance(text, str) else ""
    raw = (text or "").lower() if isinstance(text, str) else ""
    if any(k in s for k in ("不具备", "停用", "故障", "占用", "unavailable", "down", "broken")):
        return "unavailable"
    if any(k in s for k in ("需采购", "采购", "询价", "购置", "purchase", "quote", "buy")):
        return "purchase"
    if any(k in s for k in ("外协", "外包", "共享", "outsource", "external", "collaborat")):
        return "outsource"
    if any(k in s for k in ("已具备", "在库", "可用", "自有", "available", "inhouse", "owned", "ready")):
        return "available"
    if any(k in raw for k in ("in-house", "in house")):
        return "available"
    return "unknown"


def num_or_none(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    q = parse_quantity(value)
    return q["value"] if q else None


def ceil_div(a, b):
    if not b or b <= 0:
        return None
    return int(math.ceil(float(a) / float(b)))


# ---------------------------------------------------------------- 输入发现

def discover_files(root):
    """返回 [(relpath, abspath, size, ext)]，跳过符号链接与不可读文件。"""
    found = []
    if not os.path.isdir(root):
        return found
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, root).replace("\\", "/")
            found.append((rel, path, size, os.path.splitext(name)[1].lower()))
    return found


# ---------------------------------------------------------------- 规范化模型

HUMAN_WORDS = ("受试者", "患者", "志愿者", "人体", "人血", "血清", "血浆", "外周血",
               "活检", "临床样本", "健康人", "human", "patient", "participant",
               "volunteer", "serum", "plasma", "biopsy", "pbmc", "urine")
ANIMAL_WORDS = ("小鼠", "大鼠", "家兔", "兔", "犬", "猪", "猴", "斑马鱼", "动物",
                "mice", "mouse", "rat", "rabbit", "canine", "porcine", "zebrafish",
                "animal", "in vivo", "nude")
HAZARD_WORDS = ("危化", "剧毒", "有毒", "致癌", "放射", "同位素", "生物安全", "bsl",
                "病原", "感染", "甲醛", "丙烯酰胺", "叠氮", "强酸", "强碱",
                "有机溶剂", "汞", "氰化物", "氯仿", "酚", "hazard", "toxic",
                "carcinogen", "radioactive", "pathogen", "infectious")
PII_WORDS = ("个人信息", "病历", "隐私", "可识别", "personal data", "privacy", "phi", "gdpr")


class Builder(object):
    """把 /input 下的真实材料规范化为 plan-model。只做发现与记录，不做判断。"""

    def __init__(self, input_dir):
        self.input_dir = input_dir
        self._issue_n = 0
        self._case_rel = "case.json"
        self.root = {}
        self.model = {
            "schema_version": SCHEMA_VERSION,
            "case_id": None,
            "challenge_id": None,
            "title": None,
            "input_files": [],
            "case_file": None,
            "plans": [],
            "registry": {},
            "domains": {},
            "context": {},
            "issues": [],
            "notes": [],
        }

    # -- 记录
    def issue(self, where, kind, detail):
        self._issue_n += 1
        self.model["issues"].append({
            "id": "I%02d" % self._issue_n, "where": where,
            "kind": kind, "detail": snip(detail, 300)})

    def loc(self, suffix=""):
        base = self.model.get("case_file") or self._case_rel
        return "%s#%s" % (base, suffix) if suffix else base

    # -- 主流程
    def build(self):
        files = discover_files(self.input_dir)
        for rel, path, size, ext in files:
            self.model["input_files"].append({"path": rel, "bytes": size, "ext": ext})
        case_rel, case_path = self._pick_case(files)
        if case_path is None:
            self.issue("input", "missing_case", "未找到 case.json / case.jsonl")
            self.model["notes"].append("输入目录中没有可解析的案例主文件")
            return self.model
        self._case_rel = case_rel
        self.model["case_file"] = case_rel
        data, warns = load_json_file(case_path)
        for warn in warns:
            self.issue(case_rel, "read", warn)
        if data is None:
            self.issue(case_rel, "fatal", "案例主文件无法解析为 JSON")
            self.model["notes"].append("案例主文件不可解析，无法建立计划模型")
            return self.model
        if isinstance(data, list):
            data = {"plans": data}
        if not isinstance(data, dict):
            self.issue(case_rel, "fatal", "案例主文件顶层不是对象")
            self.model["notes"].append("案例主文件顶层结构不支持")
            return self.model
        self.root = data
        self._ids()
        self._plans()
        self._registry()
        self._domains()
        self._context()
        self._attach_material_notes(files, case_path)
        return self.model

    def _pick_case(self, files):
        cands = [f for f in files if os.path.basename(f[0]).lower() in ("case.json", "case.jsonl")]
        cands.sort(key=lambda f: (f[0].count("/"), f[0]))
        if cands:
            return cands[0][0], cands[0][1]
        for rel, path, size, ext in files:
            if ext != ".json":
                continue
            obj, _ = load_json_file(path)
            if isinstance(obj, dict) and (get_val(obj, PLAN_KEYS)[0] or get_val(obj, ("case_id",))[0]):
                return rel, path
        return None, None

    def _ids(self):
        for key in ("case_id", "caseId", "challenge_id", "challengeId", "armiId", "id"):
            value = self.root.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                if key.lower().startswith("case") or self.model["case_id"] is None:
                    self.model["case_id"] = self.model["case_id"] or str(value).strip()
                if "challenge" in key.lower():
                    self.model["challenge_id"] = str(value).strip()
                if "armi" in key.lower():
                    self.model["challenge_id"] = self.model["challenge_id"] or str(value).strip()
        nested_key, nested = get_val(self.root, ("case", "case_info", "meta"))
        if isinstance(nested, dict):
            for key in ("case_id", "caseId", "id"):
                if isinstance(nested.get(key), (str, int)) and not self.model["case_id"]:
                    self.model["case_id"] = str(nested[key]).strip()
                if isinstance(nested.get(key), (str, int)) and "challenge" in key.lower():
                    self.model["challenge_id"] = str(nested[key]).strip()
        if not self.model["case_id"]:
            self.model["case_id"] = "unknown"
        title_key, title = get_val(self.root, ("title", "case_title", "name", "题目"))
        if isinstance(title, str):
            self.model["title"] = title.strip()
            if self.model["title"] == self.model["case_id"]:
                self.model["title"] = None

    def _plans(self):
        key, val = get_val(self.root, PLAN_KEYS)
        loc_prefix = key or "plans"
        if val is None:
            for parent_key, parent in self.root.items():
                if isinstance(parent, dict):
                    sub_key, sub = get_val(parent, PLAN_KEYS)
                    if sub is not None:
                        key, val, loc_prefix = sub_key, sub, "%s.%s" % (parent_key, sub_key)
                        break
        if val is None:
            if get_val(self.root, STEP_KEYS)[0]:
                val, loc_prefix = [self.root], "case"
            else:
                for list_key, list_val in self.root.items():
                    if isinstance(list_val, list) and list_val and isinstance(list_val[0], dict):
                        if get_val(list_val[0], STEP_KEYS)[0] or get_val(list_val[0], PARAM_KEYS)[0]:
                            val, loc_prefix = list_val, list_key
                            break
        if val is None:
            self.issue("case", "missing_plans", "未定位到候选方案列表（plans/candidates/versions 等）")
            return
        for label, raw in as_items(val):
            if not isinstance(raw, dict):
                continue
            if "items" in raw and isinstance(raw["items"], list):
                for idx, sub in enumerate(raw["items"]):
                    if isinstance(sub, dict):
                        loc = "%s#%s.%s[%d]" % (self._case_rel, loc_prefix, label, idx) if label \
                            else "%s#%s[%d]" % (self._case_rel, loc_prefix, idx)
                        self.model["plans"].append(self._extract_plan(sub, loc, label or str(idx)))
                continue
            loc = "%s#%s[%s]" % (self._case_rel, loc_prefix, label) if label \
                else "%s#%s" % (self._case_rel, loc_prefix)
            self.model["plans"].append(self._extract_plan(raw, loc, label))
        if not self.model["plans"]:
            self.issue("case", "empty_plans", "方案列表为空或元素不是对象")

    def _plan_id(self, raw, label):
        for key in ("plan_id", "id", "name", "version", "label", "方案编号", "方案名称"):
            value = raw.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        return label if label else "P1"

    def _extract_plan(self, raw, locator, label):
        pid = self._plan_id(raw, label)
        plan = {
            "plan_id": pid, "label": label, "locator": locator, "raw": raw,
            "goal": None, "hypothesis": None, "assumptions": [], "variables": [],
            "controls": [], "steps": [], "parameters": [], "samples": [],
            "risks": [], "criteria": [], "budget": None, "start": None,
            "personnel": [], "resources_required": [], "materials_required": [],
            "text": "", "flags": {}, "metrics": {}, "findings": [],
        }
        _, goal = get_val(raw, QUESTION_KEYS)
        plan["goal"] = self._text_of(goal)
        _, hyp = get_val(raw, HYPOTHESIS_KEYS)
        plan["hypothesis"] = self._text_of(hyp)
        _, assumptions = get_val(raw, ASSUMP_KEYS)
        plan["assumptions"] = self._string_list(assumptions, locator + ".assumptions")
        _, variables = get_val(raw, VAR_KEYS)
        plan["variables"] = self._variables(variables, locator + ".variables")
        _, controls = get_val(raw, CONTROL_KEYS)
        plan["controls"] = self._string_list(controls, locator + ".controls")
        _, criteria = get_val(raw, CRITERIA_KEYS)
        plan["criteria"] = self._string_list(criteria, locator + ".criteria")
        _, budget = get_val(raw, BUDGET_KEYS)
        plan["budget"] = parse_quantity(budget, "CNY") if budget is not None else None
        _, start = get_val(raw, START_KEYS)
        plan["start"] = parse_date(start)
        _, personnel = get_val(raw, PERSONNEL_KEYS)
        plan["personnel"] = self._personnel(personnel, locator + ".personnel")

        step_key, step_val = get_val(raw, STEP_KEYS)
        if step_val is None:
            for wrapper in ("protocol", "procedure", "method", "methods", "workflow"):
                _, box = get_val(raw, (wrapper,))
                if isinstance(box, dict):
                    inner_key, inner = get_val(box, STEP_KEYS)
                    if inner is not None:
                        step_key, step_val = inner_key, inner
                        break
        plan["steps"] = self._steps(step_val, plan["plan_id"], locator, step_key or "steps")

        _, pparams = get_val(raw, PARAM_KEYS)
        if pparams is not None and not isinstance(pparams, list):
            plan["parameters"] = self._params(pparams, locator + ".parameters")
        elif isinstance(pparams, list):
            plan["parameters"] = self._params(pparams, locator + ".parameters")

        _, samples = get_val(raw, SAMPLE_KEYS)
        plan["samples"] = self._samples(samples, locator + ".samples")

        _, risks = get_val(raw, RISK_KEYS)
        plan["risks"] = self._risks(risks, locator + ".risks")

        for req_key, target, kind in ((EQ_KEYS, "resources_required", "equipment"),
                                      (MAT_KEYS, "materials_required", "material")):
            _, req = get_val(raw, req_key)
            for item in scalar_items(req):
                if isinstance(item, str):
                    plan[target].append({"id": item.strip(), "kind": kind,
                                         "locator": locator, "raw": item})
                elif isinstance(item, dict):
                    rid = item.get("id") or item.get("name") or item.get("equipment")
                    if rid:
                        plan[target].append({"id": str(rid), "kind": kind,
                                             "locator": locator, "raw": snip(item)})

        plan["text"] = dumps(raw, 0).lower()
        for flag, words in (("human", HUMAN_WORDS), ("animal", ANIMAL_WORDS),
                            ("hazard", HAZARD_WORDS), ("personal_data", PII_WORDS)):
            plan["flags"][flag] = [w for w in words if w in plan["text"]][:6]
        return plan

    @staticmethod
    def _text_of(value):
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            for key in ("text", "question", "statement", "value", "description"):
                if isinstance(value.get(key), str):
                    return value[key].strip()
            return snip(value, 240)
        if isinstance(value, list):
            return "；".join(str(v) for v in value if isinstance(v, str)) or None
        return None

    @staticmethod
    def _string_list(value, locator):
        out = []
        items = value if isinstance(value, list) else ([value] if value is not None else [])
        for idx, item in enumerate(items):
            if isinstance(item, str):
                out.append({"text": item.strip(), "locator": "%s[%d]" % (locator, idx)})
            elif isinstance(item, dict):
                text = None
                for key in ("text", "statement", "description", "name", "value", "assumption", "risk", "control"):
                    if isinstance(item.get(key), str):
                        text = item[key]
                        break
                if text:
                    out.append({"text": text.strip(), "locator": "%s[%d]" % (locator, idx),
                                "raw": snip(item, 200)})
        return out

    def _variables(self, value, locator):
        out = []
        items = value if isinstance(value, list) else (
            [value] if isinstance(value, (dict, str)) else [])
        for idx, item in enumerate(items):
            if isinstance(item, str):
                out.append({"name": item.strip(), "role": None, "unit": None,
                            "levels": [], "locator": "%s[%d]" % (locator, idx)})
            elif isinstance(item, dict):
                name = item.get("name") or item.get("variable") or item.get("factor")
                role = item.get("role") or item.get("type")
                unit = item.get("unit")
                levels = item.get("levels") or item.get("values") or item.get("groups") or []
                if isinstance(levels, str):
                    levels = [levels]
                if name:
                    out.append({"name": str(name), "role": role, "unit": unit,
                                "levels": [str(x) for x in levels] if isinstance(levels, list) else [],
                                "locator": "%s[%d]" % (locator, idx), "raw": snip(item)})
        return out

    def _personnel(self, value, locator):
        out = []
        for idx, (_, item) in enumerate(as_items(value)):
            if isinstance(item, dict):
                role = item.get("role") or item.get("name") or "member"
                n = num_or_none(item.get("n") or item.get("count") or item.get("fte"))
                hours = item.get("hours_per_week") or item.get("capacity_hours_per_week")
                out.append({"role": str(role), "n": n or 1,
                            "hours_per_week": num_or_none(hours),
                            "locator": "%s[%d]" % (locator, idx), "raw": snip(item)})
            elif isinstance(item, str):
                out.append({"role": item, "n": 1, "hours_per_week": None,
                            "locator": "%s[%d]" % (locator, idx)})
        return out

    def _steps(self, value, pid, locator, key):
        out = []
        for idx, (label, item) in enumerate(as_items(value)):
            if not isinstance(item, dict):
                if isinstance(item, str):
                    out.append(self._step_from_text(item, pid, idx, locator, key))
                continue
            step = self._step(item, pid, idx, locator, key, label)
            out.append(step)
        return out

    def _step_from_text(self, text, pid, idx, locator, key):
        q = parse_quantity(text)
        dur = q if q and q["dim"] == "time" else None
        name = QTY_RE.sub("", text).strip(" :：,，") or text
        return {"step_id": "%s.S%d" % (pid, idx + 1), "index": idx + 1,
                "name": name, "locator": "%s.%s[%d]" % (locator, key, idx),
                "raw": text, "duration": dur, "operator": None, "wait": None,
                "equipment": [], "materials": [], "params": [], "samples": [],
                "depends_on": []}

    def _step(self, item, pid, idx, locator, key, label):
        name = None
        for field in ("name", "title", "step", "action", "description", "task", "步骤"):
            if isinstance(item.get(field), str):
                name = item[field]
                break
        name = (name or label or ("step %d" % (idx + 1))).strip()
        default_unit = None
        for fkey, fval in item.items():
            if isinstance(fval, str) and key_match(fkey, ("duration_unit", "time_unit", "单位")):
                default_unit = fval
                break
        dur = None
        for field in DUR_KEYS:
            _, raw = get_val(item, (field,))
            if raw is not None:
                dur = parse_quantity(raw, default_unit)
                if dur and not dur.get("unit"):
                    dur["assumed_unit"] = True
                    dur["unit"] = "min"
                break
        if dur is None:
            _, raw = get_val(item, ("time_estimate", "estimated_time"))
            dur = parse_quantity(raw, default_unit) if raw is not None else None
        if dur and dur.get("assumed_unit"):
            self.issue("%s.%s[%d]" % (locator, key, idx), "unit_assumed",
                       "步骤时长无单位，按分钟解释：%s" % snip(dur.get("raw"), 80))
        oper = None
        for field in OPER_KEYS:
            _, raw = get_val(item, (field,))
            if raw is not None:
                oper = parse_quantity(raw, default_unit or (dur or {}).get("unit"))
                break
        wait = None
        for field in WAIT_KEYS:
            _, raw = get_val(item, (field,))
            if raw is not None:
                wait = parse_quantity(raw, default_unit or (dur or {}).get("unit"))
                break
        equipment = []
        _, eq_val = get_val(item, EQ_KEYS)
        for e in scalar_items(eq_val):
            entry = self._eq_entry(e)
            if entry:
                equipment.append(entry)
        materials = []
        _, mat_val = get_val(item, MAT_KEYS)
        for m in scalar_items(mat_val):
            entry = self._mat_entry(m)
            if entry:
                materials.append(entry)
        params = []
        _, pval = get_val(item, PARAM_KEYS)
        if pval is not None:
            params = self._params(pval, "%s.%s[%d].parameters" % (locator, key, idx))
        deps = []
        _, dval = get_val(item, DEP_KEYS)
        if isinstance(dval, (list, tuple)):
            deps = [str(x).strip() for x in dval if str(x).strip()]
        elif isinstance(dval, str) and dval.strip():
            deps = [dval.strip()]
        samples = []
        _, sval = get_val(item, SAMPLE_KEYS)
        if sval is not None:
            samples = self._samples(sval, "%s.%s[%d].samples" % (locator, key, idx))
        return {"step_id": "%s.S%d" % (pid, idx + 1), "index": idx + 1,
                "name": name, "locator": "%s.%s[%d]" % (locator, key, idx),
                "raw": snip(item, 300), "duration": dur, "operator": oper,
                "wait": wait, "equipment": equipment, "materials": materials,
                "params": params, "samples": samples, "depends_on": deps}

    @staticmethod
    def _eq_entry(item):
        if isinstance(item, str):
            text = item.strip()
            q = parse_quantity(text)
            if q and q["dim"] == "time":
                name = QTY_RE.sub("", text).strip(" :：,，") or text
                return {"id": name, "minutes": q["value"], "raw": text}
            return {"id": text, "minutes": None, "raw": text}
        if isinstance(item, dict):
            rid = item.get("id") or item.get("equipment_id") or item.get("name") or item.get("model")
            mk, mv = get_val(item, ("minutes", "duration_min", "occupancy_min", "time_min", "使用时长"))
            return {"id": str(rid).strip() if rid else snip(item, 40),
                    "minutes": num_or_none(mv) if mk else None, "raw": snip(item)}
        return None

    @staticmethod
    def _mat_entry(item):
        if isinstance(item, str):
            text = item.strip()
            q = parse_quantity(text)
            if q:
                name = QTY_RE.sub("", text).strip(" :：,，") or text
                return {"id": name, "qty": q, "raw": text}
            return {"id": text, "qty": None, "raw": text}
        if isinstance(item, dict):
            rid = item.get("id") or item.get("name") or item.get("reagent")
            qraw = None
            for field in ("qty", "quantity", "amount", "volume", "mass", "用量"):
                if item.get(field) is not None:
                    qraw = item[field]
                    break
            unit = item.get("unit")
            q = parse_quantity(qraw, unit) if qraw is not None else None
            return {"id": str(rid).strip() if rid else snip(item, 40),
                    "qty": q, "raw": snip(item)}
        return None

    def _params(self, value, locator):
        out = []
        if isinstance(value, dict):
            for name, spec in value.items():
                if isinstance(spec, dict):
                    raw_value = spec.get("value", spec.get("val"))
                    unit = spec.get("unit")
                    q = parse_quantity(raw_value, unit) if raw_value is not None else None
                    out.append({"name": str(name), "value": q,
                                "unit": unit, "locator": "%s.%s" % (locator, name),
                                "raw": snip(spec)})
                else:
                    q = parse_quantity(spec)
                    out.append({"name": str(name), "value": q, "unit": None,
                                "locator": "%s.%s" % (locator, name), "raw": snip(spec)})
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    name = item.get("name") or item.get("param") or item.get("parameter")
                    q = parse_quantity(item.get("value"), item.get("unit"))
                    if name:
                        out.append({"name": str(name), "value": q,
                                    "unit": item.get("unit"),
                                    "locator": "%s[%d]" % (locator, idx), "raw": snip(item)})
                elif isinstance(item, str):
                    m = re.match(r"^\s*([^:=：]{1,40})[:：=]\s*(.+)$", item)
                    if m:
                        name, rest = m.group(1).strip(), m.group(2).strip()
                    else:
                        parts = item.split(None, 1)
                        name, rest = parts[0], (parts[1] if len(parts) > 1 else "")
                    q = parse_quantity(rest)
                    out.append({"name": name, "value": q, "unit": None,
                                "locator": "%s[%d]" % (locator, idx), "raw": item})
        return out

    def _samples(self, value, locator):
        out = []
        if isinstance(value, str):
            q = parse_quantity(value)
            return [{"id": value, "n": q["value"] if q and q["dim"] == "count" else None,
                     "per_arm": False, "unit": "samples", "volume": None,
                     "locator": locator}]
        if isinstance(value, dict):
            n = num_or_none(value.get("n") or value.get("count") or value.get("number"))
            per_arm = value.get("per_arm")
            out.append({"id": str(value.get("id") or value.get("type") or "sample"),
                        "n": n, "per_arm": bool(per_arm),
                        "unit": value.get("unit") or "samples",
                        "volume": parse_quantity(value.get("volume"), value.get("volume_unit"))
                        if value.get("volume") is not None else None,
                        "locator": locator, "raw": snip(value)})
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    n = num_or_none(item.get("n") or item.get("count") or item.get("number"))
                    out.append({"id": str(item.get("id") or item.get("type") or "sample"),
                                "n": n, "per_arm": bool(item.get("per_arm")),
                                "unit": item.get("unit") or "samples",
                                "volume": parse_quantity(item.get("volume"), item.get("volume_unit"))
                                if item.get("volume") is not None else None,
                                "locator": "%s[%d]" % (locator, idx), "raw": snip(item)})
                elif isinstance(item, str):
                    q = parse_quantity(item)
                    out.append({"id": item, "n": q["value"] if q and q["dim"] == "count" else None,
                                "per_arm": False, "unit": "samples",
                                "volume": None, "locator": "%s[%d]" % (locator, idx)})
        return out

    def _risks(self, value, locator):
        out = []
        for idx, (_, item) in enumerate(as_items(value) if isinstance(value, (list, dict))
                                         else [("", value)]):
            if isinstance(item, str):
                out.append({"text": item.strip(), "mitigation": [], "locator": "%s[%d]" % (locator, idx)})
            elif isinstance(item, dict):
                text = None
                for field in ("risk", "text", "description", "name", "hazard", "failure_mode"):
                    if isinstance(item.get(field), str):
                        text = item[field]
                        break
                _, mit = get_val(item, MITIG_KEYS)
                mits = []
                if isinstance(mit, str):
                    mits = [mit]
                elif isinstance(mit, list):
                    mits = [str(m) for m in mit if m]
                if text:
                    out.append({"text": text.strip(), "mitigation": mits,
                                "locator": "%s[%d]" % (locator, idx), "raw": snip(item)})
        return out

    # -- 资源清单与合同
    def _registry(self):
        reg = {}
        root = self.root
        for key, val in root.items():
            kind_hint = None
            if key_match(key, ("equipment", "instruments", "devices", "facilities")):
                kind_hint = "equipment"
            elif key_match(key, ("materials", "reagents", "consumables", "supplies")):
                kind_hint = "material"
            elif key_match(key, REG_KEYS):
                kind_hint = None
            else:
                continue
            self._add_resources(reg, val, kind_hint, "%s#%s" % (self.loc(), key))
        contract_key, contracts = get_val(root, CONTRACT_KEYS)
        if contracts is not None:
            self._apply_contracts(reg, contracts, "%s#%s" % (self.loc(), contract_key))
        self.model["registry"] = reg

    def _add_resources(self, reg, value, kind_hint, locator):
        if isinstance(value, dict) and not self._looks_like_resource(value):
            for key, sub in value.items():
                hint = kind_hint
                if key_match(key, ("equipment", "instruments", "devices", "facilities")):
                    hint = "equipment"
                elif key_match(key, ("materials", "reagents", "consumables", "supplies")):
                    hint = "material"
                self._add_resources(reg, sub, hint, "%s.%s" % (locator, key))
            return
        for label, item in as_items(value):
            if not isinstance(item, dict):
                continue
            entry = self._resource_entry(item, kind_hint, "%s[%s]" % (locator, label))
            if entry and entry["id"]:
                reg.setdefault(nk(entry["id"]), entry)

    @staticmethod
    def _looks_like_resource(value):
        if not isinstance(value, dict):
            return False
        return any(key_match(k, ("id", "name", "model", "status", "unit_price", "price",
                                 "capacity_hours_per_week", "stock", "lead_time_days"))
                   for k in value)

    def _resource_entry(self, item, kind_hint, locator):
        rid = None
        for field in ("id", "name", "model", "catalog_no", "code", "item", "resource_id", "名称", "设备名称"):
            if isinstance(item.get(field), (str, int)) and str(item[field]).strip():
                rid = str(item[field]).strip()
                break
        if not rid:
            return None
        keys = {nk(k) for k in item}
        kind = kind_hint
        if kind is None:
            if any(k in keys for k in ("capacityhoursperweek", "precision", "resolution",
                                       "maxrpm", "throughput", "detectionlimit", "speeds")) \
               or any("precision" in k or "resolution" in k for k in keys):
                kind = "equipment"
            elif any(k in keys for k in ("packsize", "packsize", "concentration")):
                kind = "material"
            else:
                kind = "equipment"
        entry = {"id": rid, "name": rid, "kind": kind, "status": "unknown",
                 "lead_time_days": None, "unit_price": None, "price_unit": None,
                 "capacity_hours_per_week": None, "booked_hours_per_week": None,
                 "stock": None, "pack_size": None, "spec": {},
                 "contract": False, "locator": locator, "raw": snip(item)}
        _, status = get_val(item, ("status", "state", "availability", "condition", "资源状态", "状态"))
        if status is not None:
            entry["status"] = to_state(status if isinstance(status, str) else snip(status))
            entry["status_raw"] = snip(status)
        _, lead = get_val(item, ("lead_time_days", "lead_time", "delivery_days", "lead",
                                 "货期", "到货周期", "交付周期"))
        if lead is not None:
            q = parse_quantity(lead, "day")
            if q:
                entry["lead_time_days"] = q["value"] / 1440.0 if q["dim"] == "time" else q["value"]
        _, price = get_val(item, ("unit_price", "price", "cost", "quote", "单价", "价格"))
        if price is not None:
            q = parse_quantity(price, "CNY")
            if q:
                entry["unit_price"] = q["value"]
                unit = (item.get("price_unit") or item.get("per") or item.get("unit")
                        or (q.get("unit") or ""))
                entry["price_unit"] = str(unit) if unit else None
        _, cap = get_val(item, ("capacity_hours_per_week", "weekly_hours",
                               "hours_per_week", "可用时长", "周可用时长"))
        if cap is not None:
            q = parse_quantity(cap, "h")
            entry["capacity_hours_per_week"] = q["value"] / 60.0 if q and q["dim"] == "time" else num_or_none(cap)
        _, booked = get_val(item, ("booked_hours_per_week", "booked_hours", "reserved_hours",
                                  "已预约", "已占用"))
        if booked is not None:
            q = parse_quantity(booked, "h")
            entry["booked_hours_per_week"] = q["value"] / 60.0 if q and q["dim"] == "time" else num_or_none(booked)
        _, stock = get_val(item, ("qty_on_hand", "stock", "available_qty", "inventory",
                                 "库存", "在库量"))
        base_unit = item.get("unit") or item.get("stock_unit") or item.get("pack_unit")
        if stock is not None:
            q = parse_quantity(stock, base_unit)
            entry["stock"] = q["value"] if q else num_or_none(stock)
            entry["stock_unit"] = (q or {}).get("unit") or base_unit
        _, pack = get_val(item, ("pack_size", "package_size", "unit_size", "规格", "包装规格"))
        if pack is not None:
            q = parse_quantity(pack, base_unit)
            entry["pack_size"] = q["value"] if q else num_or_none(pack)
            entry["pack_unit"] = (q or {}).get("unit") or base_unit
            if q and q["dim"] == "money" and entry["unit_price"] is None:
                entry["unit_price"] = q["value"]
                entry["price_unit"] = "pack"
        for key, val in item.items():
            nkey = nk(key)
            if any(token in nkey for token in ("precision", "resolution", "accuracy",
                                               "maxrpm", "minvolume", "throughput",
                                               "detectionlimit", "temperaturerange",
                                               "wavelength", "sensitivity")):
                entry["spec"][str(key)] = snip(val, 80)
        _, ready = get_val(item, ("available_from", "ready_date", "delivery_date", "可用日期", "到货日期"))
        entry["available_from"] = parse_date(ready) if ready is not None else None
        return entry

    def _apply_contracts(self, reg, contracts, locator):
        items = []
        if isinstance(contracts, dict):
            if any(isinstance(v, dict) for v in contracts.values()):
                for key, val in contracts.items():
                    if isinstance(val, dict):
                        merged = dict(val)
                        merged.setdefault("id", key)
                        items.append(merged)
            else:
                items.append(contracts)
        elif isinstance(contracts, list):
            items = [c for c in contracts if isinstance(c, dict)]
        self.model["notes"].append("合同条目 %d 条" % len(items))
        for idx, item in enumerate(items):
            rid = None
            for field in ("resource_id", "id", "item", "name", "equipment", "reagent"):
                if isinstance(item.get(field), (str, int)) and str(item[field]).strip():
                    rid = str(item[field]).strip()
                    break
            if not rid:
                continue
            key = nk(rid)
            entry = reg.get(key)
            if entry is None:
                for reg_key, reg_entry in reg.items():
                    if reg_key and (key in reg_key or reg_key in key):
                        entry = reg_entry
                        break
            if entry is None:
                entry = self._resource_entry(item, None, "%s[%d]" % (locator, idx)) or {
                    "id": rid, "name": rid, "kind": "service", "status": "unknown",
                    "lead_time_days": None, "unit_price": None, "price_unit": None,
                    "capacity_hours_per_week": None, "booked_hours_per_week": None,
                    "stock": None, "pack_size": None, "spec": {},
                    "contract": True, "locator": "%s[%d]" % (locator, idx),
                    "raw": snip(item)}
                reg[key] = entry
            base = self._resource_entry(item, entry.get("kind"), "%s[%d]" % (locator, idx))
            if base:
                for field in ("status", "lead_time_days", "unit_price", "price_unit",
                              "capacity_hours_per_week", "booked_hours_per_week",
                              "stock", "pack_size", "available_from"):
                    if base.get(field) is not None:
                        entry[field] = base[field]
                entry["spec"].update(base.get("spec") or {})
            entry["contract"] = True
            entry["contract_locator"] = "%s[%d]" % (locator, idx)

    # -- 参数域
    def _domains(self):
        key, val = get_val(self.root, DOMAIN_KEYS)
        out = {}
        if val is None:
            return
        locator = "%s#%s" % (self.loc(), key)
        if isinstance(val, dict):
            for name, spec in val.items():
                pr = parse_range(spec)
                if pr:
                    pr["locator"] = "%s.%s" % (locator, name)
                    pr["raw"] = snip(spec)
                    out[str(name)] = pr
        elif isinstance(val, list):
            for idx, item in enumerate(val):
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("parameter") or item.get("id")
                spec = item.get("range") or item.get("domain") or item
                pr = parse_range(spec)
                if pr and name:
                    pr["locator"] = "%s[%d]" % (locator, idx)
                    pr["raw"] = snip(item)
                    out[str(name)] = pr
        self.model["domains"] = out

    # -- 全局上下文
    def _context(self):
        ctx = {"budget": None, "planned_start": None, "decision_date": None,
               "personnel": [], "sample_inventory": [], "ethics": [],
               "hazard_declared": [], "window": None}
        _, budget = get_val(self.root, BUDGET_KEYS)
        ctx["budget"] = parse_quantity(budget, "CNY") if budget is not None else None
        _, window = get_val(self.root, ("time_window", "window", "total_time_limit",
                                       "总时长上限", "时间窗"))
        if window is not None:
            q = parse_quantity(window, "min")
            if q and q["dim"] == "time":
                ctx["window"] = {"value": q["value"], "unit": "min", "raw": q["raw"]}
            elif isinstance(window, (int, float)):
                ctx["window"] = {"value": float(window), "unit": "min", "raw": str(window)}
        _, start = get_val(self.root, START_KEYS)
        ctx["planned_start"] = parse_date(start) if start is not None else None
        _, decision = get_val(self.root, DECISION_KEYS)
        ctx["decision_date"] = parse_date(decision) if decision is not None else None
        _, personnel = get_val(self.root, PERSONNEL_KEYS)
        ctx["personnel"] = []
        for idx, (_, item) in enumerate(as_items(personnel)):
            if isinstance(item, dict):
                ctx["personnel"].append({
                    "role": str(item.get("role") or item.get("name") or "member"),
                    "n": num_or_none(item.get("n") or item.get("count")) or 1,
                    "hours_per_week": num_or_none(item.get("hours_per_week")
                                                  or item.get("capacity_hours_per_week"))})
        _, samples = get_val(self.root, SAMPLE_SRC_KEYS)
        for idx, (_, item) in enumerate(as_items(samples)):
            if isinstance(item, dict):
                q = parse_quantity(item.get("n") or item.get("count") or item.get("available"))
                ctx["sample_inventory"].append({
                    "id": str(item.get("id") or item.get("type") or "sample"),
                    "n": q["value"] if q else None,
                    "volume": parse_quantity(item.get("volume"), item.get("volume_unit"))
                    if item.get("volume") is not None else None,
                    "consent": bool(item.get("consent") or item.get("consented")),
                    "locator": "%s#%s[%d]" % (self.loc(), "samples", idx),
                    "raw": snip(item)})
        _, ethics = get_val(self.root, ETHICS_KEYS)
        ctx["ethics"] = self._ethics(ethics)
        for plan in self.model["plans"]:
            if plan["flags"].get("hazard"):
                ctx["hazard_declared"].extend(plan["flags"]["hazard"][:3])
        self.model["context"] = ctx

    def _ethics(self, value):
        out = []
        locator = "%s#ethics" % self.loc()
        items = []
        if isinstance(value, dict):
            if any(isinstance(v, dict) for v in value.values()):
                for scope, val in value.items():
                    if isinstance(val, dict):
                        merged = dict(val)
                        merged.setdefault("scope", scope)
                        items.append(merged)
                    elif isinstance(val, str):
                        items.append({"scope": scope, "status": val})
            else:
                items.append(value)
        elif isinstance(value, list):
            items = [v for v in value if isinstance(v, dict)]
        for idx, item in enumerate(items):
            status_raw = item.get("status") or item.get("state") or item.get("审批状态")
            s = nk(status_raw) if isinstance(status_raw, str) else ""
            if any(k in s for k in ("approved", "已批准", "通过", "批准", "有效")):
                status = "approved"
            elif any(k in s for k in ("pending", "审批中", "待批", "申请中", "reviewing")):
                status = "pending"
            elif any(k in s for k in ("expired", "过期", "失效")):
                status = "expired"
            elif any(k in s for k in ("rejected", "驳回", "拒绝")):
                status = "rejected"
            elif any(k in s for k in ("none", "未申请", "无")):
                status = "not_applied"
            else:
                status = "unknown"
            out.append({"scope": str(item.get("scope") or item.get("kind") or item.get("type") or "unspecified"),
                        "status": status, "status_raw": snip(status_raw, 60),
                        "valid_until": parse_date(item.get("valid_until") or item.get("expiry")
                                                  or item.get("有效期")),
                        "approval_id": item.get("approval_id") or item.get("id"),
                        "duration_days": num_or_none(item.get("duration_days")
                                                     or item.get("review_days")),
                        "locator": "%s[%d]" % (locator, idx), "raw": snip(item)})
        return out

    def _attach_material_notes(self, files, case_path):
        extras = []
        for rel, path, size, ext in files:
            if os.path.abspath(path) == os.path.abspath(case_path):
                continue
            if ext in (".md", ".txt", ".csv", ".tsv"):
                extras.append({"path": rel, "bytes": size})
        self.model["notes"].append("附带文本材料 %d 个（未自动并入计划模型，供 Agent 阅读核对）" % len(extras))
        self.model["extra_materials"] = extras


# ---------------------------------------------------------------- 校验引擎

class Engine(object):
    """基于 plan_model 做确定性校验，产出 calc 与 finding。"""

    def __init__(self, model):
        self.m = model
        self.findings = []
        self.calcs = []
        self._fids = set()
        self._cid_n = 0

    def calc(self, cid, label, formula, inputs, value, unit, method, locators=None, notes=None):
        if cid in {c["id"] for c in self.calcs}:
            return None
        rec = {"id": cid, "label": label, "formula": formula, "inputs": inputs,
               "value": None if value is None else round(float(value), 4),
               "unit": unit, "method": method,
               "locators": locators or [], "notes": notes or []}
        self.calcs.append(rec)
        return rec

    def find(self, fid, dim, subject, claim, verdict, plan_id=None, locator=None,
             quote=None, evidence=None, conditions=None, resource_state=None,
             calc_refs=None, confidence="medium", needs_expert=False, notes=None,
             scope=None):
        if fid in self._fids:
            return None
        self._fids.add(fid)
        if verdict not in VERDICTS:
            verdict = "unconfirmed"
            notes = (notes or []) + ["verdict 非法，已降级为 unconfirmed"]
        if verdict in ("ok", "conditional", "blocked", "conflict") and not evidence:
            evidence = [{"locator": locator, "quote": quote or "", "kind": "plan"}]
        elif not evidence:
            evidence = []
        for e in evidence:
            e.setdefault("kind", "plan")
            if e.get("scope") is None and scope:
                e["scope"] = scope
        if resource_state and dim == "equipment_materials":
            if resource_state not in RESOURCE_STATES:
                resource_state = "unknown"
        rec = {"id": fid, "dimension": dim, "plan_id": plan_id, "subject": subject,
               "claim": claim, "verdict": verdict, "resource_state": resource_state,
               "conditions": conditions or [], "evidence": evidence,
               "calc_refs": calc_refs or [], "confidence": confidence,
               "needs_expert": needs_expert, "notes": notes or []}
        self.findings.append(rec)
        return rec


def reg_lookup(reg, key):
    k = nk(key)
    if not k:
        return None
    if k in reg:
        return reg[k]
    for rk, entry in reg.items():
        if rk and (k == rk or (len(k) >= 3 and len(rk) >= 3 and (k in rk or rk in k))):
            return entry
    return None


def domain_lookup(domains, name):
    k = nk(name)
    if not k:
        return None
    if k in domains:
        return domains[k]
    for dk, spec in domains.items():
        if dk and (k == dk or (len(k) >= 3 and len(dk) >= 3 and (k in dk or dk in k))):
            return spec
    return None


def critical_path(steps):
    """返回 (cp_min, has_cycle, unresolved)。依赖可为序号/名称/step_id。"""
    dur = {}
    name_to_id = {}
    for s in steps:
        dur[s["step_id"]] = s["duration"]["value"] if s.get("duration") else 0.0
        name_to_id[nk(s["name"])] = s["step_id"]
        name_to_id[nk(s["step_id"])] = s["step_id"]
        name_to_id[str(s["index"])] = s["step_id"]
        name_to_id[str(s["index"] + 1)] = s["step_id"]
    edges = {}
    unresolved = []
    for s in steps:
        deps = []
        for d in s.get("depends_on") or []:
            key = nk(d)
            if key in name_to_id:
                deps.append(name_to_id[key])
            elif d in name_to_id:
                deps.append(name_to_id[d])
            else:
                unresolved.append({"step": s["step_id"], "dep": d})
        edges[s["step_id"]] = deps
    state = {}
    memo = {}
    cycle = [False]

    def longest(node):
        if node in state and state[node] == 1:
            cycle[0] = True
            return 0.0
        if node in memo:
            return memo[node]
        state[node] = 1
        best = 0.0
        for dep in edges.get(node, []):
            if dep in dur:
                best = max(best, longest(dep))
        state[node] = 2
        memo[node] = dur.get(node, 0.0) + best
        return memo[node]

    cp = 0.0
    for node in dur:
        cp = max(cp, longest(node))
    return cp, cycle[0], unresolved


def _week_span(plan_metrics):
    span_days = plan_metrics.get("span_days") or 1.0
    return max(span_days / 7.0, 1.0)


def compute_metrics(eng, plan, reg, ctx):
    steps = plan["steps"]
    pid = plan["plan_id"]
    metrics = {"time": {}, "equipment_occ": [], "material_use": [], "cost": {},
               "samples": {}, "schedule": {}, "arms": None}
    known = [s["duration"]["value"] for s in steps if s.get("duration")]
    missing_dur = [s["name"] for s in steps if not s.get("duration")]
    seq = float(sum(known))
    eng.calc("%s.T.wall" % pid, "步骤时长累加（顺序上界）",
             "Σ step.duration_min", {"durations_min": [round(v, 4) for v in known]},
             seq, "min", "sequential_upper_bound",
             locators=[s["locator"] for s in steps if s.get("duration")],
             notes=(["%d 步未给出时长：%s" % (len(missing_dur), "、".join(missing_dur[:5]))]
                    if missing_dur else []))
    operator_known = [s["operator"]["value"] for s in steps if s.get("operator")]
    eng.calc("%s.T.operator" % pid, "人工操作时长累加", "Σ step.operator_min",
             {"values_min": [round(v, 4) for v in operator_known]},
             float(sum(operator_known)), "min", "sum",
             locators=[s["locator"] for s in steps if s.get("operator")])
    cp, has_cycle, unresolved = critical_path(steps)
    if steps:
        eng.calc("%s.T.critical" % pid, "关键路径（依赖可用时）",
                 "max over DAG of (duration + Σ predecessors)",
                 {"cp_min": round(cp, 4)}, cp, "min",
                 "critical_path" if not has_cycle else "fallback_sequential",
                 notes=(["检测到依赖环，回退为顺序和"] if has_cycle else [])
                       + (["未解析依赖 %d 处" % len(unresolved)] if unresolved else []))
    span_days = (cp or seq) / 1440.0 if (cp or seq) else None
    metrics["time"] = {"sequential_min": seq, "operator_min": float(sum(operator_known)),
                       "critical_min": cp, "span_days": span_days,
                       "missing_duration_steps": missing_dur,
                       "has_cycle": has_cycle, "unresolved_deps": unresolved}

    occ = {}
    for s in steps:
        base = s["duration"]["value"] if s.get("duration") else None
        for e in s.get("equipment") or []:
            mins = e.get("minutes") if e.get("minutes") is not None else base
            key = nk(e["id"])
            occ[key] = occ.get(key, 0.0) + (mins or 0.0)
    occ_list = []
    for key, mins in sorted(occ.items()):
        e = reg_lookup(reg, key)
        occ_list.append({"id": (e or {}).get("id") or key, "occupancy_min": mins,
                         "hours": mins / 60.0,
                         "hours_per_week": mins / 60.0 / _week_span({"span_days": span_days or 1.0}),
                         "registry_key": key, "registered": bool(e)})
    metrics["equipment_occ"] = occ_list

    use = {}
    for s in steps:
        for m in s.get("materials") or []:
            q = m.get("qty")
            val = q["value"] if q else None
            dim = q["dim"] if q else None
            key = nk(m["id"])
            agg = use.setdefault(key, {"id": m["id"], "total": 0.0, "dim": dim, "parts": []})
            if val is not None:
                if agg["dim"] in (None, dim) and agg["total"] == 0:
                    agg["dim"] = dim
                if agg["dim"] == dim or not agg["parts"]:
                    agg["total"] = (agg["total"] or 0.0) + val
                agg["parts"].append({"value": val, "dim": dim, "raw": q["raw"] if q else None})
    metrics["material_use"] = [{"id": v["id"], "total": v["total"], "dim": v["dim"],
                                "parts": v["parts"]} for v in use.values()]

    cost_items = []
    for item in occ_list:
        e = reg_lookup(reg, item["registry_key"])
        if e and e.get("unit_price") is not None and e.get("price_unit") \
           and "h" in str(e["price_unit"]).lower():
            cost = item["hours"] * float(e["unit_price"])
            cost_items.append({"resource": item["id"], "type": "equipment",
                               "basis": "hours*unit_price", "value": cost,
                               "calc": "%s hours × %s %s" % (round(item["hours"], 2),
                                                             e["unit_price"], e.get("price_unit"))})
    for item in metrics["material_use"]:
        e = reg_lookup(reg, item["registry_key"]) if "registry_key" in item else reg_lookup(reg, item["id"])
        if e and e.get("unit_price") is not None:
            need = item["total"]
            stock = e.get("stock")
            deficit = max(0.0, need - stock) if stock is not None else need
            pack = e.get("pack_size")
            unit_price = float(e["unit_price"])
            if pack and pack > 0:
                packs = ceil_div(deficit, pack)
                cost = (packs or 0) * unit_price
                basis = "%d 包 × %s" % (packs or 0, unit_price)
            else:
                cost = deficit * unit_price
                basis = "%s × %s" % (round(deficit, 4), unit_price)
            cost_items.append({"resource": item["id"], "type": "material",
                               "basis": basis, "value": cost, "deficit": deficit})
    total_cost = float(sum(c["value"] for c in cost_items))
    eng.calc("%s.C.total" % pid, "已登记单价项的成本合计",
             "Σ equipment_hours×price + Σ material_deficit×price",
             {"items": [{"r": c["resource"], "v": round(c["value"], 2)} for c in cost_items]},
             total_cost, "CNY", "declared_prices_only",
             notes=["仅统计资源清单中给出单价的项；未标价项不计入"])
    metrics["cost"] = {"items": cost_items, "total": total_cost,
                       "budget": plan.get("budget") or ctx.get("budget")}
    budget = plan.get("budget") or ctx.get("budget")
    if budget and budget.get("value") is not None:
        metrics["cost"]["budget_value"] = budget["value"]
        metrics["cost"]["overrun"] = total_cost - budget["value"]

    # 样本
    samples = plan.get("samples") or []
    total_n = None
    arms = None
    for var in plan.get("variables") or []:
        if var.get("levels"):
            arms = len(var["levels"])
            break
    if arms is None and plan.get("controls"):
        arms = 2
    for item in samples:
        if item.get("n") is not None:
            total_n = item["n"] * (arms or 1) if item.get("per_arm") else item["n"]
            break
    metrics["samples"] = {"required_n": total_n, "arms": arms, "declared": samples}
    metrics["arms"] = arms

    # 采购前置
    lead_days = []
    for item in occ_list:
        e = reg_lookup(reg, item["registry_key"])
        if e and e.get("lead_time_days") is not None and e.get("status") in ("purchase", "outsource", "unknown"):
            lead_days.append((item["id"], float(e["lead_time_days"])))
    for item in metrics["material_use"]:
        e = reg_lookup(reg, item["id"])
        if e and e.get("lead_time_days") is not None and e.get("status") in ("purchase", "outsource", "unknown"):
            lead_days.append((item["id"], float(e["lead_time_days"])))
    max_lead = max([d for _, d in lead_days], default=None)
    metrics["schedule"] = {"lead_items": lead_days, "max_lead_days": max_lead}
    return metrics


def _evidence(locator, quote, kind="plan", scope=None):
    item = {"locator": locator, "quote": quote or "", "kind": kind}
    if scope:
        item["scope"] = scope
    return item


def check_plan(eng, model, plan):
    pid = plan["plan_id"]
    reg = model["registry"]
    domains = model["domains"]
    ctx = model["context"]
    loc = plan["locator"]
    metrics = compute_metrics(eng, plan, reg, ctx)
    plan["metrics"] = metrics

    # ---- 1. 假设与变量
    question = plan["hypothesis"] or plan["goal"]
    if question:
        eng.find("%s.AV-1" % pid, "assumptions_variables", "研究问题/假设",
                 "方案给出可检验的研究问题或假设：%s" % snip(question, 100),
                 "ok", plan_id=pid, locator=loc, quote=snip(question, 140))
    else:
        eng.find("%s.AV-1" % pid, "assumptions_variables", "研究问题/假设",
                 "方案未给出可检验的研究问题或假设，无法判定变量与判读标准是否指向同一目标",
                 "missing", plan_id=pid, locator=loc, quote="",
                 scope="检索了 question/objective/hypothesis 等字段", confidence="high")
    if plan["assumptions"]:
        eng.find("%s.AV-2" % pid, "assumptions_variables", "关键假设",
                 "方案列出 %d 条假设，界定外推边界" % len(plan["assumptions"]),
                 "ok", plan_id=pid, evidence=[_evidence(plan["assumptions"][0]["locator"],
                                                        plan["assumptions"][0]["text"], "plan")])
    else:
        eng.find("%s.AV-2" % pid, "assumptions_variables", "关键假设",
                 "方案未列出关键假设（如样本稳定性、设备精度、批次一致性），结论外推边界不清",
                 "conditional", plan_id=pid, locator=loc, quote="",
                 conditions=["补充至少一条可验证的边界假设并给出依据"],
                 scope="检索了 assumptions/hypotheses 字段", confidence="high")
    if plan["variables"]:
        bad = [v for v in plan["variables"] if not (v.get("unit") or v.get("levels"))]
        if bad:
            eng.find("%s.AV-3" % pid, "assumptions_variables", "变量可测量性",
                     "%d 个变量未给出单位或水平，无法判读测量结果" % len(bad),
                     "conditional", plan_id=pid,
                     evidence=[_evidence(v["locator"], v["name"], "plan") for v in bad[:3]],
                     conditions=["为每个变量补单位或分组水平，并写明测量方法"],
                     notes=["涉及：%s" % "、".join(v["name"] for v in bad[:8])])
        else:
            eng.find("%s.AV-3" % pid, "assumptions_variables", "变量可测量性",
                     "%d 个变量均给出单位或水平，具备可测量定义" % len(plan["variables"]),
                     "ok", plan_id=pid,
                     evidence=[_evidence(plan["variables"][0]["locator"],
                                         plan["variables"][0]["name"], "plan")])
    else:
        eng.find("%s.AV-3" % pid, "assumptions_variables", "变量可测量性",
                 "方案未定义自变量/因变量，无法进入对照与参数审查",
                 "missing", plan_id=pid, locator=loc, quote="",
                 scope="检索了 variables/outcomes/endpoints 字段", confidence="high")

    # ---- 2. 对照组
    if plan["controls"]:
        eng.find("%s.CT-1" % pid, "controls", "对照组设置",
                 "方案给出对照/参照条件：%s" % snip(plan["controls"][0]["text"], 80),
                 "ok", plan_id=pid,
                 evidence=[_evidence(plan["controls"][0]["locator"],
                                     plan["controls"][0]["text"], "plan")])
    else:
        eng.find("%s.CT-1" % pid, "controls", "对照组设置",
                 "方案未写对照组或对照条件，无法排除混淆变量（方案未写≠已满足）",
                 "missing", plan_id=pid, locator=loc, quote="",
                 scope="检索了 control_group/vehicle/sham 字段", confidence="high")
    if metrics["samples"].get("required_n") is not None:
        n = metrics["samples"]["required_n"]
        n_str = str(int(n)) if float(n).is_integer() else str(n)
        eng.find("%s.CT-2" % pid, "controls", "样本量/臂数",
                 "样本需求按臂数展开为 %s 例（臂数=%s）" % (n_str, metrics["samples"].get("arms")),
                 "ok" if metrics["samples"].get("arms") else "conditional",
                 plan_id=pid,
                 evidence=[_evidence(loc, snip(plan["samples"][0]["raw"], 120), "plan")]
                 if plan["samples"] else None,
                 calc_refs=["%s.T.wall" % pid],
                 conditions=([] if metrics["samples"].get("arms")
                             else ["需确认分组臂数后重算每臂样本量"]))
    else:
        eng.find("%s.CT-2" % pid, "controls", "样本量/臂数",
                 "方案未给出每臂样本量与分组数，样本规模无法核验",
                 "missing", plan_id=pid, locator=loc, quote="",
                 scope="检索了 samples/n/per_arm 字段", confidence="high")

    # ---- 3. 设备与材料
    for item in metrics["equipment_occ"]:
        e = reg_lookup(reg, item["registry_key"])
        fid = "%s.EM.%s" % (pid, item["registry_key"])
        if e is None:
            eng.find(fid, "equipment_materials", "设备:" + item["id"],
                     "所需设备未在资源清单/合同中出现，可用性未知",
                     "missing", plan_id=pid,
                     evidence=[_evidence(loc, item["id"], "plan",
                                         scope="设备占用来自步骤清单")],
                     confidence="high", resource_state="unknown")
            continue
        status = e.get("status") or "unknown"
        if status == "unavailable":
            eng.find(fid, "equipment_materials", "设备:" + item["id"],
                     "设备状态为不具备/停用，当前方案该步骤不可执行",
                     "blocked", plan_id=pid, resource_state="unavailable",
                     evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                     conditions=["更换设备型号或改外协，并重新排期"],
                     calc_refs=[], confidence="high")
        elif status in ("purchase", "outsource"):
            lead = e.get("lead_time_days")
            price = e.get("unit_price")
            eng.find(fid, "equipment_materials", "设备:" + item["id"],
                     "设备状态为%s，需采购/外协后方可使用%s%s" % (
                         STATE_ZH.get(status, status),
                         "，货期 %s 天" % lead if lead is not None else "",
                         "，参考单价 %s" % price if price is not None else ""),
                     "conditional", plan_id=pid,
                     resource_state=("purchase" if status == "purchase" else "outsource"),
                     evidence=[_evidence(e.get("contract_locator") or e["locator"],
                                         e.get("status_raw", "") or e["raw"], "resource")],
                     conditions=(["确认下单日不晚于计划开始前 %s 天" % lead] if lead is not None
                                 else ["补充货期/交付周期，确认能否赶上计划开始时间"]),
                     needs_expert=lead is None, confidence="medium")
        elif status == "available":
            cap = e.get("capacity_hours_per_week")
            booked = e.get("booked_hours_per_week")
            need = item["hours_per_week"]
            if cap is not None:
                free = cap - (booked or 0.0)
                if free <= 0:
                    eng.find(fid, "equipment_materials", "设备:" + item["id"],
                             "设备每周可用机时已被预约占满（capacity %.1f h，booked %.1f h）"
                             % (cap, booked or 0.0),
                             "conditional", plan_id=pid, resource_state="available",
                             evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                             conditions=["重新预约机时或改外协，确认新的可用窗口"],
                             calc_refs=[])
                    continue
                util = need / free
                eng.calc("%s.EM.%s.util" % (pid, item["registry_key"]),
                         "设备每周机时占用率", "need / (capacity - booked)",
                         {"need_h_per_week": round(need, 3), "capacity": cap,
                          "booked": booked or 0.0},
                         util, "ratio", "utilization")
                if util > 1.0:
                    eng.find(fid, "equipment_materials", "设备:" + item["id"],
                             "每周机时占用率 %.0f%% 超过可用机时（%.1f h/周）" % (
                                 util * 100, free),
                             "conditional", plan_id=pid, resource_state="available",
                             evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                             conditions=["延长排期或预约额外机时：至少需 %.1f 个可用周"
                                         % (need / free)],
                             calc_refs=["%s.EM.%s.util" % (pid, item["registry_key"])])
                else:
                    eng.find(fid, "equipment_materials", "设备:" + item["id"],
                             "设备已具备，每周机时占用率 %.0f%% 在可用范围内" % (util * 100),
                             "ok", plan_id=pid, resource_state="available",
                             evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                             calc_refs=["%s.EM.%s.util" % (pid, item["registry_key"])])
            else:
                eng.find(fid, "equipment_materials", "设备:" + item["id"],
                         "设备已具备，但未给出每周可用机时，无法判定排期是否冲突",
                         "unconfirmed", plan_id=pid, resource_state="available",
                         evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                         conditions=["补充每周可用机时或预约窗口"], needs_expert=True)
        else:
            eng.find(fid, "equipment_materials", "设备:" + item["id"],
                     "设备可用状态未在清单中写明，无法判定是否到位",
                     "unconfirmed", plan_id=pid, resource_state="unknown",
                     evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                     conditions=["补充设备状态：已具备/可外协/需采购"], needs_expert=True)

    for req in plan.get("resources_required") or []:
        e = reg_lookup(reg, req["id"])
        if e is None:
            eng.find("%s.EM.req.%s" % (pid, nk(req["id"])), "equipment_materials",
                     "设备/材料:" + req["id"],
                     "方案声明需要的资源未在清单登记",
                     "missing", plan_id=pid, resource_state="unknown",
                     evidence=[_evidence(req["locator"], req["raw"], "plan")], confidence="high")
        elif (e.get("status") or "unknown") == "unavailable":
            eng.find("%s.EM.req.%s" % (pid, nk(req["id"])), "equipment_materials",
                     "设备/材料:" + req["id"], "方案声明需要的资源在清单中标记为不具备",
                     "blocked", plan_id=pid, resource_state="unavailable",
                     evidence=[_evidence(e["locator"], e.get("status_raw", "") or e["raw"], "resource")],
                     conditions=["更换资源或改外协"])

    for item in metrics["material_use"]:
        e = reg_lookup(reg, item["id"])
        fid = "%s.EM.mat.%s" % (pid, nk(item["id"]))
        if e is None:
            eng.find(fid, "equipment_materials", "材料:" + item["id"],
                     "所需材料未在清单/合同出现，规格与货期无法核验",
                     "missing", plan_id=pid,
                     evidence=[_evidence(loc, item["id"], "plan")], confidence="high")
            continue
        stock = e.get("stock")
        need = item["total"]
        pack = e.get("pack_size")
        if stock is not None:
            deficit = max(0.0, need - stock)
            packs = ceil_div(deficit, pack) if pack else (ceil_div(deficit, 1) if deficit else 0)
            if deficit <= 0:
                eng.find(fid, "equipment_materials", "材料:" + item["id"],
                         "库存 %.2f 覆盖用量 %.2f，无需采购" % (stock, need),
                         "ok", plan_id=pid,
                         evidence=[_evidence(e["locator"], e.get("raw"), "resource")])
            else:
                eng.find(fid, "equipment_materials", "材料:" + item["id"],
                         "库存不足：需额外 %.2f，约 %s 包/单位" % (deficit, packs if packs else "未知"),
                         "conditional", plan_id=pid,
                         evidence=[_evidence(e["locator"], e.get("raw"), "resource")],
                         conditions=["采购不足量并确认货期" + (
                             " %s 天" % e["lead_time_days"] if e.get("lead_time_days") is not None else "")],
                         resource_state="purchase")
        else:
            if e.get("status") in ("purchase", "outsource") or e.get("unit_price") is not None:
                eng.find(fid, "equipment_materials", "材料:" + item["id"],
                         "材料未给出在库量，需按采购/外协处理，货期与价格见合同",
                         "conditional", plan_id=pid, resource_state="purchase",
                         evidence=[_evidence(e.get("contract_locator") or e["locator"], e.get("raw"), "resource")],
                         conditions=["确认采购量与到货时间"])
            else:
                eng.find(fid, "equipment_materials", "材料:" + item["id"],
                         "材料库存状态未写明，无法判定是否充足",
                         "unconfirmed", plan_id=pid, resource_state="unknown",
                         evidence=[_evidence(e["locator"], e.get("raw"), "resource")],
                         conditions=["补充库存量/可外协/需采购"], needs_expert=True)

    # ---- 4. 时间成本
    if metrics["time"]["missing_duration_steps"]:
        eng.find("%s.TC-1" % pid, "time_cost", "步骤时长",
                 "%d 步未给出时长，总时长不可完整累加" % len(metrics["time"]["missing_duration_steps"]),
                 "conditional", plan_id=pid,
                 evidence=[_evidence(loc, "、".join(metrics["time"]["missing_duration_steps"][:4]), "plan",
                                     scope="缺失步骤名")],
                 conditions=["补齐缺失步骤的时长与单位"], calc_refs=["%s.T.wall" % pid])
    else:
        dur_steps = [s for s in plan["steps"] if s.get("duration")]
        eng.find("%s.TC-1" % pid, "time_cost", "步骤时长",
                 "全部步骤时长已给出，顺序累计 %.1f min（关键路径 %.1f min）" % (
                     metrics["time"]["sequential_min"], metrics["time"]["critical_min"]),
                 "ok", plan_id=pid, calc_refs=["%s.T.wall" % pid, "%s.T.critical" % pid],
                 evidence=[_evidence(dur_steps[0]["locator"], dur_steps[0]["duration"]["raw"], "plan")]
                 if dur_steps else [_evidence(loc, "", "scope")])
    window = ctx.get("window")
    if window and window.get("value"):
        wall = metrics["time"]["critical_min"] or metrics["time"]["sequential_min"]
        ok_time = wall <= window["value"]
        eng.find("%s.TC-2" % pid, "time_cost", "时间窗",
                 "预计总时长 %.1f %s 相对时间窗 %.1f %s：%s" % (
                     wall, "min", window["value"], window.get("unit") or "min",
                     "可满足" if ok_time else "超期"),
                 "ok" if ok_time else "blocked", plan_id=pid,
                 evidence=[_evidence(loc, window.get("raw") or "", "constraint")],
                 calc_refs=["%s.T.wall" % pid])
    pers_hours = sum((p.get("hours_per_week") or 0) * (p.get("n") or 1)
                     for p in ctx.get("personnel") or [])
    if metrics["time"]["operator_min"] and pers_hours:
        weeks = _week_span(metrics)
        need_hrs = metrics["time"]["operator_min"] / 60.0 / weeks
        eng.calc("%s.TC.personnel" % pid, "人工周均负荷", "operator_hours / weeks",
                 {"operator_min": round(metrics["time"]["operator_min"], 2),
                  "weeks": round(weeks, 3)}, need_hrs, "h/week", "personnel_load")
        if need_hrs > pers_hours:
            eng.find("%s.TC-3" % pid, "time_cost", "人力配置",
                     "人工操作周均 %.1f h 超过在册人力 %.1f h/周，需加人或延长排期" % (need_hrs, pers_hours),
                     "conditional", plan_id=pid, calc_refs=["%s.TC.personnel" % pid],
                     evidence=[_evidence(loc, "%s 人 × %.1f h/周" % (
                         sum((p.get("n") or 1) for p in ctx.get("personnel") or []), pers_hours), "computed")],
                     conditions=["增加人手或延长排期到至少 %.1f 周" % (metrics["time"]["operator_min"] / 60.0 / pers_hours)])
        else:
            eng.find("%s.TC-3" % pid, "time_cost", "人力配置",
                     "人工操作周均 %.1f h 不超过在册人力 %.1f h/周" % (need_hrs, pers_hours),
                     "ok", plan_id=pid, calc_refs=["%s.TC.personnel" % pid],
                     evidence=[_evidence(loc, "在册人力 %.1f h/周" % pers_hours, "computed")])
    elif metrics["time"]["operator_min"]:
        eng.find("%s.TC-3" % pid, "time_cost", "人力配置",
                 "有 %.1f min 人工操作，但未给出可用人力，无法判定排期" % metrics["time"]["operator_min"],
                 "unconfirmed", plan_id=pid, calc_refs=["%s.T.operator" % pid],
                 conditions=["补充可用人力（人数×每周工时）"], needs_expert=True)
    max_lead = metrics["schedule"]["max_lead_days"]
    lead_ev = []
    for lead_id, lead_days in metrics["schedule"].get("lead_items", [])[:3]:
        entry = reg_lookup(reg, lead_id)
        lead_ev.append(_evidence((entry or {}).get("contract_locator") or (entry or {}).get("locator") or loc,
                                 "货期 %s 天" % lead_days if isinstance(lead_days, (int, float)) else str(lead_days),
                                 "resource"))
    if max_lead is not None:
        start = plan.get("start") or ctx.get("planned_start")
        decision = ctx.get("decision_date")
        if start and decision:
            avail = (start - decision).days
            ok_lead = max_lead <= avail
            eng.find("%s.TC-4" % pid, "time_cost", "采购前置",
                     "最长货期 %.0f 天 vs 距计划开始 %.0f 天：%s" % (
                         max_lead, avail, "来得及" if ok_lead else "来不及"),
                     "ok" if ok_lead else "blocked", plan_id=pid,
                     evidence=lead_ev or [_evidence(loc, "", "scope")],
                     conditions=([] if ok_lead else ["压缩货期或推迟开始至 %s" % (
                         decision + _dt.timedelta(days=max_lead)).isoformat()]))
        else:
            eng.find("%s.TC-4" % pid, "time_cost", "采购前置",
                     "最长货期 %.0f 天；缺少决策日/计划开始日期，无法判定窗口" % max_lead,
                     "conditional", plan_id=pid,
                     evidence=lead_ev or [_evidence(loc, "", "scope")],
                     conditions=["确认下单日不晚于计划开始前 %.0f 天" % max_lead])
    else:
        eng.find("%s.TC-4" % pid, "time_cost", "采购前置",
                 "方案未给出需采购项货期，采购窗口不可判定",
                 "unconfirmed", plan_id=pid, conditions=["补充各采购项货期"], needs_expert=True)
    budget = plan.get("budget") or ctx.get("budget")
    total_cost = metrics["cost"]["total"]
    budget_quote = (budget or {}).get("raw") if budget else ""
    if budget and budget.get("value") is not None:
        over = total_cost - budget["value"]
        eng.find("%s.TC-5" % pid, "time_cost", "预算",
                 "已标价成本合计 %.0f CNY vs 预算 %.0f CNY：%s" % (
                     total_cost, budget["value"], "超支 %.0f" % over if over > 0 else "未超支"),
                 "ok" if over <= 0 else "conditional", plan_id=pid,
                 calc_refs=["%s.C.total" % pid],
                 evidence=[_evidence(loc, budget_quote, "plan")],
                 conditions=([] if over <= 0 else ["削减成本或追加预算 %.0f CNY" % over]),
                 notes=["仅含已标价项"])
    elif metrics["cost"]["items"]:
        eng.find("%s.TC-5" % pid, "time_cost", "预算",
                 "存在已标价成本 %.0f CNY，但方案未给出预算上限" % total_cost,
                 "conditional", plan_id=pid, calc_refs=["%s.C.total" % pid],
                 evidence=[_evidence(loc, total_cost, "computed")],
                 conditions=["补充预算上限，并确认该成本可承担"])
    else:
        eng.find("%s.TC-5" % pid, "time_cost", "预算",
                 "方案未给出预算上限，且无标价成本项，成本无法核验",
                 "missing", plan_id=pid, scope="检索了 budget/cost 字段", quote="")

    # ---- 5. 参数范围
    params = plan["parameters"]
    for step in plan["steps"]:
        params = params + [dict(p, locator=p.get("locator") or step["locator"])
                           for p in step.get("params") or []]
    if not params:
        eng.find("%s.PR-1" % pid, "parameter_ranges", "关键参数",
                 "方案未给出任何关键参数（如温度、转速、样本量、试剂浓度），无法做范围校验",
                 "missing", plan_id=pid, locator=loc, quote="",
                 scope="检索了 parameters/settings 字段", confidence="high")
    else:
        checked = 0
        for param in params[:10]:
            spec = domain_lookup(domains, param["name"])
            pname = param["name"]
            if spec is None:
                eng.find("%s.PR.%s" % (pid, nk(pname)), "parameter_ranges", "参数:" + pname,
                         "参数未给出允许范围/参数域，无法判断是否越界",
                         "unconfirmed", plan_id=pid,
                         evidence=[_evidence(param.get("locator") or loc,
                                             param.get("raw") or pname, "plan")],
                         conditions=["补充该参数的允许范围及其依据"], needs_expert=True)
                continue
            checked += 1
            q = param.get("value")
            if q is None:
                eng.find("%s.PR.%s" % (pid, nk(pname)), "parameter_ranges", "参数:" + pname,
                         "参数名存在但数值缺失，无法比对参数域",
                         "missing", plan_id=pid,
                         evidence=[_evidence(param.get("locator") or loc, param.get("raw") or pname, "plan")])
                continue
            lo, hi = spec.get("min"), spec.get("max")
            if spec.get("allowed") is not None:
                allowed = [nk(str(x)) for x in spec["allowed"]]
                hit = nk(str(q["value"])) in allowed or str(q["value"]) in allowed
                eng.find("%s.PR.%s" % (pid, nk(pname)), "parameter_ranges", "参数:" + pname,
                         "参数取值 %s 在允许集合内" % q["raw"] if hit else "参数取值 %s 不在允许集合 %s" % (
                             q["raw"], "、".join(str(x) for x in spec["allowed"][:6])),
                         "ok" if hit else "blocked", plan_id=pid,
                         evidence=[_evidence(spec.get("locator") or loc, spec.get("raw") or "", "spec"),
                                   _evidence(param.get("locator") or loc, q["raw"], "plan")])
                continue
            if lo is not None and hi is not None and q["dim"] not in ("unknown",) \
               and unit_info(spec.get("unit"))[0] == q["dim"]:
                in_range = lo <= q["value"] <= hi
                eng.find("%s.PR.%s" % (pid, nk(pname)), "parameter_ranges", "参数:" + pname,
                         "参数取值 %s 相对允许范围 [%s, %s] %s：%s" % (
                             q["raw"], lo, hi, spec.get("unit") or "",
                             "在范围内" if in_range else "越界"),
                         "ok" if in_range else "blocked", plan_id=pid,
                         evidence=[_evidence(spec.get("locator") or loc, spec.get("raw") or "", "spec"),
                                   _evidence(param.get("locator") or loc, q["raw"], "plan")],
                         calc_refs=[],
                         conditions=([] if in_range else ["调整取值到 [%s, %s] %s 内" % (
                             lo, hi, spec.get("unit") or "")]))
            elif lo is not None or hi is not None:
                in_range = True
                if lo is not None and q["value"] < lo:
                    in_range = False
                if hi is not None and q["value"] > hi:
                    in_range = False
                eng.find("%s.PR.%s" % (pid, nk(pname)), "parameter_ranges", "参数:" + pname,
                         "参数取值 %s 相对边界（%s%s）：%s" % (
                             q["raw"], "≥" if lo is not None else "", lo if lo is not None else hi,
                             "在范围内" if in_range else "越界"),
                         "ok" if in_range else "blocked", plan_id=pid,
                         evidence=[_evidence(spec.get("locator") or loc, spec.get("raw") or "", "spec"),
                                   _evidence(param.get("locator") or loc, q["raw"], "plan")])
            else:
                eng.find("%s.PR.%s" % (pid, nk(pname)), "parameter_ranges", "参数:" + pname,
                         "参数域与取值单位不一致或参数域未给边界，无法机械比对",
                         "conflict", plan_id=pid,
                         evidence=[_evidence(spec.get("locator") or loc, spec.get("raw") or "", "spec"),
                                   _evidence(param.get("locator") or loc, q["raw"], "plan")],
                         notes=["参数取值单位：%s；参数域单位：%s" % (q.get("dim"), spec.get("unit"))])
        if checked == 0:
            eng.find("%s.PR-2" % pid, "parameter_ranges", "参数域覆盖",
                     "方案有参数但均未命中任何参数域，需补齐判定依据",
                     "conditional", plan_id=pid, conditions=["为关键参数补充允许范围与来源"],
                     needs_expert=True)

    # ---- 6. 风险与异常
    if plan["risks"]:
        unm = [r for r in plan["risks"] if not r.get("mitigation")]
        if unm:
            eng.find("%s.RS-1" % pid, "risks_exceptions", "风险处置",
                     "%d 条已声明风险中 %d 条未写处置/降级方案" % (len(plan["risks"]), len(unm)),
                     "conditional", plan_id=pid,
                     evidence=[_evidence(u["locator"], u["text"], "plan") for u in unm[:3]],
                     conditions=["为每条风险补处置、降级或转人工节点"])
        else:
            eng.find("%s.RS-1" % pid, "risks_exceptions", "风险处置",
                     "%d 条已声明风险均有处置方案" % len(plan["risks"]),
                     "ok", plan_id=pid,
                     evidence=[_evidence(plan["risks"][0]["locator"],
                                         plan["risks"][0]["text"], "plan")])
    else:
        eng.find("%s.RS-1" % pid, "risks_exceptions", "风险处置",
                 "方案未列出风险清单与异常处置（设备故障、批次差异、样本不足等）",
                 "missing", plan_id=pid, locator=loc, quote="",
                 scope="检索了 risks/hazards 字段", confidence="high")
    detected = []
    for item in metrics["equipment_occ"]:
        e = reg_lookup(reg, item["registry_key"])
        if e and (e.get("status") or "unknown") in ("purchase", "outsource") and e.get("lead_time_days") is None:
            detected.append("设备「%s」需采购/外协但无货期" % item["id"])
    if detected:
        eng.find("%s.RS-2" % pid, "risks_exceptions", "采购风险",
                 "检出采购风险：%s" % "；".join(detected[:3]),
                 "conditional", plan_id=pid,
                 evidence=[_evidence(loc, "；".join(detected[:3]), "plan")],
                 conditions=["补采购货期并设置到货验收节点"])
    else:
        lead_ok_ev = []
        for item in metrics["equipment_occ"]:
            e = reg_lookup(reg, item["registry_key"])
            if e and (e.get("status") or "unknown") in ("purchase", "outsource"):
                lead_ok_ev.append(_evidence(e.get("contract_locator") or e["locator"],
                                            e.get("status_raw") or e["raw"], "resource"))
        eng.find("%s.RS-2" % pid, "risks_exceptions", "采购风险",
                 "未检出无货期的采购项（或方案无采购依赖）",
                 "ok", plan_id=pid,
                 evidence=lead_ok_ev or [_evidence(loc, "", "scope")])
    # 设备故障降级
    bottleneck = [item for item in metrics["equipment_occ"]
                  if item.get("hours_per_week") and item["hours_per_week"] > 0 and
                  reg_lookup(reg, item["registry_key"]) and
                  (reg_lookup(reg, item["registry_key"]).get("capacity_hours_per_week") or 0) > 0 and
                  item["hours_per_week"] / max(
                      reg_lookup(reg, item["registry_key"]).get("capacity_hours_per_week", 0)
                      - (reg_lookup(reg, item["registry_key"]).get("booked_hours_per_week") or 0), 1e-9) > 0.8]
    if bottleneck:
        eng.find("%s.RS-3" % pid, "risks_exceptions", "瓶颈设备",
                 "设备「%s」机时利用率 >80%%，故障无冗余" % bottleneck[0]["id"],
                 "conditional", plan_id=pid,
                 evidence=[_evidence((reg_lookup(reg, bottleneck[0]["registry_key"]) or {}).get("locator") or loc,
                                     bottleneck[0]["id"], "resource")],
                 conditions=["为该设备准备备用设备/外协渠道或延长排期"])
    else:
        cand = max(metrics["equipment_occ"], key=lambda i: i.get("hours_per_week") or 0) \
            if metrics["equipment_occ"] else None
        entry = reg_lookup(reg, cand["registry_key"]) if cand else None
        eng.find("%s.RS-3" % pid, "risks_exceptions", "瓶颈设备",
                 "未检出>80%%利用率的瓶颈设备", "ok", plan_id=pid,
                 evidence=[_evidence((entry or {}).get("locator") or loc,
                                     (entry or {}).get("status_raw") or ((cand or {}).get("id") or ""),
                                     "resource")])

    # ---- 7. 伦理与安全
    scope_hits = []
    if plan["flags"]["human"]:
        scope_hits.append("human")
    if plan["flags"]["animal"]:
        scope_hits.append("animal")
    if plan["flags"]["hazard"]:
        scope_hits.append("hazard")
    if plan["flags"]["personal_data"]:
        scope_hits.append("personal_data")
    if not scope_hits:
        eng.find("%s.ES-1" % pid, "ethics_safety", "伦理/安全边界",
                 "基于全文关键词扫描，未检出人类受试者/动物/危化品/个人信息信号",
                 "ok", plan_id=pid,
                 evidence=[_evidence(loc, snip(plan["text"], 80), "plan",
                                     scope="关键词扫描：human/animal/hazard/personal_data")],
                 notes=["结论依赖文本扫描，非法规裁定；仍需由 PI 确认"])
        return
    for scope in scope_hits:
        ethics = [e for e in ctx.get("ethics") or [] if nk(e.get("scope", "")) == nk(scope)]
        fid = "%s.ES.%s" % (pid, scope)
        if not ethics:
            eng.find(fid, "ethics_safety", "伦理:" + scope,
                     "检出「%s」相关活动，但未找到对应伦理/安全审批记录" % scope,
                     "unconfirmed", plan_id=pid, needs_expert=True,
                     evidence=[_evidence(loc, "、".join(plan["flags"][scope][:4]), "plan")],
                     conditions=["提交伦理/安全审批；写清审批级别、材料与预计时长"])
            continue
        status = ethics[0]["status"]
        if status == "approved":
            exp = ethics[0].get("valid_until")
            if exp and ctx.get("planned_start") and exp < ctx["planned_start"]:
                eng.find(fid, "ethics_safety", "伦理:" + scope,
                         "审批已通过但有效期 %s 早于计划开始 %s，需续批" % (
                             exp.isoformat(), ctx["planned_start"].isoformat()),
                         "blocked", plan_id=pid, needs_expert=True,
                         evidence=[_evidence(ethics[0]["locator"], ethics[0].get("raw", ""), "approval")],
                         conditions=["办理延续审批或调整开始日期"])
            else:
                eng.find(fid, "ethics_safety", "伦理:" + scope,
                         "存在已批准记录%s，可进入实施" % (
                             "，有效期至 %s" % exp.isoformat() if exp else ""),
                         "ok", plan_id=pid, needs_expert=True,
                         evidence=[_evidence(ethics[0]["locator"], ethics[0].get("raw", ""), "approval")])
        elif status == "pending":
            dur = ethics[0].get("duration_days")
            eng.find(fid, "ethics_safety", "伦理:" + scope,
                     "审批状态为待批%s，当前不可开工" % (
                         "，预计 %s 天" % dur if dur is not None else ""),
                     "unconfirmed", plan_id=pid, needs_expert=True,
                     evidence=[_evidence(ethics[0]["locator"], ethics[0].get("raw", ""), "approval")],
                     conditions=["取得批件后再启动；将等待纳入排期"])
        elif status == "expired":
            eng.find(fid, "ethics_safety", "伦理:" + scope,
                     "审批记录已过期", "blocked", plan_id=pid, needs_expert=True,
                     evidence=[_evidence(ethics[0]["locator"], ethics[0].get("raw", ""), "approval")],
                     conditions=["重新提交审批"])
        elif status == "rejected":
            eng.find(fid, "ethics_safety", "伦理:" + scope,
                     "审批被驳回，方案需修改后重新提交", "blocked", plan_id=pid, needs_expert=True,
                     evidence=[_evidence(ethics[0]["locator"], ethics[0].get("raw", ""), "approval")],
                     conditions=["按驳回意见修订并重新送审"])
        else:
            eng.find(fid, "ethics_safety", "伦理:" + scope,
                     "审批状态不明，未经确认不得当作可执行事实",
                     "unconfirmed", plan_id=pid, needs_expert=True,
                     evidence=[_evidence(ethics[0]["locator"], ethics[0].get("raw", ""), "approval")],
                     conditions=["确认审批机构、状态与有效期"])
    if "hazard" in scope_hits:
        has_sop = any("处置" in r.get("text", "") or "sop" in r.get("text", "").lower()
                      for r in plan["risks"]) or any(
                          "废弃物" in a.get("text", "") or "安全" in a.get("text", "")
                          for a in plan["assumptions"])
        eng.find("%s.ES-2" % pid, "ethics_safety", "危化/生物安全",
                 "检出危化/生物安全相关信号，方案%s给出处置或 SOP" % ("未" if not has_sop else "已"),
                 "ok" if has_sop else "conditional", plan_id=pid, needs_expert=True,
                 conditions=([] if has_sop else ["补充危化品/废弃物处置 SOP、防护等级与负责人"]))


# ---------------------------------------------------------------- 方案比较

_WEIGHT = {"ok": 0, "conditional": 1, "unconfirmed": 1.5, "missing": 1.5,
           "conflict": 2, "blocked": 4}


def plan_fingerprint(finding):
    subject = nk(finding.get("subject") or "")
    dim = finding.get("dimension") or ""
    return "%s|%s" % (dim, subject)


def compare_plans(model, engine):
    plans = model["plans"]
    ranking = []
    for plan in plans:
        findings = [f for f in engine.findings if f["plan_id"] == plan["plan_id"]]
        counts = {v: sum(1 for f in findings if f["verdict"] == v) for v in VERDICTS}
        penalty = sum(_WEIGHT[f["verdict"]] for f in findings)
        metrics = plan.get("metrics") or {}
        ranking.append({
            "plan_id": plan["plan_id"], "locator": plan["locator"],
            "penalty": round(penalty, 2), "counts": counts,
            "wall_min": metrics.get("time", {}).get("critical_min")
                        or metrics.get("time", {}).get("sequential_min"),
            "cost": metrics.get("cost", {}).get("total"),
            "overrun": metrics.get("cost", {}).get("overrun"),
            "verdict": None,
        })
    if not ranking:
        return {"ranking": [], "diffs": [], "recommended": None, "summary": {}}
    ranking.sort(key=lambda r: (r["penalty"], r["wall_min"] if r["wall_min"] is not None else 1e18,
                                r["cost"] if r["cost"] is not None else 1e18))
    for rank, item in enumerate(ranking, 1):
        if item["counts"].get("blocked", 0) > 0:
            item["verdict"] = "blocked"
        elif item["counts"].get("conditional", 0) + item["counts"].get("unconfirmed", 0) \
                + item["counts"].get("missing", 0) > 0:
            item["verdict"] = "conditional"
        else:
            item["verdict"] = "ok"
        item["rank"] = rank
    recommended = ranking[0]["plan_id"]
    diffs = []
    for a, b in zip(plans, plans[1:]):
        sa = {nk(s["name"]): s for s in a["steps"]}
        sb = {nk(s["name"]): s for s in b["steps"]}
        added = [n for n in sb if n not in sa]
        removed = [n for n in sa if n not in sb]
        pa = {nk(p["name"]): p for p in a["parameters"]}
        pb = {nk(p["name"]): p for p in b["parameters"]}
        changed = []
        for name in sorted(set(pa) & set(pb)):
            va = pa[name].get("value")
            vb = pb[name].get("value")
            if (va or {}).get("raw") != (vb or {}).get("raw"):
                changed.append({"name": pa[name]["name"],
                                "from": (va or {}).get("raw"), "to": (vb or {}).get("raw")})
        fa = {plan_fingerprint(f): f for f in engine.findings if f["plan_id"] == a["plan_id"]}
        fb = {plan_fingerprint(f): f for f in engine.findings if f["plan_id"] == b["plan_id"]}
        resolved = [k for k in fa if k in fb
                    and _WEIGHT.get(fb[k]["verdict"], 0) < _WEIGHT.get(fa[k]["verdict"], 0)]
        introduced = [k for k in fb if k not in fa
                      and _WEIGHT.get(fb[k]["verdict"], 0) >= 1]
        diffs.append({"from": a["plan_id"], "to": b["plan_id"],
                      "steps_added": added, "steps_removed": removed,
                      "params_changed": changed,
                      "issues_resolved": resolved, "issues_introduced": introduced})
    return {"ranking": ranking, "diffs": diffs, "recommended": recommended,
            "summary": {"best": recommended,
                        "reason": "按 可行性惩罚分 → 关键路径 → 成本 排序；惩罚分越低越可行"}}


# ---------------------------------------------------------------- 结论与状态

def overall_conclusion(model, engine, comparison):
    plans = model["plans"]
    total = {v: sum(1 for f in engine.findings if f["verdict"] == v) for v in VERDICTS}
    if not plans:
        return {"verdict": "insufficient_evidence",
                "summary": "未提取到候选方案，无法进行七类约束审查",
                "conditions": [], "blocks": [], "notes": []}
    blocked = [f for f in engine.findings if f["verdict"] == "blocked"]
    conditional = [f for f in engine.findings if f["verdict"] in ("conditional", "unconfirmed", "missing", "conflict")]
    if blocked:
        verdict = "blocked"
        summary = "%d 项硬阻断（如设备不具备、参数越界、审批过期）；需先消解后再开工" % len(blocked)
    elif conditional:
        verdict = "conditional"
        summary = "%d 项条件/待确认；无硬阻断，满足所列条件后可开工" % len(conditional)
    else:
        verdict = "ok"
        summary = "七类约束均已通过机械校验；建议由 PI 对设备、伦理与供应商信息做最终确认"
    rec = comparison.get("recommended") if comparison else None
    return {"verdict": verdict, "summary": summary,
            "recommended_plan": rec,
            "counts": total,
            "conditions": [f["id"] + ": " + "；".join(f["conditions"]) for f in conditional[:12]],
            "blocks": [f["id"] + ": " + f["claim"] for f in blocked[:12]],
            "notes": ["结论来自 plan_model 的确定性校验 + Agent 复核；不把未经确认事项写成事实"]}


# ---------------------------------------------------------------- 输出契约

def contract_entries(contract):
    """把 output_contract 展平为 [(key, spec)]，键以 . 表示嵌套路径。"""
    out = []
    leaf_hints = ("type", "description", "required", "format", "default",
                  "example", "desc", "items", "enum")

    def is_leaf_spec(node):
        keys = {nk(k) for k in node}
        return bool(keys) and keys <= {nk(k) for k in leaf_hints}

    def walk(node, prefix):
        if isinstance(node, dict):
            if prefix and is_leaf_spec(node):
                out.append((prefix.rstrip("."), node))
                return
            for key, value in node.items():
                walk(value, "%s%s." % (prefix, key))
            if not node:
                out.append((prefix.rstrip("."), {}))
        else:
            out.append((prefix.rstrip("."), node))

    if isinstance(contract, dict):
        walk(contract, "")
    elif isinstance(contract, list):
        for idx, item in enumerate(contract):
            if isinstance(item, str):
                out.append((item, {"type": "string"}))
            elif isinstance(item, dict):
                key = item.get("name") or item.get("field") or ("field_%d" % idx)
                out.append((str(key), item))
    else:
        out.append(("result", contract))
    return out


def fill_contract(contract, result, route):
    """按 key 语义把契约字段回填到 result 顶层；返回 fill_report。"""
    report = {"filled": [], "unfilled": [], "echo": contract}
    if contract is None:
        return report
    for key, spec in contract_entries(contract):
        top = key.split(".")[0]
        value, source = route(top, key, spec)
        if top not in result:
            result[top] = value
        report["filled"].append({"key": top, "path": key, "source": source})
        if source is None:
            report["unfilled"].append({"key": top, "path": key,
                                       "reason": "未匹配到契约路由，缺值以 null 保留"})
    return report


# ---------------------------------------------------------------- 结果装配

def _best_plan(model, comparison):
    if comparison and comparison.get("recommended"):
        for plan in model["plans"]:
            if plan["plan_id"] == comparison["recommended"]:
                return plan
    return model["plans"][0] if model["plans"] else None


def experiment_plan_of(plan, model):
    if plan is None:
        return {"research_question": None, "assumptions": [], "variables": [],
                "controls": [], "steps": [], "parameters": [], "resources": [],
                "interpretation_criteria": [], "sample_plan": [], "personnel": []}
    registry = model["registry"]
    steps = []
    for s in plan["steps"]:
        steps.append({"step_id": s["step_id"], "name": s["name"],
                      "locator": s["locator"],
                      "duration": (s["duration"] or {}).get("raw"),
                      "operator": (s["operator"] or {}).get("raw"),
                      "wait": (s["wait"] or {}).get("raw"),
                      "equipment": [e["id"] for e in s["equipment"]],
                      "materials": [m["id"] for m in s["materials"]],
                      "depends_on": s["depends_on"]})
    resources = []
    for item in plan["metrics"].get("equipment_occ", []):
        e = reg_lookup(registry, item["registry_key"])
        resources.append({"id": item["id"], "kind": "equipment",
                          "occupancy_min": item["occupancy_min"],
                          "status": (e or {}).get("status") if e else None,
                          "state_zh": STATE_ZH.get((e or {}).get("status", "")) if e else None,
                          "locator": (e or {}).get("locator") if e else None})
    for item in plan["metrics"].get("material_use", []):
        e = reg_lookup(registry, item["id"])
        resources.append({"id": item["id"], "kind": "material",
                          "required": item["total"], "dim": item["dim"],
                          "stock": (e or {}).get("stock") if e else None,
                          "pack_size": (e or {}).get("pack_size") if e else None,
                          "status": (e or {}).get("status") if e else None})
    return {"research_question": plan["hypothesis"] or plan["goal"],
            "assumptions": [a["text"] for a in plan["assumptions"]],
            "variables": [{"name": v["name"], "role": v["role"], "unit": v["unit"],
                           "levels": v["levels"]} for v in plan["variables"]],
            "controls": [c["text"] for c in plan["controls"]],
            "steps": steps,
            "parameters": [{"name": p["name"], "value": (p.get("value") or {}).get("raw"),
                            "locator": p.get("locator")} for p in plan["parameters"]],
            "resources": resources,
            "interpretation_criteria": [c["text"] for c in plan["criteria"]],
            "sample_plan": plan["samples"],
            "personnel": plan["personnel"],
            "plan_id": plan["plan_id"], "locator": plan["locator"]}


def review_of(engine, plan_id=None):
    out = {dim: [] for dim in DIMENSIONS}
    for finding in engine.findings:
        if plan_id and finding["plan_id"] != plan_id:
            continue
        out.setdefault(finding["dimension"], []).append(finding)
    return out


def sources_of(model):
    out = []
    for f in model["input_files"]:
        out.append({"path": f["path"], "bytes": f["bytes"], "ext": f["ext"],
                    "role": "case" if f["path"] == model.get("case_file") else "material"})
    for ref in _extract_refs(model):
        out.append(ref)
    return out


def _extract_refs(model):
    out = []
    seen = set()
    text = dumps(model, 0)
    for url in re.findall(r"https?://[^\s\"'<>]+", text):
        if url not in seen:
            seen.add(url)
            out.append({"url": url, "kind": "url"})
    for doi in re.findall(r"10\.\d{4,9}/[^\s\"'<>]+", text):
        tag = doi.rstrip(".,;)")
        if tag not in seen:
            seen.add(tag)
            out.append({"doi": tag, "kind": "doi"})
    return out


def uncertainty_of(model, engine):
    out = []
    for issue in model["issues"]:
        out.append({"id": issue["id"], "topic": issue["where"],
                    "detail": issue["detail"], "impact": issue["kind"]})
    for finding in engine.findings:
        if finding["verdict"] in ("unconfirmed", "conflict", "missing"):
            out.append({"id": finding["id"], "topic": finding["subject"],
                        "detail": finding["claim"], "impact": finding["verdict"]})
    return out


def pending_human_of(engine):
    out = []
    for finding in engine.findings:
        if finding["needs_expert"] or finding["verdict"] == "unconfirmed":
            out.append({"item": finding["subject"], "why": finding["claim"],
                        "who": "伦理委员会" if finding["dimension"] == "ethics_safety" else "设备管理员/供应商/专家",
                        "estimate": None})
    return out


def confirmation_of(engine):
    out = {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []}
    for finding in engine.findings:
        bucket = "expert_confirmation"
        if finding["dimension"] == "ethics_safety":
            bucket = "ethics" if "审批" in finding["claim"] or "伦理" in finding["subject"] else "safety"
        elif finding["dimension"] == "equipment_materials" and (
                "设备" in finding["subject"] or "material" in finding["subject"]):
            bucket = "equipment_availability"
        out[bucket].append({"item": finding["subject"], "claim": finding["claim"],
                            "status": finding["verdict"], "id": finding["id"]})
    return out


def assemble_result(model, engine, comparison, contract, adjudication=None, work_dir=None):
    best = _best_plan(model, comparison)
    result = {
        "case_id": model["case_id"] or "unknown",
        "challenge_id": model.get("challenge_id"),
        "skill": "experiment-feasibility-review",
        "status": None,
        "schema_version": SCHEMA_VERSION,
        "overall_conclusion": overall_conclusion(model, engine, comparison),
        "experiment_plan": experiment_plan_of(best, model),
        "review": review_of(engine),
        "plan_comparison": comparison,
        "evidence_and_risks": {
            "evidence": [{"locator": f["evidence"][0]["locator"],
                          "quote": f["evidence"][0]["quote"]}
                         for f in engine.findings if f.get("evidence")][:60],
            "risks": [f for f in engine.findings if f["dimension"] == "risks_exceptions"],
            "exception_handling": [r for p in model["plans"] for r in p["risks"] if r.get("mitigation")],
        },
        "confirmation": confirmation_of(engine),
        "sources": sources_of(model),
        "uncertainty": uncertainty_of(model, engine),
        "pending_human": pending_human_of(engine),
        "errors": [i for i in model["issues"] if i["kind"] in ("fatal", "json_error")],
        "degradations": [i for i in model["issues"] if i["kind"] not in ("fatal", "json_error")],
        "change_log": [{"step": "plan_model", "action": "规范化中间表示已构建",
                        "reason": "保证全部数值与证据可从 plan_model.json 复算"}],
        "runtime": {"budget_seconds": 900, "budget_requests": 64,
                    "stages": ["skeleton", "plan_model", "checks", "adjudication", "report"],
                    "deterministic": True},
        "artifacts": [],
        "output_contract": contract,
        "contract_fill_report": None,
    }
    # 中间表示与算术追踪的引用（评审可直接打开复核）
    if work_dir:
        result["intermediate_representation"] = {
            "plan_model": "plan_model.json", "calc_trace": "calc_trace.json",
            "note": "plan_model.json 是全部审查判断的单一事实来源；calc_trace.json 记录每个派生数值的公式与输入"}

    def route(top, path, spec):
        canon = nk(path)
        if any(t in canon for t in ("caseid", "case", "challenge", "armi")):
            return result["case_id"], "case_id"
        if canon in ("status", "state"):
            return result["status"], "status"
        if any(t in canon for t in ("planmodel", "intermediate", "model")):
            return result.get("intermediate_representation", {}), "intermediate_representation"
        if any(t in canon for t in ("plan", "experiment", "design", "protocol")):
            return result["experiment_plan"], "experiment_plan"
        if any(t in canon for t in ("review", "check", "assessment", "audit")):
            return result["review"], "review"
        if any(t in canon for t in ("compar", "rank", "version", "diff", "select")):
            return result["plan_comparison"], "plan_comparison"
        if any(t in canon for t in ("risk", "exception", "mitig", "hazard")):
            return result["evidence_and_risks"], "evidence_and_risks"
        if any(t in canon for t in ("confirm", "approval", "pending", "human")):
            return result["confirmation"], "confirmation"
        if any(t in canon for t in ("source", "evidence", "citation", "reference")):
            return result["sources"], "sources"
        if any(t in canon for t in ("uncertain", "limitation", "gap")):
            return result["uncertainty"], "uncertainty"
        if any(t in canon for t in ("conclusion", "summary", "verdict", "recommend", "overall")):
            return result["overall_conclusion"], "overall_conclusion"
        if any(t in canon for t in ("changelog", "change", "log")):
            return result["change_log"], "change_log"
        if any(t in canon for t in ("cost", "budget", "price")):
            return result["plan_comparison"].get("summary", {}), "plan_comparison.summary"
        if any(t in canon for t in ("time", "schedule", "timeline")):
            return result["plan_comparison"], "plan_comparison"
        if any(t in canon for t in ("artifact", "output")):
            return result["artifacts"], "artifacts"
        return None, None

    result["contract_fill_report"] = fill_contract(contract, result, route)
    # 状态：无方案/不可解析 → 相应状态；否则七类齐全即 ok
    if model.get("notes") and any("不可解析" in n for n in model["notes"]):
        result["status"] = "input_error"
    elif not model["plans"]:
        result["status"] = "insufficient_evidence"
    else:
        dims = {f["dimension"] for f in engine.findings}
        result["status"] = "ok" if set(DIMENSIONS) - dims == set() else "partial"
    if adjudication:
        result["change_log"].append({"step": "adjudication",
                                     "action": "合并 Agent 复核：覆盖 %d 条，新增 %d 条" % (
                                         len(adjudication.get("overrides", [])),
                                         len(adjudication.get("additions", []))),
                                     "reason": "把脚本机械判断与 Agent 判断合并，保留可复核证据"})
    result["change_log"].append({"step": "result", "action": "result.json 装配完成",
                                 "reason": "输出契约字段已回填，证据可溯源"})
    return result
