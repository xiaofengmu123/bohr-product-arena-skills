#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立自检脚本：验证 Skill 在空输入、坏 JSON、缺输入、样本案例、契约回填下的鲁棒性。

不依赖网络与检索工具，只在临时目录内用 main.py 的确定性命令跑完整性检查。
退出码：0 = 全部通过；1 = 存在失败。
"""
import json
import os
import sys
import tempfile
import traceback

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import main as main_mod
import core_impl as core


def run_cli(args):
    try:
        return main_mod.main(args)
    except SystemExit as e:
        return e.code if e.code is not None else 0


def write_case(root, case_obj, task_text="", task_name="task.md"):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case_obj, f, ensure_ascii=False, indent=2)
    with open(os.path.join(root, task_name), "w", encoding="utf-8") as f:
        f.write(task_text)


def check_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    assert os.path.isfile(path), "result.json 不存在"
    r = json.load(open(path, encoding="utf-8"))
    assert r.get("case_id"), "case_id 缺失"
    assert r.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"), \
        "status 非法: " + str(r.get("status"))
    for key in ("sources", "uncertainty", "pending_human", "change_log", "results", "evidence"):
        assert key in r, "缺字段: " + key
    return r


def run():
    checks = []
    note = lambda name: checks.append([name, None])

    def case(name, fn):
        try:
            fn()
            checks.append([name, True])
        except Exception:
            checks.append([name, False])
            traceback.print_exc()

    base = tempfile.mkdtemp(prefix="skill_selftest_")

    def shell(fn):
        w = tempfile.mkdtemp(prefix="work_", dir=base)
        o = tempfile.mkdtemp(prefix="out_", dir=base)
        return w, o

    # 1. --help 退出码 0
    def help_ok():
        code = run_cli(["--help"])
        assert code == 0, "help 退出码非 0: %d" % code
    case("--help 退出码 0", help_ok)

    # 2. 空输入目录
    def empty_input():
        inp = tempfile.mkdtemp(prefix="in_", dir=base)
        w, o = shell("empty")
        code = run_cli(["run", "--input", inp, "--output", o, "--work", w])
        assert code == 0, "run 退出码非 0: %d" % code
        r = check_result(o)
        assert r["status"] == "insufficient_evidence", "空输入应 insufficient_evidence，得 " + r["status"]
    case("空输入目录不崩溃且 insufficient_evidence", empty_input)

    # 3. 坏 JSON
    def bad_json():
        inp = tempfile.mkdtemp(prefix="in_", dir=base)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ bad json !!!")
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# 坏 JSON 容错测试")
        w, o = shell("badjson")
        code = run_cli(["run", "--input", inp, "--output", o, "--work", w])
        assert code == 0, "run 退出码非 0: %d" % code
        r = check_result(o)
        assert r["status"] in ("partial", "input_error", "insufficient_evidence"), \
            "坏 JSON 应降级，得 " + r["status"]
        assert r.get("degradations"), "坏 JSON 应写入 degradations"
    case("坏 JSON 降级不崩溃", bad_json)

    # 4. 样本案例 + import + evidence + assemble + 契约回填
    def sample_case():
        inp = tempfile.mkdtemp(prefix="in_", dir=base)
        w, o = shell("sample")
        contract = {"required_papers": 5, "tags": ["foo"], "extra": None}
        case_obj = {
            "case_id": "case-selftest-001",
            "research_question": "可解释机器学习在医学影像中的应用",
            "output_contract": contract,
        }
        write_case(inp, case_obj, "# 案例说明\n研究问题：可解释机器学习在医学影像中的应用\n")
        assert run_cli(["run", "--input", inp, "--output", o, "--work", w]) == 0
        # 更新计划
        plan_payload = json.dumps({
            "aspects": [
                {"id": "a1", "label": "方法(XAI)"},
                {"id": "a2", "label": "对象(医学影像)"},
            ],
            "queries": [
                {"id": "s1", "text": "explainable AI medical imaging",
                 "source": "paper_search", "database": "crossref",
                 "aspect_id": "a1", "rationale": "核心方法", "status": "planned"},
            ],
        })
        assert run_cli(["plan-update", "--inline", plan_payload, "--work", w]) == 0
        # 导入候选 + 证据
        cand = {"records": [{
            "title": "Explainable Deep Learning for Medical Image Analysis",
            "authors": ["Doe, J.", "Smith, A."], "year": "2023",
            "journal": "IEEE Trans. Med. Imaging", "doi": "10.1000/example.2023",
            "url": "https://doi.org/10.1000/example.2023", "database": "crossref",
            "relevance": 0.92, "relevance_reason": "直接讨论医学影像可解释方法",
            "aspects": ["a1", "a2"], "query_ids": ["s1"],
            "evidence": [{"locator": "abstract", "quote": "we propose an explainable ..."}],
        }]}
        candf = os.path.join(w, "cand.json")
        with open(candf, "w", encoding="utf-8") as f:
            json.dump(cand, f, ensure_ascii=False)
        assert run_cli(["import", "--file", candf, "--work", w]) == 0
        ev = json.dumps({"query": "explainable AI medical imaging", "source": "paper_search",
                         "database": "crossref", "aspect_id": "a1", "rationale": "核心方法",
                         "id": "s1", "hits_found": 12, "hits_kept": 1,
                         "result_ids": ["10.1000/example.2023"]})
        assert run_cli(["evidence", "--inline", ev, "--work", w]) == 0
        assert run_cli(["assemble", "--input", inp, "--output", o, "--work", w]) == 0
        sc = run_cli(["selfcheck", "--input", inp, "--output", o, "--work", w])
        assert sc == 0, "selfcheck 应通过，退出码 %d" % sc
        r = check_result(o)
        # 契约回填
        for k in ("required_papers", "tags", "extra"):
            assert k in r, "契约键缺失: " + k
        assert r["results"][0]["score_components"], "score_components 缺失"
        assert r["coverage_matrix"], "coverage_matrix 缺失"
    case("样本案例全流程 + 契约回填 + selfcheck", sample_case)

    # 5. 重复运行一致性
    def determinism():
        inp = tempfile.mkdtemp(prefix="in_", dir=base)
        w1, o1 = shell("det1")
        w2, o2 = shell("det2")
        case_obj = {"case_id": "case-det", "research_question": "测试问题"}
        write_case(inp, case_obj, "# 测试")
        run_cli(["run", "--input", inp, "--output", o1, "--work", w1])
        run_cli(["run", "--input", inp, "--output", o2, "--work", w2])
        r1 = json.load(open(os.path.join(o1, "result.json"), encoding="utf-8"))
        r2 = json.load(open(os.path.join(o2, "result.json"), encoding="utf-8"))
        # 去掉时间戳类字段后再比较
        for r in (r1, r2):
            r.pop("runtime", None)
            r.pop("change_log", None)
        assert json.dumps(r1, sort_keys=True, ensure_ascii=False) == \
               json.dumps(r2, sort_keys=True, ensure_ascii=False), "两次运行结果不一致"
    case("重复运行结果一致", determinism)

    passed = sum(1 for _, v in checks if v is True)
    total = len(checks)
    print("selftest: %d/%d passed" % (passed, total))
    for name, v in checks:
        print("[%s] %s" % ("PASS" if v is True else "FAIL", name))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())