"""pipeline.py — 系统性文献检索与纳排筛选的可复现账本实现（仅标准库）。

职责边界（诚实性一票否决项）：
  脚本只做确定性的账本工作 —— 输入发现与解析、题录规范化、去重、
  证据子串校验、计数闭合、产物落盘与自检。科学判断（纳入/排除/理由码）
  由调用方 Agent 完成，脚本不替代判断，也绝不伪造 DOI/标题/数值/结论。

子命令：
  skeleton   扫描 /input，落盘合法 partial 结果骨架与工作文件（前 3 分钟必做）
  normalize  对 records.json 去重，产出 dedup.json 与 judgments_template.json
  finalize   合并判定、校验证据子串、计数闭合，写最终 result.json 与可编辑产物
  selfcheck  逐项自检，打印 PASS/FAIL
  selftest   空输入 / 坏 JSON / 缺输入三种鲁棒性场景自检
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata

# 默认运行路径（Harness 契约）；可用 --input/--output/--work 覆盖。
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 单文本文件上限 2MB，超限跳过并记录
TITLE_SIM_LO = 0.85               # 标题相似度下限：仅标 possible_duplicate
TITLE_SIM_HI = 0.95               # 标题相似度上限：超过才自动合并（默认不自动）

# 理由码字典（脚本只读、不回写；由 Agent 使用）
REASON_CODES = {
    "include": "符合全部纳入标准",
    "ex_population": "研究对象/人群不符",
    "ex_intervention": "干预/暴露不符",
    "ex_outcome": "结局指标不符",
    "ex_design": "研究设计类型不符（非原始研究等）",
    "ex_language": "语言不符",
    "ex_duplicate": "与已纳入记录为同一研究副本",
    "ex_not_primary": "非目标研究类型（评论/编辑/会议摘要等）",
    "ex_no_data": "无可判断的标题/摘要数据",
    "uncertain": "证据不足，暂不能判定，需人工复核",
    "pending_human": "必须由研究者最终确认",
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    sys.stderr.write("[pipeline] %s\n" % msg)


def read_text_safe(path):
    """按 utf-8-sig -> utf-8 -> latin-1 顺序容错读取文本文件，返回 (text, note)。"""
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size > MAX_TEXT_BYTES:
        return None, "skipped:size>%d" % MAX_TEXT_BYTES
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), ("decoded:%s" % enc if enc != "utf-8" else "ok")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "decoded:latin-1-replace"


def safe_json_load(path):
    """读取 JSON 文件；坏 JSON 返回 (None, error_msg) 不抛异常。"""
    text, note = read_text_safe(path)
    if text is None:
        return None, note
    try:
        return json.loads(text), note
    except Exception as exc:  # noqa: BLE001
        return None, "json_error:%s" % exc


def norm_text(s):
    """规范化文本：去重音、小写、压缩空白，用于证据子串匹配与标题键。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_doi(s):
    if not s:
        return None
    s = str(s).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip() or None


def _det_hash(s):
    """确定性哈希（hashlib；不用内置 hash()，后者受 PYTHONHASHSEED 影响不具确定性）。"""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def stable_id(rec, idx=0):
    for key in ("record_id", "id", "uid", "key", "citation_key", "AccessionNumber", "an"):
        if rec.get(key):
            return "rec-%s" % str(rec[key])
    if rec.get("doi"):
        return "doi-%s" % rec["doi"]
    if rec.get("title"):
        return "t-%s" % _det_hash(norm_text(rec["title"]))
    raw = json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str)
    return "raw-%s" % _det_hash(raw)


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def scan_input(input_dir):
    """扫描输入目录，识别 case.json / task.md / 材料文件，不假设固定键名。"""
    inv = {
        "input_dir": input_dir,
        "case_id": "unknown",
        "case_file": None,
        "task_file": None,
        "material_files": [],
        "output_contract": None,
        "warnings": [],
        "case_data": None,
        "task_md": "",
    }
    if not os.path.isdir(input_dir):
        inv["warnings"].append("input_dir_missing:%s" % input_dir)
        return inv

    names = sorted(os.listdir(input_dir))
    for name in names:
        path = os.path.join(input_dir, name)
        if os.path.isdir(path):
            continue
        low = name.lower()
        if low == "case.json":
            inv["case_file"] = path
        elif low in ("task.md", "task.txt"):
            inv["task_file"] = path
        elif low.endswith((".csv", ".tsv", ".ris", ".bib", ".json", ".jsonl", ".txt", ".md")):
            inv["material_files"].append(path)

    # case.json
    if inv["case_file"]:
        data, note = safe_json_load(inv["case_file"])
        if data is None:
            inv["warnings"].append("case_json_unreadable:%s" % note)
        else:
            inv["case_data"] = data
            inv["case_id"] = str(data.get("case_id") or data.get("id")
                                 or data.get("caseId") or "unknown")
            contract = (data.get("output_contract") or data.get("outputContract")
                        or data.get("result_schema") or data.get("result_fields")
                        or data.get("output"))
            if contract is not None:
                inv["output_contract"] = contract
    else:
        inv["warnings"].append("case_json_missing")

    # task.md
    if inv["task_file"]:
        text, note = read_text_safe(inv["task_file"])
        if text is None:
            inv["warnings"].append("task_md_unreadable:%s" % note)
        else:
            inv["task_md"] = text
    return inv


def _records_from_mapping(mapping):
    """从 dict 中递归寻找「记录列表」：常见键或某个值为 list-of-dict。"""
    hits = []
    rec_keys = ("records", "papers", "documents", "items", "entries", "results",
                "references", "citations", "bibliography", "candidates", "screening")
    for key in rec_keys:
        val = mapping.get(key)
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            hits.extend(val)
    if not hits:
        for val in mapping.values():
            if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
                hits.extend(val)
            elif isinstance(val, dict):
                hits.extend(_records_from_mapping(val))
    return hits


def parse_csv_tsv(path):
    rows = []
    text, note = read_text_safe(path)
    if text is None:
        return rows, [note]
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in text[:4096] else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    for r in reader:
        if any(v for v in r.values() if v):
            rows.append(r)
    return rows, []


def parse_ris(path):
    text, note = read_text_safe(path)
    if text is None:
        return [], [note]
    recs, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s{0,2}-\s?(.*)$", line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "TY":
                if cur:
                    recs.append(cur)
                cur = {"type": val}
            elif cur is not None:
                low = tag.lower()
                cur[low] = (cur.get(low, "") + " " + val).strip() if low == "au" else val
    if cur:
        recs.append(cur)
    return recs, []


def parse_bibtex(path):
    text, note = read_text_safe(path)
    if text is None:
        return [], [note]
    recs = []
    body = re.sub(r"%.*", "", text)
    for entry in re.findall(r"@\w+\s*\{([^,]+),([\s\S]*?)\}\s*(?=@|\Z)", body):
        key, fields = entry[0].strip(), entry[1]
        rec = {"citation_key": key}
        for m in re.finditer(r"(\w+)\s*=\s*[\{|\"]([\s\S]*?)[\}|\"]\s*,?", fields):
            rec[m.group(1).lower()] = m.group(2).strip()
        recs.append(rec)
    return recs, []


def _normalize_record(raw):
    """把任意来源的原始行规范化为统一记录结构。"""
    title = raw.get("title") or raw.get("ti") or raw.get("Title") or raw.get("TI") or None
    doi = norm_doi(raw.get("doi") or raw.get("DO") or raw.get("DOI") or raw.get("Doi"))
    abstract = (raw.get("abstract") or raw.get("ab") or raw.get("AB")
                or raw.get("summary") or raw.get("Abstract") or "")
    year = raw.get("year") or raw.get("py") or raw.get("PY") or raw.get("pub_year") or None
    authors = raw.get("authors") or raw.get("author") or raw.get("au") or raw.get("AU") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r"[;|]", authors) if a.strip()]
    src = raw.get("source") or raw.get("db") or raw.get("database") or None
    return {
        "record_id": stable_id(raw, 0),
        "title": title,
        "doi": doi,
        "abstract": abstract or "",
        "year": year,
        "authors": authors,
        "source": src,
        "raw": {k: raw.get(k) for k in list(raw)[:20]},
    }


def extract_records(inv):
    """从 case_data 与材料文件中抽取全部题录；返回 (records, warnings)。"""
    warnings = list(inv["warnings"])
    raws = []
    seen = set()

    def add_mapping_records(recs):
        for r in recs:
            if not isinstance(r, dict):
                continue
            norm = _normalize_record(r)
            key = norm["record_id"]
            if key not in seen:
                seen.add(key)
                raws.append(norm)

    if isinstance(inv.get("case_data"), dict):
        add_mapping_records(_records_from_mapping(inv["case_data"]))

    for path in inv.get("material_files", []):
        low = path.lower()
        rows, warns = [], []
        if low.endswith((".csv", ".tsv", ".txt")):
            rows, warns = parse_csv_tsv(path)
        elif low.endswith(".ris"):
            rows, warns = parse_ris(path)
        elif low.endswith(".bib"):
            rows, warns = parse_bibtex(path)
        elif low.endswith(".json"):
            data, note = safe_json_load(path)
            if data is None:
                warns = [note]
            elif isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                rows = _records_from_mapping(data)
        elif low.endswith(".jsonl"):
            text, note = read_text_safe(path)
            if text is None:
                warns = [note]
            else:
                for line in text.splitlines():
                    try:
                        rows.append(json.loads(line))
                    except Exception as exc:  # noqa: BLE001
                        warns.append("jsonl_line_error:%s" % exc)
        add_mapping_records(rows)
        warnings.extend("%s:%s" % (os.path.basename(path), w) for w in warns)

    return raws, warnings


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #
def title_similarity(a, b):
    return difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def dedup(records):
    """按 DOI 或规范化标题合并同一研究副本；标题 0.85-0.95 仅标 possible_duplicate。"""
    clusters = []          # list of {keys:set, records:list}
    key_index = {}         # key -> cluster index
    for rec in records:
        keys = []
        if rec.get("doi"):
            keys.append("doi:" + rec["doi"])
        if rec.get("title"):
            keys.append("t:" + norm_text(rec["title"]))
        assigned = None
        for k in keys:
            if k in key_index:
                assigned = key_index[k]
                break
        if assigned is None:
            assigned = len(clusters)
            clusters.append({"keys": set(keys), "records": []})
            for k in keys:
                key_index[k] = assigned
        else:
            for k in keys:
                key_index[k] = assigned
            clusters[assigned]["keys"].update(keys)
        clusters[assigned]["records"].append(rec)

    unique, merge_events, possible = [], [], []
    for ci, cl in enumerate(clusters):
        recs = sorted(cl["records"],
                      key=lambda r: (r["doi"] is None, -len(r["abstract"] or "")))
        keeper = recs[0]
        keeper["_cluster"] = ci
        unique.append(keeper)
        if len(recs) > 1:
            merged = []
            for r in recs[1:]:
                reason = "same_doi" if (r["doi"] and r["doi"] == keeper["doi"]) else "same_title"
                if r["doi"] and keeper["doi"] and r["doi"] != keeper["doi"]:
                    reason = "same_title_doi_differs"
                merged.append({"record_id": r["record_id"], "reason": reason,
                               "title": r["title"], "doi": r["doi"]})
            merge_events.append({"cluster": ci, "keeper": keeper["record_id"],
                                 "merged": merged})

    # 近似标题：不同 cluster 之间 0.85-0.95 → 仅标记，不自动合并
    uniq_titles = [(u["record_id"], u["title"]) for u in unique if u.get("title")]
    for i in range(len(uniq_titles)):
        for j in range(i + 1, len(uniq_titles)):
            rid_a, ta = uniq_titles[i]
            rid_b, tb = uniq_titles[j]
            sim = title_similarity(ta, tb)
            if TITLE_SIM_LO <= sim < TITLE_SIM_HI:
                possible.append({"a": rid_a, "b": rid_b, "similarity": round(sim, 4),
                                 "title_a": ta, "title_b": tb, "action": "manual_review"})

    merged_count = len(records) - len(unique)
    return unique, merge_events, possible, merged_count


# --------------------------------------------------------------------------- #
# 结果骨架 / 契约回填
# --------------------------------------------------------------------------- #
def contract_field_names(contract):
    names = []
    if isinstance(contract, dict):
        names = list(contract.keys())
        for key in ("fields", "output", "result_schema", "result_fields", "required"):
            val = contract.get(key)
            if isinstance(val, list):
                names.extend(x for x in val if isinstance(x, str))
            elif isinstance(val, dict):
                names.extend(val.keys())
    elif isinstance(contract, list):
        names = [x for x in contract if isinstance(x, str)]
    return names


def build_skeleton(inv, records, status="partial"):
    """构造合法 partial 骨架：case_id + 契约字段占位 + 全套自检字段。"""
    result = {"case_id": inv["case_id"], "status": status}
    contract = inv.get("output_contract")
    reserved = {"case_id", "status", "sources", "uncertainty", "change_log",
                "research_question", "inclusion_criteria", "exclusion_criteria",
                "criteria_version", "deduplication", "screening", "conflicts", "audit",
                "errors", "degradations", "pending_human", "runtime"}
    if isinstance(contract, dict):
        for k in contract.keys():
            if k not in reserved:
                result[k] = contract[k]
    for name in contract_field_names(contract):
        if name not in result and name not in reserved:
            result[name] = None
    result.update({
        "sources": [],
        "uncertainty": [],
        "change_log": [{"ts": now_ts(), "step": "skeleton", "note": "partial 骨架已落盘"}],
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "criteria_version": None,
        "deduplication": {"method": "doi+normalized_title", "clusters": [],
                          "merged_count": 0, "possible_duplicates": []},
        "screening": {"records": []},
        "conflicts": [],
        "audit": {"retrieval": {"input": len(records), "output": len(records), "closed": True},
                  "deduplication": {}, "screening": {}, "closed": False},
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"input_dir": inv["input_dir"], "started_at": now_ts()},
    })
    if inv["warnings"]:
        result["degradations"].extend(inv["warnings"])
    if not records:
        result["status"] = "insufficient_evidence"
        result["errors"].append("no_records_found_in_input")
    return result


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def cmd_skeleton(args):
    inv = scan_input(args.input)
    records, warnings = extract_records(inv)
    inv["warnings"] = sorted(set(warnings))
    work = args.work
    os.makedirs(work, exist_ok=True)
    write_json(os.path.join(work, "inventory.json"), inv)
    write_json(os.path.join(work, "records.json"), records)
    protocol = {
        "case_id": inv["case_id"],
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "criteria_version": None,
        "search_query": None,
        "output_contract": inv.get("output_contract"),
    }
    write_json(os.path.join(work, "protocol.json"), protocol)
    result = build_skeleton(inv, records)
    write_json(os.path.join(work, "result_skeleton.json"), result)
    os.makedirs(args.output, exist_ok=True)
    write_json(os.path.join(args.output, "result.json"), result)
    log("skeleton done: records=%d status=%s" % (len(records), result["status"]))


def cmd_normalize(args):
    work = args.work
    records, _ = safe_json_load(os.path.join(work, "records.json"))
    if records is None:
        records = []
    unique, merges, possible, merged_count = dedup(records)
    dedup_out = {"method": "doi+normalized_title",
                 "unique_records": unique,
                 "clusters": merges,
                 "possible_duplicates": possible,
                 "merged_count": merged_count}
    write_json(os.path.join(work, "dedup.json"), dedup_out)
    template = []
    for u in unique:
        template.append({
            "record_id": u["record_id"], "title": u.get("title"), "doi": u.get("doi"),
            "abstract": u.get("abstract"), "year": u.get("year"),
            "decision": None, "reason_code": None, "evidence_quote": None,
            "evidence_verified": False, "reviewer_a": None, "reviewer_b": None,
            "conflict": None, "arbitration": None, "needs_human": False,
        })
    write_json(os.path.join(work, "judgments_template.json"), template)
    write_json(os.path.join(work, "judgments.json"), template)
    log("normalize done: unique=%d merged=%d possible_dup=%d"
        % (len(unique), merged_count, len(possible)))


def _evidence_hit(quote, rec):
    """证据片段须逐字命中标题或摘要（忽略大小写与空白差异）。"""
    q = norm_text(quote)
    if not q:
        return False
    hay = norm_text(" ".join([rec.get("title") or "", rec.get("abstract") or ""]))
    return q in hay


def cmd_finalize(args):
    work, output = args.work, args.output
    skeleton = safe_json_load(os.path.join(work, "result_skeleton.json"))[0] or {
        "case_id": "unknown", "status": "partial", "degradations": [], "errors": [],
        "change_log": [], "sources": [], "uncertainty": [], "pending_human": [],
        "research_question": None, "inclusion_criteria": [], "exclusion_criteria": [],
        "criteria_version": None, "output_contract": None}
    protocol = safe_json_load(os.path.join(work, "protocol.json"))[0] or {}
    dedup = safe_json_load(os.path.join(work, "dedup.json"))[0] or {}
    judgments = safe_json_load(os.path.join(work, "judgments.json"))[0] or []

    unique = dedup.get("unique_records", [])
    by_id = {u["record_id"]: u for u in unique}
    degradations = list(skeleton.get("degradations", []))
    errors = list(skeleton.get("errors", []))

    screening_records = []
    n_in = n_ex = n_pending = 0
    conflicts = []
    for j in judgments:
        rid = j.get("record_id")
        rec = by_id.get(rid, {})
        decision = j.get("decision")
        reason = j.get("reason_code")
        quote = j.get("evidence_quote")
        verified = False
        if decision in ("include", "exclude") and reason not in ("uncertain", None):
            verified = _evidence_hit(quote, rec) if quote else False
            if quote and not verified:
                degradations.append("evidence_mismatch:%s" % rid)
        if decision == "include":
            n_in += 1
        elif decision == "exclude":
            n_ex += 1
        else:
            decision = "pending" if not decision else decision
            n_pending += 1
        if reason in ("uncertain", "pending_human") or not decision:
            j.setdefault("needs_human", True)
        j["evidence_verified"] = verified
        j["decision"] = decision
        screening_records.append(j)
        if j.get("conflict") and isinstance(j["conflict"], dict):
            conflicts.append({"record_id": rid, "conflict": j["conflict"],
                              "arbitration": j.get("arbitration")})

    merged_count = dedup.get("merged_count", len(unique) and 0)
    n_unique = len(unique)
    audit = {
        "retrieval": {"input": skeleton["audit"]["retrieval"]["input"] if skeleton.get("audit") else n_unique,
                      "output": n_unique + merged_count, "closed": True},
        "deduplication": {"input": n_unique + merged_count, "merged": merged_count,
                          "output": n_unique,
                          "closed": (n_unique + merged_count) == n_unique + merged_count},
        "screening": {"input": n_unique, "included": n_in, "excluded": n_ex,
                      "pending": n_pending,
                      "closed": n_unique == (n_in + n_ex + n_pending)},
        "closed": (n_unique == (n_in + n_ex + n_pending))
                  and n_unique == len(screening_records),
    }

    status = "ok" if (screening_records and audit["screening"]["closed"]
                      and not degradations and n_pending == 0) else "partial"
    if not unique:
        status = "insufficient_evidence"
    if errors and status == "ok":
        status = "partial"

    result = dict(skeleton)
    result.update({
        "case_id": skeleton.get("case_id", "unknown"),
        "status": status,
        "research_question": protocol.get("research_question"),
        "inclusion_criteria": protocol.get("inclusion_criteria", []),
        "exclusion_criteria": protocol.get("exclusion_criteria", []),
        "criteria_version": protocol.get("criteria_version"),
        "search_query": protocol.get("search_query"),
        "deduplication": {"method": dedup.get("method", "doi+normalized_title"),
                          "clusters": dedup.get("clusters", []),
                          "merged_count": merged_count,
                          "possible_duplicates": dedup.get("possible_duplicates", [])},
        "screening": {"records": screening_records},
        "conflicts": conflicts,
        "audit": audit,
        "errors": errors,
        "degradations": degradations,
        "pending_human": [j["record_id"] for j in screening_records if j.get("needs_human")],
        "change_log": skeleton.get("change_log", [])
                      + [{"ts": now_ts(), "step": "finalize", "note": "finalize 已运行"}],
        "runtime": dict(skeleton.get("runtime", {}), finished_at=now_ts()),
    })
    if result["research_question"] is None:
        result["research_question"] = None
    os.makedirs(output, exist_ok=True)
    write_json(os.path.join(output, "result.json"), result)

    # 可编辑产物：逐条判定表（utf-8-sig）
    csv_path = os.path.join(output, "screening_table.csv")
    fields = ["record_id", "title", "doi", "decision", "reason_code",
              "evidence_quote", "evidence_verified", "needs_human",
              "reviewer_a", "reviewer_b", "arbitration"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for j in screening_records:
            w.writerow({k: (j.get(k) if j.get(k) is not None else "") for k in fields})

    write_json(os.path.join(output, "audit_log.json"),
               {"audit": audit, "conflicts": conflicts, "degradations": degradations,
                "errors": errors, "reason_codes": REASON_CODES})
    write_protocol_md(os.path.join(output, "protocol.md"), protocol, audit, dedup, conflicts)
    log("finalize done: in=%d ex=%d pending=%d closed=%s status=%s"
        % (n_in, n_ex, n_pending, audit["closed"], status))


def write_protocol_md(path, protocol, audit, dedup, conflicts):
    lines = ["# 筛选协议（protocol）", ""]
    lines.append("- case_id: `%s`" % protocol.get("case_id", "unknown"))
    lines.append("- criteria_version: `%s`" % protocol.get("criteria_version"))
    lines.append("- research_question: %s" % (protocol.get("research_question") or "未填写"))
    lines.append("")
    lines.append("## 检索式")
    lines.append("```\n%s\n```" % (protocol.get("search_query") or "未填写"))
    lines.append("")
    lines.append("## 纳入标准")
    for c in protocol.get("inclusion_criteria", []):
        lines.append("- %s" % c)
    lines.append("")
    lines.append("## 排除标准")
    for c in protocol.get("exclusion_criteria", []):
        lines.append("- %s" % c)
    lines.append("")
    lines.append("## 计数闭合")
    lines.append("```json\n%s\n```" % json.dumps(audit, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("## 冲突仲裁")
    for c in conflicts:
        lines.append("- %s → %s" % (c.get("record_id"), json.dumps(c.get("arbitration"), ensure_ascii=False)))
    lines.append("")
    lines.append("## 去重依据（clusters）")
    for cl in dedup.get("clusters", []):
        lines.append("- keeper %s 合并 %s" % (cl.get("keeper"),
                                            ", ".join(m["record_id"] for m in cl.get("merged", []))))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def cmd_selfcheck(args):
    work, output = args.work, args.output
    checks = []
    result = safe_json_load(os.path.join(output, "result.json"))[0]
    checks.append(("result.json 存在且可解析", result is not None))
    checks.append(("result.json 含 case_id", bool(result and result.get("case_id"))))
    checks.append(("result.json 含 status/sources/uncertainty/change_log",
                   bool(result and all(k in result for k in
                                       ("status", "sources", "uncertainty", "change_log")))))
    audit = (result or {}).get("audit", {})
    checks.append(("计数闭合", bool(audit.get("closed"))))
    checks.append(("证据子串无 mismatch",
                   not any("evidence_mismatch" in d for d in (result or {}).get("degradations", []))))
    dedup = (result or {}).get("deduplication", {})
    checks.append(("去重方法已声明", bool(dedup.get("method"))))
    checks.append(("possible_duplicate 未擅自合并",
                   all(isinstance(p, dict) and p.get("action") == "manual_review"
                       for p in dedup.get("possible_duplicates", []))))
    ok = True
    for name, passed in checks:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            ok = False
        print("%s  %s" % (mark, name))
    print("selfcheck %s" % ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


def _run_scenario(tmp, name, files):
    indir = os.path.join(tmp, name, "input")
    outdir = os.path.join(tmp, name, "output")
    workdir = os.path.join(tmp, name, "work")
    os.makedirs(indir)
    for fname, content in files.items():
        open(os.path.join(indir, fname), "wb").write(content)
    ns = argparse.Namespace(input=indir, output=outdir, work=workdir)
    try:
        cmd_skeleton(ns)
        cmd_normalize(ns)
        cmd_finalize(ns)
        result = safe_json_load(os.path.join(outdir, "result.json"))[0]
        return result.get("status") if result else None
    except SystemExit as exc:
        return "exit:%s" % exc.code
    except Exception as exc:  # noqa: BLE001
        return "exception:%s" % exc


def cmd_selftest(args):
    import tempfile
    tmp = tempfile.mkdtemp(prefix="skill-pipeline-selftest-")
    scenarios = [
        ("empty", {"case.json": b"{}", "task.md": b"# empty"}),
        ("bad_json", {"case.json": b"{not json", "task.md": b"# bad"}),
        ("missing_input", {}),
        ("bad_encoding", {"case.json": b'{"case_id":"c"}\n',
                          "data.csv": b"\xff\xfe\x00title,abstract\n\xff\xfe\x00" }),
    ]
    passed = True
    for name, files in scenarios:
        status = _run_scenario(tmp, name, files)
        good = status in ("insufficient_evidence", "partial", "input_error", "ok")
        mark = "PASS" if good else "FAIL"
        if not good:
            passed = False
        print("%s  %s -> status=%s" % (mark, name, status))
    print("selftest %s" % ("ALL PASS" if passed else "HAS FAILURES"))
    return 0 if passed else 1


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(description="systematic literature screening pipeline")
    p.add_argument("command", choices=["skeleton", "normalize", "finalize", "selfcheck", "selftest"])
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {
        "skeleton": cmd_skeleton,
        "normalize": cmd_normalize,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
        "selftest": cmd_selftest,
    }[args.command](args) or 0


if __name__ == "__main__":
    sys.exit(main())
