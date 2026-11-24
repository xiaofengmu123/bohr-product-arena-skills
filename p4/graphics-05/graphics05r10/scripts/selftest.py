#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：空输入 / 坏 JSON / 合成案例三态，覆盖脚本路径与产物合法性。

合成案例是显式标注的示意数据，仅用于验证确定性布局与 SVG 可解析性，不冒充正式案例。
运行：python3 -B scripts/selftest.py（退出码 0 表示通过，1 表示失败）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import core_impl as ci  # noqa: E402
import main as pipeline  # noqa: E402


def _mk(d: Path, files: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(content, encoding="utf-8")
    return d


def _ns(input_dir: str, output_dir: str, work_dir: str, only: str = "full") -> argparse.Namespace:
    return argparse.Namespace(input=input_dir, output=output_dir, work=work_dir,
                              only=only, uncertainty=None, limitations=None)


def test_empty_input() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = _mk(root / "input", {})
        out = root / "out"
        wrk = root / "work"
        rc = pipeline.run(_ns(str(inp), str(out), str(wrk)))
        res = ci.load_json(out / "result.json")[0]
        return (isinstance(res, dict) and res.get("status") == "failed"
                and res.get("case_id") is None and rc != 0)


def test_bad_json() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = _mk(root / "input", {"case.json": "{not valid json", "task.md": "task text"})
        out = root / "out"
        wrk = root / "work"
        rc = pipeline.run(_ns(str(inp), str(out), str(wrk)))
        res = ci.load_json(out / "result.json")[0]
        deg = (res or {}).get("degradations") or []
        return isinstance(res, dict) and any("bad_json" in d for d in deg)


def test_synthetic_full() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = _mk(root / "input", {
            "case.json": json.dumps({
                "case_id": "synthetic-001",
                "output_contract": {"custom_key": "kept"},
                "facts": {
                    "question": "Q?", "method": "M", "results": "R", "conclusion": "C",
                    "title": "Synthetic demo (示意)",
                    "entities": [
                        {"id": "e1", "label": "Cell A", "kind": "cell", "panel": "question",
                         "source": "s1", "shape": "rect", "role": "subject"},
                        {"id": "e2", "label": "Molecule B", "kind": "molecule",
                         "panel": "results", "source": "s1", "shape": "ellipse",
                         "role": "result"},
                    ],
                    "relations": [
                        {"id": "r1", "from": "e1", "to": "e2", "kind": "promotes",
                         "direction": "forward", "label": "activates", "source": "s1"},
                    ],
                    "data_mapping": [
                        {"id": "d1", "entity_id": "e2", "channel": "color", "value": 3.2,
                         "unit": "uM", "scale": "log", "source": "s1"},
                    ],
                    "legend": [{"channel": "color", "meaning": "concentration", "source": "s1"}],
                    "evidence": [
                        {"id": "s1", "locator": "case.json#facts", "quote": "Cell A activates Molecule B",
                         "kind": "fact"},
                    ],
                },
            }),
            "task.md": "Synthetic self-test case (示意，非正式案例)",
        })
        out = root / "out"
        wrk = root / "work"
        rc = pipeline.run(_ns(str(inp), str(out), str(wrk)))
        res = ci.load_json(out / "result.json")[0]
        svg = out / "fig_synthetic-001.svg"
        if not (isinstance(res, dict) and svg.exists()):
            return False
        for key in ("case_id", "status", "sources", "uncertainty", "pending_human",
                    "change_log", "limitations", "custom_key"):
            if key not in res:
                return False
        if res.get("status") != "completed":
            return False
        try:
            ET.parse(svg)
        except ET.ParseError:
            return False
        # 确定性：重跑一次，SVG 字节一致
        rc2 = pipeline.run(_ns(str(inp), str(out), str(wrk)))
        svg2 = ci.read_text(svg)[0]
        return rc == 0 and rc2 == 0 and len(svg2) > 0 and res.get("custom_key") == "kept"


def main() -> int:
    checks = [
        ("empty_input", test_empty_input),
        ("bad_json", test_bad_json),
        ("synthetic_full", test_synthetic_full),
    ]
    ok = True
    for name, fn in checks:
        try:
            passed = fn()
        except Exception as exc:  # noqa: BLE001
            passed = False
            print(f"[FAIL] {name}: exception {exc}")
        else:
            print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
