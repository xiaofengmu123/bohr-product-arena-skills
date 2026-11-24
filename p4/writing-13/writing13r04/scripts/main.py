#!/usr/bin/env python3
"""Step 0 skeleton builder for grant-writing evidence-verifier Skill.

Reads case.json (tolerant to BOM / bad JSON), scans the input directory,
computes SHA256 source manifest, and writes:
  - /app/output/result.json  (partial skeleton, never empty)
  - /app/output/draft.md     (seven-element editable section template)
  - /app/work/source_manifest.json
  - /app/work/contract_hint.json

Usage:
  python3 scripts/main.py [--case /input/case.json] [--input /input]
                          [--out /app/output] [--work /app/work]

Exit codes: 0 = ok (including empty/missing input, which yields a valid
`insufficient_evidence` result), 1 = unrecoverable output write failure.
Pure standard library.
"""
import argparse
import hashlib
import json
import os
import sys
import time

DEFAULT_CONTRACT = ["artifact", "fact_source_map", "version_boundary"]
SEVEN_ELEMENTS = [
    "立项依据",
    "科学问题",
    "研究目标",
    "研究内容",
    "技术路线",
    "创新点",
    "研究基础与可行性",
]
MAX_SOURCE_BYTES = 2 * 1024 * 1024  # skip oversized files, record as degradation


def candidate_input_dirs():
    """Return existing input dirs in platform-first order with local fallback."""
    for d in ("/input", "input", "../input", "../../input"):
        if d and os.path.isdir(d):
            yield d


def candidate_case_files(input_dir):
    for name in ("case.json", "task.json"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            yield p


def load_json(path):
    """Tolerant JSON loader. Returns (obj_or_None, error_or_None)."""
    if not path or not os.path.isfile(path):
        return None, "missing: %s" % path
    raw = None
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            raw = data.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if raw is None:
        return None, "undecodable: %s" % path
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, "bad json: %s" % e


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def scan_sources(input_dir, degradations):
    sources = []
    if not os.path.isdir(input_dir):
        return sources
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError as e:
                degradations.append("stat failed %s: %s" % (p, e))
                continue
            if size > MAX_SOURCE_BYTES:
                degradations.append("skipped oversized %s (%d bytes)" % (p, size))
                continue
            ext = os.path.splitext(name)[1].lower()
            try:
                sources.append({
                    "path": p,
                    "size": size,
                    "sha256": sha256_of(p),
                    "kind": ext.lstrip(".") or "unknown",
                })
            except OSError as e:
                degradations.append("hash failed %s: %s" % (p, e))
    return sources


def build_skeleton(case, case_path, sources, degradations, out_dir, work_dir):
    errors = []
    contract_hint = {}
    case_id = None
    status = "in_progress"
    if case is not None:
        case_id = case.get("id") or case.get("case_id") or case.get("challenge_id")
        for key in ("output_contract", "output_schema", "required_fields"):
            if key in case:
                contract_hint[key] = case[key]
    else:
        # case read failed; case_id derived from filename to keep idempotent key
        case_id = os.path.splitext(os.path.basename(case_path or "case.json"))[0]
        status = "input_error"
        if case_path:
            errors.append(load_json(case_path)[1] if load_json(case_path)[1] else "case.json unreadable")

    # Determine contract fields to echo (default when none declared).
    declared = None
    contract_keys = list(DEFAULT_CONTRACT)
    for key in ("output_contract", "output_schema", "required_fields"):
        val = contract_hint.get(key)
        if isinstance(val, dict):
            declared = val
            contract_keys = [k for k in val.keys() if k] or contract_keys
            break
        if isinstance(val, list):
            declared = {str(k): None for k in val if str(k)}
            contract_keys = list(declared.keys()) or contract_keys
            break

    defaults = {
        "artifact": {"path": None, "kind": "grant_section_draft",
                     "format": "markdown", "rendered_previews": []},
        "fact_source_map": [],
        "version_boundary": {"modification_diff": [], "manual_confirm": [],
                             "material_gaps": [], "anomalies": []},
    }
    contract_obj = dict(declared) if declared is not None else {
        k: defaults.get(k) for k in DEFAULT_CONTRACT}
    # Ensure writable slots even when a declared contract left them null.
    if not isinstance(contract_obj.get("artifact"), dict):
        contract_obj["artifact"] = defaults["artifact"]
    if not isinstance(contract_obj.get("version_boundary"), dict):
        contract_obj["version_boundary"] = defaults["version_boundary"]
    if "fact_source_map" not in contract_obj:
        contract_obj["fact_source_map"] = []

    if status == "in_progress" and not sources:
        status = "insufficient_evidence"

    result = {
        "case_id": case_id,
        "status": status,
        "artifact": contract_obj.get("artifact", None),
        "fact_source_map": contract_obj.get("fact_source_map", []),
        "version_boundary": contract_obj.get("version_boundary", None),
        "seven_elements": {
            "covered": {e: False for e in SEVEN_ELEMENTS},
            "consistency": {
                "objective_count": None,
                "content_count": None,
                "route_count": None,
                "indicator_count": None,
                "aligned": None,
            },
        },
        "sources": sources,
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{
            "ts": utc_now(), "action": "skeleton_created",
            "reason": "Step 0: 先落盘 partial 骨架，后续增量改进",
            "effect": "result.json/draft.md/source_manifest.json 已初始化",
        }],
        "runtime": {"generated_at": utc_now(), "tool": "scripts/main.py"},
    }
    # Echo any extra contract keys that may not be among defaults.
    for k in contract_keys:
        if k not in result:
            result[k] = None

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    draft_path = os.path.join(out_dir, "draft.md")
    write_draft_template(draft_path, status)
    result["artifact"]["path"] = draft_path

    result_path = os.path.join(out_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with open(os.path.join(work_dir, "source_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": case_id, "sources": sources,
                   "scanned_at": utc_now()}, f, ensure_ascii=False, indent=2)

    with open(os.path.join(work_dir, "contract_hint.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": case_id, "contract_keys": contract_keys,
                   "contract_hint": contract_hint}, f, ensure_ascii=False, indent=2)

    return result_path


def write_draft_template(path, status):
    lines = [
        "# 基金申请书章节稿（可编辑初稿）",
        "",
        "> 状态：%s。本文件由 scripts/main.py 生成骨架，正文由执行 Agent 逐要素填写；"
        "每条事实需在 result.json 的 fact_source_map 登记 locator 与逐字 quote。"
        "凡材料未提供的内容，一律写“【材料未提供，待申请人补充】”，不得编造。" % status,
        "",
    ]
    for i, e in enumerate(SEVEN_ELEMENTS, 1):
        lines.append("## %d. %s" % (i, e))
        lines.append("")
        lines.append("（待填写。若材料不足，注明缺口并标【材料未提供，待申请人补充】。）")
        lines.append("")
    lines.append("## 要素对照说明（三级对应：段落—要素—证据）")
    lines.append("")
    lines.append("| 段落/句子 | 要素 | 证据 locator | 逐字 quote 或 not_reported |")
    lines.append("|---|---|---|---|")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default=None)
    ap.add_argument("--input", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--work", default=None)
    a = ap.parse_args(argv)

    input_dir = a.input
    if not (input_dir and os.path.isdir(input_dir)):
        input_dir = next(candidate_input_dirs(), "input")

    out_dir = a.out or ("/app/output" if os.path.isdir("/app") else "output")
    work_dir = a.work or ("/app/work" if os.path.isdir("/app") else "work")

    case_path = a.case
    if not (case_path and os.path.isfile(case_path)):
        for p in candidate_case_files(input_dir):
            case_path = p
            break

    degradations = []
    case, case_err = load_json(case_path)
    if case_err:
        degradations.append(case_err)

    sources = scan_sources(input_dir, degradations)
    try:
        result_path = build_skeleton(case, case_path, sources, degradations,
                                     out_dir, work_dir)
    except OSError as e:
        # Last-resort: write minimal valid result next to cwd to avoid empty dir.
        try:
            minimal = {"case_id": os.path.splitext(os.path.basename(case_path or "case.json"))[0],
                       "status": "failed", "errors": [str(e)],
                       "artifact": {"path": None}, "fact_source_map": [],
                       "version_boundary": {}, "sources": [], "uncertainty": [],
                       "limitations": ["output write failed"], "pending_human": [],
                       "change_log": []}
            with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump(minimal, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, "case_id": os.path.basename(result_path) and
                      json.load(open(result_path, encoding="utf-8"))["case_id"],
                      "result": result_path, "sources": len(sources),
                      "status": json.load(open(result_path, encoding="utf-8"))["status"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
