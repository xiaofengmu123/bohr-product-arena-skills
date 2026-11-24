#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_impl.py — 科研问题驱动检索 Skill 的执行后端（仅 Python 标准库）。

设计目标：
  1. 确定性的状态机：run -> set-plan -> import -> evidence -> set-stop -> assemble -> selfcheck，
     每一步只做增量更新，任何时刻落到 /app/output/result.json 的都是合法 JSON。
  2. 证据溯源强化：每条结论（claim）都要求绑定 locator + 逐字 quote；
     本地材料可对 quote 做规范化子串校验（evidence_map.verification），未命中/无引用
     的单列 orphan_claims 与 unverified_quotes，绝不静默丢弃。
  3. 不编造：缺数据返回 insufficient_evidence / partial，并写清 limits，
     空输入/坏 JSON/坏编码均降级为合法结果且退出码 0。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 常量与默认值
# ---------------------------------------------------------------------------
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATE_FILE = "state.json"
MATERIAL_CACHE = "materials_cache.json"
MAX_INPUT_FILE = 2 * 1024 * 1024  # 单输入文件 2MB 上限，超限跳过

SCORE_FORMULA = "0.45*relevance + 0.30*evidence_strength + 0.15*metadata_completeness + 0.10*source_reliability"
META_FIELDS = ("title", "authors", "year", "journal", "doi")
TEXT_SUFFIXES = (
    ".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".html",
    ".yml", ".yaml", ".tex", ".py", ".rst", ".log",
)
CONTRACT_KEYS = (
    "output_contract", "output", "schema", "required_fields", "contract",
)
WS_RE = re.compile(r"\s+")
CLAIM_RE = re.compile(r"\bCLM-[A-Za-z0-9_\-\.]+\b")


def norm_ws(s) -> str:
    """规范化空白，供逐字 quote 子串校验使用（不做语义改写）。"""
    if s is None:
        return ""
    return WS_RE.sub(" ", str(s)).strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha1_hex(s) -> str:
    return hashlib.sha1(norm_ws(s).encode("utf-8")).hexdigest()


def log(msg: str):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# 文件读写（容错）
# ---------------------------------------------------------------------------
def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def read_text_robust(path: str):
    """utf-8-sig 优先，坏编码降级 latin-1；返回 (text, warnings)。"""
    warnings = []
    raw = None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, ["unreadable: %s" % e]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), warnings
        except (UnicodeDecodeError, LookupError):
            warnings.append("decode_failed:" + enc)
    return raw.decode("latin-1", errors="replace"), warnings


def read_json_robust(path: str):
    """坏 JSON 降级返回 None 并记录告警，不抛异常。"""
    text, warnings = read_text_robust(path)
    if text is None:
        return None, warnings
    try:
        return json.loads(text), warnings
    except (ValueError, TypeError) as e:
        return None, warnings + ["bad_json:%s" % e]


def load_json(path: str, default=None):
    if not os.path.isfile(path):
        return default
    obj, _ = read_json_robust(path)
    return default if obj is None else obj


def write_json(path: str, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_text(path: str, text: str):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# 状态骨架
# ---------------------------------------------------------------------------
def new_state() -> dict:
    return {
        "schema_version": "2.1.0",
        "case_id": None,
        "research_question": None,
        "status": "partial",
        "status_notes": "",
        "input_dir": None,
        "input_files": [],
        "materials": [],
        "output_contract_source": None,
        "output_contract": None,
        "sub_questions": [],
        "query_plan": {"order": [], "dependencies": [], "parallel_groups": []},
        "term_expansion": {"concepts": [], "ambiguities": [], "max_variants_per_concept": 6},
        "stop_decision": {
            "status": "handoff",
            "criteria": [],
            "marginal_benefit": "",
            "uncovered_sub_questions": [],
            "rationale": "",
        },
        "records": [],
        "evidence": [],
        "screening": {
            "included": [], "excluded": [], "uncertain": [], "merged_duplicates": [],
            "total_seen": 0, "notes": "",
        },
        "evidence_map": {
            "by_claim": {}, "by_result": {}, "by_sub_question": {},
            "orphan_claims": [], "unverified_quotes": [],
            "verification": {
                "total_claims": 0, "verified_local": 0, "unverified_local": 0,
                "external_unverifiable": 0, "orphan": 0,
            },
        },
        "sources": {"databases": [], "materials": [], "retrieved_on": None},
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {},
        "input_error": False,
        "assemble_error": None,
    }


def load_state(work: str) -> dict:
    st = load_json(os.path.join(work, STATE_FILE), default=None)
    if st is None:
        st = new_state()
    # 防御：补齐可能缺失的顶层键（老状态文件不崩溃）
    for k, v in new_state().items():
        if k not in st:
            st[k] = v
    return st


def save_state(work: str, state: dict):
    write_json(os.path.join(work, STATE_FILE), state)


def append_log(state: dict, action: str, reason: str, status: str = "ok"):
    state["change_log"].append({
        "time": now_iso(), "action": action, "reason": reason, "status": status,
    })


# ---------------------------------------------------------------------------
# 输入目录扫描
# ---------------------------------------------------------------------------
def scan_input_dir(inp: str) -> dict:
    """扫描 /input 全部文件，不假设固定键名；返回解析结果。"""
    res = {
        "exists": False, "files": [], "case": None, "case_path": None,
        "task": None, "task_path": None, "materials": [], "degradations": [],
    }
    if not os.path.isdir(inp):
        res["degradations"].append({"file": inp, "issue": "input_dir_missing"})
        return res
    res["exists"] = True
    case_cands, task_cands = [], []
    for root, _dirs, fnames in os.walk(inp):
        for fn in sorted(fnames):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, inp).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError as e:
                res["degradations"].append({"file": rel, "issue": "stat_failed:%s" % e})
                continue
            res["files"].append({"path": rel, "size": size})
            if size > MAX_INPUT_FILE:
                res["degradations"].append({"file": rel, "issue": "too_large", "size": size})
                continue
            base = os.path.basename(rel).lower()
            if base in ("case.json", "input.json", "case.jsonl"):
                case_cands.append((0, rel, p))
            elif base in ("task.md", "task.txt", "instructions.md", "README.md"):
                task_cands.append((0, rel, p))
            elif base.endswith(".json") and "case" in base:
                case_cands.append((1, rel, p))
            elif base.endswith((".md", ".txt")) and "task" in base:
                task_cands.append((1, rel, p))
            elif rel.startswith("materials/"):
                res["materials"].append({"name": rel, "path": p, "size": size})
    case_cands.sort(); task_cands.sort()
    for _, rel, p in case_cands[:1]:
        obj, warns = read_json_robust(p)
        res["case"] = obj
        res["case_path"] = rel
        for w in warns:
            res["degradations"].append({"file": rel, "issue": w})
    for _, rel, p in task_cands[:1]:
        txt, warns = read_text_robust(p)
        res["task"] = txt
        res["task_path"] = rel
        for w in warns:
            res["degradations"].append({"file": rel, "issue": w})
    return res


def extract_contract(case_obj) -> tuple:
    """从 case.json 中定位 output_contract（或其别名）。"""
    if not isinstance(case_obj, dict):
        return None, None
    for key in CONTRACT_KEYS:
        if key in case_obj and case_obj[key] is not None:
            return key, case_obj[key]
    return None, None


def default_for(v):
    if isinstance(v, dict):
        return {}
    if isinstance(v, list):
        return []
    return None


def fill_contract_missing(dst: dict, contract):
    """把 output_contract 的字段原样（缺省 null/[]）补齐到 dst，不改名不删未知键。"""
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k not in dst:
                dst[k] = default_for(v)
            elif isinstance(v, dict) and isinstance(dst[k], dict):
                fill_contract_missing(dst[k], v)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                if item not in dst:
                    dst[item] = None
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name and name not in dst:
                    dst[name] = default_for(item.get("default"))


# ---------------------------------------------------------------------------
# run 命令：落盘 partial 骨架
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    ensure_dir(args.work)
    ensure_dir(args.output)
    state = load_state(args.work)
    append_log(state, "run", "开始：扫描输入目录并落盘 partial 骨架")
    scan = scan_input_dir(args.input)
    state["input_dir"] = args.input
    state["input_files"] = scan["files"]
    state["materials"] = [{"name": m["name"], "size": m["size"]} for m in scan["materials"]]
    for d in scan["degradations"]:
        if d not in state["degradations"]:
            state["degradations"].append(d)

    case_obj = scan["case"]
    if case_obj is None:
        state["input_error"] = True
        state["case_id"] = None
        state["errors"].append({"file": scan["case_path"], "issue": "case_json_missing_or_unparseable"})
    else:
        state["case_id"] = (case_obj.get("case_id") or case_obj.get("id") or case_obj.get("challenge_id") or "unknown")
        src_key, contract = extract_contract(case_obj)
        state["output_contract_source"] = src_key
        state["output_contract"] = contract
        rq = case_obj.get("research_question") or case_obj.get("question") or case_obj.get("task")
        if not rq and scan["task"]:
            rq = scan["task"].strip().splitlines()[0] if scan["task"].strip() else None
        state["research_question"] = rq

    # 本地材料文本缓存（供 quote 校验），仅缓存文本类文件
    cache = load_json(os.path.join(args.work, MATERIAL_CACHE), default={})
    if isinstance(cache, dict):
        for m in scan["materials"]:
            if os.path.splitext(m["name"])[1].lower() in TEXT_SUFFIXES and m["name"] not in cache:
                txt, warns = read_text_robust(m["path"])
                if txt is not None:
                    cache[m["name"]] = txt
                for w in warns:
                    state["degradations"].append({"file": m["name"], "issue": w})
    write_json(os.path.join(args.work, MATERIAL_CACHE), cache)

    state["sources"]["materials"] = [m["name"] for m in scan["materials"]]
    state["sources"]["retrieved_on"] = now_iso()
    state["status_notes"] = "骨架已落盘，等待拆解/检索/筛选增量更新。"
    state["status"] = "input_error" if state["input_error"] else "partial"
    append_log(state, "run", "骨架落盘完成，status=%s" % state["status"])
    save_state(args.work, state)
    return assemble(args, state)


# ---------------------------------------------------------------------------
# set-plan / set-stop：拆解、查询规划、术语扩展、停止判断
# ---------------------------------------------------------------------------
def _load_payload(args) -> tuple:
    """--file 优先，其次 --inline；返回 (dict 或 list, error)。"""
    if getattr(args, "file", None):
        obj, warns = read_json_robust(args.file)
        if obj is None:
            return None, warns or ["bad_json"]
        return obj, warns
    if getattr(args, "inline", None):
        try:
            return json.loads(args.inline), []
        except (ValueError, TypeError) as e:
            return None, ["bad_inline:%s" % e]
    return None, ["no_payload"]


def _as_list(v, key):
    if isinstance(v, list):
        return v
    if isinstance(v, dict) and key in v:
        return v[key] if isinstance(v[key], list) else [v[key]]
    return []


def cmd_set_plan(args) -> int:
    state = load_state(args.work)
    payload, warns = _load_payload(args)
    for w in warns:
        state["degradations"].append({"issue": w, "command": "set-plan"})
    if payload is None:
        state["errors"].append({"command": "set-plan", "issue": "no_payload"})
        append_log(state, "set-plan", "无有效负载", "failed")
        save_state(args.work, state)
        return 0
    if isinstance(payload, list):
        payload = {"sub_questions": payload}
    if "research_question" in payload and payload["research_question"]:
        state["research_question"] = payload["research_question"]
    if "sub_questions" in payload:
        state["sub_questions"] = _as_list(payload, "sub_questions")
    if "query_plan" in payload and isinstance(payload["query_plan"], dict):
        state["query_plan"] = payload["query_plan"]
    if "term_expansion" in payload and isinstance(payload["term_expansion"], dict):
        state["term_expansion"] = payload["term_expansion"]
    if "stop_decision" in payload and isinstance(payload["stop_decision"], dict):
        state["stop_decision"] = payload["stop_decision"]
    append_log(state, "set-plan", "更新拆解/查询规划/术语扩展/停止判断，共 %d 条子问题" % len(state["sub_questions"]))
    save_state(args.work, state)
    return 0


def cmd_set_stop(args) -> int:
    state = load_state(args.work)
    payload, warns = _load_payload(args)
    for w in warns:
        state["degradations"].append({"issue": w, "command": "set-stop"})
    if isinstance(payload, dict) and payload.get("stop_decision"):
        state["stop_decision"] = payload["stop_decision"]
        append_log(state, "set-stop", "停止判断：%s" % state["stop_decision"].get("status"))
    else:
        state["errors"].append({"command": "set-stop", "issue": "no_stop_decision"})
    save_state(args.work, state)
    return 0


# ---------------------------------------------------------------------------
# 证据与记录处理
# ---------------------------------------------------------------------------
def source_reliability_for(rec: dict) -> float:
    db = str(rec.get("database") or rec.get("source") or "").lower()
    url = str(rec.get("url") or "").lower()
    if any(k in db for k in ("materials", "local", "pdf")):
        return 1.0
    if "paper_search" in db or "crossref" in db or "openalex" in db:
        return 0.9
    if any(k in db for k in ("arxiv", "pubmed", "semanticscholar", "doi.org", "dblp")):
        return 0.8
    if any(k in url for k in ("nature.com", "science.org", "sciencedirect.com", "springer.com",
                              "ieee.org", "acm.org", "pnas.org", "cell.com", "thelancet.com",
                              "jama", "nejm.org", "wiley.com", "mdpi.com", "frontiersin.org")):
        return 0.7
    if db.startswith("web") or "http" in db:
        return 0.5
    return 0.5


def metadata_completeness(rec: dict) -> float:
    n = 0
    for f in META_FIELDS:
        v = rec.get(f)
        if v not in (None, "", [], {}):
            n += 1
    return round(n / len(META_FIELDS), 3)


def evidence_item_strength(ev: dict, local_verified) -> float:
    quote = bool(str(ev.get("quote") or "").strip())
    locator = bool(ev.get("locator")) and bool(str(ev.get("locator", "")).strip())
    if quote and locator:
        if local_verified is True:
            return 1.0
        if local_verified is False:
            return 0.1
        return 0.6  # 外部来源，已由 Agent 记录逐字 quote，但本脚本无法二次核验
    if quote or locator:
        return 0.3
    return 0.0


def material_cache(work: str) -> dict:
    c = load_json(os.path.join(work, MATERIAL_CACHE), default={})
    return c if isinstance(c, dict) else {}


def verify_local_quote(source_ref: str, quote: str, cache: dict):
    """在本地材料文本中规范化匹配逐字 quote；返回 True/False/None(非本地)。"""
    q = norm_ws(quote)
    if not q:
        return None
    # source_ref 形如 materials/xxx.md 或 materials/xxx.md#p3；据此定位本地材料
    fkey = None
    for known in cache.keys():
        if known in source_ref or source_ref.startswith(known.split("#")[0]):
            fkey = known
            break
    if fkey is None:
        return None
    txt = cache.get(fkey)
    if not isinstance(txt, str):
        return None
    return q in norm_ws(txt)


def load_material_for_source(source_ref: str, cache: dict):
    fkey = None
    for known in cache.keys():
        if known in source_ref or source_ref.startswith(known.split("#")[0]):
            fkey = known
            break
    return fkey


def cmd_import(args) -> int:
    state = load_state(args.work)
    payload, warns = _load_payload(args)
    for w in warns:
        state["degradations"].append({"issue": w, "command": "import"})
    if payload is None:
        state["errors"].append({"command": "import", "issue": "no_payload"})
        append_log(state, "import", "无有效负载", "failed")
        save_state(args.work, state)
        return 0
    if isinstance(payload, dict):
        records = _as_list(payload, "records")
    else:
        records = payload if isinstance(payload, list) else []
    cache = material_cache(args.work)
    for rec in records:
        if not isinstance(rec, dict):
            state["degradations"].append({"issue": "record_not_dict", "command": "import"})
            continue
        ridx = len(state["records"])
        rec.setdefault("rank", None)
        rec.setdefault("title", None)
        rec.setdefault("authors", None)
        rec.setdefault("year", None)
        rec.setdefault("journal", None)
        rec.setdefault("doi", None)
        rec.setdefault("url", None)
        rec.setdefault("database", None)
        rec.setdefault("sub_question_ids", [])
        rec.setdefault("relevance", 0.0)
        rec.setdefault("screening", "uncertain")
        rec.setdefault("evidencing", rec.pop("evidence", []) if "evidence" in rec else [])

        evs = []
        for ev in rec.get("evidencing", []):
            if not isinstance(ev, dict):
                continue
            e = dict(ev)
            e.setdefault("evidence_id", "EV-%03d" % (len(state["evidence"]) + len(evs) + 1))
            e.setdefault("claim_id", None)
            e.setdefault("claim_text", "")
            e.setdefault("kind", "relevance")
            e.setdefault("source_ref", rec.get("url") or rec.get("database"))
            e.setdefault("locator", None)
            e.setdefault("quote", "")
            e.setdefault("source", rec.get("database") or rec.get("url"))
            q = str(e.get("quote") or "")
            e["quote_hash"] = "sha1:" + sha1_hex(q) if q else None
            e["timestamp"] = e.get("timestamp") or now_iso()
            e["confidence"] = e.get("confidence") or "medium"
            sref = str(e.get("source_ref") or "")
            lv = verify_local_quote(sref, q, cache)
            e["local_verified"] = lv
            fkey = load_material_for_source(sref, cache) if q else None
            if lv is False and fkey:
                e["_verify_failed_locator"] = fkey
            evs.append(e)

        rec["evidence"] = evs
        rec.pop("evidencing", None)

        sr = float(rec.get("source_reliability") or source_reliability_for(rec))
        sr = max(0.0, min(1.0, sr))
        rec["source_reliability"] = sr
        strengths = [evidence_item_strength(e, e.get("local_verified")) for e in evs]
        rec["evidence_strength"] = round(sum(strengths) / len(strengths), 3) if strengths else 0.0
        rec["meta_complete"] = metadata_completeness(rec)
        rel = float(rec.get("relevance") or 0.0)
        rel = max(0.0, min(1.0, rel))
        rec["relevance"] = rel
        rec["score"] = round(0.45 * rel + 0.30 * rec["evidence_strength"]
                             + 0.15 * rec["meta_complete"] + 0.10 * sr, 3)
        rec["score_breakdown"] = {
            "relevance": rel,
            "evidence_strength": rec["evidence_strength"],
            "metadata_completeness": rec["meta_complete"],
            "source_reliability": sr,
            "score": rec["score"],
            "formula": SCORE_FORMULA,
        }
        state["records"].append(rec)

        # 筛选账本：全量记录，排除项不静默丢弃
        screen = rec.get("screening") or "uncertain"
        entry = {
            "record_index": ridx,
            "title": rec.get("title"),
            "reason": rec.get("screening_reason", ""),
            "evidence_ids": [e["evidence_id"] for e in evs],
        }
        ledger = {"included": [], "excluded": [], "uncertain": []}
        if screen == "included":
            ledger["included"].append(entry)
        elif screen == "excluded":
            ledger["excluded"].append(entry)
        else:
            ledger["uncertain"].append(entry)
        for k, v in ledger.items():
            state["screening"][k].extend(v)
        state["screening"]["total_seen"] += 1

    # 排序：仅在 included 内按 score 降序，再赋 rank
    inc = [rec for rec in state["records"] if rec.get("screening") == "included"]
    inc.sort(key=lambda r: (r.get("score") or 0.0), reverse=True)
    rankmap = {}
    for i, r in enumerate(inc, start=1):
        r["rank"] = i
        rankmap[id(r)] = i
    for r in state["records"]:
        if r.get("screening") != "included":
            r["rank"] = None

    rebuild_evidence_map(state, cache)
    append_log(state, "import", "导入 %d 条候选，排序与证据映射已重建" % len(records))
    save_state(args.work, state)
    return 0


def cmd_evidence(args) -> int:
    state = load_state(args.work)
    payload, warns = _load_payload(args)
    for w in warns:
        state["degradations"].append({"issue": w, "command": "evidence"})
    items = []
    if isinstance(payload, dict):
        items = _as_list(payload, "evidence")
        if not items and isinstance(payload.get("claims"), list):
            # claims 负载：每条 claim 可带 evidence 列表
            for c in payload["claims"]:
                if isinstance(c, dict):
                    items.extend(c.get("evidence") if isinstance(c.get("evidence"), list) else [c])
    elif isinstance(payload, list):
        items = payload
    cache = material_cache(args.work)
    for ev in items:
        if not isinstance(ev, dict):
            continue
        e = dict(ev)
        e.setdefault("evidence_id", "EV-%03d" % (len(state["evidence"]) + 1))
        e.setdefault("claim_id", None)
        e.setdefault("claim_text", "")
        e.setdefault("kind", "query")
        e.setdefault("source_ref", e.get("source"))
        e.setdefault("locator", None)
        e.setdefault("quote", "")
        q = str(e.get("quote") or "")
        e["quote_hash"] = "sha1:" + sha1_hex(q) if q else None
        e["timestamp"] = e.get("timestamp") or now_iso()
        e["confidence"] = e.get("confidence") or "medium"
        e["local_verified"] = verify_local_quote(str(e.get("source_ref") or ""), q, cache)
        state["evidence"].append(e)
    rebuild_evidence_map(state, cache)
    append_log(state, "evidence", "追加 %d 条证据记录" % len(items))
    save_state(args.work, state)
    return 0


def rebuild_evidence_map(state: dict, cache: dict):
    """重建证据映射：by_claim / by_result / by_sub_question + 核验统计与孤儿/未命中。"""
    em = state["evidence_map"]
    by_claim, by_result, by_sub = {}, {}, {}
    all_ev = []
    for ev in state["evidence"]:
        all_ev.append(("ledger", ev))
    for i, rec in enumerate(state["records"]):
        for ev in rec.get("evidence", []):
            all_ev.append((str(i), ev))

    claimed = set()
    for loc, ev in all_ev:
        eid = ev.get("evidence_id")
        cid = ev.get("claim_id")
        if cid:
            claimed.add(cid)
            by_claim.setdefault(cid, []).append(eid)
        if loc.isdigit():
            key = loc
            by_result.setdefault(key, []).append(eid)
            for sq in state["records"][int(loc)].get("sub_question_ids", []):
                by_sub.setdefault(str(sq), []).append(eid)
    # 从 relevance_reason / screening_reason 提取 CLM-xxx 引用，缺证据者记为孤儿
    referenced = set()
    for rec in state["records"]:
        for field in ("relevance_reason", "screening_reason"):
            txt = str(rec.get(field) or "")
            referenced.update(CLAIM_RE.findall(txt))
    orphan = sorted(c for c in referenced if c not in claimed)

    unverified = []
    verified_local = external_unverifiable = unverified_local = 0
    for loc, ev in all_ev:
        q = str(ev.get("quote") or "").strip()
        lv = ev.get("local_verified")
        if not q:
            continue
        if lv is True:
            verified_local += 1
        elif lv is False:
            unverified_local += 1
            unverified.append({
                "evidence_id": ev.get("evidence_id"),
                "claim_id": ev.get("claim_id"),
                "source_ref": ev.get("source_ref"),
                "locator": ev.get("locator"),
                "quote_hash": ev.get("quote_hash"),
            })
        else:
            external_unverifiable += 1

    em["by_claim"] = by_claim
    em["by_result"] = by_result
    em["by_sub_question"] = by_sub
    em["orphan_claims"] = orphan
    em["unverified_quotes"] = unverified
    em["verification"] = {
        "total_claims": len(claimed) + len(orphan),
        "verified_local": verified_local,
        "unverified_local": unverified_local,
        "external_unverifiable": external_unverifiable,
        "orphan": len(orphan),
    }


# ---------------------------------------------------------------------------
# assemble：写 result.json + screening.md + plan.md + evidence_map.md
# ---------------------------------------------------------------------------
def compute_status(state: dict) -> str:
    if state.get("input_error"):
        return "input_error"
    sqs = state.get("sub_questions") or []
    recs = state.get("records") or []
    if not sqs and not recs:
        return "insufficient_evidence"
    if not recs:
        return "insufficient_evidence"
    sd = state.get("stop_decision") or {}
    if sd.get("status") == "converged":
        return "ok"
    if sd.get("status") == "handoff":
        return "partial"
    return "partial"


def build_result(state: dict) -> dict:
    result = {
        "case_id": state.get("case_id"),
        "status": state.get("status"),
        "status_notes": state.get("status_notes", ""),
        "research_question": state.get("research_question"),
        "sub_questions": state.get("sub_questions", []),
        "query_plan": state.get("query_plan"),
        "term_expansion": state.get("term_expansion"),
        "stop_decision": state.get("stop_decision"),
        "results": state.get("records", []),
        "screening": state.get("screening"),
        "evidence": state.get("evidence", []),
        "evidence_map": state.get("evidence_map"),
        "sources": state.get("sources"),
        "uncertainty": state.get("uncertainty"),
        "pending_human": state.get("pending_human", []),
        "change_log": state.get("change_log", []),
        "errors": state.get("errors", []),
        "degradations": state.get("degradations", []),
        "limitations": state.get("limitations", []),
        "runtime": state.get("runtime", {}),
    }
    result["status"] = compute_status(state)
    # 回填 output_contract 全部字段
    if state.get("output_contract") is not None:
        fill_contract_missing(result, state["output_contract"])
    return result


def render_screening_md(state: dict) -> str:
    lines = ["# 全量筛选清单", ""]
    lines.append("- case_id: %s" % (state.get("case_id") or "unknown"))
    lines.append("- total_seen: %d" % state["screening"].get("total_seen", 0))
    lines.append("")
    for grp, label in (("included", "纳入"), ("excluded", "排除"), ("uncertain", "待定")):
        lines.append("## %s（%d）" % (label, len(state["screening"].get(grp, []))))
        for e in state["screening"].get(grp, []):
            lines.append("- [%d] %s — %s（证据:%s）" % (
                e.get("record_index"), e.get("title") or "(无标题)", e.get("reason") or "-",
                ",".join(e.get("evidence_ids") or [])))
        lines.append("")
    lines.append("## merged_duplicates")
    for e in state["screening"].get("merged_duplicates", []):
        lines.append("- %s" % json.dumps(e, ensure_ascii=False))
    lines.append("")
    if state["screening"].get("notes"):
        lines.append("备注：%s" % state["screening"]["notes"])
    return "\n".join(lines)


def render_plan_md(state: dict) -> str:
    lines = ["# 拆解 / 查询规划 / 术语扩展 / 停止判断", ""]
    lines.append("## 研究问题")
    lines.append(str(state.get("research_question") or "（未提供）"))
    lines.append("")
    lines.append("## 子问题（六要素）")
    for sq in state.get("sub_questions", []):
        lines.append("- **%s**：%s" % (sq.get("id"), sq.get("question")))
        if sq.get("facets"):
            for k, v in sq["facets"].items():
                lines.append("  - %s：%s" % (k, v))
    lines.append("")
    lines.append("## 查询规划")
    lines.append("```json")
    lines.append(json.dumps(state.get("query_plan"), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 术语扩展")
    lines.append("```json")
    lines.append(json.dumps(state.get("term_expansion"), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 停止判断")
    lines.append("```json")
    lines.append(json.dumps(state.get("stop_decision"), ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def render_evidence_map_md(state: dict) -> str:
    em = state.get("evidence_map", {})
    lines = ["# 证据溯源映射（evidence_map）", ""]
    lines.append("## 核验统计")
    lines.append("```json")
    lines.append(json.dumps(em.get("verification", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 孤儿结论（引用了 CLM 但无证据条目）")
    for c in em.get("orphan_claims", []):
        lines.append("- %s" % c)
    lines.append("")
    lines.append("## 本地未命中 quote（规范化匹配失败）")
    for u in em.get("unverified_quotes", []):
        lines.append("- %s" % json.dumps(u, ensure_ascii=False))
    lines.append("")
    lines.append("## by_claim")
    lines.append("```json")
    lines.append(json.dumps(em.get("by_claim", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def assemble(args, state: dict | None = None) -> int:
    if state is None:
        state = load_state(args.work)
    try:
        secs = getattr(args, "runtime_seconds", None) or 0
        reqs = getattr(args, "runtime_requests", None) or 0
        st = state["runtime"].get("started_at") or state["runtime"].get("start")
        if not st:
            st = now_iso()
        state["runtime"] = {
            "started_at": st,
            "seconds": float(secs),
            "model_requests": int(reqs),
            "assembled_at": now_iso(),
            "working_dir": state.get("input_dir"),
        }
        state["status"] = compute_status(state)
        state["runtime"]["final_status"] = state["status"]
        save_state(args.work, state)

        result = build_result(state)
        write_json(os.path.join(args.output, "result.json"), result)
        write_text(os.path.join(args.output, "screening.md"), render_screening_md(state))
        write_text(os.path.join(args.output, "plan.md"), render_plan_md(state))
        write_text(os.path.join(args.output, "evidence_map.md"), render_evidence_map_md(state))
        state["assemble_error"] = None
        save_state(args.work, state)
        log("assemble: result.json + screening.md + plan.md + evidence_map.md 已写入 %s" % args.output)
        return 0
    except Exception as e:  # 写盘失败也绝不崩溃
        state["assemble_error"] = "%s" % e
        state.setdefault("errors", []).append({"command": "assemble", "issue": str(e), "trace": traceback.format_exc(limit=3)})
        try:
            ensure_dir(args.output)
            write_json(os.path.join(args.output, "result.json"), {
                "case_id": state.get("case_id"), "status": "input_error",
                "status_notes": "assemble 失败，尽力落盘最小合法产物。",
                "errors": state["errors"], "degradations": state["degradations"],
                "pending_human": state.get("pending_human", []),
                "change_log": state.get("change_log", []), "runtime": state.get("runtime", {}),
            })
        except Exception:
            pass
        save_state(args.work, state)
        return 0


def cmd_assemble(args) -> int:
    return assemble(args)


# ---------------------------------------------------------------------------
# selfcheck：逐条 PASS/FAIL（对应评分维度）
# ---------------------------------------------------------------------------
def cmd_selfcheck(args) -> int:
    result = load_json(os.path.join(args.output, "result.json"), default=None)
    checks = []
    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    if result is None:
        chk("result.json 存在且合法", False, "找不到或无法解析 result.json")
    else:
        chk("case_id 存在", bool(result.get("case_id")), str(result.get("case_id")))
        chk("status 合法", result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"),
            str(result.get("status")))
        chk("主产物字段齐全", all(k in result for k in ("sub_questions", "query_plan", "term_expansion",
                                                     "stop_decision", "results", "screening", "evidence")), "")
        sqs = result.get("sub_questions") or []
        recs = result.get("results") or []
        chk("至少 1 条可检索子问题", len(sqs) >= 1, "n=%d" % len(sqs))
        # 六要素
        faceted = sum(1 for s in sqs if isinstance(s.get("facets"), dict))
        chk("子问题含六要素标注", len(sqs) == 0 or faceted == len(sqs), "%d/%d" % (faceted, len(sqs)))
        # 每条 included 结果有证据与理由
        inc = [r for r in recs if r.get("screening") == "included"]
        ev_ok = all((r.get("evidence") or []) for r in inc) if inc else True
        reason_ok = all((r.get("relevance_reason") or "") for r in inc) if inc else True
        chk("每条 included 结果有 relevance_reason", reason_ok, "included=%d" % len(inc))
        chk("每条 included 结果有证据条目", ev_ok, "included=%d" % len(inc))
        quoted = sum(1 for r in recs for e in (r.get("evidence") or []) if str(e.get("quote") or "").strip())
        located = sum(1 for r in recs for e in (r.get("evidence") or []) if e.get("locator"))
        chk("每条结论带逐字 quote", all(str(e.get('quote') or '').strip() for r in recs for e in (r.get('evidence') or [])) if recs else True,
            "quoted=%d" % quoted)
        chk("每条结论带 locator", all(e.get('locator') for r in recs for e in (r.get('evidence') or [])) if recs else True,
            "located=%d" % located)
        em = result.get("evidence_map") or {}
        chk("evidence_map 存在", bool(em), "")
        chk("orphan_claims 记录", isinstance(em.get("orphan_claims"), list), "")
        # 全量子问题覆盖
        uncovered = [s for s in sqs if s.get("status") != "covered"]
        chk("未覆盖子问题列入 coverage_gaps", not uncovered or
            any(s.get("id") in [g.get("sub_question_id") for g in (result.get("uncertainty") or {}).get("coverage_gaps", [])]
                for s in uncovered), "uncovered=%s" % [s.get('id') for s in uncovered])
        chk("screening 排除项有理由", all((e.get("reason") or "") for e in (result.get("screening") or {}).get("excluded", [])), "")
        chk("evidence 非空", bool(result.get("evidence")), "")
        chk("uncertainty 字段存在", isinstance(result.get("uncertainty"), dict), "")
        chk("change_log 存在", isinstance(result.get("change_log"), list), "")

    # 输出报告
    npass = sum(1 for _, ok, _ in checks if ok)
    nfail = len(checks) - npass
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print("  %-6s  %s%s" % (mark, name, ("  | " + detail) if detail else ""))
    print("selfcheck summary: %d PASS, %d FAIL, %d TOTAL" % (npass, nfail, len(checks)))
    return 0  # 只报告，不用失败退出码打断 Harness


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _add_common(parser):
    """共享 --input/--output/--work；子命令上用 SUPPRESS 防止覆盖主解析器默认值。"""
    parser.add_argument("--input", default=argparse.SUPPRESS)
    parser.add_argument("--output", default=argparse.SUPPRESS)
    parser.add_argument("--work", default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py", description="科研问题驱动检索 Skill（证据溯源强化版）")
    _add_common(p)
    p.set_defaults(input=DEFAULT_INPUT, output=DEFAULT_OUTPUT, work=DEFAULT_WORK)
    sub = p.add_subparsers(dest="command")

    sp_run = sub.add_parser("run", help="落盘 partial 骨架并 assemble")
    _add_common(sp_run)
    sp_run.add_argument("--runtime-seconds", type=float, default=0)
    sp_run.add_argument("--runtime-requests", type=int, default=0)

    sp_sp = sub.add_parser("set-plan", help="增量更新拆解/查询规划/术语扩展/停止判断")
    _add_common(sp_sp)
    sp_sp.add_argument("--file")
    sp_sp.add_argument("--inline")

    sp_imp = sub.add_parser("import", help="导入候选记录并重建证据映射")
    _add_common(sp_imp)
    sp_imp.add_argument("--file")
    sp_imp.add_argument("--inline")

    sp_ev = sub.add_parser("evidence", help="追加证据/结论条目")
    _add_common(sp_ev)
    sp_ev.add_argument("--file")
    sp_ev.add_argument("--inline")

    sp_st = sub.add_parser("set-stop", help="更新停止判断")
    _add_common(sp_st)
    sp_st.add_argument("--file")
    sp_st.add_argument("--inline")

    sp_as = sub.add_parser("assemble", help="写 result.json + 可编辑产物")
    _add_common(sp_as)
    sp_as.add_argument("--runtime-seconds", type=float, default=0)
    sp_as.add_argument("--runtime-requests", type=int, default=0)

    sp_ck = sub.add_parser("selfcheck", help="对 result.json 跑自检清单")
    _add_common(sp_ck)
    return p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        cmd = args.command
        try:
            if cmd == "run":
                return cmd_run(args)
            if cmd == "set-plan":
                return cmd_set_plan(args)
            if cmd == "set-stop":
                return cmd_set_stop(args)
            if cmd == "import":
                return cmd_import(args)
            if cmd == "evidence":
                return cmd_evidence(args)
            if cmd == "assemble":
                return cmd_assemble(args)
            if cmd == "selfcheck":
                return cmd_selfcheck(args)
            parser.print_help()
            return 0
        except Exception as e:  # 任何运行时异常都降级为退出 0
            log("ERROR %s: %s" % (cmd, e))
            log(traceback.format_exc(limit=5))
            return 0
    except SystemExit as e:
        # argparse 的 -h/--help 与参数错误都会走这里；保持其退出码但不抛给 Harness
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
        return code


if __name__ == "__main__":
    raise SystemExit(main())