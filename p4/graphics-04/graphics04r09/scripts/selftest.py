#!/usr/bin/env python3
"""selftest.py — smoke test for the figure-ir-skill pipeline.

Runs build + validate on a synthetic IR, and checks the robustness paths
(missing input, empty IR). Exit code 0 means all assertions passed.
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core_impl as C  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp(prefix="figure-ir-selftest-"))
    ir = {
        "case_id": "selftest",
        "title": "自检样例",
        "kind": "flow",
        "entities": [
            {"id": "e1", "label": "原料储罐", "type": "vessel", "source_ref": "demo.md#L1"},
            {"id": "e2", "label": "反应釜", "type": "vessel", "source_ref": "demo.md#L2"},
            {"id": "e3", "label": "产物收集", "type": "vessel", "source_ref": "demo.md#L3"},
        ],
        "relations": [
            {"from": "e1", "to": "e2", "kind": "flow", "direction": "forward", "source_ref": "demo.md#L4"},
            {"from": "e2", "to": "e3", "kind": "flow", "direction": "forward", "source_ref": "demo.md#L5"},
        ],
        "data_encoding": [{"key": "温度", "mapping": "120 °C → 节点标红", "source_ref": "demo.md#L6"}],
        "legend_annotations": [],
        "sources": [{"path": "demo.md", "locator": "L1-L6"}],
        "uncertainty": [],
    }

    svg_path = tmp / "figure.svg"
    meta = C.build_svg(ir, str(svg_path))
    checks = C.validate(ir, str(svg_path))
    fails = [c for c in checks if c["status"] == "fail"]

    missing_obj, missing_err = C.read_json(str(tmp / "does-not-exist.json"))
    missing_handled = missing_obj is None and bool(missing_err)

    empty_meta = C.build_svg({"entities": [], "relations": [], "title": "empty"},
                             str(tmp / "empty.svg"))

    report = {
        "ok": bool(svg_path.exists())
        and not fails
        and missing_handled
        and empty_meta["n_entities"] == 0,
        "svg_exists": svg_path.exists(),
        "svg_bytes": svg_path.stat().st_size if svg_path.exists() else 0,
        "checks": {c["id"]: c["status"] for c in checks},
        "fails": fails,
        "missing_handled": missing_handled,
        "empty_build_ok": empty_meta["n_entities"] == 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
