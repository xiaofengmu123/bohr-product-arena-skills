#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的确定性核心实现（仅 Python 标准库）。

架构取向（与单文件流水线明显不同）：
1. 中间表示（IR）：/app/work 下存放小而规范的 JSON/JSONL 片段
   （case / inventory / screening / evidence / themes / claims /
    conflicts_gaps / meta），是所有人工产物与 result.json 的单一事实源。
2. 单向投影：review.md、themes.md、conflicts_and_gaps.md、
   screening_log.json、evidence_table.jsonl 全部由 IR 在一个 render 阶段
   确定性生成，正文 [E#] 标记由渲染器按 claims 的 evidence_ids 自动追加，
   因此"写出来的句子"不可能引用一条不存在的证据行。
3. 校验顺序固定为三关：contract 解析 → 片段容错装载 → 交叉引用
   （claim → evidence → source），在写出前拦截脱节引用。

设计目标：空输入、坏 JSON、坏编码、缺 pdftotext、缺字段都不崩溃；
给定相同片段时，render 输出逐字节一致。
"""

import hashlib
import json
import os
import shutil
import subprocess

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_STRENGTH = {"established", "suggestive", "disputed", "not_reported"}
ALLOWED_DECISIONS = {"include", "exclude", "abstract_only"}
MAX_FILE_BYTES = 2 * 1024 * 1024

PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json", "challenge.json"}
SCHEMA_META_KEYS = {
    "type", "title", "description", "$schema", "$id", "additionalProperties",
    "definitions", "$ref", "required", "properties", "items", "enum", "default",
}

DEFAULT_CONTRACT_FIELDS = [
    ("research_question", None, "string"),
    ("screening_criteria", None, "object"),
    ("screening_log", [], "array"),
    ("evidence_table", [], "array"),
    ("themes", [], "array"),
    ("review_draft", None, "string"),
    ("conflicts_and_gaps", [], "array"),
    ("references", [], "array"),
]

FIELD_ALIASES = {
    "review": ["review", "reviewdraft", "draft", "synthesis", "summary", "manuscript"],
    "screening": ["screening", "screeninglog", "inclusionexclusion", "prisma", "screeningdecisions"],
    "evidence": ["evidence", "evidencetable", "evidencematrix", "matrix", "extraction", "extracteddata"],
    "themes": ["themes", "theme", "topics", "topic", "clusters", "themeclusters"],
    "conflicts": ["conflicts", "conflictsandgaps", "gapsandconflicts", "disagreements"],
    "references": ["references", "sources", "bibliography", "includedstudies", "studies", "papers"],
}


def now_ts():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[lit-review] " + msg, flush=True)


def read_text(path):
    """utf-8-sig / utf-8 / latin-1 顺序容错读取，绝不抛异常。"""
    last = "未知错误"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc, None
        except UnicodeDecodeError as e:
            last = str(e)
        except Exception as e:
            return None, None, str(e)
    return None, None, last


def read_json(path):
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取: " + (err or "未知错误")]
    try:
        return json.loads(text), None
    except Exception as e:
        return None, ["JSON 解析失败: " + str(e)]


def write_json(path, data):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def type_hint(spec):
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            return t
        if isinstance(t, list):
            return ",".join(str(x) for x in t)
        if "properties" in spec:
            return "object"
        if "items" in spec:
            return "array"
        return "unknown"
    if isinstance(spec, list):
        return "enum"
    if isinstance(spec, str):
        return "string"
    return "unknown"


def default_for_spec(spec):
    """据 schema/type 提示给数组字段 []、对象字段 {}，其余 null。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            if t == "array":
                return []
            if t == "object":
                return {}
            if t in ("integer", "number", "boolean"):
                return None
        if isinstance(t, list):
            tl = [str(x).lower() for x in t]
            if "null" in tl:
                return None
            if "array" in tl:
                return []
            if "object" in tl:
                return {}
        if "items" in spec and spec.get("type") == "array":
            return []
        if "properties" in spec:
            return {}
        d = spec.get("default")
        if d is not None:
            return d
        return None
    if isinstance(spec, (list, tuple)):
        tl = [str(x).lower() for x in spec]
        if "null" in tl:
            return None
        if "array" in tl:
            return []
        if "object" in tl:
            return {}
        return None
    return None


def find_output_contract(node):
    """递归查找 output_contract / output_schema，找不到返回 None。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("output_contract", "outputcontract", "output_schema", "outputschema"):
                return v
        for v in node.values():
            r = find_output_contract(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_output_contract(item)
            if r is not None:
                return r
    return None


def parse_contract(node):
    """归一化 output_contract 为 [(name, default, type_hint), ...] 及原文。"""
    if node is None:
        return DEFAULT_CONTRACT_FIELDS, None
    raw = node
    fields = []
    props = None
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            props = node["properties"]
        elif isinstance(node.get("fields"), dict):
            props = node["fields"]
        elif isinstance(node.get("fields"), list):
            props = node["fields"]
    if props is None:
        props = node
    if isinstance(props, dict):
        for name, spec in props.items():
            if name in SCHEMA_META_KEYS:
                continue
            fields.append((name, default_for_spec(spec), type_hint(spec)))
    elif isinstance(props, list):
        for item in props:
            if isinstance(item, str):
                fields.append((item, None, "string"))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    fields.append((nm, default_for_spec(item), type_hint(item)))
    if not fields:
        fields = list(DEFAULT_CONTRACT_FIELDS)
    return fields, raw


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if not isinstance(case, dict):
        return None
    for k in ("research_question", "question", "query", "prompt", "title", "objective", "goal"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    t = case.get("task")
    if isinstance(t, dict):
        for k in ("research_question", "question", "prompt", "description", "title", "objective", "goal"):
            v = t.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def read_task_question(input_dir):
    """case.json 未给研究问题时，从 task.md 首个标题做弱回退。"""
    for name in ("task.md", "TASK.md", "Task.md"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            text, _enc, _err = read_text(p)
            if text:
                for line in text.splitlines():
                    s = line.strip()
                    if s.startswith("# "):
                        return s.lstrip("# ").strip()
                    if s and not s.startswith(("-", ">", "|", "`")):
                        return s[:200]
    return None


def find_case_file(input_dir):
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        return p
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for f in sorted(files):
                if f.lower() == "case.json":
                    return os.path.join(root, f)
    return None


def load_case(input_dir):
    case_path = find_case_file(input_dir)
    case = {}
    errs = []
    if case_path:
        case, errs = read_json(case_path)
        if case is None:
            case = {}
    return case_path, case, errs


def discover_materials(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(files):
            if f.lower() in META_NAMES:
                continue
            if f.startswith("."):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in MATERIAL_EXTS:
                out.append(os.path.join(root, f))
    return out


def resolve_ref(ref, input_dir):
    if not isinstance(ref, str) or not ref.strip():
        return None
    ref = ref.strip()
    if ref.startswith(("http://", "https://")):
        return None
    if os.path.isabs(ref):
        return ref
    return os.path.join(input_dir, ref)


def resolve_material_refs(case, input_dir):
    if not isinstance(case, dict):
        return []
    out = []
    for key in ("materials", "material_paths", "files", "documents", "sources", "inputs", "attachments"):
        v = case.get(key)
        if isinstance(v, str):
            r = resolve_ref(v, input_dir)
            if r:
                out.append(r)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    r = resolve_ref(item, input_dir)
                    if r:
                        out.append(r)
                elif isinstance(item, dict):
                    for k2 in ("path", "file", "filename", "url"):
                        r = resolve_ref(item.get(k2), input_dir)
                        if r:
                            out.append(r)
        elif isinstance(v, dict):
            for k2 in ("path", "file", "filename", "url"):
                r = resolve_ref(v.get(k2), input_dir)
                if r:
                    out.append(r)
    seen, res = set(), []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p):
            res.append(p)
    return res


def extract_pdf(pdf_path, out_txt):
    tool = shutil.which("pdftotext")
    if not tool:
        return False, "pdftotext 不可用"
    try:
        os.makedirs(os.path.dirname(out_txt), exist_ok=True)
        proc = subprocess.run(
            [tool, "-layout", pdf_path, out_txt],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60,
        )
        if proc.returncode != 0 or not os.path.isfile(out_txt) or os.path.getsize(out_txt) == 0:
            msg = proc.stderr.decode("utf-8", "replace")[:200] or "无文本输出"
            return False, msg
        return True, "ok"
    except Exception as e:
        return False, str(e)


def scaffold(input_dir, output_dir, work_dir):
    """Phase 0：发现输入、解析契约、去重、抽 PDF、写 IR 片段 + partial result.json。"""
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case_path, case, case_errs = load_case(input_dir)
    case_id = extract_case_id(case, case_path)
    question = extract_question(case) or read_task_question(input_dir)
    contract_node = find_output_contract(case)
    fields, contract_raw = parse_contract(contract_node)

    errors = list(case_errs or [])
    degradations = []
    if case_path and case_errs:
        errors.append("case.json 不可解析，已按空 case 继续")

    paths = discover_materials(input_dir)
    for p in resolve_material_refs(case, input_dir):
        if p not in paths:
            paths.append(p)

    sources, dedup = [], []
    seen_sha = {}
    n = 0
    for p in paths:
        if not os.path.isfile(p):
            errors.append("材料路径不存在: " + p)
            continue
        if os.path.getsize(p) > MAX_FILE_BYTES:
            degradations.append("超大文件跳过(>2MB): " + p)
            continue
        n += 1
        sid = "S%02d" % n
        ext = os.path.splitext(p)[1].lower()
        kind = "pdf" if ext in PDF_EXTS else ("record" if ext in RECORD_EXTS else "txt")
        h = sha256_file(p)
        dup_of = None
        if h and h in seen_sha:
            dup_of = seen_sha[h]
            dedup.append({"duplicate": sid, "of": dup_of, "basis": "same sha256"})
        elif h:
            seen_sha[h] = sid
        extracted = None
        fulltext_status = "not_extracted"
        if kind == "pdf":
            out_txt = os.path.join(work_dir, "extracted", sid + ".txt")
            ok, msg = extract_pdf(p, out_txt)
            if ok:
                extracted = out_txt
                fulltext_status = "fulltext"
            else:
                degradations.append("PDF 抽取失败: %s (%s)" % (os.path.basename(p), msg))
        elif kind == "txt":
            extracted = p
            fulltext_status = "fulltext"
        sources.append({
            "source_id": sid, "path": p, "filename": os.path.basename(p),
            "kind": kind, "sha256": h, "title": None, "doi": None,
            "meta_status": "missing", "fulltext_status": fulltext_status,
            "extracted_path": extracted, "dedup_of": dup_of, "notes": "",
        })

    case_frag = {
        "case_id": case_id,
        "research_question": question,
        "contract_fields": [{"name": n, "default": d, "type_hint": t} for n, d, t in fields],
        "contract_raw": contract_raw,
    }
    inventory_frag = {"sources": sources, "dedup": dedup}
    screening_frag = {"criteria": {}, "decisions": []}
    themes_frag = {"themes": []}
    claims_frag = {"paragraphs": []}
    conflicts_frag = {"conflicts": [], "gaps": []}
    status = "insufficient_evidence" if not sources else "partial"
    meta_frag = {
        "status": status,
        "uncertainty": [],
        "pending_human": [],
        "degradations": degradations,
        "errors": errors,
        "change_log": [{"ts": now_ts(), "event": "scaffold 写入 partial 结果骨架（%d 条材料）" % len(sources)}],
    }

    write_json(os.path.join(work_dir, "case.json"), case_frag)
    write_json(os.path.join(work_dir, "inventory.json"), inventory_frag)
    write_json(os.path.join(work_dir, "screening.json"), screening_frag)
    write_jsonl(os.path.join(work_dir, "evidence.jsonl"), [])
    write_json(os.path.join(work_dir, "themes.json"), themes_frag)
    write_json(os.path.join(work_dir, "claims.json"), claims_frag)
    write_json(os.path.join(work_dir, "conflicts_gaps.json"), conflicts_frag)
    write_json(os.path.join(work_dir, "meta.json"), meta_frag)

    result, problems = render_all(work_dir, output_dir)
    return result, problems


def load_json_fragment(work_dir, name):
    p = os.path.join(work_dir, name)
    if not os.path.isfile(p):
        return None, ["missing fragment: " + name]
    data, err = read_json(p)
    if data is None:
        return None, ["fragment 解析失败 " + name + ": " + (err or "")]
    return data, []


def load_evidence(work_dir):
    p = os.path.join(work_dir, "evidence.jsonl")
    rows, errs = [], []
    if not os.path.isfile(p):
        return rows, ["missing evidence.jsonl"]
    try:
        with open(p, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception as e:
                    errs.append("evidence.jsonl 第%d行解析失败: %s" % (i, e))
    except Exception as e:
        errs.append("读取 evidence.jsonl 失败: %s" % e)
    return rows, errs


def assemble_ir(work_dir):
    problems = []
    case, e = load_json_fragment(work_dir, "case.json")
    problems += e
    inventory, e = load_json_fragment(work_dir, "inventory.json")
    problems += e
    screening, e = load_json_fragment(work_dir, "screening.json")
    problems += e
    themes, e = load_json_fragment(work_dir, "themes.json")
    problems += e
    claims, e = load_json_fragment(work_dir, "claims.json")
    problems += e
    conflicts_gaps, e = load_json_fragment(work_dir, "conflicts_gaps.json")
    problems += e
    meta, e = load_json_fragment(work_dir, "meta.json")
    problems += e
    evidence, e = load_evidence(work_dir)
    problems += e

    ir = {
        "case": case or {},
        "inventory": inventory or {"sources": [], "dedup": []},
        "screening": screening or {"criteria": {}, "decisions": []},
        "themes": themes or {"themes": []},
        "claims": claims or {"paragraphs": []},
        "conflicts_gaps": conflicts_gaps or {"conflicts": [], "gaps": []},
        "meta": meta or {},
        "evidence": evidence,
    }
    return ir, problems


def validate_crossref(ir):
    problems = []
    src_ids = {s.get("source_id") for s in ir["inventory"].get("sources", [])}
    ev_ids = {}
    for row in ir["evidence"]:
        eid = row.get("evidence_id")
        if not eid:
            problems.append("evidence 行缺 evidence_id")
            continue
        if eid in ev_ids:
            problems.append("evidence_id 重复: " + eid)
        ev_ids[eid] = row
        sid = row.get("source_id")
        if sid and sid not in src_ids:
            problems.append("evidence %s 的 source_id %s 不在 inventory" % (eid, sid))
        if row.get("strength") not in ALLOWED_STRENGTH:
            problems.append("evidence %s strength 非法: %r" % (eid, row.get("strength")))
        if not row.get("quote"):
            problems.append("evidence %s 缺逐字 quote" % eid)
        if not row.get("locator"):
            problems.append("evidence %s 缺 locator" % eid)
    for pg in ir["claims"].get("paragraphs", []):
        for c in pg.get("claims", []):
            for eid in c.get("evidence_ids", []):
                if eid not in ev_ids:
                    problems.append("claim %s 引用缺失 evidence %s" % (c.get("claim_id", "?"), eid))
    for d in ir["screening"].get("decisions", []):
        if d.get("source_id") and d["source_id"] not in src_ids:
            problems.append("screening 决定引用未知 source_id: " + d["source_id"])
        if d.get("decision") not in ALLOWED_DECISIONS:
            problems.append("screening 决定 decision 非法: %r" % d.get("decision"))
    for t in ir["themes"].get("themes", []):
        for s in t.get("supporting_sources", []):
            if s not in src_ids:
                problems.append("theme %s 引用未知 source %s" % (t.get("theme_id", "?"), s))
    return problems, ev_ids, src_ids


def escape_md(s):
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_review(ir):
    case = ir["case"]
    q = case.get("research_question") or "（未给出研究问题）"
    lines = ["# 文献综述（草稿）", "", "研究问题：" + q, ""]
    lines.append(
        "> 本稿为可复核草稿；正文 `[E#]` 指向 evidence_table.jsonl 的 evidence_id，"
        "每行含 source_id、locator 与逐字 quote。")
    lines.append("")
    for pg in ir["claims"].get("paragraphs", []):
        heading = pg.get("heading")
        if heading:
            lines.append("## " + heading)
            lines.append("")
        for c in pg.get("claims", []):
            text = (c.get("text") or "").strip()
            if not text:
                continue
            markers = "".join("[%s]" % eid for eid in c.get("evidence_ids", []))
            lines.append("- %s %s" % (text, markers))
            lines.append("")
    lines.append("## 来源与证据索引")
    lines.append("")
    lines.append("| evidence_id | source_id | locator | claim | strength |")
    lines.append("|---|---|---|---|---|")
    for row in ir["evidence"]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            row.get("evidence_id", ""), row.get("source_id", ""),
            escape_md(row.get("locator", "")), escape_md(row.get("claim", "")),
            row.get("strength", "")))
    return "\n".join(lines) + "\n"


def render_themes(ir):
    lines = ["# 主题归纳", "", "主题从证据聚类（对象/方法/结论分歧）生成，非通用框架。", ""]
    for t in ir["themes"].get("themes", []):
        lines.append("## %s %s" % (t.get("theme_id", ""), t.get("title", "")))
        lines.append("")
        lines.append("支撑文献：" + ", ".join(t.get("supporting_sources", [])))
        if t.get("summary"):
            lines.append("")
            lines.append(t["summary"])
        for st in t.get("subthemes", []):
            lines.append("- 子主题：%s（%s）" % (st.get("title", ""), ", ".join(st.get("sources", []))))
        if t.get("boundary"):
            lines.append("")
            lines.append("边界：" + t["boundary"])
        lines.append("")
    return "\n".join(lines) + "\n"


def render_conflicts_gaps(ir):
    lines = ["# 冲突证据与研究空白", "", "## 结论冲突（并列呈现）", ""]
    for x in ir["conflicts_gaps"].get("conflicts", []):
        lines.append("### %s（%s）" % (x.get("conflict_id", ""), x.get("status", "")))
        lines.append("")
        if x.get("description"):
            lines.append(x["description"])
            lines.append("")
        for side in x.get("sides", []):
            m = "".join("[%s]" % e for e in side.get("evidence_ids", []))
            lines.append("- %s %s" % (side.get("claim", ""), m))
        lines.append("")
    lines.append("## 研究空白（落到具体证据缺口）")
    lines.append("")
    for g in ir["conflicts_gaps"].get("gaps", []):
        lines.append("- %s：%s（依据：%s）" % (g.get("gap_id", ""), g.get("description", ""), g.get("basis", "")))
    return "\n".join(lines) + "\n"


def render_screening_log(ir):
    return {
        "criteria": ir["screening"].get("criteria", {}),
        "decisions": ir["screening"].get("decisions", []),
        "dedup": ir["inventory"].get("dedup", []),
    }


def artifact_paths(output_dir):
    return {
        "review": os.path.join(output_dir, "review.md"),
        "screening_log": os.path.join(output_dir, "screening_log.json"),
        "evidence_table": os.path.join(output_dir, "evidence_table.jsonl"),
        "themes": os.path.join(output_dir, "themes.md"),
        "conflicts_and_gaps": os.path.join(output_dir, "conflicts_and_gaps.md"),
        "result": os.path.join(output_dir, "result.json"),
    }


def classify_field(name):
    n = "".join(ch for ch in name.lower() if ch.isalnum())
    for cls, aliases in FIELD_ALIASES.items():
        if n in aliases:
            return cls
    return None


def field_target_type(name, ir):
    for f in ir["case"].get("contract_fields", []):
        if f.get("name") == name:
            return f.get("type_hint", ""), f.get("default")
    return "", None


def fill_contract_fields(result, ir):
    for name in list(result.keys()):
        th, dflt = field_target_type(name, ir)
        is_array = "array" in th or isinstance(dflt, list)
        is_object = "object" in th or isinstance(dflt, dict)
        n = "".join(ch for ch in name.lower() if ch.isalnum())
        if n in ("researchquestion", "question", "objective", "goal"):
            result[name] = ir["case"].get("research_question")
            continue
        if n in ("screeningcriteria", "criteria"):
            result[name] = ir["screening"].get("criteria", {})
            continue
        cls = classify_field(name)
        if cls is None:
            continue
        if cls == "review":
            if is_array:
                texts = []
                for pg in ir["claims"].get("paragraphs", []):
                    for c in pg.get("claims", []):
                        texts.append((c.get("text") or "").strip())
                result[name] = texts
            elif is_object:
                result[name] = {"file": "review.md", "paragraphs": ir["claims"].get("paragraphs", [])}
            else:
                result[name] = render_review(ir)
        elif cls == "screening":
            result[name] = render_screening_log(ir) if is_object else ir["screening"].get("decisions", [])
        elif cls == "evidence":
            result[name] = ir["evidence"]
        elif cls == "themes":
            result[name] = ir["themes"].get("themes", [])
        elif cls == "conflicts":
            result[name] = ir["conflicts_gaps"].get("conflicts", [])
        elif cls == "references":
            result[name] = ir["inventory"].get("sources", [])


def compute_status(ir, problems):
    meta_status = ir["meta"].get("status", "partial")
    if meta_status not in ALLOWED_STATUS:
        return "input_error"
    sources = ir["inventory"].get("sources", [])
    evidence = ir["evidence"]
    if meta_status == "ok":
        if problems or not sources or not evidence:
            return "partial"
    if meta_status == "partial" and not sources:
        return "insufficient_evidence"
    return meta_status


def build_result(ir, output_dir, problems):
    case = ir["case"]
    result = {}
    for f in case.get("contract_fields", []):
        name = f.get("name")
        if name:
            result[name] = f.get("default")
    raw = case.get("contract_raw")
    if isinstance(raw, dict):
        props = raw.get("properties") if isinstance(raw.get("properties"), dict) else None
        if props:
            for k in props:
                if k not in result:
                    result[k] = None
        else:
            for k in raw:
                if k not in result and k not in SCHEMA_META_KEYS:
                    result[k] = None

    fill_contract_fields(result, ir)

    meta = ir["meta"]
    base_status = meta.get("status", "partial")
    status = compute_status(ir, problems)
    combined_errors = list(meta.get("errors", [])) + list(problems)
    if status != base_status:
        combined_errors.append("status 由 %s 调整为 %s（因校验问题）" % (base_status, status))

    paths = artifact_paths(output_dir)
    gaps_text = ["研究空白[%s]: %s" % (g.get("gap_id", "?"), g.get("description", ""))
                 for g in ir["conflicts_gaps"].get("gaps", [])]
    uncertainty = list(meta.get("uncertainty", []))
    for g in gaps_text:
        if g not in uncertainty:
            uncertainty.append(g)

    result["case_id"] = case.get("case_id") or ""
    result["status"] = status
    result["artifacts"] = {k: v for k, v in paths.items()}
    result["sources"] = ir["inventory"].get("sources", [])
    result["uncertainty"] = uncertainty
    result["pending_human"] = meta.get("pending_human", [])
    result["degradations"] = meta.get("degradations", [])
    result["errors"] = combined_errors
    result["change_log"] = meta.get("change_log", [])
    return result


def render_all(work_dir, output_dir):
    """读片段 → 校验 → 渲染全部产物与 result.json（幂等、确定性）。"""
    ir, problems = assemble_ir(work_dir)
    xprobs, _ev, _src = validate_crossref(ir)
    all_problems = problems + xprobs
    os.makedirs(output_dir, exist_ok=True)
    write_text(os.path.join(output_dir, "review.md"), render_review(ir))
    write_json(os.path.join(output_dir, "screening_log.json"), render_screening_log(ir))
    write_jsonl(os.path.join(output_dir, "evidence_table.jsonl"), ir["evidence"])
    write_text(os.path.join(output_dir, "themes.md"), render_themes(ir))
    write_text(os.path.join(output_dir, "conflicts_and_gaps.md"), render_conflicts_gaps(ir))
    result = build_result(ir, output_dir, all_problems)
    write_json(os.path.join(output_dir, "result.json"), result)
    return result, all_problems


def check(work_dir, output_dir):
    """只读自检：校验片段、交叉引用、result.json 与产物齐全性。"""
    ir, problems = assemble_ir(work_dir)
    xprobs, _ev, _src = validate_crossref(ir)
    all_problems = problems + xprobs
    rp = os.path.join(output_dir, "result.json")
    if not os.path.isfile(rp):
        all_problems.append("result.json 不存在")
    else:
        r, err = read_json(rp)
        if r is None:
            all_problems.append("result.json 解析失败: " + (err or ""))
        else:
            for req in ("case_id", "status", "sources", "uncertainty", "change_log"):
                if req not in r:
                    all_problems.append("result.json 缺字段: " + req)
            if r.get("status") not in ALLOWED_STATUS:
                all_problems.append("result.json status 非法: %r" % r.get("status"))
    for name, p in artifact_paths(output_dir).items():
        if name != "result" and not os.path.isfile(p):
            all_problems.append("产物缺失: " + p)
    return all_problems
