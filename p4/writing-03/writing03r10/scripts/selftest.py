#!/usr/bin/env python3
"""Self-test: build a synthetic case in a temp dir and run scaffold -> verify -> inspect.

Exit code 0 means the whole deterministic pipeline passes. The demo uses synthetic
material and is explicitly NOT a formal case answer.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args):
    return subprocess.run([sys.executable, MAIN] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    tmp = tempfile.mkdtemp(prefix="fta_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        for d in (inp, out, work):
            os.makedirs(d)

        case = {
            "case_id": "demo-001",
            "task": "把稿件套用到学位论文模板",
            "output_contract": {
                "deliverable": {"content": None, "artifact_path": None, "preview_path": None},
                "fact_source_map": [],
                "version_boundary": {"change_diff": [], "human_confirmation": [], "material_gaps": [], "anomalies": []},
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
        with open(os.path.join(inp, "template.txt"), "w", encoding="utf-8") as f:
            f.write("模板要求：正文小四号宋体，1.5倍行距，上下页边距2.54厘米，单栏。"
                    "图题置于图下方，表题置于表上方。参考文献采用GB/T 7714。")

        # scaffold
        r = run(["scaffold", "--case", os.path.join(inp, "case.json"),
                 "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        result_path = os.path.join(out, "result.json")
        res = json.load(open(result_path, encoding="utf-8"))
        assert res["case_id"] == "demo-001"
        assert res["status"] == "partial"
        for k in ("deliverable", "fact_source_map", "version_boundary"):
            assert k in res, k
        for k in ("sources", "uncertainty", "pending_human", "change_log"):
            assert k in res, k

        # idempotency: re-run scaffold must not duplicate the scaffold change_log entry
        r_again = run(["scaffold", "--case", os.path.join(inp, "case.json"),
                       "--output", out, "--work", work])
        assert r_again.returncode == 0, r_again.stderr
        res2 = json.load(open(result_path, encoding="utf-8"))
        scaffold_entries = [e for e in res2["change_log"] if e.get("action") == "scaffold"]
        assert len(scaffold_entries) == 1, "scaffold must be idempotent"

        # add a locatable claim and verify
        res2["fact_source_map"].append({
            "claim_id": "c1",
            "claim_text": "正文采用小四号宋体",
            "quote": "正文小四号宋体",
            "locator": "template.txt",
            "source_ref": "template.txt",
            "evidence_level": "template_rule",
        })
        json.dump(res2, open(result_path, "w", encoding="utf-8"), ensure_ascii=False)

        report_path = os.path.join(out, "verification_report.json")
        r2 = run(["verify", "--result", result_path, "--case", os.path.join(inp, "case.json"),
                  "--report", report_path, "--input", inp, "--work", work])
        assert r2.returncode in (0, 1), r2.stderr
        report = json.load(open(report_path, encoding="utf-8"))
        assert report["checks"]["quote_locatable"]["located"] == 1
        assert report["checks"]["case_id"] is True
        assert report["checks"]["contract_fields"] is True
        assert report["checks"]["reserved_fields"] is True

        # inspect
        r3 = run(["inspect", "--input", inp, "--work", work])
        assert r3.returncode == 0, r3.stderr
        assert os.path.exists(os.path.join(work, "spec_scaffold.json"))
        assert os.path.exists(os.path.join(work, "auto_spec_hints.json"))
        hints = json.load(open(os.path.join(work, "auto_spec_hints.json"), encoding="utf-8"))
        assert any(h["spec_name"] == "line_spacing" for h in hints)

        print(json.dumps({
            "ok": True,
            "case_id": "demo-001",
            "quote_located": report["checks"]["quote_locatable"]["located"],
            "auto_hints": len(hints),
            "idempotent": True,
        }, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
