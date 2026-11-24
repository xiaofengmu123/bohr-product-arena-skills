#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py — 工作稿模板、机械校验与 result.json 信封组装（纯函数，标准库）。

职责：
- 定义 draft.json 的字段模板；
- check 子命令的机械校验：结构合法、source_id 存在、quote 逐字命中；
- 把确定性 library 与模型 draft 合并为最终 result.json 信封。
校验只拦截"编造/错源"，不判断语义优劣。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime",
)
LIST_ENVELOPE_KEYS = (
    "claims", "uncertainties", "sources", "change_log",
    "pending_human", "errors", "degradations",
)
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_NOTE_KINDS = {
    "alias", "version", "metadata", "correction", "query_answer",
    "recovery", "index", "conflict", "skip",
}
ALLOWED_CONFIDENCE = {"explicit", "inferred", "heuristic"}

# draft.json 模板
DRAFT_TEMPLATE = {
    "case_id": "",
    "_template": True,
    "notes": [
        {
            "target": "F004",
            "kind": "alias|version|metadata|correction|query_answer|recovery|index|conflict|skip",
            "text": "……",
            "basis": "……",
            "confidence": "explicit|inferred|heuristic",
            "evidence": [
                {"source_id": "F004", "locator": "materials/x.pdf", "quote": "逐字片段"}
            ],
        }
    ],
    "queries": [
        {
            "query_id": "Q1",
            "answer": "……",
            "status": "answered|no_direct_match|insufficient_evidence",
            "evidence": [
                {"source_id": "F004", "locator": "…", "quote": "…"}
            ],
        }
    ],
}


def is_template_draft(draft: dict) -> bool:
    """判断 draft 是否为未填写的模板（含 _template 标记或填充了占位符）。"""
    if not isinstance(draft, dict):
        return True
    if draft.get("_template"):
        return True
    if draft.get("error"):
        return True
    return False


def _fmt(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


# 常见 output_contract 字段名 → 由 library 确定性回填的映射（其余键留给模型 draft 语义填充）。
_CONTRACT_FILLERS = {
    "inventory": lambda lib: lib.get("files", []),
    "files": lambda lib: lib.get("files", []),
    "file_inventory": lambda lib: lib.get("files", []),
    "relations": lambda lib: lib.get("analysis", {}).get("explicit_relations", []),
    "aliases": lambda lib: lib.get("analysis", {}).get("alias_groups", []),
    "alias_groups": lambda lib: lib.get("analysis", {}).get("alias_groups", []),
    "versions": lambda lib: lib.get("analysis", {}).get("version_groups", []),
    "version_groups": lambda lib: lib.get("analysis", {}).get("version_groups", []),
    "conflicts": lambda lib: lib.get("analysis", {}).get("conflicts", []),
    "queries": lambda lib: lib.get("analysis", {}).get("queries", []),
    "search_results": lambda lib: _search_results(lib),
    "index": lambda lib: lib.get("index", {}),
    "errors": lambda lib: lib.get("errors", []),
}


def _search_results(lib: dict) -> list:
    out = []
    for q in lib.get("analysis", {}).get("queries", []):
        text = q.get("text", "")
        hits = []
        for rel_path, rec in lib.get("index", {}).items():
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())
            excerpt = rec.get("excerpt", "").lower()
            if tokens and all(t in excerpt for t in tokens):
                hits.append({"rel_path": rel_path, "id": rec.get("id")})
        out.append({"query_id": q.get("query_id"), "query": text, "hits": hits})
    return out


def fill_contract(result: dict, library: dict, contract_keys: list) -> dict:
    """把 output_contract 的字段确定性回填；未知键保持 null，由模型 draft 补语义值。"""
    for key in contract_keys:
        filler = _CONTRACT_FILLERS.get(key)
        if filler is not None:
            result[key] = filler(library)
    return result


def load_draft(draft_path) -> dict:
    """容错读取 draft.json；坏 JSON 返回带 error 标记。"""
    try:
        raw = draft_path.read_text(encoding="utf-8-sig")
        return json.loads(raw)
    except FileNotFoundError:
        return {"error": "missing_draft"}
    except (json.JSONDecodeError, UnicodeError) as exc:
        return {"error": f"invalid_json:{exc}"}
    except OSError as exc:
        return {"error": f"unreadable:{exc}"}


def validate_draft(draft: dict, library: dict, input_dir) -> dict:
    """机械校验 draft，返回 check_report。

    校验项：
    - draft 可解析且为 dict；
    - case_id 非空；
    - notes / queries 为列表，条目结构合法；
    - 每条 evidence 的 source_id 在 library.files 中存在；
    - 每条 evidence 的 quote 在对应材料中逐字命中（文本文件）；二进制文件 quote
      退化为文件名命中并标记 quote_verbatim=false。
    """
    report = {"ok": True, "errors": [], "warnings": [], "counts": {}}

    if isinstance(draft, dict) and draft.get("error"):
        report["ok"] = False
        report["errors"].append(draft["error"])
        return report

    if not isinstance(draft, dict):
        report["ok"] = False
        report["errors"].append("draft 必须是 JSON 对象")
        return report

    if not draft.get("case_id"):
        report["errors"].append("draft.case_id 缺失")

    file_ids = {f["id"]: f for f in library.get("files", [])}
    notes = draft.get("notes", [])
    queries = draft.get("queries", [])

    if not isinstance(notes, list):
        report["errors"].append("draft.notes 必须是列表")
        notes = []
    if not isinstance(queries, list):
        report["errors"].append("draft.queries 必须是列表")
        queries = []

    for i, note in enumerate(notes):
        _validate_note(note, i, file_ids, input_dir, report)

    for i, q in enumerate(queries):
        _validate_query(q, i, file_ids, input_dir, report)

    report["counts"] = {"notes": len(notes), "queries": len(queries)}
    if report["errors"]:
        report["ok"] = False
    return report


def _validate_note(note, idx, file_ids, input_dir, report):
    tag = f"notes[{idx}]"
    if not isinstance(note, dict):
        report["errors"].append(f"{tag} 必须是对象")
        return
    if not note.get("target"):
        report["errors"].append(f"{tag}.target 缺失")
    if note.get("kind") not in ALLOWED_NOTE_KINDS:
        report["warnings"].append(f"{tag}.kind 非法：{note.get('kind')}")
    if note.get("confidence") not in ALLOWED_CONFIDENCE:
        report["warnings"].append(f"{tag}.confidence 非法：{note.get('confidence')}")
    if not isinstance(note.get("text"), str) or not note["text"].strip():
        report["errors"].append(f"{tag}.text 缺失")
    _validate_evidence(note.get("evidence"), tag, file_ids, input_dir, report)


def _validate_query(q, idx, file_ids, input_dir, report):
    tag = f"queries[{idx}]"
    if not isinstance(q, dict):
        report["errors"].append(f"{tag} 必须是对象")
        return
    if not q.get("query_id"):
        report["errors"].append(f"{tag}.query_id 缺失")
    if not isinstance(q.get("answer"), str) or not q["answer"].strip():
        report["warnings"].append(f"{tag}.answer 为空")
    if q.get("status") not in ("answered", "no_direct_match", "insufficient_evidence"):
        report["warnings"].append(f"{tag}.status 非法：{q.get('status')}")
    _validate_evidence(q.get("evidence"), tag, file_ids, input_dir, report)


def _validate_evidence(evidence, tag, file_ids, input_dir, report):
    if evidence is None:
        report["warnings"].append(f"{tag}.evidence 为空")
        return
    if not isinstance(evidence, list):
        report["errors"].append(f"{tag}.evidence 必须是列表")
        return
    for j, ev in enumerate(evidence):
        etag = f"{tag}.evidence[{j}]"
        if not isinstance(ev, dict):
            report["errors"].append(f"{etag} 必须是对象")
            continue
        sid = ev.get("source_id")
        if sid not in file_ids and sid != "case":
            report["errors"].append(f"{etag}.source_id 不存在：{sid}")
            continue
        locator = ev.get("locator", "")
        quote = ev.get("quote", "")
        if not locator:
            report["errors"].append(f"{etag}.locator 缺失")
        if not isinstance(quote, str) or not quote:
            report["errors"].append(f"{etag}.quote 缺失")
            continue
        # 逐字命中校验
        _check_quote_verbatim(sid, locator, quote, file_ids, input_dir, etag, report)


def _check_quote_verbatim(sid, locator, quote, file_ids, input_dir, etag, report):
    if sid == "case":
        # 对 case.json 的 quote 命中做字符串包含检查
        try:
            case_text = (input_dir / "case.json").read_text(encoding="utf-8-sig")
            if quote not in case_text:
                report["errors"].append(f"{etag}.quote 未在 case.json 中逐字命中")
        except OSError:
            report["warnings"].append(f"{etag} 无法读取 case.json 校验 quote")
        return
    f = file_ids.get(sid)
    if not f:
        return
    # 一致性检查：source_id 应指向 locator 对应的文件；不一致记录 warning。
    if locator and Path(f["rel_path"]).as_posix() != Path(locator).as_posix():
        report["warnings"].append(
            f"{etag} source_id={sid} 的 rel_path({f['rel_path']}) 与 locator({locator}) 不一致"
        )
    # 以 locator 为权威定位读取材料（locator 缺失则回退 source_id 的 rel_path）。
    target_rel = locator or f["rel_path"]
    path = input_dir / target_rel
    if f.get("kind") == "text":
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            try:
                text = path.read_text(encoding="latin-1")
            except OSError:
                report["warnings"].append(f"{etag} 无法读取材料校验 quote")
                return
        if quote not in text:
            report["errors"].append(f"{etag}.quote 未在 {target_rel} 中逐字命中")
    else:
        # 二进制材料无法逐字校验，退化为 locator 文件名一致检查；
        # 由 build 阶段在 evidence_map 中标记 quote_verbatim=false。
        if Path(locator).name not in Path(f["rel_path"]).name:
            report["warnings"].append(f"{etag} locator 与 {f['rel_path']} 文件名不一致")
        report["warnings"].append(f"{etag} 材料为二进制，quote 仅能按 locator 核对")


def _mark_evidence(ev: dict, files_by_id: dict) -> dict:
    """给单条 evidence 附加 quote_verbatim 标记（文本/case 为 true，二进制为 false）。"""
    sid = ev.get("source_id")
    f = files_by_id.get(sid) if sid else None
    item = {
        "source_id": sid,
        "locator": ev.get("locator"),
        "quote": ev.get("quote"),
    }
    if (f and f.get("kind") == "text") or sid == "case":
        item["quote_verbatim"] = True
    else:
        item["quote_verbatim"] = False
        item["quote_note"] = "binary_material_locator_only"
    return item


def build_evidence_map(result: dict, library: dict, draft: dict) -> dict:
    """生成 evidence_map.json：claim_id -> 逐条 evidence（含 quote_verbatim 标记）。

    文本材料的 quote 标记逐字命中；二进制材料标记 quote_verbatim=false，
    quote_note 说明只能按 locator 定位。
    """
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    out = {}
    for claim in result.get("claims", []):
        cid = claim.get("claim_id")
        if not cid:
            continue
        out[cid] = [_mark_evidence(ev, files_by_id) for ev in claim.get("evidence", [])]
    # 同时映射 query 证据（与 claim 证据同构，便于逐条复核）
    queries = draft.get("queries", []) if isinstance(draft, dict) else []
    for q in queries:
        if not isinstance(q, dict):
            continue
        qid = q.get("query_id")
        if not qid:
            continue
        out[f"query:{qid}"] = [
            _mark_evidence(ev, files_by_id) for ev in q.get("evidence", [])
        ]
    return out


def build_result(case_id: str, contract_keys: list, status: str) -> dict:
    """构建 result.json 信封骨架。"""
    result = {}
    for key in contract_keys:
        result[key] = None
    for key in ENVELOPE_KEYS:
        result[key] = [] if key in LIST_ENVELOPE_KEYS else None
    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {}
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    result["runtime"] = {"deterministic": True}
    return result


def derive_case_id(case: dict, library: dict) -> str:
    """从 case.json 或 library 稳定提取 case_id。"""
    if isinstance(case, dict):
        for key in ("case_id", "id", "case"):
            if case.get(key) not in (None, ""):
                return str(case[key])
    # 回退：材料文件名集合的哈希派生稳定 ID（未知但可复现）
    files = library.get("files", [])
    if files:
        joined = "\n".join(f["rel_path"] for f in files)
        import hashlib
        return "derived-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return "unknown"


def _evidence_for(files_by_id, *ids) -> list:
    out = []
    for i in ids:
        f = files_by_id.get(i)
        if f:
            out.append({"source_id": i, "locator": f["rel_path"], "quote": f["rel_path"]})
    return out


def mechanical_claims(library: dict) -> list:
    """从确定性分析生成机械 claim（quote 用 rel_path，逐字命中材料目录名）。"""
    claims = []
    analysis = library.get("analysis", {})
    files_by_id = {f["id"]: f for f in library.get("files", [])}
    n = 0
    for g in analysis.get("alias_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "directly_read",
            "confidence": "explicit",
            "text": f"文件 {', '.join(g['member_ids'])} 的 sha256 相同，为同一文件的副本/别名",
            "evidence": _evidence_for(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("version_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "inferred",
            "confidence": "heuristic",
            "text": f"文件 {', '.join(g['member_ids'])} 归一化 stem 相同，为同一文档的候选版本",
            "evidence": _evidence_for(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("conflicts", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "inferred",
            "confidence": "heuristic",
            "text": f"文件 {', '.join(g['member_ids'])} stem 相同但哈希不同且无版本标记，存在来源冲突",
            "evidence": _evidence_for(files_by_id, *g["member_ids"]),
        })
    return claims


def build_sources(library: dict) -> list:
    """把 library 文件清单转成 sources 列表（含 locator、sha256、类型）。"""
    out = []
    for f in library.get("files", []):
        out.append({
            "source_id": f["id"],
            "locator": f["rel_path"],
            "kind": f["kind"],
            "sha256": f["sha256"],
            "size": f["size"],
            "source": f["source"],
        })
    if library.get("case_parse") == "ok":
        out.append({
            "source_id": "case",
            "locator": "case.json",
            "kind": "input_meta",
            "sha256": None,
        })
    return out


def build_change_log(library: dict, draft: dict) -> list:
    """合并确定性与 draft 操作记录，形成可复核 change_log。"""
    log = []
    log.append({
        "op": "inventory", "target": "input", "reason": "确定性盘点",
        "before": None,
        "after": f"{len(library.get('files', []))} 个文件",
        "locator": "input",
    })
    notes = draft.get("notes", []) if isinstance(draft, dict) else []
    for note in notes:
        if not isinstance(note, dict):
            continue
        log.append({
            "op": note.get("kind", "note"),
            "target": note.get("target"),
            "reason": note.get("basis", ""),
            "after": note.get("text", ""),
            "locator": note.get("evidence", [{}])[0].get("locator") if note.get("evidence") else None,
            "confidence": note.get("confidence"),
        })
    return log


def build_uncertainties(library: dict, draft: dict) -> list:
    out = []
    analysis = library.get("analysis", {})
    for g in analysis.get("conflicts", []):
        out.append({
            "type": "source_conflict",
            "targets": g["member_ids"],
            "detail": "归一化 stem 相同但哈希不同且无显式版本标记",
        })
    notes = draft.get("notes", []) if isinstance(draft, dict) else []
    for note in notes:
        if isinstance(note, dict) and note.get("confidence") in ("inferred", "heuristic"):
            out.append({
                "type": "heuristic_inference",
                "target": note.get("target"),
                "detail": note.get("text", ""),
            })
    return out


def assemble_result(case_id: str, contract_keys: list, status: str,
                    library: dict, draft: dict, degradations: list,
                    errors: list) -> dict:
    """组装最终 result.json 信封。"""
    result = build_result(case_id, contract_keys, status)
    # 契约字段确定性回填（未知键保持 null，模型 draft 语义值在 build 后合并）。
    fill_contract(result, library, contract_keys)
    claims = mechanical_claims(library)
    draft_claims = _draft_claims(draft)
    claims.extend(draft_claims)
    result["claims"] = claims
    result["sources"] = build_sources(library)
    result["change_log"] = build_change_log(library, draft)
    result["uncertainties"] = build_uncertainties(library, draft)
    result["uncertainty"] = {
        "summary": _uncertainty_summary(result["uncertainties"]),
        "count": len(result["uncertainties"]),
        "items": result["uncertainties"],
    }
    result["result"] = {
        "summary": {
            "file_count": len(library.get("files", [])),
            "material_count": sum(1 for f in library.get("files", []) if f.get("source") == "material"),
            "alias_group_count": len(library.get("analysis", {}).get("alias_groups", [])),
            "version_group_count": len(library.get("analysis", {}).get("version_groups", [])),
            "conflict_count": len(library.get("analysis", {}).get("conflicts", [])),
            "query_count": len(library.get("analysis", {}).get("queries", [])),
        },
        "files": library.get("files", []),
        "analysis": library.get("analysis", {}),
        "index": library.get("index", {}),
    }
    result["pending_human"] = _pending_human(library, draft)
    result["degradations"] = degradations
    result["errors"] = errors + library.get("errors", [])
    result["runtime"] = {"deterministic": True, "contract_keys": len(contract_keys)}
    return result


def _draft_claims(draft: dict) -> list:
    out = []
    if not isinstance(draft, dict):
        return out
    notes = draft.get("notes", [])
    if not isinstance(notes, list):
        return out
    n = 0
    for note in notes:
        if not isinstance(note, dict):
            continue
        n += 1
        out.append({
            "claim_id": f"N{n:03d}",
            "kind": note.get("kind", "note"),
            "confidence": note.get("confidence", "inferred"),
            "text": note.get("text", ""),
            "basis": note.get("basis", ""),
            "evidence": note.get("evidence", []),
        })
    return out


def _uncertainty_summary(items: list) -> str:
    if not items:
        return "无明确不确定性"
    kinds = {}
    for it in items:
        kinds[it.get("type", "unknown")] = kinds.get(it.get("type", "unknown"), 0) + 1
    return "; ".join(f"{k}x{v}" for k, v in sorted(kinds.items()))


def _pending_human(library: dict, draft: dict) -> list:
    out = []
    for f in library.get("files", []):
        if f.get("hash_status") not in ("ok",):
            out.append({
                "target": f["id"],
                "locator": f["rel_path"],
                "issue": f["hash_status"],
                "suggest": "人工确认文件是否可读/是否需拆包",
            })
    for g in library.get("analysis", {}).get("conflicts", []):
        out.append({
            "target": g["member_ids"],
            "issue": "source_conflict",
            "suggest": "需人工裁定版本关系，不擅自判定",
        })
    return out
