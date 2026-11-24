# -*- coding: utf-8 -*-
"""main.py — 薄 CLI 分发：discover / scaffold / verify。

- discover：扫描 /input，抽取 case_id 与 output_contract，写 work/ 中间态。
- scaffold：从 contract_spec 生成 partial result.json 骨架 + 空台账（<3 分钟先落盘）。
- verify：校验合同回填与稿件/台账一致性，并从台账派生 evidence_map/change_log。

退出码：0 正常；2 输入/结果不可解析；3 校验存在硬伤（errors）。
逻辑全部在 core_impl.py；本文件保持 <30KB 的薄入口。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402

DEFAULTS = {"input": "/input", "work": "/app/work", "output": "/app/output"}


def _ns(args, name):
    value = getattr(args, name, None)
    return value or DEFAULTS[name]


def _load_spec(work_dir: str) -> dict:
    spec, err = C.load_json(os.path.join(work_dir, "contract_spec.json"), default=None)
    if spec is None or not isinstance(spec, dict):
        return {"case_id": None, "contract_fields": [], "case_error": err, "manifest": None}
    return spec


def cmd_discover(args) -> int:
    input_dir = _ns(args, "input")
    work_dir = C.ensure_dir(_ns(args, "work"))
    manifest = C.scan_input(input_dir)

    case_obj = None
    case_err = None
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        case_obj, case_err = C.load_json(case_path)

    contract = C.find_contract(case_obj) if case_obj is not None else None
    fields = C.normalize_contract(contract)
    case_id = C.find_case_id(case_obj) if case_obj is not None else None

    spec = {
        "case_id": case_id,
        "contract_fields": fields,
        "case_error": case_err,
        "case_loaded": bool(case_obj),
        "manifest": manifest,
    }
    C.write_text(os.path.join(work_dir, "input_manifest.json"), C.dumps(manifest))
    C.write_text(os.path.join(work_dir, "contract_spec.json"), C.dumps(spec))
    if case_obj is not None:
        C.write_text(os.path.join(work_dir, "case_normalized.json"), C.dumps(case_obj))

    summary = {
        "ok": True,
        "case_id": case_id,
        "file_count": len(manifest.get("files", [])),
        "contract_fields": [f.get("name") for f in fields],
        "degradations": manifest.get("degradations", []),
    }
    print(C.dumps(summary))
    return 0


def cmd_scaffold(args) -> int:
    input_dir = _ns(args, "input")
    work_dir = _ns(args, "work")
    output_dir = C.ensure_dir(_ns(args, "output"))
    spec = _load_spec(work_dir)
    case_id = spec.get("case_id")
    fields = spec.get("contract_fields", [])
    manifest = spec.get("manifest") or C.scan_input(input_dir)
    case_error = spec.get("case_error")
    status = C.initial_status(case_id, manifest, case_error)
    result = C.emit_skeleton(case_id, fields, status)
    if case_error is not None:
        result["errors"].append(case_error)
        result["degradations"].append({"issue": "case_json_unparseable"})
    ledger = C.empty_ledger(case_id)
    C.write_text(os.path.join(work_dir, "ledger.json"), C.dumps(ledger))
    C.write_text(os.path.join(output_dir, "result.json"), C.dumps(result))
    print(C.dumps({
        "ok": True,
        "case_id": case_id,
        "status": result["status"],
        "contract_fields": [f.get("name") for f in fields],
    }))
    return 0


def cmd_verify(args) -> int:
    work_dir = _ns(args, "work")
    output_dir = _ns(args, "output")
    result, rerr = C.load_json(os.path.join(output_dir, "result.json"), default=None)
    if result is None or not isinstance(result, dict):
        print(C.dumps({"ok": False, "errors": ["result_json_missing_or_bad"], "detail": rerr}))
        return 2

    spec = _load_spec(work_dir)
    fields = spec.get("contract_fields", [])
    ledger, _ = C.load_json(os.path.join(work_dir, "ledger.json"), default=None)
    manuscript, _, _ = C.read_text(os.path.join(output_dir, "manuscript.md"))

    report = C.validate_result(result, fields, ledger, manuscript)
    if ledger is not None:
        C.write_text(os.path.join(output_dir, "evidence_map.json"), C.dumps(C.derive_evidence_map(ledger)))
        C.write_text(os.path.join(output_dir, "change_log.json"), C.dumps(C.derive_change_log(ledger)))
        C.write_text(os.path.join(output_dir, "preserved_facts.json"), C.dumps(C.derive_preserved_facts(ledger)))
    C.write_text(os.path.join(work_dir, "verify_report.json"), C.dumps(report))
    print(C.dumps(report))
    return 0 if report["ok"] else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="引言与相关工作写作：发现 / 落骨架 / 校验（合同先行 + 证据台账）。",
    )
    parser.add_argument("command", choices=["discover", "scaffold", "verify"])
    parser.add_argument("--input", default=None, help="输入目录（缺省 /input）")
    parser.add_argument("--work", default=None, help="工作目录（缺省 /app/work）")
    parser.add_argument("--output", default=None, help="产物目录（缺省 /app/output）")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {
        "discover": cmd_discover,
        "scaffold": cmd_scaffold,
        "verify": cmd_verify,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
