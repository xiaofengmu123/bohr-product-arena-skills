#!/usr/bin/env python3
"""Orchestrator: early skeleton -> material inventory -> contract validation.

Guarantees an honest partial result.json lands on disk immediately, then improves
incrementally. Stdlib only.

    python scripts/main.py --case /input/case.json --task /input/task.md \
        --input /input --work /app/work --output /app/output
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence  # noqa: E402
import verify  # noqa: E402


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_json_tolerant(path):
    if not os.path.isfile(path):
        return None, "missing file: {}".format(path)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f), "ok"
        except (UnicodeDecodeError, UnicodeError):
            continue
        except json.JSONDecodeError as e:
            return None, "bad json: {}".format(e)
        except OSError as e:
            return None, "read error: {}".format(e)
    return None, "undecodable"


def default_skeleton(case_obj, task_text):
    cid = None
    if isinstance(case_obj, dict):
        cid = case_obj.get("case_id") or case_obj.get("id")
    return {
        "case_id": cid or "",
        "status": "partial",
        "diagnosis": "",
        "edit_plan": [],
        "source_map": [],
        "version_boundary": {"diff_summary": "", "material_gaps": [],
                             "anomalies": []},
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": now_iso(),
            "action": "skeleton_written",
            "status": "partial",
            "note": "初始骨架落盘；六维分析待进行",
        }],
    }


def build_skeleton(case_obj, task_text, existing):
    base = existing if isinstance(existing, dict) and existing else {}
    sk = default_skeleton(case_obj, task_text)
    for k, v in sk.items():
        if k not in base or base[k] in (None, "", [], {}):
            base.setdefault(k, v)
    if not isinstance(base.get("change_log"), list) or not base["change_log"]:
        base["change_log"] = sk["change_log"]
    oc = None
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract")
    if isinstance(oc, dict):
        for k in oc:
            base.setdefault(k, None)
    elif isinstance(oc, list):
        for k in oc:
            if isinstance(k, str):
                base.setdefault(k, None)
    cid = (case_obj.get("case_id") or case_obj.get("id")) \
        if isinstance(case_obj, dict) else None
    if cid and not base.get("case_id"):
        base["case_id"] = cid
    base["status"] = base.get("status") or "partial"
    return base


def atomic_write(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            return True
        except OSError:
            return False


def append_change(result, action, status, note):
    cl = result.setdefault("change_log", [])
    if not isinstance(cl, list):
        cl = []
        result["change_log"] = cl
    for e in cl:
        if isinstance(e, dict) and e.get("action") == action:
            return
    cl.append({"time": now_iso(), "action": action, "status": status,
               "note": note})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=os.environ.get("CASE_JSON",
                                                     "/input/case.json"))
    ap.add_argument("--task", default=os.environ.get("TASK_MD", "/input/task.md"))
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    a = ap.parse_args()

    for d in (a.work, a.output):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass

    result_path = os.path.join(a.output, "result.json")
    case_obj, case_err = load_json_tolerant(a.case)
    task_text = None
    if os.path.isfile(a.task):
        task_text, _ = evidence.read_text(a.task)
    if task_text is None:
        task_text = ""

    # 1) early skeleton: never leave result.json absent
    existing = None
    if os.path.isfile(result_path):
        existing, _ = load_json_tolerant(result_path)
    result = build_skeleton(case_obj, task_text, existing)
    atomic_write(result_path, result)

    # 2) material inventory + manuscript segmentation + claim candidates
    ev = evidence.build_evidence(a.input)
    ev_path = os.path.join(a.work, "evidence.json")
    atomic_write(ev_path, ev)

    # 3) merge inventory gaps and record the action (do NOT auto-fill unverified
    #    sources with raw candidates — the model verifies before writing sources)
    result = load_json_tolerant(result_path)[0] or result
    vb = result.setdefault("version_boundary", {})
    if not isinstance(vb, dict):
        vb = {}
        result["version_boundary"] = vb
    vb["material_gaps"] = [w for w in ev.get("warnings", [])]
    append_change(result, "inventory_done", "partial",
                  "materials={}, candidate_claims={}".format(
                      len(ev.get("materials", [])),
                      len(ev.get("candidate_claims", []))))
    atomic_write(result_path, result)

    # 4) contract validation (informational; model fixes FAILs in step 4)
    required = verify.discover_contract_keys(case_obj)
    checks = verify.validate(result, required)
    report = {
        "generated_at": now_iso(),
        "result_path": result_path,
        "required_keys": required,
        "checks": checks,
        "pass": sum(1 for c in checks if c["level"] == "PASS"),
        "warn": sum(1 for c in checks if c["level"] == "WARN"),
        "fail": sum(1 for c in checks if c["level"] == "FAIL"),
        "ok": not any(c["level"] == "FAIL" for c in checks),
    }
    verify_report_path = os.path.join(a.output, "verify_report.json")
    atomic_write(verify_report_path, report)

    print(json.dumps({
        "ok": report["ok"],
        "result": result_path,
        "evidence": ev_path,
        "verify": verify_report_path,
        "materials": len(ev.get("materials", [])),
        "candidate_claims": len(ev.get("candidate_claims", [])),
        "verify_fail": report["fail"],
        "status": result.get("status"),
        "case_err": case_err,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
