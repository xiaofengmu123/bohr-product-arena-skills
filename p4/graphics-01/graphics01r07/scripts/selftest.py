#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-schematic-editor 自检：覆盖五类鲁棒性场景 + 正常出图。

场景：
  1. 空输入目录 -> 退出码 0，result.json 状态 partial 且记录 uncertainty
  2. 坏 JSON（case.json）-> 不崩溃，错误写入 errors
  3. 缺输入目录 -> 不崩溃，errors 记录缺失
  4. 坏编码（latin-1 字节）-> 容错读取，不崩溃
  5. 超大文件（>2MB）-> 跳过并记录 degradation
  6. 正常 spec -> 生成可解析、含 <g> 与 <text> 的分层 SVG
  7. 校验器对缺失产物如实报错（不伪造）

全部通过退出码 0，任一失败退出码 1。仅用标准库。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import core_impl as core  # noqa: E402

RESULTS = []


def run_main(args, cwd=HERE):
    return subprocess.run([sys.executable, MAIN] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%-28s %s  %s" % ("PASS" if cond else "FAIL", name, detail))


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    base = tempfile.mkdtemp(prefix="sci-schematic-selftest-")

    # 1. 空输入目录
    d = os.path.join(base, "empty")
    os.makedirs(os.path.join(d, "input"))
    r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                  "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    res = load(os.path.join(d, "output", "result.json"))
    check("empty-input", r.returncode == 0 and res.get("status") == "partial"
          and any("empty" in u.get("reason", "") for u in res.get("uncertainty", [])),
          "rc=%d status=%s" % (r.returncode, res.get("status")))

    # 2. 坏 JSON
    d = os.path.join(base, "badjson")
    os.makedirs(os.path.join(d, "input"))
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as f:
        f.write('{"case_id": "x", "broken": [1,2,')
    r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                  "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    res = load(os.path.join(d, "output", "result.json"))
    check("bad-json", r.returncode == 0 and any("case.json" in e.get("reason", "") for e in res.get("errors", [])),
          "rc=%d errors=%d" % (r.returncode, len(res.get("errors", []))))

    # 3. 缺输入目录
    d = os.path.join(base, "noinput")
    r = run_main(["scaffold", "--input", os.path.join(d, "nope"),
                  "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    res = load(os.path.join(d, "output", "result.json"))
    check("missing-input", r.returncode == 0 and res.get("case_id") == "unknown"
          and any("input dir" in e.get("reason", "") for e in res.get("errors", [])),
          "rc=%d status=%s" % (r.returncode, res.get("status")))

    # 4. 坏编码（latin-1 字节）
    d = os.path.join(base, "badenc")
    os.makedirs(os.path.join(d, "input"))
    with open(os.path.join(d, "input", "notes.md"), "wb") as f:
        f.write("caf\xe9 la nai\xefve \xf0".encode("latin-1"))
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as f:
        f.write('{"case_id": "enc"}')
    r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                  "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    res = load(os.path.join(d, "output", "result.json"))
    check("bad-encoding", r.returncode == 0 and res.get("case_id") == "enc",
          "rc=%d case_id=%s" % (r.returncode, res.get("case_id")))

    # 5. 超大文件（>2MB）
    d = os.path.join(base, "oversize")
    os.makedirs(os.path.join(d, "input"))
    with open(os.path.join(d, "input", "big.txt"), "wb") as f:
        f.write(b"x" * (core.FILE_CAP + 100))
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as f:
        f.write('{"case_id": "big"}')
    r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                  "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    res = load(os.path.join(d, "output", "result.json"))
    src = next((s for s in res.get("sources", []) if s.get("ref") == "input discovery"
                and "big.txt" in s.get("path", "")), None)
    check("oversize-file", r.returncode == 0 and src is not None and src.get("note") == "oversize",
          "rc=%d noted=%s" % (r.returncode, src.get("note") if src else None))

    # 6. 正常 spec -> 分层 SVG
    d = os.path.join(base, "ok")
    os.makedirs(os.path.join(d, "output"))
    os.makedirs(os.path.join(d, "work"))
    spec = {
        "title": "Selftest mechanism",
        "canvas": {"width_mm": 120, "height_mm": 90},
        "entities": [
            {"id": "a", "label": "Ligand", "role": "node"},
            {"id": "b", "label": "Receptor", "role": "node"},
            {"id": "c", "label": "Output", "role": "process", "shape": "ellipse"},
        ],
        "relations": [
            {"from": "a", "to": "b", "type": "activation", "label": "bind"},
            {"from": "b", "to": "c", "type": "inhibition"},
        ],
        "legend": {"items": [{"label": "up", "color": "#1b1b1b", "shape": "arrow"},
                             {"label": "down", "color": "#1b1b1b", "shape": "line"}]},
    }
    spec_path = os.path.join(d, "work", "figure_spec.json")
    core.write_json(spec_path, spec)
    r = run_main(["build", "--spec", spec_path, "--output", os.path.join(d, "output"),
                  "--work", os.path.join(d, "work")])
    svg_path = os.path.join(d, "output", "figure.svg")
    ok_svg = os.path.exists(svg_path)
    if ok_svg:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
        tag = lambda n: "{%s}%s" % (ns, n) if ns else n
        has_g = len(root.findall(".//" + tag("g"))) >= 6
        has_text = len(root.findall(".//" + tag("text"))) >= 3
        raw_svg = open(svg_path, encoding="utf-8").read()
        has_tee = "url(#tee-head)" in raw_svg and "url(#arrow-head)" in raw_svg
        ok_svg = ok_svg and has_g and has_text and has_tee
    check("normal-spec-svg", r.returncode == 0 and ok_svg,
          "rc=%d svg=%s" % (r.returncode, svg_path if ok_svg else "missing"))

    # 7. 校验器对缺失产物如实报错
    res_path = os.path.join(d, "output", "result.json")
    core.write_json(res_path, {
        "case_id": "ok", "status": "complete",
        "editable_source_files": [{"path": "figure.svg", "kind": "svg"}],
        "export_files": [{"path": "figure.png", "format": "png", "width_px": 1417,
                          "height_px": 1063, "dpi": 300, "color_space": "RGB",
                          "font_check": "none", "note": ""}],
        "provenance": [], "sources": [], "uncertainty": [], "pending_human": [],
        "errors": [], "degradations": [], "change_log": [], "runtime": {},
    })
    rep = core.validate_result(load(res_path), os.path.join(d, "output"))
    check("validator-honest", rep.get("ok") is False
          and any("export missing" in e for e in rep.get("errors", [])),
          "errors=%d" % len(rep.get("errors", [])))

    # 确定性：同 spec 两次渲染逐字节一致
    svg1 = core.render_spec(spec)
    svg2 = core.render_spec(spec)
    check("deterministic", svg1 == svg2, "bytes=%d" % len(svg1))

    shutil.rmtree(base, ignore_errors=True)
    fails = [n for n, ok, _ in RESULTS if not ok]
    print("\n%s/%s passed" % (len(RESULTS) - len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
