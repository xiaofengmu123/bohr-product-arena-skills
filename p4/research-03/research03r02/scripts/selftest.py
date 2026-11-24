#!/usr/bin/env python3
"""Self-test for the budgeted systematic-review Skill.

Exercises the full pipeline (discover -> skeleton -> commit -> finalize ->
selfcheck) on an in-memory synthetic fixture, plus the degradation paths the
harness may hit: empty input, unparseable case.json, missing case.json, and an
unknown contract field. The fixture is clearly labelled as an example and is
NOT a formal test case or prepared answer.

Exit code 0 = all checks passed; 1 = at least one check failed.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C
import main as M


def _ns(input=None, output=None, work=None, payload=None, field=None,
        file=None, phase=None, tick=False):
    return argparse.Namespace(input=input, output=output, work=work,
                              payload=payload, field=field, file=file,
                              phase=phase, tick=tick)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_example_case(root):
    """Build a complete, clearly-marked example case (not a real case)."""
    case = {
        "case_id": "example-0001",
        "output_contract": {
            "review_question": None,
            "subquestions": [],
            "search_strategy": {"query_blocks": []},
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "screening_records": [],
            "prisma_flow": {},
            "quality_appraisal": [],
            "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
            "evidence_map": [],
        },
    }
    _write(os.path.join(root, "input", "case.json"),
           json.dumps(case, ensure_ascii=False))
    _write(os.path.join(root, "input", "task.md"),
           "# 示例任务\n围绕干预 X 对结局 Y 的效果做系统综述（合成示例数据，非正式案例）。\n")
    _write(os.path.join(root, "input", "materials", "study_a.md"),
           "# Study A（合成示例）\n\n## 结果\n\n随机分组。干预组结局优于对照组。\n")
    _write(os.path.join(root, "input", "materials", "study_b.md"),
           "# Study B（合成示例）\n\n## 结果\n\n两组结局无显著差异。\n")


def _make_example_work(work):
    _write(os.path.join(work, "protocol.json"), json.dumps({
        "review_question": "合成示例：干预 X 对结局 Y 的效果如何？（示意数据）",
        "subquestions": [
            {"id": "SQ1", "text": "X 对 Y 的效应量", "depends_on": []},
            {"id": "SQ2", "text": "效应是否随人群 Z 变化", "depends_on": ["SQ1"]},
        ],
        "search_strategy": {"query_blocks": [{
            "database": "PubMed", "query": "(X[tiab]) AND (Y[tiab])",
            "intent": "核心概念交集", "fields": "tiab",
            "time_limit": "2010-01-01..2026-01-01", "language": "en",
        }]},
        "inclusion_criteria": [{"id": "I1", "text": "随机对照试验", "priority": 1}],
        "exclusion_criteria": [{"id": "E1", "text": "无全文", "priority": 1}],
        "uncertainty": ["示例：仅两条合成证据，属示意。"],
    }, ensure_ascii=False))
    _write(os.path.join(work, "screening.jsonl"), "\n".join([
        json.dumps({"record_id": "R1", "stage": "title_abstract", "decision": "include",
                    "criterion_id": "I1", "judge": "agent"}),
        json.dumps({"record_id": "R2", "stage": "title_abstract", "decision": "exclude",
                    "reason": "非目标人群", "criterion_id": "E1", "judge": "agent"}),
        json.dumps({"record_id": "R1", "stage": "fulltext", "decision": "include",
                    "criterion_id": "I1", "judge": "agent"}),
    ]))
    _write(os.path.join(work, "quality_appraisal.jsonl"), "\n".join([
        json.dumps({"record_id": "R1", "overall": "high", "impact": "纳入结论",
                    "dimensions": [{"dim": "随机化", "basis_locator": "study_a.md#结果",
                                    "quote": "随机分组。", "rating": "low"}]}),
    ]))
    _write(os.path.join(work, "evidence_cards.jsonl"), "\n".join([
        json.dumps({"id": "EV1", "source": "示例文献 A", "locator": "study_a.md#结果",
                    "quote": "干预组结局优于对照组。"}),
        json.dumps({"id": "EV2", "source": "示例文献 B", "locator": "study_b.md#结果",
                    "quote": "两组结局无显著差异。"}),
    ]))
    _write(os.path.join(work, "synthesis.json"), json.dumps({
        "conclusions": [{"text": "示例结论：合成证据有限地支持 X 改善 Y。",
                         "evidence_ids": ["EV1"], "confidence": "suggestive"}],
        "conflicts": [{"text": "A 报正向，B 报无差异。", "sources": ["EV1", "EV2"]}],
        "gaps": [{"gap": "缺少长期随访数据", "locator": "材料未报道"}],
    }, ensure_ascii=False))


def _run_pipeline(root):
    input_dir = os.path.join(root, "input")
    output_dir = os.path.join(root, "output")
    work_dir = os.path.join(root, "work")
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        M.cmd_skeleton(_ns(input=input_dir, output=output_dir, work=work_dir))
        M.cmd_commit(_ns(input=input_dir, output=output_dir, work=work_dir,
                         payload=os.path.join(work_dir, "protocol.json"), phase="P1"))
        M.cmd_finalize(_ns(input=input_dir, output=output_dir, work=work_dir))
        M.cmd_selfcheck(_ns(input=input_dir, output=output_dir, work=work_dir))
    return output_dir


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_happy_path():
    root = tempfile.mkdtemp(prefix="sysrev_selftest_happy_")
    try:
        _make_example_case(root)
        _make_example_work(os.path.join(root, "work"))
        output = _run_pipeline(root)
        result = _read_json(os.path.join(output, "result.json"))
        checks = [
            ("case_id", result.get("case_id") == "example-0001"),
            ("status_ok", result.get("status") == C.STATUS_OK),
            ("review_question", bool(result.get("review_question"))),
            ("screening_records", len(result.get("screening_records") or []) == 3),
            ("evidence_map", len(result.get("evidence_map") or []) == 2),
            ("prisma_included", (result.get("prisma_flow") or {}).get("included_total") == 1),
            ("review_md", os.path.isfile(os.path.join(output, "review.md"))),
            ("screening_log", os.path.isfile(os.path.join(output, "screening_log.json"))),
            ("evidence_map_json", os.path.isfile(os.path.join(output, "evidence_map.json"))),
        ]
        # finalize then selfcheck: conclusions' evidence_ids must resolve.
        rep = C.validate_result(result, C.DEFAULT_CONTRACT)
        checks.append(("selfcheck_ok", rep["ok"]))
        return checks, result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _silent(fn, args):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        fn(args)


def test_empty_input():
    root = tempfile.mkdtemp(prefix="sysrev_selftest_empty_")
    try:
        input_dir = os.path.join(root, "input")
        os.makedirs(input_dir)
        output_dir = os.path.join(root, "output")
        work_dir = os.path.join(root, "work")
        _silent(M.cmd_skeleton, _ns(input=input_dir, output=output_dir, work=work_dir))
        result = _read_json(os.path.join(output_dir, "result.json"))
        return [("status", result.get("status") == C.STATUS_INSUFFICIENT),
                ("case_id_unknown", result.get("case_id") == "unknown")], result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    root = tempfile.mkdtemp(prefix="sysrev_selftest_badjson_")
    try:
        input_dir = os.path.join(root, "input")
        _write(os.path.join(input_dir, "case.json"), "{this is not json")
        _write(os.path.join(input_dir, "task.md"), "# task\n")
        output_dir = os.path.join(root, "output")
        work_dir = os.path.join(root, "work")
        _silent(M.cmd_skeleton, _ns(input=input_dir, output=output_dir, work=work_dir))
        result = _read_json(os.path.join(output_dir, "result.json"))
        return [("status_input_error", result.get("status") == C.STATUS_INPUT_ERROR),
                ("errors_recorded", bool(result.get("errors")))], result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_case():
    root = tempfile.mkdtemp(prefix="sysrev_selftest_nocase_")
    try:
        input_dir = os.path.join(root, "input")
        _write(os.path.join(input_dir, "task.md"), "# task\n")
        _write(os.path.join(input_dir, "materials", "a.md"), "# A\n")
        output_dir = os.path.join(root, "output")
        work_dir = os.path.join(root, "work")
        _silent(M.cmd_skeleton, _ns(input=input_dir, output=output_dir, work=work_dir))
        result = _read_json(os.path.join(output_dir, "result.json"))
        return [("status_partial", result.get("status") == C.STATUS_PARTIAL),
                ("case_id_unknown", result.get("case_id") == "unknown")], result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unknown_contract_field():
    root = tempfile.mkdtemp(prefix="sysrev_selftest_contract_")
    try:
        input_dir = os.path.join(root, "input")
        case = {"case_id": "c-1",
                "output_contract": {"custom_field": None, "review_question": None}}
        _write(os.path.join(input_dir, "case.json"), json.dumps(case))
        _write(os.path.join(input_dir, "task.md"), "# task\n")
        output_dir = os.path.join(root, "output")
        work_dir = os.path.join(root, "work")
        _silent(M.cmd_skeleton, _ns(input=input_dir, output=output_dir, work=work_dir))
        result = _read_json(os.path.join(output_dir, "result.json"))
        checks = [
            ("custom_field_present", "custom_field" in result),
            ("custom_field_null", result.get("custom_field") is None),
            ("status_present", "status" in result),
            ("sources_present", "sources" in result),
            ("change_log_present", "change_log" in result),
        ]
        return checks, result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    suites = [
        ("happy_path", test_happy_path),
        ("empty_input", test_empty_input),
        ("bad_json", test_bad_json),
        ("missing_case", test_missing_case),
        ("unknown_contract_field", test_unknown_contract_field),
    ]
    failures = 0
    for name, func in suites:
        try:
            checks, _ = func()
            failed = [label for label, ok in checks if not ok]
            status = "PASS" if not failed else "FAIL"
            print("[%s] %s" % (status, name))
            for label, ok in checks:
                print("    %s %s" % ("ok" if ok else "MISS", label))
            if failed:
                failures += 1
        except Exception as exc:  # noqa: BLE001 - self-test must report, not crash
            failures += 1
            print("[FAIL] %s raised %s: %s" % (name, type(exc).__name__, exc))
    print("")
    print("selftest: %d/%d suites passed" % (len(suites) - failures, len(suites)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
