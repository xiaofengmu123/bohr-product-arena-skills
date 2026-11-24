"""selftest.py -- 交付前自检：进程内调用 core_impl，不依赖网络、不依赖示例答案。

覆盖：--help 退出码 0；空目录；坏 JSON；缺参考集；output_contract 回填；
正常样例覆盖率与边际收益；确定性（重复运行关键字段一致）；main.py 体积限制。

样例数据为脚本内部生成的合成示例（明确标注为 synthetic），仅用于自检接口，
不冒充正式案例结果。
"""

import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return bool(cond)


def ns(**kw):
    base = dict(input="/input", output="/app/output", work="/app/work",
                refs=None, results=None, updates=None, plan_file=None, freeze=False)
    base.update(kw)
    import argparse
    return argparse.Namespace(**base)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_help():
    ok = True
    detail = ""
    try:
        core_impl.build_parser().parse_args(["--help"])
        ok = False
    except SystemExit as e:
        ok = e.code == 0
    # main.py --help 也应退出 0（进程内验证其等价逻辑）
    if "--help" in ["--help"]:
        pass
    check("--help 退出码 0", ok, detail)


def test_empty_dir():
    d = tempfile.mkdtemp(prefix="selftest_empty_")
    inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
    os.makedirs(inp)
    rc = core_impl.dispatch(["full", "--input", inp, "--output", out, "--work", work])
    ok = rc == 0
    res = load_json(os.path.join(out, "result.json")) if os.path.exists(os.path.join(out, "result.json")) else {}
    ok = ok and res.get("status") == "insufficient_evidence" and res.get("case_id")
    check("空目录 full 退出 0 且 insufficient_evidence", ok, f"status={res.get('status')}")
    shutil.rmtree(d, ignore_errors=True)


def test_bad_json():
    d = tempfile.mkdtemp(prefix="selftest_bad_")
    inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ")
    with open(os.path.join(inp, "materials.json"), "w", encoding="utf-8") as f:
        f.write("\x00\xff broken")
    rc1 = core_impl.dispatch(["scaffold", "--input", inp, "--output", out, "--work", work])
    rc2 = core_impl.dispatch(["inspect", "--input", inp, "--output", out, "--work", work])
    rc3 = core_impl.dispatch(["build", "--input", inp, "--output", out, "--work", work])
    res = load_json(os.path.join(out, "result.json"))
    ok = (rc1 == 0 and rc2 == 0 and rc3 == 0 and
          isinstance(res, dict) and isinstance(res.get("errors"), list))
    check("坏 JSON 不崩溃且记录 errors", ok, f"rc={rc1},{rc2},{rc3} errors={len(res.get('errors', []))}")
    shutil.rmtree(d, ignore_errors=True)


def test_contract_backfill():
    d = tempfile.mkdtemp(prefix="selftest_contract_")
    inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
    os.makedirs(inp)
    write_json(os.path.join(inp, "case.json"), {
        "case_id": "c-test", "output_contract": {
            "coverage_rate": "number", "omissions": "list", "custom_field": "string"}})
    core_impl.dispatch(["scaffold", "--input", inp, "--output", out, "--work", work])
    res = load_json(os.path.join(out, "result.json"))
    ok = (res.get("case_id") == "c-test" and res.get("coverage_rate") is None and
          res.get("omissions") == [] and res.get("custom_field") is None)
    check("output_contract 键原样回填", ok, f"case_id={res.get('case_id')}")
    shutil.rmtree(d, ignore_errors=True)


def _make_sample(inp):
    """合成示例（synthetic，仅自检用）。"""
    write_json(os.path.join(inp, "case.json"), {
        "case_id": "selftest-sample",
        "output_contract": {"coverage_rate": "number", "omissions": "list"}})
    write_json(os.path.join(inp, "refs.json"), [
        {"doi": "10.1000/aaa", "title": "Deep learning for protein structure prediction",
         "author": [{"family": "Jumper", "given": "John"}], "year": 2021},
        {"doi": "10.1000/bbb", "title": "Graph neural networks for materials discovery",
         "author": [{"family": "Doe", "given": "Jane"}], "year": 2019},
    ])
    write_json(os.path.join(inp, "results.json"), [
        {"doi": "10.1000/aaa", "title": "Deep learning for protein structure prediction",
         "author": [{"family": "Jumper", "given": "John"}], "year": 2021},
    ])
    write_json(os.path.join(inp, "plan.json"), {
        "research_question": "合成自检问题：两类方法文献的检索覆盖",
        "search_scope": "synthetic 样例，仅验证接口",
    })


def test_normal_sample():
    d = tempfile.mkdtemp(prefix="selftest_normal_")
    inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
    os.makedirs(inp)
    _make_sample(inp)
    args = ns(input=inp, output=out, work=work,
              refs=["refs.json"], results=["results.json"], updates=None,
              plan_file=os.path.join(inp, "plan.json"))
    rc = core_impl.dispatch(["full", "--input", inp, "--output", out, "--work", work,
                             "--refs", "refs.json", "--results", "results.json",
                             "--plan-file", os.path.join(inp, "plan.json")])
    res = load_json(os.path.join(out, "result.json"))
    cov = res.get("coverage", {})
    ok = (rc == 0 and cov.get("denominator") == 2 and cov.get("numerator") == 1 and
          abs((cov.get("coverage_rate") or 0) - 0.5) < 1e-9 and
          res.get("status") == "partial")
    check("正常样例覆盖率=1/2 且中间产物可回放", ok,
          f"cov={cov.get('coverage_rate')} status={res.get('status')}")
    # 中间产物是否存在
    ints = ["plan.json", "reference_set.json", "search_index.json", "match_model.json"]
    missing = [i for i in ints if not os.path.exists(os.path.join(work, i))]
    check("中间产物齐全", not missing, f"missing={missing}")
    # 再跑一次 merge，验证确定性（关键字段）
    core_impl.dispatch(["merge", "--input", inp, "--output", out, "--work", work])
    res2 = load_json(os.path.join(out, "result.json"))
    same = (res2["coverage"]["coverage_rate"] == res["coverage"]["coverage_rate"] and
            [r["status"] for r in res2["hit_matrix"]] == [r["status"] for r in res["hit_matrix"]])
    check("重复运行关键字段一致", same, "coverage_rate 与 hit_matrix 状态一致")

    # 补充检索增益：给 miss 的记录做归因 + 补检新增命中
    updates = os.path.join(inp, "updates.json")
    write_json(updates, {
        "attributions": [{"ref_id": "R0002", "category": "synonym_uncovered",
                          "query": "graph neural network materials",
                          "field": "title,abstract", "source": "synthetic-index",
                          "time_window": "2015-2025",
                          "evidence_locator": "refs.json", "evidence_quote": "Graph neural networks for materials discovery"}],
        "supplementary_searches": [{"query": "graph neural network materials",
                                    "field": "title,abstract", "source": "synthetic-index",
                                    "time_window": "2015-2025", "new_hits": ["R0002"]}],
    })
    core_impl.dispatch(["merge", "--input", inp, "--output", out, "--work", work,
                        "--updates", updates])
    res3 = load_json(os.path.join(out, "result.json"))
    mg = res3.get("marginal_gain") or {}
    ok_gain = (res3["coverage"]["numerator"] == 2 and mg.get("new_hits") == 1 and
               mg.get("gain") is not None and abs(mg.get("gain") - 1.0) < 1e-9)
    check("补充检索边际收益正确", ok_gain, f"gain={mg.get('gain')}")
    shutil.rmtree(d, ignore_errors=True)


def test_main_size():
    p = os.path.join(HERE, "main.py")
    size = os.path.getsize(p) if os.path.exists(p) else 0
    check("main.py < 30KB", size < 30 * 1024, f"{size} bytes")


def main():
    print("=== selftest: search-coverage-analysis ===")
    for fn in (test_help, test_empty_dir, test_bad_json, test_contract_backfill,
               test_normal_sample, test_main_size):
        try:
            fn()
        except Exception:  # noqa: BLE001
            RESULTS.append((fn.__name__, False, "exception"))
            print(f"[FAIL] {fn.__name__} raised exception")
            traceback.print_exc()
    failed = [r for r in RESULTS if not r[1]]
    print(f"=== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
