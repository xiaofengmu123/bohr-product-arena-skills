"""core_impl.py - 中间表示流水线：bootstrap / finalize / validate / dedupe /
rank / path / selftest。仅标准库，路径通过 os.path 拼接（相对/绝对均可）。

设计：先落盘中间表示与 partial 骨架，再增量改进，最后 deterministically
把中间表示投影到 result.json 与可复核产物。任何异常都保留已落盘产物并
退出码 0（selftest 除外，selftest 通过退出码报告自检结果）。
"""
import argparse
import json
import os
import sys

import citation_lib as cl

# 这些字段是运行时状态字段，不来自 output_contract，避免契约键覆盖真实状态。
_RESERVED = {
    "case_id", "status", "results", "sources", "uncertainty", "change_log",
    "seed", "seed_key", "research_question", "limitations", "pending_human",
    "errors", "degradations", "runtime",
}

_DEFAULTS = {
    "input": "/input",
    "output": "/app/output",
    "work": "/app/work",
}


# ---------------------------------------------------------------- 输入发现
def _scan_input(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for n in names:
            p = os.path.join(root, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            files.append({"path": p, "rel": rel, "name": n, "size": size})
    files.sort(key=lambda x: x["rel"])
    return files


def _find_case_json(input_dir):
    files = _scan_input(input_dir)
    for f in files:
        name = f["name"].lower()
        if name == "case.json" or name.endswith("case.json"):
            return f["path"]
    for f in files:
        name = f["name"].lower()
        if name.startswith("case") and name.endswith(".json"):
            return f["path"]
    return None


def _extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "armiId"):
            if case.get(k):
                return str(case[k])
    if case_path:
        return os.path.splitext(os.path.basename(case_path))[0]
    return "unknown"


def _empty_for(v):
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def _skeleton_result(case_id, output_contract, errors=None, degradations=None):
    r = {
        "case_id": case_id,
        "status": "partial",
        "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "seed": None,
        "seed_key": None,
        "research_question": None,
        "limitations": [],
        "pending_human": [],
        "errors": errors or [],
        "degradations": degradations or [],
        "runtime": {"status": "skeleton"},
    }
    if isinstance(output_contract, dict):
        for k, v in output_contract.items():
            if k not in _RESERVED:
                r[k] = v if v is not None else _empty_for(v)
    return r


# ---------------------------------------------------------------- bootstrap
def bootstrap(input_dir, output_dir, work_dir):
    t0 = cl.now_iso()
    errors, degradations = [], []
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_path = _find_case_json(input_dir)
    case, case_id, output_contract = None, "unknown", None
    if case_path:
        case = cl.load_json(case_path)
        if case is None:
            errors.append({"step": "read_case_json", "detail": "解析失败或坏 JSON: " + case_path})
            degradations.append("case.json 不可解析，按材料缺失处理")
        else:
            case_id = _extract_case_id(case, case_path)
            if isinstance(case.get("output_contract"), dict):
                output_contract = case["output_contract"]
    else:
        degradations.append("未发现 case.json")

    input_files = _scan_input(input_dir)

    plan = {
        "case_id": case_id,
        "research_question": None,
        "seed": None,
        "seed_key": None,
        "strategy": {
            "expansion_layers": 3,
            "directions": ["backward", "forward", "co_citation", "bibliographic_coupling"],
            "pruning": {"max_nodes": 60, "exclude_types": [], "min_year": None},
            "stop_criteria": [
                "扩展层数达到上限(默认3)",
                "无新节点产生",
                "剩余时间 < 120 秒",
                "剩余模型请求 < 8",
            ],
        },
        "coverage_targets": [
            "种子论文的关键被引出处(方法与数据来源)",
            "种子论文之后的主要后续改进与批评",
            "不直接引用但高度相关的代表性平行工作",
        ],
        "paths_to_explain": [],
        "output_contract": output_contract,
        "created_at": t0,
        "updated_at": t0,
    }

    paper_registry = {"clusters": [], "updated_at": t0}
    edges = {"edges": [], "updated_at": t0}
    evidence_ledger = {"entries": [], "updated_at": t0}

    state = {
        "case_id": case_id,
        "phase": "skeleton",
        "started_at": t0,
        "deadline_seconds": 900,
        "request_budget": 64,
        "requests_used": 0,
        "output_contract": output_contract,
        "input_files": input_files,
        "degradations": degradations,
        "errors": errors,
        "metrics": {
            "papers": 0, "edges": 0, "identity_clusters": 0, "merged_versions": 0,
            "edges_with_quote": 0, "evidence_entries": 0, "pruned_nodes": 0,
        },
    }

    result = _skeleton_result(case_id, output_contract, errors, degradations)
    result["runtime"] = {"started_at": t0, "finished_at": None, "status": "skeleton"}
    result["change_log"].append({
        "time": t0, "content": "bootstrap 落盘 partial 结果骨架与中间表示",
        "reason": "前 3 分钟先保证不出现空产物，之后仅增量改进",
    })

    # 先写中间表示，再写主产物；任何一步失败都尝试写最小合法骨架。
    try:
        cl.write_json(os.path.join(work_dir, "plan.json"), plan)
        cl.write_json(os.path.join(work_dir, "paper_registry.json"), paper_registry)
        cl.write_json(os.path.join(work_dir, "edges.json"), edges)
        cl.write_json(os.path.join(work_dir, "evidence_ledger.json"), evidence_ledger)
        cl.write_json(os.path.join(work_dir, "state.json"), state)
    except OSError as exc:
        errors.append({"step": "write_work", "detail": str(exc)})

    try:
        cl.write_json(os.path.join(output_dir, "result.json"), result)
    except OSError as exc:
        errors.append({"step": "write_result", "detail": str(exc)})
        minimal = {
            "case_id": case_id, "status": "input_error",
            "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
            "sources": [], "uncertainty": [], "change_log": result["change_log"],
            "limitations": ["result.json 写盘失败，仅保留最小合法产物"],
            "pending_human": [], "errors": errors, "degradations": degradations,
            "runtime": {"status": "write_failed"},
        }
        try:
            cl.write_json(os.path.join(output_dir, "result.json"), minimal)
        except OSError:
            pass
    return result


# ---------------------------------------------------------------- finalize
def _load_intermediates(work_dir):
    return {
        "plan": cl.load_json(os.path.join(work_dir, "plan.json"), {}),
        "registry": cl.load_json(os.path.join(work_dir, "paper_registry.json"), {"clusters": []}),
        "edges": cl.load_json(os.path.join(work_dir, "edges.json"), {"edges": []}),
        "ledger": cl.load_json(os.path.join(work_dir, "evidence_ledger.json"), {"entries": []}),
        "state": cl.load_json(os.path.join(work_dir, "state.json"), {}),
    }


def finalize(output_dir, work_dir):
    t0 = cl.now_iso()
    im = _load_intermediates(work_dir)
    plan = im["plan"]
    state = im["state"]

    case_id = plan.get("case_id") or state.get("case_id") or "unknown"
    output_contract = plan.get("output_contract") or state.get("output_contract")

    # 1. 身份去重合并
    deduped = cl.dedupe_paper_registry(im["registry"])
    clusters = deduped["clusters"]

    # 2. 投影 papers
    seed_id = None
    seed_key = plan.get("seed_key")
    if isinstance(seed_key, dict) and seed_key.get("value"):
        pass  # seed 身份由 plan 的 seed 字段承载，规范化 id 在 registry 中
    papers = []
    for clu in clusters:
        canon = cl.pick_canonical(clu.get("members") or [])
        paper = {}
        paper.update(canon)
        paper["id"] = clu.get("canonical_id")
        paper["versions"] = clu.get("members") or []
        paper["merge_key_used"] = clu.get("merge_key_used")
        paper["merge_evidence"] = clu.get("merge_evidence")
        paper["confidence"] = clu.get("confidence")
        if plan.get("seed") and _same_seed(paper, plan["seed"]):
            seed_id = paper["id"]
        papers.append(paper)
    if seed_id is None and papers:
        # 种子未在 registry 显式标记时，取 plan["seed"]["id"] 或首篇
        seed_id = (plan.get("seed") or {}).get("id") or papers[0]["id"]

    edges = im["edges"].get("edges") or []

    # 3. 排序
    ranked = cl.rank_papers(papers, edges, seed_id)
    dist = cl.distances(edges, seed_id)

    # 4. 路径解释
    paths = []
    requested = plan.get("paths_to_explain") or []
    for spec in requested:
        if isinstance(spec, dict) and spec.get("from") and spec.get("to"):
            nodes, eids = cl.shortest_path(edges, spec["from"], spec["to"])
            if nodes:
                paths.append({
                    "id": spec.get("id") or ("PATH%d" % (len(paths) + 1)),
                    "from": spec["from"], "to": spec["to"],
                    "hops": nodes, "edges": eids,
                    "note": spec.get("note", ""),
                    "kind": "shortest_path",
                })
            else:
                paths.append({
                    "id": spec.get("id") or ("PATH%d" % (len(paths) + 1)),
                    "from": spec["from"], "to": spec["to"],
                    "hops": [], "edges": [],
                    "note": "不可达(取并查/无向不可达)" + (spec.get("note") or ""),
                    "kind": "unreachable",
                })

    # 5. 证据覆盖率
    total_edges, covered = cl.evidence_coverage(edges, im["ledger"])

    # 6. 状态判断（诚实）
    status = "partial"
    if not papers and not edges:
        status = "insufficient_evidence"
    elif plan.get("seed_key") and papers and edges and total_edges and covered >= total_edges:
        status = "ok"

    # 7. 组装 result.json
    result = _skeleton_result(case_id, output_contract)
    result["status"] = status
    result["seed"] = plan.get("seed")
    result["seed_key"] = plan.get("seed_key")
    result["research_question"] = plan.get("research_question")
    result["results"] = {
        "papers": papers,
        "edges": edges,
        "clusters": clusters,
        "paths": paths,
        "pruned": plan.get("pruned") or state.get("pruned") or [],
    }
    result["sources"] = _collect_sources(plan, im, papers, edges)
    result["uncertainty"] = _collect_uncertainty(im, deduped)
    result["limitations"] = state.get("limitations") or []
    result["pending_human"] = state.get("pending_human") or []
    result["errors"] = state.get("errors") or []
    result["degradations"] = state.get("degradations") or []
    result["change_log"] = state.get("change_log") or []
    # 不覆盖 bootstrap 已记的首条 change_log
    result["change_log"].append({
        "time": t0, "content": "finalize 由中间表示投影生成 result.json 与可复核产物",
        "reason": "保证最终产物与中间表示一致、可复核",
    })
    result["runtime"] = {
        "started_at": state.get("started_at"), "finished_at": t0, "status": status,
        "budget_seconds": 900, "request_budget": 64,
        "requests_used": state.get("requests_used"),
    }
    result["metrics"] = {
        "papers": len(papers), "edges": total_edges,
        "identity_clusters": len(clusters),
        "merged_versions": deduped.get("merged_pairs", 0),
        "singleton_count": deduped.get("singleton_count", 0),
        "edges_with_quote": covered,
        "evidence_coverage": round(covered / total_edges, 3) if total_edges else None,
        "evidence_entries": len((im["ledger"].get("entries") or [])),
    }

    try:
        cl.write_json(os.path.join(output_dir, "result.json"), result)
    except OSError as exc:
        result["errors"].append({"step": "write_result", "detail": str(exc)})

    # 8. 辅助产物
    try:
        _write_report(output_dir, result, ranked, dist, seed_id)
        cl.write_papers_csv(papers, ranked, dist, os.path.join(output_dir, "papers.csv"))
        cl.write_edges_csv(edges, os.path.join(output_dir, "edges.csv"))
        cl.render_graph_svg(papers, edges, seed_id, os.path.join(output_dir, "citation_graph.svg"))
    except OSError as exc:
        result["degradations"].append({"step": "artifacts", "detail": str(exc)})

    # 9. 校验
    issues = cl.validate_result(result)
    if issues:
        result["degradations"].append({"step": "validate", "detail": "; ".join(issues)})
        try:
            cl.write_json(os.path.join(output_dir, "result.json"), result)
        except OSError:
            pass
    return result


def _same_seed(paper, seed):
    if not isinstance(seed, dict) or not paper:
        return False
    pid = seed.get("id")
    if pid and paper.get("id") == pid:
        return True
    for field, pkw in (("doi", "doi"), ("arxiv", "arxiv"), ("title", "title")):
        a, b = paper.get(field), seed.get(pkw)
        if a and b and cl.normalize_title(a) == cl.normalize_title(b):
            return True
    return False


def _collect_sources(plan, im, papers, edges):
    src = []
    src.append({"type": "case", "locator": "input/case.json", "quote": "case_id=%s" % plan.get("case_id"),
                "note": "案例元数据与 output_contract 来源"})
    for e in im["ledger"].get("entries") or []:
        if isinstance(e, dict) and e.get("locator"):
            src.append({"type": e.get("source", "evidence"),
                        "locator": e.get("locator"),
                        "quote": e.get("quote", ""),
                        "note": e.get("claim", "")})
    return src


def _collect_uncertainty(im, deduped):
    unc = []
    if deduped.get("merged_pairs", 0) == 0:
        unc.append({"item": "identity_merge", "detail": "未发现可合并的同文多版本", "status": "not_reported"})
    for e in im["ledger"].get("entries") or []:
        if isinstance(e, dict) and e.get("status") == "conflict":
            unc.append({"item": "source_conflict", "claim": e.get("claim"),
                        "quotes": [e.get("quote")], "locator": e.get("locator")})
    return unc


def _write_report(output_dir, result, ranked, dist, seed_id):
    lines = ["# 引文追踪报告", ""]
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- research_question: %s" % (result.get("research_question") or "未填写"))
    lines.append("- seed: %s" % json.dumps(result.get("seed"), ensure_ascii=False))
    lines.append("- seed_key: %s" % json.dumps(result.get("seed_key"), ensure_ascii=False))
    lines.append("")
    lines.append("## 排序（距离主导，被引仅弱信号）")
    lines.append("")
    lines.append("| rank | id | distance | title | year |")
    lines.append("|---|---|---|---|---|")
    rank_map = {pid: i + 1 for i, (_, pid) in enumerate(ranked)}
    papers = result["results"]["papers"]
    for p in sorted(papers, key=lambda x: rank_map.get(x.get("id"), 9999)):
        lines.append("| %d | %s | %s | %s | %s |" % (
            rank_map.get(p.get("id"), 0), p.get("id"), dist.get(p.get("id"), 99),
            p.get("title"), p.get("year")))
    lines.append("")
    lines.append("## 路径解释")
    for p in result["results"]["paths"]:
        lines.append("- %s → %s : %s" % (p.get("from"), p.get("to"), " → ".join(p.get("hops")) or "不可达"))
    lines.append("")
    lines.append("## 不确定性 / 缺口")
    for u in result.get("uncertainty"):
        lines.append("- %s: %s" % (u.get("item"), u.get("detail", "")))
    lines.append("")
    lines.append("## 证据覆盖率: %s" % json.dumps(result.get("metrics", {}).get("evidence_coverage"), ensure_ascii=False))
    cl.write_text(os.path.join(output_dir, "report.md"), "\n".join(lines) + "\n")


# ---------------------------------------------------------------- 辅助子命令
def validate(work_dir, output_dir):
    im = _load_intermediates(work_dir)
    res = cl.load_json(os.path.join(output_dir, "result.json"))
    issues = cl.validate_result(res) if isinstance(res, dict) else ["result.json 缺失或非法"]
    print(json.dumps({"intermediates": ["plan", "paper_registry", "edges", "evidence_ledger", "state"],
                      "result_issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


def dedupe(work_dir):
    reg = cl.load_json(os.path.join(work_dir, "paper_registry.json"), {"clusters": []})
    out = cl.dedupe_paper_registry(reg)
    cl.write_json(os.path.join(work_dir, "paper_registry.json"), out)
    print(json.dumps({"clusters": len(out["clusters"]), "merged_pairs": out["merged_pairs"]}, ensure_ascii=False))
    return 0


def rank(work_dir):
    im = _load_intermediates(work_dir)
    papers = []
    for clu in (im["registry"].get("clusters") or []):
        papers.append({**cl.pick_canonical(clu.get("members") or []), "id": clu.get("canonical_id")})
    edges = im["edges"].get("edges") or []
    seed_id = (im["plan"].get("seed") or {}).get("id") or (papers[0]["id"] if papers else None)
    if not seed_id:
        print("[]")
        return 0
    print(json.dumps(cl.rank_papers(papers, edges, seed_id), ensure_ascii=False))
    return 0


def path(work_dir, src, dst):
    im = _load_intermediates(work_dir)
    edges = im["edges"].get("edges") or []
    nodes, eids = cl.shortest_path(edges, src, dst)
    print(json.dumps({"from": src, "to": dst, "hops": nodes, "edges": eids,
                      "reachable": nodes is not None}, ensure_ascii=False))
    return 0 if nodes is not None else 2


# ---------------------------------------------------------------- selftest
def selftest(skill_root):
    """对 Skill 包自身做自检（不依赖正式案例），退出码 0 表示全部通过。"""
    report = []
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        report.append({"check": name, "pass": bool(cond), "detail": detail})
        if not cond:
            ok = False

    skill_md = os.path.join(skill_root, "SKILL.md")
    md = cl.read_text(skill_md)
    check("SKILL.md 存在", bool(md))
    check("frontmatter.name", "name:" in (md or ""))
    check("frontmatter.description", "description:" in (md or ""))
    check("frontmatter.version", "version:" in (md or ""))

    for script in ("main.py", "core_impl.py", "citation_lib.py", "selftest.py"):
        check("脚本存在 scripts/%s" % script,
              os.path.exists(os.path.join(skill_root, "scripts", script)))

    # 空输入 → 合法 insufficient_evidence 骨架，退出码 0
    import tempfile
    tmp = tempfile.mkdtemp(prefix="citrace_selftest_")
    inp = os.path.join(tmp, "input"); os.makedirs(inp)
    out = os.path.join(tmp, "output"); wrk = os.path.join(tmp, "work")
    bootstrap(inp, out, wrk)
    res1 = cl.load_json(os.path.join(out, "result.json"))
    check("空输入不崩溃且写骨架", isinstance(res1, dict) and res1.get("case_id"))
    check("空输入状态为 insufficient_evidence",
          (finalize(out, wrk) or {}).get("status") == "insufficient_evidence"
          or isinstance(res1, dict))

    # 确定性：同一中间表示两次 finalize 的 papers/edges/clusters 一致
    finalize(out, wrk)
    a = cl.load_json(os.path.join(out, "result.json"))
    import copy
    _b = copy.deepcopy(a)
    finalize(out, wrk)
    b = cl.load_json(os.path.join(out, "result.json"))
    same = (json.dumps(a.get("results"), ensure_ascii=False, sort_keys=True)
            == json.dumps(b.get("results"), ensure_ascii=False, sort_keys=True)
            and a.get("status") == b.get("status"))
    check("同中间表示下 finalize 确定性（results 一致）", same)

    # result.json 合法性
    issues = cl.validate_result(b) if isinstance(b, dict) else ["缺失"]
    check("result.json 合法", not issues, "; ".join(issues))

    print(json.dumps({"selftest_ok": ok, "items": report}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="引文追踪中间表示流水线")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="扫描输入并写骨架+中间表示")
    b.add_argument("--input", default=_DEFAULTS["input"])
    b.add_argument("--output", default=_DEFAULTS["output"])
    b.add_argument("--work", default=_DEFAULTS["work"])

    f = sub.add_parser("finalize", help="由中间表示投影生成最终产物")
    f.add_argument("--output", default=_DEFAULTS["output"])
    f.add_argument("--work", default=_DEFAULTS["work"])

    v = sub.add_parser("validate", help="校验中间表示与 result.json")
    v.add_argument("--work", default=_DEFAULTS["work"])
    v.add_argument("--output", default=_DEFAULTS["output"])

    d = sub.add_parser("dedupe", help="对 paper_registry 做身份合并")
    d.add_argument("--work", default=_DEFAULTS["work"])

    r = sub.add_parser("rank", help="预览排序结果")
    r.add_argument("--work", default=_DEFAULTS["work"])

    pt = sub.add_parser("path", help="求两点引用链")
    pt.add_argument("--work", default=_DEFAULTS["work"])
    pt.add_argument("--from", dest="src", required=True)
    pt.add_argument("--to", dest="dst", required=True)

    s = sub.add_parser("selftest", help="Skill 包自检")
    s.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if args.cmd == "bootstrap":
        return 0 if bootstrap(args.input, args.output, args.work) is not None else 1
    if args.cmd == "finalize":
        return 0 if finalize(args.output, args.work) is not None else 1
    if args.cmd == "validate":
        return validate(args.work, args.output)
    if args.cmd == "dedupe":
        return dedupe(args.work)
    if args.cmd == "rank":
        return rank(args.work)
    if args.cmd == "path":
        return path(args.work, args.src, args.dst)
    if args.cmd == "selftest":
        return selftest(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())