#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性文献综述 Skill 的核心库（仅 Python 标准库）。

本模块被 `main.py`（Phase 0 骨架）与 `selftest.py`（自检）共用，提供：
  1. 容错的输入发现与解析（空输入 / 坏 JSON / 坏编码 / 缺软件均不崩溃）；
  2. output_contract 的发现、归一化与空值回填（不改名、不删键）；
  3. 材料盘点、按内容哈希去重、PDF 文本抽取；
  4. 规范 JSON 序列化 + 仅当字节变化才落盘的增量写入；
  5. 由输入内容哈希派生的 run_id（不用墙钟时间，保证可复现）。

确定性约定：
  - 文件发现一律按相对路径排序；列表按稳定键排序；
  - JSON 使用 `sort_keys=True`、`ensure_ascii=False`、固定缩进；
  - 产物不写入墙钟时间戳，仅写 `run_id` 与固定预算常量；
  - 同一输入两次运行，脚本生成的骨架产物字节级一致。
"""

import hashlib
import json
import os
import subprocess

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
STRENGTH_LEVELS = {"established", "suggestive", "disputed", "not_reported"}
MAX_FILE_BYTES = 2 * 1024 * 1024

PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json", "manifest.json"}

BUDGET = {"seconds": 900, "model_requests": 64, "max_tokens_per_response": 16384}

ARTIFACT_NAMES = [
    "result.json",
    "review.md",
    "screening_log.json",
    "evidence_table.jsonl",
    "themes.md",
    "conflicts_and_gaps.md",
]


# ---------------------------------------------------------------------------
# 序列化与写入（确定性）
# ---------------------------------------------------------------------------
def canonical_bytes(data):
    """把 dict/list 序列化为规范的 UTF-8 字节；键排序保证同数据同字节。"""
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")
    ).encode("utf-8")


def _ensure_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def _write_bytes(path, data):
    """仅当目标文件字节不同才重写；返回是否发生写入。"""
    _ensure_dir(path)
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except FileNotFoundError:
        pass
    with open(path, "wb") as f:
        f.write(data)
    return True


def write_json_atomic(path, data):
    return _write_bytes(path, canonical_bytes(data))


def write_text_atomic(path, text):
    return _write_bytes(path, text.encode("utf-8"))


# ---------------------------------------------------------------------------
# 容错读取
# ---------------------------------------------------------------------------
def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取，返回 (文本, 编码, 错误)。"""
    last = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc, None
        except UnicodeDecodeError as e:
            last = str(e)
        except OSError as e:
            return None, None, str(e)
    return None, None, last


def read_json(path):
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取: " + (err or "未知错误")]
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, ["JSON 解析失败: " + str(e)]


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
def walk_files(root):
    """递归列出文件，目录与文件名均按字典序排序，保证顺序稳定。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            out.append(os.path.join(dirpath, fn))
    return out


def find_case_file(input_dir):
    """先找 case.json；再找任一含 output_contract 的 .json；找不到返回 None。"""
    direct = os.path.join(input_dir, "case.json")
    if os.path.isfile(direct):
        return direct
    for full in walk_files(input_dir):
        if os.path.basename(full).lower() == "case.json":
            return full
    for full in walk_files(input_dir):
        if full.lower().endswith(".json"):
            data, errs = read_json(full)
            if data is not None and errs is None and find_output_contract(data) is not None:
                return full
    return None


# ---------------------------------------------------------------------------
# output_contract 解析与回填
# ---------------------------------------------------------------------------
def default_for_spec(spec):
    """依据 schema 提示给数组 []、对象 {}，其余 null。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        types = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        flat = [str(x).lower() for x in types]
        if "array" in flat:
            return []
        if "object" in flat:
            return {}
        if "items" in spec or "properties" in spec:
            return []
    return None


def contract_fields(contract):
    """把多种形态的 output_contract 归一化为 [(字段名, 空值), ...]。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fields = contract.get("fields")
        if fields is not None:
            return contract_fields(fields)
        return [(name, default_for_spec(contract[name])) for name in contract]
    if isinstance(contract, list):
        out = []
        for item in contract:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                name = (item.get("name") or item.get("field_id")
                        or item.get("id") or item.get("key") or item.get("field"))
                if name:
                    out.append((name, default_for_spec(item)))
        return out
    if isinstance(contract, str) and contract.strip():
        return [(contract.strip(), None)]
    return None


def find_output_contract(node):
    """递归查找 output_contract / output_schema。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in ("output_contract", "outputcontract",
                               "output_schema", "outputschema"):
                return value
        for value in node.values():
            found = find_output_contract(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_output_contract(item)
            if found is not None:
                return found
    return None


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "challenge_id"):
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if isinstance(case, dict):
        for key in ("research_question", "question", "query", "prompt", "title", "objective"):
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        task = case.get("task")
        if isinstance(task, dict):
            for key in ("research_question", "question", "prompt", "description",
                        "title", "objective"):
                value = task.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


# ---------------------------------------------------------------------------
# 材料盘点与去重
# ---------------------------------------------------------------------------
def material_inventory(input_dir, max_bytes=MAX_FILE_BYTES):
    items = []
    for full in walk_files(input_dir):
        base = os.path.basename(full)
        if base.lower() in META_NAMES:
            continue
        ext = os.path.splitext(full)[1].lower()
        if ext not in MATERIAL_EXTS:
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        items.append({
            "relpath": os.path.relpath(full, input_dir).replace("\\", "/"),
            "abspath": full,
            "ext": ext,
            "size": size,
            "sha256": sha256_file(full),
            "skipped": size > max_bytes,
        })
    items.sort(key=lambda x: x["relpath"])
    dedup_materials(items)
    return items


def dedup_materials(items):
    """按内容哈希标记重复材料，重复项写 duplicate_of，不删除任何记录。"""
    first_by_hash = {}
    for item in items:
        h = item.get("sha256")
        if h and not item.get("skipped"):
            if h in first_by_hash:
                item["duplicate_of"] = first_by_hash[h]
            else:
                first_by_hash[h] = item["relpath"]
    return items


def extract_pdf(pdf_path, out_txt):
    """调用 pdftotext 抽取 PDF 文本；失败返回 (False, 原因)，不伪造结果。

    注意：失败原因必须"路径无关"，避免把临时目录绝对路径写进结果，
    否则同一输入换一个 work 目录跑，result.json 会因路径不同而失去确定性。
    """
    try:
        subprocess.run(
            ["pdftotext", "-enc", "UTF-8", pdf_path, out_txt],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True, None
    except FileNotFoundError:
        return False, "pdftotext 未安装"
    except subprocess.CalledProcessError as e:
        return False, "pdftotext 退出码 %d" % e.returncode
    except OSError:
        return False, "无法执行 pdftotext（OSError）"


# ---------------------------------------------------------------------------
# run_id 与骨架构建
# ---------------------------------------------------------------------------
def build_run_id(case_id, question, fields, materials):
    payload = {
        "case_id": case_id,
        "question": question,
        "contract": sorted([(name, default) for name, default in (fields or [])]),
        "materials": sorted(
            [(m["relpath"], m.get("sha256"), m.get("size")) for m in materials]
        ),
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:16]


def _skeleton_artifacts(status, case_error):
    review = (
        "# 综述草稿（骨架）\n\n"
        "> 本文件由 `scripts/main.py` 在 Phase 0 生成，尚无实质论断。"
        "Phase 3 将基于证据表逐段写入带 `[S#]` 引用的草稿；"
        "每一句实质论断必须指向 `evidence_table.jsonl` 中可解析的证据行。\n\n"
        "## 研究问题\n（Phase 1 从 `/input/case.json` 与材料确认后回填）\n\n"
        "## 草稿正文\n（待 Phase 3 生成；禁止在无证据时写入实质论断）\n"
    )
    themes = (
        "# 主题归纳（骨架）\n\n"
        "> Phase 0 骨架。Phase 2 从证据表聚类生成主题与子主题，"
        "每个主题列出支撑文献与边界；不套用背景/方法/挑战/展望的通用框架。\n"
    )
    conflicts = (
        "# 冲突与研究空白（骨架）\n\n"
        "> Phase 0 骨架。Phase 3 将并列呈现结论冲突（标 `disputed`），"
        "并把研究空白落到具体证据缺口；禁止用套话收尾。\n"
    )
    screening = {
        "criteria": {
            "population": None,
            "include": [],
            "exclude": [],
            "dedup_rule": None,
            "defined_before_decisions": True,
        },
        "decisions": [],
        "duplicates": [],
        "sources": [],
    }
    return review, themes, conflicts, screening


def build_skeleton(input_dir, output_dir, work_dir, max_bytes=MAX_FILE_BYTES):
    """Phase 0：盘点输入并落盘合法骨架。永远不抛异常，返回 (result, ok)。"""
    errors = []
    degradations = []
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_path = find_case_file(input_dir)
    case_data = None
    case_error = None
    if case_path:
        case_data, errs = read_json(case_path)
        if case_data is None:
            case_error = "; ".join(errs or ["未知错误"])
            errors.append({"kind": "case_json_unreadable", "detail": case_error})
    else:
        errors.append({"kind": "case_json_missing", "detail": "未发现 case.json"})

    case_id = extract_case_id(case_data, case_path or os.path.join(input_dir, "case.json"))
    question = extract_question(case_data)
    contract = find_output_contract(case_data) if case_data is not None else None
    fields = contract_fields(contract) if contract is not None else None

    materials = material_inventory(input_dir, max_bytes)

    extracted_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)
    for item in materials:
        if item.get("skipped"):
            degradations.append({
                "kind": "file_too_large",
                "relpath": item["relpath"],
                "limit_bytes": max_bytes,
            })
            continue
        if item.get("ext") == ".pdf" and item.get("sha256"):
            base = os.path.splitext(item["relpath"].replace("/", "__").replace("\\", "__"))[0]
            out_txt = os.path.join(extracted_dir, base + ".txt")
            ok, err = extract_pdf(item["abspath"], out_txt)
            if ok:
                item["extracted_text"] = "extracted/" + os.path.basename(out_txt)
            else:
                degradations.append({
                    "kind": "pdf_extract_failed",
                    "relpath": item["relpath"],
                    "detail": err,
                })

    active = [m for m in materials if not m.get("skipped") and m.get("sha256")]
    if case_error:
        status = "input_error"
    elif not active:
        status = "insufficient_evidence"
    else:
        status = "partial"

    run_id = build_run_id(case_id, question, fields, materials)

    uncertainty = []
    pending = []
    if status == "input_error":
        uncertainty.append({"kind": "unparseable_input", "detail": case_error or "case.json 缺失"})
        pending.append("修复或补充 /input/case.json 后重跑")
    elif status == "insufficient_evidence":
        uncertainty.append({"kind": "no_materials", "detail": "输入目录未发现可读取的材料文件"})
        pending.append("确认 /input/materials 或 case.json 中给出的材料路径")
    else:
        uncertainty.append({
            "kind": "phase0_skeleton",
            "detail": "筛选、证据抽取、主题归纳、草稿尚未执行，需在 Phase 1–4 完成",
        })

    result = {"case_id": case_id}
    if fields:
        for name, default in fields:
            result[name] = default
    result["status"] = status
    result["artifacts"] = {
        "review_md": "review.md",
        "screening_log_json": "screening_log.json",
        "evidence_table_jsonl": "evidence_table.jsonl",
        "themes_md": "themes.md",
        "conflicts_and_gaps_md": "conflicts_and_gaps.md",
        "manifest_json": "manifest.json",
    }
    result["sources"] = []
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending
    result["change_log"] = [{
        "seq": 0,
        "stage": "phase0_skeleton",
        "status": status,
        "note": "main.py 生成确定性骨架，等待 Agent 增量补全",
    }]
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = {
        "run_id": run_id,
        "deterministic": True,
        "phase": 0,
        "budget": BUDGET,
    }

    review, themes, conflicts, screening = _skeleton_artifacts(status, case_error)
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "review.md"), review)
    write_json_atomic(os.path.join(output_dir, "screening_log.json"), screening)
    write_text_atomic(os.path.join(output_dir, "evidence_table.jsonl"), "")
    write_text_atomic(os.path.join(output_dir, "themes.md"), themes)
    write_text_atomic(os.path.join(output_dir, "conflicts_and_gaps.md"), conflicts)

    inventory = {
        "run_id": run_id,
        "case_id": case_id,
        "question": question,
        "contract_fields": [{"name": n, "default": d} for n, d in (fields or [])],
        "materials": materials,
    }
    write_json_atomic(os.path.join(work_dir, "inventory.json"), inventory)

    manifest = {
        "run_id": run_id,
        "deterministic": True,
        "files": {
            name: (sha256_file(os.path.join(output_dir, name)) or "")
            for name in ARTIFACT_NAMES
        },
    }
    write_json_atomic(os.path.join(output_dir, "manifest.json"), manifest)

    return result, True
