#!/usr/bin/env python3
"""离线自检：在临时目录内跑 scaffold/validate/evidence，验证脚本可运行、契约正确、证据逐字可核验。"""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*argv):
    return subprocess.run([sys.executable, MAIN] + list(argv),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    tmp = tempfile.mkdtemp(prefix="talk-skill-selftest-")
    case_path = os.path.join(tmp, "case.json")
    task_path = os.path.join(tmp, "task.md")
    mat_dir = os.path.join(tmp, "materials")
    os.makedirs(mat_dir, exist_ok=True)
    paper_path = os.path.join(mat_dir, "paper.md")
    out = os.path.join(tmp, "output")
    os.makedirs(out, exist_ok=True)

    contract = {
        "report_goal": "让评审认可方法新颖性",
        "audience": "评审委员",
        "pages": [],
        "evidence_map": [],
    }
    with open(case_path, "w", encoding="utf-8") as f:
        json.dump({"id": "case-selftest", "output_contract": contract}, f, ensure_ascii=False)
    with open(task_path, "w", encoding="utf-8") as f:
        f.write("# 自检任务\n")
    with open(paper_path, "w", encoding="utf-8") as f:
        f.write("# Paper\n\nX remains open.\n")
    sources_path = os.path.join(tmp, "sources.json")
    with open(sources_path, "w", encoding="utf-8") as f:
        json.dump([{"path": paper_path, "type": "markdown", "read": True}], f, ensure_ascii=False)

    # 1) 落盘骨架
    r = run("scaffold", "--case", case_path, "--task", task_path, "--out", out)
    assert r.returncode == 0, r.stderr
    result_path = os.path.join(out, "result.json")
    result = json.load(open(result_path, encoding="utf-8"))
    assert result["case_id"] == "case-selftest"
    assert result["status"] == "partial"
    assert "report_goal" in result and "evidence_map" in result

    # 2) 模拟 Agent 增量工作：填充一页 + 证据映射
    result["pages"] = [{
        "page_index": 1, "title": "问题", "single_claim": "X 是未解难题",
        "evidence_strength": "high", "figures": ["fig1"], "speaker_notes": "先讲背景，再引出 X。",
        "source_refs": [{"locator": "materials/paper.md#p1", "quote": "X remains open."}],
    }]
    result["evidence_map"] = [{"claim": "X 是未解难题", "locator": "materials/paper.md#p1",
                               "quote": "X remains open.", "strength": "high"}]
    json.dump(result, open(result_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 3) 增量合并不丢字段
    r = run("scaffold", "--update", "--case", case_path, "--out", out)
    assert r.returncode == 0, r.stderr
    merged = json.load(open(result_path, encoding="utf-8"))
    assert len(merged["pages"]) == 1, "--update 不应覆盖已有 pages"
    assert merged["report_goal"] == "让评审认可方法新颖性"

    # 4) 契约与证据校验通过
    r = run("validate", "--result", result_path, "--case", case_path)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run("evidence", "--result", result_path)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run("evidence", "--result", result_path, "--sources", sources_path)
    assert r.returncode == 0, r.stdout + r.stderr

    # 5) 反向用例：空证据与改写 quote 均应报错
    bad = copy.deepcopy(result)
    bad["pages"][0]["source_refs"] = []
    bad_path = os.path.join(out, "bad.json")
    json.dump(bad, open(bad_path, "w", encoding="utf-8"), ensure_ascii=False)
    r = run("evidence", "--result", bad_path)
    assert r.returncode == 1, "缺 source_refs 应报错"

    bad2 = copy.deepcopy(result)
    bad2["evidence_map"][0]["quote"] = "Y has been solved."
    bad2_path = os.path.join(out, "bad2.json")
    json.dump(bad2, open(bad2_path, "w", encoding="utf-8"), ensure_ascii=False)
    r = run("evidence", "--result", bad2_path, "--sources", sources_path)
    assert r.returncode == 1, "改写 quote 应被逐字核验拦下"

    print("selftest ok: %s" % tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
