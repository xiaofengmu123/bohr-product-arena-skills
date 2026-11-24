#!/usr/bin/env python3
"""Self-test for the skill package. Run from the skill root:

    python scripts/selftest.py

Checks: --help, empty input, bad JSON, missing input dir, SVG render, validate.
Standard library only. Exits 0 iff all checks pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args):
    return subprocess.run([sys.executable, MAIN] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=HERE)


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((": " + str(detail)) if detail else ""))
    return bool(cond)


def last_json(stdout):
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return None


def main():
    results = []

    r = run(["--help"])
    results.append(check("main.py --help exits 0", r.returncode == 0, f"rc={r.returncode}"))

    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "input"), os.path.join(d, "out"), os.path.join(d, "work")
        os.makedirs(inp)
        r = run(["init", "--input", inp, "--out", out, "--work", work])
        status = None
        if os.path.exists(os.path.join(out, "result.json")):
            with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
                status = json.load(fh).get("status")
        results.append(check("empty input -> legal result.json",
                             r.returncode == 0 and status == "insufficient_evidence",
                             f"rc={r.returncode} status={status}"))

    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "input"), os.path.join(d, "out"), os.path.join(d, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        r = run(["init", "--input", inp, "--out", out, "--work", work,
                 "--case", os.path.join(inp, "case.json")])
        status = None
        if os.path.exists(os.path.join(out, "result.json")):
            with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
                status = json.load(fh).get("status")
        results.append(check("bad case.json -> input_error, exit 0",
                             r.returncode == 0 and status == "input_error",
                             f"rc={r.returncode} status={status}"))

    with tempfile.TemporaryDirectory() as d:
        out, work = os.path.join(d, "out"), os.path.join(d, "work")
        r = run(["init", "--input", os.path.join(d, "nope"), "--out", out, "--work", work])
        status = None
        if os.path.exists(os.path.join(out, "result.json")):
            with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
                status = json.load(fh).get("status")
        results.append(check("missing input dir -> legal result",
                             r.returncode == 0 and status in ("input_error", "insufficient_evidence"),
                             f"rc={r.returncode} status={status}"))

    with tempfile.TemporaryDirectory() as d:
        spec = {
            "title": "Self-test", "canvas": {"width_px": 1200, "height_px": 800, "dpi": 150},
            "entities": [
                {"id": "a", "label": "A", "x": 0.2, "y": 0.5, "shape": "rounded"},
                {"id": "b", "label": "B", "x": 0.8, "y": 0.5, "shape": "rounded"},
            ],
            "relations": [{"id": "r1", "from": "a", "to": "b", "kind": "activation"}],
            "legend": {"items": [{"label": "activation", "color": "#1a73e8", "marker": "arrow"}]},
        }
        spec_path = os.path.join(d, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        svg_path = os.path.join(d, "fig.svg")
        r = run(["render", "--spec", spec_path, "--out", svg_path])
        svg_ok = False
        if os.path.exists(svg_path):
            try:
                ET.parse(svg_path)
                svg_ok = True
            except Exception:  # noqa: BLE001
                pass
        results.append(check("render -> well-formed SVG", r.returncode == 0 and svg_ok,
                             f"rc={r.returncode}"))

        inp, out, work = os.path.join(d, "input"), os.path.join(d, "out"), os.path.join(d, "work")
        os.makedirs(inp)
        case_path = os.path.join(inp, "case.json")
        with open(case_path, "w", encoding="utf-8") as fh:
            json.dump({"case_id": "demo", "output_contract": {"custom_key": "kept"}}, fh)
        run(["init", "--input", inp, "--out", out, "--work", work, "--case", case_path])
        run(["render", "--spec", spec_path, "--out", os.path.join(out, "figure01.svg"),
             "--result", os.path.join(out, "result.json")])
        rv = run(["validate", "--result", os.path.join(out, "result.json"), "--out", out])
        rep = last_json(rv.stdout)
        ok_contract = False
        if os.path.exists(os.path.join(out, "result.json")):
            with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
                ok_contract = json.load(fh).get("custom_key") == "kept"
        results.append(check("validate runs and reports",
                             rv.returncode == 0 and isinstance(rep, dict) and "checks" in rep,
                             f"rc={rv.returncode}"))
        results.append(check("output_contract keys passthrough", ok_contract))

    ok = all(results)
    print("SUMMARY " + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
