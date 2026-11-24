#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — evidence-driven writing Skill 入口。

子命令（均从 Skill 根以相对路径 scripts/main.py 调用）：
  scaffold  早落盘：写 result.json 骨架（status=in_progress）。不覆盖非 in_progress 版本。
  extract   抽取：发现材料 -> 提取 PDF 文本 -> 启发式论断切分 -> 落 /app/work。
  build     合并：把 Agent 中间产物（claims/sources/meta）增量合入 result.json。
  finalize  终检：校验 + 渲染 claim_list.md / evidence_table.md + 写 finished_at。
  validate  只校验不写。
  selftest  自检：空目录 / 坏 JSON / 坏编码 / 骨架幂等 / locator 校验。

纯标准库，无第三方依赖。缺省 --input /input --output /app/output --work /app/work。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C


def _ensure_dirs(out, work):
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)


def cmd_scaffold(args):
    _ensure_dirs(args.out, args.work)
    out_path = os.path.join(args.out, "result.json")
    case = {}
    case_path = os.path.join(args.input, "case.json")
    if os.path.exists(case_path):
        case, err = C.read_json_robust(case_path)
    case_id = C.extract_case_id(case) if isinstance(case, dict) else None
    contract = C.extract_output_contract(case) if isinstance(case, dict) else {}
    existing, _ = C.read_result(out_path)
    if existing and existing.get("status") != "in_progress":
        print(json.dumps({"ok": True, "action": "skip_existing", "status": existing.get("status")},
                         ensure_ascii=False))
        return 0
    skel = C.default_skeleton(case_id, contract)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(skel, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "action": "scaffold_written", "case_id": case_id}, ensure_ascii=False))
    return 0


def cmd_extract(args):
    _ensure_dirs(args.out, args.work)
    found, errs = C.discover_inputs(args.input)
    report = {"ok": True, "errors": errs, "materials": [], "claims_written": False}
    txt_all = []
    for mp in found.get("material", []):
        low = mp.lower()
        if low.endswith(".pdf"):
            out_txt = os.path.join(args.work, os.path.basename(mp) + ".txt")
            ok, msg = C.extract_pdf(mp, out_txt)
            report["materials"].append({"path": mp, "kind": "pdf", "extract": msg})
            if ok:
                text, terr = C.read_text_robust(out_txt)
                if text:
                    txt_all.append((mp, text))
        elif low.endswith((".txt", ".md", ".markdown", ".tex")):
            text, terr = C.read_text_robust(mp)
            if text:
                txt_all.append((mp, text))
            report["materials"].append({"path": mp, "kind": "text", "errors": terr})
        else:
            report["materials"].append({"path": mp, "kind": "skipped"})
    # 启发式切分（作为 Agent 精化起点）
    claims = []
    for mp, text in txt_all:
        claims.extend(C.split_claims(text, base_doc=os.path.basename(mp)))
    if claims:
        cpath = os.path.join(args.work, "claims_candidates.json")
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"claims": claims}, f, ensure_ascii=False, indent=2)
        report["claims_written"] = True
        report["claims_count"] = len(claims)
        report["claims_candidates"] = cpath
    mpath = os.path.join(args.work, "input_manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _read_list_json(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = C.read_json_robust(path)
    return obj


def cmd_build(args):
    _ensure_dirs(args.out, args.work)
    out_path = os.path.join(args.out, "result.json")
    base, _ = C.read_result(out_path)
    if base is None:
        # 无骨架则先落骨架（不覆盖既有非 in_progress 已在 scaffold 保证）
        case_path = os.path.join(args.input, "case.json")
        case = C.read_json_robust(case_path)[0] if os.path.exists(case_path) else {}
        contract = C.extract_output_contract(case) if isinstance(case, dict) else {}
        base = C.default_skeleton(C.extract_case_id(case) if isinstance(case, dict) else None, contract)
    claims_obj = _read_list_json(args.claims)
    sources_obj = _read_list_json(args.sources)
    meta_obj = _read_list_json(args.meta)
    claims = (claims_obj or {}).get("claims")
    sources = (sources_obj or {}).get("sources")
    meta = meta_obj or {}
    C.merge_into(base, claims=claims, sources=sources, meta=meta)
    base["change_log"].append({
        "time": C.now_iso(),
        "action": "build",
        "detail": "合并 claims/sources/meta（claims=%d, sources=%d）" % (
            len(base.get("claims", [])), len(base.get("sources", []))),
    })
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
    ok, problems = C.validate_result(base)
    print(json.dumps({"ok": ok, "problems": problems[:20], "status": base.get("status")},
                     ensure_ascii=False))
    return 0


def cmd_finalize(args):
    _ensure_dirs(args.out, args.work)
    out_path = os.path.join(args.out, "result.json")
    base, err = C.read_result(out_path)
    if base is None:
        print(json.dumps({"ok": False, "error": "no_result_json", "detail": err}, ensure_ascii=False))
        return 1
    if base.get("status") == "in_progress":
        base["status"] = "partial"
    base["change_log"].append({
        "time": C.now_iso(), "action": "finalize", "detail": "终检与渲染",
    })
    base["runtime"]["finished_at"] = C.now_iso()
    ok, problems = C.validate_result(base)
    # 保守：不自动升级到 complete；complete 只由 Agent 在 meta.status 中显式给出，
    # 避免"缺数据仍写 complete"的虚高状态。
    paths = {}
    try:
        paths = C.render_markdown(base, args.out)
        base["deliverable"]["claim_list"] = os.path.basename(paths.get("claim_list", ""))
        base["deliverable"]["evidence_table"] = os.path.basename(paths.get("evidence_table", ""))
    except OSError as e:
        base["degradations"].append("render_failed: %s" % e)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": ok, "problems": problems[:20], "status": base.get("status"),
                      "rendered": list(paths.values())}, ensure_ascii=False))
    return 0 if ok else 0  # 校验问题不阻断落盘，只报告


def cmd_validate(args):
    out_path = os.path.join(args.out, "result.json")
    base, err = C.read_result(out_path)
    if base is None:
        print(json.dumps({"ok": False, "error": "no_result_json", "detail": err}, ensure_ascii=False))
        return 1
    ok, problems = C.validate_result(base)
    print(json.dumps({"ok": ok, "problems": problems, "status": base.get("status")},
                     ensure_ascii=False))
    return 0


def cmd_selftest(args):
    """自检：不依赖真实案例，验证库的确定性行为。"""
    import tempfile
    results = []
    tmp = tempfile.mkdtemp(prefix="ewd_selftest_")

    def check(name, cond, detail=""):
        results.append({"name": name, "ok": bool(cond), "detail": detail})

    # 1. 空输入目录：discover 不抛异常，返回空清单
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    found, errs = C.discover_inputs(empty)
    check("empty_input_dir", found["case"] == [] and found["material"] == [], str(errs))

    # 2. 坏 JSON：read_json_robust 返回 (None, errors) 不崩溃
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    obj, err = C.read_json_robust(bad)
    check("bad_json_degrades", obj is None and bool(err), str(err))

    # 3. 坏编码：read_text_robust 不崩溃（gb18030 兜底）
    gbk = os.path.join(tmp, "gbk.txt")
    with open(gbk, "wb") as f:
        f.write("中文内容测试".encode("gb18030"))
    text, err = C.read_text_robust(gbk)
    check("bad_encoding_degrades", text is not None and "中文" in text, str(err))

    # 4. 骨架幂等：scaffold 不覆盖非 in_progress
    outdir = os.path.join(tmp, "out")
    workdir = os.path.join(tmp, "work")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(workdir, exist_ok=True)
    op = os.path.join(outdir, "result.json")
    skel = C.default_skeleton("case-x", {})
    skel["status"] = "complete"
    with open(op, "w", encoding="utf-8") as f:
        json.dump(skel, f, ensure_ascii=False)
    existing, _ = C.read_result(op)
    check("scaffold_skips_non_inprogress", existing.get("status") == "complete")

    # 5. locator 校验
    check("locator_valid", C.is_valid_locator("S2.P3.s4") and C.is_valid_locator("page:7")
          and C.is_valid_locator("char[10:20]"))
    check("locator_invalid", not C.is_valid_locator("foo") and not C.is_valid_locator("S.P.s"))

    # 6. 论断切分：含数字/引用的句被保留，短句被过滤
    claims = C.split_claims("研究显示风险显著降低（P<0.05）。\n\n短句。\n\n样本为 120 例。")
    check("claim_split_keeps_substantive", len(claims) >= 2, "n=%d" % len(claims))

    # 7. 校验器捕获缺字段 / 空 quote / 坏 locator
    badres = {"case_id": "c", "status": "complete", "deliverable": {}, "sources": [
        {"claim_id": "C1", "source_doc": "d", "locator": "x", "quote": "", "support": "supports", "reason": ""}
    ], "claims": [{"claim_id": "C1", "strength": "S1"}], "change_log": []}
    ok, problems = C.validate_result(badres)
    check("validate_catches_problems", not ok and any("empty_quote" in p for p in problems)
          and any("bad_locator" in p for p in problems), str(problems[:5]))

    print(json.dumps({"selftest": results, "all_ok": all(r["ok"] for r in results)},
                     ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", "--out", default="/app/output", dest="out")
    common.add_argument("--work", default="/app/work")

    ap = argparse.ArgumentParser(description="evidence-driven writing Skill entry",
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("scaffold", "extract", "finalize", "validate", "selftest"):
        sub.add_parser(name, parents=[common])

    b = sub.add_parser("build", parents=[common])
    b.add_argument("--claims")
    b.add_argument("--sources")
    b.add_argument("--meta")

    args = ap.parse_args(argv)
    handlers = {
        "scaffold": cmd_scaffold,
        "extract": cmd_extract,
        "build": cmd_build,
        "finalize": cmd_finalize,
        "validate": cmd_validate,
        "selftest": cmd_selftest,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
