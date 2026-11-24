#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robust bootstrap: discover input, build an initial valid result.json.

Never crashes. On empty input, bad JSON, missing params or oversized files it
degrades to a legal partial result with exit code 0.

Run from the skill root:
  python -B scripts/main.py --input /input --output /app/output --work /app/work
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skeleton  # noqa: E402


def _default_input() -> str:
    return skeleton._default_input()


def _default_output() -> str:
    return "/app/output" if os.path.isdir("/app") else "output"


def _default_work() -> str:
    return "/app/work" if os.path.isdir("/app") else "work"


def summarize_materials(files):
    """Small, deterministic digest of available materials."""
    text_files = []
    for f in files:
        if f.get("skipped"):
            continue
        rel = f.get("rel", "")
        lower = rel.lower()
        if lower.endswith((".md", ".txt", ".json", ".csv", ".tex", ".markdown")):
            text_files.append(rel)
    return {
        "file_count": len(files),
        "text_files": text_files[:50],
        "oversized_skipped": sum(1 for f in files if f.get("skipped")),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Robust bootstrap for grant-writing Skill")
    ap.add_argument("--input", default=_default_input())
    ap.add_argument("--output", default=_default_output())
    ap.add_argument("--work", default=_default_work())
    ap.add_argument("--out", default=None, help="override result.json path")
    a = ap.parse_args(argv)

    started_at = skeleton.utcnow()
    t0 = time.time()

    for d in (a.output, a.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass

    out_path = a.out or os.path.join(a.output, "result.json")
    case_obj, case_err = skeleton.load_json(os.path.join(a.input, "case.json"))
    task_text, task_err = skeleton.read_text(os.path.join(a.input, "task.md"))

    result = skeleton.build_skeleton(case_obj, a.input, started_at)

    # Load task metadata without failing.
    if task_err:
        result["errors"].append("task.md:" + task_err)
    if task_text is None and not os.path.exists(os.path.join(a.input, "task.md")):
        result["errors"].append("missing_task_md")

    # case.json handling
    bad_case = bool(case_err) and case_err.startswith("bad_json")
    if case_err:
        result["errors"].append("case.json:" + case_err)
        if bad_case:
            result["uncertainty"].append({"item": "bad_case_json",
                                          "reason": case_err})

    # case_id
    cid = skeleton.case_id_from(case_obj)
    if cid:
        result["case_id"] = cid
    elif case_obj is None and case_err:
        result["case_id"] = None
        result["pending_human"].append("case_id_unavailable:%s" % (case_err or "no case.json"))

    # Skill's own seven-element / four-check framework scaffold (domain knowledge,
    # not facts extracted from the case). Included so incremental result.json edits
    # carry the framework the Agent must populate.
    result["_framework"] = {
        "seven_elements": [
            "立项依据", "科学问题", "研究目标", "研究内容",
            "创新点", "技术路线", "研究基础与可行性",
        ],
        "four_checks": ["立得住", "说得准", "对得上", "查得到"],
        "material_summary": summarize_materials(result.get("sources") or []),
    }

    # Fallback artifact path once a draft exists (marker only).
    if result.get("artifact") is None:
        result["artifact"] = {"path": None, "type": "markdown", "description": ""}
    result.setdefault("artifact", {})
    if not result["artifact"].get("path"):
        result["artifact"]["path"] = None

    # Deterministic status: bad case.json -> input_error; no material files ->
    # insufficient_evidence; otherwise partial (the agent upgrades to done).
    if bad_case:
        result["status"] = "input_error"
    elif not result.get("sources"):
        result["status"] = "insufficient_evidence"
    else:
        result["status"] = "partial"

    result["runtime"]["finished_at"] = skeleton.utcnow()
    result["runtime"]["seconds"] = round(time.time() - t0, 3)
    result["change_log"].append({
        "time": skeleton.utcnow(),
        "action": "bootstrap",
        "reason": "Step 1 material inventory and initial result",
        "impact": "sources snapshot + contract fields + status set",
    })

    ok, err = skeleton.write_json(result, out_path)
    if not ok:
        sys.stderr.write("main write failed: %s\n" % err)
        return 1
    print(json_summary(result, out_path))
    return 0


def json_summary(result, out_path):
    import json
    return json.dumps({
        "ok": True,
        "out": out_path,
        "status": result.get("status"),
        "case_id": result.get("case_id"),
        "sources": len(result.get("sources", [])),
        "errors": len(result.get("errors", [])),
        "degradations": len(result.get("degradations", [])),
    }, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
