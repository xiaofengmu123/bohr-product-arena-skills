#!/usr/bin/env python3
"""selftest.py —— 本 Skill 的自检脚本（仅标准库）。

运行内容（全部使用临时目录，示例数据显式标注「示例」）：
  1) main.py --help 退出码 0；
  2) 完整管线 discover -> screen -> check 在示例输入上产出合法 result.json；
  3) 确定性：discover / screen 对同一输入两次运行逐字节一致；
  4) 空输入目录：status=insufficient_evidence 且退出码 0；
  5) 坏 JSON：降级记录 degradations，退出码 0。

用法: python3 -B scripts/selftest.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def demo_case():
    """示例输入（仅自检用，非正式案例）。"""
    return {
        "case_id": "example_case",
        "research_question": "mRNA vaccine immunogenicity in older adults",
        "inclusion_criteria": [
            "研究人群为老年人（>=65 岁）",
            "干预为 mRNA 疫苗",
            "结局为免疫原性指标",
        ],
        "exclusion_criteria": ["与疫苗免疫原性无关的研究设计"],
        "output_contract": {
            "screened_papers": {"type": "array"},
            "report": {"type": "string"},
            "meta": {"type": "object"},
        },
        "papers": [
            {
                "title": "mRNA vaccine immunogenicity in older adults: a phase 3 trial",
                "authors": ["Smith J", "Lee K"],
                "year": 2024,
                "venue": "New England Journal of Medicine",
                "doi": "10.1000/example.2024.001",
                "url": "https://example.org/nejm/001",
                "abstract": "This phase 3 trial measured immunogenicity of an mRNA vaccine in adults aged 65 and older.",
            },
            {
                "title": "mRNA vaccine immunogenicity in older adults: a phase 3 trial",
                "authors": ["Smith J", "Lee K"],
                "year": 2023,
                "venue": "arXiv preprint",
                "doi": None,
                "url": "https://arxiv.org/abs/2301.00001",
                "abstract": "Preprint reporting immunogenicity of an mRNA vaccine in older adults.",
            },
            {
                "title": "Deep learning for land cover segmentation in satellite imagery",
                "authors": ["Wang M"],
                "year": 2022,
                "venue": "Remote Sensing",
                "doi": "10.1000/rs.2022.999",
                "url": "https://example.org/rs/999",
                "abstract": "A segmentation network for satellite land cover mapping.",
            },
        ],
    }


def synthetic_fill(result_path, norm):
    """把示例候选填成示例结果（显式标注示例；证据为逐字摘要片段）。"""
    result = json.load(open(result_path, encoding="utf-8"))
    cands = norm["candidates"]
    out = []
    for i, c in enumerate(cands):
        if i == 0:
            rel, tier, score = "direct", "retain", 0.95
            reasons = ["研究对象、干预与结局均匹配纳入标准（示例）"]
            conflict, dedupe = None, None
            conf = "high"
            matched = ["population:older adults", "intervention:mRNA vaccine"]
        elif i == 1:
            rel, tier, score = "direct", "exclude", 0.90
            reasons = ["与 rec_0000 为同一研究预印本，已并入正式版本（示例）"]
            conflict = None
            dedupe = {"group": "dg_0000", "role": "preprint", "canonical_record_id": "rec_0000"}
            conf = "high"
            matched = ["population:older adults"]
        else:
            rel, tier, score = "irrelevant", "exclude", 0.05
            reasons = ["研究主题与疫苗免疫原性无关（示例）"]
            conflict, dedupe = None, None
            conf = "high"
            matched = []
        out.append({
            "record_id": c["record_id"],
            "title": c["title"],
            "authors": c["authors"],
            "year": c["year"],
            "venue": c["venue"],
            "doi": c["doi"],
            "url": c["url"],
            "source_type": c["source_type"],
            "relevance": rel,
            "tier": tier,
            "priority_score": score,
            "rank": i + 1,
            "reasons": reasons,
            "evidence": [{"locator": f"case.json#papers[{i}].abstract",
                          "quote": c["abstract"]}],
            "confidence": conf,
            "criteria_matched": matched,
            "conflict": conflict,
            "dedupe": dedupe,
        })
    result["results"] = out
    result["status"] = "ok"
    json.dump(result, open(result_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    open(result_path, "a", encoding="utf-8").write("\n")


def test_pipeline(tmp):
    inp = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(inp, exist_ok=True)
    json.dump(demo_case(), open(os.path.join(inp, "case.json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=2)

    r1 = run([sys.executable, "-B", MAIN, "discover",
              "--input", inp, "--work", work, "--output", out])
    assert r1.returncode == 0, r1.stderr
    result_path = os.path.join(out, "result.json")
    skeleton = json.load(open(result_path, encoding="utf-8"))
    assert skeleton["status"] == "partial"
    for k in ("screened_papers", "report", "meta"):
        assert k in skeleton, f"output_contract 字段 {k} 未回填"

    r2 = run([sys.executable, "-B", MAIN, "screen",
              "--work", work, "--output", out])
    assert r2.returncode == 0, r2.stderr
    scaffold = json.load(open(os.path.join(work, "scaffold.json"), encoding="utf-8"))
    assert scaffold["dedupe_groups"], "应识别出重复组"

    norm = json.load(open(os.path.join(work, "case_normalized.json"), encoding="utf-8"))
    synthetic_fill(result_path, norm)

    r3 = run([sys.executable, "-B", MAIN, "check",
              "--input", inp, "--work", work, "--output", out])
    assert r3.returncode == 0, r3.stdout + r3.stderr
    final = json.load(open(result_path, encoding="utf-8"))
    assert final["status"] == "ok"
    assert final["results"][0]["rank"] == 1
    print("  pipeline PASS (status=ok, errors=0)")


def test_determinism(tmp):
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    json.dump(demo_case(), open(os.path.join(inp, "case.json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=2)
    for tag in ("a", "b"):
        work = os.path.join(tmp, f"work_{tag}")
        out = os.path.join(tmp, f"output_{tag}")
        run([sys.executable, "-B", MAIN, "discover",
             "--input", inp, "--work", work, "--output", out])
        run([sys.executable, "-B", MAIN, "screen",
             "--work", work, "--output", out])
    ha = sha(os.path.join(tmp, "work_a", "case_normalized.json"))
    hb = sha(os.path.join(tmp, "work_b", "case_normalized.json"))
    assert ha == hb, "discover 非确定性"
    sa = sha(os.path.join(tmp, "work_a", "scaffold.json"))
    sb = sha(os.path.join(tmp, "work_b", "scaffold.json"))
    assert sa == sb, "screen 非确定性"
    print("  determinism PASS (两次运行逐字节一致)")


def test_empty_input(tmp):
    inp = os.path.join(tmp, "empty_input")
    work = os.path.join(tmp, "work_empty")
    out = os.path.join(tmp, "output_empty")
    os.makedirs(inp, exist_ok=True)
    r = run([sys.executable, "-B", MAIN, "discover",
             "--input", inp, "--work", work, "--output", out])
    assert r.returncode == 0, r.stderr
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert res["status"] == "insufficient_evidence"
    assert res["omissions"], "空输入应写明遗漏"
    print("  empty-input PASS (insufficient_evidence, exit 0)")


def test_bad_json(tmp):
    inp = os.path.join(tmp, "bad_input")
    work = os.path.join(tmp, "work_bad")
    out = os.path.join(tmp, "output_bad")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("not a valid json {")
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\nchallenge_id: demo-bad\n")
    r = run([sys.executable, "-B", MAIN, "discover",
             "--input", inp, "--work", work, "--output", out])
    assert r.returncode == 0, r.stderr
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert res["status"] == "insufficient_evidence"
    assert any("bad_json" in d or "无法解析" in d for d in res.get("degradations", [])), \
        "坏 JSON 应记入 degradations"
    print("  bad-json PASS (降级 + exit 0)")


def main():
    r = run([sys.executable, "-B", MAIN, "--help"])
    assert r.returncode == 0, "main.py --help 失败"
    print("help PASS")
    with tempfile.TemporaryDirectory() as tmp:
        test_pipeline(tmp)
        test_determinism(tmp)
        test_empty_input(tmp)
        test_bad_json(tmp)
    print("ALL PASS")


if __name__ == "__main__":
    main()
