#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — offline self-checks for the skill package.

Runs entirely in a temp directory; never touches /input or /app/output.
Covers: empty input, bad JSON, contract echo, patch/validate, determinism,
language detection and dedup-key normalization. Exit 0 only when all pass.
"""

import json
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as cl  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


def run_selftest():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # A. empty input directory
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        sk = cl.build_skeleton("unknown_case", None, cl.discover_inputs(empty),
                               "case.json_not_found")
        check("empty_input", sk["status"] == "input_error", sk["status"])

        # B. bad JSON case.json
        bad = os.path.join(tmp, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        found, case, task, err = cl.load_case(bad)
        sk2 = cl.build_skeleton("x", None, found, err)
        check("bad_json", sk2["status"] == "partial"
              and any("bad_json" in str(e) for e in sk2["errors"]),
              json.dumps(sk2["errors"], ensure_ascii=False))

        # C. good case with contract in case.json and task.md
        good = os.path.join(tmp, "good")
        os.makedirs(good)
        case_obj = {
            "case_id": "case-001",
            "output_contract": {"items": [], "summary": None, "target_language": "pt"},
        }
        with open(os.path.join(good, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case_obj, fh, ensure_ascii=False)
        with open(os.path.join(good, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\noutput_contract: {\"extra\": \"value\"}\n")
        found2, case2, task2, err2 = cl.load_case(good)
        contract = cl.find_contract(case2)
        check("contract_detected",
              isinstance(contract, dict) and "items" in contract and "extra" not in contract,
              json.dumps(contract, ensure_ascii=False))
        from_text = cl.find_contract_from_text(task2)
        check("contract_from_text", isinstance(from_text, dict) and "extra" in from_text,
              json.dumps(from_text, ensure_ascii=False))

        # D. contract echo + patch + validate
        merged_contract = cl.deep_merge(dict(contract), from_text)
        sk3 = cl.build_skeleton("case-001", merged_contract, found2, err2)
        check("contract_echoed",
              all(k in sk3 for k in ("items", "summary", "target_language", "extra"))
              and sk3.get("items") is None and sk3.get("extra") is None,
              json.dumps({k: sk3.get(k) for k in ("items", "summary", "target_language", "extra")}))
        merged = cl.deep_merge(sk3, {"status": "ok", "results": [{"id": "r1"}]})
        check("deep_merge", merged["status"] == "ok"
              and merged["results"] == [{"id": "r1"}] and "extra" in merged,
              json.dumps(merged["status"], ensure_ascii=False))
        issues = cl.validate_result(merged, merged_contract)
        check("validate", len(issues) == 0, json.dumps(issues))

        # E. determinism (frozen clock)
        a = cl.build_skeleton("case-001", merged_contract, found2, err2,
                              now="2026-01-01T00:00:00Z")
        b = cl.build_skeleton("case-001", merged_contract, found2, err2,
                              now="2026-01-01T00:00:00Z")
        check("determinism",
              json.dumps(a, sort_keys=True, ensure_ascii=False)
              == json.dumps(b, sort_keys=True, ensure_ascii=False),
              "skeletons identical")

        # F. language detection
        lang_pt = cl.detect_language(
            "Este estudo investiga a relação entre dieta e saúde em mulheres não grávidas.")
        lang_en = cl.detect_language(
            "This study investigates the relationship between diet and health.")
        check("lang_pt", lang_pt["language"] == "pt", json.dumps(lang_pt))
        check("lang_en", lang_en["language"] == "en", json.dumps(lang_en))

        # G. dedup key normalization (DOI case + diacritic stripping)
        k1 = cl.dedup_key(doi="https://doi.org/10.1000/ABC.123", title="Título do Estudo")
        k2 = cl.dedup_key(doi="10.1000/abc.123", title="Titulo do Estudo")
        check("dedup_norm", k1 == k2, "%r vs %r" % (k1, k2))

        # H. version classification
        v1 = cl.classify_version({"body_language": "pt"})
        v2 = cl.classify_version({"body_language": "pt", "abstract_language": "en"})
        v3 = cl.classify_version({"translation_statement": "official translation"})
        v4 = cl.classify_version({"duplicate_of": "doi:10.1000/abc.123"})
        check("version_vocab", v1[0] == "original" and v2[0] == "abstract_only"
              and v3[0] == "full_translation" and v4[0] == "duplicate",
              "%r %r %r %r" % (v1, v2, v3, v4))
    except Exception:  # noqa: BLE001
        check("no_exception", False, traceback.format_exc())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return RESULTS


def main():
    results = run_selftest()
    failed = 0
    for name, ok, detail in results:
        line = ("PASS" if ok else "FAIL") + " " + name
        if detail:
            line += " — " + detail
        print(line)
        if not ok:
            failed += 1
    passed = len(results) - failed
    print("summary: %d/%d passed" % (passed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
