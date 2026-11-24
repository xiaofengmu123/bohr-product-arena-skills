#!/usr/bin/env python3
"""Bootstrap a valid partial result.json and editable deliverable skeletons.

Reads /input/case.json and task.md, discovers provided materials, and writes
under the output directory:

  result.json          -- status=partial, every contract field present
  revision_ledger.json -- machine-readable comment ledger skeleton
  revised_manuscript.md / response_letter.md / modification_map.md / evidence_ledger.md

Standard library only. Never fabricates case data; missing input degrades to an
honest `partial` result instead of crashing.
"""
import argparse
import datetime
import json
import os
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tex", ".yaml", ".yml"}


def now_ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """Read UTF-8 text tolerantly (BOM, bad encoding fallback)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError as exc:
        return None


def read_json(path):
    """Read JSON tolerantly; return ({}, [errors]) on failure."""
    errors = []
    if not path or not os.path.exists(path):
        return {}, ["missing: %s" % (path or "<none>")]
    text = read_text(path)
    if text is None:
        return {}, ["unreadable: %s" % path]
    try:
        data = json.loads(text)
        return data, errors
    except json.JSONDecodeError as exc:
        # try stripping a leading BOM / trailing junk
        try:
            data = json.loads(text.lstrip("\ufeff"))
            errors.append("decoded after BOM strip: %s" % path)
            return data, errors
        except json.JSONDecodeError:
            errors.append("bad JSON %s: %s" % (path, exc))
            return {}, errors


def discover_materials(input_dir):
    """Walk /input, list readable files with size caps, never assume content."""
    items = []
    skipped = []
    if not os.path.isdir(input_dir):
        return items, skipped
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                skipped.append({"ref": rel, "reason": "unreadable"})
                continue
            ext = os.path.splitext(name)[1].lower()
            if size > MAX_FILE_BYTES:
                skipped.append({"ref": rel, "reason": "size>2MB", "bytes": size})
                continue
            if ext in TEXT_EXTS:
                head = (read_text(full) or "")[:200]
                items.append({"ref": rel, "type": "text", "bytes": size,
                              "preview": head.replace("\n", " ")[:120]})
            else:
                items.append({"ref": rel, "type": ext.lstrip(".") or "binary", "bytes": size})
    return items, skipped


def resolve_case_id(case):
    for key in ("case_id", "id", "challenge_id"):
        val = case.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return "unknown"


def build_skeleton(case, task_text, materials, skipped, errors):
    case_id = resolve_case_id(case)
    out = os.path.join("/app/output")
    artifacts = {
        "revised_manuscript": {"path": out + "/revised_manuscript.md", "title": "修订稿",
                               "sections": ["基线保留", "按意见修订", "待人工确认"]},
        "response_letter": {"path": out + "/response_letter.md", "title": "逐条回复信草稿",
                            "sections": ["总体说明", "逐条回应", "未到位事项"]},
        "modification_map": {"path": out + "/modification_map.md", "title": "修改定位表",
                             "sections": ["意见编号", "改动内容", "定位锚点", "状态"]},
        "evidence_ledger": {"path": out + "/evidence_ledger.md", "title": "证据清单",
                            "sections": ["补证需求", "到位状态", "出处或无法补做说明"]},
    }
    result = {
        "case_id": case_id,
        "deliverable": {
            "revised_manuscript": artifacts["revised_manuscript"],
            "response_letter": artifacts["response_letter"],
            "modification_map": artifacts["modification_map"],
            "evidence_ledger": artifacts["evidence_ledger"],
            "format": "markdown",
            "status": "draft",
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [],
            "pending_confirmation": [],
            "material_gaps": [],
            "anomalies": [],
        },
        "status": "partial",
        "sources": [{"id": "case", "type": "input_json", "ref": "/input/case.json",
                     "description": "任务与约束来源"}],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"ts": now_ts(), "action": "scaffold",
                        "detail": "落盘 partial 骨架；未填充事实内容"}],
        "runtime": {"limits": "骨架阶段：意见拆解、逐条回应、证据清单尚未完成"},
    }
    if task_text:
        result["sources"].append({"id": "task", "type": "task_md", "ref": "/input/task.md",
                                  "description": "体裁/受众/格式约束"})
    if not materials:
        result["version_boundary"]["material_gaps"].append(
            "未在 /input 下发现可读材料文件（审稿意见清单或稿件基线）")
    if errors:
        result["version_boundary"]["anomalies"].extend(errors)
    if skipped:
        result["version_boundary"]["anomalies"].append(
            "跳过的文件: " + "; ".join(s["ref"] for s in skipped))
    return result, artifacts, materials


def write_skeleton_files(artifacts, out_dir):
    written = []
    for key, meta in artifacts.items():
        path = os.path.join(out_dir, os.path.basename(meta["path"]))
        if key == "revised_manuscript":
            body = "# 修订稿（骨架，待填充）\n\n<!-- 按意见逐条修订指定基线；改动处用“旧→新”锚点标注，勿新增材料外事实 -->\n"
        elif key == "response_letter":
            body = "# 逐条回复信草稿（骨架，待填充）\n\n<!-- 每条意见：结论+理由+改动定位+语气；禁止空泛致谢 -->\n"
        elif key == "modification_map":
            body = "# 修改定位表（骨架，待填充）\n\n| 意见编号 | 改动内容 | 定位锚点(旧→新) | 状态 |\n|-|-|-|-|\n"
        else:
            body = "# 证据清单（骨架，待填充）\n\n| 补证需求 | 到位状态 | 出处/无法补做说明 |\n|-|-|-|\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        meta["path"] = path
        written.append(path)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bootstrap partial result.json + skeletons")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    input_dir = os.path.dirname(os.path.abspath(a.case))
    case, case_errors = read_json(a.case)
    task_text = read_text(a.task) if os.path.exists(a.task) else None
    materials, skipped = discover_materials(input_dir)

    result, artifacts, _materials = build_skeleton(case, task_text, materials, skipped, case_errors)
    result["deliverable"]["status"] = "partial"

    written = write_skeleton_files(artifacts, a.out)

    # ledger skeleton
    ledger_path = os.path.join(a.out, "revision_ledger.json")
    ledger = {"case_id": result["case_id"], "comments": [],
              "_note": "每项见 references/methodology.md 第3节字段；由模型逐条回填"}
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    out_path = os.path.join(a.out, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "case_id": result["case_id"],
        "status": result["status"],
        "result": out_path,
        "deliverables": written,
        "ledger": ledger_path,
        "materials_found": len(materials),
        "skipped": skipped,
        "errors": case_errors,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
