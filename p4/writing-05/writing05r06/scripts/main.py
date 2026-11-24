#!/usr/bin/env python3
"""Evidence-driven writing: deterministic pipeline entry point.

Subcommands
  scaffold   read case/task, write result.json skeleton + plan.json
  extract    extract text from /input materials into /app/work/sources
  claims     split the draft into provisional claim units
  evidence   create evidence_map.json skeleton
  verify     verbatim-quote + schema + hedge verification
  finalize   merge intermediates into final result.json + review_report.md
  run        end-to-end deterministic dry run (no semantic analysis)
  selftest   self-test on synthetic data; exit code 0 on success
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="main.py", description="evidence-driven-writing skill")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold", help="write result.json skeleton")
    sp.add_argument("--case", default=None)
    sp.add_argument("--task", default=None)

    sub.add_parser("extract", help="extract material text")
    cp = sub.add_parser("claims", help="split draft into claim units")
    cp.add_argument("--draft", default=None)
    sub.add_parser("evidence", help="create evidence_map skeleton")
    sub.add_parser("verify", help="verify quotes/schema/hedges")
    sub.add_parser("finalize", help="merge to result.json")
    sub.add_parser("run", help="end-to-end deterministic dry run")
    sub.add_parser("selftest", help="run self-test")

    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    case = getattr(a, "case", None) or os.path.join(a.input, "case.json")
    task = getattr(a, "task", None) or os.path.join(a.input, "task.md")

    if a.cmd == "scaffold":
        rp, st = C.scaffold(case, task, a.work, a.out)
        print(json.dumps({"ok": st == "ok", "result_path": rp, "status": st},
                         ensure_ascii=False))
        return 0
    if a.cmd == "extract":
        m, d = C.extract_materials(a.input, a.work)
        print(json.dumps({"ok": True, "materials": len(m), "degradations": len(d)},
                         ensure_ascii=False))
        return 0
    if a.cmd == "claims":
        draft = getattr(a, "draft", None)
        n, err = C.claims_command(a.input, a.work, draft)
        print(json.dumps({"ok": err is None, "claim_count": n, "error": err},
                         ensure_ascii=False))
        return 0
    if a.cmd == "evidence":
        C.evidence_skeleton(a.work)
        print(json.dumps({"ok": True}, ensure_ascii=False))
        return 0
    if a.cmd == "verify":
        rep = C.verify(a.work, a.out)
        print(json.dumps({"ok": True, "issues": rep.get("issue_count", 0)},
                         ensure_ascii=False))
        return 0
    if a.cmd == "finalize":
        res = C.finalize(a.work, a.out)
        print(json.dumps({"ok": True, "status": res.get("status")},
                         ensure_ascii=False))
        return 0
    if a.cmd == "run":
        res = C.run_pipeline(a.input, a.work, a.out)
        print(json.dumps({"ok": True, "status": res.get("status")},
                         ensure_ascii=False))
        return 0
    if a.cmd == "selftest":
        return C.selftest()
    ap.error("unknown command: %s" % a.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
