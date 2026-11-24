#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 通用入口：契约发现 + 先落盘骨架 + 结果校验 + 自检。

用法（SKILL.md 只要求按需调用，不串流水线）：
  python3 -B scripts/main.py --mode init       # 读 case.json → 写 result.json 骨架
  python3 -B scripts/main.py --mode check      # 校验 result.json 与产物，写 compliance_checks
  python3 -B scripts/main.py --mode verify     # 只做 pptx/pdf 文件级验证
  python3 -B scripts/main.py --mode selftest   # 空/坏 JSON/契约/pptx/check 五态自检
默认 --input /input --work /app/work --output /app/output（可用参数覆盖）。

init 的职责只到“合法可交接”：读 case.json → 动态解析 output_contract → 写
result.json 骨架 + work/plan.json 骨架。内容的科学判断与写作由执行 Agent 完成。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from slides_lib import (  # noqa: E402
    CANONICAL_FIELDS, ENVELOPE_FIELDS, build_skeleton, discover_materials,
    ensure_dir, extract_contract_keys, is_zip_pptx, now_iso, pdf_page_count,
    read_json, validate_result, write_json, write_text,
)

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


def resolve_dirs(args) -> tuple:
    inp = Path(args.input).expanduser() if getattr(args, "input", None) else DEFAULT_INPUT
    work = Path(args.work).expanduser() if getattr(args, "work", None) else DEFAULT_WORK
    out = Path(args.output).expanduser() if getattr(args, "output", None) else DEFAULT_OUTPUT
    for d in (work, out):
        ok, err = ensure_dir(d)
        if not ok:
            print(f"[error] 目录不可用 {d}: {err}", file=sys.stderr)
    return inp, work, out


def _ns(inp, work, out):
    return SimpleNamespace(input=str(inp), work=str(work), output=str(out))


def cmd_init(args) -> int:
    inp, work, out = resolve_dirs(args)
    case, case_err = read_json(inp / "case.json")
    if case_err or not isinstance(case, dict):
        case_id = "unknown-case"
        keys = list(CANONICAL_FIELDS)
        errors = [f"case.json unavailable: {case_err}" if case_err else "case.json 非对象"]
        status = "input_error"
    else:
        case_id = str(case.get("case_id") or case.get("id") or "unknown-case")
        keys, contract_err = extract_contract_keys(case)
        errors = []
        status = "partial"
        if contract_err:
            errors.append(contract_err)

    skeleton = build_skeleton(case_id, keys, status=status, errors=errors)
    skeleton["runtime"] = skeleton.get("runtime") or {}
    skeleton["runtime"]["mode"] = "init"

    # 规范字段的占位主产物（仅当该字段确在契约中；直接赋值，覆盖骨架默认空值）。
    if "editable_source" in keys:
        skeleton["editable_source"] = {
            "path": "presentation.pptx", "format": "pptx", "slides": 0,
            "template": {"provided": False, "inherited": False},
        }
    if "render_record" in keys:
        skeleton["render_record"] = {
            "source": "presentation.pptx", "export_pdf": None, "export_png": None,
            "fonts": [], "template": None, "changes": [],
        }
    if "evidence_map" in keys:
        skeleton["evidence_map"] = []
    if "compliance_checks" in keys:
        skeleton["compliance_checks"] = []

    materials = discover_materials(inp)
    skeleton.setdefault("sources", [])
    skeleton["sources"] = [
        {"rel": m["rel"], "bytes": m["bytes"], "kind": m["kind"]} for m in materials
    ]
    skeleton.setdefault("change_log", []).append({
        "at": now_iso(), "action": "init", "note": "落盘 partial 骨架（先交粗稿，再增量改进）",
    })

    write_json(out / "result.json", skeleton)
    write_json(work / "plan.json", {
        "case_id": case_id, "status": status, "created_at": now_iso(),
        "slides": [], "notes": "逐页填充：page/claim/role/evidence[{locator,quote}]/figures/notes",
    })
    write_text(work / "contract_keys.txt",
               "output_contract 字段（按 case.json 原样顺序）：\n" + "\n".join(keys) + "\n")
    print(f"[init] case_id={case_id} status={status} 契约字段={len(keys)} 材料={len(materials)}")
    print(f"[init] 已写 {out / 'result.json'} 与 {work / 'plan.json'}")
    return 0


def cmd_check(args) -> int:
    inp, work, out = resolve_dirs(args)
    result, err = read_json(out / "result.json")
    if err or not isinstance(result, dict):
        print(f"[check] result.json 不可读: {err}")
        return 1
    case, _ = read_json(inp / "case.json")
    keys, _ = extract_contract_keys(case if isinstance(case, dict) else {})
    if not keys:
        keys = [k for k in result if k not in ENVELOPE_FIELDS]
    issues = validate_result(result, keys, output_dir=out)

    src = result.get("editable_source")
    if isinstance(src, dict) and src.get("path"):
        p = Path(src["path"])
        if not p.is_absolute():
            p = out / p
        if p.exists() and p.suffix.lower() == ".pptx" and not is_zip_pptx(p):
            issues.append(f"pptx 结构非法（非 zip 或缺 slide 部件）: {p}")

    pdf = None
    rr = result.get("render_record") or result.get("export_artifact")
    if isinstance(rr, dict):
        pdf = rr.get("export_pdf")
    if pdf:
        pp = Path(pdf)
        if not pp.is_absolute():
            pp = out / pp
        if pp.exists():
            cnt, perr = pdf_page_count(pp)
            if perr:
                issues.append(f"PDF 校验失败: {perr}")
            elif isinstance(src, dict) and isinstance(src.get("slides"), int) and cnt is not None:
                if cnt != src["slides"] and src["slides"] > 0:
                    issues.append(f"PDF 页数({cnt})与 slides 数({src['slides']})不一致")

    checks = result.get("compliance_checks")
    if not isinstance(checks, list):
        checks = []
    checks.append({"at": now_iso(), "mode": "check", "passed": not issues, "issues": issues})
    result["compliance_checks"] = checks
    write_json(out / "result.json", result)
    print(f"[check] 通过={not issues} 问题={len(issues)}")
    for it in issues:
        print(f"  - {it}")
    return 0 if not issues else 2


def cmd_verify(args) -> int:
    """只做文件级验证，不修改 result.json。"""
    inp, work, out = resolve_dirs(args)
    ok_all = True
    for name in ("presentation.pptx",):
        p = out / name
        if not p.exists():
            print(f"[verify] 缺 {name}")
            ok_all = False
        elif name.endswith(".pptx") and not is_zip_pptx(p):
            print(f"[verify] {name} 结构非法")
            ok_all = False
        else:
            print(f"[verify] {name} OK ({p.stat().st_size} 字节)")
    for name in ("presentation.pdf",):
        p = out / name
        if not p.exists():
            print(f"[verify] 缺 {name}")
            ok_all = False
        else:
            cnt, perr = pdf_page_count(p)
            if perr:
                print(f"[verify] {name} 非法: {perr}")
                ok_all = False
            else:
                print(f"[verify] {name} OK, 页数(启发式)={cnt}")
    return 0 if ok_all else 3


# ---------------------------------------------------------------- selftest ---

def _selt_empty():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = Path(d) / "in", Path(d) / "out", Path(d) / "work"
        inp.mkdir()
        rc = cmd_init(_ns(inp, work, out))
        assert rc == 0
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert res.get("case_id") == "unknown-case" and res.get("status") == "input_error"
        assert isinstance(res.get("errors"), list) and res["errors"]
    return True


def _selt_bad_json():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = Path(d) / "in", Path(d) / "out", Path(d) / "work"
        inp.mkdir()
        (inp / "case.json").write_text("{ not json", encoding="utf-8")
        assert cmd_init(_ns(inp, work, out)) == 0
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert res.get("status") == "input_error" and res["errors"]
    return True


def _selt_contract():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = Path(d) / "in", Path(d) / "out", Path(d) / "work"
        inp.mkdir()
        (inp / "case.json").write_text(json.dumps({
            "case_id": "demo-1",
            "output_contract": {"case_id": None, "editable_source": None, "evidence_map": None,
                                "render_record": None, "compliance_checks": None, "exceptions": None},
        }, ensure_ascii=False), encoding="utf-8")
        assert cmd_init(_ns(inp, work, out)) == 0
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        for k in ("case_id", "editable_source", "evidence_map", "render_record",
                  "compliance_checks", "exceptions"):
            assert k in res, k
        assert res["case_id"] == "demo-1"
        assert "unknown_field" not in res
    return True


def _selt_pptx():
    from pptx_oo import build_deck
    import zipfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "deck.pptx"
        ok, err = build_deck([
            {"title": "Title 1", "body": ["point A", "point B"]},
            {"title": "Title 2", "body": ["point C"]},
        ], p)
        assert ok, err
        assert zipfile.is_zipfile(str(p))
        with zipfile.ZipFile(str(p)) as zf:
            names = set(zf.namelist())
            for name in names:
                if name.endswith((".xml", ".rels")):
                    ET.fromstring(zf.read(name))
        assert "[Content_Types].xml" in names and "ppt/slides/slide2.xml" in names
        assert is_zip_pptx(p)
    return True


def _selt_check():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = Path(d) / "in", Path(d) / "out", Path(d) / "work"
        inp.mkdir()
        (inp / "case.json").write_text(json.dumps({
            "case_id": "demo-2",
            "output_contract": {"case_id": None, "editable_source": None, "evidence_map": None},
        }, ensure_ascii=False), encoding="utf-8")
        assert cmd_init(_ns(inp, work, out)) == 0
        assert cmd_check(_ns(inp, work, out)) in (0, 2)
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert isinstance(res.get("compliance_checks"), list) and res["compliance_checks"]
    return True


def cmd_selftest(args) -> int:
    scenarios = [
        ("空输入目录", _selt_empty),
        ("坏 JSON", _selt_bad_json),
        ("动态契约回填", _selt_contract),
        ("最小 pptx 构建器", _selt_pptx),
        ("check 校验不崩溃", _selt_check),
    ]
    failed = 0
    for name, fn in scenarios:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {exc.__class__.__name__}: {exc}")
    print(f"selftest: {len(scenarios) - failed}/{len(scenarios)} passed")
    return 1 if failed else 0


# ---------------------------------------------------------------- entry -----

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="main.py", description="slides-01 入口")
    ap.add_argument("--mode", choices=["init", "check", "verify", "selftest"], default="init")
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args(argv)
    started = time.time()
    handlers = {"init": cmd_init, "check": cmd_check, "verify": cmd_verify, "selftest": cmd_selftest}
    try:
        rc = handlers[args.mode](args)
    except Exception as exc:  # noqa: BLE001 —— 兜底不得崩
        print(f"[fatal] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        rc = 9
    print(f"[done] elapsed={time.time() - started:.2f}s rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
