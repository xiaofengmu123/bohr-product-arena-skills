#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：验证本 Skill 包在交付前满足全部硬约束与评分维度对齐要求。

只依赖标准库。运行方式（在包根目录）：

    python3 -B scripts/selftest.py

覆盖：
  1. CLI 可执行性（--help 退出码 0；缺参数默认指向 /input、/app/output、/app/work）
  2. 运行时健壮性：空目录 / 坏 JSON / 坏编码 / 缺 case.json 均退出码 0 且写合法 result.json
  3. output_contract 全字段回填（叶子/数组/嵌套对象）
  4. 确定性：同输入两次运行字节一致（SHA-256）
  5. validate 能捕获坏 result.json（退出码 1）
  6. SKILL.md 结构：frontmatter(name/description/version)、六大必备节、预算与骨架纪律
  7. 评分维度对齐：SKILL.md 自检清单逐条标注评分维度，且五维度齐全
  8. 文件规模约束：≤14 文件、≤1.2MB、SKILL.md ≤180 行、main.py <30KB

退出码：0 = 全部通过；1 = 有失败项。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 包根目录（outputs/）
MAIN = os.path.join(HERE, "main.py")
SKILL = os.path.join(ROOT, "SKILL.md")

DIMENSIONS = {
    "实际效果": ("E", "实际效果"),
    "使用增益": ("U", "使用增益"),
    "创新增益": ("I", "创新增益"),
    "可靠体验": ("R", "可靠体验"),
    "生态贡献": ("M", "生态贡献"),
}

REQUIRED_SECTIONS = [
    "角色与何时使用",
    "输入契约",
    "输出契约",
    "执行流程",
    "自检清单",
    "降级策略",
]


def run(cmd, cwd=ROOT):
    """运行子命令，返回 CompletedProcess。"""
    return subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )


def main():
    failures = []

    def check(name, cond, detail=""):
        tag = "PASS" if cond else "FAIL"
        print(f"[{tag}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    # 1. CLI
    p = run([sys.executable, "-B", MAIN, "help"])
    check("cli_help_exit0", p.returncode == 0, p.stdout.decode("utf-8", "replace")[-200:])

    with tempfile.TemporaryDirectory() as td:
        def fresh(tag):
            base = os.path.join(td, tag)
            in_dir = os.path.join(base, "in")
            work = os.path.join(base, "work")
            out = os.path.join(base, "out")
            os.makedirs(in_dir)
            return in_dir, work, out

        # 2a. 空目录：scan → skeleton → validate 均退出 0，status=insufficient_evidence
        in_dir, work, out = fresh("empty")
        p = run([sys.executable, "-B", MAIN, "scan", "--input", in_dir, "--work", work])
        check("empty_scan_exit0", p.returncode == 0)
        p = run([sys.executable, "-B", MAIN, "skeleton", "--work", work, "--output", out])
        check("empty_skeleton_exit0", p.returncode == 0)
        p = run([sys.executable, "-B", MAIN, "validate", "--work", work, "--output", out])
        check("empty_validate_exit0", p.returncode == 0, p.stdout.decode("utf-8", "replace")[-200:])
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            r = json.load(fh)
        check("empty_status_insufficient", r.get("status") == "insufficient_evidence")

        # 2b. 坏 JSON：继续处理，status=input_error 且 errors 记录
        in_dir, work, out = fresh("badjson")
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        p = run([sys.executable, "-B", MAIN, "scan", "--input", in_dir, "--work", work])
        check("badjson_scan_exit0", p.returncode == 0)
        p = run([sys.executable, "-B", MAIN, "skeleton", "--work", work, "--output", out])
        check("badjson_skeleton_exit0", p.returncode == 0)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            r = json.load(fh)
        check("badjson_status_input_error", r.get("status") == "input_error")
        check("badjson_errors_recorded", len(r.get("errors") or []) >= 1)

        # 2c. 正常 case.json + output_contract：合同字段全回填
        in_dir, work, out = fresh("normal")
        case = {
            "case_id": "demo-selftest",
            "output_contract": {
                "introduction": "string",
                "related_work": "string",
                "references": {"type": "array"},
                "metadata": {"authors": "string", "venue": "string"},
            },
        }
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(case, ensure_ascii=False))
        p = run([sys.executable, "-B", MAIN, "scan", "--input", in_dir, "--work", work])
        check("normal_scan_exit0", p.returncode == 0)
        with open(os.path.join(work, "case_normalized.json"), encoding="utf-8") as fh:
            norm = json.load(fh)
        check("contract_fields_discovered", set(norm["contract_fields"]) == {
            "introduction", "related_work", "references", "metadata"})
        p = run([sys.executable, "-B", MAIN, "skeleton", "--work", work, "--output", out])
        check("normal_skeleton_exit0", p.returncode == 0)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            r = json.load(fh)
        check("case_id_passthrough", r.get("case_id") == "demo-selftest")
        check("contract_leaf_null", r.get("introduction") is None)
        check("contract_array_empty", r.get("references") == [])
        check("contract_nested_structure", r.get("metadata") == {"authors": None, "venue": None})
        check("standard_fields_present", all(k in r for k in (
            "status", "sources", "uncertainty", "pending_human", "change_log",
            "artifacts", "errors", "degradations", "runtime")))

        # 2d. 确定性：重建两次字节一致（复用 normal 的 work）
        out_path = os.path.join(out, "result.json")
        os.remove(out_path)
        run([sys.executable, "-B", MAIN, "skeleton", "--work", work, "--output", out])
        with open(out_path, "rb") as fh:
            b1 = fh.read()
        os.remove(out_path)
        run([sys.executable, "-B", MAIN, "skeleton", "--work", work, "--output", out])
        with open(out_path, "rb") as fh:
            b2 = fh.read()
        check("determinism_sha256", hashlib.sha256(b1).hexdigest() == hashlib.sha256(b2).hexdigest())

        # 2e. validate 捕获坏 result.json（退出码 1）
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("{}")
        p = run([sys.executable, "-B", MAIN, "validate", "--work", work, "--output", out])
        check("validate_catches_broken", p.returncode == 1)

        # 2f. 缺参数默认值（help 文本声明 /input、/app/output）
        p = run([sys.executable, "-B", MAIN, "help"])
        check("default_args_documented", b"/input" in p.stdout and b"/app/output" in p.stdout)

    # 3. SKILL.md 结构
    if not os.path.isfile(SKILL):
        check("skill_md_exists", False, "SKILL.md missing")
    else:
        with open(SKILL, encoding="utf-8") as fh:
            text = fh.read()
        lines = text.splitlines()
        fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        check("frontmatter_present", fm is not None)
        if fm:
            fm_text = fm.group(1)
            for key in ("name", "description", "version"):
                check(f"frontmatter_{key}", re.search(rf"^{key}\s*:", fm_text, re.MULTILINE) is not None)
        for sec in REQUIRED_SECTIONS:
            check(f"section_{sec}", any(sec in ln for ln in lines))
        check("skill_lines_le180", len(lines) <= 180, f"{len(lines)} lines")
        check("budget_900", "900" in text and "64" in text)
        check("skeleton_3min", "3" in text and ("骨架" in text or "partial" in text))
        check("status_field_documented", "status" in text and "change_log" in text)

        # 4. 评分维度对齐
        missing_dim = [d for d in DIMENSIONS if d not in text]
        check("five_dimensions_named", not missing_dim, f"missing: {missing_dim}")
        tags = re.findall(r"\[([EUI R M]\d+)\]", text)
        check("dimension_tags_present", len(tags) >= 5, f"found {len(tags)} tags")

    # 5. 文件规模约束
    total = 0
    nfiles = 0
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            total += os.path.getsize(fp)
            nfiles += 1
    check("file_count_le14", nfiles <= 14, f"{nfiles} files")
    check("total_bytes_le12mb", total <= 1.2 * 1024 * 1024, f"{total} bytes")
    if os.path.isfile(MAIN):
        check("main_py_lt30kb", os.path.getsize(MAIN) < 30 * 1024,
              f"{os.path.getsize(MAIN)} bytes")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print(f"all passed ({nfiles} files, {total} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
