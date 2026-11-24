#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — deterministic self-tests for the cross-lingual literature skill.

Runs against synthetic in-memory input (NOT a real case, NOT a prepared answer).
Verifies the invariants the scoring dimensions depend on:

  * empty input still yields a legal result (input_error), no crash
  * case_id and output_contract are discovered; contract fields are backfilled
    (unknown keys preserved, missing keys filled null / [])
  * evidence records render deterministically (two renders are byte-equal
    except for the wall-clock runtime field)
  * upsert-by-id is idempotent (re-recording the same step does not duplicate)
  * validate() reports no issues on a well-formed result

Exit code 0 when all checks pass, 1 otherwise.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as c  # noqa: E402


def _strip_runtime(obj):
    obj = dict(obj)
    obj.pop("runtime", None)
    return obj


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    return bool(cond)


def run():
    ok = True
    tmp = tempfile.mkdtemp(prefix="xls_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        for path in (inp, out, work):
            os.makedirs(path, exist_ok=True)

        # 1. empty input -> legal input_error skeleton
        found, case, task, err = c.load_inputs(inp)
        ok &= check("empty input yields case_error", err is not None)
        state = {"case_id": "unknown_case", "contract": None, "contract_fields": [],
                 "inputs": found, "case_error": err, "started_at": 0, "version": c.SKILL_VERSION}
        res = c.render(state, [])
        ok &= check("empty render status=input_error", res["status"] == "input_error")
        ok &= check("empty render is a dict with case_id", isinstance(res, dict) and res["case_id"] == "unknown_case")

        # 2. synthetic case with an output_contract (dict form)
        case_obj = {
            "case_id": "demo-1",
            "output_contract": {
                "research_question": "string",
                "terminology_table": "list",
                "results": "list",
                "novel_key": "list",
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case_obj, f, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# task\n研究问题涉及葡萄牙语与英语文献。\n")

        found2, case2, task2, err2 = c.load_inputs(inp)
        ok &= check("case.json discovered", found2["case"] is not None)
        ok &= check("case_id parsed", c.find_case_id(case2, task2) == "demo-1")
        contract = c.find_contract(case2) or c.find_contract_from_text(task2)
        ok &= check("contract found", contract is not None)
        cf = c.normalize_contract(contract)
        ok &= check("contract fields normalized (4)", len(cf) == 4)
        ok &= check("contract list detection", any(f["name"] == "results" and f["is_list"] for f in cf))

        # 3. render with evidence records
        records = [
            {"id": "rq1", "kind": "research_question",
             "body": {"question": "Identify the Portuguese original and its English version",
                      "src_lang": "pt", "tgt_lang": "en", "scope": "known-item"}},
            {"id": "t1", "kind": "term",
             "body": {"src": "piroptose", "src_lang": "pt", "en": "pyroptosis",
                      "type": "synonym", "basis": "abstract_pt", "verification": "back-translation"},
             "locator": "materials/a.pdf#p2", "quote": "piroptose"},
            {"id": "r1", "kind": "result",
             "body": {"title": "Cell death by pyroptosis", "authors": ["A"], "year": 2020,
                      "journal": "J", "doi": "10.0/example", "language": "en",
                      "version": "original", "source": "https://example.org/1"},
             "locator": "https://example.org/1", "quote": "pyroptosis is a form of cell death"},
            {"id": "r2", "kind": "result",
             "body": {"title": "Morte celular por piroptose", "authors": ["A"], "year": 2020,
                      "journal": "J", "doi": "10.0/example", "language": "pt",
                      "version": "translation", "source": "https://example.org/2"},
             "locator": "https://example.org/2", "quote": "piroptose é uma forma de morte celular"},
        ]
        for rec in records:
            c.ensure_id(rec)
        state2 = {"case_id": "demo-1", "contract": contract, "contract_fields": cf,
                  "inputs": found2, "case_error": None, "started_at": 0, "version": c.SKILL_VERSION}
        res = c.render(state2, records)
        ok &= check("status=ok with results", res["status"] == "ok")
        ok &= check("contract field novel_key present", "novel_key" in res)
        ok &= check("results count == 2", len(res["results"]) == 2)
        ok &= check("terminology count == 1", len(res["terminology_table"]) == 1)
        ok &= check("evidence_map has r1", "r1" in res.get("evidence_map", {}))
        ok &= check("sources collected (2)", len(res["sources"]) == 2)

        res_b = c.render(state2, records)
        ok &= check("deterministic render (except runtime)",
                    json.dumps(_strip_runtime(res), ensure_ascii=False, sort_keys=True)
                    == json.dumps(_strip_runtime(res_b), ensure_ascii=False, sort_keys=True))

        # 4. upsert by id is idempotent
        store = c.EvidenceStore(work)
        store._write([])
        store.append(records)
        n1 = len(store.load())
        store.append(records)
        n2 = len(store.load())
        ok &= check("upsert idempotent (no duplicates)", n1 == n2 == len(records))

        # 5. validate a well-formed result
        issues = c.validate(res)
        ok &= check("validate no issues", issues == [])

        # 6. atomic write + re-read
        c.atomic_write_json(os.path.join(out, "result.json"), res)
        reread, _ = c.safe_json_load(os.path.join(out, "result.json"))
        ok &= check("result.json written and re-readable", reread == res)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nselftest " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
