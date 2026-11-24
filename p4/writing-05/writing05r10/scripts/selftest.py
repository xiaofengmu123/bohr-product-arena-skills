#!/usr/bin/env python3
"""Self-test for the evidence-claim-writing Skill (standard library only).

Runs a deterministic battery covering core helpers, contract backfill,
scaffold behavior on empty/valid input, idempotence, finalize on a synthetic
claim graph, and result validation. No network, no external files. Exits
non-zero on any failure. Run from the skill root or via absolute path.
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as core  # noqa: E402
import main as app  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + ((" - " + detail) if detail else ""))
    if not cond:
        FAILURES.append(name)


def main():
    # --- core helpers ---
    check("normalize NFC", core.normalize("a\u0301") == "\u00e1")
    check("collapse_ws", core.collapse_ws("a  b\nc") == "abc")
    ok, _ = core.verify_quote("本研究纳入了 123 例患者。", "123 例患者")
    check("verify_quote verbatim", ok)
    ok2, _ = core.verify_quote("本研究纳入了 123 例患者。", "456 例患者")
    check("verify_quote mismatch", not ok2)
    check("split_sentences", len(core.split_sentences("第一句。第二句！")) == 2)
    c = core.backfill_contract({"unknown_key": 42, "sources": None})
    check("backfill keeps unknown key", c.get("unknown_key") == 42 and c.get("deliverable") is None)
    sk = core.result_skeleton("case-1", {"foo": "bar"})
    required = ["case_id", "status", "deliverable", "sources", "uncertainty",
                "pending_human", "change_log", "limitations"]
    check("skeleton required keys", all(k in sk for k in required) and sk.get("foo") == "bar")
    check("next_id", core.next_id("C", [{"id": "C001"}, {"id": "C003"}]) == "C004")
    check("overclaim heuristic", core.check_overclaim("该干预降低了死亡率"))

    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        a = argparse.Namespace(case=os.path.join(inp, "case.json"), out=out, work=work)

        # empty input -> legal input_error result, exit 0
        rc = app.cmd_scaffold(a)
        r, _ = app.read_json(os.path.join(out, "result.json"))
        check("scaffold empty dir exit 0", rc == 0)
        check("scaffold empty dir input_error", isinstance(r, dict) and r.get("status") == "input_error")

        # synthetic case -> in_progress + case_id + contract key preserved
        case = {
            "case_id": "demo-case",
            "output_contract": {"deliverable": None, "sources": [], "change_log": [],
                                "custom": "keep-me"},
            "materials": [{"path": "paper.txt"}],
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
        rc = app.cmd_scaffold(a)
        r, _ = app.read_json(os.path.join(out, "result.json"))
        check("scaffold case_id", isinstance(r, dict) and r.get("case_id") == "demo-case")
        check("scaffold contract key preserved", isinstance(r, dict) and r.get("custom") == "keep-me")
        check("scaffold seeds claim graph", os.path.exists(os.path.join(work, "claim_graph.json")))

        # preserve improved (non-in_progress) result on rerun
        r["status"] = "complete"
        app.write_json(os.path.join(out, "result.json"), r)
        app.cmd_scaffold(a)
        r2, _ = app.read_json(os.path.join(out, "result.json"))
        check("scaffold preserves complete", r2.get("status") == "complete")

        # synthetic claim graph -> finalize renders complete result + markdown
        graph = {
            "schema_version": "1.0.0",
            "case_id": "demo-case",
            "materials": [{"id": "M1", "path": "paper.txt", "kind": "txt"}],
            "claims": [
                {
                    "id": "C001",
                    "statement": "某干预降低了事件发生率",
                    "locator": "S3.P2.s1",
                    "quote": "干预组事件发生率显著低于对照组",
                    "source_ids": ["S001"],
                    "support": "direct",
                    "strength": "rct",
                    "uncertainty": "原文已用显著限定",
                    "status": "ok",
                    "action": "",
                }
            ],
            "sources": [
                {
                    "id": "S001",
                    "type": "original",
                    "citation": "作者等 2024",
                    "locator": "S3.P2",
                    "quote": "干预组事件发生率显著低于对照组",
                    "metadata_status": "verified",
                    "source_doc": "paper.txt",
                }
            ],
            "draft": "示例草稿正文（self-test 生成，非正式案例）。",
            "summary": "self-test",
        }
        with open(os.path.join(work, "claim_graph.json"), "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False)
        # reset result.json to in_progress so finalize may proceed
        app.write_json(os.path.join(out, "result.json"),
                       core.result_skeleton("demo-case", case.get("output_contract")))
        rc = app.cmd_finalize(a)
        r, _ = app.read_json(os.path.join(out, "result.json"))
        check("finalize exit 0", rc == 0)
        check("finalize status complete", isinstance(r, dict) and r.get("status") == "complete")
        check("finalize deliverable claims",
              isinstance(r, dict) and len((r.get("deliverable") or {}).get("claims", [])) == 1)
        check("finalize sources non-empty",
              isinstance(r, dict) and len(r.get("sources") or []) >= 2)
        check("finalize md exists", os.path.exists(os.path.join(out, "evidence_writing.md")))

        # validation
        check("validate pass", app.cmd_validate(a) == 0)

    print("")
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURES -> " + ", ".join(FAILURES))
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
