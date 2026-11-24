#!/usr/bin/env python3
"""selftest — package self-check before submission.

Verifies, with the Python standard library only:

1. SKILL.md has valid name/description/version frontmatter;
2. main.py --help exits 0;
3. core_impl is importable and the default contract is complete;
4. empty input directory -> exit 0 + legal partial result;
5. broken case.json -> exit 0 + legal partial result (no crash);
6. a full plan.json round-trips into result.json with evidence checks and an
   honest status.

Exit code is 0 when all checks pass, 1 otherwise. Run from any directory;
all paths are resolved relative to this file's location.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def run_main(args, timeout=120):
    return subprocess.run(
        [sys.executable, "-B", os.path.join(HERE, "main.py")] + args,
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def main():
    results = []

    def ok(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # 1. frontmatter -------------------------------------------------------
    skill = os.path.join(PKG, "SKILL.md")
    if os.path.isfile(skill):
        text = open(skill, encoding="utf-8").read()
        fm_ok = text.startswith("---")
        parts = text.split("---", 2)
        fm_ok = fm_ok and len(parts) >= 3
        fm = parts[1] if fm_ok else ""
        ok("frontmatter", fm_ok, "SKILL.md YAML frontmatter")
        if fm_ok:
            ok("frontmatter_name", "name:" in fm, "")
            ok("frontmatter_description", "description:" in fm, "")
            ok("frontmatter_version", "version:" in fm, "")
    else:
        ok("frontmatter", False, "SKILL.md missing")

    # 2. CLI ---------------------------------------------------------------
    r = run_main(["--help"])
    ok("main_help_exit0", r.returncode == 0, "rc=%d" % r.returncode)

    # 3. engine import + default contract ----------------------------------
    contract = C.default_contract()
    ok("default_contract_keys",
       all(k in contract for k in C.DEFAULT_CONTRACT_KEYS), "")

    # 4. empty input -------------------------------------------------------
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "in")
        work = os.path.join(d, "work")
        out = os.path.join(d, "out")
        os.makedirs(inp)
        r = run_main(["--input", inp, "--work", work, "--output", out])
        res = read_json(os.path.join(out, "result.json"))
        ok("empty_input_rc0", r.returncode == 0, "rc=%d" % r.returncode)
        ok("empty_input_legal", res is not None and "case_id" in res and "status" in res, "")
        ok("empty_input_status",
           res is not None and res.get("status") in C.STATUSES,
           str(res.get("status") if res else None))

    # 5. broken case.json --------------------------------------------------
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "in")
        work = os.path.join(d, "work")
        out = os.path.join(d, "out")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json ")
        r = run_main(["--input", inp, "--work", work, "--output", out])
        res = read_json(os.path.join(out, "result.json"))
        ok("badjson_rc0", r.returncode == 0, "rc=%d" % r.returncode)
        ok("badjson_legal", res is not None and "status" in res, "")
        ok("badjson_honest",
           res is not None and res.get("status") in ("partial", "insufficient_evidence", "input_error"),
           str(res.get("status") if res else None))

    # 6. full plan IR round-trip -------------------------------------------
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "in")
        work = os.path.join(d, "work")
        out = os.path.join(d, "out")
        os.makedirs(inp)
        os.makedirs(work)
        deck = os.path.join(work, "deck.pptx")
        prev = os.path.join(work, "preview.pdf")
        for p in (deck, prev):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("placeholder binary\n")

        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "case-selftest-1", "output_contract": {
                "report_goal": {}, "audience": {}, "narrative": {},
                "pages": [], "editable_deck": {}, "evidence_map": {},
                "qa_prep": [], "rendering": {}, "extra_key": "preserved",
            }}, fh, ensure_ascii=False)

        plan = {
            "case_id": "case-selftest-1",
            "report_goal": {"objective": "演示", "minutes": 10, "max_slides": 8},
            "audience": {"prior": "同行", "concerns": ["方法新颖性"], "term_depth": "medium"},
            "narrative": {"spine": "问题—方法—结果—结论", "choices": [], "excluded": []},
            "pages": [{
                "page_index": 1,
                "title": "开场",
                "single_claim": "该材料给出可复核的唯一主张。",
                "evidence_strength": "high",
                "figures": [],
                "speaker_notes": "本页只讲这一句。",
                "source_refs": [{"locator": "paper.pdf#p2", "quote": "原文逐字片段"}],
            }],
            "qa_prep": [{"category": "limitation", "question": "局限？",
                         "answer": "见第1页", "citable_pages": [1]}],
            "editable_deck": {"path": deck, "format": "pptx", "template": None},
            "rendering": {"export_files": [prev], "source_files": [deck]},
            "sources": [{"path": "paper.pdf", "type": "pdf", "read": True}],
            "change_log": [{"timestamp": C.now_iso(), "action": "test", "scope": "plan.json"}],
        }
        with open(os.path.join(work, "plan.json"), "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False)

        r = run_main(["--input", inp, "--work", work, "--output", out])
        res = read_json(os.path.join(out, "result.json"))
        ok("roundtrip_rc0", r.returncode == 0, "rc=%d" % r.returncode)
        ok("roundtrip_merged", res is not None and len(res.get("pages", [])) == 1, "")
        ok("roundtrip_contract_preserved",
           res is not None and res.get("extra_key") == "preserved", "")
        ok("roundtrip_status_complete",
           res is not None and res.get("status") == "complete",
           str(res.get("status") if res else None))
        ok("roundtrip_evidence_map",
           res is not None and len(res.get("evidence_map", [])) == 1, "")
        rep = read_json(os.path.join(work, "validation_report.json"))
        ok("roundtrip_validation_report",
           rep is not None and rep.get("issues") == [], "")

    # ---- summary ---------------------------------------------------------
    passed = sum(1 for _, c, _ in results if c)
    total = len(results)
    for name, cond, detail in results:
        mark = "PASS" if cond else "FAIL"
        print("[%s] %s%s" % (mark, name, (" | " + detail) if detail else ""))
    print("selftest: %d/%d checks passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
