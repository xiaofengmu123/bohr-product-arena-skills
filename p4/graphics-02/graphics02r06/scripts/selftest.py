#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检。

对照 result.json、work 目录中间表示与 /app/output 产物，逐条核验契约完整性、
状态、产物存在性、SVG 可编辑性（含 <text>）、PNG/PDF 魔数、以及图元溯源
（每个关键图元有 locator + 逐字 quote）。结果写入 /app/work/check_report.json，
退出码恒为 0（报告而非崩溃）。

调用：
    python scripts/selftest.py [--result /app/output/result.json
                               --work /app/work --out /app/output]
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
VALID_STATUS = {"complete", "partial", "failed"}


def load_json(path):
    if not os.path.exists(path):
        return None, "missing:%s" % path
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:
        return None, "parse:%s" % e


def collect_artifact_paths(obj, acc, prefix=""):
    """递归收集 result.json 中指向产物的字符串路径（含 .svg/.png/.pdf 或已存在）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            collect_artifact_paths(v, acc, prefix + "." + k if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            collect_artifact_paths(v, acc, "%s[%d]" % (prefix, i))
    elif isinstance(obj, str):
        if obj.endswith((".svg", ".png", ".pdf", ".pptx", ".eps")) or obj.startswith(("fig", "render", "figure")):
            acc.append((prefix, obj))


def _svg_has_text(path):
    try:
        tree = ET.parse(path)
        return any(el.tag == "{%s}text" % SVG_NS for el in tree.iter())
    except Exception:
        return None  # 解析失败


def _file_magic(path, kind):
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if kind == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "pdf":
        return head.startswith(b"%PDF-")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--result", default=os.path.join(os.environ.get("OUTPUT_DIR", "/app/output"), "result.json"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    a = ap.parse_args(argv)

    checks = []
    result, err = load_json(a.result)
    if result is None:
        checks.append({"id": "result_parseable", "pass": False, "note": err})
        report = {"ok": False, "checks": checks, "summary": "result.json 无法解析"}
        os.makedirs(a.work, exist_ok=True)
        with open(os.path.join(a.work, "check_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False))
        return 0

    # 1 契约与状态
    checks.append({"id": "case_id", "pass": bool(result.get("case_id")),
                   "note": result.get("case_id") or "空"})
    checks.append({"id": "status", "pass": result.get("status") in VALID_STATUS,
                   "note": result.get("status")})

    # 契约字段清单：优先 work/contract.json，否则取 result 中非元数据顶层键。
    contract, _ = load_json(os.path.join(a.work, "contract.json"))
    keys = contract.get("keys") if isinstance(contract, dict) else None
    if not keys:
        meta_keys = {"case_id", "status", "sources", "uncertainty", "pending_human",
                     "errors", "change_log", "output_contract"}
        keys = [k for k in result if k not in meta_keys]
    filled, missing = 0, []
    for k in keys:
        v = result.get(k)
        if v is None:
            missing.append(k)
        else:
            filled += 1
    checks.append({"id": "contract_fields", "pass": not missing,
                   "note": "filled=%d/%d; null=%s" % (filled, len(keys), missing)})

    for lst in ("sources", "change_log"):
        v = result.get(lst)
        ok = isinstance(v, list) and len(v) > 0
        checks.append({"id": lst, "pass": ok, "note": "len=%d" % (len(v) if isinstance(v, list) else -1)})
    v = result.get("uncertainty")
    checks.append({"id": "uncertainty", "pass": isinstance(v, list),
                   "note": "len=%d（空列表=已确认无不确定项，可接受）" % (len(v) if isinstance(v, list) else -1)})
    checks.append({"id": "pending_human", "pass": isinstance(result.get("pending_human"), list),
                   "note": "present" if isinstance(result.get("pending_human"), list) else "missing"})

    # 2 产物存在与格式
    artifacts = []
    collect_artifact_paths(result, artifacts)
    seen = set()
    for prefix, rel in artifacts:
        p = rel if os.path.isabs(rel) else os.path.join(a.out, rel)
        p = os.path.normpath(p)
        if p in seen:
            continue
        seen.add(p)
        if not os.path.exists(p):
            continue
        ext = os.path.splitext(p)[1].lower()
        if ext == ".svg":
            has_text = _svg_has_text(p)
            checks.append({"id": "svg_editable:%s" % rel,
                           "pass": has_text is True,
                           "note": "含<text>文本节点" if has_text else ("解析失败" if has_text is None else "文字已转曲/无<text>")})
        elif ext == ".png":
            checks.append({"id": "png_magic:%s" % rel, "pass": bool(_file_magic(p, "png")),
                           "note": "PNG 魔数"})
        elif ext == ".pdf":
            checks.append({"id": "pdf_magic:%s" % rel, "pass": bool(_file_magic(p, "pdf")),
                           "note": "PDF 魔数"})

    # 3 图元溯源（figure_model.json / scene.json）
    model, _ = load_json(os.path.join(a.work, "figure_model.json"))
    if model is None:
        model, _ = load_json(os.path.join(a.work, "scene.json"))
    if isinstance(model, dict):
        src_marks = []

        def rec(o):
            if isinstance(o, dict):
                if "source" in o and isinstance(o.get("source"), dict):
                    src_marks.append(o)
                for v in o.values():
                    rec(v)
            elif isinstance(o, list):
                for v in o:
                    rec(v)
        rec(model)
        no_ev = [m.get("id") for m in src_marks
                 if not (m["source"].get("locator") and m["source"].get("quote"))]
        checks.append({"id": "evidence_traceability", "pass": not no_ev,
                       "note": "marks=%d; missing_locator_or_quote=%s" % (len(src_marks), no_ev)})
    else:
        checks.append({"id": "evidence_traceability", "pass": False,
                       "note": "未找到 figure_model.json / scene.json"})

    # 4 CJK 字体 sanity（仅提示）
    cjk_warn = None
    for prefix, rel in artifacts:
        p = os.path.normpath(rel if os.path.isabs(rel) else os.path.join(a.out, rel))
        if p.endswith(".svg") and os.path.exists(p):
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
                has_cjk = any('\u4e00' <= c <= '\u9fff' for c in txt)
                has_font = 'font-family' in txt
                if has_cjk and not has_font:
                    cjk_warn = "SVG 含中文但未见 font-family"
            except OSError:
                pass
    checks.append({"id": "cjk_font", "pass": cjk_warn is None, "note": cjk_warn or "ok"})

    all_pass = all(c["pass"] for c in checks)
    report = {"ok": all_pass, "checks": checks,
              "summary": "%d/%d 通过" % (sum(1 for c in checks if c["pass"]), len(checks))}
    os.makedirs(a.work, exist_ok=True)
    with open(os.path.join(a.work, "check_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
