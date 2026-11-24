# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在临时目录里跑三种场景并断言不变量。

运行：python3 scripts/selftest.py
仅依赖标准库；不写正式测试案例，只构造最小合成输入用于验证契约。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402
import main as main_mod  # noqa: E402

_FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  [ok] " + name)
    else:
        print("  [FAIL] " + name + (" " + detail if detail else ""))
        _FAIL.append(name)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def scenario_empty(tmp):
    d = os.path.join(tmp, "empty")
    os.makedirs(d, exist_ok=True)
    rc = main_mod.main(["discover", "--input", d, "--work", os.path.join(tmp, "w0"), "--output", os.path.join(tmp, "o0")])
    check("empty-input exits 0", rc == 0)
    r = core.load_json_file(os.path.join(tmp, "o0", "result.json"))[0]
    check("empty-input status insufficient_evidence", bool(r) and r.get("status") == "insufficient_evidence")
    check("empty-input result legal", bool(r) and r.get("case_id") is None and isinstance(r.get("change_log"), list))


def scenario_badjson(tmp):
    d = os.path.join(tmp, "badjson")
    _write(os.path.join(d, "case.json"), "{not valid json")
    rc = main_mod.main(["discover", "--input", d, "--work", os.path.join(tmp, "w1"), "--output", os.path.join(tmp, "o1")])
    check("bad-json exits 0", rc == 0)
    r = core.load_json_file(os.path.join(tmp, "o1", "result.json"))[0]
    check("bad-json recorded as degradation/status", bool(r) and r.get("status") in ("insufficient_evidence", "partial"))
    check("bad-json errors/degradations nonempty", bool(r) and (r.get("errors") or r.get("degradations")))


def scenario_normal(tmp):
    d = os.path.join(tmp, "normal")
    case = {
        "case_id": "selftest-case-01",
        "output_contract": {
            "candidate_papers": [],
            "ranking_and_filtering": [],
            "evidence_records": [],
            "coverage_gaps": [],
            "fetched_metric": None,
        },
        "seed": {"doi": "10.1000/selftest.1", "title": "Seed Method Paper", "first_author": "Zhang", "year": 2020},
    }
    _write(os.path.join(d, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(d, "task.md"), "# 自检任务\n追踪种子论文。\n")
    _write(os.path.join(d, "materials", "refs.json"), json.dumps({"references": [{"title": "Backward Base"}], "citations": [{"title": "Forward Followup"}]}, ensure_ascii=False))

    w = os.path.join(tmp, "w2")
    o = os.path.join(tmp, "o2")
    rc = main_mod.main(["discover", "--input", d, "--work", w, "--output", o])
    check("normal discover exits 0", rc == 0)
    r = core.load_json_file(os.path.join(o, "result.json"))[0]
    check("contract fields echoed verbatim", bool(r) and set(case["output_contract"]).issubset(set(r.keys())))
    check("contract blank list filled []", isinstance(r.get("candidate_papers"), list) and r.get("fetched_metric") is None)
    check("case_id echoed", r.get("case_id") == "selftest-case-01")

    # 用 merge 增量注入 papers + edges + sources
    patch = {
        "seed": {"id": "doi:10.1000/selftest.1", "title": "Seed Method Paper"},
        "seed_key": "doi:10.1000/selftest.1",
        "results": {
            "papers": [
                {"id": "doi:10.1000/selftest.1", "title": "Seed Method Paper", "doi": "10.1000/selftest.1", "first_author": "Zhang", "year": 2020, "kind": "preprint"},
                {"id": "raw:seed-journal", "title": "Seed Method Paper (journal version)", "doi": "10.1000/selftest.1", "first_author": "Zhang", "year": 2021, "kind": "journal"},
                {"id": "title:backwardbase:2015", "title": "Backward Base", "year": 2015},
                {"id": "title:forwardfollowup:2022", "title": "Forward Followup", "year": 2022},
            ],
            "edges": [
                {"source": "doi:10.1000/selftest.1", "target": "title:backwardbase:2015", "relation": "cites", "evidence": {"locator": "materials/refs.json#references/0", "quote": "Backward Base"}},
                {"source": "title:forwardfollowup:2022", "target": "doi:10.1000/selftest.1", "relation": "cites", "evidence": {"locator": "materials/refs.json#citations/0", "quote": "Forward Followup"}},
                {"source": "raw:seed-journal", "target": "title:backwardbase:2015", "relation": "cites", "evidence": {"locator": "materials/refs.json#references/0", "quote": "Backward Base"}},
            ],
        },
        "sources": [{"locator": "materials/refs.json", "quote": "{\"references\":[...]}"}],
    }
    _write(os.path.join(tmp, "patch.json"), json.dumps(patch, ensure_ascii=False))
    rc = main_mod.main(["merge", "--work", w, "--patch", os.path.join(tmp, "patch.json"), "--reason", "自检注入"])
    check("merge exits 0", rc == 0)
    ir = core.load_ir(w)
    check("merge appended change_log", len(ir.get("change_log", [])) == 2)

    # dedupe
    papers = (ir.get("results") or {}).get("papers") or []
    merged, evidence = core.dedupe_papers(papers)
    check("dedupe merges DOI duplicates", len(merged) == 3, "got %d" % len(merged))
    check("dedupe records evidence", len(evidence) == 1)

    # dedupe --apply：写回并重映射边
    rc = main_mod.main(["dedupe", "--work", w, "--apply"])
    check("dedupe --apply exits 0", rc == 0)
    ir = core.load_ir(w)
    papers_after = (ir.get("results") or {}).get("papers") or []
    edges_after = (ir.get("results") or {}).get("edges") or []
    check("dedupe --apply canonical count", len(papers_after) == 3)
    check("dedupe --apply remapped edges", all(e.get("source") != "raw:seed-journal" for e in edges_after))
    merged_seed = next((p for p in papers_after if p.get("id") == "doi:10.1000/selftest.1"), None)
    check("dedupe --apply kept versions", bool(merged_seed) and len(merged_seed.get("versions", [])) == 2)

    # path
    path = core.shortest_path(edges_after, "title:forwardfollowup:2022", "title:backwardbase:2015")
    check("shortest_path found", path is not None and path["length"] == 2)

    # rank
    ranked = core.rank_papers(list(papers_after), edges_after, "doi:10.1000/selftest.1")
    check("rank assigns distance/score", all("distance" in p and "score" in p for p in ranked))

    # finalize
    rc = main_mod.main(["finalize", "--work", w, "--output", o])
    check("finalize exits 0", rc == 0)
    for f in ("result.json", "report.md", "papers.csv", "edges.csv", "citation_graph.svg"):
        check("artifact %s exists" % f, os.path.isfile(os.path.join(o, f)))
    # 校验应干净（dedupe 后无重复 id）
    clean = core.validate_ir(ir, "full")
    check("post-dedupe validation clean", len(clean) == 0, "; ".join(clean))
    # SVG well-formed
    svg = core.read_text_robust(os.path.join(o, "citation_graph.svg"))
    try:
        ET.fromstring(svg)
        check("SVG well-formed XML", True)
    except Exception as exc:  # noqa: BLE001
        check("SVG well-formed XML", False, str(exc))

    # 确定性：finalize 两次产物一致
    import hashlib
    h1 = hashlib.sha256(open(os.path.join(o, "result.json"), "rb").read()).hexdigest()
    rc = main_mod.main(["finalize", "--work", w, "--output", o])
    h2 = hashlib.sha256(open(os.path.join(o, "result.json"), "rb").read()).hexdigest()
    check("determinism: repeat run identical", h1 == h2)


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        print("== scenario: empty input ==")
        scenario_empty(tmp)
        print("== scenario: bad json ==")
        scenario_badjson(tmp)
        print("== scenario: normal pipeline ==")
        scenario_normal(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if _FAIL:
        print("\nSELFTEST FAILED: %d check(s)" % len(_FAIL))
        print("  - " + "\n  - ".join(_FAIL))
        return 1
    print("\nSELFTEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())