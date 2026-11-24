#!/usr/bin/env python3
"""Entry point: bootstrap -> evidence -> verify (honest partial-first pipeline)."""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C


def run(script, args):
    p = os.path.join(HERE, script)
    if not os.path.exists(p):
        return None
    return subprocess.run([sys.executable, p] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(description="paper-structure-review pipeline")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--out", default="/app/output")
    args = ap.parse_args()

    paths = C.resolve_paths(args.case, args.task, args.input, args.work,
                            args.out, os.path.join(args.out, "result.json"))
    C.ensure_dirs(paths)

    started = time.time()
    steps = []

    r = run("bootstrap.py", ["--case", paths["case"], "--task", paths["task"],
                             "--out", paths["out"]])
    steps.append({"step": "bootstrap", "ok": bool(r and r.returncode == 0)})

    r = run("evidence.py", ["--case", paths["case"], "--input", paths["input"],
                            "--work", paths["work"]])
    steps.append({"step": "evidence", "ok": bool(r and r.returncode == 0)})

    r = run("verify.py", ["--case", paths["case"], "--result", paths["result"],
                          "--out", paths["out"]])
    steps.append({"step": "verify", "ok": bool(r and r.returncode == 0)})

    elapsed = round(time.time() - started, 1)
    # Do not overwrite the model's own diagnosis/evidence if it has already
    # filled them in; only ensure the skeleton exists and record runtime facts.
    data = C.load_existing_result(paths["result"])
    if not data:
        case_data = C.read_json(paths["case"])
        data = C.default_skeleton(case_data)
        C.fill_output_contract_fields(data, case_data)
        C.write_json(paths["result"], data)
    data.setdefault("runtime", {})
    data["runtime"].setdefault("started_at", C.utcnow())
    data["runtime"]["seconds"] = elapsed
    data.setdefault("change_log", []).append({
        "time": C.utcnow(),
        "action": "pipeline bootstrap+evidence+verify",
        "status": "ok" if all(s["ok"] for s in steps) else "partial",
    })
    C.write_json(paths["result"], data)

    print(json.dumps({"ok": all(s["ok"] for s in steps),
                      "steps": steps, "elapsed_s": elapsed},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
