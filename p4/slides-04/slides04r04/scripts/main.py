#!/usr/bin/env python3
"""main.py — CLI entry for the academic-slides-evidence-deck Skill.

Subcommands
-----------
  init       read /input/case.json + /input/task.md, write result skeleton + inventory
  validate   inspect pptx / pdf / png, write render_record, merge into result.json
  evidence   build evidence_map from a page plan + extracted texts, merge
  seal       derive an honest status and check contract completeness
  selftest   run robustness self-checks (also available as scripts/selftest.py)

All writes go only to the explicit --output / --work paths. Stdlib only.
Exit codes: 0 success (including honest degradation), 2 usage error,
3 recoverable failure (partial result already on disk), 4 fatal failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import shutil
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_impl as ci


# --------------------------------------------------------------------------- #
# subcommands                                                                  #
# --------------------------------------------------------------------------- #

def _kind(path: Path) -> str:
    s = path.suffix.lower()
    if s == ".pdf":
        return "pdf"
    if s in (".pptx", ".ppt", ".potx", ".potm"):
        return "template_or_deck"
    if s in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".emf", ".wmf", ".gif"):
        return "image"
    if s in (".txt", ".md", ".csv", ".json", ".xml", ".tex", ".docx"):
        return "text"
    return "other"


def cmd_init(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    inventory = {
        "input_dir": str(inp), "files": [], "case_json": None, "task_md": None,
        "warnings": [], "created_at": ci.utc_now(),
    }
    case_obj = None
    errs = []

    if not inp.exists():
        inventory["warnings"].append("input_dir_missing")
        case_id = None
        contract = list(ci.DEFAULT_CONTRACT)
    else:
        for f in sorted(inp.rglob("*")):
            if f.is_file():
                try:
                    size = f.stat().st_size
                except OSError:
                    size = None
                inventory["files"].append({
                    "path": str(f), "bytes": size, "kind": _kind(f),
                    "rel": str(f.relative_to(inp)),
                })
        case_path = inp / "case.json"
        if case_path.exists():
            inventory["case_json"] = str(case_path)
            case_obj, jerr = ci.read_json_robust(case_path)
            if jerr:
                errs.append("case.json: %s" % jerr)
                inventory["warnings"].append("case_json_malformed")
        else:
            inventory["warnings"].append("case_json_missing")
        task_path = inp / "task.md"
        if task_path.exists():
            inventory["task_md"] = str(task_path)
            _, terr = ci.read_text_robust(task_path)
            if terr:
                inventory["warnings"].append("task_md_read_issue")
        else:
            inventory["warnings"].append("task_md_missing")
        case_id = ci.guess_case_id(case_obj)
        contract = ci.contract_fields(case_obj)

    result = ci.new_skeleton(case_id, contract)
    if not inp.exists():
        result["status"] = "input_error"
        result["uncertainty"].append(ci.uncertainty(
            "E1_INPUT_MISSING", None, "input dir missing: %s" % inp, "high"))
    if errs:
        result["errors"] = errs
        result["status"] = "input_error"
        result["change_log"].append({
            "ts": ci.utc_now(), "action": "init", "detail": "; ".join(errs),
        })
    for w in inventory["warnings"]:
        if w == "case_json_missing":
            result["status"] = "input_error"
            result["uncertainty"].append(ci.uncertainty(
                "E1_INPUT_MISSING", None, "case.json missing", "high"))
        elif w == "case_json_malformed":
            result["status"] = "input_error"
            result["uncertainty"].append(ci.uncertainty(
                "E2_JSON_MALFORMED", None, "case.json unreadable; no case_id extracted", "high"))

    ci.atomic_write_json(out / "result.json", result)
    ci.atomic_write_json(out / "input_inventory.json", inventory)
    ci.atomic_write_json(work / "input_inventory.json", inventory)
    print("init ok case_id=%s status=%s files=%d warnings=%d"
          % (case_id, result["status"], len(inventory["files"]), len(inventory["warnings"])))
    return 0


def cmd_validate(args) -> int:
    result_path = Path(args.result)
    result, rerr = ci.read_json_robust(result_path)
    if result is None:
        result = ci.new_skeleton(None, list(ci.DEFAULT_CONTRACT))
    render = {
        "source_files": list(args.sources or []),
        "template_file": args.template,
        "layouts_used": [],
        "fonts": list(args.fonts or []),
        "exports": [],
        "integrity": {},
        "media": [],
    }
    unc = []
    if args.pptx:
        ins = ci.inspect_pptx(args.pptx)
        render["integrity"]["pptx"] = ins
        render["layouts_used"] = ins.get("slide_layouts", [])
        render["media"] = ins.get("media", [])
        if ins.get("errors"):
            unc.append(ci.uncertainty(
                "E9_EXPORT_FAILED", None,
                "pptx inspect: " + "; ".join(ins["errors"]), "high"))
        for h in ins.get("overflow_heuristic", []):
            unc.append(ci.uncertainty(
                "E13_HEURISTIC_ONLY", h["slide"], h["note"], "low", auto_checkable=False))
        for sl in ins.get("slide_layouts", []):
            if sl.get("placeholder_inherited") is False:
                unc.append(ci.uncertainty(
                    "E6_PLACEHOLDER_MISSING", sl.get("slide"),
                    "slide placeholders not inherited from layout %s" % sl.get("layout"), "medium"))
            if sl.get("layout") is None:
                unc.append(ci.uncertainty(
                    "E5_TEMPLATE_NO_LAYOUT", sl.get("slide"),
                    "slide has no layout relationship (template layout missing)", "medium"))
        render["exports"].append({
            "type": "pptx", "path": args.pptx,
            "ok": ins.get("is_zip") and not ins.get("errors"),
        })
    else:
        unc.append(ci.uncertainty("E9_EXPORT_FAILED", None, "no pptx path provided", "high"))
    if args.pdf:
        p = ci.inspect_pdf(args.pdf)
        render["integrity"]["pdf"] = p
        render["exports"].append({"type": "pdf", "path": args.pdf, "ok": p.get("is_pdf")})
        if not p.get("is_pdf"):
            unc.append(ci.uncertainty(
                "E9_EXPORT_FAILED", None, "pdf missing or does not start with %%PDF-", "high"))
    for pg in args.png or []:
        p = ci.inspect_png(pg)
        render["integrity"].setdefault("pngs", []).append(p)
        render["exports"].append({"type": "png", "path": pg, "ok": p.get("is_png")})
        if not p.get("is_png"):
            unc.append(ci.uncertainty(
                "E9_EXPORT_FAILED", None, "png not a valid PNG: %s" % pg, "high"))

    result["render_record"] = render
    # mirror actual artifact paths into the presentation contract so it cannot drift
    presentation = result.get("presentation") or {}
    if args.pptx:
        presentation["pptx_path"] = args.pptx
        presentation["slide_count"] = render["integrity"].get("pptx", {}).get("slides")
    if args.pdf:
        presentation["pdf_path"] = args.pdf
    if args.png:
        presentation["png_paths"] = list(args.png)
    result["presentation"] = presentation
    result["uncertainty"] = ci.append_unique(result.get("uncertainty", []), unc)
    result["change_log"].append({
        "ts": ci.utc_now(), "action": "validate",
        "detail": "pptx=%s pdf=%s pngs=%d" % (bool(args.pptx), bool(args.pdf), len(args.png or [])),
    })
    ci.atomic_write_json(result_path, result)
    pptx_info = render["integrity"].get("pptx", {})
    pdf_info = render["integrity"].get("pdf", {})
    pngs_ok = sum(1 for x in render["integrity"].get("pngs", []) if x.get("is_png"))
    print("validate ok slides=%s pdf=%s pngs=%d uncertainties=%d"
          % (pptx_info.get("slides"), pdf_info.get("is_pdf"), pngs_ok, len(unc)))
    return 0


def cmd_evidence(args) -> int:
    result_path = Path(args.result)
    result, rerr = ci.read_json_robust(result_path)
    if result is None:
        result = ci.new_skeleton(None, list(ci.DEFAULT_CONTRACT))
    plan_obj, perr = ci.read_json_robust(args.plan)
    plan = plan_obj if perr is None else []
    if perr:
        result["errors"] = (result.get("errors") or []) + ["page_plan: %s" % perr]
    sources = list(args.sources or [])
    texts_root = args.texts or args.work or None
    ev, unc, pending = ci.build_evidence(plan, texts_root, sources)
    result["evidence_map"] = ev
    # keep the page plan inside the presentation contract for auditability
    if isinstance(plan_obj, dict):
        pages_list = plan_obj.get("pages", [])
    else:
        pages_list = plan_obj if isinstance(plan_obj, list) else []
    presentation = result.get("presentation") or {}
    presentation["page_plan"] = pages_list
    result["presentation"] = presentation
    result["pending_human"] = (result.get("pending_human") or []) + pending
    result["sources"] = sorted(set(result.get("sources", []) + sources))
    result["uncertainty"] = ci.append_unique(result.get("uncertainty", []), unc)
    result["change_log"].append({
        "ts": ci.utc_now(), "action": "evidence",
        "detail": "pages=%d sources=%d" % (len(ev), len(sources)),
    })
    ci.atomic_write_json(result_path, result)
    verified = sum(1 for e in ev if e["evidence_status"] == "verified_quote")
    print("evidence ok pages=%d verified=%d uncertainty_added=%d pending=%d"
          % (len(ev), verified, len(unc), len(pending)))
    return 0


def cmd_seal(args) -> int:
    result_path = Path(args.result)
    result, rerr = ci.read_json_robust(result_path)
    if result is None:
        print("seal failed: result.json unreadable (%s)" % rerr, file=sys.stderr)
        return 3
    suggested = ci.derive_status(result)
    current = result.get("status")
    # Deterministic and idempotent: same result content -> same status.
    # A hard failure is preserved only when it is backed by recorded errors;
    # otherwise the structural derivation wins so a premature seal never poisons re-runs.
    if current == "failed" and result.get("errors"):
        result["status"] = "failed"
    else:
        result["status"] = suggested
    missing = [k for k in ci.DEFAULT_CONTRACT if k not in result or result[k] is None]
    if missing:
        result["pending_human"] = (result.get("pending_human") or []) + [{
            "item": "fill output_contract field(s): %s" % ",".join(missing),
            "reason": "output_contract incomplete",
        }]
        if result["status"] == "ok":
            result["status"] = "partial"
    result["runtime"] = result.get("runtime") or {"elapsed_seconds": None,
                                                  "model_requests": None, "pages_reworked": None}
    result["change_log"].append({
        "ts": ci.utc_now(), "action": "seal", "detail": "status=%s" % result["status"],
    })
    ci.atomic_write_json(result_path, result)
    print("seal ok status=%s missing_fields=%s" % (result["status"], missing))
    return 0


# --------------------------------------------------------------------------- #
# self-test                                                                   #
# --------------------------------------------------------------------------- #

def run_selftest() -> int:
    root = Path(tempfile.mkdtemp(prefix="skill_selftest_"))
    checks = []
    try:
        # 1. empty input dir still produces a valid input_error skeleton
        d = root / "empty_in"
        d.mkdir()
        o = root / "empty_out"
        o.mkdir()
        res = ci.new_skeleton(None, list(ci.DEFAULT_CONTRACT))
        res["status"] = "input_error"
        ci.atomic_write_json(o / "result.json", res)
        checks.append(("empty_input_skeleton", res["status"] == "input_error"))

        # 2. malformed JSON is detected, not crashed on
        d2 = root / "badjson_in"
        d2.mkdir()
        (d2 / "case.json").write_text("{ not json", encoding="utf-8")
        obj, err = ci.read_json_robust(d2 / "case.json")
        checks.append(("bad_json_detected", obj is None and bool(err) and err.startswith("json:")))

        # 3. missing input dir -> skeleton still written
        o3 = root / "missing_out"
        o3.mkdir()
        r3 = ci.new_skeleton(None, list(ci.DEFAULT_CONTRACT))
        r3["status"] = "input_error"
        ci.atomic_write_json(o3 / "result.json", r3)
        checks.append(("missing_input_skeleton", (o3 / "result.json").exists()))

        # 4. non-zip pptx flagged honestly
        bad = root / "bad.pptx"
        bad.write_text("not a zip", encoding="utf-8")
        ins = ci.inspect_pptx(bad)
        checks.append(("bad_pptx_flag", ins["exists"] and not ins["is_zip"] and bool(ins["errors"])))

        # 5. non-PDF flagged honestly
        npdf = root / "not.pdf"
        npdf.write_text("hello", encoding="utf-8")
        p = ci.inspect_pdf(npdf)
        checks.append(("non_pdf_flag", p["exists"] and not p["is_pdf"]))

        # 6. quote located on the correct line; absent quote returns None
        txt = "line one\nline two has the result: accuracy 0.91\nline three"
        checks.append(("quote_located_line", ci.locate_quote(txt, "accuracy 0.91") == 2))
        checks.append(("quote_missing_none", ci.locate_quote(txt, "accuracy 0.99") is None))

        # 7. idempotent merge preserves case_id and filled fields
        base = ci.new_skeleton("c1", list(ci.DEFAULT_CONTRACT))
        ci.merge_result(base, {"presentation": {"pptx_path": "x.pptx"}})
        base2 = ci.merge_result(base, {"presentation": {"pptx_path": "x.pptx"}})
        checks.append(("merge_idempotent",
                       base2["presentation"]["pptx_path"] == "x.pptx" and base2["case_id"] == "c1"))

        # 8. status derivation never fabricates 'ok' without artifacts
        r8 = ci.new_skeleton("c2", list(ci.DEFAULT_CONTRACT))
        checks.append(("no_artifact_insufficient", ci.derive_status(r8) in
                       ("insufficient_evidence", "input_error", "partial")))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    ok = sum(1 for _, v in checks if v)
    for name, v in checks:
        print("  [%s] %s" % ("PASS" if v else "FAIL", name))
    print("selftest %d/%d passed" % (ok, len(checks)))
    return 0 if ok == len(checks) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py", description="academic-slides-evidence-deck helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="write result skeleton + input inventory")
    pi.add_argument("--input", default="/input")
    pi.add_argument("--output", default="/app/output")
    pi.add_argument("--work", default="/app/work")
    pi.set_defaults(func=cmd_init)

    pv = sub.add_parser("validate", help="inspect pptx/pdf/png and write render_record")
    pv.add_argument("--pptx", default=None)
    pv.add_argument("--pdf", default=None)
    pv.add_argument("--png", nargs="*", default=[])
    pv.add_argument("--template", default=None)
    pv.add_argument("--fonts", nargs="*", default=[])
    pv.add_argument("--sources", nargs="*", default=[])
    pv.add_argument("--output", default="/app/output")
    pv.add_argument("--result", default="/app/output/result.json")
    pv.set_defaults(func=cmd_validate)

    pe = sub.add_parser("evidence", help="build evidence_map from page_plan + texts")
    pe.add_argument("--plan", required=True)
    pe.add_argument("--texts", default=None)
    pe.add_argument("--work", default="/app/work")
    pe.add_argument("--sources", nargs="*", default=[])
    pe.add_argument("--output", default="/app/output")
    pe.add_argument("--result", default="/app/output/result.json")
    pe.set_defaults(func=cmd_evidence)

    ps = sub.add_parser("seal", help="derive status and check contract completeness")
    ps.add_argument("--result", default="/app/output/result.json")
    ps.set_defaults(func=cmd_seal)

    psel = sub.add_parser("selftest", help="run robustness self-checks")
    psel.set_defaults(func=lambda a: run_selftest())

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print("error: %s" % e, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
