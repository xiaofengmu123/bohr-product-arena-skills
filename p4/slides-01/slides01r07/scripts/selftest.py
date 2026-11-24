#!/usr/bin/env python3
"""slides-01 自检：验证 SKILL.md frontmatter、脚本可编译、三类降级场景不崩溃。"""

from __future__ import annotations

import json
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PKG_ROOT / "scripts"
MAIN = SCRIPTS / "main.py"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    results = []

    # 1) SKILL.md 与 frontmatter
    skill = PKG_ROOT / "SKILL.md"
    if skill.exists():
        text = skill.read_text(encoding="utf-8")
        fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        results.append(check("SKILL.md 存在", True))
        if fm:
            front = fm.group(1)
            has_name = bool(re.search(r"^name:\s*\S+", front, re.M))
            has_desc = bool(re.search(r"^description:\s*\S+", front, re.M))
            has_ver = bool(re.search(r"^version:\s*\S+", front, re.M))
            results.append(check("frontmatter 含 name/description/version",
                                 has_name and has_desc and has_ver))
        else:
            results.append(check("frontmatter 含 name/description/version", False, "未找到 YAML frontmatter"))
    else:
        results.append(check("SKILL.md 存在", False))

    # 2) 脚本可编译
    for py in sorted(SCRIPTS.glob("*.py")):
        try:
            py_compile.compile(str(py), doraise=True)
            results.append(check(f"可编译 {py.name}", True))
        except py_compile.PyCompileError as exc:
            results.append(check(f"可编译 {py.name}", False, str(exc)))

    # 3) 三类降级场景：空输入 / 坏 JSON / 缺参数
    def run_case(tag: str, prep) -> bool:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp, work, out = root / "input", root / "work", root / "output"
            inp.mkdir(parents=True)
            prep(inp)
            try:
                proc = subprocess.run(
                    [sys.executable, "-B", str(MAIN),
                     "--input", str(inp), "--work", str(work), "--output", str(out)],
                    capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                return check(f"{tag}: 不崩溃（超时）", False)
            ok_exit = proc.returncode == 0
            res_file = out / "result.json"
            ok_res = res_file.exists()
            status = None
            if ok_res:
                try:
                    obj = json.loads(res_file.read_text(encoding="utf-8"))
                    status = obj.get("status")
                    ok_res = bool(obj.get("case_id"))
                except (json.JSONDecodeError, OSError):
                    ok_res = False
            return check(f"{tag}: 退出码0+合法result.json",
                         ok_exit and ok_res, f"status={status}")

    results.append(run_case("空输入", lambda inp: None))
    results.append(run_case("坏JSON", lambda inp: (inp / "case.json").write_text("{bad json", encoding="utf-8")))
    results.append(run_case("缺参数(默认路径由显式参数覆盖)",
                            lambda inp: (inp / "case.json").write_text(
                                json.dumps({"case_id": "selftest", "output_contract":
                                            {"case_id": "string", "status": "string",
                                             "editable_source": "object", "export_artifact": "object",
                                             "content_map": "list", "compliance_checks": "list",
                                             "exceptions": "list"}}),
                                encoding="utf-8")))

    passed = all(results)
    print(f"\n{'ALL PASS' if passed else 'SOME FAIL'}: {sum(results)}/{len(results)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
