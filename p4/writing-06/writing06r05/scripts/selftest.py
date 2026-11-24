#!/usr/bin/env python3
"""Self-test for the paper-structure-review Skill. Stdlib only.

Scenarios:
  1. empty input dir            -> discover() returns [], build_skeleton() writes
  2. bad case.json JSON         -> tolerant load, degradation recorded
  3. minimal manuscript         -> segmentation + claim candidates found
  4. complete sample result     -> verify.validate() has no FAIL
  5. output_contract discovery  -> custom keys are added to required list

    python scripts/selftest.py        (exit 0 = all pass)
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence as ev  # noqa: E402
import verify as ver  # noqa: E402
import main as orch  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


MINIMAL_MD = (
    "# Introduction\n\n"
    "This study examines the effect of X on Y.\n"
    "The results indicate a significant effect (p < 0.05).\n\n"
    "# Methods\n\n"
    "We used 42 samples and two groups.\n\n"
    "# Conclusion\n\n"
    "Therefore X causes Y.\n"
)


def scenario_empty_input():
    root = tempfile.mkdtemp(prefix="pst_empty_")
    inp = os.path.join(root, "input")
    os.makedirs(inp)
    items = ev.discover(inp)
    check("empty_input_discover", items == [], "空目录应返回空材料列表")

    skeleton = orch.build_skeleton({"id": "case-empty"}, "", None)
    check("skeleton_case_id", skeleton.get("case_id") == "case-empty")
    check("skeleton_status", skeleton.get("status") == "partial")
    check("skeleton_change_log",
          isinstance(skeleton.get("change_log"), list)
          and len(skeleton["change_log"]) >= 1)


def scenario_bad_json():
    root = tempfile.mkdtemp(prefix="pst_badjson_")
    case = os.path.join(root, "case.json")
    write_text(case, "{ this is not valid json")
    obj, err = orch.load_json_tolerant(case)
    check("bad_json_tolerated", obj is None and err.startswith("bad json"),
          "坏 JSON 应被捕获并记录: {}".format(err))


def scenario_minimal_manuscript():
    root = tempfile.mkdtemp(prefix="pst_ms_")
    inp = os.path.join(root, "input")
    write_text(os.path.join(inp, "manuscript.md"), MINIMAL_MD)
    evt = ev.build_evidence(inp)
    check("ms_discovered", len(evt["materials"]) == 1)
    ms = evt["manuscripts"][0] if evt["manuscripts"] else None
    check("ms_sections", ms is not None and len(ms["sections"]) >= 3,
          "应识别 3 个章节标题")
    check("ms_claims", ms is not None and len(ms["claims"]) >= 1,
          "应抽取到至少一条带数字/论证动词的候选论断")
    if ms and ms["claims"]:
        c0 = ms["claims"][0]
        check("claim_trace", bool(c0.get("locator") and c0.get("quote")),
              "候选论断应带 locator+quote")


def complete_sample():
    return {
        "case_id": "case-sample",
        "status": "complete",
        "diagnosis": "# 结构诊断\n\n## 结论边界\nlocator 处存在因果过度表述。\n",
        "edit_plan": [{
            "id": "E1", "issue_id": "D3", "action": "narrow",
            "from": "manuscript.md#S3P0@L3", "to": "",
            "instruction": "把 'X causes Y' 收窄为 'X is associated with Y'",
            "reason": "相关设计与因果动词不匹配",
            "quote": "Therefore X causes Y.",
        }],
        "source_map": [{
            "claim_id": "C1", "claim": "结论声称因果关系",
            "locator": "manuscript.md#S3P0@L3", "quote": "Therefore X causes Y.",
            "source_ref": "manuscript.md#Conclusion", "confidence": "high",
        }],
        "version_boundary": {"diff_summary": "仅收窄结论动词", "material_gaps": [],
                             "anomalies": []},
        "artifacts": ["/app/output/diagnosis.md", "/app/output/source_map.json"],
        "sources": [{
            "claim_id": "C1", "locator": "manuscript.md#S3P0@L3",
            "quote": "Therefore X causes Y.", "source_ref": "manuscript.md#Conclusion",
            "confidence": "high",
        }],
        "uncertainty": [{"item": "无对照组数据", "reason": "材料未提供",
                         "effect": "因果强度待确认"}],
        "pending_human": [{"item": "确认结论动词", "why": "涉及因果表述",
                           "suggested_resolution": "作者核对设计类型"}],
        "change_log": [{"time": "t0", "action": "skeleton_written",
                        "status": "partial", "note": "骨架"},
                       {"time": "t1", "action": "analysis_done",
                        "status": "complete", "note": "六维诊断完成"}],
    }


def scenario_complete_sample():
    sample = complete_sample()
    required = ver.discover_contract_keys({"id": "case-sample"})
    checks = ver.validate(sample, required)
    fails = [c for c in checks if c["level"] == "FAIL"]
    check("complete_no_fail", not fails, "完整样本不应有 FAIL：{}".format(
        [c["item"] for c in fails]))
    # verify a broken sample is caught
    broken = dict(sample)
    broken["sources"][0]["quote"] = ""
    checks2 = ver.validate(broken, required)
    check("broken_caught", any(c["level"] == "FAIL" and "_trace" in c["item"]
                               for c in checks2),
          "缺 quote 的 sources 应被判 FAIL")


def scenario_contract_discovery():
    case = {"id": "c1", "output_contract": {"custom_field": "x", "extra_list": []}}
    required = ver.discover_contract_keys(case)
    check("custom_key_added", "custom_field" in required and "extra_list" in required)
    # default keys always present
    for k in ver.RUNTIME_REQUIRED:
        check("required_{}".format(k), k in required)


def main():
    scenarios = [scenario_empty_input, scenario_bad_json,
                 scenario_minimal_manuscript, scenario_complete_sample,
                 scenario_contract_discovery]
    for fn in scenarios:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(fn.__name__, False, "异常: {!r}".format(e))

    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    n_total = len(RESULTS)
    for name, ok, detail in RESULTS:
        print("[{}] {} {}".format("PASS" if ok else "FAIL", name, detail))
    print("summary: {}/{} passed".format(n_pass, n_total))
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
