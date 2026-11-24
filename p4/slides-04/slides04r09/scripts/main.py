#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术 PPT 模板适配的确定性记账与校验脚本（仅 Python 标准库）。

子命令:
  init      读取 case.json，幂等落盘 result.json 骨架 + input_inventory.json
  validate  校验 pptx 结构/母版版式继承/字体/图表等比/溢出启发式，写 render_record
  evidence  由页计划 + 提取文本生成逐页证据映射（locator + 逐字 quote 校验）
  finalize  合并收口字段（status/sources/uncertainty/pending_human/change_log）

退出码: 0=成功（含诚实降级）；2=参数错误；1=内部错误。
"""

import argparse
import glob
import hashlib
import json
import os
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def q(prefix, tag):
    return "{%s}%s" % (NS[prefix], tag)


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    """按 utf-8-sig/utf-8/gbk/latin-1 容错读取；失败返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.loads(f.read())
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
    return None


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def deep_merge(base, patch):
    """dict 递归合并；list/标量以 patch 覆盖；patch 值为 None 时不覆盖（保幂等）。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            elif v is not None:
                out[k] = v
        return out
    return patch if patch is not None else base


def update_result(path, patch, change=None):
    cur = load_json(path) or {}
    merged = deep_merge(cur, patch)
    ts = now_iso()
    if change:
        log = list(merged.get("change_log") or [])
        log.append({"ts": ts, "event": change.get("event", "update"),
                    "detail": change.get("detail", "")})
        merged["change_log"] = log
    rt = dict(merged.get("runtime") or {})
    rt["updated_at"] = ts
    rt.setdefault("started_at", ts)
    merged["runtime"] = rt
    write_json(path, merged)
    return merged


def sha_file(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def default_for(v):
    if isinstance(v, dict):
        return {k: default_for(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return []
    return None


def list_files(root, max_depth=4):
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        rel_depth = os.path.relpath(dirpath, root).count(os.sep)
        if rel_depth >= max_depth:
            dirs[:] = []
        for fn in sorted(files):
            out.append(os.path.join(dirpath, fn))
    return out


def inventory_dir(root):
    files = []
    for full in list_files(root):
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        try:
            st = os.stat(full)
            size = st.st_size
            digest = None
            skipped = size > 2_000_000
            if not skipped:
                digest = sha_file(full)
            files.append({"path": rel, "size": size, "sha256": digest,
                          "skipped_large": skipped})
        except OSError as e:
            files.append({"path": rel, "error": str(e)})
    return {"files": files, "scanned_at": now_iso()}


def find_case(input_dir):
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        obj = load_json(p)
        if isinstance(obj, dict):
            return obj, p
    for full in list_files(input_dir):
        if full.lower().endswith(".json"):
            obj = load_json(full)
            if isinstance(obj, dict) and any(k in obj for k in ("case_id", "id")):
                return obj, full
    return {}, None


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for k in ("case_id", "id", "challenge_id", "caseId"):
        v = obj.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return None


def build_skeleton(case_id, contract):
    sk = {
        "case_id": case_id,
        "status": "in_progress" if case_id else "insufficient_evidence",
        "presentation": {"pptx_path": None, "pdf_path": None, "png_paths": [],
                         "slide_count": None, "page_plan": []},
        "evidence_map": [],
        "render_record": {"source_files": [], "template_file": None,
                          "layouts_used": [], "fonts": [], "exports": [],
                          "integrity": {}, "media": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "runtime": {"started_at": now_iso(), "updated_at": now_iso()},
    }
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k not in sk:
                sk[k] = default_for(v)
    return sk


# ---------------------------------------------------------------- init ----

def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case, case_path = find_case(args.input)
    case_id = extract_case_id(case)
    skeleton = build_skeleton(case_id, case.get("output_contract") if isinstance(case, dict) else None)
    result_path = os.path.join(args.output, "result.json")
    cur = load_json(result_path) or {}
    merged = deep_merge(skeleton, cur)
    merged["case_id"] = case_id
    existing_status = (cur or {}).get("status")
    if existing_status and existing_status != "in_progress":
        merged["status"] = existing_status
    else:
        merged["status"] = "in_progress" if case_id else "insufficient_evidence"

    if not cur:
        merged.setdefault("change_log", []).append(
            {"ts": now_iso(), "event": "init", "detail": "result skeleton written"})

    # 输入缺失 / 坏 JSON 的诚实标注
    case_file = os.path.join(args.input, "case.json")
    if not os.path.isfile(case_file):
        _add_uncertainty(merged, {"code": "missing_case_json", "detail": "no case.json found"})
    elif load_json(case_file) is None:
        _add_uncertainty(merged, {"code": "bad_case_json", "detail": "case.json failed to parse"})

    write_json(result_path, merged)
    write_json(os.path.join(args.work, "input_inventory.json"), inventory_dir(args.input))
    print("init: case_id=%s status=%s files=%d" % (
        case_id or "(none)", merged["status"],
        len(load_json(os.path.join(args.work, "input_inventory.json"))["files"])))


def _add_uncertainty(result, item):
    for u in result.get("uncertainty") or []:
        if u.get("code") == item.get("code"):
            return
    result.setdefault("uncertainty", []).append(item)


# ------------------------------------------------------------- validate ----

def read_xml(z, name):
    try:
        return ET.fromstring(z.read(name))
    except Exception:
        return None


def parse_rels(z, rels_path):
    if rels_path not in z.namelist():
        return []
    root = read_xml(z, rels_path)
    if root is None:
        return []
    out = []
    for rel in root:
        out.append({"id": rel.get("Id"), "type": rel.get("Type"),
                    "target": rel.get("Target")})
    return out


def first_rel(rels, suffix):
    for r in rels:
        if r.get("type") and suffix in r["type"]:
            return r
    return None


def collect_fonts(root):
    out = set()
    if root is None:
        return out
    for tag in (q("a", "latin"), q("a", "ea"), q("a", "cs")):
        for el in root.iter(tag):
            tf = el.get("typeface")
            if tf:
                out.add(tf)
    return out


def shape_extent(el):
    xfrm = el.find(q("p", "spPr") + "/" + q("a", "xfrm"))
    if xfrm is None:
        return None
    ext = xfrm.find(q("a", "ext"))
    if ext is None:
        return None
    try:
        return int(ext.get("cx")), int(ext.get("cy"))
    except (TypeError, ValueError):
        return None


def first_font_size(sp):
    for rPr in sp.iter(q("a", "rPr")):
        sz = rPr.get("sz")
        if sz:
            try:
                return int(sz) / 100.0
            except ValueError:
                pass
    return 18.0


def png_dims(data):
    if not data or len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def jpeg_dims(data):
    if not data or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1] if i + 1 < len(data) else 0
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 >= len(data):
                return None
            h = struct.unpack(">H", data[i + 5:i + 7])[0]
            w = struct.unpack(">H", data[i + 7:i + 9])[0]
            return w, h
        if i + 4 > len(data):
            break
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seglen
    return None


def image_dims(data):
    if data is None:
        return None
    return png_dims(data) or jpeg_dims(data)


def scan_slide(root, rels, z):
    checks = []
    media = []
    if root is None:
        return checks, media
    rid_map = {r["id"]: r for r in rels if r.get("id")}
    for sp in root.iter(q("p", "sp")):
        ext = shape_extent(sp)
        if not ext:
            continue
        body = "".join(t.text or "" for t in sp.iter(q("a", "t")))
        if not body:
            continue
        size_pt = first_font_size(sp)
        est = len(body) * 0.55 * size_pt * 12700  # EMU 启发式估算
        if est > ext[0] * 1.02:
            checks.append({"code": "text_overflow_candidate", "level": "warn",
                           "detail": "est %d EMU > width %d EMU" % (int(est), ext[0]),
                           "snippet": body[:40]})
    for pic in root.iter(q("p", "pic")):
        ext = shape_extent(pic)
        blip = pic.find(q("p", "blipFill") + "/" + q("a", "blip"))
        if blip is None or ext is None:
            continue
        rid = blip.get(q("r", "embed"))
        rel = rid_map.get(rid)
        if not rel:
            checks.append({"code": "pic_unresolved", "level": "warn", "detail": rid})
            continue
        target = os.path.normpath(os.path.join("ppt/slides", rel["target"])).replace(os.sep, "/")
        data = z.read(target) if target in z.namelist() else None
        dims = image_dims(data)
        if dims is None:
            media.append({"path": target, "ok": False, "note": "missing_or_unknown_format"})
            continue
        w, h = dims
        ratio_img = w / h if h else 0
        ratio_ext = ext[0] / ext[1] if ext[1] else 0
        ok = ratio_img > 0 and abs(ratio_img - ratio_ext) / ratio_img < 0.02
        media.append({"path": target, "w": w, "h": h, "ext_cx": ext[0],
                      "ext_cy": ext[1], "aspect_ok": ok})
        if not ok:
            checks.append({"code": "image_stretched", "level": "warn",
                           "detail": "%s img %dx%d vs ext %dx%d" % (target, w, h, ext[0], ext[1])})
    return checks, media


def validate_pptx(path):
    out = {"checks": [], "slide_count": None, "layout_count": None,
           "master_count": None, "notes_count": None, "layouts_used": [],
           "fonts": [], "media": [], "slide_size": None}
    if not path or not os.path.isfile(path):
        out["checks"].append({"code": "pptx_missing", "level": "error", "detail": str(path)})
        return out
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        out["checks"].append({"code": "pptx_not_zip", "level": "error", "detail": str(e)})
        return out
    names = set(z.namelist())
    slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    layouts = sorted(n for n in names if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n))
    masters = sorted(n for n in names if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n))
    notes = sorted(n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n))

    out["slide_count"] = len(slides)
    out["layout_count"] = len(layouts)
    out["master_count"] = len(masters)
    out["notes_count"] = len(notes)

    pres = read_xml(z, "ppt/presentation.xml")
    sld_sz = pres.find(q("p", "sldSz")) if pres is not None else None
    if sld_sz is not None:
        out["slide_size"] = {"cx": sld_sz.get("cx"), "cy": sld_sz.get("cy")}

    fonts = set()
    for m in masters:
        fonts |= collect_fonts(read_xml(z, m))
    for l in layouts:
        fonts |= collect_fonts(read_xml(z, l))

    for slide in slides:
        num = re.search(r"slide(\d+)", slide).group(1)
        rels = parse_rels(z, "ppt/slides/_rels/slide%s.xml.rels" % num)
        lay_rel = first_rel(rels, "/slideLayout")
        if lay_rel:
            lay_target = os.path.normpath(os.path.join("ppt/slides", lay_rel["target"])).replace(os.sep, "/")
            lay_base = os.path.basename(lay_target)
            out["layouts_used"].append({"slide": num, "layout": lay_base})
            lay_num = re.search(r"slideLayout(\d+)", lay_base)
            if lay_num:
                lay_rels = parse_rels(z, "ppt/slideLayouts/_rels/slideLayout%s.xml.rels" % lay_num.group(1))
                mas_rel = first_rel(lay_rels, "/slideMaster")
                out["checks"].append({
                    "code": "layout_inheritance", "level": "ok",
                    "slide": num, "layout": lay_base,
                    "master": os.path.basename(mas_rel["target"]) if mas_rel else None,
                    "ok": bool(mas_rel)})
                if not mas_rel:
                    out["checks"].append({"code": "layout_master_missing", "level": "warn",
                                          "slide": num, "detail": lay_base})
        else:
            out["checks"].append({"code": "no_layout_inheritance", "level": "warn", "slide": num})

        sroot = read_xml(z, slide)
        fonts |= collect_fonts(sroot)
        chk, media = scan_slide(sroot, rels, z)
        out["checks"].extend(chk)
        out["media"].extend(media)

    out["fonts"] = sorted(fonts)
    if len(slides) == 0:
        out["checks"].append({"code": "no_slides", "level": "error", "detail": "no slide parts found"})
    if len(notes) == 0:
        out["checks"].append({"code": "no_notes", "level": "warn", "detail": "no speaker notes found"})
    return out


def cmd_validate(args):
    rr = {"source_files": [], "template_file": args.template, "layouts_used": [],
          "fonts": [], "exports": [], "integrity": {}, "media": [],
          "checks": [], "human_review": []}
    v = validate_pptx(args.pptx)
    rr["integrity"]["pptx"] = {
        "path": args.pptx, "sha256": sha_file(args.pptx),
        "slide_count": v["slide_count"], "layout_count": v["layout_count"],
        "master_count": v["master_count"], "notes_count": v["notes_count"],
        "slide_size": v["slide_size"]}
    rr["layouts_used"] = v["layouts_used"]
    rr["fonts"] = v["fonts"]
    rr["media"] = v["media"]
    rr["checks"].extend(v["checks"])

    for kind, fp in [("pdf", args.pdf)] + [("png", x) for x in (args.png or [])]:
        if not fp:
            continue
        ex = {"path": fp, "kind": kind, "exists": os.path.isfile(fp),
              "size": os.path.getsize(fp) if os.path.isfile(fp) else None,
              "sha256": sha_file(fp), "signature_ok": False}
        if ex["exists"]:
            with open(fp, "rb") as fh:
                head = fh.read(8)
            ex["signature_ok"] = head.startswith(b"%PDF-") if kind == "pdf" else head.startswith(b"\x89PNG")
        rr["exports"].append(ex)
        if not (ex["exists"] and ex["signature_ok"]):
            rr["checks"].append({"code": "%s_invalid_or_missing" % kind, "level": "error", "path": fp})

    # 只能人工核验的边界（确定性检查不覆盖）
    rr["human_review"] = [
        {"item": "text_overflow", "note": "启发式仅标记候选，须对照渲染 PNG 目检"},
        {"item": "font_embedding", "note": "PDF 字体替换/嵌入须目检导出件"},
        {"item": "layout_fidelity", "note": "模板视觉保真须人工逐页核对"},
        {"item": "claim_figure_consistency", "note": "页内数字/图号与源材料须人工逐字核对"},
    ]

    if args.result:
        update_result(args.result, {"render_record": rr},
                      change={"event": "validate", "detail": "pptx=%s slides=%s" % (args.pptx, v["slide_count"])})
    if args.output:
        write_json(os.path.join(args.output, "render_record.json"), rr)
    errs = [c for c in rr["checks"] if c.get("level") == "error"]
    print("validate: slides=%s checks=%d errors=%d fonts=%d media=%d" % (
        v["slide_count"], len(rr["checks"]), len(errs), len(rr["fonts"]), len(rr["media"])))


# ------------------------------------------------------------- evidence ----

def load_texts(patterns):
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    texts = []
    for fp in sorted(set(files)):
        try:
            with open(fp, "r", encoding="utf-8-sig", errors="replace") as f:
                texts.append((fp, f.read()))
        except OSError:
            continue
    return texts


def quote_found(qs, texts):
    if not qs:
        return None
    qs = qs.strip()
    if not qs:
        return None
    for _, t in texts:
        if qs in t:
            return True
    qn = re.sub(r"\s+", " ", qs)
    for _, t in texts:
        if qn in re.sub(r"\s+", " ", t):
            return True
    return False


def plan_pages(plan):
    if isinstance(plan, list):
        return plan
    if isinstance(plan, dict):
        for k in ("page_plan", "pages", "slides"):
            if isinstance(plan.get(k), list):
                return plan[k]
    return []


def cmd_evidence(args):
    plan = load_json(args.plan)
    pages = plan_pages(plan)
    pats = args.txt_glob or ["/app/work/extracted/*.txt", "/app/work/*.txt"]
    texts = load_texts(pats)
    evidence_map = []
    uncertainty = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        idx = page.get("index", page.get("page"))
        claim = page.get("claim") or page.get("title")
        refs = page.get("refs", [])
        norm = []
        located = True
        for r in refs:
            if isinstance(r, str):
                loc, quote = r, None
            elif isinstance(r, dict):
                loc, quote = r.get("locator"), r.get("quote")
            else:
                loc, quote = None, None
            status = "located"
            if not loc:
                status = "missing_locator"
                located = False
            found = quote_found(quote, texts) if quote else None
            if quote and found is False:
                status = "unverified_quote"
                located = False
            norm.append({"locator": loc, "quote": quote, "status": status})
            if status != "located":
                uncertainty.append({"code": "evidence_gap", "page": idx,
                                    "detail": "%s: %s" % (status, quote or loc or "")})
        evidence_status = "located" if located and norm else "partial"
        evidence_map.append({"page": idx, "claim": claim, "refs": norm,
                             "evidence_status": evidence_status})

    if args.output:
        write_json(os.path.join(args.output, "evidence_map.json"), evidence_map)
    if args.result:
        cur = load_json(args.result) or {}
        merged_unc = list(cur.get("uncertainty") or [])
        for u in uncertainty:
            if not any(x.get("code") == u.get("code") and x.get("page") == u.get("page") for x in merged_unc):
                merged_unc.append(u)
        update_result(args.result, {"evidence_map": evidence_map, "uncertainty": merged_unc},
                      change={"event": "evidence", "detail": "pages=%d texts=%d" % (len(evidence_map), len(texts))})
    print("evidence: pages=%d texts=%d" % (len(evidence_map), len(texts)))


# ------------------------------------------------------------- finalize ----

def coerce(item):
    try:
        v = json.loads(item)
        return v
    except Exception:
        return {"detail": item}


def cmd_finalize(args):
    patch = {}
    cur = load_json(args.result) or {}
    if args.status:
        patch["status"] = args.status
    for key, items in (("sources", args.source), ("uncertainty", args.uncertainty),
                       ("pending_human", args.pending)):
        if not items:
            continue
        merged_list = list(cur.get(key) or [])
        for it in items:
            val = coerce(it)
            if key == "uncertainty" and isinstance(val, dict) and "code" not in val:
                val["code"] = "manual"
            merged_list.append(val)
        patch[key] = merged_list
    update_result(args.result, patch,
                  change={"event": "finalize", "detail": args.note or ""})
    r = load_json(args.result) or {}
    print("finalize: status=%s uncertainty=%d pending_human=%d" % (
        r.get("status"), len(r.get("uncertainty") or []), len(r.get("pending_human") or [])))


# ------------------------------------------------------------------ main ----

def build_parser():
    p = argparse.ArgumentParser(description="学术 PPT 模板适配的确定性记账与校验")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="落盘 result.json 骨架")
    a.add_argument("--input", default="/input")
    a.add_argument("--output", default="/app/output")
    a.add_argument("--work", default="/app/work")
    a.set_defaults(fn=cmd_init)

    a = sub.add_parser("validate", help="校验 pptx 与导出件")
    a.add_argument("--pptx")
    a.add_argument("--pdf")
    a.add_argument("--png", action="append")
    a.add_argument("--template")
    a.add_argument("--output", default=".")
    a.add_argument("--result")
    a.set_defaults(fn=cmd_validate)

    a = sub.add_parser("evidence", help="生成逐页证据映射")
    a.add_argument("--plan", required=True)
    a.add_argument("--txt-glob", action="append")
    a.add_argument("--output", default=".")
    a.add_argument("--result")
    a.set_defaults(fn=cmd_evidence)

    a = sub.add_parser("finalize", help="合并收口字段")
    a.add_argument("--result", required=True)
    a.add_argument("--status")
    a.add_argument("--source", action="append")
    a.add_argument("--uncertainty", action="append")
    a.add_argument("--pending", action="append")
    a.add_argument("--note")
    a.set_defaults(fn=cmd_finalize)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.fn(args)
        return 0
    except SystemExit:
        raise
    except Exception as e:  # 兜底：不崩溃，诚实退出 1
        print("internal_error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
