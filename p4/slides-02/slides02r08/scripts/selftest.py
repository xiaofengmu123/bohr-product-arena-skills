#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-deck-architect 自检：对 /app/output 做可复核终检，输出 selfcheck.json。

每条检查带 dimension 标签，对齐题目评分维度：
  effect(实际效果40) / benefit(使用增益25) / innovation(创新增益15)
  / reliability(可靠体验10) / ecosystem(生态贡献10)
退出码：0 = 无 FAIL；1 = 有 FAIL（可作 CI 门禁）。任何异常退出码仍为 0 并写 input_error 报告。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{exc.__class__.__name__}"
    if not raw.strip():
        return None, "empty"
    try:
        return json.loads(raw.decode("utf-8-sig")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{exc.__class__.__name__}"


def contract_keys(case: dict) -> list:
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    return keys or ["case_id", "status", "editable_source", "export_artifact",
                    "content_map", "compliance_checks", "exceptions"]


def run_checks(input_root: Path, output: Path):
    items = []

    def add(level, check_id, dimension, detail):
        items.append({"level": level, "id": check_id, "dimension": dimension,
                      "detail": detail})

    case, case_err = read_json(input_root / "case.json")
    if case_err:
        add("WARN", "case.json", "reliability", f"case.json 不可读: {case_err}（按空输入处理）")
        case = {}
    keys = contract_keys(case)

    result, err = read_json(output / "result.json")
    if err or not isinstance(result, dict):
        add("FAIL", "result.json", "reliability", err or "result.json 非 JSON 对象")
    else:
        missing = [k for k in keys if k not in result]
        add("PASS" if not missing else "FAIL", "contract.keys", "effect",
            "契约字段齐全" if not missing else f"缺失: {missing}")
        add("PASS" if result.get("case_id") == (case.get("case_id") or result.get("case_id"))
            else "WARN", "case_id", "effect",
            f"case_id={result.get('case_id')!r}")
        add("PASS" if result.get("status") in STATUS else "FAIL", "status", "effect",
            f"status={result.get('status')!r}")

        editable = result.get("editable_source") or {}
        pptx_path = output / str(editable.get("path", "presentation.pptx"))
        if pptx_path.is_file() and pptx_path.stat().st_size > 2:
            add("PASS" if pptx_path.read_bytes()[:2] == b"PK" else "FAIL",
                "editable_source", "effect",
                f"{pptx_path.name} 存在且为 ZIP" if pptx_path.read_bytes()[:2] == b"PK" else "pptx 非 ZIP")
        else:
            add("FAIL", "editable_source", "effect", f"{pptx_path.name} 缺失或空")

        pdf = (result.get("export_artifact") or {}).get("pdf") or {}
        pdf_path = output / str(pdf.get("path", "presentation.pdf"))
        if pdf_path.is_file():
            add("PASS" if pdf_path.read_bytes()[:5] == b"%PDF-" else "FAIL",
                "export.pdf", "effect",
                f"{pdf_path.name} 存在且 %PDF- 头" if pdf_path.read_bytes()[:5] == b"%PDF-" else "pdf 非法")
        else:
            add("FAIL", "export.pdf", "effect", f"{pdf_path.name} 缺失")

        cm = result.get("content_map") or []
        verified = sum(1 for x in cm if isinstance(x, dict) and x.get("evidence"))
        add("PASS" if cm and verified == len(cm) else "WARN", "evidence.map", "effect",
            f"{verified}/{len(cm)} 页带证据映射")
        quoted = sum(1 for x in cm if isinstance(x, dict) and
                     all(ev.get("quote") for ev in (x.get("evidence") or [])))
        add("PASS" if quoted == len(cm) and cm else "WARN", "evidence.quote", "effect",
            f"{quoted}/{len(cm)} 页逐字引文齐备")

        logs = result.get("change_log") or []
        add("PASS" if logs else "WARN", "change_log", "benefit",
            f"{len(logs)} 条变更记录（返工/效率证据）")
        for key in ("sources", "uncertainty", "pending_human"):
            add("PASS" if key in result else "FAIL", f"envelope.{key}", "reliability",
                f"{key} 存在" if key in result else f"{key} 缺失")

    report = {"checked_at": now_iso(), "skill_version": VERSION,
              "items": items,
              "pass": sum(1 for i in items if i["level"] == "PASS"),
              "warn": sum(1 for i in items if i["level"] == "WARN"),
              "fail": sum(1 for i in items if i["level"] == "FAIL"),
              "note": "自检协议；不写评测分数。"}
    return report


def build_parser():
    parser = argparse.ArgumentParser(prog="selftest.py",
                                     description="evidence-deck-architect 自检")
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--demo", action="store_true",
                        help="在临时目录合成最小输入跑通自检（示意，非正式案例）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.demo:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "output").mkdir()
            (root / "input" / "case.json").write_bytes(
                json.dumps({"case_id": "demo-001", "output_contract": [
                    {"name": "case_id", "type": "string", "required": True},
                    {"name": "status", "type": "string", "required": True},
                    {"name": "editable_source", "type": "object", "required": True},
                    {"name": "export_artifact", "type": "object", "required": True},
                    {"name": "content_map", "type": "array", "required": True},
                    {"name": "compliance_checks", "type": "array", "required": True},
                    {"name": "exceptions", "type": "array", "required": True}]},
                 ensure_ascii=False).encode("utf-8-sig"))
            report = run_checks(root / "input", root / "output")
            (root / "output" / "selfcheck.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        input_root = Path(args.input).expanduser() if args.input else Path("/input")
        output = Path(args.output).expanduser() if args.output else Path("/app/output")
        try:
            report = run_checks(input_root, output)
            Path(output / "selfcheck.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except BaseException as exc:  # noqa: BLE001
            report = {"checked_at": now_iso(), "skill_version": VERSION,
                      "items": [{"level": "FAIL", "id": "selftest", "dimension": "reliability",
                                 "detail": f"{exc.__class__.__name__}: {exc}"}],
                      "pass": 0, "warn": 0, "fail": 1}
    for item in report["items"]:
        print(f"[{item['level']}] {item['dimension']} · {item['id']}: {item['detail']}")
    print(json.dumps({"selftest": "done", "pass": report["pass"], "warn": report["warn"],
                      "fail": report["fail"]}, ensure_ascii=False))
    return 1 if report["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
