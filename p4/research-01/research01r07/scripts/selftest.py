#!/usr/bin/env python3
"""Self-test for the deep-research skill (stdlib only).

Exercises the robustness scenarios named in SKILL.md and prints PASS/FAIL.
Exit code 0 when all checks pass, 1 otherwise. Run from the skill root:

    python3 -B scripts/selftest.py

The test writes only into a temporary directory and never touches /input or
/app/output.
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resultlib as R

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("%s %s%s" % ("PASS" if condition else "FAIL", name,
                       (" -- " + detail) if detail else ""))


def run_cli(args):
    """Run main.py with sys.executable; return (code, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, "-B", os.path.join(HERE, "main.py")] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def make_case(tmp, case_name, files, encoding="utf-8"):
    """Create a case directory with the given {relpath: content} files."""
    case_dir = os.path.join(tmp, case_name)
    os.makedirs(case_dir, exist_ok=True)
    for rel, content in files.items():
        path = os.path.join(case_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(rel) else None
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": encoding}
        with open(path, mode, **kwargs) as handle:
            handle.write(content)
    return case_dir


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    tmp = tempfile.mkdtemp(prefix="deepresearch-selftest-")
    try:
        # 1. Empty input directory
        empty_dir = make_case(tmp, "empty", {})
        code, out, _ = run_cli(["skeleton", "--input", empty_dir,
                                "--output", os.path.join(tmp, "o_empty"),
                                "--work", os.path.join(tmp, "w_empty")])
        r_empty = read_json(os.path.join(tmp, "o_empty", "result.json"))
        check("empty-input-skeleton", code == 0 and
              r_empty["status"] == R.STATUS_INSUFFICIENT,
              "status=%s" % r_empty["status"])

        # 2. Bad JSON in case.json
        bad_dir = make_case(tmp, "badjson",
                            {"case.json": "{ not valid json !!"})
        code, out, _ = run_cli(["skeleton", "--input", bad_dir,
                                "--output", os.path.join(tmp, "o_bad"),
                                "--work", os.path.join(tmp, "w_bad")])
        r_bad = read_json(os.path.join(tmp, "o_bad", "result.json"))
        check("bad-json-case", code == 0 and r_bad["status"] == R.STATUS_INPUT_ERROR,
              "status=%s" % r_bad["status"])

        # 3. Bad encoding text file (non-UTF-8, non-ASCII)
        bad_enc_dir = make_case(tmp, "badenc",
                                {"notes.txt": "中文内容".encode("gb18030")})
        text, errs, degs = R.read_text(os.path.join(bad_enc_dir, "notes.txt"))
        check("bad-encoding-read", bool(text) and not errs,
              "decoded %d chars, degradations=%d" % (len(text), len(degs)))

        # 4. Missing args: discover falls back to defaults without crashing
        code, out, _ = run_cli(["discover"])
        check("missing-args-defaults", code == 0 and "input_dir" in out,
              "exit=%d" % code)

        # 5. Oversized text file is truncated and recorded, not skipped
        big_dir = make_case(tmp, "big", {})
        big_path = os.path.join(big_dir, "huge.txt")
        with open(big_path, "wb") as handle:
            handle.write(b"x" * (R.DEFAULT_TEXT_MAX + 4096))
        text, errs, degs = R.read_text(big_path)
        check("oversized-truncate", len(text) == R.DEFAULT_TEXT_MAX and
              any(d.get("type") == "truncated" for d in degs),
              "len=%d" % len(text))

        # 6. Unknown key verbatim round-trip through the contract
        contract = {"custom_field": {"a": 1}, "research_product": None}
        skeleton = R.new_skeleton("C1", contract)
        check("contract-roundtrip", skeleton.get("custom_field") == {"a": 1},
              "custom_field preserved")

        # 7. Full happy-path: skeleton -> merge -> finalize -> selfcheck
        happy_dir = make_case(tmp, "happy", {
            "case.json": json.dumps({
                "case_id": "CASE-1",
                "output_contract": {"research_product": None,
                                    "evidence_matrix": [],
                                    "uncertainty": None},
            }),
            "task.md": "# task\n研究问题：比较两种方法。\n",
        })
        out_dir = os.path.join(tmp, "o_happy")
        work_dir = os.path.join(tmp, "w_happy")
        os.makedirs(work_dir, exist_ok=True)
        with open(os.path.join(work_dir, "decomposition.md"), "w", encoding="utf-8") as fh:
            fh.write("SQ1 检索方法A\nSQ2 检索方法B\n依赖：SQ2 依赖 SQ1\n")
        with open(os.path.join(work_dir, "search_plan.md"), "w", encoding="utf-8") as fh:
            fh.write("顺序：SQ1 后 SQ2；来源范围：材料内 PDF；停止条件：证据饱和\n")
        payload = {
            "status": "partial",
            "research_product": {
                "type": "synthesis", "question": "比较两种方法",
                "summary": "方法 A 与 B 各有证据支持。",
                "conclusions": [
                    {"id": "C1", "text": "方法 A 有直接证据。",
                     "evidence_ids": ["E1"], "strength": "established"},
                ],
            },
            "evidence_matrix": [
                {"id": "E1", "subquestion": "SQ1", "source": "materials/a.pdf",
                 "locator": "p3", "quote": "方法 A 的有效性得到证实。",
                 "claim": "方法 A 有效", "judgment": "supports",
                 "reason": "原文直接支持", "conclusion_ids": ["C1"]},
            ],
            "uncertainty": {"conflicts": [{"a": "E1", "b": "E2",
                                           "resolution": "并列"}],
                            "biases": [], "gaps": ["缺少方法 B 的对照"],
                            "undetermined": []},
            "pending_human": ["确认方法 B 是否在材料中存在"],
        }
        payload_path = os.path.join(tmp, "payload.json")
        with open(payload_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

        code, _, _ = run_cli(["skeleton", "--input", happy_dir,
                              "--output", out_dir, "--work", work_dir])
        check("happy-skeleton", code == 0)
        code, _, _ = run_cli(["merge", "--payload", payload_path,
                              "--output", out_dir, "--work", work_dir])
        check("happy-merge", code == 0)
        code, _, _ = run_cli(["finalize", "--input", happy_dir,
                              "--output", out_dir, "--work", work_dir])
        r_happy = read_json(os.path.join(out_dir, "result.json"))
        check("happy-finalize", code == 0 and
              os.path.exists(os.path.join(out_dir, "report.md")) and
              os.path.exists(os.path.join(out_dir, "evidence_map.json")),
              "status=%s" % r_happy["status"])

        code, out, _ = run_cli(["selfcheck", "--output", out_dir,
                                "--work", work_dir])
        sc = json.loads(out)
        check("happy-selfcheck", code == 0 and sc["valid"],
              "valid=%s six=%d/6" % (sc["valid"], sum(sc["six_point"].values())))

        # 8. Deterministic layout on repeated finalize (same evidence order)
        ev1 = [e["id"] for e in r_happy["evidence_matrix"]]
        run_cli(["finalize", "--input", happy_dir, "--output", out_dir,
                 "--work", work_dir])
        r_happy2 = read_json(os.path.join(out_dir, "result.json"))
        ev2 = [e["id"] for e in r_happy2["evidence_matrix"]]
        check("deterministic-layout", ev1 == ev2 and
              r_happy["case_id"] == r_happy2["case_id"],
              "evidence order stable")

        # 9. Idempotent merge: re-merge same payload does not duplicate change_log
        logs_before = len(r_happy["change_log"])
        run_cli(["merge", "--payload", payload_path, "--output", out_dir,
                 "--work", work_dir])
        r_merged2 = read_json(os.path.join(out_dir, "result.json"))
        merge_logs = [x for x in r_merged2["change_log"]
                      if x.get("action") == "merge"]
        check("idempotent-merge", len(merge_logs) <= 1,
              "merge change_log entries=%d" % len(merge_logs))

        # 10. Write failure degrades gracefully (parent path is a regular file)
        blocker = os.path.join(tmp, "blocker.txt")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        errs = R.write_json(os.path.join(blocker, "sub", "result.json"), {})
        check("write-failure-degrades", bool(errs), "errors=%d" % len(errs))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
