#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Methods & Experiments chapter-writing Skill — runtime pipeline.

Subcommands
-----------
skeleton   Write a legal *partial* result.json + state.json immediately
           (the "first 3 minutes" fallback). Never blocks on analysis.
inventory  Discover material files under /input and extract *candidate*
           facts with locator + verbatim quote. Never asserts facts itself.
validate   Check the result.json output contract, standard fields, evidence
           map, preserved facts, six-element coverage and artifact paths.
selftest   Run the whole pipeline against synthetic inputs in a temp dir
           (empty input, bad JSON, normal input). Exit 0 on success.
run        skeleton -> inventory; prints the remaining model-side steps.

All scripts are standard-library only and deterministic for a fixed input.
Defaults: --input /input --output /app/output --work /app/work.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import (  # noqa: E402
    MAX_FACTS, MAX_FILE_BYTES, OFFICE_EXTENSIONS, PDF_EXTENSIONS,
    STANDARD_FIELDS, SIX_ELEMENTS, STATUS_INPUT_ERROR, STATUS_OK,
    STATUS_PARTIAL, VALID_STATUSES,
    discover_case_id, ensure_dir, extract_facts_from_text,
    extract_office_text, extract_pdf_text, extract_output_contract_keys,
    is_text_file, load_json_any, norm_rel, read_text_any, write_json,
)


# --------------------------------------------------------------------------- #
# skeleton
# --------------------------------------------------------------------------- #
def run_skeleton(args):
    input_dir = args.input
    output_dir = args.output
    work_dir = args.work
    ensure_dir(output_dir)
    ensure_dir(work_dir)

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    state_path = os.path.join(work_dir, "state.json")
    result_path = os.path.join(output_dir, "result.json")

    case = None
    errors = []
    degradations = []
    if os.path.exists(case_path):
        case = load_json_any(case_path)
        if case is None:
            errors.append("case.json exists but could not be parsed as JSON")
    else:
        degradations.append("case.json not found under %s" % input_dir)

    task_text = None
    if os.path.exists(task_path):
        task_text = read_text_any(task_path)
    else:
        degradations.append("task.md not found under %s" % input_dir)

    case_id = discover_case_id(case) if case is not None else "unknown"
    contract_keys = extract_output_contract_keys(case) if case is not None else []

    state = {}
    if os.path.exists(state_path):
        state = load_json_any(state_path) or {}
    started_at = state.get("started_at") or int(time.time())

    old_result = {}
    if os.path.exists(result_path):
        old_result = load_json_any(result_path) or {}
    change_log = list(old_result.get("change_log") or [])

    result = {"case_id": case_id}
    for k in contract_keys:
        result[k] = None
    for k in STANDARD_FIELDS:
        result[k] = None

    result["status"] = STATUS_PARTIAL
    result["artifacts"] = []
    result["sources"] = []
    result["uncertainty"] = {
        "summary": "尚未开始核对；skeleton 阶段占位。",
        "unverified_claims": [],
        "conflicting_claims": [],
        "low_confidence_claims": [],
        "version_gaps": [],
        "seed_gaps": [],
    }
    result["limitations"] = []
    result["pending_human"] = []
    result["change_log"] = change_log
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["runtime"] = {
        "started_at": started_at,
        "elapsed_seconds": int(time.time()) - started_at,
        "stage": "skeleton",
        "model_request_budget": 64,
        "time_budget_seconds": 900,
    }

    write_json(result_path, result)

    state = {
        "started_at": started_at,
        "stage": "skeleton",
        "case_id": case_id,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "output_contract_keys": contract_keys,
        "output_contract_present": bool(contract_keys),
        "task_found": task_text is not None,
        "case_found": case is not None,
        "budget": {"model_requests": 64, "seconds": 900, "tokens_per_response": 16384},
    }
    write_json(state_path, state)
    print("skeleton: wrote %s (status=%s, case_id=%s, contract_keys=%d)"
          % (result_path, result["status"], case_id, len(contract_keys)))
    return 0


# --------------------------------------------------------------------------- #
# inventory
# --------------------------------------------------------------------------- #
def run_inventory(args):
    input_dir = args.input
    output_dir = args.output
    work_dir = args.work
    ensure_dir(work_dir)
    ensure_dir(output_dir)

    files = []
    skipped = []
    extracted_dir = os.path.join(work_dir, "extracted")
    ensure_dir(extracted_dir)

    for root, _dirs, names in os.walk(input_dir):
        if os.path.realpath(root).startswith(os.path.realpath(work_dir)):
            continue
        for name in sorted(names):
            path = os.path.join(root, name)
            rel = norm_rel(path, input_dir)
            try:
                size = os.path.getsize(path)
            except OSError:
                skipped.append({"path": rel, "reason": "unreadable"})
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": rel, "reason": "size > 2MB"})
                continue

            ext = os.path.splitext(name)[1].lower()
            if ext in OFFICE_EXTENSIONS:
                text = extract_office_text(path)
                if text is None:
                    skipped.append({"path": rel, "reason": "office text extraction failed"})
                    continue
                files.append({
                    "path": rel, "type": "office", "size": size,
                    "extracted_text": text, "text_ok": True,
                })
            elif ext in PDF_EXTENSIONS:
                text, ok, note = extract_pdf_text(path, work_dir)
                if not ok:
                    skipped.append({"path": rel, "reason": note})
                    files.append({
                        "path": rel, "type": "pdf", "size": size,
                        "extracted_text": None, "text_ok": False, "note": note,
                    })
                    continue
                files.append({
                    "path": rel, "type": "pdf", "size": size,
                    "extracted_text": text, "text_ok": True,
                })
            elif is_text_file(path):
                try:
                    text = read_text_any(path)
                except OSError:
                    skipped.append({"path": rel, "reason": "unreadable"})
                    continue
                files.append({
                    "path": rel, "type": "text", "size": size,
                    "extracted_text": text, "text_ok": True,
                })
            else:
                skipped.append({"path": rel, "reason": "unsupported type %s" % (ext or "(none)")})

    facts = []
    for f in files:
        if not f.get("text_ok") or not f.get("extracted_text"):
            continue
        prefix = "materials/" + f["path"]
        mode = "page" if f["type"] == "pdf" else "line"
        facts.extend(extract_facts_from_text(f["extracted_text"], prefix, mode))
        if len(facts) >= MAX_FACTS:
            facts = facts[:MAX_FACTS]
            break

    inventory = {
        "files": [{k: v for k, v in f.items() if k != "extracted_text"} for f in files],
        "skipped": skipped,
        "summary": {
            "total_files": len(files) + len(skipped),
            "usable_files": len(files),
            "skipped": len(skipped),
            "candidate_facts": len(facts),
        },
    }
    write_json(os.path.join(work_dir, "materials_inventory.json"), inventory)
    write_json(os.path.join(work_dir, "extracted_facts.json"), {"facts": facts})

    gap_path = os.path.join(output_dir, "gap_register.json")
    gap = load_json_any(gap_path) if os.path.exists(gap_path) else None
    if not isinstance(gap, dict) or "six_elements" not in gap:
        gap = {"six_elements": {k: dict(v) for k, v in SIX_ELEMENTS.items()}}
    gap["_note"] = (
        "Script-seeded. The agent must fill status ('found'|'partial'|'not_found'), "
        "evidence ids and gaps; never delete the seed."
    )
    write_json(gap_path, gap)

    result_path = os.path.join(output_dir, "result.json")
    if os.path.exists(result_path):
        result = load_json_any(result_path) or {}
        if isinstance(result, dict):
            changed = _append_change_log(result, {
                "stage": "inventory", "source": "script",
                "action": "scanned /input and extracted candidate facts",
                "result": "usable_files=%d skipped=%d candidate_facts=%d"
                          % (len(files), len(skipped), len(facts)),
            })
            if changed:
                rt = result.get("runtime")
                if isinstance(rt, dict):
                    rt["stage"] = "inventory"
                    started = rt.get("started_at") or int(time.time())
                    rt["elapsed_seconds"] = int(time.time()) - started
                write_json(result_path, result)

    print("inventory: usable_files=%d skipped=%d candidate_facts=%d"
          % (len(files), len(skipped), len(facts)))
    return 0


# --------------------------------------------------------------------------- #
# change_log helper
# --------------------------------------------------------------------------- #
def _append_change_log(result, entry):
    log = result.get("change_log") or []
    if entry.get("source") == "script":
        if any(e.get("stage") == entry.get("stage") for e in log):
            return False
    log.append(entry)
    result["change_log"] = log
    return True


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #
def run_validate(args):
    input_dir = args.input
    output_dir = args.output

    result_path = os.path.join(output_dir, "result.json")
    report = {"passed": True, "failures": [], "warnings": []}

    if not os.path.exists(result_path):
        report["passed"] = False
        report["failures"].append("result.json missing")
        _finalize_validate(report, output_dir)
        return 1

    result = load_json_any(result_path)
    if not isinstance(result, dict):
        report["passed"] = False
        report["failures"].append("result.json is not a JSON object")
        _finalize_validate(report, output_dir)
        return 1

    case_path = os.path.join(input_dir, "case.json")
    case = load_json_any(case_path) if os.path.exists(case_path) else None
    contract_keys = extract_output_contract_keys(case) if case is not None else []

    if not isinstance(result.get("case_id"), str) or not result["case_id"]:
        report["failures"].append("case_id missing or empty")
        report["passed"] = False

    for k in contract_keys:
        if k not in result:
            report["failures"].append("missing output_contract field: %s" % k)
            report["passed"] = False

    for k in STANDARD_FIELDS:
        if k not in result:
            report["failures"].append("missing standard field: %s" % k)
            report["passed"] = False

    if result.get("status") not in VALID_STATUSES:
        report["failures"].append("invalid status: %r" % result.get("status"))
        report["passed"] = False

    status = result.get("status")

    for rel in (result.get("artifacts") or []):
        if not isinstance(rel, str) or not rel:
            report["failures"].append("artifact path empty")
            report["passed"] = False
            continue
        if not os.path.exists(os.path.join(output_dir, rel)):
            if status == STATUS_OK:
                report["failures"].append("artifact missing: %s" % rel)
                report["passed"] = False
            else:
                report["warnings"].append("artifact missing (allowed for %s): %s" % (status, rel))

    ev_path = os.path.join(output_dir, "evidence_map.json")
    if os.path.exists(ev_path):
        ev = load_json_any(ev_path)
        if isinstance(ev, dict):
            bad = [sid for sid, rec in ev.items()
                   if not isinstance(rec, dict) or not rec.get("locator") or not rec.get("quote")]
            if bad:
                report["failures"].append("evidence_map entries with empty locator/quote: %s"
                                         % ", ".join(bad[:10]))
                report["passed"] = False
        else:
            report["failures"].append("evidence_map.json is not an object")
            report["passed"] = False
    elif status == STATUS_OK:
        report["failures"].append("evidence_map.json missing for status=ok")
        report["passed"] = False
    else:
        report["warnings"].append("evidence_map.json missing")

    pf_path = os.path.join(output_dir, "preserved_facts.json")
    if os.path.exists(pf_path):
        pf = load_json_any(pf_path)
        if isinstance(pf, list):
            bad = [i for i, rec in enumerate(pf)
                   if not isinstance(rec, dict) or not rec.get("quote")]
            if bad:
                report["failures"].append("preserved_facts entries without quote at index %s"
                                         % bad[:10])
                report["passed"] = False
        else:
            report["failures"].append("preserved_facts.json is not a list")
            report["passed"] = False
    elif status == STATUS_OK:
        report["failures"].append("preserved_facts.json missing for status=ok")
        report["passed"] = False
    else:
        report["warnings"].append("preserved_facts.json missing")

    ms_path = os.path.join(output_dir, "manuscript.md")
    if not os.path.exists(ms_path):
        if status == STATUS_OK:
            report["failures"].append("manuscript.md missing for status=ok")
            report["passed"] = False
        else:
            report["warnings"].append("manuscript.md missing")

    gap_path = os.path.join(output_dir, "gap_register.json")
    if os.path.exists(gap_path):
        gap = load_json_any(gap_path) or {}
        six = gap.get("six_elements") if isinstance(gap, dict) else None
        if isinstance(six, dict):
            missing = [k for k, v in six.items()
                       if not isinstance(v, dict) or v.get("status") == "not_found"]
            if missing and status == STATUS_OK:
                report["failures"].append("six-element coverage incomplete for status=ok: %s"
                                         % ", ".join(missing))
                report["passed"] = False
            elif missing:
                report["warnings"].append("six-element coverage incomplete: %s" % ", ".join(missing))
        else:
            report["warnings"].append("gap_register.json has no six_elements map")
    elif status == STATUS_OK:
        report["failures"].append("gap_register.json missing for status=ok")
        report["passed"] = False

    _finalize_validate(report, output_dir)

    if isinstance(result, dict):
        entry = {
            "stage": "validate", "source": "script",
            "action": "ran validation",
            "result": "PASS" if report["passed"]
                      else ("FAIL: %s" % "; ".join(report["failures"][:5])),
        }
        if _append_change_log(result, entry):
            write_json(result_path, result)

    print("validate: %s (%d failures, %d warnings)"
          % ("PASS" if report["passed"] else "FAIL",
             len(report["failures"]), len(report["warnings"])))
    return 0 if report["passed"] else 1


def _finalize_validate(report, output_dir):
    report["checked_at"] = int(time.time())
    write_json(os.path.join(output_dir, "validation_report.json"), report)


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _mk_case(base, case_id, output_contract, extra_materials=True, bad_json=False):
    input_dir = os.path.join(base, "input")
    output_dir = os.path.join(base, "output")
    work_dir = os.path.join(base, "work")
    ensure_dir(input_dir)
    ensure_dir(os.path.join(input_dir, "materials"))
    case = {"case_id": case_id, "id": case_id, "output_contract": output_contract}
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        if bad_json:
            fh.write('{"case_id": "broken", "output_contract": [')
        else:
            json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 方法与实验章节写作\n\n受众：审稿人。体裁：论文 Methods/Experiments。\n")
    if extra_materials:
        with open(os.path.join(input_dir, "materials", "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("batch_size: 32\nlearning_rate: 1e-4\nseed: 42\nmodel: bert-base-uncased\n")
        with open(os.path.join(input_dir, "materials", "run.log"), "w", encoding="utf-8") as fh:
            fh.write("epoch 1 loss 0.31\nepoch 2 loss 0.18\n$ python train.py --epochs 10\n")
    return input_dir, output_dir, work_dir


class _A:
    def __init__(self, input_, output_, work_):
        self.input = input_
        self.output = output_
        self.work = work_


def run_selftest():
    base = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        in_a, out_a, work_a = _mk_case(base + "_A", "case-A",
                                       ["target_artifact", "evidence_map", "preserved_facts"])
        assert run_skeleton(_A(in_a, out_a, work_a)) == 0, "skeleton A failed"
        assert os.path.exists(os.path.join(out_a, "result.json")), "result.json A missing"
        res = load_json_any(os.path.join(out_a, "result.json"))
        for k in ("case_id", "status", "artifacts", "sources", "uncertainty",
                  "pending_human", "change_log", "runtime"):
            assert k in res, "missing field %s" % k
        for k in ("target_artifact", "evidence_map", "preserved_facts"):
            assert k in res, "missing contract field %s" % k
        assert res["case_id"] == "case-A", "wrong case_id"

        assert run_inventory(_A(in_a, out_a, work_a)) == 0, "inventory A failed"
        inv = load_json_any(os.path.join(work_a, "materials_inventory.json"))
        assert inv["summary"]["candidate_facts"] > 0, "no facts extracted"
        facts = load_json_any(os.path.join(work_a, "extracted_facts.json"))["facts"]
        assert all(f.get("locator") and f.get("quote") for f in facts), "fact missing locator/quote"

        res["status"] = "ok"  # force ok to check validate catches missing manuscript
        write_json(os.path.join(out_a, "result.json"), res)
        assert run_validate(_A(in_a, out_a, work_a)) == 1, "validate should fail for ok w/o artifacts"

        in_b, out_b, work_b = _mk_case(base + "_B", "case-B", [], extra_materials=False)
        assert run_skeleton(_A(in_b, out_b, work_b)) == 0, "skeleton B failed"
        assert load_json_any(os.path.join(out_b, "result.json"))["status"] == "partial"
        assert run_inventory(_A(in_b, out_b, work_b)) == 0, "inventory B (empty) failed"

        in_c, out_c, work_c = _mk_case(base + "_C", "case-C", [], bad_json=True)
        assert run_skeleton(_A(in_c, out_c, work_c)) == 0, "skeleton C failed"
        res_c = load_json_any(os.path.join(out_c, "result.json"))
        assert any("could not be parsed" in e for e in res_c["errors"]), \
            "bad JSON not recorded in errors"

        print("selftest: PASS (3 scenarios)")
        return 0
    except AssertionError as exc:
        print("selftest: FAIL -> %s" % exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        print("selftest: ERROR -> %r" % exc)
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Methods & Experiments chapter-writing pipeline (standard library only).",
    )
    p.add_argument("command", nargs="?", default="run",
                   choices=["skeleton", "inventory", "validate", "selftest", "run"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "skeleton":
        return run_skeleton(args)
    if args.command == "inventory":
        return run_inventory(args)
    if args.command == "validate":
        return run_validate(args)
    if args.command == "selftest":
        return run_selftest()
    rc = run_skeleton(args)
    if rc != 0:
        return rc
    rc = run_inventory(args)
    if rc != 0:
        return rc
    print(
        "\nNext (model-side) steps:\n"
        "  1) read /input/case.json, /input/task.md, /app/work/extracted_facts.json\n"
        "  2) verify every candidate fact against the original material\n"
        "  3) write /app/output/manuscript.md with [S#] markers, evidence_map.json,\n"
        "     preserved_facts.json; fill gap_register.json; set uncertainty/limitations/\n"
        "     pending_human; append change_log; set status\n"
        "  4) run: python3 scripts/main.py validate\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
