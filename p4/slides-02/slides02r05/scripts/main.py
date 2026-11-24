#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""academic-slide-contract 主控脚本（仅 Python 标准库）。

子命令（相对 Skill 根目录调用，脚本不依赖当前工作目录）：

    python3 scripts/main.py scaffold
    python3 scripts/main.py record change  --step build --action "..." [--status ok] --reason "..."
    python3 scripts/main.py record source  --ref "Fig 3" --loc "paper p.7" [--kind figure] [--used-in page4]
    python3 scripts/main.py record evidence --page 4 --role result --claim "..." [--refs "Fig 3,Fig 4"] [--loc p.7] [--quote "..."]
    python3 scripts/main.py record version --name v1 [--note "..."] [--files path1,path2]
    python3 scripts/main.py record field   --key presentation_file --value ./deck.pptx
    python3 scripts/main.py check [--strict]
    python3 scripts/main.py finalize --status complete|partial|failed [--note "..."]

目录可用 INPUT_DIR / WORK_DIR / OUTPUT_DIR 环境变量覆盖
（默认 /input、/app/work、/app/output）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import contract


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def load_result(out_dir: Path) -> dict:
    data, err = contract.load_json(out_dir / "result.json")
    return data if isinstance(data, dict) else {}


def resolve_output_path(out_dir: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else out_dir / p


def sha256_of(path: Path):
    try:
        if not path.exists():
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def determine_status(loaded, err) -> str:
    if err == "missing":
        return "insufficient_evidence"
    if err in ("bad_json", "unreadable"):
        return "input_error"
    if loaded is None or (isinstance(loaded, dict) and not loaded):
        return "insufficient_evidence"
    return "partial"


def append_change(result: dict, step: str, action: str, status: str, reason: str) -> None:
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({
        "t": contract.utcnow(),
        "seq": len(log) + 1,
        "step": step,
        "action": action,
        "status": status,
        "reason": reason,
    })


def append_to_field(result: dict, key: str, entry, out_dir: Path) -> str:
    """向字段追加条目；字段为数组则内联追加，为字符串路径则写外部 JSON 数组。"""
    current = result.get(key)
    if isinstance(current, str) and current.strip():
        path = resolve_output_path(out_dir, current)
        data, err = contract.load_json(path)
        items = data if isinstance(data, list) else []
        items.append(entry)
        write_json(path, items)
        return f"wrote {path}"
    if not isinstance(current, list):
        current = []
    current.append(entry)
    result[key] = current
    return f"inline {len(current)}"


# ---------------------------------------------------------------- scaffold

def cmd_scaffold(args) -> int:
    in_dir = env_path("INPUT_DIR", args.input or "/input")
    out_dir = env_path("OUTPUT_DIR", args.output or "/app/output")
    work_dir = env_path("WORK_DIR", args.work or "/app/work")
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case, err = contract.load_json(in_dir / "case.json")
    contract_map, note = contract.discover_contract(case)
    existing = load_result(out_dir)
    existing_has_status = bool(existing.get("status"))

    result = contract.align(existing, contract_map, case)
    if not existing_has_status:
        result["status"] = determine_status(case, err)

    result.setdefault("runtime", {})
    runtime = result["runtime"]
    runtime.update({
        "budget_seconds": 900,
        "budget_requests": 64,
        "max_tokens_per_response": 16384,
        "contract_source": note,
    })
    if not runtime.get("started_at"):
        runtime["started_at"] = contract.utcnow()

    if not result.get("change_log"):
        append_change(result, "scaffold", "落盘结果骨架",
                      result.get("status", "partial"), note)

    write_json(out_dir / "result.json", result)

    summary = {
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "contract_note": note,
        "contract_fields": list(contract_map.keys()),
        "input_files": sorted(
            str(p.relative_to(in_dir)) if _is_within(p, in_dir) else str(p)
            for p in in_dir.rglob("*") if p.is_file()
        ),
    }
    write_json(work_dir / "case_summary.json", summary)

    print(json.dumps({
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "contract_fields": list(contract_map.keys()),
        "result_path": str(out_dir / "result.json"),
    }, ensure_ascii=False))
    return 0


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- record

def _split_files(text):
    if not text:
        return []
    return [part.strip() for part in text.replace(" ", ",").split(",") if part.strip()]


def cmd_record(args) -> int:
    out_dir = env_path("OUTPUT_DIR", args.output or "/app/output")
    result = load_result(out_dir)
    if not result:
        print("result.json 不存在，请先运行 scaffold", file=sys.stderr)
        return 1

    action = args.action
    if action == "change":
        append_change(result, args.step or "record", args.action_text or "",
                      args.status or "ok", args.reason or "")
    elif action == "source":
        used_in = _split_files(args.used_in)
        append_to_field(result, "sources", {
            "ref": args.ref or "",
            "loc": args.loc or "",
            "kind": args.kind or "text",
            "used_in": used_in,
        }, out_dir)
        append_change(result, "evidence", f"source {args.ref}", "ok",
                      f"记录来源 {args.ref} @ {args.loc}")
    elif action == "evidence":
        refs = _split_files(args.refs)
        append_to_field(result, "evidence_map", {
            "page": args.page,
            "role": args.role or "",
            "claim": args.claim or "",
            "refs": refs,
            "loc": args.loc or "",
            "quote": args.quote or "",
        }, out_dir)
        append_change(result, "evidence", f"evidence page {args.page}", "ok",
                      f"记录第 {args.page} 页证据映射")
    elif action == "version":
        files = _split_files(args.files)
        hashes = {}
        for f in files:
            p = resolve_output_path(out_dir, f)
            hashes[f] = sha256_of(p)
        append_to_field(result, "version_records", {
            "name": args.name or f"v{len(result.get('version_records', []) or []) + 1}",
            "note": args.note or "",
            "t": contract.utcnow(),
            "files": files,
            "sha256": hashes,
        }, out_dir)
        append_change(result, "build", f"version {args.name}", "ok",
                      f"快照 {len(files)} 个文件")
    elif action == "field":
        if not args.key:
            print("--key 必填", file=sys.stderr)
            return 1
        result[args.key] = parse_value(args.value)
        append_change(result, "contract", f"field {args.key}", "ok",
                      f"设置字段 {args.key}")
    else:
        print(f"未知 action: {action}", file=sys.stderr)
        return 1

    write_json(out_dir / "result.json", result)
    print(json.dumps({"case_id": result.get("case_id"), "status": result.get("status"),
                      "action": action, "change_log_len": len(result.get("change_log", []))},
                     ensure_ascii=False))
    return 0


def parse_value(text):
    if text is None:
        return None
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        return text


# ---------------------------------------------------------------- audit

def check_artifacts(result: dict, out_dir: Path):
    artifacts = []
    issues = []
    pf = result.get("presentation_file")
    if isinstance(pf, str) and pf:
        p = resolve_output_path(out_dir, pf)
        artifacts.append({"name": "presentation_file", "path": str(p), "exists": p.exists()})
        if not p.exists():
            issues.append(f"presentation_file 指向的文件不存在: {p}")
    else:
        artifacts.append({"name": "presentation_file", "path": None, "exists": False})
    previews = result.get("preview_files")
    if isinstance(previews, list):
        for i, v in enumerate(previews):
            if isinstance(v, str) and v:
                p = resolve_output_path(out_dir, v)
                artifacts.append({"name": f"preview_files[{i}]", "path": str(p), "exists": p.exists()})
                if not p.exists():
                    issues.append(f"preview_files[{i}] 指向的文件不存在: {p}")
    return artifacts, issues


def audit(out_dir: Path, strict: bool):
    result = load_result(out_dir)
    case, err = contract.load_json(env_path("INPUT_DIR", "/input") / "case.json")
    contract_map, _ = contract.discover_contract(case)
    ok, issues, fields = contract.validate(result, contract_map)
    gaps = contract.nonempty_gaps(result, contract_map)
    artifacts, artifact_issues = check_artifacts(result, out_dir)
    if strict and gaps:
        issues.append(f"契约字段仍为空（strict）: {gaps}")
    all_issues = issues + artifact_issues
    complete_candidate = (ok and not gaps and not artifact_issues)
    return result, contract_map, fields, artifacts, all_issues, complete_candidate


def write_self_check(out_dir: Path, fields, artifacts, issues, complete_candidate) -> Path:
    path = out_dir / "self_check.json"
    write_json(path, {
        "generated_at": contract.utcnow(),
        "fields": fields,
        "artifacts": artifacts,
        "issues": issues,
        "complete_candidate": complete_candidate,
        "exit_code": 0 if not issues else 1,
    })
    return path


def cmd_check(args) -> int:
    out_dir = env_path("OUTPUT_DIR", args.output or "/app/output")
    result, contract_map, fields, artifacts, issues, complete = audit(out_dir, bool(args.strict))
    path = write_self_check(out_dir, fields, artifacts, issues, complete)
    print(json.dumps({
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "contract_fields": list(contract_map.keys()),
        "complete_candidate": complete,
        "issues": issues,
        "self_check": str(path),
    }, ensure_ascii=False))
    return 0 if not issues else 1


# ---------------------------------------------------------------- finalize

def cmd_finalize(args) -> int:
    out_dir = env_path("OUTPUT_DIR", args.output or "/app/output")
    result = load_result(out_dir)
    if not result:
        print("result.json 不存在，请先运行 scaffold", file=sys.stderr)
        return 2

    status = args.status or "partial"
    if status not in contract.STATUS_VALUES:
        print(f"非法 status: {status}", file=sys.stderr)
        return 2

    _, _, fields, artifacts, issues, complete = audit(out_dir, strict=(status == "complete"))
    if status == "complete" and not complete:
        status = "partial"
        result.setdefault("degradations", []).append({
            "t": contract.utcnow(),
            "reason": "终检发现缺口，降级为 partial",
            "issues": issues,
        })

    result["status"] = status
    result.setdefault("runtime", {})["finished_at"] = contract.utcnow()
    append_change(result, "finalize", f"终检 status={status}",
                  "ok" if status == "complete" else "partial",
                  "；".join(issues) if issues else "契约字段齐、产物齐、证据齐")

    write_json(out_dir / "result.json", result)
    path = write_self_check(out_dir, fields, artifacts, issues, complete)
    print(json.dumps({
        "case_id": result.get("case_id"),
        "final_status": status,
        "complete_candidate": complete,
        "issues": issues,
        "self_check": str(path),
    }, ensure_ascii=False))
    return 0 if status == "complete" else 1


# ---------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="学术演示稿契约主控")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="落盘 result.json 骨架（增量合并，不覆盖）")
    p.add_argument("--input"); p.add_argument("--output"); p.add_argument("--work")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("record", help="增量记录 change/source/evidence/version/field")
    p.add_argument("action", choices=["change", "source", "evidence", "version", "field"])
    p.add_argument("--output")
    p.add_argument("--step"); p.add_argument("--action-text", dest="action_text")
    p.add_argument("--status"); p.add_argument("--reason")
    p.add_argument("--ref"); p.add_argument("--loc"); p.add_argument("--kind")
    p.add_argument("--used-in", dest="used_in")
    p.add_argument("--page"); p.add_argument("--role"); p.add_argument("--claim")
    p.add_argument("--refs"); p.add_argument("--quote")
    p.add_argument("--name"); p.add_argument("--note"); p.add_argument("--files")
    p.add_argument("--key"); p.add_argument("--value")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("check", help="校验 result.json 并写 self_check.json")
    p.add_argument("--output"); p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("finalize", help="写终版 result.json 与 self_check.json")
    p.add_argument("--output"); p.add_argument("--status"); p.add_argument("--note")
    p.set_defaults(func=cmd_finalize)

    return parser


def main(argv=None) -> int:
    _reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
