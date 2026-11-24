"""selftest.py — self-check battery for the plan-comparison skill.

Runs the documented robustness checks: empty input, bad JSON, missing
materials, deterministic repeat, and artifact format validation. Uses only
temp directories; the synthetic fixture is clearly labeled a demo, never a
formal case or a benchmark score.
"""

import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

import ingest
import compare

PASS, FAIL = "PASS", "FAIL"


def _report(name, ok, detail=""):
    print("[%s] %s%s" % (PASS if ok else FAIL, name, (" - " + detail if detail else "")))
    return ok


def _make_case(base, candidates=True, bad_json=False, output_contract=True):
    os.makedirs(os.path.join(base, "materials"), exist_ok=True)
    if bad_json:
        with open(os.path.join(base, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
    else:
        case = {"case_id": "selftest-case"}
        if output_contract:
            case["output_contract"] = {"experimental_plan": "object",
                                       "evidence_and_risks": "object",
                                       "confirmation_items": "list"}
        if candidates:
            case["candidates"] = [
                {"plan_id": "P1", "name": "route A", "cost": {"total": 100, "currency": "CNY"}},
                {"plan_id": "P2", "name": "route B", "cost": {"total": 200, "currency": "CNY"}},
            ]
        ingest.write_json(os.path.join(base, "case.json"), case)
    with open(os.path.join(base, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest case (example only)\n")
    with open(os.path.join(base, "materials", "quote.csv"), "w", encoding="utf-8") as fh:
        fh.write("item,amount,unit\nreagent,10,CNY\n")
    return base


def _full_run(root, base):
    """inspect -> write IR -> assemble -> validate; returns (result, validation, output_dir)."""
    input_dir = os.path.join(base, "input")
    output_dir = os.path.join(base, "output")
    work_dir = os.path.join(base, "work")
    pack = ingest.build_case_pack(input_dir)
    ingest.write_json(os.path.join(work_dir, "case_pack.json"), pack)
    skeleton = ingest.build_skeleton(pack)
    ingest.write_json(os.path.join(output_dir, "result.json"), skeleton)

    ledger = {"case_id": pack["case_id"], "entries": [
        {"id": "E1", "claim": "reagent unit price", "locator": "materials/quote.csv#L2",
         "quote": "reagent,10,CNY", "kind": "quote", "confidence": "high"}]}
    ingest.write_json(os.path.join(work_dir, "evidence_ledger.json"), ledger)
    spec = {
        "case_id": pack["case_id"],
        "research_question": {"testable_question": "t", "variables": {"independent": [], "dependent": [], "controlled": []},
                              "hypothesis": "h"},
        "comparison_frame": {"objectives": ["o"], "hard_constraints": [],
                             "criteria": list(ingest.FIVE_DIMS)},
        "weights": ingest.DEFAULT_WEIGHTS,
        "candidates": [
            {"plan_id": "P1", "name": "route A",
             "seven_fields": {f: "sf %s" % f for f in ingest.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 3},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
             "risks": [], "evidence_refs": ["E1"],
             "cost": {"currency": "CNY", "method": "computed",
                      "lines": [{"item": "reagent", "amount": 10, "unit": "CNY", "per": "run", "runs": 1}]},
             "time": {"unit": "h", "method": "computed",
                      "parts": {"prep": 1, "scheduling": 1, "execution": 2, "characterization": 1, "rework": 0}}},
            {"plan_id": "P2", "name": "route B",
             "seven_fields": {f: "sf %s" % f for f in ingest.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 3, "risk": 3, "cost": 2, "feasibility": 3, "evidence": 2},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
             "risks": [], "evidence_refs": ["E1"],
             "cost": {"currency": "CNY", "method": "computed",
                      "lines": [{"item": "reagent", "amount": 20, "unit": "CNY", "per": "run", "runs": 1}]},
             "time": {"unit": "h", "method": "computed",
                      "parts": {"prep": 1, "scheduling": 1, "execution": 2, "characterization": 1, "rework": 0}}},
        ],
        "selection": {"chosen": "P1", "ranking": ["P1", "P2"], "rationale": "cheaper",
                      "rejected": [{"plan_id": "P2", "reason": "higher cost"}], "sensitivity_notes": ""},
        "confirmations": [], "uncertainty": [], "change_log": [],
    }
    ingest.write_json(os.path.join(work_dir, "plan_spec.json"), spec)
    result = compare.assemble(pack, ledger, spec, output_dir, work_dir, ingest.utc_now())
    ingest.write_json(os.path.join(output_dir, "result.json"), result)
    v = compare.validate_result(os.path.join(output_dir, "result.json"), work_dir, input_dir)
    return result, v, output_dir


def _check_artifacts(output_dir, result):
    ok = True
    artifacts = {os.path.basename(a["path"]): a for a in result.get("artifacts", [])}
    svg = artifacts.get("comparison_matrix.svg", {}).get("path")
    if svg and os.path.isfile(svg):
        try:
            ET.parse(svg)
        except Exception:
            ok = False
    else:
        ok = False
    csvp = artifacts.get("comparison_matrix.csv", {}).get("path")
    if csvp and os.path.isfile(csvp):
        with open(csvp, "rb") as fh:
            ok = ok and fh.read(3) == b"\xef\xbb\xbf"
    else:
        ok = False
    pdf = artifacts.get("comparison_matrix.pdf", {}).get("path")
    if pdf and os.path.isfile(pdf):
        with open(pdf, "rb") as fh:
            data = fh.read()
        ok = ok and data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
    else:
        ok = False
    return ok


def _normalize_for_determinism(obj):
    """Strip timestamps and absolute artifact paths so only computed content is compared."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("time", "generated_at", "started_at", "assemble_at", "artifacts_at", "runtime"):
                continue
            if k == "path":
                out[k] = os.path.basename(str(v))
            else:
                out[k] = _normalize_for_determinism(v)
        return out
    if isinstance(obj, list):
        return [_normalize_for_determinism(v) for v in obj]
    return obj


def run():
    results = []
    root = tempfile.mkdtemp(prefix="plan-compare-selftest-")

    # 1. empty input dir
    base = os.path.join(root, "empty")
    os.makedirs(os.path.join(base, "input"), exist_ok=True)
    pack = ingest.build_case_pack(os.path.join(base, "input"))
    skel = ingest.build_skeleton(pack)
    results.append(_report("empty input -> valid skeleton, honest status",
                           skel.get("status") in (ingest.STATUS_INPUT_ERROR, ingest.STATUS_INSUFFICIENT)
                           and skel.get("case_id")))

    # 2. bad JSON
    base = os.path.join(root, "badjson")
    _make_case(os.path.join(base, "input"), bad_json=True)
    pack = ingest.build_case_pack(os.path.join(base, "input"))
    skel = ingest.build_skeleton(pack)
    results.append(_report("bad JSON -> recorded, status input_error",
                           skel.get("status") == ingest.STATUS_INPUT_ERROR and "bad_json" in " ".join(pack["errors"])))

    # 3. missing materials (no candidates)
    base = os.path.join(root, "nomaterials")
    _make_case(os.path.join(base, "input"), candidates=False)
    pack = ingest.build_case_pack(os.path.join(base, "input"))
    skel = ingest.build_skeleton(pack)
    results.append(_report("no candidates -> insufficient_evidence",
                           skel.get("status") == ingest.STATUS_INSUFFICIENT))

    # 4. full run + artifacts + determinism
    base = os.path.join(root, "full")
    _make_case(os.path.join(base, "input"), candidates=True)
    r1, v1, out1 = _full_run(root, base)
    ok_art = _check_artifacts(out1, r1)
    results.append(_report("full run artifacts valid (svg/csv/pdf)", ok_art))
    results.append(_report("full run validate valid=true", v1["valid"], "; ".join(v1["issues"])))

    base2 = os.path.join(root, "full2")
    _make_case(os.path.join(base2, "input"), candidates=True)
    r2, v2, out2 = _full_run(root, base2)
    same = (_normalize_for_determinism(r1) == _normalize_for_determinism(r2))
    results.append(_report("deterministic repeat", same))

    # 5. hard threshold elimination (budget violated)
    base3 = os.path.join(root, "threshold")
    _make_case(os.path.join(base3, "input"), candidates=True)
    pack3 = ingest.build_case_pack(os.path.join(base3, "input"))
    ingest.write_json(os.path.join(base3, "work", "case_pack.json"), pack3)
    ledger3 = {"case_id": pack3["case_id"], "entries": [
        {"id": "E1", "claim": "reagent price", "locator": "materials/quote.csv#L2",
         "quote": "reagent,10,CNY", "kind": "quote", "confidence": "high"}]}
    ingest.write_json(os.path.join(base3, "work", "evidence_ledger.json"), ledger3)
    spec3 = {
        "case_id": pack3["case_id"],
        "research_question": {"testable_question": "t", "variables": {}, "hypothesis": "h"},
        "comparison_frame": {"objectives": ["o"],
                             "hard_constraints": [{"name": "budget", "op": "<=", "value": 150, "unit": "CNY"}],
                             "criteria": list(ingest.FIVE_DIMS)},
        "weights": ingest.DEFAULT_WEIGHTS,
        "candidates": [
            {"plan_id": "P1", "name": "A", "seven_fields": {f: "sf" for f in ingest.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 3},
             "feasibility": {"equipment_available": "yes"}, "risks": [], "evidence_refs": ["E1"],
             "cost": {"currency": "CNY", "method": "override", "total": 100, "lines": []},
             "time": {"method": "computed", "parts": {"execution": 1}}},
            {"plan_id": "P2", "name": "B", "seven_fields": {f: "sf" for f in ingest.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 3},
             "feasibility": {"equipment_available": "yes"}, "risks": [], "evidence_refs": ["E1"],
             "cost": {"currency": "CNY", "method": "override", "total": 200, "lines": []},
             "time": {"method": "computed", "parts": {"execution": 1}}},
        ],
        "selection": {"chosen": "P1", "ranking": ["P1"], "rationale": "",
                      "rejected": [{"plan_id": "P2", "reason": "budget"}], "sensitivity_notes": ""},
        "confirmations": [], "uncertainty": [], "change_log": [],
    }
    ingest.write_json(os.path.join(base3, "work", "plan_spec.json"), spec3)
    r3 = compare.assemble(pack3, ledger3, spec3, os.path.join(base3, "output"),
                          os.path.join(base3, "work"), ingest.utc_now())
    elim = r3["comparison_matrix"]["eliminations"]
    results.append(_report("budget threshold eliminates P2", "budget" in " ".join(elim.get("P2", []))))

    shutil.rmtree(root, ignore_errors=True)
    ok_all = all(results)
    print("selftest %s (%d/%d)" % (PASS if ok_all else FAIL, sum(1 for r in results if r), len(results)))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(run())
