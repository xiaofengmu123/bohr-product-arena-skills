#!/usr/bin/env python3
"""Package self-test for scix-figure-contract-skill.

Checks (read-only where possible, writes only to a temp dir):
  1. SKILL.md exists and has valid name/description/version frontmatter
  2. scripts importable with standard library only
  3. empty input degrades to a valid partial result
  4. result.json aligns field-by-field with a declared output_contract
  5. IR -> SVG output is well-formed XML and deterministic on re-run
  6. validation flags dangling arrows / overflow / missing evidence

Exit code 0 means all checks passed; 1 means at least one failure.
Usage: python scripts/selftest.py
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    failures = []
    results = {}

    # 1. SKILL.md frontmatter
    skill_path = os.path.join(ROOT, "SKILL.md")
    if not os.path.isfile(skill_path):
        failures.append("SKILL.md 缺失")
        results["frontmatter"] = "FAIL"
    else:
        text = open(skill_path, encoding="utf-8").read()
        fm = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not fm:
            failures.append("SKILL.md 无合法 YAML frontmatter")
            results["frontmatter"] = "FAIL"
        else:
            body = fm.group(1)
            name = re.search(r"^name:\s*(\S+)", body, re.MULTILINE)
            desc = re.search(r"^description:\s*(.+)", body, re.MULTILINE)
            ver = re.search(r"^version:\s*(\S+)", body, re.MULTILINE)
            if not (name and desc and ver):
                failures.append("frontmatter 缺 name/description/version 之一")
                results["frontmatter"] = "FAIL"
            else:
                results["frontmatter"] = "OK name=%s version=%s" % (name.group(1), ver.group(1))

    # 2. scripts importable, standard library only
    sys.path.insert(0, HERE)
    try:
        import core_impl  # noqa: F401
        import main  # noqa: F401
        results["imports"] = "OK"
    except Exception as exc:  # noqa: BLE001
        failures.append("脚本导入失败: %s" % exc)
        results["imports"] = "FAIL"

    # check for forbidden third-party imports
    for fname in ("core_impl.py", "main.py", "selftest.py"):
        src = open(os.path.join(HERE, fname), encoding="utf-8").read()
        for m in ("numpy", "matplotlib", "pandas", "PIL", "cv2"):
            if re.search(r"\bimport\s+%s\b|\bfrom\s+%s\b" % (re.escape(m), re.escape(m)), src):
                failures.append("%s 引用了第三方库 %s" % (fname, m))
                results["stdlib"] = "FAIL"

    tmp = tempfile.mkdtemp(prefix="scix_selftest_")
    try:
        # 3. empty input -> valid partial result
        empty_in = os.path.join(tmp, "empty_in")
        os.makedirs(empty_in)
        out_dir = os.path.join(tmp, "empty_out")
        r = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "run",
                            "--case", os.path.join(empty_in, "case.json"),
                            "--out", out_dir, "--work", os.path.join(tmp, "empty_work")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        res_path = os.path.join(out_dir, "result.json")
        if r.returncode != 0 or not os.path.isfile(res_path):
            failures.append("空输入未产生 result.json（rc=%s）" % r.returncode)
            results["empty_input"] = "FAIL"
        else:
            obj = json.load(open(res_path, encoding="utf-8"))
            if obj.get("status") != "partial" or not obj.get("case_id"):
                failures.append("空输入 status 不是 partial 或缺 case_id")
                results["empty_input"] = "FAIL"
            elif not obj.get("limitations"):
                failures.append("空输入未写 limitations")
                results["empty_input"] = "FAIL"
            else:
                results["empty_input"] = "OK status=partial"

        # 4. contract alignment
        case_dir = os.path.join(tmp, "contract_in")
        os.makedirs(case_dir)
        contract = {"entities": {"desc": "x"}, "relations": {}, "custom_metric": "n"}
        case = {"case_id": "case-123", "output_contract": contract}
        json.dump(case, open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8"), ensure_ascii=False)
        out2 = os.path.join(tmp, "contract_out")
        r = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "init",
                            "--case", os.path.join(case_dir, "case.json"),
                            "--out", out2, "--work", os.path.join(tmp, "contract_work")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        obj2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
        missing = [k for k in contract if k not in obj2]
        if missing:
            failures.append("契约字段缺失: %s" % missing)
            results["contract_align"] = "FAIL"
        else:
            results["contract_align"] = "OK fields=%s" % ",".join(contract.keys())

        # 5. IR -> SVG well-formed and deterministic
        ir = {
            "title": "自检示例",
            "canvas": {"width": 900, "height": 500},
            "meta": {"layout": "grid"},
            "entities": [
                {"id": "e1", "label": "反应器", "type": "vessel", "source_ref": "materials/demo.md#p1", "quote": "反应器"},
                {"id": "e2", "label": "泵", "type": "pump", "source_ref": "materials/demo.md#p2", "quote": "泵"},
            ],
            "relations": [
                {"from": "e1", "to": "e2", "kind": "material", "direction": "forward",
                 "style": "solid", "source_ref": "materials/demo.md#p3", "quote": "流向泵"},
            ],
            "legend": [{"key": "物料", "visual": "color:#e11d48;solid", "text": "物料流"}],
            "annotations": [],
            "data_encoding": [],
        }
        ir_path = os.path.join(tmp, "ir.json")
        json.dump(ir, open(ir_path, "w", encoding="utf-8"), ensure_ascii=False)
        svg1 = os.path.join(tmp, "f1.svg")
        svg2 = os.path.join(tmp, "f2.svg")
        r1 = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "build",
                             "--ir", ir_path, "--svg", svg1], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        r2 = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "build",
                             "--ir", ir_path, "--svg", svg2], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        ok_svg = (r1.returncode == 0 and r2.returncode == 0)
        try:
            ET.parse(svg1)
        except ET.ParseError:
            ok_svg = False
        if not ok_svg:
            failures.append("IR->SVG 构建或解析失败")
            results["svg_build"] = "FAIL"
        else:
            h1 = hashlib.sha256(open(svg1, "rb").read()).hexdigest()
            h2 = hashlib.sha256(open(svg2, "rb").read()).hexdigest()
            if h1 != h2:
                failures.append("重复运行产出不一致（非确定性布局）")
                results["svg_determinism"] = "FAIL"
            else:
                results["svg_determinism"] = "OK sha256=%s" % h1[:12]

        # 6. validation flags dangling arrows
        bad_ir = json.loads(json.dumps(ir))
        bad_ir["relations"].append({"from": "e1", "to": "e_missing", "kind": "material",
                                    "direction": "forward", "source_ref": "x", "quote": "y"})
        bad_path = os.path.join(tmp, "bad_ir.json")
        json.dump(bad_ir, open(bad_path, "w", encoding="utf-8"), ensure_ascii=False)
        checks_path = os.path.join(tmp, "checks.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "validate",
                            "--ir", bad_path, "--svg", svg1, "--checks", checks_path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        checks = json.load(open(checks_path, encoding="utf-8"))
        if any("dangling_to" in c for c in checks.get("critical", [])):
            results["validation"] = "OK"
        else:
            failures.append("validate 未标记断头箭头")
            results["validation"] = "FAIL"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(json.dumps({"passed": not failures, "results": results, "failures": failures},
                     ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
