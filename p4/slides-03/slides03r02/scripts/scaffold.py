#!/usr/bin/env python3
"""scaffold.py — 落盘 result.json 骨架并支持增量合并（仅标准库）。

职责：
1) 运行早期（前 180 秒）写出一版合法 partial result.json 骨架；
2) 之后所有内容更新经 --update / --update-file 只做顶层合并，不重写既有字段；
3) 任何中断都不留下非法或不完整产物；缺输入时仍写合法 insufficient_evidence 结果。

用法示例：
  python3 scripts/scaffold.py --input /input --task /input/task.md --work /app/work --output /app/output
  python3 scripts/scaffold.py --update '{"pages": [...]}' --output /app/output
  python3 scripts/scaffold.py --checkpoint P2_narrative --output /app/output
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

CONTENT_CONTRACT_DEFAULT = [
    "report_goal", "audience", "narrative", "pages",
    "editable_deck", "evidence_map", "qa_prep", "rendering",
]
LIST_FIELDS = {"narrative", "pages", "evidence_map", "qa_prep"}
VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 单文件读入上限 2MB


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _log(result, action, scope):
    result.setdefault("change_log", [])
    result["change_log"].append({
        "ts": _now_iso(),
        "epoch": time.time(),
        "action": action,
        "scope": scope,
    })


def read_text(path, cap=MAX_FILE_SIZE):
    """容错读取文本：BOM/坏编码降级，超限跳过。返回 (text, error)。"""
    try:
        size = os.path.getsize(path)
        if size > cap:
            return None, "skipped: exceeds {0} bytes".format(cap)
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "unreadable: {0}".format(e)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded with replacement"


def load_json(path):
    """容错读取 JSON。返回 (obj, error)。"""
    if not os.path.exists(path):
        return None, "missing: {0}".format(path)
    text, err = read_text(path)
    if err is not None and text is None:
        return None, err
    if text is None:
        text = ""
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "bad json: {0}".format(e)


def _guess_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        return "image"
    if ext in (".pptx", ".odp", ".html", ".htm"):
        return "deck"
    if ext == ".json":
        return "json"
    if ext in (".md", ".txt"):
        return "text"
    return "other"


def discover_materials(input_dir):
    """扫描输入目录，收集材料清单（≤2MB；超限/不可读如实标注）。"""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir)
            try:
                size = os.path.getsize(p)
            except OSError as e:
                items.append({"path": rel, "size": None, "type": "file",
                              "read": False, "note": "unreadable: {0}".format(e)})
                continue
            if size > MAX_FILE_SIZE:
                items.append({"path": rel, "size": size, "type": "file",
                              "read": False, "note": "exceeds 2MB"})
                continue
            items.append({"path": rel, "size": size, "type": _guess_type(name),
                          "read": True, "note": None})
    return items


def resolve_case_id(case):
    if not isinstance(case, dict):
        return "unknown"
    return case.get("id") or case.get("armiId") or "unknown"


def resolve_content_keys(case):
    oc = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(oc, dict) and oc:
        keys = list(oc.keys())
        for k in CONTENT_CONTRACT_DEFAULT:
            if k not in keys:
                keys.append(k)
        return keys, "case.json.output_contract"
    return list(CONTENT_CONTRACT_DEFAULT), "default"


def build_skeleton(case, input_dir, task_path=None):
    """构造一版合法 partial 结果：回填契约字段 + 包裹字段。"""
    content_keys, contract_source = resolve_content_keys(case)
    result = {}
    for k in content_keys:
        result[k] = [] if k in LIST_FIELDS else None
    result["case_id"] = resolve_case_id(case)
    result["status"] = "partial"
    result["sources"] = discover_materials(input_dir)
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = []
    if task_path and os.path.exists(task_path):
        task_base = os.path.basename(task_path)
        # 去除材料扫描中重复的 task 文件，避免同一文件出现两次
        result["sources"] = [m for m in result["sources"]
                             if os.path.basename(m.get("path", "")) != task_base]
        _t, _e = read_text(task_path)
        result["sources"].insert(0, {
            "path": task_base, "type": "task",
            "read": _e is None, "note": _e,
        })
    result["runtime"] = {
        "scaffolded_at": _now_iso(),
        "scaffolded_epoch": time.time(),
        "phase": "P1_scaffold",
        "contract_source": contract_source,
        "content_keys": content_keys,
    }
    _log(result, "scaffold", "result.json skeleton")
    return result


def merge_update(result, patch):
    """顶层合并：patch 覆盖同名字段；case_id 与 change_log 受保护。"""
    if not isinstance(patch, dict):
        return result
    protected_case_id = result.get("case_id")
    for k, v in patch.items():
        if k == "change_log":
            continue
        if k == "case_id" and v in (None, "", "unknown"):
            continue
        result[k] = v
    if protected_case_id:
        result["case_id"] = protected_case_id
    _log(result, "update", "merged patch keys: " + ", ".join(sorted(patch.keys())))
    return result


def write_result(result, output_dir):
    """原子写盘：临时文件 + rename，保证 result.json 始终完整。"""
    os.makedirs(output_dir, exist_ok=True)
    out = os.path.join(output_dir, "result.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="落盘 result.json 骨架并增量合并（仅标准库）")
    ap.add_argument("--input", default="/input", help="输入目录，默认 /input")
    ap.add_argument("--task", default="/input/task.md", help="task.md 路径")
    ap.add_argument("--work", default="/app/work", help="工作目录，默认 /app/work")
    ap.add_argument("--output", default="/app/output", help="输出目录，默认 /app/output")
    ap.add_argument("--update", default=None, help="JSON 字符串 patch（顶层合并）")
    ap.add_argument("--update-file", default=None, help="JSON 文件 patch")
    ap.add_argument("--checkpoint", default=None, help="标记当前阶段，如 P2_narrative")
    a = ap.parse_args(argv)

    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)

    case_path = os.path.join(a.input, "case.json")
    if not os.path.exists(case_path) and a.input.endswith(".json") and os.path.exists(a.input):
        case_path = a.input
    case, err = load_json(case_path)
    if case is None:
        case = {}

    result_path = os.path.join(a.output, "result.json")
    if os.path.exists(result_path):
        result, rerr = load_json(result_path)
        if result is None:
            result = build_skeleton(case, a.input, a.task)
            result.setdefault("uncertainty", []).append("result.json 损坏，已重建骨架: {0}".format(rerr))
    else:
        result = build_skeleton(case, a.input, a.task)
        if err:
            result.setdefault("uncertainty", []).append("case.json: {0}".format(err))

    patch = None
    if a.update:
        try:
            patch = json.loads(a.update)
        except ValueError as e:
            result.setdefault("uncertainty", []).append("update 解析失败: {0}".format(e))
    if a.update_file:
        p2, e2 = load_json(a.update_file)
        if p2 is not None:
            patch = p2 if patch is None else {**patch, **p2}
        else:
            result.setdefault("uncertainty", []).append("update-file: {0}".format(e2))
    if patch is not None:
        merge_update(result, patch)

    if a.checkpoint:
        rt = result.setdefault("runtime", {})
        rt["phase"] = a.checkpoint
        rt["phase_at"] = _now_iso()
        _log(result, "checkpoint", a.checkpoint)

    out = write_result(result, a.output)
    print(json.dumps({
        "ok": True,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "result": out,
        "phase": result.get("runtime", {}).get("phase"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
