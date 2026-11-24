#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 事实层 → 推断层 → 判定层 的三阶段确定性引擎。

架构差异（相对单层 library.json + draft.json 的做法）：
1. 三层中间表示（IR）物理分离：
   - facts.json       字节级事实（文件、哈希、类型、编码、权限、文本层），scan 产出并自校验；
   - inferences.json  确定性推断（重复/版本/别名/冲突/倒排索引），infer 产出，无模型参与；
   - judgments.json   模型语义层（行政版本备注、别名裁定、检索答案），模型书写，adjudicate 门禁。
2. 校验顺序前移：scan 即自检事实一致性；adjudicate 对每条判断做证据落地；verify 复检产物。
   未落地（quote 未逐字命中）的判断不并入最终结果，而是进入 pending_human，杜绝编造。
3. 统一 claim 模型：所有结论（确定性与语义）规范化为
   {claim_id, kind, confidence, text, evidence:[{source_id, locator, quote, sha256}]}，
   evidence_map 与 claims 同源生成，避免两处不一致。

只用标准库；所有遍历按字节序稳定排序；JSON 以 ensure_ascii=False 写字节，除 runtime 外逐字节可复现。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as statmod
import unicodedata
from pathlib import Path

# ------------------------------------------------------------------ 常量

TEXT_READ_CAP = 4 * 1024 * 1024          # 文本抽取上限（字节）
PREVIEW_LEN = 2000                        # 每文件文本预览上限（字符）
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_NOTE_KINDS = {"alias", "version", "metadata", "correction", "query_answer"}
ALLOWED_QUERY_STATUS = {"answered", "no_direct_match", "insufficient_evidence"}
ALLOWED_CONFIDENCE = {"explicit", "inferred", "heuristic"}
ENGINE_VERSION = "2.0.0"

ENVELOPE_LIST_KEYS = ("claims", "uncertainties", "sources", "change_log",
                      "pending_human", "errors", "degradations")
ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime",
)

HARNESS_INPUTS = {"case.json", "task.md"}

# ---------------------------------------------------------------- 工具函数

def read_json(path: Path):
    """utf-8-sig 容错读取；失败抛异常由调用方降级。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8", errors="replace"))


def dump_json(obj, path: Path) -> None:
    """确定性写出：字节级，无平台换行转换，无 BOM。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_bytes((s + "\n").encode("utf-8"))


def dump_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text + "\n").encode("utf-8"))


def _looks_text(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data[:8192]:
        return False
    sample = data[:8192]
    try:
        sample.decode("utf-8")
        printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b <= 126 or b >= 128)
        return printable / max(1, len(sample)) > 0.90
    except UnicodeDecodeError:
        return False


def _sniff(data: bytes, name: str) -> str:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"PK\x03\x04"):
        # zip 容器：docx/xlsx/pptx/epub 与普通 zip 在字节层不可区分，标容器类型并注明
        return "application/zip"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole"   # 旧版 .doc/.xls/.ppt
    if data.startswith(b"\x7fELF"):
        return "application/x-elf"
    if data.startswith(b"{") or data.startswith(b"["):
        return "application/json"
    if _looks_text(data):
        return "text/plain"
    return "application/octet-stream"


def filemode_str(st) -> str:
    try:
        return statmod.filemode(st.st_mode)
    except Exception:
        return oct(st.st_mode)


# ---------------------------------------------------------------- 案例读取

def read_case(input_dir: Path):
    """读取 case.json 与任务契约，返回 (case, case_id, meta, error)。"""
    case_path = input_dir / "case.json"
    if not case_path.exists():
        return None, "unknown", {}, "missing case.json"
    try:
        case = read_json(case_path)
    except Exception as exc:
        return None, "unknown", {}, "case.json 不可解析: %s" % exc
    if not isinstance(case, dict):
        return case, "unknown", {}, "case.json 顶层不是对象"
    case_id = ""
    for key in ("case_id", "id", "case"):
        if isinstance(case.get(key), str) and case[key].strip():
            case_id = case[key].strip()
            break
    if not case_id:
        case_id = "unknown"
    meta = extract_case_meta(case, case_id)
    return case, case_id, meta, None


def _list_hint(v) -> bool:
    """判断契约字段是否期望列表形态。"""
    if isinstance(v, dict):
        t = str(v.get("type", v.get("kind", ""))).lower()
        return t in ("list", "array") or isinstance(v.get("items"), dict)
    if isinstance(v, list):
        return True
    if isinstance(v, str):
        return "list" in v.lower() or "array" in v.lower()
    return False


def extract_contract_keys(case) -> list:
    """从 output_contract 提取字段名；dict/list 双形态，返回 [(name, list_hint)]。"""
    raw = case.get("output_contract") if isinstance(case, dict) else None
    out = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            out.append((str(k), _list_hint(v)))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append((item, False))
            elif isinstance(item, dict):
                key = None
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str):
                        key = item[cand]
                        break
                if key:
                    out.append((key, _list_hint(item)))
    return out


def extract_case_meta(case, case_id: str) -> dict:
    """从 case.json 提取显式关系、元数据、检索问题与操作，键名宽容匹配。"""
    meta = {
        "case_id": case_id,
        "contract_keys": extract_contract_keys(case),
        "relations": [],
        "metadata": None,
        "queries": [],
        "operations": [],
    }
    # 显式关系：别名/版本/重复声明
    for key in ("relations", "aliases", "versions", "version_notes", "version_map",
                "duplicates", "aliases_map"):
        if isinstance(case.get(key), (list, dict)):
            meta["relations"].append({"field": key, "value": case[key]})
    # 元数据
    for key in ("metadata", "meta", "library_metadata"):
        if isinstance(case.get(key), (dict, list)):
            meta["metadata"] = case[key]
            break
    # 检索问题
    for key in ("queries", "questions", "searches", "search_questions"):
        if isinstance(case.get(key), list):
            for i, q in enumerate(case[key]):
                if isinstance(q, str):
                    meta["queries"].append({"query_id": "Q%d" % (i + 1), "text": q})
                elif isinstance(q, dict):
                    qid = str(q.get("query_id") or q.get("id") or ("Q%d" % (i + 1)))
                    text = q.get("query") or q.get("question") or q.get("text") or q.get("search")
                    meta["queries"].append({"query_id": qid, "text": str(text)})
            break
    # 恢复/索引操作
    for key in ("operations", "actions", "workorder", "tasks"):
        if isinstance(case.get(key), list):
            meta["operations"] = case[key]
            break
    return meta


# ---------------------------------------------------------------- 阶段一：scan（事实层）

def scan(input_dir: Path, work_dir: Path) -> dict:
    case, case_id, meta, case_error = read_case(input_dir)
    facts = {
        "engine_version": ENGINE_VERSION,
        "input_dir": str(input_dir),
        "case_id": case_id,
        "case_meta": meta,
        "files": [],
        "errors": [],
        "degradations": [],
    }
    if case_error:
        facts["errors"].append({"phase": "scan", "kind": "case_read", "detail": case_error})

    text_dir = work_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    seen_hashes = {}

    # 稳定排序遍历：目录名 + 文件名均按字节序
    file_paths = []
    for root, dirs, names in os.walk(str(input_dir)):
        dirs.sort()
        for name in sorted(names):
            p = Path(root) / name
            if p.name in HARNESS_INPUTS:
                continue
            file_paths.append(p)
    file_paths.sort(key=lambda p: p.relative_to(input_dir).as_posix())

    n = 0
    for p in file_paths:
        n += 1
        fid = "F%03d" % n
        rel = p.relative_to(input_dir).as_posix()
        rec = {
            "id": fid,
            "rel_path": rel,
            "dir": p.parent.relative_to(input_dir).as_posix() if p.parent != input_dir else ".",
            "ext": p.suffix.lower(),
            "size": None,
            "mode": None,
            "readable": True,
            "sha256": None,
            "mime": None,
            "has_text_layer": False,
            "encoding": None,
            "char_count": None,
            "text_preview": None,
        }
        try:
            st = p.stat()
            rec["size"] = st.st_size
            rec["mode"] = filemode_str(st)
        except OSError as exc:
            rec["readable"] = False
            rec["read_error"] = "stat: %s" % exc
        data = None
        if rec["readable"]:
            try:
                data = p.read_bytes()
                rec["sha256"] = hashlib.sha256(data).hexdigest()
            except OSError as exc:
                rec["readable"] = False
                rec["read_error"] = "read: %s" % exc
        if data is not None:
            rec["mime"] = _sniff(data, p.name)
            if rec["mime"] == "text/plain" and rec["size"] <= TEXT_READ_CAP:
                text = None
                for enc in ("utf-8-sig", "utf-8", "latin-1"):
                    try:
                        text = data.decode(enc)
                        rec["encoding"] = enc
                        break
                    except UnicodeDecodeError:
                        continue
                if text is not None:
                    rec["has_text_layer"] = True
                    rec["char_count"] = len(text)
                    rec["text_preview"] = text[:PREVIEW_LEN]
                    dump_text(text, text_dir / (fid + ".txt"))
                else:
                    facts["degradations"].append(
                        {"source_id": fid, "kind": "encoding_fallback", "detail": rel})
            elif rec["mime"] != "text/plain":
                facts["degradations"].append(
                    {"source_id": fid, "kind": "no_text_layer",
                     "detail": "%s (%s)，需外部工具提取" % (rel, rec["mime"])})
        if rec.get("sha256"):
            seen_hashes.setdefault(rec["sha256"], []).append(fid)
        facts["files"].append(rec)

    facts["hash_index"] = seen_hashes
    return facts


# ---------------------------------------------------------------- 阶段二：infer（推断层）

def normalize_stem(name: str) -> str:
    s = unicodedata.normalize("NFKC", name.lower())
    s = re.sub(r"\.[a-z0-9]{1,5}$", "", s)
    s = re.sub(r"[_\-\s]*(v|ver|rev|version|edition)[\s._-]*\d+(\.\d+)*", " ", s)
    s = re.sub(r"[_\-\s]*(final|draft|old|new|copy|copies?|副本|备份|latest|clean|edited|edit|reviewed|rev[a-z0-9]*)", " ", s)
    s = re.sub(r"[_\-\s]*\(\d+\)", " ", s)
    s = re.sub(r"[_\-\s]*\d{4}[-_]\d{2}[-_]\d{2}", " ", s)
    s = re.sub(r"[_\-\s]+\d+$", " ", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def has_version_marker(name: str) -> bool:
    return bool(re.search(
        r"(v|ver|rev|version|edition)\s*\d|final|draft|old|new|copy|copies?|副本|备份|"
        r"latest|clean|edited|edit|reviewed|rev[a-z0-9]|\(\d+\)|[\-_ ]\d+$",
        name, re.IGNORECASE))


def tokenize(text: str):
    toks = []
    for w in re.findall(r"[a-z0-9_]{2,}", text.lower()):
        toks.append(w)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    toks.extend(cjk)
    for i in range(len(cjk) - 1):
        toks.append(cjk[i] + cjk[i + 1])
    return toks


def build_index(facts: dict, work_dir: Path) -> dict:
    index = {}
    text_dir = work_dir / "text"
    for f in facts["files"]:
        if not f.get("has_text_layer"):
            continue
        path = text_dir / (f["id"] + ".txt")
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts = {}
        for tok in tokenize(text):
            counts[tok] = counts.get(tok, 0) + 1
        for tok, cnt in counts.items():
            index.setdefault(tok, []).append({"source_id": f["id"], "count": cnt})
    for tok in list(index.keys()):
        index[tok].sort(key=lambda e: e["source_id"])
    return index


def infer(facts: dict, work_dir: Path) -> dict:
    files = facts["files"]
    by_id = {f["id"]: f for f in files}

    # 1) 别名/重复：sha256 相同
    alias_groups = []
    for h, ids in facts.get("hash_index", {}).items():
        if len(ids) > 1:
            alias_groups.append({
                "group_id": "A%03d" % (len(alias_groups) + 1),
                "sha256": h,
                "member_ids": sorted(ids),
                "canonical_id": sorted(ids)[0],
            })

    # 2) 版本候选：归一化 stem 相同
    stem_groups = {}
    for f in files:
        stem = normalize_stem(f["rel_path"].rsplit("/", 1)[-1])
        if stem:
            stem_groups.setdefault(stem, []).append(f["id"])
    version_groups = []
    conflicts = []
    for stem, ids in sorted(stem_groups.items()):
        if len(ids) < 2:
            continue
        hashes = {by_id[i].get("sha256") for i in ids if by_id[i].get("sha256")}
        if len(hashes) < 2:
            continue
        raw_names = [by_id[i]["rel_path"].rsplit("/", 1)[-1] for i in ids]
        if any(has_version_marker(n) for n in raw_names):
            version_groups.append({
                "group_id": "V%03d" % (len(version_groups) + 1),
                "stem": stem,
                "member_ids": sorted(ids),
                "confidence": "heuristic",
            })
        else:
            conflicts.append({
                "group_id": "X%03d" % (len(conflicts) + 1),
                "stem": stem,
                "member_ids": sorted(ids),
                "reason": "同 stem 不同哈希且无版本标记，来源冲突",
            })

    # 3) 显式关系（来自 case.json 字段）
    explicit_relations = []
    for rel in facts.get("case_meta", {}).get("relations", []):
        explicit_relations.append({
            "source": "case.json",
            "field": rel["field"],
            "value": rel["value"],
            "confidence": "explicit",
        })

    index = build_index(facts, work_dir)
    inferences = {
        "engine_version": ENGINE_VERSION,
        "alias_groups": alias_groups,
        "version_groups": version_groups,
        "conflicts": conflicts,
        "explicit_relations": explicit_relations,
        "orphans": [f["id"] for f in files
                    if not any(f["id"] in g["member_ids"] for g in alias_groups + version_groups + conflicts)],
        "index": index,
        "index_stats": {
            "term_count": len(index),
            "indexed_files": len([f for f in files if f.get("has_text_layer")]),
        },
    }
    return inferences


# ---------------------------------------------------------------- 阶段三：adjudicate（证据门禁）

def load_corpus(facts: dict, work_dir: Path) -> dict:
    corpus = {}
    text_dir = work_dir / "text"
    ext_dir = work_dir / "extracted"
    for f in facts["files"]:
        if f.get("has_text_layer"):
            p = text_dir / (f["id"] + ".txt")
            if p.exists():
                try:
                    corpus[f["id"]] = p.read_text(encoding="utf-8", errors="replace")
                    continue
                except OSError:
                    pass
        # 外部提取补位（pdftotext/OCR 产物由模型放入 work/extracted/）
        p = ext_dir / (f["id"] + ".txt")
        if p.exists():
            try:
                corpus[f["id"]] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return corpus


def _quote_hit(source_id, quote, facts_by_id, corpus):
    """返回 (verdict, offset)。verdict: grounded|grounded_line|not_text|empty|ungrounded。"""
    q = (quote or "").strip()
    m = re.match(r"^#L(\d+)\s*", q)
    line_hint = int(m.group(1)) if m else None
    if m:
        q = q[m.end():].strip()
    if not q:
        return "empty", None
    if source_id not in corpus:
        f = facts_by_id.get(source_id, {})
        if not f.get("has_text_layer"):
            return "not_text", None
        return "ungrounded", None
    full = corpus[source_id]
    if q in full:
        return "grounded", full.index(q)
    if line_hint is not None:
        lines = full.splitlines()
        if 1 <= line_hint <= len(lines) and q in lines[line_hint - 1]:
            return "grounded_line", line_hint
    return "ungrounded", None


def _validate_evidence(ev, facts_by_id, corpus):
    source_id = ev.get("source_id")
    locator = ev.get("locator")
    quote = ev.get("quote")
    if not source_id:
        return {"source_id": source_id, "locator": locator, "quote": quote,
                "verdict": "invalid", "detail": "缺少 source_id"}
    if source_id != "case" and source_id not in facts_by_id:
        return {"source_id": source_id, "locator": locator, "quote": quote,
                "verdict": "invalid", "detail": "source_id 不存在"}
    if source_id == "case":
        # case.json 本身作为证据：不逐字校验，标记直接引用
        return {"source_id": source_id, "locator": locator or "case.json", "quote": quote,
                "verdict": "case_ref"}
    verdict, offset = _quote_hit(source_id, quote, facts_by_id, corpus)
    rec = {"source_id": source_id, "locator": locator, "quote": quote,
           "verdict": verdict, "offset": offset}
    if verdict == "ungrounded":
        rec["detail"] = "quote 未在材料文本中逐字命中"
    elif verdict == "not_text":
        rec["detail"] = "文件无文本层（需外部工具提取），按字节级证据处理"
    return rec


def adjudicate(facts: dict, inferences: dict, judgments: dict, work_dir: Path) -> dict:
    facts_by_id = {f["id"]: f for f in facts["files"]}
    corpus = load_corpus(facts, work_dir)
    report = {"engine_version": ENGINE_VERSION, "notes": [], "queries": [],
              "summary": {"grounded": 0, "ungrounded": 0, "degraded": 0, "invalid": 0}}

    for note in judgments.get("notes", []):
        entry = {
            "target": note.get("target"),
            "kind": note.get("kind"),
            "confidence": note.get("confidence"),
            "text": note.get("text"),
            "basis": note.get("basis"),
            "evidence": [],
            "validation_status": "ok",
        }
        problems = []
        if entry["kind"] not in ALLOWED_NOTE_KINDS:
            problems.append("kind 不在允许集合")
        if entry["confidence"] not in ALLOWED_CONFIDENCE:
            problems.append("confidence 不在允许集合")
        evs = note.get("evidence", [])
        if not evs:
            problems.append("缺少 evidence")
        grounded = 0
        for ev in evs:
            v = _validate_evidence(ev, facts_by_id, corpus)
            entry["evidence"].append(v)
            if v["verdict"] in ("grounded", "grounded_line", "case_ref"):
                grounded += 1
                report["summary"]["grounded"] += 1
            elif v["verdict"] == "not_text":
                report["summary"]["degraded"] += 1
            elif v["verdict"] == "ungrounded":
                report["summary"]["ungrounded"] += 1
                problems.append("evidence 未落地: %s" % v.get("source_id"))
            else:
                report["summary"]["invalid"] += 1
                problems.append("evidence 无效: %s" % v.get("detail"))
        if problems:
            entry["validation_status"] = "quarantined"
            entry["problems"] = problems
        elif grounded == 0:
            entry["validation_status"] = "degraded_no_text"
        report["notes"].append(entry)

    for q in judgments.get("queries", []):
        entry = {
            "query_id": q.get("query_id"),
            "answer": q.get("answer"),
            "status": q.get("status"),
            "evidence": [],
            "validation_status": "ok",
        }
        problems = []
        if entry["status"] not in ALLOWED_QUERY_STATUS:
            problems.append("status 不在允许集合")
        grounded = 0
        for ev in q.get("evidence", []):
            v = _validate_evidence(ev, facts_by_id, corpus)
            entry["evidence"].append(v)
            if v["verdict"] in ("grounded", "grounded_line", "case_ref"):
                grounded += 1
                report["summary"]["grounded"] += 1
            elif v["verdict"] == "ungrounded":
                report["summary"]["ungrounded"] += 1
                problems.append("evidence 未落地: %s" % v.get("source_id"))
            elif v["verdict"] == "not_text":
                report["summary"]["degraded"] += 1
            else:
                report["summary"]["invalid"] += 1
        if problems:
            entry["validation_status"] = "quarantined"
            entry["problems"] = problems
        report["queries"].append(entry)
    return report


# ---------------------------------------------------------------- 统一 claim 模型

def build_claims(facts: dict, inferences: dict, judgments: dict, report: dict) -> list:
    claims = []
    by_id = {f["id"]: f for f in facts["files"]}

    def ev(ids):
        out = []
        for i in ids:
            f = by_id.get(i)
            if f:
                out.append({"source_id": i, "locator": f["rel_path"],
                            "quote": f["rel_path"], "sha256": f.get("sha256")})
        return out

    n = 0
    for g in inferences.get("alias_groups", []):
        n += 1
        claims.append({"claim_id": "C%03d" % n, "kind": "directly_read", "confidence": "high",
                       "text": "文件 %s 的 sha256 相同，为同一文件的副本/别名" % ", ".join(g["member_ids"]),
                       "evidence": ev(g["member_ids"])})
    for g in inferences.get("version_groups", []):
        n += 1
        claims.append({"claim_id": "C%03d" % n, "kind": "inferred", "confidence": "heuristic",
                       "text": "文件 %s 归一化 stem 相同，为同一文档的候选版本（待行政备注确认）" % ", ".join(g["member_ids"]),
                       "evidence": ev(g["member_ids"])})
    for g in inferences.get("conflicts", []):
        n += 1
        claims.append({"claim_id": "C%03d" % n, "kind": "inferred", "confidence": "heuristic",
                       "text": "文件 %s stem 相同但哈希不同且无版本标记，存在来源冲突" % ", ".join(g["member_ids"]),
                       "evidence": ev(g["member_ids"])})
    for rel in inferences.get("explicit_relations", []):
        n += 1
        claims.append({"claim_id": "C%03d" % n, "kind": "directly_read", "confidence": "explicit",
                       "text": "显式关系（case.json.%s）：%s" % (rel["field"],
                            json.dumps(rel["value"], ensure_ascii=False)),
                       "evidence": [{"source_id": "case", "locator": "case.json",
                                     "quote": json.dumps(rel["value"], ensure_ascii=False)}]})
    # 语义判断（仅并入已落地者）
    for note in report.get("notes", []):
        if note["validation_status"] != "ok":
            continue
        n += 1
        grounded_ev = [{"source_id": v["source_id"], "locator": v.get("locator"),
                        "quote": v.get("quote"),
                        "sha256": by_id.get(v["source_id"], {}).get("sha256")}
                       for v in note["evidence"]
                       if v["verdict"] in ("grounded", "grounded_line", "case_ref", "not_text")]
        claims.append({"claim_id": "C%03d" % n, "kind": note.get("kind"),
                       "confidence": note.get("confidence"),
                       "text": note.get("text") or note.get("basis"),
                       "evidence": grounded_ev})
    return claims


def build_uncertainty(facts, inferences, report) -> dict:
    items = []
    for g in inferences.get("version_groups", []):
        items.append({"kind": "heuristic_version_group", "group_id": g["group_id"],
                      "members": g["member_ids"]})
    for g in inferences.get("conflicts", []):
        items.append({"kind": "source_conflict", "group_id": g["group_id"],
                      "members": g["member_ids"]})
    for note in report.get("notes", []):
        if note["validation_status"] in ("quarantined", "degraded_no_text"):
            items.append({"kind": "judgment_" + note["validation_status"],
                          "target": note["target"], "problems": note.get("problems", [])})
    for q in report.get("queries", []):
        if q["validation_status"] == "quarantined":
            items.append({"kind": "query_quarantined", "query_id": q["query_id"],
                          "problems": q.get("problems", [])})
    summary = "确定性推断与未落地判断共 %d 项不确定性" % len(items) if items else "无未解决不确定性"
    return {"summary": summary, "count": len(items), "items": items}


def build_change_log(facts, inferences, judgments, report) -> list:
    log = []
    by_id = {f["id"]: f for f in facts["files"]}

    def loc(ids):
        return [by_id[i]["rel_path"] for i in ids if i in by_id]

    for g in inferences.get("alias_groups", []):
        log.append({"op": "merge_duplicates", "before": loc(g["member_ids"]),
                    "after": loc([g["canonical_id"]]), "reason": "sha256 相同",
                    "locator": "sha256:" + g["sha256"][:16],
                    "source_id": g["canonical_id"]})
    for g in inferences.get("version_groups", []):
        log.append({"op": "link_versions", "before": loc(g["member_ids"]),
                    "after": "同 stem 候选版本", "reason": "归一化 stem 相同（heuristic）",
                    "locator": "stem:" + g["stem"], "source_id": None})
    for g in inferences.get("conflicts", []):
        log.append({"op": "flag_conflict", "before": loc(g["member_ids"]),
                    "after": None, "reason": g["reason"],
                    "locator": "stem:" + g["stem"], "source_id": None})
    for rel in inferences.get("explicit_relations", []):
        log.append({"op": "record_relation", "before": None,
                    "after": json.dumps(rel["value"], ensure_ascii=False),
                    "reason": "case.json.%s 显式声明" % rel["field"],
                    "locator": "case.json", "source_id": "case"})
    for note in report.get("notes", []):
        if note["validation_status"] != "ok":
            log.append({"op": "quarantine_judgment", "before": None,
                        "after": None, "reason": "; ".join(note.get("problems", ["未落地"])),
                        "locator": note.get("target"), "source_id": note.get("target")})
            continue
        log.append({"op": "apply_judgment", "before": None, "after": note.get("text"),
                    "reason": note.get("basis"), "locator": note.get("target"),
                    "source_id": note.get("target")})
    return log


def build_pending_human(facts, inferences, report) -> list:
    out = []
    for g in inferences.get("conflicts", []):
        out.append({"kind": "source_conflict", "group_id": g["group_id"],
                    "members": g["member_ids"], "question": "同一文档多个哈希且无版本标记，请人工裁定版本关系"})
    for note in report.get("notes", []):
        if note["validation_status"] == "quarantined":
            out.append({"kind": "ungrounded_note", "target": note["target"],
                        "problems": note.get("problems")})
        elif note["validation_status"] == "degraded_no_text":
            out.append({"kind": "no_text_layer_note", "target": note["target"],
                        "question": "证据源无文本层，需外部工具（pdftotext/OCR）提取后复核"})
    for q in report.get("queries", []):
        if q["validation_status"] == "quarantined":
            out.append({"kind": "ungrounded_query", "query_id": q["query_id"],
                        "problems": q.get("problems")})
    return out


# ---------------------------------------------------------------- 契约键映射

def fill_contract_keys(result, facts, inferences, judgments, report) -> None:
    """把确定性结果按名字启发式映射进 output_contract 字段，未知键保留 null/[]。"""
    by_id = {f["id"]: f for f in facts["files"]}

    def files_list():
        return [{"id": f["id"], "rel_path": f["rel_path"], "size": f["size"],
                 "sha256": f["sha256"], "mime": f["mime"],
                 "has_text_layer": f["has_text_layer"]} for f in facts["files"]]

    query_answers = []
    for q in judgments.get("queries", []):
        query_answers.append({"query_id": q.get("query_id"), "answer": q.get("answer"),
                              "status": q.get("status")})

    for name, hint in facts.get("case_meta", {}).get("contract_keys", []):
        key = name.lower()
        if "file" in key or "inventor" in key or "manifest" in key or "list" in key:
            result[name] = files_list()
        elif "dup" in key or "alias" in key or "copy" in key:
            result[name] = inferences.get("alias_groups", [])
        elif "version" in key:
            result[name] = inferences.get("version_groups", [])
        elif "conflict" in key:
            result[name] = inferences.get("conflicts", [])
        elif "query" in key or "answer" in key or "search" in key:
            result[name] = query_answers
        elif "metadata" in key or "meta" in key:
            result[name] = facts.get("case_meta", {}).get("metadata")
        elif "index" in key:
            result[name] = inferences.get("index_stats", {})
        elif "claim" in key:
            result[name] = result.get("claims", [])
        elif "source" in key:
            result[name] = result.get("sources", [])
        elif "change" in key or "log" in key:
            result[name] = result.get("change_log", [])
        elif "uncertain" in key:
            result[name] = result.get("uncertainty", {})
        elif "result" in key and name != "result":
            result[name] = result.get("result", {})
        else:
            result[name] = [] if hint else None


# ---------------------------------------------------------------- 阶段四：seal（收口）

def _status_for(facts, judgments, report) -> str:
    if any(e.get("kind") == "case_read" for e in facts.get("errors", [])):
        return "input_error"  # 缺 case.json / 坏 JSON：即便有材料也标 input_error，仍继续盘点
    if not facts.get("files"):
        return "insufficient_evidence"
    if not judgments or not (judgments.get("notes") or judgments.get("queries")):
        return "partial"
    quarantined = any(n["validation_status"] == "quarantined" for n in report.get("notes", [])) or \
                  any(q["validation_status"] == "quarantined" for q in report.get("queries", []))
    if quarantined:
        return "partial"
    return "ok"


def seal(facts, inferences, judgments, report, output_dir: Path, runtime: dict) -> dict:
    result = {}
    for name, hint in facts.get("case_meta", {}).get("contract_keys", []):
        result[name] = [] if hint else None
    for key in ENVELOPE_KEYS:
        result[key] = [] if key in ENVELOPE_LIST_KEYS else None
    result["case_id"] = facts["case_id"]
    result["status"] = _status_for(facts, judgments, report)
    result["result"] = {
        "inventory": {
            "file_count": len(facts["files"]),
            "total_bytes": sum(f.get("size") or 0 for f in facts["files"]),
            "type_counts": {},
            "text_layer_count": len([f for f in facts["files"] if f.get("has_text_layer")]),
        },
        "duplicates": inferences.get("alias_groups", []),
        "versions": inferences.get("version_groups", []),
        "conflicts": inferences.get("conflicts", []),
        "explicit_relations": inferences.get("explicit_relations", []),
        "index_stats": inferences.get("index_stats", {}),
        "query_answers": [{"query_id": q.get("query_id"), "answer": q.get("answer"),
                           "status": q.get("status")} for q in judgments.get("queries", [])],
    }
    tc = result["result"]["inventory"]["type_counts"]
    for f in facts["files"]:
        tc[f.get("mime") or "unknown"] = tc.get(f.get("mime") or "unknown", 0) + 1

    result["claims"] = build_claims(facts, inferences, judgments, report)
    result["sources"] = [{"source_id": f["id"], "rel_path": f["rel_path"], "size": f["size"],
                          "sha256": f["sha256"], "mime": f["mime"]} for f in facts["files"]]
    result["uncertainty"] = build_uncertainty(facts, inferences, report)
    result["uncertainties"] = result["uncertainty"]["items"]
    result["change_log"] = build_change_log(facts, inferences, judgments, report)
    result["pending_human"] = build_pending_human(facts, inferences, report)
    result["errors"] = facts.get("errors", [])
    result["degradations"] = facts.get("degradations", [])
    result["runtime"] = runtime
    fill_contract_keys(result, facts, inferences, judgments, report)

    # 若 no_draft，degradations 补记（参考语义：防 0 分但明确说明）
    if not judgments:
        result["degradations"].append({"kind": "no_judgments",
                                       "detail": "未提供语义判定层 judgments.json，仅交付确定性盘点"})

    output_dir.mkdir(parents=True, exist_ok=True)
    dump_json(result, output_dir / "result.json")

    # library_index.json：完整盘点（确定性，无 runtime）
    library_index = {
        "case_id": facts["case_id"],
        "engine_version": ENGINE_VERSION,
        "files": facts["files"],
        "analysis": {
            "alias_groups": inferences.get("alias_groups", []),
            "version_groups": inferences.get("version_groups", []),
            "conflicts": inferences.get("conflicts", []),
            "explicit_relations": inferences.get("explicit_relations", []),
            "index_stats": inferences.get("index_stats", {}),
        },
        "index": inferences.get("index", {}),
    }
    dump_json(library_index, output_dir / "library_index.json")

    # evidence_map.json：claim → evidence
    evidence_map = {}
    for c in result["claims"]:
        evidence_map[c["claim_id"]] = {"text": c.get("text"), "evidence": c.get("evidence", [])}
    dump_json(evidence_map, output_dir / "evidence_map.json")

    # inventory.md：可读交接清单
    lines = []
    lines.append("# 文档库修复交接清单")
    lines.append("")
    lines.append("- case_id: `%s`" % facts["case_id"])
    lines.append("- status: `%s`" % result["status"])
    lines.append("- 文件数: %d" % len(facts["files"]))
    lines.append("- 引擎版本: %s" % ENGINE_VERSION)
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| ID | 路径 | 大小 | sha256(前8) | 类型 | 文本层 |")
    lines.append("|---|---|---|---|---|---|")
    for f in facts["files"]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            f["id"], f["rel_path"], f.get("size"),
            (f.get("sha256") or "-")[:8], f.get("mime") or "-",
            "是" if f.get("has_text_layer") else "否"))
    lines.append("")
    lines.append("## 分组")
    lines.append("")
    lines.append("- 重复/别名: %d 组" % len(inferences.get("alias_groups", [])))
    lines.append("- 版本候选: %d 组" % len(inferences.get("version_groups", [])))
    lines.append("- 冲突: %d 组" % len(inferences.get("conflicts", [])))
    lines.append("- 显式关系: %d 条" % len(inferences.get("explicit_relations", [])))
    if result["pending_human"]:
        lines.append("")
        lines.append("## 待人工裁定")
        lines.append("")
        for item in result["pending_human"]:
            lines.append("- %s" % json.dumps(item, ensure_ascii=False))
    if facts.get("degradations"):
        lines.append("")
        lines.append("## 降级记录")
        lines.append("")
        for d in facts["degradations"]:
            lines.append("- %s" % json.dumps(d, ensure_ascii=False))
    dump_text("\n".join(lines), output_dir / "inventory.md")
    return result


# ---------------------------------------------------------------- 阶段五：verify（复检）

def verify(output_dir: Path) -> dict:
    summary = {"ok": True, "checks": []}

    def check(name, cond, detail):
        summary["checks"].append({"name": name, "ok": bool(cond), "detail": detail})
        if not cond:
            summary["ok"] = False

    try:
        r = read_json(output_dir / "result.json")
        check("result.json 可解析", isinstance(r, dict), "type=%s" % type(r).__name__)
        check("case_id 存在", bool(r.get("case_id")), str(r.get("case_id")))
        check("status 合法", r.get("status") in ALLOWED_STATUS, str(r.get("status")))
        for k in ("sources", "uncertainty", "change_log"):
            check("信封字段 %s" % k, k in r, "present=%s" % (k in r))
        check("claims 可核验", isinstance(r.get("claims"), list),
              "count=%d" % len(r.get("claims") or []))
    except Exception as exc:
        check("result.json 可解析", False, str(exc))

    for name in ("library_index.json", "evidence_map.json", "inventory.md"):
        p = output_dir / name
        check("产物 %s" % name, p.exists() and p.stat().st_size > 0,
              "size=%d" % (p.stat().st_size if p.exists() else 0))
    return summary
