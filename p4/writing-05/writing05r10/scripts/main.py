#!/usr/bin/env python3
"""Entry point for the evidence-claim-writing Skill.

Commands (flags):
  --scaffold   read case.json, write result.json skeleton (status=in_progress)
  --finalize   validate the claim graph IR, render result.json + evidence_writing.md
  --validate   validate an existing result.json (used by selftest)
  (no flag)    run scaffold then finalize as a full pipeline

Standard library only. Defaults match the evaluation runtime:
  --input /input, --output /app/output, --work /app/work, --case <input>/case.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as core  # noqa: E402


def read_json(path):
    """Read a JSON file with encoding tolerance; never raises."""
    if not path or not os.path.exists(path):
        return None, f"missing: {path}"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f), None
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as e:
            return None, f"bad json: {e}"
        except Exception as e:  # noqa: BLE001
            return None, f"read error: {e}"
    return None, "undecodable encoding"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def get_case_id(case):
    for k in ("case_id", "caseId", "id"):
        v = case.get(k) if isinstance(case, dict) else None
        if v not in (None, ""):
            return str(v)
    return None


def get_contract(case):
    for k in ("output_contract", "outputContract", "contract", "result_schema"):
        v = case.get(k) if isinstance(case, dict) else None
        if isinstance(v, dict):
            return v
    return None


def load_graph(work):
    for name in ("claim_graph.json", "analysis.json"):
        p = os.path.join(work, name)
        if os.path.exists(p):
            g, err = read_json(p)
            if isinstance(g, dict):
                return g, None
            return None, err or f"unreadable {name}"
    return None, "no claim_graph.json / analysis.json in work dir"


def cmd_scaffold(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "result.json")
    # Never clobber an improved (non-in_progress) result.
    if os.path.exists(out_path):
        prev, _ = read_json(out_path)
        if isinstance(prev, dict) and prev.get("status") not in (None, "in_progress", "input_error"):
            print(json.dumps({"ok": True, "skipped": True,
                              "reason": "existing non-in_progress result preserved"}, ensure_ascii=False))
            return 0
    case, err = read_json(args.case)
    if case is None:
        sk = core.result_skeleton(None, None)
        sk["status"] = "input_error"
        sk["errors"].append({"msg": err or "case.json missing"})
        write_json(out_path, sk)
        print(json.dumps({"ok": True, "status": "input_error", "error": err}, ensure_ascii=False))
        return 0
    cid = get_case_id(case)
    contract = get_contract(case)
    sk = core.result_skeleton(cid, contract)
    write_json(out_path, sk)
    # Seed an empty IR so the agent edits incrementally rather than starting blind.
    graph_path = os.path.join(args.work, "claim_graph.json")
    if not os.path.exists(graph_path):
        g = core.init_claim_graph(cid, case.get("materials") or [])
        write_json(graph_path, g)
    print(json.dumps({"ok": True, "case_id": cid, "status": "in_progress"}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "result.json")
    prev, _ = read_json(out_path)
    case, _ = read_json(args.case)
    cid = get_case_id(prev) if isinstance(prev, dict) else None
    contract = get_contract(prev) if isinstance(prev, dict) else None
    if cid is None and isinstance(case, dict):
        cid = get_case_id(case)
    if contract is None and isinstance(case, dict):
        contract = get_contract(case)

    graph, gerr = load_graph(args.work)
    issues, warnings = ([], [])
    if isinstance(graph, dict):
        issues, warnings = core.validate_claim_graph(graph)

    sk = core.result_skeleton(cid, contract)
    prior_log = []
    if isinstance(prev, dict) and isinstance(prev.get("change_log"), list):
        prior_log = prev["change_log"]
    if not prior_log:
        prior_log = [{"time": core.utc_now(), "action": "scaffold",
                      "detail": "no prior skeleton; created at finalize"}]

    if isinstance(graph, dict):
        sk["deliverable"] = {
            "claims": graph.get("claims") or [],
            "evidence": graph.get("sources") or [],
            "draft": graph.get("draft") or "",
            "summary": graph.get("summary") or "",
        }
        sk["sources"] = core.build_sources(graph)
        sk["uncertainty"] = graph.get("uncertainty") or []
        sk["pending_human"] = graph.get("pending_human") or []
        sk["limitations"] = graph.get("limitations") or []
        for w in warnings:
            sk["limitations"].append(w.get("msg"))
        for i in issues:
            target = sk["errors"] if i.get("level") == "error" else sk["limitations"]
            target.append(i.get("msg"))
        sk["status"] = core.decide_status(graph, issues)
    else:
        sk["status"] = "insufficient_evidence"
        sk["limitations"].append(gerr or "no claim graph; only partial evidence available")
        sk["errors"].append(gerr or "missing claim graph")

    sk["change_log"] = prior_log + [
        {"time": core.utc_now(), "action": "finalize",
         "detail": "validated IR and rendered deliverables"}
    ]
    sk["runtime"]["finished_at"] = core.utc_now()

    write_json(out_path, sk)
    md_path = os.path.join(args.out, "evidence_writing.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(core.render_markdown(graph))
    print(json.dumps({"ok": True, "status": sk["status"],
                      "result": out_path, "markdown": md_path}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    r, err = read_json(os.path.join(args.out, "result.json"))
    if r is None:
        print("FAIL - no result.json: " + (err or ""))
        return 1
    problems = []
    for k in ("case_id", "status", "deliverable", "sources", "uncertainty",
              "pending_human", "change_log"):
        if k not in r:
            problems.append("missing key: " + k)
    valid_status = {"complete", "partial", "insufficient_evidence",
                    "input_error", "failed", "in_progress"}
    if r.get("status") not in valid_status:
        problems.append("invalid status: " + str(r.get("status")))
    if problems:
        print("FAIL")
        for p in problems:
            print("  - " + p)
        return 1
    print("PASS - result.json has required keys and valid status")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="evidence-claim-writing skill entry point")
    ap.add_argument("--scaffold", action="store_true", help="write result.json skeleton")
    ap.add_argument("--finalize", action="store_true", help="validate IR and render deliverables")
    ap.add_argument("--validate", action="store_true", help="validate existing result.json")
    ap.add_argument("--case", default=None, help="path to case.json (default <input>/case.json)")
    ap.add_argument("--input", default="/input", help="input directory")
    ap.add_argument("--output", "--out", dest="out", default="/app/output", help="output directory")
    ap.add_argument("--work", default="/app/work", help="work directory")
    a = ap.parse_args(argv)
    a.case = a.case or os.path.join(a.input, "case.json")
    if a.scaffold:
        return cmd_scaffold(a)
    if a.finalize:
        return cmd_finalize(a)
    if a.validate:
        return cmd_validate(a)
    rc = cmd_scaffold(a)
    if rc != 0:
        return rc
    return cmd_finalize(a)


if __name__ == "__main__":
    sys.exit(main())
