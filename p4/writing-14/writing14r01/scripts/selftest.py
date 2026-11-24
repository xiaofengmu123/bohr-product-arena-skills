#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在临时目录中端到端验证 main.py 的 init/check/render。

覆盖场景：
  1) 正常案例（含 output_contract、材料、周期约束）——init/check/render 全部通过，FAIL=0；
  2) 空输入（case.json 不存在）——init 仍落盘合法骨架，status=partial/failed，不崩溃；
  3) 坏 JSON——init 捕获并记录到 errors，退出码 0。

仅依赖标准库；可用 `python scripts/selftest.py` 在 Skill 根目录运行。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd):
    return subprocess.run(
        [sys.executable, MAIN] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_normal(tmp):
    case = os.path.join(tmp, "case.json")
    material = os.path.join(tmp, "materials", "paper.md")
    os.makedirs(os.path.dirname(material), exist_ok=True)
    with open(material, "w", encoding="utf-8") as f:
        f.write("# 前期数据\n\n已知：样本量为 120 例。\n")
    case_obj = {
        "case_id": "selftest-1",
        "output_contract": {
            "title": {"required": True},
            "summary": {"required": True},
            "seven_elements": {"required": True},
            "evidence_map": {"required": True},
            "artifacts": {"required": True},
        },
        "period_months": 24,
        "materials": [material],
    }
    with open(case, "w", encoding="utf-8") as f:
        json.dump(case_obj, f, ensure_ascii=False)

    out_dir = os.path.join(tmp, "out")
    work_dir = os.path.join(tmp, "work")
    os.makedirs(out_dir)
    os.makedirs(work_dir)
    result = os.path.join(out_dir, "result.json")

    r1 = run(["init", "--case", case, "--out", result, "--work", work_dir], tmp)
    assert r1.returncode == 0, "init 失败: " + r1.stderr
    skeleton = _load(result)
    assert skeleton.get("case_id") == "selftest-1"
    assert skeleton.get("status") in ("partial", "failed")
    assert "change_log" in skeleton and "uncertainty" in skeleton

    # 回填一条带证据的论断，再跑 check / render
    skeleton["title"] = "自检开题方案"
    skeleton["summary"] = "示意摘要，非正式案例结果。"
    skeleton["seven_elements"]["background"] = [
        {
            "claim_id": "bg-1",
            "text": "前期数据样本量为 120 例。",
            "locator": material + "#L3",
            "quote": "已知：样本量为 120 例。",
            "source_id": "S1",
            "kind": "fact",
            "verdict": "supported",
        }
    ]
    skeleton["sources"] = [
        {"source_id": "S1", "path": material, "locator": "L3", "quote": "已知：样本量为 120 例。"}
    ]
    with open(result, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False)

    r2 = run(["check", "--result", result, "--case", case], tmp)
    assert r2.returncode == 0, "check FAIL: " + r2.stdout + r2.stderr
    check_out = json.loads(r2.stdout)
    assert check_out.get("fail") == 0, "FAIL 应为 0: " + str(check_out)

    r3 = run(["render", "--result", result, "--out", out_dir, "--work", work_dir], tmp)
    assert r3.returncode == 0, "render 失败: " + r3.stderr
    assert os.path.exists(os.path.join(out_dir, "proposal.md"))
    assert os.path.exists(os.path.join(out_dir, "evidence_map.json"))
    print("  [ok] 正常案例 init/check/render 通过")


def test_empty_input(tmp):
    work = os.path.join(tmp, "empty")
    out_dir = os.path.join(work, "out")
    os.makedirs(out_dir, exist_ok=True)
    result = os.path.join(out_dir, "result.json")
    r = run(
        ["init", "--case", os.path.join(work, "missing.json"), "--out", result,
         "--work", os.path.join(work, "work")],
        tmp,
    )
    assert r.returncode == 0, "空输入不应崩溃"
    skeleton = _load(result)
    assert skeleton.get("status") in ("partial", "failed")
    assert "errors" in skeleton and "uncertainty" in skeleton
    print("  [ok] 空输入降级为 partial/failed 骨架")


def test_bad_json(tmp):
    work = os.path.join(tmp, "bad")
    bad = os.path.join(work, "bad.json")
    os.makedirs(work, exist_ok=True)
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    out_dir = os.path.join(work, "out")
    os.makedirs(out_dir, exist_ok=True)
    result = os.path.join(out_dir, "result.json")
    r = run(
        ["init", "--case", bad, "--out", result, "--work", os.path.join(tmp, "work")],
        tmp,
    )
    assert r.returncode == 0, "坏 JSON 不应崩溃"
    skeleton = _load(result)
    assert skeleton.get("errors"), "坏 JSON 应记录到 errors"
    print("  [ok] 坏 JSON 捕获并记录")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        test_normal(tmp)
        test_empty_input(tmp)
        test_bad_json(tmp)
        print("SELFTEST OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
