#!/usr/bin/env python3
"""Self-test for the systematic-review Skill.

Exercises the CLI end-to-end against synthetic inputs: empty directory, a
minimal valid case (with an output_contract), and a broken case.json. It does
not create or ship any formal test-case answers.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*argv):
    return subprocess.run([sys.executable, "-B", MAIN] + list(argv),
                          capture_output=True, text=True)


def check(cond, label, details=""):
    print(("PASS " if cond else "FAIL ") + label + ((" :: " + details) if details else ""))
    return bool(cond)


def make_case(tmp, case):
    inp, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"),
                      os.path.join(tmp, "work"))
    for d in (inp, out, work):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\n围绕材料做系统综述。\n")
    with open(os.path.join(inp, "paper1.md"), "w", encoding="utf-8") as fh:
        fh.write("# Study A\n\n这是第一条证据。\n")
    return inp, out, work


def main():
    tmp = tempfile.mkdtemp(prefix="srs-selftest-")
    ok = True
    try:
        # 1) empty input directory
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        out_e, work_e = os.path.join(tmp, "out_empty"), os.path.join(tmp, "work_empty")
        r = run("skeleton", "--input", empty, "--output", out_e, "--work", work_e)
        res = json.load(open(os.path.join(out_e, "result.json"), encoding="utf-8"))
        ok &= check(r.returncode == 0, "empty dir exits 0", "rc=%d" % r.returncode)
        ok &= check(res.get("status") == "insufficient_evidence",
                    "empty dir -> insufficient_evidence", res.get("status"))
        ok &= check(res.get("case_id") == "unknown", "empty dir has case_id", res.get("case_id"))

        # 2) minimal valid case with output_contract + one material
        case = {"case_id": "case-demo",
                "output_contract": {"review_question": "", "synthesis": {"conclusions": []},
                                    "custom_field": []}}
        inp, out, work = make_case(tmp, case)
        r = run("skeleton", "--input", inp, "--output", out, "--work", work)
        ok &= check(r.returncode == 0, "skeleton exits 0", "rc=%d" % r.returncode)

        payload = {
            "review_question": "某种干预是否有效？",
            "subquestions": [{"q": "效果如何", "depends_on": []}],
            "search_strategy": {"query_blocks": [
                {"database": "PubMed", "query": "(a) AND (b)", "intent": "core", "hits": 3}]},
            "inclusion_criteria": [{"criterion_id": "I1", "rule": "纳入", "priority": 1}],
            "exclusion_criteria": [{"criterion_id": "E1", "rule": "排除", "priority": 1}],
        }
        with open(os.path.join(work, "payload.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        r = run("merge", "--payload", os.path.join(work, "payload.json"),
                "--input", inp, "--output", out, "--work", work)
        ok &= check(r.returncode == 0, "merge exits 0", "rc=%d" % r.returncode)

        with open(os.path.join(work, "screening.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"record_id": "S1", "source": "Study A", "stage": "title_abstract",
                                 "decision": "include", "judge": "agent"}) + "\n")
            fh.write(json.dumps({"record_id": "S2", "source": "Study B", "stage": "fulltext",
                                 "decision": "exclude", "reason": "无对照组", "criterion_id": "E1",
                                 "judge": "agent"}) + "\n")
            fh.write(json.dumps({"record_id": "S3", "source": "Study C", "stage": "fulltext",
                                 "decision": "include", "judge": "agent"}) + "\n")
        with open(os.path.join(work, "evidence_cards.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"evidence_id": "EV1", "claim": "有证据", "locator": "paper1.md#L3",
                                 "quote": "这是第一条证据。", "source": "Study A",
                                 "strength": "direct"}) + "\n")
        with open(os.path.join(work, "synthesis.json"), "w", encoding="utf-8") as fh:
            json.dump({"conclusions": [{"text": "证据支持结论", "evidence_ids": ["EV1"]}],
                       "conflicts": [{"statement": "方向相反", "source_a": "A", "source_b": "B",
                                      "resolution": "undetermined"}],
                       "gaps": [{"gap": "缺长期数据"}]}, fh, ensure_ascii=False)

        r = run("finalize", "--input", inp, "--output", out, "--work", work)
        ok &= check(r.returncode == 0, "finalize exits 0", "rc=%d" % r.returncode)
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ok &= check(res.get("case_id") == "case-demo", "case_id preserved", res.get("case_id"))
        ok &= check("custom_field" in res, "output_contract extra key preserved")
        ok &= check(isinstance(res.get("synthesis"), dict) and res["synthesis"].get("conclusions"),
                    "synthesis assembled")
        ok &= check(res.get("prisma_flow", {}).get("included") == 1, "prisma recomputed",
                    str(res.get("prisma_flow")))
        ok &= check(os.path.exists(os.path.join(out, "review.md")), "review.md written")
        ok &= check(os.path.exists(os.path.join(out, "evidence_map.json")), "evidence_map.json written")

        r = run("selfcheck", "--input", inp, "--output", out, "--work", work)
        ok &= check(r.returncode == 0, "selfcheck exits 0", "rc=%d" % r.returncode)
        summary = json.loads(r.stdout)
        ok &= check(summary["failed"] == 0, "selfcheck all pass", "failed=%d" % summary["failed"])

        # 3) broken case.json
        bad_in, bad_out, bad_work = (os.path.join(tmp, "bad_input"), os.path.join(tmp, "bad_output"),
                                     os.path.join(tmp, "bad_work"))
        os.makedirs(bad_in, exist_ok=True)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        r = run("skeleton", "--input", bad_in, "--output", bad_out, "--work", bad_work)
        res = json.load(open(os.path.join(bad_out, "result.json"), encoding="utf-8"))
        ok &= check(r.returncode == 0, "broken JSON exits 0", "rc=%d" % r.returncode)
        ok &= check(res.get("status") in ("input_error", "partial"),
                    "broken JSON -> input_error/partial", res.get("status"))

        print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
