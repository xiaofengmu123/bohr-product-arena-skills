#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the proposal skill scripts.

Runs against in-memory fixtures and a temporary directory, so it never depends
on /input or /app being present. Exits 0 when all checks pass, 1 otherwise.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def _sample_case(shape="map"):
    if shape == "map":
        return {
            "case_id": "case-001",
            "output_contract": {
                "title": {"required": True},
                "proposal": {"required": True},
                "evidence_map": {"required": True},
            },
        }
    if shape == "list":
        return {"id": "case-002", "output_schema": ["title", "manuscript", "references"]}
    if shape == "fields_wrapper":
        return {"case": "case-003", "required_fields": {"fields": ["title", "notes"]}}
    return {}


def _sample_result(case=None):
    result = core.init_result(case or _sample_case(), source_path="fixture")
    result["title"] = "自检：某课题开题方案"
    result["summary"] = "仅用于脚本自检的示例内容，非正式案例结果。"
    seven = result["seven_elements"]
    seven["problems"] = [{"question": "Q1 是否为可证伪问题？", "falsifiable": "观察到 X 且排除 Y"}]
    seven["objectives"] = [{"problem_id": "Q1", "goal": "验证 X", "metric": "准确率",
                            "sample": "n=30", "control": "空白对照", "timepoint": "第 2 个月"}]
    seven["technical_route"] = [{"step": "预处理", "input": "原始数据", "output": "干净数据",
                                 "depends_on": [], "parallel_with": ["文献复核"]}]
    seven["stage_plan"] = {"milestones": ["M1 数据就绪（第 1 月）"], "critical_path": ["预处理→建模"],
                           "resources": ["2 人月"], "parallel": ["文献复核"]}
    seven["risks"] = [{"category": "data_acquisition", "risk": "样本不足",
                       "trigger": "n<20", "impact": "结论无统计效力", "fallback": "扩展来源",
                       "decision_point": "第 3 周末"}]
    seven["expected_outcomes"] = [{"outcome": "可复核结论", "criterion": "复现实验",
                                   "linked_objective": "验证 X"}]
    result["evidence_map"] = [{"id": "ev-01", "claim": "材料称样本 n=30",
                               "locator": "fixture/paper.md#p2", "quote": "n=30",
                               "kind": "fact", "target": "objectives[0]"}]
    result["four_checks"]["stand_up"] = {"status": "pass", "note": "fixture", "evidence": ["ev-01"]}
    return result


def _check(name, cond, detail):
    return {"name": name, "pass": bool(cond), "detail": detail}


def run():
    results = []
    tmp = tempfile.mkdtemp(prefix="proposal-selftest-")

    try:
        # 1. contract extraction across shapes
        for shape, expected in (("map", {"title", "proposal", "evidence_map"}),
                                ("list", {"title", "manuscript", "references"}),
                                ("fields_wrapper", {"title", "notes"})):
            got = set(core.find_contract(_sample_case(shape)).keys())
            results.append(_check("contract_shape_%s" % shape, got == expected,
                                  "got=%s want=%s" % (sorted(got), sorted(expected))))

        # 2. init produces valid skeleton with contract keys + envelope
        case = _sample_case("map")
        r = core.init_result(case, source_path="fixture")
        ok = (r.get("case_id") == "case-001" and r.get("status") == "partial"
              and "title" in r and "proposal" in r and "evidence_map" in r
              and isinstance(r.get("sources"), list)
              and isinstance(r.get("uncertainty"), list)
              and isinstance(r.get("pending_human"), list)
              and isinstance(r.get("change_log"), list))
        results.append(_check("init_skeleton", ok, "keys=%s" % sorted(r.keys())))

        # 3. check passes on populated result (FAIL=0)
        sample = _sample_result(case)
        fails, warns = core.check_result(sample, output_dir=tmp, case=case)
        results.append(_check("check_populated", len(fails) == 0,
                              "fails=%d warns=%d" % (len(fails), len(warns))))

        # 4. check detects a broken result
        broken = json.loads(json.dumps(sample))
        broken.pop("case_id")
        del broken["seven_elements"]["risks"]
        fails_b, _ = core.check_result(broken, output_dir=tmp, case=case)
        results.append(_check("check_detects_fail", len(fails_b) >= 2,
                              "fails=%d" % len(fails_b)))

        # 5. markdown rendering is non-empty and contains the seven sections
        md = core.proposal_to_markdown(sample)
        ok_md = all(s in md for s in ("## 1. 研究背景", "## 2. 问题定义", "## 3. 目标",
                                      "## 4. 技术路线", "## 5. 阶段计划",
                                      "## 6. 风险", "## 7. 预期成果"))
        results.append(_check("render_markdown", ok_md, "len=%d" % len(md)))

        # 6. docx is a valid zip (PK magic) and contains the document part
        docx_path = os.path.join(tmp, "proposal.docx")
        core.md_to_docx(md, docx_path)
        with open(docx_path, "rb") as fh:
            magic = fh.read(4)
        ok_docx = magic == b"PK\x03\x04"
        results.append(_check("render_docx", ok_docx, "magic=%r" % magic))

        # 7. build_artifacts writes the four artifacts and records them
        artifacts = core.build_artifacts(sample, output_dir=tmp)
        paths = [a.get("path") for a in artifacts if isinstance(a, dict)]
        ok_art = all(os.path.exists(os.path.join(tmp, p)) for p in paths) and len(paths) >= 3
        results.append(_check("build_artifacts", ok_art, "artifacts=%s" % paths))

    except Exception as exc:  # pragma: no cover
        results.append(_check("unexpected_exception", False, repr(exc)))

    passed = [r for r in results if r["pass"]]
    summary = {"ok": len(passed) == len(results), "passed": len(passed),
               "total": len(results), "results": results}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(run())
