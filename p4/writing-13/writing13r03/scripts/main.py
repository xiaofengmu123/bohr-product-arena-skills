#!/usr/bin/env python3
"""Grant-proposal writing Skill: deterministic result.json scaffold/finalize/check.

Determinism contract: every JSON is written with sorted keys and a fixed indent;
no wall-clock or random values enter outputs, and change_log uses step numbers
instead of timestamps, so the same input twice yields byte-identical results.

Standard library only.
"""
import argparse
import hashlib
import json
import os
import sys

SCHEMA_VERSION = "2.0.0"
SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性",
]
REQUIRED_RESULT_KEYS = [
    "case_id", "status", "artifact", "fact_source_map", "version_boundary",
    "sources", "uncertainty", "change_log", "runtime",
]


def resolve_dir(path, default_abs, default_local):
    """Pick an existing directory, falling back to a local debug directory."""
    if path:
        return path
    if os.path.isdir(default_abs):
        return default_abs
    return default_local


def load_json(path):
    """Read JSON with BOM/encoding tolerance; return {} on failure."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except Exception:
            return {}


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def canonical_digest(obj):
    """Stable digest over all result content except the runtime block."""
    clone = json.loads(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    clone.pop("runtime", None)
    data = json.dumps(clone, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def case_id_of(case, case_path):
    if isinstance(case, dict):
        for key in ("id", "case_id", "challenge_id"):
            if case.get(key):
                return str(case[key])
    if case_path:
        base = os.path.basename(case_path)
        if base.lower().endswith(".json"):
            return os.path.splitext(base)[0]
        return base
    return "unknown_case"


def contract_hint_of(case):
    hint = {}
    if isinstance(case, dict):
        for key in ("output_contract", "output_schema", "required_fields"):
            if key in case and case[key] not in (None, {}, []):
                hint[key] = case[key]
    return hint


def empty_result(case_id):
    return {
        "case_id": case_id,
        "status": "in_progress",
        "schema_version": SCHEMA_VERSION,
        "artifact": {"path": None, "type": None, "description": None},
        "elements": {e: {"covered": False, "fact_count": 0} for e in SEVEN_ELEMENTS},
        "fact_source_map": [],
        "version_boundary": {
            "modification_diff": [], "manual_confirm": [],
            "material_gaps": [], "anomalies": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "change_log": [
            {
                "step": 0, "action": "scaffold",
                "reason": "Step 0 落盘结果骨架",
                "impact": "建立 result.json 与 run_meta.json",
            }
        ],
        "runtime": {
            "deterministic": True, "rerun_identical": True,
            "python_version": sys.version.split()[0],
        },
    }


def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case = load_json(args.case)
    case_id = case_id_of(case, args.case)
    result = empty_result(case_id)
    result["contract_hint"] = contract_hint_of(case)
    result["runtime"]["result_digest"] = canonical_digest(result)
    dump_json(result, os.path.join(args.output, "result.json"))
    dump_json(
        {
            "case_id": case_id,
            "input_dir": args.input,
            "output_dir": args.output,
            "work_dir": args.work,
            "seven_elements": SEVEN_ELEMENTS,
            "schema_version": SCHEMA_VERSION,
        },
        os.path.join(args.work, "run_meta.json"),
    )
    print(json.dumps({"ok": True, "case_id": case_id, "result": os.path.join(args.output, "result.json")},
                     ensure_ascii=False))
    return 0


def recompute_elements(result):
    counts = {e: 0 for e in SEVEN_ELEMENTS}
    for rec in result.get("fact_source_map", []):
        el = rec.get("element")
        if el in counts:
            counts[el] += 1
    result["elements"] = {
        e: {"covered": counts[e] > 0, "fact_count": counts[e]} for e in SEVEN_ELEMENTS
    }
    return result


def append_log(result, step, action, reason, impact):
    log = result.setdefault("change_log", [])
    if not any(entry.get("step") == step for entry in log):
        log.append({"step": step, "action": action, "reason": reason, "impact": impact})
    log.sort(key=lambda e: e.get("step", 0))
    return result


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    result = load_json(result_path)
    if not result:
        result = empty_result(case_id_of(load_json(args.case), args.case))
    # Merge exported fact ledger (work/fact_source_map.json).
    fsmap = load_json(os.path.join(args.work, "fact_source_map.json"))
    facts = fsmap.get("fact_source_map", []) if isinstance(fsmap, dict) else fsmap
    if isinstance(facts, list) and facts:
        facts.sort(key=lambda r: str(r.get("id", "")))
        result["fact_source_map"] = facts
    # Merge source manifest (work/source_manifest.json).
    manifest = load_json(os.path.join(args.work, "source_manifest.json"))
    if isinstance(manifest, dict):
        files = manifest.get("files", [])
        result["sources"] = [
            {
                "path": f.get("path"),
                "size": f.get("size"),
                "sha256": f.get("sha256"),
                "type": f.get("type"),
            }
            for f in files
        ]
        result["runtime"]["input_digest"] = manifest.get("input_digest")
    recompute_elements(result)
    result["artifact"] = {
        "path": os.path.basename(args.artifact) if args.artifact else result.get("artifact", {}).get("path"),
        "type": args.artifact_type or result.get("artifact", {}).get("type") or "markdown",
        "description": args.artifact_desc or result.get("artifact", {}).get("description") or "可直接进入申请书的章节稿",
    }
    if args.matrix:
        result["artifact"]["element_matrix"] = os.path.basename(args.matrix)
    if args.status:
        result["status"] = args.status
    append_log(
        result, 4, "finalize",
        "Step 4/5 合并事实台账与来源快照，回填 artifact/sources/status",
        "result.json 反映最终产物与状态",
    )
    result["runtime"]["result_digest"] = canonical_digest(result)
    dump_json(result, result_path)
    print(json.dumps({"ok": True, "status": result["status"], "result": result_path},
                     ensure_ascii=False))
    return 0


def cmd_check(args):
    result = load_json(os.path.join(args.output, "result.json"))
    errors = []
    warnings = []
    if not result:
        errors.append("result.json 不存在或无法解析")
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False))
        return 1
    for key in REQUIRED_RESULT_KEYS:
        if key not in result:
            if args.fix:
                result.setdefault(key, [] if key in ("fact_source_map", "change_log") else
                                  {"path": None, "type": None, "description": None} if key == "artifact" else
                                  {} if key in ("version_boundary", "runtime") else
                                  [] if key in ("sources", "uncertainty") else None)
            else:
                errors.append("缺少必需字段: %s" % key)
    if result.get("case_id") in (None, ""):
        errors.append("case_id 为空")
    if args.fix and result.get("status") in (None, ""):
        result["status"] = "partial"
    art = result.get("artifact") or {}
    art_path = art.get("path")
    if art_path:
        full = os.path.join(args.output, art_path)
        if not os.path.isfile(full):
            errors.append("artifact.path 指向不存在的文件: %s" % art_path)
    else:
        warnings.append("artifact.path 为空（可能尚未产出主稿）")
    # Determinism: verify stored digest matches recomputation.
    stored = (result.get("runtime") or {}).get("result_digest")
    if stored and stored != canonical_digest(result):
        errors.append("result_digest 不一致：产物被非确定性修改")
    if args.fix:
        dump_json(result, os.path.join(args.output, "result.json"))
    ok = not errors
    print(json.dumps({"ok": ok, "errors": errors, "warnings": warnings},
                     ensure_ascii=False))
    return 0 if ok else 1


def _add_common(p, suppress_defaults=False):
    """Add shared path options; subparsers use SUPPRESS so they never clobber
    a value already given on the parent parser (supports args before or after
    the subcommand)."""
    d = argparse.SUPPRESS if suppress_defaults else None
    p.add_argument("--input", default=d, help="输入目录（默认 /input，回退 input/）")
    p.add_argument("--output", default=d, help="输出目录（默认 /app/output，回退 output/）")
    p.add_argument("--work", default=d, help="工作目录（默认 /app/work，回退 work/）")
    p.add_argument("--case", default=d, help="case.json 路径（默认 <input>/case.json）")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="基金申请书写作 Skill 主入口")
    _add_common(ap)
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scaffold", help="Step 0 落盘结果骨架")
    _add_common(sp, suppress_defaults=True)
    sp.set_defaults(func=lambda a: cmd_scaffold(a))

    fp = sub.add_parser("finalize", help="合并台账/来源/工件并回填 result.json")
    _add_common(fp, suppress_defaults=True)
    fp.add_argument("--artifact", default=None, help="主稿文件名（相对 output 目录）")
    fp.add_argument("--matrix", default=None, help="要素对照说明文件名")
    fp.add_argument("--artifact-type", default=None)
    fp.add_argument("--artifact-desc", default=None)
    fp.add_argument("--status", default=None, choices=["in_progress", "done", "partial", "failed"])
    fp.set_defaults(func=lambda a: cmd_finalize(a))

    cp = sub.add_parser("check", help="result.json 完整性自检")
    _add_common(cp, suppress_defaults=True)
    cp.add_argument("--fix", action="store_true", help="补齐缺失字段")
    cp.set_defaults(func=lambda a: cmd_check(a))

    args = ap.parse_args(argv)
    args.input = resolve_dir(args.input, "/input", "input")
    args.output = resolve_dir(args.output, "/app/output", "output")
    args.work = resolve_dir(args.work, "/app/work", "work")
    if not getattr(args, "case", None):
        args.case = os.path.join(args.input, "case.json")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
