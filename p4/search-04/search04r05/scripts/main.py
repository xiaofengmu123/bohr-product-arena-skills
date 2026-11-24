# -*- coding: utf-8 -*-
"""main.py — 引文追踪 Skill 的薄入口（核心实现见 citation_lib.py）。

用法（相对路径，默认指向运行时目录）：

  python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
  python3 scripts/main.py update    --output /app/output [--file instr.json | --json '...']
  python3 scripts/main.py finalize  --output /app/output --work /app/work
  python3 scripts/main.py selftest
  python3 scripts/main.py --help
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import citation_lib as lib


# --------------------------------------------------------------------------
# bootstrap：扫描输入、构造契约对齐骨架、原子落盘 result.json + state.json
# --------------------------------------------------------------------------

def _find_case_json(input_dir, files):
    for f in files:
        if f["rel"].endswith("case.json"):
            return f["path"]
    direct = os.path.join(input_dir, "case.json")
    return direct if os.path.isfile(direct) else None


def cmd_bootstrap(args):
    input_dir = args.input or "/input"
    output_dir = args.output or "/app/output"
    work_dir = args.work or "/app/work"
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    files = lib.scan_input(input_dir)
    case = {}
    case_err = None
    case_path = _find_case_json(input_dir, files)
    if case_path is not None:
        obj, err = lib.read_json_file(case_path)
        if err is not None:
            case_err = err
        else:
            case = obj if isinstance(obj, dict) else {}

    # 读取 task.md（可选，供 Agent 后续使用）
    task_text = ""
    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        txt, _enc, err = lib.read_text(task_path)
        if err is None:
            task_text = txt

    skel = lib.build_skeleton(case, input_dir)
    skel["runtime"]["input_files"] = [
        {"rel": f["rel"], "size": f["size"], "skipped_size": f["skipped_size"]}
        for f in files
    ]
    skel["runtime"]["task_md_chars"] = len(task_text)

    if not files:
        skel["status"] = "insufficient_evidence"
        skel["limitations"].append("empty input directory: %s" % input_dir)
        skel["pending_human"].append("provide case.json and task.md under /input")
    elif case_path is None:
        skel["status"] = "insufficient_evidence"
        skel["limitations"].append("case.json not found in input directory")
        skel["pending_human"].append("provide case.json")
    elif case_err is not None:
        skel["status"] = "input_error"
        skel["errors"].append("case.json unreadable: %s" % case_err)
        skel["degradations"].append("case.json parse failed; contract fields may be incomplete")
    elif not case:
        skel["status"] = "insufficient_evidence"
        skel["limitations"].append("case.json is empty or not an object")

    skel["runtime"]["steps"].append({"step": 0, "time": lib.now_utc(), "note": "bootstrap complete"})
    lib.recompute_contract(skel)

    result_path = lib.atomic_write_json(os.path.join(output_dir, "result.json"), skel)
    state = {
        "case_id": skel["case_id"],
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "contract_fields": skel["contract"]["fields"],
        "contract_source": skel["contract"]["source"],
        "started_utc": skel["runtime"]["started_utc"],
        "step": 0,
    }
    lib.atomic_write_json(os.path.join(work_dir, "state.json"), state)

    print("bootstrap ok: %s" % result_path)
    print("status=%s case_id=%s contract_fields=%d files=%d" % (
        skel["status"], skel["case_id"], len(skel["contract"]["fields"]), len(files)))
    return 0


# --------------------------------------------------------------------------
# update：读一条或一批 JSON 修改指令，原子增量落盘并追加 change_log
# --------------------------------------------------------------------------

def cmd_update(args):
    output_dir = args.output or "/app/output"
    result_path = os.path.join(output_dir, "result.json")

    if os.path.isfile(result_path):
        result = lib.load_result(result_path)
    else:
        # 鲁棒降级：没有骨架就先造一份最小骨架，避免崩溃
        os.makedirs(output_dir, exist_ok=True)
        result = lib.build_skeleton({}, None)

    raw = None
    if args.file:
        txt, _enc, err = lib.read_text(args.file)
        if err is not None:
            print("update error: cannot read instruction file: %s" % err, file=sys.stderr)
            return 2
        obj, perr = lib.parse_json(txt)
        if perr is not None:
            print("update error: instruction file not valid JSON: %s" % perr, file=sys.stderr)
            return 2
        raw = obj
    elif args.json:
        obj, perr = lib.parse_json(args.json)
        if perr is not None:
            print("update error: --json not valid: %s" % perr, file=sys.stderr)
            return 2
        raw = obj
    else:
        txt = sys.stdin.read()
        obj, perr = lib.parse_json(txt)
        if perr is not None:
            print("update error: stdin not valid JSON: %s" % perr, file=sys.stderr)
            return 2
        raw = obj

    instrs = raw if isinstance(raw, list) else [raw]
    for ins in instrs:
        try:
            lib.apply_instruction(result, ins)
        except Exception as e:  # noqa: BLE001 —— 单条失败不影响其余指令
            result.setdefault("errors", []).append(
                "update op %r failed: %s" % (ins.get("op") if isinstance(ins, dict) else ins, e))
            lib.append_change(result, "update_error", str(e), ins.get("reason", "") if isinstance(ins, dict) else "")

    lib.atomic_write_json(result_path, result)
    print("update ok: %s" % result_path)
    print("status=%s change_log=%d contract_satisfied=%s" % (
        result.get("status"), len(result.get("change_log", [])),
        (result.get("contract") or {}).get("satisfied")))
    return 0


# --------------------------------------------------------------------------
# finalize：校验 + 渲染 report.md / papers.csv / edges.csv / citation_graph.svg
# --------------------------------------------------------------------------

def cmd_finalize(args):
    output_dir = args.output or "/app/output"
    os.makedirs(output_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(result_path):
        result = lib.load_result(result_path)
    else:
        result = lib.build_skeleton({}, None)
        lib.atomic_write_json(result_path, result)
    artifacts = lib.finalize_artifacts(result, output_dir)
    print("finalize ok, artifacts:")
    for a in artifacts:
        print("  - %s" % a)
    return 0


# --------------------------------------------------------------------------
# selftest：委托 selftest.py（真实构造临时输入，验证契约/鲁棒性/确定性）
# --------------------------------------------------------------------------

def cmd_selftest(_args):
    try:
        import selftest
    except ImportError:
        print("selftest: cannot import selftest.py", file=sys.stderr)
        return 2
    return selftest.run()


def build_parser():
    p = argparse.ArgumentParser(
        prog="citation-skill",
        description="引文追踪 Skill：契约对齐、增量落盘、证据溯源、去重与路径解释。")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="扫描输入并落盘契约对齐的结果骨架")
    b.add_argument("--input", default="/input")
    b.add_argument("--output", default="/app/output")
    b.add_argument("--work", default="/app/work")

    u = sub.add_parser("update", help="应用一条或一批 JSON 修改指令（增量落盘）")
    u.add_argument("--output", default="/app/output")
    u.add_argument("--file", help="含 JSON 对象或数组的指令文件路径")
    u.add_argument("--json", help="内联 JSON 指令")

    f = sub.add_parser("finalize", help="校验并渲染可复核产物")
    f.add_argument("--output", default="/app/output")
    f.add_argument("--work", default="/app/work")

    sub.add_parser("selftest", help="运行自检（临时目录，不写正式产物）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "bootstrap": cmd_bootstrap,
        "update": cmd_update,
        "finalize": cmd_finalize,
        "selftest": cmd_selftest,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
