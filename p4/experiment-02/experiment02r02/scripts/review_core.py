#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_core.py — 版本化实验方案可行性审查的确定性引擎

只做"能从输入原文取到数"的工作：结构识别、方案×合同交叉匹配、算术台账、
参数域校验、版本排序、逐字证据片段生成。每一条数值结论都带 expr + terms，
可用 safe_eval 复算；每一条引文都按去空白包含关系回溯原文。

仅依赖 Python 标准库；Python >= 3.8。
"""

from __future__ import annotations

import ast
import json
import math
import re
import statistics
from collections import Counter, OrderedDict

SCHEMA_NAME = "versioned_feasibility_review"
SCHEMA_VERSION = "1.2.0"

DIMENSIONS = (
    "assumptions_variables", "controls", "equipment_materials", "time_cost",
    "parameter_ranges", "risks_exceptions", "ethics_safety",
)
VERDICTS = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
RESOURCE_STATES = ("已具备", "可外协", "需采购", "不具备", "未知")

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def norm_ws(text):
    """去掉全部空白，用于逐字片段校验。"""
    return re.sub(r"\s+", "", text or "")


def json_dumps_compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def safe_eval(expr, env):
    """只允许算术表达式与白名单函数。"""
    allowed = {"min": min, "max": max, "ceil": math.ceil, "floor": math.floor,
               "round": round, "abs": abs, "sqrt": math.sqrt, "log": math.log,
               "log10": math.log10, "exp": math.exp}
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in allowed):
                raise ValueError("disallowed call")
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                                   ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div,
                                   ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.FloorDiv, ast.Call)):
            raise ValueError("disallowed node")
    ns = {"__builtins__": {}}
    ns.update(allowed)
    ns.update(env)
    return eval(compile(tree, "<ledger>", "eval"), ns, {})


def round_number(x, ndigits=4):
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, ndigits)
    return x


def z_value(p):
    if not isinstance(p, (int, float)) or not (0 < p < 1):
        return None
    try:
        return round(statistics.NormalDist().inv_cdf(p), 6)
    except statistics.StatisticsError:
        return None


# ---------------------------------------------------------------------------
# 别名表
# ---------------------------------------------------------------------------

ID_KEYS = ("case_id", "id", "challenge_id", "caseId", "caseID", "armiId")
TITLE_KEYS = ("title", "task_title", "name")
GOAL_KEYS = ("research_goal", "goal", "objective", "aim", "research_question", "question", "purpose")
PLAN_KEYS = ("plans", "candidate_plans", "plan_versions", "versions", "variants", "proposals", "candidates")
STEP_KEYS = ("steps", "procedure", "protocol_steps", "workflow", "operations", "tasks", "experimental_steps")
PARAM_KEYS = ("parameters", "params", "settings", "operating_parameters", "conditions", "parameter_set")
DOMAIN_KEYS = ("parameter_domains", "domains", "parameter_space", "allowed_ranges", "finite_domains",
               "parameter_domain", "domains_allowed")
RES_KEYS = ("resources", "equipment", "instruments", "devices", "resource_inventory", "inventory",
            "facilities", "capabilities")
CONTRACT_KEYS = ("contracts", "quotes", "procurement", "booking", "bookings", "prices", "price_list",
                 "supply", "resource_contracts")
SAMPLE_KEYS = ("samples", "sample", "cohort", "specimens", "biological_samples", "sample_plan", "sample_size")
RISK_KEYS = ("risks", "risk_register", "hazards", "failure_modes", "exceptions", "risk_assessment")
BUDGET_KEYS = ("budget", "budget_cap", "cost_cap", "total_budget", "funding", "max_cost")
DEADLINE_KEYS = ("deadline", "due_date", "target_date", "end_date", "timeline_days", "duration_days",
                 "window_days", "available_days")

PLAN_ID_KEYS = ("id", "plan_id", "version", "name", "label", "variant")
STEP_NAME_KEYS = ("name", "title", "step", "action", "description", "content", "operation")
STEP_HOURS_KEYS = ("duration_h", "hours", "duration_hours", "time_h", "man_hours")
STEP_WAIT_KEYS = ("wait_h", "waiting_h", "wait_days", "waiting_days", "turnaround_h", "turnaround_days",
                  "incubation_h", "lead_h", "overnight")
STEP_MIN_KEYS = ("duration_min", "minutes", "time_min")
STEP_DAY_KEYS = ("duration_days", "days", "elapsed_days")
USE_KEYS = ("resources", "equipment", "instruments", "materials", "consumables", "reagents", "kits", "uses")

RES_ID_KEYS = ("id", "resource_id", "equipment_id", "item_id", "code")
RES_NAME_KEYS = ("name", "resource", "item", "equipment", "material", "model")
RES_STATUS_KEYS = ("status", "availability", "state", "mode")
PRICE_KEYS = ("unit_price", "price", "unit_cost", "cost", "price_cny", "amount")
LEAD_KEYS = ("lead_time_days", "lead_days", "delivery_days", "procurement_days", "lead_time")
RATE_KEYS = ("rate_per_hour", "hourly_rate", "rate", "booking_rate", "usage_rate")
STOCK_KEYS = ("stock", "on_hand", "inventory_qty", "available_quantity", "qty_on_hand")
FEE_KEYS = ("booking_fee", "fixed_fee", "setup_fee", "service_fee")

SAMPLE_FIELDS = (
    ("n", ("n", "n_total", "total_n", "sample_size", "n_samples")),
    ("n_per_group", ("n_per_group", "n_group", "group_n")),
    ("groups", ("groups", "n_groups", "arms", "group_count")),
    ("effect", ("effect_size", "effect", "delta", "minimal_difference", "mcid")),
    ("sd", ("sd", "std", "standard_deviation", "sigma")),
    ("cv", ("cv", "coefficient_of_variation", "rsd")),
    ("alpha", ("alpha", "significance_level", "type_i_error")),
    ("power", ("power", "statistical_power")),
    ("attrition", ("attrition", "dropout_rate", "loss_rate")),
    ("p1", ("p1", "baseline_rate")),
    ("p2", ("p2", "expected_rate")),
)

DOMAIN_MIN_KEYS = ("min", "minimum", "lower", "lower_bound")
DOMAIN_MAX_KEYS = ("max", "maximum", "upper", "upper_bound")
DOMAIN_STEP_KEYS = ("step", "increment", "resolution", "grid_step")
DOMAIN_ALLOWED_KEYS = ("allowed", "values", "enumerated", "set", "categorical", "options")

CONTROL_KEYS = ("control", "controls", "control_group", "blank", "baseline", "placebo",
                "replicates", "replicate", "randomization", "randomisation", "blinding", "masking")

KEYWORD_FLAGS = {
    "animal": ("animal", "mice", "mouse", "rat", "rats", "小鼠", "大鼠", "动物", "primate", "rabbit"),
    "human_subject": ("human", "patient", "patients", "volunteer", "志愿者", "人体", "受试者",
                      "blood sample", "血清", "plasma", "知情同意", "informed consent"),
    "hazardous": ("hazardous", "危险", "有毒", "toxic", "flammable", "易燃", "corrosive", "放射性",
                  "radioactive", "生物安全", "biosafety", "bsl-", "pathogen", "病原", "氰化物", "cyanide"),
    "personal_data": ("personal data", "个人信息", "隐私", "privacy", "gdpr", "可识别",
                      "identifiable", "基因数据"),
}

ETHICS_META = {
    "animal": ("IACUC/动物伦理", ["动物伦理申请表", "实验动物使用计划", "人员资质"], "30–90 天"),
    "human_subject": ("IRB/伦理委员会", ["伦理审查申请", "知情同意书", "数据管理计划"], "30–90 天"),
    "hazardous": ("实验室安全/生物安全委员会", ["风险评估表", "SOP", "危化品台账", "废弃物处置协议"], "14–60 天"),
    "personal_data": ("数据合规/隐私影响评估", ["数据用途说明", "去标识化方案", "授权书"], "14–45 天"),
}

SCORE_WEIGHTS = OrderedDict([
    ("hard_blocks", 0.30), ("parameter_compliance", 0.20), ("resource_readiness", 0.15),
    ("schedule_slack", 0.10), ("cost_headroom", 0.10), ("sample_adequacy", 0.10),
    ("control_strength", 0.05),
])


# ---------------------------------------------------------------------------
# 取值与遍历
# ---------------------------------------------------------------------------

def pick(d, aliases):
    if not isinstance(d, dict):
        return None, None
    lowered = {str(k).lower(): str(k) for k in d.keys()}
    for a in aliases:
        if a.lower() in lowered:
            k = lowered[a.lower()]
            return d[k], k
    return None, None


def deep_find(obj, aliases, path="", depth=0, maxdepth=4, out=None):
    if out is None:
        out = []
    if depth > maxdepth:
        return out
    wanted = {a.lower() for a in aliases}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k).lower() in wanted:
                out.append((p, v))
            deep_find(v, aliases, p, depth + 1, maxdepth, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            deep_find(v, aliases, f"{path}[{i}]", depth + 1, maxdepth, out)
    return out


def to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", v.replace(",", ""))
        if not m:
            return None
        s = m.group()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        return float(s)
    return None


def hours_from(step):
    for k in STEP_HOURS_KEYS:
        n = to_number(step.get(k))
        if n is not None:
            return n, k, step.get(k)
    for k in STEP_DAY_KEYS:
        n = to_number(step.get(k))
        if n is not None:
            return n * 24.0, k, step.get(k)
    for k in STEP_MIN_KEYS:
        n = to_number(step.get(k))
        if n is not None:
            return n / 60.0, k, step.get(k)
    return None, None, None


def wait_days_from(step):
    for k in STEP_WAIT_KEYS:
        n = to_number(step.get(k))
        if n is None:
            continue
        return (n if "day" in k.lower() else n / 24.0), k, step.get(k)
    return None, None, None


def _norm_name(s):
    return "" if s is None else re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(s).lower())


def _tokens(s):
    return set(re.findall(r"[0-9a-z]+", (s or "").lower()))


# ---------------------------------------------------------------------------
# 证据片段
# ---------------------------------------------------------------------------

class Doc:
    def __init__(self, name, text):
        self.name = name
        self.text = text or ""
        self.norm = norm_ws(self.text)


def split_path(path):
    return [t for t in re.split(r"\.|(?=\[)", path) if t]


def path_member(path):
    """返回路径最后一级的成员名（若是数组元素则返回 None）。"""
    for tok in reversed(split_path(path)):
        if tok and not tok.startswith("["):
            return tok
    return None


def make_quote(value, member=None):
    """构造逐字片段：成员型路径给 `"key": value`，数组元素给 value 本身序列化。

    只引 `"key": value`（不带外层的 `{}`），因为对象成员在原文里是逐字段相邻的，
    这样引文才是原文里真实连续的片段。
    """
    if member and not member.startswith("["):
        try:
            return json.dumps(member, ensure_ascii=False) + ":" + json_dumps_compact(value)
        except TypeError:
            pass
    try:
        return json_dumps_compact(value)
    except TypeError:
        return None


def quote_verified(doc, quote):
    """按去空白包含关系校验引文是原文片段。"""
    return bool(doc and quote) and norm_ws(quote) in doc.norm


def find_snippet(doc, needle, width=30):
    if not doc or not needle:
        return None
    i = doc.text.lower().find(needle.lower())
    if i < 0:
        return None
    return doc.text[max(0, i - width): min(len(doc.text), i + len(needle) + width)]


def make_evidence(doc, locator, quote, kind="case"):
    return {"locator": locator, "quote": quote,
            "quote_verified": quote_verified(doc, quote) if quote else False, "kind": kind}


# ---------------------------------------------------------------------------
# 结构识别
# ---------------------------------------------------------------------------

def as_plan_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        for k in ("plans", "versions", "variants", "candidates", "list", "items"):
            if isinstance(value.get(k), list):
                return [v for v in value[k] if isinstance(v, dict)]
        sub = {k: v for k, v in value.items() if isinstance(v, dict)}
        if sub:
            return list(sub.values())
    return []


def iter_named_items(value):
    if isinstance(value, list):
        for it in value:
            yield None, it
    elif isinstance(value, dict):
        if value and all(isinstance(v, dict) for v in value.values()):
            for k, it in value.items():
                yield k, it
        else:
            yield None, value


def flatten_items(v, path):
    """把容器统一拍平成 (item, path) 列表；兼容 list / dict-of-dict / dict-of-list。"""
    out = []
    if isinstance(v, list):
        for i, it in enumerate(v):
            out.append((it, "%s[%d]" % (path, i)))
    elif isinstance(v, dict):
        if v and all(isinstance(x, dict) for x in v.values()):
            for k, it in v.items():
                out.append((it, "%s.%s" % (path, k)))
        elif v and all(isinstance(x, list) for x in v.values()):
            for k, lst in v.items():
                for i, it in enumerate(lst):
                    out.append((it, "%s.%s[%d]" % (path, k, i)))
        else:
            out.append((v, path))
    return out


def _looks_like_resource(d):
    if not isinstance(d, dict):
        return False
    name_or_id = any(k in d for k in RES_NAME_KEYS + RES_ID_KEYS)
    attr = any(k in d for k in PRICE_KEYS + LEAD_KEYS + RATE_KEYS + STOCK_KEYS + FEE_KEYS + RES_STATUS_KEYS)
    return name_or_id and attr


def collect_resources(v, path, out, seen):
    if isinstance(v, list):
        for i, it in enumerate(v):
            collect_resources(it, "%s[%d]" % (path, i), out, seen)
    elif isinstance(v, dict):
        if _looks_like_resource(v):
            if path not in seen:
                seen.add(path)
                out.append(normalize_resource(v, path))
        else:
            for k, sub in v.items():
                collect_resources(sub, "%s.%s" % (path, k), out, seen)


def map_resource_state(v):
    if v is None:
        return "未知"
    s = _norm_name(v)
    if any(x in s for x in ("available", "inhouse", "ready", "已具备", "有货", "onhand")):
        return "已具备"
    if any(x in s for x in ("outsource", "external", "外协", "外送")):
        return "可外协"
    if any(x in s for x in ("purchase", "procure", "buy", "采购", "订购")):
        return "需采购"
    if any(x in s for x in ("absent", "none", "unavailable", "不具备", "缺")):
        return "不具备"
    return "未知"


def normalize_resource(raw, path):
    it = raw if isinstance(raw, dict) else {}
    rid = pick(it, RES_ID_KEYS)[0]
    name = pick(it, RES_NAME_KEYS)[0]
    return {
        "id": rid if isinstance(rid, str) else None,
        "name": name if isinstance(name, str) else str(rid or ""),
        "status_raw": pick(it, RES_STATUS_KEYS)[0],
        "status": map_resource_state(pick(it, RES_STATUS_KEYS)[0]),
        "unit_price": to_number(pick(it, PRICE_KEYS)[0]),
        "currency": pick(it, ("currency", "currency_code"))[0],
        "unit": pick(it, ("unit", "pack", "pack_size"))[0],
        "lead_days": to_number(pick(it, LEAD_KEYS)[0]),
        "rate_per_hour": to_number(pick(it, RATE_KEYS)[0]),
        "booking_fee": to_number(pick(it, FEE_KEYS)[0]),
        "stock": to_number(pick(it, STOCK_KEYS)[0]),
        "locator": path,
    }


def get_steps(plan):
    steps = []
    for path, v in deep_find(plan, STEP_KEYS, maxdepth=2):
        if isinstance(v, list):
            for i, s in enumerate(v):
                d = dict(s) if isinstance(s, dict) else {"name": s}
                d["__locator__"] = f"{path}[{i}]"
                steps.append(d)
    return steps


def get_params(plan):
    params = []
    for path, v in deep_find(plan, PARAM_KEYS, maxdepth=2):
        if isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    name, _ = pick(item, ("name", "parameter", "key"))
                    value_key = "value" if "value" in item else pick(item, ("value", "target", "setpoint"))[1]
                    val = item["value"] if "value" in item else pick(item, ("value", "target", "setpoint"))[0]
                    if name is None and value_key is None:
                        name, val = next(iter(item.items()), (None, None))
                        value_key = name
                    params.append({"name": name, "value": val, "value_key": value_key,
                                   "unit": item.get("unit"), "locator": f"{path}[{i}]"})
                else:
                    params.append({"name": None, "value": item, "value_key": None, "unit": None,
                                   "locator": f"{path}[{i}]"})
        elif isinstance(v, dict):
            for k, val in v.items():
                if isinstance(val, dict) and any(x in val for x in ("value", "min", "max", "unit")):
                    params.append({"name": k, "value": val.get("value"), "value_key": "value",
                                   "unit": val.get("unit"), "locator": f"{path}.{k}"})
                else:
                    params.append({"name": k, "value": val, "value_key": k, "unit": None,
                                   "locator": f"{path}.{k}"})
    return params


def normalize_use(item, path):
    if isinstance(item, str):
        return {"name": item, "qty": None, "hours": None, "unit": None, "locator": path}
    d = item if isinstance(item, dict) else {}
    return {
        "name": pick(d, RES_NAME_KEYS)[0],
        "qty": to_number(pick(d, ("qty", "quantity", "amount", "units", "packs"))[0]),
        "hours": to_number(pick(d, ("hours", "usage_hours", "machine_hours", "duration_h"))[0]),
        "unit": pick(d, ("unit", "pack_size"))[0],
        "locator": path,
    }


def get_resource_uses(plan, steps):
    uses = []
    for path, v in deep_find(plan, USE_KEYS, maxdepth=1):
        for item, loc in flatten_items(v, path):
            uses.append(normalize_use(item, loc))
    for s in steps:
        rel = s.get("__locator__", "steps")
        for path, v in deep_find(s, USE_KEYS, maxdepth=1):
            for item, loc in flatten_items(v, path):
                full = loc if loc.startswith(rel) else "%s.%s" % (rel, loc)
                uses.append(normalize_use(item, full))
    return uses


def normalize_domain(item, key=None):
    if isinstance(item, (list, tuple, set)):
        return {"name": key, "allowed": list(item), "locator": None, "raw": item}
    if not isinstance(item, dict):
        return {"name": key, "value": item, "locator": None, "raw": item}
    allowed = pick(item, DOMAIN_ALLOWED_KEYS)[0]
    if allowed is not None and not isinstance(allowed, list):
        allowed = [allowed]
    return {"name": pick(item, ("name", "parameter", "key"))[0] or key,
            "min": to_number(pick(item, DOMAIN_MIN_KEYS)[0]),
            "max": to_number(pick(item, DOMAIN_MAX_KEYS)[0]),
            "step": to_number(pick(item, DOMAIN_STEP_KEYS)[0]),
            "allowed": allowed,
            "unit": pick(item, ("unit", "units"))[0],
            "locator": None,
            "raw": item}


def get_domains(case):
    domains = []
    for path, v in deep_find(case, DOMAIN_KEYS, maxdepth=3):
        if isinstance(v, list):
            for i, item in enumerate(v):
                d = normalize_domain(item)
                d["locator"] = f"{path}[{i}]"
                domains.append(d)
        elif isinstance(v, dict):
            for k, val in v.items():
                d = normalize_domain(val, key=k)
                d["locator"] = f"{path}.{k}"
                domains.append(d)
    return domains


def get_contract_items(case):
    items, seen = [], set()
    for path, v in deep_find(case, CONTRACT_KEYS, maxdepth=2):
        collect_resources(v, path, items, seen)
    if isinstance(case, dict):
        for k in RES_KEYS:
            if k in case:
                collect_resources(case[k], k, items, seen)
    if not items:
        for path, v in deep_find(case, RES_KEYS, maxdepth=3):
            if any(seg in ("plans", "steps") for seg in split_path(path)):
                continue
            collect_resources(v, path, items, seen)
    return items


def get_sample_spec(plan):
    spec = {}
    for path, v in deep_find(plan, SAMPLE_KEYS, maxdepth=2):
        for cand in (v, *([v] if not isinstance(v, (list, dict)) else [])):
            if not isinstance(cand, dict):
                continue
            for field, aliases in SAMPLE_FIELDS:
                val = pick(cand, aliases)[0]
                if val is not None and field not in spec:
                    spec[field] = to_number(val)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    for field, aliases in SAMPLE_FIELDS:
                        val = pick(item, aliases)[0]
                        if val is not None and field not in spec:
                            spec[field] = to_number(val)
    return spec


# ---------------------------------------------------------------------------
# 匹配与台账
# ---------------------------------------------------------------------------

def match_use_to_resource(use, items):
    if not items:
        return None
    uname = _norm_name(use.get("name"))
    uid = use.get("id") if isinstance(use, dict) else None
    if uid:
        for it in items:
            if it["id"] and _norm_name(it["id"]) == _norm_name(uid):
                return it
    if not uname:
        return None
    for it in items:
        if _norm_name(it["id"]) == uname or _norm_name(it["name"]) == uname:
            return it
    for it in items:
        n2 = _norm_name(it["name"]) or _norm_name(it["id"])
        if n2 and (uname in n2 or n2 in uname):
            return it
    t1 = _tokens(uname)
    best, bestscore = None, 0.0
    for it in items:
        t2 = _tokens(_norm_name(it["name"]) or _norm_name(it["id"]))
        if not t1 or not t2:
            continue
        inter = t1 & t2
        score = len(inter) / max(1, min(len(t1), len(t2)))
        if score >= 0.5 and len(inter) >= 2 and score > bestscore:
            best, bestscore = it, score
    return best


def term(symbol, value, locator, quote, unit=None, derived=False, note=None):
    t = {"symbol": symbol, "value": round_number(value), "locator": locator, "quote": quote,
         "unit": unit, "derived": bool(derived)}
    if note:
        t["note"] = note
    return t


def ledger_entry(lid, kind, title, expr, terms, result, unit, status="computed",
                 assumptions=None, notes=None):
    entry = {"id": lid, "kind": kind, "title": title, "expr": expr, "terms": terms,
             "result": round_number(result), "unit": unit, "status": status,
             "assumptions": assumptions or [], "notes": notes or [], "checks": []}
    if expr and status == "computed" and result is not None:
        env = {t["symbol"]: t["value"] for t in terms if isinstance(t.get("value"), (int, float))}
        try:
            recomputed = safe_eval(expr, env)
            entry["recomputed"] = round_number(recomputed)
            entry["recompute_ok"] = abs(recomputed - result) <= 1e-6 * max(1.0, abs(result))
        except Exception as exc:  # noqa: BLE001
            entry["recomputed"] = None
            entry["recompute_ok"] = False
            entry["notes"].append({"kind": "recompute_error", "detail": str(exc)})
    elif status == "computed" and result is not None:
        # 声明式台账：数值直接取自原文，无算术表达式需要复算
        entry["recomputed"] = round_number(result)
        entry["recompute_ok"] = True
        entry["notes"].append({"kind": "declarative_value"})
    return entry


def build_plan_profile(plan, idx, case_doc, case_data):
    base = "plans[%d]" % idx
    pid = None
    for k in PLAN_ID_KEYS:
        if plan.get(k) is not None:
            pid = str(plan[k])
            break
    pid = pid or "v%d" % (idx + 1)
    steps = get_steps(plan)
    params = get_params(plan)
    uses = get_resource_uses(plan, steps)
    goal = pick(plan, GOAL_KEYS)[0]
    if goal is None and isinstance(case_data, dict):
        goal = pick(case_data, GOAL_KEYS)[0]
    budget = to_number(pick(plan, BUDGET_KEYS)[0])
    if budget is None and isinstance(case_data, dict):
        budget = to_number(pick(case_data, BUDGET_KEYS)[0])
    deadline_days = to_number(pick(plan, DEADLINE_KEYS)[0])

    for p in params:
        p["locator"] = f"{base}.{p['locator']}"
    for u in uses:
        if not str(u["locator"]).startswith(base):
            u["locator"] = f"{base}.{u['locator']}"

    rows = []
    for i, s in enumerate(steps):
        hours, hk, hraw = hours_from(s)
        wdays, wk, wraw = wait_days_from(s)
        rows.append({"index": i, "name": pick(s, STEP_NAME_KEYS)[0], "hours": hours,
                     "hours_key": hk, "hours_raw": hraw, "wait_days": wdays, "wait_key": wk,
                     "wait_raw": wraw, "locator": f"{base}.{s.get('__locator__', f'steps[{i}]')}"})
    return {"plan_id": pid, "locator": base, "goal": goal, "budget": budget,
            "deadline_days": deadline_days, "steps": rows, "parameters": params,
            "resource_uses": uses, "sample": get_sample_spec(plan), "raw_plan": plan}


def compute_cost(profile, items, case_doc):
    pid = profile["plan_id"]
    terms, parts, unmatched, missing_qty = [], [], [], []
    seen_fees, currency, n = set(), None, 0
    for u in profile["resource_uses"]:
        m = match_use_to_resource(u, items)
        if m is None:
            unmatched.append(u)
            continue
        currency = currency or m["currency"]
        if u.get("hours") and m["rate_per_hour"] is not None:
            n += 1
            terms += [term("r%d" % n, m["rate_per_hour"], m["locator"],
                           make_quote(m["rate_per_hour"], "rate_per_hour"), m["currency"] or "CNY/h"),
                      term("h%d" % n, u["hours"], u["locator"], make_quote(u["hours"], "hours"), "h")]
            parts.append("r%d*h%d" % (n, n))
        elif m["unit_price"] is not None and u.get("qty") is not None:
            n += 1
            terms += [term("p%d" % n, m["unit_price"], m["locator"],
                           make_quote(m["unit_price"], "unit_price"), m["currency"] or "CNY"),
                      term("q%d" % n, u["qty"], u["locator"], make_quote(u["qty"], "qty"), u.get("unit"))]
            parts.append("p%d*q%d" % (n, n))
        elif m["unit_price"] is not None:
            missing_qty.append(u)
        if m["booking_fee"] is not None and m["locator"] not in seen_fees:
            seen_fees.add(m["locator"])
            n += 1
            terms.append(term("f%d" % n, m["booking_fee"], m["locator"],
                              make_quote(m["booking_fee"], "booking_fee"), m["currency"] or "CNY"))
            parts.append("f%d" % n)

    notes = [{"kind": "unmatched_use", "name": x.get("name"), "locator": x.get("locator")} for x in unmatched]
    notes += [{"kind": "missing_qty", "name": x.get("name"), "locator": x.get("locator")} for x in missing_qty]
    if parts:
        expr = " + ".join(parts)
        result = safe_eval(expr, {t["symbol"]: t["value"] for t in terms})
        entries = [ledger_entry("L-cost-%s" % pid, "cost", "%s 采购/使用成本" % pid, expr, terms,
                                result, currency or "CNY",
                                assumptions=["只累加能匹配到合同/清单的资源项；未匹配项见 notes"], notes=notes)]
    else:
        entries = [ledger_entry("L-cost-%s" % pid, "cost", "%s 采购/使用成本" % pid, None, [], None, None,
                                status="not_computable", notes=notes or [{"kind": "no_matched_resources"}])]
    return entries, {"unmatched": unmatched, "missing_qty": missing_qty, "currency": currency}


def compute_time(profile):
    pid = profile["plan_id"]
    terms, parts, wterms, wparts, n = [], [], [], [], 0
    for r in profile["steps"]:
        if r["hours"] is not None:
            n += 1
            terms.append(term("h%d" % n, r["hours"], r["locator"],
                              make_quote(r["hours_raw"], r["hours_key"]), "h"))
            parts.append("h%d" % n)
        if r["wait_days"] is not None:
            n += 1
            wterms.append(term("w%d" % n, r["wait_days"], r["locator"],
                               make_quote(r["wait_raw"], r["wait_key"]), "day"))
            wparts.append("w%d" % n)
    entries = []
    if parts:
        expr = " + ".join(parts)
        total = safe_eval(expr, {t["symbol"]: t["value"] for t in terms})
        entries.append(ledger_entry("L-time-%s" % pid, "time", "%s 设备/人工时长合计" % pid,
                                    expr, terms, total, "h",
                                    assumptions=["步骤按串行累加；未声明并行分组时取保守串行估算"]))
        entries.append(ledger_entry("L-days-%s" % pid, "time", "%s 折算工作日（8h/日）" % pid,
                                    "(%s)/8" % expr, terms, total / 8.0, "workday",
                                    assumptions=["按 8 小时/工作日折算，用于与方案排期比较"]))
    else:
        entries.append(ledger_entry("L-time-%s" % pid, "time", "%s 设备/人工时长合计" % pid,
                                    None, [], None, "h", status="not_computable",
                                    notes=[{"kind": "no_step_durations"}]))
    if wparts:
        wexpr = " + ".join(wparts)
        entries.append(ledger_entry("L-wait-%s" % pid, "time", "%s 等待/周转合计" % pid,
                                    wexpr, wterms,
                                    safe_eval(wexpr, {t["symbol"]: t["value"] for t in wterms}), "day"))
    return entries


def compute_sample(profile):
    pid = profile["plan_id"]
    s = profile["sample"]
    alpha = s.get("alpha") if s.get("alpha") is not None else 0.05
    power = s.get("power") if s.get("power") is not None else 0.80
    attrition = s.get("attrition") if s.get("attrition") is not None else 0.0
    entries = []

    if s.get("n") is not None:
        entries.append(ledger_entry("L-sample-n-%s" % pid, "sample", "%s 声明样本总量" % pid, None,
                                    [term("n", s["n"], profile["locator"], make_quote(s["n"], "n"))],
                                    s["n"], "sample"))
    if s.get("groups") and s.get("n_per_group"):
        entries.append(ledger_entry("L-sample-declared-%s" % pid, "sample", "%s 按组折算总量" % pid,
                                    "groups*n_group",
                                    [term("groups", s["groups"], profile["locator"], make_quote(s["groups"], "groups")),
                                     term("n_group", s["n_per_group"], profile["locator"],
                                          make_quote(s["n_per_group"], "n_per_group"))],
                                    s["groups"] * s["n_per_group"], "sample"))

    ratio = s.get("cv") if s.get("cv") is not None else s.get("sd")
    if s.get("effect") and ratio:
        za, zb = z_value(1 - alpha / 2), z_value(power)
        if za is not None and zb is not None:
            expr = "ceil(2*((za+zb)**2)*((ratio/effect)**2)/(1-attrition))"
            terms = [term("za", za, None, None, derived=True, note="NormalDist().inv_cdf(1-alpha/2)"),
                     term("zb", zb, None, None, derived=True, note="NormalDist().inv_cdf(power)"),
                     term("ratio", ratio, profile["locator"],
                          make_quote(ratio, "cv" if s.get("cv") is not None else "sd"), "CV"),
                     term("effect", s["effect"], profile["locator"], make_quote(s["effect"], "effect_size"), "rel"),
                     term("attrition", attrition, profile["locator"],
                          make_quote(attrition, "attrition") if s.get("attrition") is not None else None, "frac")]
            entries.append(ledger_entry("L-sample-required-%s" % pid, "sample",
                                        "%s 每组最小样本量（连续结局）" % pid, expr, terms,
                                        safe_eval(expr, {t["symbol"]: t["value"] for t in terms}),
                                        "sample/group",
                                        assumptions=["双侧、两独立组；ratio/effect 为相对量；attrition 为脱落率"]))
    if s.get("p1") is not None and s.get("p2") is not None and s["p1"] != s["p2"]:
        za, zb = z_value(1 - alpha / 2), z_value(power)
        if za is not None and zb is not None:
            expr = "ceil(((za+zb)**2)*(p1*(1-p1)+p2*(1-p2))/((p1-p2)**2)/(1-attrition))"
            terms = [term("za", za, None, None, derived=True, note="inv_cdf"),
                     term("zb", zb, None, None, derived=True, note="inv_cdf"),
                     term("p1", s["p1"], profile["locator"], make_quote(s["p1"], "p1")),
                     term("p2", s["p2"], profile["locator"], make_quote(s["p2"], "p2")),
                     term("attrition", attrition, profile["locator"],
                          make_quote(attrition, "attrition") if s.get("attrition") is not None else None)]
            entries.append(ledger_entry("L-sample-prop-%s" % pid, "sample",
                                        "%s 每组最小样本量（比例结局）" % pid, expr, terms,
                                        safe_eval(expr, {t["symbol"]: t["value"] for t in terms}),
                                        "sample/group", assumptions=["两独立比例；正态近似"]))
    if not entries:
        entries.append(ledger_entry("L-sample-%s" % pid, "sample", "%s 样本量核算" % pid,
                                    None, [], None, None, status="not_computable",
                                    notes=[{"kind": "no_sample_numbers"}]))
    return entries


def domain_check(profile, domains, case_doc):
    pid = profile["plan_id"]
    out = []
    for p in profile["parameters"]:
        name = p.get("name")
        if not name:
            continue
        d = next((dd for dd in domains if _norm_name(dd.get("name")) == _norm_name(name)), None)
        value_key = p.get("value_key") or name
        value_ev = make_evidence(case_doc, "case.json#%s" % p["locator"],
                                 make_quote(p.get("value"), value_key))
        if d is None:
            out.append({"id": "P-%s-missing-domain-%s" % (pid, _norm_name(name)), "dimension": "parameter_ranges",
                        "check": '%s 参数 "%s" 未在有限参数域中声明可核验范围' % (pid, name),
                        "target": p["locator"], "verdict": "unconfirmed",
                        "conditions": ["补录 %s 的允许域（min/max/step 或枚举值）并说明依据" % name],
                        "evidence": [value_ev], "confidence": "medium", "needs_expert": True, "notes": []})
            continue
        val = to_number(p.get("value"))
        if val is None and d.get("allowed"):
            ok = p.get("value") in d["allowed"]
            out.append({"id": "P-%s-enum-%s" % (pid, _norm_name(name)), "dimension": "parameter_ranges",
                        "check": '%s 参数 "%s" 为枚举值 %s' % (pid, name, p.get("value")),
                        "target": p["locator"], "verdict": "ok" if ok else "blocked",
                        "conditions": [] if ok else ["改用允许值之一：%s" % d["allowed"]],
                        "evidence": [make_evidence(case_doc, "case.json#%s" % d["locator"],
                                                   make_quote(d.get("allowed"), "allowed"))],
                        "confidence": "high", "needs_expert": False, "notes": []})
            continue
        if val is None:
            out.append({"id": "P-%s-noval-%s" % (pid, _norm_name(name)), "dimension": "parameter_ranges",
                        "check": '%s 参数 "%s" 无可数值化取值，无法与参数域比对' % (pid, name),
                        "target": p["locator"], "verdict": "missing",
                        "conditions": ["为 %s 写明数值取值" % name],
                        "evidence": [value_ev], "confidence": "high", "needs_expert": False, "notes": []})
            continue
        problems = []
        if d.get("min") is not None and val < d["min"]:
            problems.append("below_min")
        if d.get("max") is not None and val > d["max"]:
            problems.append("above_max")
        if d.get("allowed") and val not in d["allowed"]:
            problems.append("not_in_allowed")
        step_mis = False
        if d.get("step") and d.get("min") is not None:
            rem = (val - d["min"]) % d["step"]
            step_mis = min(rem, d["step"] - rem) > 1e-6
        conds = []
        verdict = "ok"
        if problems:
            verdict = "blocked"
            conds.append("将 %s 调整到允许域 [min=%s, max=%s] 内后复核该步" % (name, d.get("min"), d.get("max")))
        elif step_mis:
            verdict = "conflict"
            conds.append("%s=%s 不在 min=%s、step=%s 的网格上，需解释取值来源或改对齐网格"
                         % (name, val, d.get("min"), d.get("step")))
        out.append({"id": "P-%s-%s" % (pid, _norm_name(name)), "dimension": "parameter_ranges",
                    "check": '%s 参数 "%s"=%s 与允许域比对（min=%s, max=%s, step=%s）'
                             % (pid, name, val, d.get("min"), d.get("max"), d.get("step")),
                    "target": p["locator"], "verdict": verdict, "conditions": conds,
                    "evidence": [value_ev,
                                 make_evidence(case_doc, "case.json#%s" % d["locator"],
                                               json_dumps_compact(d.get("raw")) if d.get("raw") is not None
                                               else make_quote(d.get("allowed"), "allowed"))],
                    "confidence": "high", "needs_expert": False,
                    "notes": [{"domain_flags": problems}] if problems else []})
    return out


# ---------------------------------------------------------------------------
# 七类审查项
# ---------------------------------------------------------------------------

def gen_assumptions(profile, case_doc):
    pid, loc, plan = profile["plan_id"], profile["locator"], profile["raw_plan"]
    out = []
    hypo, hypo_key = pick(plan, ("hypothesis", "hypotheses", "assumptions", "assumption"))
    iv, iv_key = pick(plan, ("independent_variables", "independent_variable", "factors", "intervention"))
    dv, dv_key = pick(plan, ("dependent_variables", "outcomes", "endpoints", "response_variables", "readout"))
    crit, crit_key = pick(plan, ("success_criteria", "criterion", "interpretation_criteria"))

    out.append({"id": "AV-%s-hyp" % pid, "dimension": "assumptions_variables",
                "check": "%s 假设可检验性" % pid, "target": loc,
                "verdict": "ok" if hypo is not None else "missing",
                "conditions": [] if hypo is not None else ["写明可证伪假设及其与检验指标的对应关系"],
                "evidence": [make_evidence(case_doc, "case.json#%s" % loc,
                                           make_quote(hypo, hypo_key))] if hypo is not None else [],
                "confidence": "medium" if hypo is not None else "high",
                "needs_expert": False, "notes": []})
    both = iv is not None and dv is not None
    ev = []
    if iv is not None:
        ev.append(make_evidence(case_doc, "case.json#%s" % loc, make_quote(iv, iv_key)))
    if dv is not None:
        ev.append(make_evidence(case_doc, "case.json#%s" % loc, make_quote(dv, dv_key)))
    out.append({"id": "AV-%s-vars" % pid, "dimension": "assumptions_variables",
                "check": "%s 自变量与因变量声明" % pid, "target": loc,
                "verdict": "ok" if both else "missing",
                "conditions": [] if both else ["分别列出自变量与因变量，并给出可操作定义"],
                "evidence": ev,
                "confidence": "high", "needs_expert": False, "notes": []})
    out.append({"id": "AV-%s-crit" % pid, "dimension": "assumptions_variables",
                "check": "%s 判读标准是否可判定" % pid, "target": loc,
                "verdict": "ok" if crit is not None else "missing",
                "conditions": [] if crit is not None else ["补一条可判定的主判读标准（阈值/方向/统计口径）"],
                "evidence": [make_evidence(case_doc, "case.json#%s" % loc,
                                           make_quote(crit, crit_key))] if crit is not None else [],
                "confidence": "medium" if crit is not None else "high",
                "needs_expert": False, "notes": []})
    return out


def gen_controls(profiles, case_doc):
    out = []
    for p in profiles:
        found = [k for k in CONTROL_KEYS if k in p["raw_plan"]]
        if found:
            out.append({"id": "CTRL-%s" % p["plan_id"], "dimension": "controls",
                        "check": "%s 对照/重复/随机化要素：%s" % (p["plan_id"], ", ".join(found)),
                        "target": p["locator"], "verdict": "ok", "conditions": [],
                        "evidence": [make_evidence(case_doc, "case.json#%s" % p["locator"],
                                                   make_quote(p["raw_plan"].get(found[0]), found[0]))],
                        "confidence": "medium", "needs_expert": False, "notes": [{"declared": found}]})
        else:
            out.append({"id": "CTRL-%s" % p["plan_id"], "dimension": "controls",
                        "check": "%s 未声明对照/重复/随机化" % p["plan_id"], "target": p["locator"],
                        "verdict": "missing",
                        "conditions": ["写明对照组、每条件重复数、随机/盲法安排；缺对照不能开工"],
                        "evidence": [], "confidence": "high", "needs_expert": False, "notes": []})
    prev = None
    for p in profiles:
        if prev is not None:
            dropped = [k for k in CONTROL_KEYS if k in prev["raw_plan"] and k not in p["raw_plan"]]
            if dropped:
                k0 = dropped[0]
                out.append({"id": "CTRL-regress-%s" % p["plan_id"], "dimension": "controls",
                            "check": "%s 相对 %s 删除了对照要素 %s（版本回归）"
                                     % (p["plan_id"], prev["plan_id"], ", ".join(dropped)),
                            "target": p["locator"], "verdict": "conflict",
                            "conditions": ["确认删除依据；若无依据应恢复该对照要素"],
                            "evidence": [make_evidence(case_doc, "case.json#%s" % prev["locator"],
                                                       make_quote(prev["raw_plan"].get(k0), k0))],
                            "confidence": "high", "needs_expert": False,
                            "notes": [{"dropped": dropped, "from": prev["plan_id"]}]})
        prev = p
    return out


def gen_equipment(profiles, contract, case_doc, cost_info):
    out = []
    for p, cost in zip(profiles, cost_info):
        pid = p["plan_id"]
        for u in cost["unmatched"]:
            out.append({"id": "EQ-%s-unmatched-%d" % (pid, len(out)), "dimension": "equipment_materials",
                        "check": '%s 使用的资源 "%s" 未在合同/清单中匹配到条目' % (pid, u.get("name")),
                        "target": u.get("locator"), "verdict": "unconfirmed", "resource_state": "未知",
                        "conditions": ['补录 "%s" 的采购/外协合同（价格、货期、状态）' % u.get("name")],
                        "evidence": [make_evidence(case_doc, "case.json#%s" % p["locator"],
                                                   make_quote(u.get("name"), "name"))],
                        "confidence": "medium", "needs_expert": True, "notes": []})
        for u in cost["missing_qty"]:
            out.append({"id": "EQ-%s-noqty-%d" % (pid, len(out)), "dimension": "equipment_materials",
                        "check": '%s 使用 "%s" 但未写用量，无法核算采购量' % (pid, u.get("name")),
                        "target": u.get("locator"), "verdict": "missing", "resource_state": "未知",
                        "conditions": ['为 "%s" 写明板数/管数/次数' % u.get("name")],
                        "evidence": [make_evidence(case_doc, "case.json#%s" % p["locator"],
                                                   make_quote(u.get("name"), "name"))],
                        "confidence": "high", "needs_expert": False, "notes": []})
        for u in p["resource_uses"]:
            m = match_use_to_resource(u, contract)
            if m is None or m["status"] not in ("需采购", "不具备"):
                continue
            lead, dl = m["lead_days"], p["deadline_days"]
            if lead is not None and dl is not None and lead > dl:
                out.append({"id": "EQ-%s-lead-%s" % (pid, _norm_name(m["id"] or m["name"])),
                            "dimension": "equipment_materials",
                            "check": '%s 关键资源 "%s" 货期 %s 天 > 方案窗口 %s 天'
                                     % (pid, m["name"], lead, dl),
                            "target": m["locator"], "verdict": "blocked", "resource_state": m["status"],
                            "conditions": ["提前下单、改外协或顺延开工；当前货期超出窗口 %s 天" % (lead - dl)],
                            "evidence": [make_evidence(case_doc, "case.json#%s" % m["locator"],
                                                       make_quote(m["lead_days"], "lead_time_days"))],
                            "confidence": "high", "needs_expert": False,
                            "notes": [{"lead_days": lead, "window_days": dl}]})
        states = Counter()
        non_ready = []
        for u in p["resource_uses"]:
            m = match_use_to_resource(u, contract)
            st = m["status"] if m else "未知"
            states[st] += 1
            if st not in ("已具备", "可外协"):
                non_ready.append(u)
        ready = all(s in ("已具备", "可外协") for s in states)
        summary_ev = []
        for u in non_ready[:3]:
            summary_ev.append(make_evidence(case_doc, "case.json#%s" % u.get("locator"),
                                            make_quote(u.get("name"), "name")))
        out.append({"id": "EQ-%s-summary" % pid, "dimension": "equipment_materials",
                    "check": "%s 资源就绪度汇总 %s" % (pid, dict(states)), "target": p["locator"],
                    "verdict": "ok" if (not states or ready) else "conditional",
                    "resource_state": (max(states, key=states.get) if states else "未知"),
                    "conditions": [] if (not states or ready) else ["需采购/不具备的资源见上列条目，逐一落实后复核"],
                    "evidence": summary_ev, "confidence": "medium", "needs_expert": False,
                    "notes": [{"states": dict(states)}]})
    return out


def gen_time_cost(profiles, ledger, case_doc):
    out = []
    for p in profiles:
        pid = p["plan_id"]
        cost_e = next((e for e in ledger if e["id"] == "L-cost-%s" % pid), None)
        days_e = next((e for e in ledger if e["id"] == "L-days-%s" % pid), None)
        if cost_e and cost_e["status"] == "computed" and p["budget"] is not None:
            over = cost_e["result"] > p["budget"]
            out.append({"id": "TC-%s-budget" % pid, "dimension": "time_cost",
                        "check": "%s 估算成本 %s %s vs 预算 %s" % (pid, cost_e["result"], cost_e["unit"], p["budget"]),
                        "target": p["locator"], "verdict": "blocked" if over else "ok",
                        "conditions": ["估算成本超出预算 %s，需压缩用量或上调预算" % round(cost_e["result"] - p["budget"], 2)] if over else [],
                        "evidence": [{"locator": cost_e["id"], "quote": cost_e["expr"], "kind": "ledger",
                                      "quote_verified": True}],
                        "confidence": "high", "needs_expert": False,
                        "notes": [{"arithmetic_ref": cost_e["id"]}]})
        elif p["budget"] is not None:
            out.append({"id": "TC-%s-budget" % pid, "dimension": "time_cost",
                        "check": "%s 预算 %s 无法与成本比对（成本不可核算）" % (pid, p["budget"]),
                        "target": p["locator"], "verdict": "unconfirmed",
                        "conditions": ["补齐资源单价与用量后复核预算"],
                        "evidence": [], "confidence": "medium", "needs_expert": False, "notes": []})
        if days_e and days_e["status"] == "computed" and p["deadline_days"] is not None:
            over = days_e["result"] > p["deadline_days"]
            out.append({"id": "TC-%s-days" % pid, "dimension": "time_cost",
                        "check": "%s 折算工作日 %s vs 排期 %s 天" % (pid, days_e["result"], p["deadline_days"]),
                        "target": p["locator"], "verdict": "blocked" if over else "ok",
                        "conditions": ["估算时长 %s 天超过窗口 %s 天，需并行/压缩等待或延期" % (days_e["result"], p["deadline_days"])] if over else [],
                        "evidence": [{"locator": days_e["id"], "quote": days_e["expr"], "kind": "ledger",
                                      "quote_verified": True}],
                        "confidence": "high", "needs_expert": False,
                        "notes": [{"arithmetic_ref": days_e["id"]}]})
    return out


def gen_risks(profiles, case_doc, case_data):
    out = []
    case_level = None
    for _, v in deep_find(case_data, RISK_KEYS, maxdepth=3):
        if isinstance(v, (list, dict)):
            case_level = v
            break
    for p in profiles:
        pr = None
        for _, v in deep_find(p["raw_plan"], RISK_KEYS, maxdepth=2):
            if isinstance(v, (list, dict)):
                pr = v
                break
        if pr is None:
            out.append({"id": "RK-%s" % p["plan_id"], "dimension": "risks_exceptions",
                        "check": "%s 未声明风险与异常处置%s"
                                 % (p["plan_id"], "" if case_level is None else "（仅有案例级风险登记，未落到本方案）"),
                        "target": p["locator"], "verdict": "missing",
                        "conditions": ["至少覆盖设备故障、材料批次差异、样本不足、参数越界四类，各配触发条件/处置/责任人"],
                        "evidence": [], "confidence": "high", "needs_expert": False, "notes": []})
            continue
        n = len(pr)
        out.append({"id": "RK-%s" % p["plan_id"], "dimension": "risks_exceptions",
                    "check": "%s 风险条目数=%d" % (p["plan_id"], n), "target": p["locator"],
                    "verdict": "ok" if n >= 4 else "conditional",
                    "conditions": [] if n >= 4 else ["补齐设备/批次/样本/参数四类风险的触发与降级路径"],
                    "evidence": [make_evidence(case_doc, "case.json#%s" % p["locator"], make_quote(pr, "risks"))],
                    "confidence": "medium", "needs_expert": False, "notes": [{"risk_count": n}]})
    return out


def gen_ethics(profiles, case_doc):
    out = []
    for p in profiles:
        text = json_dumps_compact(p["raw_plan"]).lower()
        hits = {}
        for flag, needles in KEYWORD_FLAGS.items():
            for nd in needles:
                if nd.lower() in text:
                    hits[flag] = nd
                    break
        pid = p["plan_id"]
        if not hits:
            out.append({"id": "ES-%s" % pid, "dimension": "ethics_safety",
                        "check": "%s 伦理/安全适用性未声明（动物/人体/危化/个人数据均未见）" % pid,
                        "target": p["locator"], "verdict": "unconfirmed", "needs_expert": True,
                        "conditions": ["由项目负责人确认是否涉及动物、人体样本、危险试剂或个人数据；若涉及需补对应审批"],
                        "evidence": [], "confidence": "medium",
                        "notes": [{"note": "未命中关键词不等于不涉及：按『方案没写≠满足』记为待确认"}]})
            continue
        for flag, needle in hits.items():
            level, materials, days = ETHICS_META[flag]
            out.append({"id": "ES-%s-%s" % (pid, flag), "dimension": "ethics_safety",
                        "check": '%s 涉及 %s（命中 "%s"），需核对审批材料' % (pid, level, needle),
                        "target": p["locator"], "verdict": "unconfirmed", "needs_expert": True,
                        "conditions": ["补充 %s 材料：%s；预估 %s" % (level, "、".join(materials), days)],
                        "evidence": [make_evidence(case_doc, "case.json#%s" % p["locator"],
                                                   find_snippet(case_doc, needle))],
                        "confidence": "high", "notes": [{"level": level}]})
    return out


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------

def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def score_plan(profile, review_items, ledger, contract):
    pid = profile["plan_id"]
    blocks = sum(1 for it in review_items
                 if it.get("dimension") in ("equipment_materials", "time_cost", "parameter_ranges")
                 and it.get("verdict") == "blocked" and pid in (it.get("id") or ""))
    pv = [it for it in review_items if it.get("dimension") == "parameter_ranges" and pid in (it.get("id") or "")]
    pv_done = [it for it in pv if it.get("verdict") in ("ok", "blocked", "conflict")]
    pv_ok = [it for it in pv if it.get("verdict") == "ok"]
    param_score = _clamp(100.0 * len(pv_ok) / max(1, len(pv_done)))
    state_score = {"已具备": 1.0, "可外协": 0.7, "需采购": 0.35, "不具备": 0.0, "未知": 0.3}
    states = []
    for u in profile["resource_uses"]:
        m = match_use_to_resource(u, contract)
        states.append(m["status"] if m else "未知")
    resource_score = _clamp(100 * sum(state_score.get(s, 0.3) for s in states) / max(1, len(states)))
    days_e = next((e for e in ledger if e["id"] == "L-days-%s" % pid), None)
    slack = None
    if days_e and days_e["status"] == "computed" and profile["deadline_days"] is not None:
        slack = profile["deadline_days"] - days_e["result"]
    schedule_score = _clamp(50 + 2 * slack) if slack is not None else 50.0
    cost_e = next((e for e in ledger if e["id"] == "L-cost-%s" % pid), None)
    cost_score = 50.0
    if cost_e and cost_e["status"] == "computed" and profile["budget"]:
        cost_score = _clamp(100 * (1 - cost_e["result"] / profile["budget"]) * 2)
    req = next((e for e in ledger if e["id"] in ("L-sample-required-%s" % pid, "L-sample-prop-%s" % pid)
                and e["status"] == "computed"), None)
    n_total = profile["sample"].get("n")
    sample_score = 50.0
    if req and n_total is not None:
        groups = profile["sample"].get("groups") or 1
        sample_score = _clamp(100 * min(1.0, n_total / max(1e-9, req["result"] * groups)))
    ctrl = next((it for it in review_items if it.get("id") == "CTRL-%s" % pid), None)
    control_score = 100.0 if (ctrl and ctrl["verdict"] == "ok") else (
        50.0 if (ctrl and ctrl["verdict"] == "conditional") else 0.0)
    sub = {"hard_blocks": _clamp(100 - 25 * blocks), "parameter_compliance": param_score,
           "resource_readiness": resource_score, "schedule_slack": schedule_score,
           "cost_headroom": cost_score, "sample_adequacy": sample_score,
           "control_strength": control_score}
    total = round(sum(SCORE_WEIGHTS[k] * sub[k] for k in SCORE_WEIGHTS), 2)
    return {"plan_id": pid, "subscores": {k: round(v, 2) for k, v in sub.items()},
            "weighted_total": total, "blocks": blocks,
            "formula": "sum(w_k * sub_k)", "weights": dict(SCORE_WEIGHTS)}


def rank_plans(scores):
    ranked = sorted(scores, key=lambda x: (-x["weighted_total"], x["plan_id"]))
    base = dict(SCORE_WEIGHTS)
    sens = []
    for w in (0.15, 0.45):
        scale = (1 - w) / (1 - base["hard_blocks"])
        w2 = {k: (w if k == "hard_blocks" else base[k] * scale) for k in base}
        alt = sorted(scores, key=lambda x: (-sum(w2[k] * x["subscores"][k] for k in w2), x["plan_id"]))
        sens.append({"hard_blocks_weight": w, "ranking": [s["plan_id"] for s in alt]})
    flips = "none" if all([s["plan_id"] for s in ranked] == s["ranking"] for s in sens) else "sensitive_to_weight"
    return ranked, sens, flips


# ---------------------------------------------------------------------------
# 总装配
# ---------------------------------------------------------------------------

def analyze(case_data, case_doc):
    plans_raw = None
    for _, v in deep_find(case_data, PLAN_KEYS, maxdepth=3):
        plans_raw = v
        break
    plan_list = as_plan_list(plans_raw)
    profiles = [build_plan_profile(p, i, case_doc, case_data) for i, p in enumerate(plan_list)]
    contract = get_contract_items(case_data)
    domains = get_domains(case_data)

    ledger, cost_info = [], []
    for p in profiles:
        entries, info = compute_cost(p, contract, case_doc)
        ledger.extend(entries)
        cost_info.append(info)
        ledger.extend(compute_time(p))
        ledger.extend(compute_sample(p))

    review = {d: [] for d in DIMENSIONS}
    for p in profiles:
        review["assumptions_variables"].extend(gen_assumptions(p, case_doc))
    review["controls"].extend(gen_controls(profiles, case_doc))
    review["equipment_materials"].extend(gen_equipment(profiles, contract, case_doc, cost_info))
    review["time_cost"].extend(gen_time_cost(profiles, ledger, case_doc))
    for p in profiles:
        review["parameter_ranges"].extend(domain_check(p, domains, case_doc))
    review["risks_exceptions"].extend(gen_risks(profiles, case_doc, case_data))
    review["ethics_safety"].extend(gen_ethics(profiles, case_doc))

    all_items = [it for lst in review.values() for it in lst]
    scores = [score_plan(p, all_items, ledger, contract) for p in profiles]
    ranking, sensitivity, flips = rank_plans(scores) if scores else ([], [], "not_evaluated")

    seen, evidence_index = set(), []
    for it in all_items:
        for ev in it.get("evidence", []):
            key = (ev.get("locator"), ev.get("quote"))
            if key in seen:
                continue
            seen.add(key)
            evidence_index.append(dict({"eid": "E%d" % (len(evidence_index) + 1)}, **ev))

    return {
        "review_mode": "versioned_plans" if len(profiles) > 1 else "single_plan",
        "plan_profiles": [{"plan_id": p["plan_id"], "locator": p["locator"], "goal": p["goal"],
                           "budget": p["budget"], "deadline_days": p["deadline_days"],
                           "step_count": len(p["steps"]), "parameter_count": len(p["parameters"]),
                           "resource_use_count": len(p["resource_uses"]), "sample": p["sample"]}
                          for p in profiles],
        "review": review,
        "arithmetic_ledger": ledger,
        "plan_comparison": {"weights": dict(SCORE_WEIGHTS), "scores": scores,
                            "ranking": [s["plan_id"] for s in ranking],
                            "recommended": ranking[0]["plan_id"] if ranking else None,
                            "sensitivity": sensitivity, "sensitivity_flips": flips,
                            "method": "确定性加权评分，权重为审查辅助口径而非测量值"},
        "evidence_index": evidence_index,
        "counts": {"plans": len(profiles), "contract_items": len(contract), "domains": len(domains),
                   "review_items": len(all_items), "ledger_entries": len(ledger),
                   "computed_ledger": sum(1 for e in ledger if e["status"] == "computed"),
                   "evidence_quoted": sum(1 for e in evidence_index if e.get("quote_verified")),
                   "evidence_total": len(evidence_index)},
    }


VERDICT_MARK = {"ok": "✅", "conditional": "⚠️", "blocked": "⛔", "unconfirmed": "❓",
                "missing": "⬜", "conflict": "🔶"}


def render_report(result):
    lines = ["# 实验方案可行性审查报告（可编辑）\n",
             "- case_id: `%s`" % result.get("case_id"),
             "- 状态: `%s`" % result.get("status"),
             "- 方案数: %s" % (result.get("counts", {}) or {}).get("plans"),
             "- 推荐方案: `%s`\n" % (result.get("plan_comparison", {}) or {}).get("recommended")]
    es = result.get("executive_summary", {})
    if es:
        lines += ["## 结论摘要\n", "- 结论: %s" % es.get("verdict"),
                  "- 阻断项: %s；未确认项: %s" % (es.get("blocking_count"), es.get("unconfirmed_count"))]
        for c in es.get("top_conditions", []):
            lines.append("- 条件: %s" % c)
        lines.append("")
    lines.append("## 方案档案\n")
    for p in result.get("plan_profiles", []):
        lines.append("- **%s**: 目标=%s; 预算=%s; 排期=%s 天; 步骤=%s; 参数=%s; 资源项=%s"
                     % (p.get("plan_id"), p.get("goal"), p.get("budget"), p.get("deadline_days"),
                        p.get("step_count"), p.get("parameter_count"), p.get("resource_use_count")))
    lines.append("")
    dim_labels = {"assumptions_variables": "假设与变量", "controls": "对照组",
                  "equipment_materials": "设备与材料", "time_cost": "时间与成本",
                  "parameter_ranges": "参数范围", "risks_exceptions": "风险与异常",
                  "ethics_safety": "伦理与安全"}
    lines.append("## 七类审查\n")
    for dim in DIMENSIONS:
        items = (result.get("review") or {}).get(dim, [])
        lines.append("### %s（%d 项）\n" % (dim_labels[dim], len(items)))
        for it in items:
            lines.append("- %s `%s` [%s] %s" % (VERDICT_MARK.get(it.get("verdict"), "•"),
                                                it.get("id"), it.get("verdict"), it.get("check")))
            if it.get("conditions"):
                lines.append("  - 条件/处置: %s" % "；".join(it["conditions"]))
            for ev in it.get("evidence", [])[:3]:
                quote = (ev.get("quote") or "")[:110].replace("\n", " ")
                lines.append("  - 证据: `%s` → `%s`" % (ev.get("locator"), quote))
        lines.append("")
    return "\n".join(lines)


def render_ledger_md(result):
    out = ["## 算术台账（可复算）\n",
           "| id | 项 | 表达式 | 结果 | 单位 | 状态 | 复算一致 |",
           "|---|---|---|---|---|---|---|"]
    for e in result.get("arithmetic_ledger", []):
        expr = (e.get("expr") or "—").replace("|", "\\|")
        out.append("| %s | %s | `%s` | %s | %s | %s | %s |"
                   % (e.get("id"), e.get("title"), expr, e.get("result"),
                      e.get("unit") or "—", e.get("status"), e.get("recompute_ok")))
    out.append("")
    for e in result.get("arithmetic_ledger", []):
        if e.get("terms"):
            out.append("**%s** 代入值：" % e["id"])
            for t in e["terms"]:
                out.append("- `%s` = %s (%s) ← `%s`" % (t["symbol"], t.get("value"),
                                                       t.get("unit") or "—", t.get("locator") or "derived"))
            out.append("")
    return "\n".join(out)
