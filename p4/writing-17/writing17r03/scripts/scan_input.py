#!/usr/bin/env python3
"""scan_input.py — 安全发现并归一化运行期输入，先落盘一份合法的 partial 结果骨架。

用途
----
1. 读取 /input/case.json 与 /input/task.md，并发现 /input 下的科研材料文件。
2. 从 case.json 中提取 case_id 与 output_contract（容许多种键名）。
3. 把输出契约的全部键原样回填到 /app/output/result.json，缺值用 null（列表型用 []）。
4. 在 /app/work 写 input_manifest.json，供后续 render / validate 脚本读取。

只用 Python 标准库；UTF-8-sig / GB18030 / latin-1 逐级降级；坏 JSON 只记录不崩溃。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".tex", ".bib"}
BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp",
              ".docx", ".pptx", ".xlsx", ".pdfx"}

# 名称含这些关键词的契约字段，缺省回填为 []，其余回填 null。
LIST_HINTS = ("sources", "references", "refs", "list", "items", "nodes", "edges",
              "relations", "materials", "changes", "errors", "warnings", "pending",
              "uncertainty", "figures", "tables", "quotes", "evidence", "tasks", "risks",
              "requirements", "outputs", "artifacts", "files", "links", "tags", "keywords")

CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "result_schema",
                 "resultSchema", "output_schema", "outputSchema", "response_schema")

CASE_ID_KEYS = ("case_id", "caseId", "id", "challenge_id", "challengeId", "task_id", "taskId")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error", "in_progress"}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path):
    """按多种编码读取文本；全部失败返回 None（latin-1 永不会失败，但只作最后兜底）。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


def parse_json_tolerant(path):
    """解析 JSON，失败返回 (None, error_string)。"""
    raw = read_text(path)
    if raw is None:
        return None, "unreadable file"
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return None, "empty file"
    try:
        return json.loads(raw), None
    except Exception as exc:  # noqa: BLE001
        return None, "json parse error: %s" % exc


def discover_inputs(base):
    """返回 (case_path, task_path, materials) 三个元素，其中 materials 为列表。"""
    case_path, task_path = None, None
    materials = []
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            if name == "case.json" and case_path is None:
                case_path = full
                continue
            if name.lower() == "task.md" and task_path is None:
                task_path = full
                continue
            if ext in TEXT_EXT or ext in BINARY_EXT:
                materials.append({"path": full, "rel": rel, "ext": ext, "name": name})
    return case_path, task_path, materials


def material_meta(path):
    st = None
    try:
        st = os.stat(path)
    except OSError:
        pass
    size = st.st_size if st else 0
    ext = os.path.splitext(path)[1].lower()
    kind = "binary"
    if ext in TEXT_EXT:
        kind = "text"
    elif ext == ".pdf":
        kind = "pdf"
    return {"path": path, "ext": ext, "kind": kind, "size": size,
            "oversize": size > MAX_FILE_BYTES}


def default_for_key(key):
    low = key.lower()
    return [] if any(h in low for h in LIST_HINTS) else None


def fill_contract_defaults(contract):
    """把契约键回填为缺省值；契约本身是 dict 时逐键回填，否则整体置 null。"""
    if isinstance(contract, dict) and contract:
        out = {}
        for k in contract.keys():
            out[k] = default_for_key(str(k))
        return out
    return None


def pick_contract(case_data):
    if not isinstance(case_data, dict):
        return None, None
    for key in CONTRACT_KEYS:
        if key in case_data:
            return case_data[key], key
    # 兜底：case.json 顶层即契约（此时没有单独契约字段）
    return case_data, "top-level"


def pick_case_id(case_data, fallback_path=None):
    if isinstance(case_data, dict):
        for key in CASE_ID_KEYS:
            if key in case_data and case_data[key] not in (None, ""):
                return str(case_data[key])
    return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--input", default=DEFAULTS["input"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    started = time.time()
    errors, warnings, degradations = [], [], []

    case_path, task_path, materials = discover_inputs(args.input)

    case_data, case_err = (None, "case.json not found")
    if case_path:
        case_data, case_err = parse_json_tolerant(case_path)
    if case_err:
        errors.append({"stage": "scan_input", "detail": case_err,
                       "locator": "case.json"})
    if not case_path:
        warnings.append("no /input/case.json found; proceeding with skeleton")

    contract, contract_source = pick_contract(case_data)
    case_id = pick_case_id(case_data, case_path)

    task_text = None
    if task_path:
        task_text = read_text(task_path)
        if task_text is None:
            errors.append({"stage": "scan_input", "detail": "task.md unreadable",
                           "locator": "task.md"})
    else:
        warnings.append("no /input/task.md found")

    material_entries = []
    for m in materials:
        meta = material_meta(m["path"])
        meta.update({"rel": m["rel"], "name": m["name"]})
        if meta["oversize"]:
            warnings.append("skipped oversize file (>2MB): %s" % m["rel"])
        else:
            if meta["kind"] == "text":
                txt = read_text(m["path"])
                meta["readable"] = txt is not None
                meta["chars"] = len(txt) if txt else 0
                if txt is None:
                    degradations.append("unreadable text file: %s" % m["rel"])
            else:
                meta["readable"] = None
                meta["chars"] = None
            material_entries.append(meta)

    # 状态判定
    if case_data is None or not materials:
        status = "insufficient_evidence" if (not materials) else "input_error"
    else:
        status = "in_progress"

    manifest = {
        "generated_at": now_iso(),
        "case_id": case_id,
        "output_contract": contract if isinstance(contract, dict) else None,
        "contract_source": contract_source,
        "inputs": {
            "case_json": {"path": case_path, "rel": "case.json", "ok": case_data is not None},
            "task_md": {"path": task_path, "rel": "task.md", "ok": task_text is not None,
                        "chars": len(task_text) if task_text else 0},
            "materials": material_entries,
        },
        "warnings": warnings,
        "errors": errors,
        "degradations": degradations,
        "task_md_excerpt": (task_text or "")[:4000],
    }

    # 先写 result 骨架：合法、含契约全部键，之后只做增量改进。
    result = fill_contract_defaults(contract)
    if not isinstance(result, dict):
        result = {}
    result["case_id"] = case_id
    result["status"] = status
    for key in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(key), list):
            result[key] = []
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["runtime"] = {
        "started_at": now_iso(),
        "seconds_elapsed": round(time.time() - started, 1),
        "budget": {"model_requests": 64, "seconds": 900, "tokens_per_response": 16384},
    }
    result["change_log"].append({
        "time": now_iso(), "step": "skeleton",
        "note": "scan_input.py 写入 partial 骨架；输出契约键已回填默认值，待后续步骤填充",
    })

    result_path = os.path.join(args.output, "result.json")
    manifest_path = os.path.join(args.work, "input_manifest.json")

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "case_id": case_id,
        "status": status,
        "contract_source": contract_source,
        "contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
        "material_count": len(material_entries),
        "result": result_path,
        "manifest": manifest_path,
        "errors": len(errors),
        "warnings": len(warnings),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
