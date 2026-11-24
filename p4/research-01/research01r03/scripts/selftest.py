#!/usr/bin/env python3
"""Self-test for the deep-research skill.

Checks:
  1. SKILL.md exists, has valid YAML frontmatter (name/description/version) and
     contains the required clauses.
  2. Determinism: the same demo input + payload assembled twice yields a
     byte-identical result.json.
  3. Output-contract roundtrip: every output_contract key is echoed verbatim,
     required extra fields exist, and every evidence row has locator + quote.
  4. Empty input degrades to status=insufficient_evidence without crashing.
  5. Malformed JSON is recorded in errors without crashing.

Run:  python3 -B scripts/selftest.py
Exit code 0 means all checks passed.
"""

import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resultlib as R  # noqa: E402


def skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_frontmatter(text):
    if not text.startswith("---\n"):
        return None, ["SKILL.md does not start with a --- block"]
    end = text.find("\n---", 4)
    if end < 0:
        return None, ["SKILL.md frontmatter not closed"]
    block = text[4:end]
    meta = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, []


def check_skillemd():
    problems = []
    path = os.path.join(skill_root(), "SKILL.md")
    if not os.path.isfile(path):
        return ["missing SKILL.md"]
    text, errs, _ = R.read_text(path)
    problems.extend(errs)
    meta, fm_errs = parse_frontmatter(text)
    problems.extend(fm_errs)
    if meta is not None:
        for key in ("name", "description", "version"):
            if not meta.get(key):
                problems.append("SKILL.md frontmatter missing %s" % key)
    for needle in ("输入契约", "输出契约", "900", "64", "前 3 分钟",
                   "自检清单", "降级策略", "/app/output/result.json"):
        if needle not in text:
            problems.append("SKILL.md missing required clause: %s" % needle)
    return problems


def build_demo_input(root):
    case = {
        "case_id": "demo-synthesis-001",
        "armiId": "ARMI:demo.0001",
        "output_contract": {
            "research_product": {"type": "object"},
            "evidence_matrix": {"type": "array"},
            "uncertainty": {"type": "object"},
            "conclusion_count": {"type": "integer"},
        },
    }
    R.write_json(os.path.join(root, "case.json"), case)
    R.write_text(os.path.join(root, "task.md"),
                 "# demo task\n\n综合两份材料，给出可回链的结论。")
    R.write_text(os.path.join(root, "materials", "a.md"),
                 "# 材料 A\n\np3: 干预组均值 2.4 (SD 0.3)。")
    R.write_text(os.path.join(root, "materials", "b.md"),
                 "# 材料 B\n\np1: 干预组均值 2.1 (SD 0.4)，方向一致但幅度略小。")
    return case


def build_payload():
    return {
        "status": "ok",
        "research_product": {
            "type": "synthesis",
            "question": "干预是否有效？",
            "summary": "两份材料一致支持正向效应，但幅度存在差异。",
            "conclusions": [
                {"id": "C1", "text": "干预方向一致为正。",
                 "evidence_ids": ["E1", "E2"], "strength": "established"},
                {"id": "C2", "text": "效应幅度存在差异，需谨慎合并。",
                 "evidence_ids": ["E1", "E2"], "strength": "inferred"},
            ],
        },
        "evidence_matrix": [
            {"id": "E1", "subquestion": "SQ1", "source": "materials/a.md",
             "locator": "p3", "quote": "干预组均值 2.4 (SD 0.3)",
             "claim": "正向效应", "judgment": "supports",
             "reason": "直接测量", "conclusion_ids": ["C1", "C2"]},
            {"id": "E2", "subquestion": "SQ1", "source": "materials/b.md",
             "locator": "p1", "quote": "干预组均值 2.1 (SD 0.4)",
             "claim": "方向一致但幅度小", "judgment": "partial",
             "reason": "幅度较小", "conclusion_ids": ["C1", "C2"]},
        ],
        "uncertainty": {
            "conflicts": [{"a": "materials/a.md", "b": "materials/b.md",
                           "issue": "幅度不同", "resolution": "并列记录并标 inferred"}],
            "biases": [], "gaps": ["缺少置信区间"], "undetermined": [],
        },
        "sources": [
            {"id": "S1", "path": "materials/a.md", "kind": "md", "note": "demo"},
            {"id": "S2", "path": "materials/b.md", "kind": "md", "note": "demo"},
        ],
        "pending_human": ["需要原始数据核对置信区间"],
    }


def run_pipeline(input_dir, output_dir, work_dir, payload):
    R.ensure_dir(output_dir)
    R.ensure_dir(work_dir)
    inv = R.discover_input(input_dir)
    case_obj, errs, degs = (None, [], [])
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)
    result = R.new_skeleton(case_id, contract, note="selftest skeleton")
    result["errors"] = list(errs)
    result["degradations"] = list(degs)
    R.write_json(os.path.join(output_dir, "result.json"), result)
    result = R.deep_merge(result, copy.deepcopy(payload))
    result["change_log"].append({"seq": 2, "phase": "merge", "action": "merge",
                                 "detail": "selftest payload"})
    result = R.finalize_result(result, "selftest finalize")
    R.write_json(os.path.join(output_dir, "result.json"), result)
    R.write_text(os.path.join(output_dir, "report.md"), R.render_report(result))
    R.write_json(os.path.join(output_dir, "evidence_map.json"), R.render_evidence_map(result))
    return result


def check_determinism():
    problems = []
    base = tempfile.mkdtemp(prefix="selftest_det_")
    try:
        build_demo_input(base)
        payload = build_payload()
        out1 = os.path.join(base, "out1")
        out2 = os.path.join(base, "out2")
        run_pipeline(base, out1, os.path.join(base, "w1"), payload)
        run_pipeline(base, out2, os.path.join(base, "w2"), payload)
        with open(os.path.join(out1, "result.json"), "rb") as fh:
            b1 = fh.read()
        with open(os.path.join(out2, "result.json"), "rb") as fh:
            b2 = fh.read()
        if b1 != b2:
            problems.append("result.json differs between two identical runs")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return problems


def check_contract_roundtrip():
    problems = []
    base = tempfile.mkdtemp(prefix="selftest_ct_")
    try:
        case = build_demo_input(base)
        out = os.path.join(base, "out")
        result = run_pipeline(base, out, os.path.join(base, "work"), build_payload())
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        for key in case["output_contract"]:
            if key not in obj:
                problems.append("missing output_contract field: %s" % key)
        for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
            if key not in obj:
                problems.append("missing required field: %s" % key)
        if obj.get("case_id") != "demo-synthesis-001":
            problems.append("case_id not extracted: %r" % obj.get("case_id"))
        if obj.get("status") != "ok":
            problems.append("status not propagated: %r" % obj.get("status"))
        for row in obj.get("evidence_matrix") or []:
            if not row.get("locator") or not row.get("quote"):
                problems.append("evidence row missing locator/quote: %s" % row.get("id"))
        problems.extend(R.validate_result(obj))
        if not os.path.isfile(os.path.join(out, "report.md")):
            problems.append("finalize did not write report.md")
        _ = result
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return problems


def check_empty_input():
    problems = []
    base = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        R.ensure_dir(out)
        R.ensure_dir(work)
        inv = R.discover_input(base)
        if inv.get("total_files") != 0:
            problems.append("empty dir reported %d files" % inv.get("total_files"))
        result = R.new_skeleton("", R.build_contract(None), note="empty input")
        result["status"] = R.STATUS_INSUFFICIENT
        result.setdefault("limitations", []).append("no materials found")
        R.write_json(os.path.join(out, "result.json"), result)
        obj, errs, _ = R.load_json(os.path.join(out, "result.json"))
        if errs:
            problems.extend(errs)
        if not isinstance(obj, dict) or obj.get("status") != R.STATUS_INSUFFICIENT:
            problems.append("empty input did not yield insufficient_evidence")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return problems


def check_bad_json():
    problems = []
    base = tempfile.mkdtemp(prefix="selftest_bad_")
    try:
        path = os.path.join(base, "case.json")
        R.write_text(path, "{ this is not valid json ")
        obj, errs, _ = R.load_json(path)
        if obj is not None or not errs:
            problems.append("malformed JSON was not reported as an error")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return problems


def run_selftest():
    checks = [
        ("SKILL.md frontmatter & required clauses", check_skillemd),
        ("determinism (two identical runs -> byte-identical result.json)", check_determinism),
        ("output contract roundtrip & evidence locator+quote", check_contract_roundtrip),
        ("empty input -> insufficient_evidence, no crash", check_empty_input),
        ("bad JSON -> recorded, no crash", check_bad_json),
    ]
    results = []
    for name, fn in checks:
        problems = fn()
        results.append((name, problems))
        print("[%s] %s" % ("PASS" if not problems else "FAIL", name))
        for p in problems:
            print("      - %s" % p)
    failed = sum(1 for _, p in results if p)
    print("")
    print("selftest: %d/%d checks passed" % (len(results) - failed, len(results)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_selftest())
