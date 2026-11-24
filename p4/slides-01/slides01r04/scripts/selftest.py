#!/usr/bin/env python3
"""selftest.py：提交/交付前自检脚本。

验证四类事项（对应评分维度“可靠体验 / 生态贡献”）：
  1. SKILL.md 存在且 frontmatter 含 name / description / version；
  2. scripts/*.py 可被 Python 正常编译（语法与导入无硬伤）；
  3. main.py 各子命令可运行，空输入/坏 JSON/缺输入三种异常场景均退出码 0 并写合法 result.json；
  4. result.json 是合法 JSON，且含 case_id 与 status。

用法（在包根目录）：
    python3 -B scripts/selftest.py
退出码 0 = 全部通过；1 = 存在失败项。
仅依赖标准库。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PKG_ROOT / "scripts"
SKILL_MD = PKG_ROOT / "SKILL.md"
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

_failures: list = []
_passes: list = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        _passes.append(name)
        print(f"  [PASS] {name}")
    else:
        _failures.append(name)
        print(f"  [FAIL] {name}{': ' + detail if detail else ''}")


def run_main(cmd: str, input_dir: Path, work_dir: Path, output_dir: Path):
    """以子进程运行 main.py 子命令，返回 (exit_code, stdout, stderr)。"""
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS_DIR / "main.py"),
         "--cmd", cmd, "--input", str(input_dir), "--work", str(work_dir),
         "--output", str(output_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def test_frontmatter():
    print("== 1. SKILL.md frontmatter ==")
    if not SKILL_MD.is_file():
        check("SKILL.md 存在", False, "未找到 " + str(SKILL_MD))
        return
    text = SKILL_MD.read_text(encoding="utf-8-sig")
    check("SKILL.md 存在", True)
    check("frontmatter 起始 ---", text.startswith("---"))
    body = text[3:]
    if "---" in body:
        fm = body.split("---", 1)[0]
    else:
        fm = ""
    for key in ("name", "description", "version"):
        check(f"frontmatter 含 {key}", f"{key}:" in fm)


def test_syntax():
    print("== 2. scripts/*.py 语法 ==")
    for py in sorted(SCRIPTS_DIR.glob("*.py")):
        try:
            ast.parse(py.read_text(encoding="utf-8"))
            check(f"{py.name} 语法", True)
        except SyntaxError as exc:
            check(f"{py.name} 语法", False, str(exc))


def _scenario(name: str, setup):
    with tempfile.TemporaryDirectory(prefix="slides01-selftest-") as td:
        root = Path(td)
        input_dir = root / "input"
        work_dir = root / "work"
        output_dir = root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        setup(input_dir)
        r = run_main("skeleton", input_dir, work_dir, output_dir)
        check(f"{name}: skeleton 退出码 0", r.returncode == 0, r.stderr.strip()[:200])
        result_path = output_dir / "result.json"
        check(f"{name}: result.json 已写", result_path.is_file())
        ok_json = False
        if result_path.is_file():
            try:
                res = json.loads(result_path.read_text(encoding="utf-8"))
                ok_json = True
                check(f"{name}: result.json 合法 JSON", True)
                check(f"{name}: 含 case_id", "case_id" in res)
                check(f"{name}: 含 status", "status" in res)
                check(f"{name}: status 合法", res.get("status") in VALID_STATUS)
            except json.JSONDecodeError:
                check(f"{name}: result.json 合法 JSON", False)
        if not ok_json:
            check(f"{name}: 含 case_id", False)
            check(f"{name}: 含 status", False)
            check(f"{name}: status 合法", False)
        # 其余子命令也应可运行（不崩溃）
        for cmd in ("plan", "finalize", "audit", "pdfcheck"):
            r2 = run_main(cmd, input_dir, work_dir, output_dir)
            check(f"{name}: {cmd} 退出码 0", r2.returncode == 0, r2.stderr.strip()[:200])
        return output_dir


def test_scenarios():
    print("== 3. 异常输入降级 ==")

    def empty(_in):
        pass
    _scenario("空输入", empty)

    def bad_json(inp):
        (inp / "case.json").write_text("{ not valid json !!!", encoding="utf-8")
    _scenario("坏 JSON", bad_json)

    with tempfile.TemporaryDirectory(prefix="slides01-selftest-missing-") as td:
        root = Path(td)
        work = root / "work"
        out = root / "output"
        r = run_main("skeleton", root / "does-not-exist", work, out)
        check("缺输入: skeleton 退出码 0", r.returncode == 0, r.stderr.strip()[:200])
        rp = out / "result.json"
        check("缺输入: result.json 已写", rp.is_file())
        if rp.is_file():
            res = json.loads(rp.read_text(encoding="utf-8"))
            check("缺输入: status=input_error", res.get("status") == "input_error")
            check("缺输入: case_id=unknown-case", res.get("case_id") == "unknown-case")


def main():
    print(f"selftest @ {PKG_ROOT}")
    test_frontmatter()
    test_syntax()
    test_scenarios()
    print()
    print(f"结果: {len(_passes)} 通过, {len(_failures)} 失败")
    if _failures:
        print("失败项:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
