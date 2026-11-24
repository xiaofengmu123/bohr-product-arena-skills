#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口（纯标准库）。

子命令：
  scaffold  发现输入、读 case.json、回填 output_contract、早落盘 partial 骨架
  plan      生成/更新检索计划中间表示 /app/work/plan.json
  normalize 规范化一个或多个专利号（--text / --numbers / --file）
  extract   从文本/文件抽取专利号候选（OCR 边界感知，不擅自合并）
  family    读 records JSON，按共用申请号/优先权号归并同族（observed）
  evidence  生成证据图中间表示 /app/work/evidence_map.json
  finalize  校验 result.json、补 runtime、登记中间产物、生成 report.md
  validate  校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work
可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖，亦可传参覆盖。
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import core  # noqa: E402


def _env_default(name, fallback):
    return os.environ.get(name) or fallback


def _read_json_arg(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = core.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err.get("error")), file=sys.stderr)
        return None
    return obj


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _result_path(args):
    return os.path.join(args.output, "result.json")


def _load_result(args):
    path = _result_path(args)
    if not os.path.exists(path):
        return None, path, "result.json not found"
    result, err = core.load_json(path)
    if err:
        return None, path, err.get("error")
    if not isinstance(result, dict):
        return None, path, "result.json is not an object"
    return result, path, None


# --------------------------------------------------------------------------- #
def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    files = core.discover_input(args.input)
    case_obj = None
    case_path = None
    case_err = None
    for f in files:
        if f["kind"] == "case":
            case_path = f["path"]
            case_obj, case_err = core.load_json(f["path"])
            break

    contract = None
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")

    result = core.build_skeleton(case_obj, contract)

    if case_path is not None and not isinstance(case_obj, dict):
        result["errors"].append({
            "item": "case_json_unreadable",
            "reason": case_err.get("error") if case_err else "case.json is not a JSON object",
        })
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "proceeded with case_id=unknown and default schema",
        })

    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({
                "rel": f["rel"], "kind": f["kind"], "size": f["size"],
                "note": "binary; read via platform PDF/OCR tools",
            })
        elif f["kind"] in ("text", "table", "task", "notes", "json"):
            text, enc, err = core.read_text(f["path"])
            inventory.append({
                "rel": f["rel"], "kind": f["kind"], "size": f["size"],
                "encoding": enc, "chars": len(text), "read_error": err,
            })
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"]})

    result.setdefault("input_inventory", inventory)
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_input_files",
            "reason": "input directory has no discoverable files",
        })

    out_path = _result_path(args)
    _write_json(out_path, result)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
    }
    _write_json(os.path.join(args.work, "state.json"), state)

    print("scaffold: wrote %s (status=%s, files=%d)"
          % (out_path, result["status"], len(files)))


def cmd_plan(args):
    result, path, err = _load_result(args)
    if err:
        # 无 result.json 时仍可基于参数生成空计划
        result = {"case_id": "unknown", "research_question": None, "retrieval_scope": None}
        path = _result_path(args)
    case_id = result.get("case_id") or "unknown"
    question = args.question if args.question is not None else result.get("research_question")
    scope = args.scope if args.scope is not None else result.get("retrieval_scope")
    plan = core.build_plan(case_id, question, scope, args.object_type)
    plan_path = os.path.join(args.work, "plan.json")
    os.makedirs(args.work, exist_ok=True)
    _write_json(plan_path, plan)

    result["research_question"] = question
    result["retrieval_scope"] = scope
    result.setdefault("plan", {
        "plan_id": plan["plan_id"], "path": plan_path,
        "object_type": args.object_type,
        "early_checkpoint_committed": plan["early_checkpoint_committed"],
    })
    result["change_log"].append({
        "at": core.now_iso(), "event": "plan",
        "detail": "retrieval plan written to /app/work/plan.json (id=%s)" % plan["plan_id"],
    })
    core.record_artifact(result, plan_path, "plan", "检索计划中间表示")
    _write_json(path, result)
    print("plan: wrote %s (id=%s)" % (plan_path, plan["plan_id"]))


def cmd_normalize(args):
    if args.file:
        data = _read_json_arg(args.file)
        if data is None:
            data = []
        items = data if isinstance(data, list) else [data]
        outputs = [core.normalize_record(x) for x in items]
    elif args.numbers:
        outputs = [core.normalize_patent_number(x) for x in args.numbers]
    elif args.text is not None:
        outputs = [core.normalize_patent_number(args.text)]
    else:
        outputs = []
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def cmd_extract(args):
    if args.file:
        text, _enc, err = core.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=False), file=sys.stderr)
            sys.exit(0)
    else:
        text = args.text or ""
    hits = core.extract_numbers(text)
    print(json.dumps(hits, ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json_arg(args.file) or []
    if not isinstance(records, list):
        records = [records]
    groups = core.group_families(records)
    print(json.dumps({
        "families": groups,
        "family_type": "observed",
        "exhaustive": False,
    }, ensure_ascii=False, indent=2))


def cmd_evidence(args):
    result, path, err = _load_result(args)
    if err:
        print("evidence: %s" % err, file=sys.stderr)
        sys.exit(0)
    mapping = core.build_evidence_map(result)
    map_path = os.path.join(args.work, "evidence_map.json")
    os.makedirs(args.work, exist_ok=True)
    _write_json(map_path, mapping)
    core.record_artifact(result, map_path, "evidence_map", "结论-证据对齐中间表示")
    result["change_log"].append({
        "at": core.now_iso(), "event": "evidence",
        "detail": "evidence_map written with %d items (%d conflicts)"
                  % (mapping["evidence_count"], len(mapping["conflicts"])),
    })
    _write_json(path, result)
    print("evidence: wrote %s (items=%d, conflicts=%d)"
          % (map_path, mapping["evidence_count"], len(mapping["conflicts"])))


def cmd_finalize(args):
    result, path, err = _load_result(args)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        sys.exit(0)

    if args.status:
        result["status"] = args.status

    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or core.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = core.now_iso()
    rt["elapsed_seconds"] = core._elapsed(started, rt["finished_at"])

    # 登记 work 目录中的中间产物（幂等）
    if os.path.isdir(args.work):
        for name in sorted(os.listdir(args.work)):
            if name.endswith(".json") and name in ("plan.json", "records.json",
                                                   "evidence_map.json", "state.json"):
                full = os.path.join(args.work, name)
                core.record_artifact(result, full, name.replace(".json", ""),
                                     "中间表示 / 状态")

    result["change_log"].append({
        "at": core.now_iso(), "event": "finalize",
        "detail": "validated, runtime finalized, report.md rendered",
    })

    ok, errors = core.validate_result(result)
    if not ok:
        for e in errors:
            result.setdefault("errors", []).append({"item": "validation", "reason": e})
        if result.get("status") == "ok":
            result["status"] = "partial"

    _write_json(path, result)

    report_path = os.path.join(args.output, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(core.render_report(result))

    print("finalize: status=%s valid=%s (%d issues); report=%s"
          % (result["status"], "yes" if ok else "no", len(errors), report_path))
    if not ok:
        for e in errors:
            print("  - %s" % e, file=sys.stderr)


def cmd_validate(args):
    result, path, err = _load_result(args)
    if err:
        print("INVALID (%s)" % err, file=sys.stderr)
        sys.exit(0)
    ok, errors = core.validate_result(result)
    if ok:
        print("VALID")
    else:
        print("INVALID")
        for e in errors:
            print("  - %s" % e, file=sys.stderr)


def _add_path_opts(sp):
    """把 --input/--output/--work 同时挂到主解析器与各子命令上。

    用 default=SUPPRESS，避免子命令解析时用子解析器默认值覆盖主解析器已解析的值，
    从而支持两种写法：`main.py --input X scaffold` 与 `main.py scaffold --input X`。
    """
    sp.add_argument("--input", dest="input", default=argparse.SUPPRESS,
                    help="输入目录（默认 $INPUT_DIR 或 /input）")
    sp.add_argument("--output", dest="output", default=argparse.SUPPRESS,
                    help="输出目录（默认 $OUTPUT_DIR 或 /app/output）")
    sp.add_argument("--work", dest="work", default=argparse.SUPPRESS,
                    help="工作目录（默认 $WORK_DIR 或 /app/work）")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill：发现输入、规范化号码、同族归并、"
                    "证据溯源与契约校验。")
    _add_path_opts(p)
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("scaffold", help="发现输入并落盘 partial 骨架")
    _add_path_opts(sp)
    sp.set_defaults(func=cmd_scaffold)

    sp = sub.add_parser("plan", help="生成检索计划中间表示 plan.json")
    _add_path_opts(sp)
    sp.add_argument("--question")
    sp.add_argument("--scope")
    sp.add_argument("--object-type", choices=["number_lookup", "topic_search", "mixed"])
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("normalize", help="规范化专利号")
    _add_path_opts(sp)
    sp.add_argument("--text")
    sp.add_argument("--numbers", nargs="*")
    sp.add_argument("--file")
    sp.set_defaults(func=cmd_normalize)

    sp = sub.add_parser("extract", help="从文本抽取专利号候选")
    _add_path_opts(sp)
    sp.add_argument("--text")
    sp.add_argument("--file")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("family", help="按共用申请号/优先权号归并同族")
    _add_path_opts(sp)
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_family)

    sp = sub.add_parser("evidence", help="生成证据图中间表示")
    _add_path_opts(sp)
    sp.set_defaults(func=cmd_evidence)

    sp = sub.add_parser("finalize", help="校验并生成 report.md")
    _add_path_opts(sp)
    sp.add_argument("--status", choices=sorted(core.ALLOWED_STATUS))
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("validate", help="校验 result.json")
    _add_path_opts(sp)
    sp.set_defaults(func=cmd_validate)

    return p


def _resolve_paths(args):
    """按 参数 > 环境变量 > 缺省 的顺序解析三个路径。"""
    args.input = getattr(args, "input", None) or _env_default("INPUT_DIR", "/input")
    args.output = getattr(args, "output", None) or _env_default("OUTPUT_DIR", "/app/output")
    args.work = getattr(args, "work", None) or _env_default("WORK_DIR", "/app/work")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(0)
    _resolve_paths(args)
    args.func(args)


if __name__ == "__main__":
    main()
