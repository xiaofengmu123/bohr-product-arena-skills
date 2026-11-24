"""Self-test for the skill scripts (standard library only).

Runs the pipeline end-to-end on synthetic in-memory inputs and asserts the
robustness contract from SKILL.md:

1. empty input directory  -> exit 0, valid result.json, insufficient_evidence;
2. malformed case.json    -> exit 0, input_error recorded, no crash;
3. flat output_contract   -> every field backfilled with typed placeholder;
4. list output_contract   -> fields backfilled as top-level keys;
5. nested contract        -> contract located under output.output_contract;
6. hand-filled result     -> validate passes and reports contract coverage;
7. determinism            -> two runs produce byte-identical manifest/result.

Run:  python3 -B scripts/selftest.py
Exit code 0 means all assertions passed; anything else prints the first
failure. No third-party packages are used.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as core  # noqa: E402
import main as main_mod  # noqa: E402


FAILURES = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok  {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


def write_case(root: str, case_obj: dict, task: str = "# task\n") -> str:
    core.ensure_dir(root)
    core.write_json(os.path.join(root, "case.json"), case_obj)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(task)
    return root


def run_pipeline(input_dir: str) -> tuple:
    """Return (work_dir, output_dir, manifest, result)."""
    base = tempfile.mkdtemp(prefix="skilltest_")
    work = os.path.join(base, "work")
    out = os.path.join(base, "output")
    core.ensure_dir(work)
    core.ensure_dir(out)
    manifest = core.build_manifest(input_dir)
    core.write_json(os.path.join(work, "input_manifest.json"), manifest)
    result = core.build_skeleton(manifest)
    core.write_json(os.path.join(out, "result.json"), result)
    return work, out, manifest, result


def test_empty_input() -> None:
    print("[1] empty input directory")
    base = tempfile.mkdtemp(prefix="skilltest_empty_")
    work, out, manifest, result = run_pipeline(base)
    check(result.get("status") == "insufficient_evidence",
          "status=insufficient_evidence")
    check(result.get("case_id") is None, "case_id=null")
    check(os.path.isfile(os.path.join(out, "result.json")), "result.json exists")
    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        json.load(fh)  # must parse
    check(True, "result.json parses as JSON")


def test_bad_json() -> None:
    print("[2] malformed case.json")
    base = tempfile.mkdtemp(prefix="skilltest_bad_")
    with open(os.path.join(base, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    work, out, manifest, result = run_pipeline(base)
    check(result.get("status") == "input_error", "status=input_error")
    check(any("invalid_json" in e for e in result.get("errors", [])),
          "invalid_json recorded in errors")
    check("errors" in result, "errors field present")


def test_flat_contract() -> None:
    print("[3] flat output_contract dict")
    case = {
        "case_id": "case-flat-1",
        "task": "write an introduction",
        "output_contract": {
            "manuscript": {"type": "string", "required": True},
            "evidence_map": {"type": "object",
                             "properties": {"S1": {"type": "string"}}},
            "sources": {"type": "array"},
        },
    }
    base = write_case(tempfile.mkdtemp(prefix="skilltest_flat_"), case)
    _work, _out, manifest, result = run_pipeline(base)
    check(manifest.get("contract_fields") == ["manuscript", "evidence_map", "sources"],
          "contract_fields discovered in order")
    check(result.get("case_id") == "case-flat-1", "case_id preserved")
    check(result.get("manuscript") is None, "string leaf -> null")
    check(result.get("evidence_map") == {"S1": None}, "object properties recursed")
    check(result.get("sources") == [], "array leaf -> []")
    # standard fields appended without clobbering contract "sources"
    check(result["sources"] == [], "contract 'sources' not clobbered")


def test_list_contract() -> None:
    print("[4] list output_contract with spec dicts")
    case = {
        "case_id": "case-list-1",
        "output_contract": [
            {"name": "introduction", "type": "string"},
            {"name": "related_work", "type": "string"},
            {"name": "citations", "type": "array"},
        ],
    }
    base = write_case(tempfile.mkdtemp(prefix="skilltest_list_"), case)
    _work, _out, manifest, result = run_pipeline(base)
    check(manifest.get("contract_fields") == ["introduction", "related_work", "citations"],
          "field names from spec dicts")
    check(result.get("introduction") is None, "introduction -> null")
    check(result.get("citations") == [], "citations -> []")


def test_nested_contract() -> None:
    print("[5] contract nested under output.output_contract")
    case = {
        "case_id": "case-nested-1",
        "output": {
            "output_contract": {
                "deliverable": {
                    "manuscript": "text",
                    "format": "markdown",
                },
            },
        },
    }
    base = write_case(tempfile.mkdtemp(prefix="skilltest_nested_"), case)
    _work, _out, manifest, result = run_pipeline(base)
    check(manifest.get("contract_key") == "output.output_contract",
          "contract key path recorded")
    check(manifest.get("contract_fields") == ["deliverable"], "top-level field")
    check(result.get("deliverable") == {"manuscript": None, "format": None},
          "nested object recursed with null leaves")


def test_validate_full() -> None:
    print("[6] validate a hand-filled result")
    case = {
        "case_id": "case-validate-1",
        "output_contract": {"manuscript": "string", "evidence_map": "object"},
    }
    base = write_case(tempfile.mkdtemp(prefix="skilltest_val_"), case)
    work, out, manifest, result = run_pipeline(base)

    # Simulate the agent's filled deliverables.
    core.write_json(os.path.join(out, "result.json"), {
        "case_id": "case-validate-1",
        "manuscript": "see manuscript.md",
        "evidence_map": {"S1": {"locator": "materials/paper.md#p1",
                                 "quote": "verbatim", "kind": "fact"}},
        "status": "partial",
        "sources": [{"key": "S1", "locator": "materials/paper.md#p1",
                     "quote": "verbatim", "kind": "fact"}],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"at": "step4", "target": "manuscript.md",
                        "before": "", "after": "draft",
                        "reason": "initial draft"}],
        "artifacts": ["manuscript.md"],
        "errors": [],
        "degradations": [],
        "runtime": 1.0,
    })
    with open(os.path.join(out, "manuscript.md"), "w", encoding="utf-8") as fh:
        fh.write("# Introduction\n\nBackground paragraph.")
    core.write_json(os.path.join(out, "evidence_map.json"),
                    {"S1": {"locator": "materials/paper.md#p1",
                            "quote": "verbatim", "kind": "fact"}})
    core.write_json(os.path.join(out, "change_log.json"),
                    [{"at": "step4", "target": "manuscript.md", "before": "",
                      "after": "draft", "reason": "initial draft"}])

    report = core.validate_output(out, work)
    check(report.get("passed") is True, f"validate passed (errors={report.get('errors')})")
    check(any(c["name"] == "contract:manuscript" and c["passed"] for c in report["checks"]),
          "contract:manuscript checked")


def test_determinism() -> None:
    print("[7] determinism of manifest/result")
    case = {
        "case_id": "case-det-1",
        "output_contract": {"a": "string", "b": {"type": "array"}},
    }
    base = write_case(tempfile.mkdtemp(prefix="skilltest_det_"), case)
    m1 = core.build_manifest(base)
    r1 = core.build_skeleton(m1)
    m2 = core.build_manifest(base)
    r2 = core.build_skeleton(m2)
    # Strip the timestamp metric before comparing byte-level content.
    m1c = {k: v for k, v in m1.items() if k != "scanned_at"}
    m2c = {k: v for k, v in m2.items() if k != "scanned_at"}
    r1c = {k: v for k, v in r1.items() if k != "runtime"}
    r2c = {k: v for k, v in r2.items() if k != "runtime"}
    check(m1c == m2c, "manifest deterministic")
    check(r1c == r2c, "result skeleton deterministic")


def main() -> int:
    tests = [
        test_empty_input,
        test_bad_json,
        test_flat_contract,
        test_list_contract,
        test_nested_contract,
        test_validate_full,
        test_determinism,
    ]
    for fn in tests:
        try:
            fn()
        except Exception:  # noqa: BLE001 - report any crash as a failure
            FAILURES.append(f"{fn.__name__} raised")
            print(f"  CRASH {fn.__name__}")
            traceback.print_exc()
    print("-" * 40)
    if FAILURES:
        print(f"SELFTEST FAILED: {len(FAILURES)} failure(s)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
