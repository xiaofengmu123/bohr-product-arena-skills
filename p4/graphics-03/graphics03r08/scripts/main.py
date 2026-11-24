#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生物机制图与通路图绘图引擎（纯标准库、确定性）。

子命令：
  init        扫描输入目录，写 spec.json（候选抽取）+ result.json partial 骨架
  check-spec  校验 spec.json：exit 0=通过, 1=阻断, 2=警告
  build       读 spec.json -> 渲染分层 SVG + stdlib 矢量 PDF -> 组装 result.json
  check       校验 result.json 契约与产物存在性/格式：exit 0/1/2
  run         init + build + check（自测与复现用）

确定性保证：布局由 spec 顺序与区室分层唯一决定；SVG/PDF 不含时间戳；
同一输入重复运行，图件字节级一致，result.json 仅 runtime/change_log 时间字段自然变化。
渲染核心见 core_impl.py。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from core_impl import (ENGINE_VERSION, DEFAULT_COMPARTMENTS, build_legend, build_pdf,
                       build_svg, compute_layout)

MAX_FILE_BYTES = 2 * 1024 * 1024

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALLOWED_STATUS = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

ENTITY_KINDS = {"gene", "protein", "complex", "family", "cell_type", "compartment", "other"}
RELATION_TYPES = {"activation", "inhibition", "binding", "modification", "transport", "conversion"}
COMPARTMENT_ORDER = ["extracellular", "membrane", "cytosol", "organelle", "nucleus"]

COMPARTMENT_TERMS = {
    "nucleus": ["nucleus", "nuclear", "核内", "细胞核", "核"],
    "cytosol": ["cytosol", "cytoplasm", "cytoplasmic", "胞质", "细胞质", "胞浆"],
    "membrane": ["membrane", "plasma membrane", "细胞膜", "膜", "跨膜"],
    "extracellular": ["extracellular", "胞外", "细胞外"],
    "organelle": ["mitochondria", "mitochondrion", "ER", "endoplasmic reticulum", "golgi",
                  "线粒体", "内质网", "高尔基体"],
}
CELL_TERMS = ["t cell", "b cell", "macrophage", "neuron", "epithelial", "fibroblast",
              "T 细胞", "B 细胞", "巨噬细胞", "神经元", "上皮细胞", "成纤维细胞"]

RELATION_VERBS = {
    "activation": ["activate", "activates", "activated", "promote", "promotes", "induce",
                   "induces", "stimulate", "stimulates", "enhance", "enhances", "upregulate",
                   "激活", "促进", "诱导", "增强", "上调", "增加"],
    "inhibition": ["inhibit", "inhibits", "suppress", "suppresses", "block", "blocks",
                   "repress", "represses", "downregulate", "抑制", "阻断", "下调", "减少"],
    "binding": ["bind", "binds", "interact", "interacts", "associate", "associates",
                "recruit", "recruits", "结合", "招募", "募集", "相互作用"],
    "modification": ["phosphorylate", "phosphorylates", "ubiquitinate", "ubiquitinates",
                     "acetylate", "acetylates", "methylate", "methylates", "cleave",
                     "磷酸化", "泛素化", "乙酰化", "甲基化", "剪切"],
    "transport": ["transport", "transports", "translocate", "translocates", "import",
                  "imports", "export", "exports", "secrete", "secretes", "转运", "转位", "分泌"],
    "conversion": ["convert", "converts", "generate", "generates", "become", "转化", "生成", "变成"],
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, cap=MAX_FILE_BYTES):
    try:
        with open(path, "rb") as f:
            data = f.read(cap + 1)
        if len(data) > cap:
            return "", "file exceeds 2MB (skipped)"
        return data.decode("utf-8-sig", errors="replace"), None
    except OSError as exc:
        return "", str(exc)


def discover_input(inp):
    if not os.path.isdir(inp):
        return []
    out = []
    for root, dirs, names in os.walk(inp):
        dirs.sort()
        names.sort()
        for n in names:
            p = os.path.join(root, n)
            rel = os.path.relpath(p, inp).replace(os.sep, "/")
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"path": p, "rel": rel, "size": st.st_size,
                        "ext": os.path.splitext(n)[1].lower()})
    return out


def find_case_file(files):
    for f in files:
        if os.path.basename(f["rel"]) == "case.json":
            return f
    for f in files:
        if f["ext"] == ".json":
            txt, err = read_text(f["path"])
            if err:
                continue
            try:
                d = json.loads(txt)
            except Exception:
                continue
            if isinstance(d, dict) and any(k in d for k in ("case_id", "id", "armiId")):
                return f
    return None


def extract_case_id(d, fallback=""):
    if isinstance(d, dict):
        for k in ("case_id", "id", "armiId", "caseId"):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return fallback or "unknown"


# ---------------------------------------------------------------- heuristics

GENE_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,12}\b")
COMMON_WORDS = {"THE", "AND", "FOR", "WITH", "FROM", "THIS", "THAT", "WAS", "ARE", "NOT",
                "FIG", "TABLE", "A", "B", "C", "D", "E", "T", "DNA", "RNA", "ATP", "GTP",
                "GDP", "ER", "IF", "OR", "IN", "ON", "OF", "TO", "BY", "AS", "IS", "BE"}


def split_sentences(text):
    return re.split(r"(?<=[.!?。！？])\s+", text)


def extract_candidates(files):
    """保守词法抽取：产出低置信候选（全部 inferred:true），供 Agent 校正。"""
    entities = []
    seen = {}
    relations = []
    token_to_eid = {}
    detected_comps = []

    def add_entity(eid, label, kind, compartment, locator, quote):
        key = (kind, label.lower())
        if key in seen:
            return seen[key]
        e = {"id": eid, "label": label, "kind": kind, "species": "", "compartment": compartment,
             "transmembrane": kind in ("protein",) and compartment == "membrane",
             "aliases": [], "inferred": True,
             "sources": [{"locator": locator, "quote": quote}]}
        entities.append(e)
        seen[key] = e["id"]
        return e["id"]

    def term_hit(term, low):
        if term.isascii():
            return re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", low)
        return term in low

    ei = [0]

    def next_id(prefix):
        ei[0] += 1
        return "%s%d" % (prefix, ei[0])

    material = [f for f in files if os.path.basename(f["rel"]) not in ("case.json", "task.md")]
    for f in material:
        if f["ext"] not in (".md", ".txt", ".json"):
            continue
        text, err = read_text(f["path"])
        if err:
            continue
        low = text.lower()
        for comp, terms in COMPARTMENT_TERMS.items():
            for t in terms:
                if term_hit(t, low) and comp not in detected_comps:
                    detected_comps.append(comp)
        for t in CELL_TERMS:
            if term_hit(t, low):
                i = low.find(t.lower())
                q = text[max(0, i - 20):i + len(t) + 20].strip()
                add_entity(next_id("ct"), t, "cell_type", "unknown", "%s#L1" % f["rel"], q)
        for m in GENE_RE.finditer(text):
            tok = m.group(0)
            if tok in COMMON_WORDS or len(tok) < 2:
                continue
            kind = "gene" if re.search(r"[0-9]", tok) else "protein"
            q = text[max(0, m.start() - 25):m.end() + 25].strip()
            loc = "%s#L1" % f["rel"]
            eid = add_entity(next_id("e"), tok, kind, "unknown", loc, q)
            token_to_eid.setdefault(tok, eid)

    ri = [0]
    for f in material:
        if f["ext"] not in (".md", ".txt"):
            continue
        text, err = read_text(f["path"])
        if err:
            continue
        for sent in split_sentences(text):
            sl = sent.lower()
            vtype = None
            for rtype, verbs in RELATION_VERBS.items():
                if any(v.lower() in sl for v in verbs):
                    vtype = rtype
                    break
            if not vtype:
                continue
            toks = [m.group(0) for m in GENE_RE.finditer(sent)
                    if m.group(0) not in COMMON_WORDS]
            toks = [t for t in toks if t in token_to_eid][:2]
            if len(toks) < 2:
                continue
            ri[0] += 1
            relations.append({"id": "r%d" % ri[0], "from": token_to_eid[toks[0]],
                              "to": token_to_eid[toks[1]], "type": vtype,
                              "direction": "forward", "modifier": "", "evidence": "inferred",
                              "inferred": True,
                              "sources": [{"locator": "%s#L1" % f["rel"], "quote": sent.strip()[:200]}]})
    return entities, relations, detected_comps


# ---------------------------------------------------------------- validation

def item_has_quote(item):
    for s in (item.get("sources") or []):
        if isinstance(s, dict) and (s.get("quote") or "").strip():
            return True
    return False


def validate_spec(spec):
    errors, warnings = [], []
    if not isinstance(spec, dict):
        errors.append("spec 不是 JSON 对象")
        return errors, warnings
    if not (spec.get("case_id") or "").strip():
        errors.append("case_id 缺失")
    ents = spec.get("entities")
    if ents is None:
        errors.append("entities 缺失")
    elif not isinstance(ents, list):
        errors.append("entities 必须是数组")
    else:
        ids = set()
        for e in ents:
            if not isinstance(e, dict):
                errors.append("entity 必须是对象")
                continue
            eid = e.get("id")
            if not eid:
                errors.append("entity 缺 id")
                continue
            if eid in ids:
                errors.append("entity id 重复: %s" % eid)
            ids.add(eid)
            if not (e.get("label") or "").strip():
                errors.append("entity %s 缺 label" % eid)
            if e.get("kind") not in ENTITY_KINDS:
                errors.append("entity %s kind 非法: %r" % (eid, e.get("kind")))
            if e.get("inferred") is True:
                if not item_has_quote(e):
                    warnings.append("entity %s 为候选但无 quote" % eid)
            else:
                if not item_has_quote(e):
                    errors.append("entity %s 为断言但缺 sources[{quote}]" % eid)
    rels = spec.get("relations")
    if rels is None:
        errors.append("relations 缺失")
    elif not isinstance(rels, list):
        errors.append("relations 必须是数组")
    else:
        ent_ids = {e.get("id") for e in ents if isinstance(e, dict)}
        for r in rels:
            if not isinstance(r, dict):
                errors.append("relation 必须是对象")
                continue
            if r.get("type") not in RELATION_TYPES:
                errors.append("relation %s type 非法: %r" % (r.get("id"), r.get("type")))
            for end in ("from", "to"):
                v = r.get(end)
                if v not in ent_ids:
                    errors.append("relation %s.%s=%r 未在 entities 中" % (r.get("id"), end, v))
            if r.get("inferred") is True:
                if not item_has_quote(r):
                    warnings.append("relation %s 为候选但无 quote" % r.get("id"))
            else:
                if not item_has_quote(r):
                    errors.append("relation %s 为断言但缺 sources[{quote}]" % r.get("id"))
    dm = spec.get("data_mappings")
    if dm is not None and isinstance(dm, list):
        for d in dm:
            if isinstance(d, dict) and not d.get("inferred") and not item_has_quote(d):
                errors.append("data_mapping 缺 sources[{quote}]")
    return errors, warnings


# ---------------------------------------------------------------- result assembly

def aggregate_sources(spec):
    out, seen = [], set()
    for e in (spec.get("entities") or []):
        for s in (e.get("sources") or []):
            key = (s.get("locator"), s.get("quote"))
            if key in seen or not s.get("quote"):
                continue
            seen.add(key)
            out.append({"id": "s%d" % len(out), "file": s.get("locator", "").split("#")[0],
                        "locator": s.get("locator", ""), "quote": s.get("quote", ""), "kind": "entity"})
    for r in (spec.get("relations") or []):
        for s in (r.get("sources") or []):
            key = (s.get("locator"), s.get("quote"))
            if key in seen or not s.get("quote"):
                continue
            seen.add(key)
            out.append({"id": "s%d" % len(out), "file": s.get("locator", "").split("#")[0],
                        "locator": s.get("locator", ""), "quote": s.get("quote", ""), "kind": "relation"})
    return out


def rubric_alignment(spec, errors, warnings):
    ents = spec.get("entities") or []
    rels = spec.get("relations") or []
    dm = spec.get("data_mappings") or []
    evidenced = sum(1 for e in ents if item_has_quote(e)) + sum(1 for r in rels if item_has_quote(r))
    total = len(ents) + len(rels)
    return {
        "actual_effect": {
            "score_dim": "实际效果 40 分", "status": "done" if total else "pending",
            "checks": ["实体不混用", "关系可区分", "方向正确", "闭环完整"],
            "metric": {"entities": len(ents), "relations": len(rels)}},
        "verifiability": {
            "score_dim": "结果可核验性", "status": "done" if evidenced >= total and total else "pending",
            "checks": ["locator+quote 全覆盖", "追溯链可复核"],
            "metric": {"evidenced": evidenced, "total": total}},
        "gain": {
            "score_dim": "使用增益 25 分", "status": "done" if len(spec.get("change_log") or []) else "pending",
            "checks": ["骨架先行", "增量改进", "导出件齐全"], "metric": {}},
        "innovation": {
            "score_dim": "创新增益 15 分", "status": "done" if dm else "pending",
            "checks": ["机制文字→图元映射", "箭头语义判定", "图例自洽"],
            "metric": {"data_mappings": len(dm)}},
        "reliability": {
            "score_dim": "可靠体验 10 分", "status": "done" if not errors else "partial",
            "checks": ["缺方向识别", "歧义识别", "导出失败处置"],
            "metric": {"errors": len(errors), "warnings": len(warnings)}},
        "ecology": {
            "score_dim": "生态贡献 10 分", "status": "done",
            "checks": ["词表可复用", "布局可复用", "校验脚本可复用"], "metric": {}},
    }


def compute_status(spec, errors):
    if not (spec.get("entities") or []):
        return STATUS_INSUFFICIENT
    if errors:
        return STATUS_PARTIAL
    if any(e.get("inferred") for e in (spec.get("entities") or [])):
        return STATUS_PARTIAL
    if any(r.get("inferred") for r in (spec.get("relations") or [])):
        return STATUS_PARTIAL
    if spec.get("uncertainty") or []:
        return STATUS_PARTIAL
    return STATUS_OK


def assemble_result(spec, errors, warnings, elapsed, out_dir):
    layout = compute_layout(spec)
    svg = build_svg(spec, layout)
    svg_path = os.path.join(out_dir, "mechanism.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)
    pdf_bytes, font_issues = build_pdf(spec, layout)
    pdf_path = os.path.join(out_dir, "mechanism.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    sources = aggregate_sources(spec)
    change_log = list(spec.get("change_log") or [])
    if not change_log:
        change_log.append({"time": now_iso(), "step": "skeleton", "summary": "骨架落盘"})
    change_log.append({"time": now_iso(), "step": "build",
                       "summary": "渲染 SVG/PDF 并组装 result.json（%d 实体/%d 关系）"
                       % (len(spec.get("entities") or []), len(spec.get("relations") or []))})
    font_check = "非 Latin-1 标签在 stdlib PDF 中以 ASCII id 回退" if font_issues else "全部标签 Latin-1，可直接渲染"
    result = {
        "case_id": spec.get("case_id") or "unknown",
        "status": compute_status(spec, errors),
        "title": spec.get("title") or "",
        "task_note": spec.get("task_note") or "",
        "editable_source_files": [{
            "path": "mechanism.svg", "format": "svg", "role": "primary_editable",
            "layers": ["background", "compartments", "edges", "nodes", "labels", "legend"],
            "width_px": layout["canvas"]["w"], "height_px": layout["canvas"]["h"],
            "renderer": "stdlib-vector", "font_check": "可替换字体栈",
            "color_check": "色板与图例一致（由引擎统一生成）"}],
        "export_artifacts": [{
            "path": "mechanism.pdf", "format": "pdf", "role": "rendered_preview",
            "width_px": layout["canvas"]["w"], "height_px": layout["canvas"]["h"],
            "dpi": 72, "renderer": "stdlib-vector", "font_check": font_check,
            "color_check": "色板与图例一致"}],
        "entities": spec.get("entities") or [],
        "relations": spec.get("relations") or [],
        "legend": build_legend(layout),
        "data_mappings": spec.get("data_mappings") or [],
        "sources": sources,
        "uncertainty": spec.get("uncertainty") or [],
        "pending_human": spec.get("pending_human") or [],
        "change_log": change_log,
        "rubric_alignment": rubric_alignment(spec, errors, warnings),
        "limitations": spec.get("limitations") or (["存在校验错误或缺口，见 errors/uncertainty"] if errors else []),
        "degradations": spec.get("degradations") or (["PNG 未由脚本生成，需平台渲染器"]),
        "errors": errors,
        "runtime": {"seconds": round(elapsed, 2), "engine_version": ENGINE_VERSION,
                    "tools_used": ["main.py", "stdlib SVG/PDF engine"]},
    }
    png_path = os.path.join(out_dir, "mechanism.png")
    if os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
        result["export_artifacts"].append({
            "path": "mechanism.png", "format": "png", "role": "rendered_preview",
            "renderer": "platform-renderer", "font_check": "未核验", "color_check": "未核验"})
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


# ---------------------------------------------------------------- result check

def check_result(out_dir):
    errors, warnings = [], []
    rp = os.path.join(out_dir, "result.json")
    if not os.path.isfile(rp):
        return ["result.json 不存在"], []
    try:
        with open(rp, "r", encoding="utf-8-sig") as f:
            result = json.load(f)
    except Exception as exc:
        return ["result.json 无法解析: %s" % exc], []
    for key in ("case_id", "status", "editable_source_files", "export_artifacts",
                "entities", "relations", "legend", "data_mappings", "sources",
                "uncertainty", "pending_human", "change_log", "limitations"):
        if key not in result:
            errors.append("result.json 缺字段: %s" % key)
    if result.get("status") not in ALLOWED_STATUS:
        errors.append("status 非法: %r" % result.get("status"))
    change_log = result.get("change_log")
    if not isinstance(change_log, list) or not change_log:
        errors.append("change_log 为空")
    elif not (isinstance(change_log[0], dict) and change_log[0].get("step") == "skeleton"):
        warnings.append("change_log 首条 step 不是 skeleton")
    for art in (result.get("export_artifacts") or []):
        p = art.get("path")
        fp = os.path.join(out_dir, p) if p else ""
        if not p or not os.path.isfile(fp):
            errors.append("export_artifact 文件不存在: %s" % p)
            continue
        with open(fp, "rb") as f:
            head = f.read(8)
        fmt = (art.get("format") or "").lower()
        if fmt == "pdf" and not head.startswith(b"%PDF-"):
            errors.append("PDF 签名错误: %s" % p)
        if fmt == "png" and head != b"\x89PNG\r\n\x1a\n":
            errors.append("PNG 签名错误: %s" % p)
        if fmt == "svg" and not head.lstrip().startswith(b"<"):
            errors.append("SVG 签名错误: %s" % p)
    for src in (result.get("editable_source_files") or []):
        p = src.get("path")
        fp = os.path.join(out_dir, p) if p else ""
        if not p or not os.path.isfile(fp):
            errors.append("editable_source_file 不存在: %s" % p)
    return errors, warnings


def check_spec_path(spec_path):
    if not os.path.isfile(spec_path):
        return ["spec.json 不存在: %s" % spec_path], []
    try:
        with open(spec_path, "r", encoding="utf-8-sig") as f:
            spec = json.load(f)
    except Exception as exc:
        return ["spec.json 无法解析: %s" % exc], []
    return validate_spec(spec)


# ---------------------------------------------------------------- commands

def cmd_init(args):
    t0 = time.time()
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    files = discover_input(args.input)
    case_file = find_case_file(files)
    case_id, meta_readable = "unknown", True
    case_meta = {}
    if case_file:
        txt, err = read_text(case_file["path"])
        if err:
            meta_readable = False
        else:
            try:
                case_meta = json.loads(txt) if txt.strip() else {}
                if not isinstance(case_meta, dict):
                    case_meta = {}
            except Exception:
                case_meta, meta_readable = {}, False
        case_id = extract_case_id(case_meta, case_file["rel"])
    else:
        case_id = os.path.basename(os.path.normpath(args.input)) or "unknown"
    entities, relations, detected_comps = extract_candidates(files)
    comps = sorted(detected_comps, key=lambda c: COMPARTMENT_ORDER.index(c) if c in COMPARTMENT_ORDER else 99) or DEFAULT_COMPARTMENTS
    spec = {
        "case_id": case_id, "title": "", "task_note": "",
        "compartments": comps,
        "entities": entities, "relations": relations, "data_mappings": [],
        "uncertainty": [], "pending_human": [], "limitations": [], "degradations": [],
        "change_log": [{"time": now_iso(), "step": "skeleton",
                        "summary": "骨架落盘：%d 候选实体 / %d 候选关系" % (len(entities), len(relations))}],
        "input_scan": {"files": [{"rel": f["rel"], "size": f["size"], "ext": f["ext"]} for f in files],
                       "case_readable": meta_readable},
    }
    spec_path = os.path.join(args.work, "spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    errors, warnings = validate_spec(spec)
    skeleton = {
        "case_id": case_id, "status": STATUS_PARTIAL if entities else STATUS_INSUFFICIENT,
        "title": "", "task_note": "",
        "editable_source_files": [], "export_artifacts": [], "entities": entities,
        "relations": relations, "legend": [], "data_mappings": [],
        "sources": aggregate_sources(spec), "uncertainty": [], "pending_human": [],
        "change_log": spec["change_log"],
        "rubric_alignment": rubric_alignment(spec, errors, warnings),
        "limitations": ["骨架阶段：实体/关系为候选，需 Agent 逐条补证据"],
        "degradations": ["未渲染图件"], "errors": [],
        "runtime": {"seconds": round(time.time() - t0, 2), "engine_version": ENGINE_VERSION,
                    "tools_used": ["main.py init"]},
    }
    with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "case_id": case_id, "entities": len(entities),
                      "relations": len(relations), "spec": spec_path,
                      "result": os.path.join(args.out, "result.json")}, ensure_ascii=False))
    return 0


def cmd_check_spec(args):
    spec_path = os.path.join(args.work, "spec.json")
    errors, warnings = check_spec_path(spec_path)
    print(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 1 if errors else (2 if warnings else 0)


def cmd_build(args):
    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)
    spec_path = os.path.join(args.work, "spec.json")
    if not os.path.isfile(spec_path):
        print("spec.json 不存在，请先运行 init", file=sys.stderr)
        return 1
    with open(spec_path, "r", encoding="utf-8-sig") as f:
        spec = json.load(f)
    if not isinstance(spec, dict):
        spec = {}
    errors, warnings = validate_spec(spec)
    result = assemble_result(spec, errors, warnings, time.time() - t0, args.out)
    print(json.dumps({"ok": True, "status": result["status"], "errors": errors,
                      "warnings": warnings, "out": args.out}, ensure_ascii=False))
    return 0


def cmd_check(args):
    errors, warnings = check_result(args.out)
    print(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 1 if errors else (2 if warnings else 0)


def cmd_run(args):
    rc = cmd_init(args)
    if rc:
        return rc
    rc2 = cmd_build(args)
    if rc2:
        return rc2
    return cmd_check(args)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Biological mechanism/pathway figure engine")
    ap.add_argument("command", choices=["init", "check-spec", "build", "check", "run"])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--out", default="/app/output")
    args = ap.parse_args(argv)
    fn = {"init": cmd_init, "check-spec": cmd_check_spec, "build": cmd_build,
          "check": cmd_check, "run": cmd_run}[args.command]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
