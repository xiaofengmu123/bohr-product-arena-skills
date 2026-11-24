#!/usr/bin/env python3
"""academic-slide-builder-deterministic —— 单一确定性 CLI。

子命令：
  scaffold  在前 3 分钟内落盘 result.json 骨架
  record    增量更新 sources / evidence / plan / version / 契约字段等
  check     校验契约并写 self_check.json（有缺口时退出码 1）
  finalize  执行终检、写诚实 status 与终版 result.json
  version   打印版本号

确定性规则（本文件强制执行）：
- result.json 不含墙钟时间戳；
- 所有列表写盘前稳定排序；
- 所有写入经 skillkit.write_json_atomic 规范序列化；
- determinism.input_digest 记录输入清单摘要，供评审复核同输入->同产物。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import skillkit as sk

STEP_BUDGET = {
    "scaffold": "0-60s",
    "inventory": "60-180s",
    "plan": "180-300s",
    "build": "300-540s",
    "render": "540-720s",
    "finalize": "720-900s",
}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _split(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _parse_value(value):
    if value == "":
        return None
    if value[0] in "{[":
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _dirs(args):
    return (
        getattr(args, "input", None) or sk.env_dir("INPUT_DIR", "/input"),
        getattr(args, "work", None) or sk.env_dir("WORK_DIR", "/app/work"),
        getattr(args, "output", None) or sk.env_dir("OUTPUT_DIR", "/app/output"),
    )


def _result_path(output_dir):
    return os.path.join(output_dir, "result.json")


def _load_result(output_dir):
    return sk.load_json(_result_path(output_dir))


def _empty_result(case_id, digest):
    return {
        "case_id": case_id,
        "status": sk.STATUS_PARTIAL,
        "presentation_file": None,
        "preview_files": [],
        "speaker_notes": None,
        "page_plan": [],
        "evidence_map": [],
        "version_records": [],
        "sources": [],
        "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
        "pending_human": [],
        "change_log": [],
        "determinism": {
            "guarantee": "同一输入 + 同一命令序列 => result.json 字节一致",
            "seed": sk.DEFAULT_SEED,
            "canonical_serialization": True,
            "input_digest": digest,
        },
        "errors": [],
        "degradations": [],
    }


def _echo_contract(result, contract):
    """按 output_contract 原样回填全部字段名（缺值给 null，不改名、不删未知键）。"""
    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        keys = [str(k) for k in contract]
    elif isinstance(contract, str) and contract.strip():
        keys = [contract.strip()]
    else:
        keys = []
    for k in keys:
        result.setdefault(k, None)
    return keys


def _append_change(result, step, action, reason, budget=""):
    entry = {
        "seq": len(result.get("change_log", [])) + 1,
        "step": step,
        "action": action,
        "reason": reason,
    }
    if budget:
        entry["budget"] = budget
    result.setdefault("change_log", []).append(entry)
    return entry


def cmd_scaffold(args):
    input_dir, work_dir, output_dir = _dirs(args)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case, err = sk.load_json(os.path.join(input_dir, "case.json"))
    degradations = []
    if err:
        case = {}
        degradations.append("case.json 不可读: " + err)

    case_id = str(case.get("id") or case.get("case_id") or case.get("armiId") or "unknown")
    contract = case.get("output_contract", case.get("outputContract"))
    digest = sk.input_digest(input_dir)

    result = _empty_result(case_id, digest)
    contract_keys = _echo_contract(result, contract)
    if contract is None:
        degradations.append("case.json 未声明 output_contract，仅回填标准字段")

    inv = sk.inventory(input_dir)
    material = [e for e in inv
                if not (e["path"].endswith("case.json") or e["path"].endswith("task.md"))]
    if not material:
        result["uncertainty"]["missing_artifacts"].append(
            "未发现除 case.json/task.md 之外的论文/材料文件")

    if degradations:
        result["degradations"] = sorted(set(degradations))

    _append_change(result, "scaffold",
                   "落盘 result.json 骨架并回填 output_contract 字段",
                   "满足前 3 分钟先落盘 partial 契约", STEP_BUDGET["scaffold"])

    sk.write_json_atomic(_result_path(output_dir), result)

    summary = {
        "case_id": case_id,
        "title": case.get("title") or case.get("title_zh") or "",
        "output_contract_keys": contract_keys,
        "input_files": inv,
        "material_files": material,
    }
    sk.write_json_atomic(os.path.join(work_dir, "case_summary.json"), summary)

    # 非确定性运行元数据：刻意放在 work/run_meta.json，不进 result.json。
    sk.write_json_atomic(os.path.join(work_dir, "run_meta.json"), {
        "skill": sk.SKILL_NAME,
        "version": sk.VERSION,
        "python": sys.version.split()[0],
        "started_at_utc": sk.iso_now(),
        "note": "runtime metadata 被排除在确定性保证之外",
    })

    print("scaffold ok case_id=%s contract_keys=%d material_files=%d output=%s" % (
        case_id, len(contract_keys), len(material), _result_path(output_dir)))
    return 0


def _step_for(sub):
    if sub in ("uncertainty", "pending", "status", "field"):
        return "finalize"
    return "build"


def _action_summary(sub, args):
    if sub == "source":
        return "source %s @ %s" % (args.ref, args.loc)
    if sub == "evidence":
        return "evidence p%d %s" % (_int(args.page), args.claim)
    if sub == "plan":
        return "plan p%d %s" % (_int(args.page), args.role)
    if sub == "version":
        return "version %s" % (args.name or "")
    if sub == "artifact":
        return "artifact %s" % (args.presentation or args.preview)
    if sub == "notes":
        return "notes"
    if sub == "uncertainty":
        return "uncertainty"
    if sub == "pending":
        return "pending %s" % args.item
    if sub == "status":
        return "status %s" % args.value
    if sub == "field":
        return "field %s" % args.key
    return sub


def cmd_record(args):
    _input_dir, _work_dir, output_dir = _dirs(args)
    path = _result_path(output_dir)
    result, err = sk.load_json(path)
    if err:
        print("error: cannot load result.json (%s): %s" % (path, err), file=sys.stderr)
        return 1

    sub = args.subcommand

    if sub == "change":
        if not args.step or not args.action:
            print("error: change requires --step and --action", file=sys.stderr)
            return 1
        _append_change(result, args.step, args.action, args.reason or "",
                       args.budget or STEP_BUDGET.get(args.step, ""))

    elif sub == "source":
        if not args.ref or not args.loc:
            print("error: source requires --ref and --loc", file=sys.stderr)
            return 1
        entry = {
            "ref": args.ref, "loc": args.loc, "kind": args.kind or "text",
            "quote": args.quote or "", "used_in": _split(args.used_in),
        }
        result.setdefault("sources", []).append(entry)
        result["sources"] = sorted(result["sources"],
                                   key=lambda e: (str(e.get("ref", "")), str(e.get("loc", ""))))

    elif sub == "evidence":
        if not args.claim:
            print("error: evidence requires --claim", file=sys.stderr)
            return 1
        entry = {
            "page": _int(args.page), "role": args.role or "claim",
            "claim": args.claim, "refs": _split(args.refs),
            "loc": args.loc or "", "quote": args.quote or "",
        }
        result.setdefault("evidence_map", []).append(entry)
        result["evidence_map"] = sorted(
            result["evidence_map"],
            key=lambda e: (_int(e.get("page")), str(e.get("role", "")), str(e.get("claim", ""))))

    elif sub == "plan":
        entry = {
            "page": _int(args.page), "role": args.role or "claim",
            "claim": args.claim or "", "refs": _split(args.refs),
            "loc": args.loc or "", "word_budget": args.word_budget or "",
        }
        result.setdefault("page_plan", []).append(entry)
        result["page_plan"] = sorted(result["page_plan"], key=lambda e: _int(e.get("page")))

    elif sub == "version":
        seq = len(result.get("version_records", [])) + 1
        entry = {
            "seq": seq,
            "name": args.name or ("v%d" % seq),
            "note": args.note or "",
            "files": _split(args.files),
        }
        result.setdefault("version_records", []).append(entry)

    elif sub == "artifact":
        if args.presentation:
            result["presentation_file"] = args.presentation
        if args.preview:
            pv = result.setdefault("preview_files", [])
            if args.preview not in pv:
                pv.append(args.preview)
            pv.sort()

    elif sub == "notes":
        result["speaker_notes"] = _parse_value(args.value)

    elif sub == "uncertainty":
        u = result.setdefault("uncertainty", {"unresolved": [], "missing_artifacts": [], "notes": ""})
        if args.missing:
            u.setdefault("missing_artifacts", [])
            if args.missing not in u["missing_artifacts"]:
                u["missing_artifacts"].append(args.missing)
            u["missing_artifacts"] = sorted(set(u["missing_artifacts"]))
        if args.unresolved:
            u.setdefault("unresolved", [])
            if args.unresolved not in u["unresolved"]:
                u["unresolved"].append(args.unresolved)
            u["unresolved"] = sorted(set(u["unresolved"]))
        if args.note:
            u["notes"] = args.note

    elif sub == "pending":
        if not args.item:
            print("error: pending requires --item", file=sys.stderr)
            return 1
        ph = result.setdefault("pending_human", [])
        if args.item not in ph:
            ph.append(args.item)
        ph.sort()

    elif sub == "status":
        if args.value not in sk.STATUS_VALUES:
            print("error: status must be one of %s" % (sk.STATUS_VALUES,), file=sys.stderr)
            return 1
        result["status"] = args.value

    elif sub == "field":
        if not args.key:
            print("error: field requires --key", file=sys.stderr)
            return 1
        result[args.key] = _parse_value(args.value)

    else:
        print("error: unknown record subcommand %s" % sub, file=sys.stderr)
        return 1

    if sub != "change":
        _append_change(result, _step_for(sub), _action_summary(sub, args),
                       "record %s" % sub)

    sk.write_json_atomic(path, result)
    print("record ok %s" % sub)
    return 0


def _is_ordered(entries):
    seqs = [e.get("seq", 0) for e in entries]
    return seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def _run_checks(result, output_dir):
    checks = []

    def ch(cid, ok, note):
        checks.append({"id": cid, "pass": bool(ok), "note": note})

    ch("case_id", bool(result.get("case_id")), "case_id=%r" % result.get("case_id"))
    ch("status_enum", result.get("status") in sk.STATUS_VALUES, "status=%r" % result.get("status"))

    for field in ("presentation_file", "preview_files", "speaker_notes", "page_plan",
                  "evidence_map", "version_records", "sources", "uncertainty",
                  "pending_human", "change_log"):
        ch("field_%s" % field, field in result, "field %s 存在" % field)

    ch("change_log_ordered", _is_ordered(result.get("change_log", [])),
       "change_log 按 seq 升序且无重复")
    ch("sources_located", all(s.get("ref") and s.get("loc") for s in result.get("sources", [])),
       "sources 均有 ref+loc")
    ch("evidence_quoted",
       all(str(e.get("quote", "")).strip() for e in result.get("evidence_map", [])),
       "evidence_map 每项均含逐字 quote")

    pf = result.get("presentation_file")
    if pf:
        ch("presentation_exists", os.path.exists(os.path.join(output_dir, pf)),
           "presentation_file=%s 存在" % pf)
    else:
        ch("presentation_exists", False, "presentation_file 为空")

    pvs = result.get("preview_files", []) or []
    if pvs:
        ch("previews_exist",
           all(os.path.exists(os.path.join(output_dir, p)) for p in pvs),
           "preview_files=%d 全部存在" % len(pvs))
    else:
        ch("previews_exist", False, "preview_files 为空")

    limit = result.get("slide_count") or result.get("page_limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None
    if limit:
        actual = len(result.get("page_plan", []))
        ch("page_limit", actual <= limit, "页计划 %d <= 约束 %d" % (actual, limit))

    det = result.get("determinism", {})
    ch("determinism_digest", bool(det.get("input_digest")), "input_digest 已记录")
    return checks


def cmd_check(args):
    _input_dir, work_dir, output_dir = _dirs(args)
    result, err = sk.load_json(_result_path(output_dir))
    if err:
        self_check = {"case_id": None, "ok": False, "pass": 0, "fail": 1,
                      "checks": [{"id": "result_json", "pass": False,
                                  "note": "cannot load: " + err}]}
        sk.write_json_atomic(os.path.join(output_dir, "self_check.json"), self_check)
        print("check: FAIL result.json 不可读 (%s)" % err)
        return 1

    checks = _run_checks(result, output_dir)
    ok = all(c["pass"] for c in checks)
    self_check = {
        "case_id": result.get("case_id"),
        "ok": ok,
        "pass": sum(1 for c in checks if c["pass"]),
        "fail": sum(1 for c in checks if not c["pass"]),
        "checks": checks,
    }
    sk.write_json_atomic(os.path.join(output_dir, "self_check.json"), self_check)
    sk.write_json_atomic(os.path.join(work_dir, "self_check.json"), self_check)
    print("check %s %d/%d" % ("OK" if ok else "GAPS", self_check["pass"], len(checks)))
    return 0 if ok else 1


def cmd_finalize(args):
    input_dir, work_dir, output_dir = _dirs(args)
    result, err = sk.load_json(_result_path(output_dir))
    if err:
        print("error: cannot load result.json: %s" % err, file=sys.stderr)
        return 1

    checks = _run_checks(result, output_dir)
    ok = all(c["pass"] for c in checks)

    requested = args.status
    status = requested
    if status == sk.STATUS_COMPLETE and not ok:
        status = sk.STATUS_PARTIAL
        u = result.setdefault("uncertainty",
                              {"unresolved": [], "missing_artifacts": [], "notes": ""})
        u["notes"] = ((u.get("notes", "") or "") + " complete 请求降级为 partial：self_check 存在缺口").strip()
    result["status"] = status

    det = result.setdefault("determinism", {})
    if not det.get("input_digest"):
        det["input_digest"] = sk.input_digest(input_dir)
        det["canonical_serialization"] = True
        det["seed"] = sk.DEFAULT_SEED

    _append_change(result, "finalize",
                   "finalize status=%s" % status,
                   "终检并写终版" + ("（complete 请求降级，自检缺口）"
                                    if requested == sk.STATUS_COMPLETE and status == sk.STATUS_PARTIAL
                                    else ""),
                   STEP_BUDGET["finalize"])

    sk.write_json_atomic(_result_path(output_dir), result)

    self_check = {
        "case_id": result.get("case_id"),
        "ok": ok,
        "pass": sum(1 for c in checks if c["pass"]),
        "fail": sum(1 for c in checks if not c["pass"]),
        "checks": checks,
    }
    sk.write_json_atomic(os.path.join(output_dir, "self_check.json"), self_check)
    sk.write_json_atomic(os.path.join(work_dir, "self_check.json"), self_check)
    print("finalize ok status=%s checks=%d/%d" % (status, self_check["pass"], len(checks)))
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=None)
    common.add_argument("--work", default=None)
    common.add_argument("--output", default=None)

    p = argparse.ArgumentParser(prog="main.py",
                                description="academic-slide-builder-deterministic CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scaffold", parents=[common])
    sub.add_parser("check", parents=[common])
    sub.add_parser("version", parents=[common])

    f = sub.add_parser("finalize", parents=[common])
    f.add_argument("--status", choices=list(sk.STATUS_VALUES), required=True)

    r = sub.add_parser("record", parents=[common])
    r.add_argument("subcommand", choices=[
        "change", "source", "evidence", "plan", "version", "artifact",
        "notes", "uncertainty", "pending", "status", "field"])
    r.add_argument("--step", default="")
    r.add_argument("--budget", default="")
    r.add_argument("--action", default="")
    r.add_argument("--reason", default="")
    r.add_argument("--ref", default="")
    r.add_argument("--loc", default="")
    r.add_argument("--kind", default="")
    r.add_argument("--quote", default="")
    r.add_argument("--used-in", default="")
    r.add_argument("--page", default="0")
    r.add_argument("--role", default="")
    r.add_argument("--claim", default="")
    r.add_argument("--refs", default="")
    r.add_argument("--word-budget", default="")
    r.add_argument("--name", default="")
    r.add_argument("--note", default="")
    r.add_argument("--files", default="")
    r.add_argument("--presentation", default="")
    r.add_argument("--preview", default="")
    r.add_argument("--key", default="")
    r.add_argument("--value", default="")
    r.add_argument("--missing", default="")
    r.add_argument("--unresolved", default="")
    r.add_argument("--item", default="")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "scaffold":
        return cmd_scaffold(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "finalize":
        return cmd_finalize(args)
    if args.command == "version":
        print(sk.VERSION)
        return 0
    if args.command == "record":
        return cmd_record(args)
    print("error: unknown command %s" % args.command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
