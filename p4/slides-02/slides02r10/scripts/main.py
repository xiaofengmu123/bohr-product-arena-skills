#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deck-spec-builder 运行时辅助脚本（纯 Python 标准库）。

设计取舍：本包采用“规格先行”架构，`deck_spec.json` 是内容事实源，
渲染与校验都消费同一份中间表示；`result.json` 只负责交付契约，
`evidence_map.json`/`change_log.json` 只负责证据与过程。脚本不依赖
python-pptx / soffice，PPTX 读写仅用 zipfile + xml.etree。

子命令：
  scaffold           根据 case.json 落一版 partial 结果骨架
  inventory          盘点输入目录并写 work/inventory.json
  spec               校验 deck_spec.json 的结构与证据字段
  bind               把 deck_spec 的页级证据合并进 evidence_map.json
  pptx-info <file>   读回 .pptx 的文本、占位符、母版/版式统计
  check              对照契约与产物做规则校验，写 work/self_check.json
  finalize           标记终版状态并写 finalized_at
  version            把指定文件复制进版本目录并写 version_records.json
  record             增量写入 change_log.json（step/action/status/reason）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET

DEFAULT_INPUT = os.environ.get("INPUT_DIR", "/input")
DEFAULT_WORK = os.environ.get("WORK_DIR", "/app/work")
DEFAULT_OUTPUT = os.environ.get("OUTPUT_DIR", "/app/output")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 运行时题目常见交付字段；case.json 的 output_contract 优先，此处仅为兜底。
DEFAULT_CONTRACT_FIELDS = [
    "presentation_file", "preview_files", "speaker_notes",
    "page_plan", "evidence_map", "version_records",
]

# 页级叙事角色白名单（缺失或未知时校验报缺口，不中断）。
KNOWN_ROLES = {
    "title", "hook", "context", "problem", "motivation", "gap",
    "method", "result", "analysis", "comparison", "limitation",
    "conclusion", "takeaway", "appendix", "agenda", "thanks",
}

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _load_json(path: str):
    """读取 JSON；BOM/坏编码容错，坏 JSON 返回 None 并记录 degradation。"""
    try:
        txt = _read_text(path)
    except OSError as e:
        return None, f"read error: {e}"
    try:
        return json.loads(txt), None
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"


def _dump_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_mutable(path: str, default):
    obj, err = _load_json(path)
    if err is not None:
        return default, err
    return (obj if isinstance(obj, dict) else default), None


def _changelog_path(work: str) -> str:
    return os.path.join(work, "change_log.json")


def append_change(work: str, entry: dict) -> str:
    """把一条变更记录写入 work/change_log.json，返回记录路径。"""
    path = _changelog_path(work)
    log, _err = _load_mutable(path, {"entries": []})
    log.setdefault("entries", [])
    entry.setdefault("time", utcnow())
    entry.setdefault("step", "")
    entry.setdefault("action", "")
    entry.setdefault("status", "ok")
    entry.setdefault("reason", "")
    log["entries"].append(entry)
    _dump_json(path, log)
    return path


def _contract_fields(case_path: str) -> list[str]:
    case, err = _load_json(case_path)
    if err is not None:
        return list(DEFAULT_CONTRACT_FIELDS)
    oc = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(oc, dict):
        names = [k for k in oc.keys() if k not in {"case_id", "status", "sources",
                                                   "uncertainty", "pending_human", "change_log"}]
        return names if names else list(DEFAULT_CONTRACT_FIELDS)
    return list(DEFAULT_CONTRACT_FIELDS)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def cmd_scaffold(args) -> int:
    case_path = os.path.join(args.input_dir, "case.json")
    case, err = _load_json(case_path)
    case_id = ""
    degradations = []
    if err is not None:
        degradations.append(f"case.json: {err}")
        case = {}
    if isinstance(case, dict):
        case_id = str(case.get("case_id") or case.get("id") or "")

    fields = _contract_fields(case_path)
    result = {"case_id": case_id, "status": "partial"}
    for name in fields:
        # 数组型字段给空数组而非 null，便于后续增量填充与校验。
        result[name] = [] if name.endswith(("_files", "_records", "_list")) else None
    result["sources"] = []
    result["uncertainty"] = {
        "unresolved": [],
        "missing_artifacts": [],
        "notes": "骨架阶段：尚未盘点材料与生成产物。",
    }
    result["pending_human"] = []
    result["change_log"] = [{
        "time": utcnow(), "step": "scaffold", "action": "write result skeleton",
        "status": "partial", "reason": "前 3 分钟先落盘可增量更新的合法结果骨架",
    }]

    _ensure_dir(args.work_dir)
    _ensure_dir(args.output_dir)
    out_path = os.path.join(args.output_dir, "result.json")
    _dump_json(out_path, result)
    summary = {
        "case_id": case_id,
        "input_dir": args.input_dir,
        "contract_fields": fields,
        "degradations": degradations,
        "scaffolded_at": utcnow(),
    }
    _dump_json(os.path.join(args.work_dir, "case_summary.json"), summary)
    _dump_json(_changelog_path(args.work_dir), {"entries": result["change_log"]})
    print(f"scaffold: {out_path} status=partial fields={len(fields)}")
    for d in degradations:
        print(f"degradation: {d}")
    return 0


def _file_size_limit(path: str, limit: int = 2 * 1024 * 1024) -> bool:
    try:
        return os.path.getsize(path) <= limit
    except OSError:
        return False


def _inventory_files(root: str) -> list[dict]:
    items = []
    if not os.path.isdir(root):
        return items
    for base, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            items.append({
                "path": p, "rel": rel, "ext": ext, "size": size,
                "within_limit": _file_size_limit(p),
            })
    return sorted(items, key=lambda x: x["rel"])


def cmd_inventory(args) -> int:
    items = _inventory_files(args.input_dir)
    pdfs = [i for i in items if i["ext"] == ".pdf"]
    decks = [i for i in items if i["ext"] in {".pptx", ".ppt", ".odp", ".key"}]
    data = [i for i in items if i["ext"] in {".csv", ".xlsx", ".xls", ".tsv", ".json"}]
    images = [i for i in items if i["ext"] in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".emf", ".wmf"}]
    _ensure_dir(args.work_dir)
    inv = {
        "generated_at": utcnow(),
        "input_dir": args.input_dir,
        "counts": {
            "total": len(items), "pdf": len(pdfs), "deck": len(decks),
            "data": len(data), "image": len(images),
        },
        "files": items,
        "hint": "PDF 用 pdf-text-tool/pdftotext 提正文；图页用 pdf-render-tool/pdftoppm；扫描页用 ocr-tool。",
    }
    out = os.path.join(args.work_dir, "inventory.json")
    _dump_json(out, inv)
    print(f"inventory: {out} total={len(items)} pdf={len(pdfs)} deck={len(decks)}")
    return 0


def _spec_fields(pages: list, issues: list, path: str) -> None:
    if not isinstance(pages, list) or not pages:
        issues.append(f"{path}: pages 为空或非数组")
        return
    for i, pg in enumerate(pages):
        loc = f"{path}:pages[{i}]"
        if not isinstance(pg, dict):
            issues.append(f"{loc}: 非对象")
            continue
        for key in ("role", "claim"):
            if not isinstance(pg.get(key), str) or not pg[key].strip():
                issues.append(f"{loc}: 缺少 {key}")
        role = pg.get("role")
        if role and str(role).lower() not in KNOWN_ROLES:
            issues.append(f"{loc}: 未知 role={role}")
        if "notes" not in pg:
            issues.append(f"{loc}: 缺少 notes（讲稿备注）")
        # 证据字段：claim 必须可溯源。
        locator = pg.get("locator")
        quote = pg.get("quote")
        if not locator or not quote:
            issues.append(f"{loc}: 证据不完整（需 locator + quote）")


def cmd_spec(args) -> int:
    spec_path = args.spec if args.spec else os.path.join(args.work_dir, "deck_spec.json")
    spec, err = _load_json(spec_path)
    issues = []
    if err is not None:
        issues.append(f"deck_spec.json: {err}")
        spec = {}
    if not isinstance(spec, dict):
        issues.append("deck_spec.json: 顶层应为对象")
        spec = {}
    pages = spec.get("pages")
    _spec_fields(pages, issues, "deck_spec.json")
    for field in ("title", "audience", "template", "slide_count"):
        if field not in spec:
            issues.append(f"deck_spec.json: 缺少 {field}")
    report = {
        "checked_at": utcnow(),
        "spec": spec_path,
        "pages": len(pages) if isinstance(pages, list) else 0,
        "issues": issues,
        "ok": not issues,
    }
    _dump_json(os.path.join(args.work_dir, "spec_check.json"), report)
    print(f"spec: pages={report['pages']} ok={report['ok']} issues={len(issues)}")
    for it in issues:
        print(f"issue: {it}")
    return 0 if report["ok"] else 1


def cmd_bind(args) -> int:
    spec_path = os.path.join(args.work_dir, "deck_spec.json")
    ev_path = os.path.join(args.work_dir, "evidence_map.json")
    spec, err = _load_json(spec_path)
    ev, _e2 = _load_mutable(ev_path, {"pages": []})
    if err is not None:
        print(f"bind error: {err}")
        return 1
    pages = spec.get("pages") if isinstance(spec, dict) else []
    bound = []
    for i, pg in enumerate(pages if isinstance(pages, list) else []):
        if not isinstance(pg, dict):
            continue
        bound.append({
            "page": i + 1,
            "role": pg.get("role"),
            "claim": pg.get("claim"),
            "locator": pg.get("locator"),
            "quote": pg.get("quote"),
            "figure": pg.get("figure") or pg.get("figures") or [],
            "table": pg.get("table") or pg.get("tables") or [],
        })
    ev["pages"] = bound
    _dump_json(ev_path, ev)
    print(f"bind: {ev_path} pages={len(bound)}")
    return 0


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _ppt_texts(pptx_path: str):
    texts: list[str] = []
    placeholders: list[str] = []
    layouts: set[str] = set()
    masters: set[str] = set()
    slides_count = 0
    try:
        with zipfile.ZipFile(pptx_path) as z:
            names = z.namelist()
            slides_count = len([n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)])
            for n in names:
                if re.match(r"^ppt/slideLayouts/slideLayout\d+\.xml$", n):
                    layouts.add(n)
                if re.match(r"^ppt/slideMasters/slideMaster\d+\.xml$", n):
                    masters.add(n)
            for n in sorted(names):
                if re.match(r"^ppt/slides/slide\d+\.xml$", n):
                    try:
                        root = ET.fromstring(z.read(n))
                    except ET.ParseError as e:
                        texts.append(f"[parse error {n}: {e}]")
                        continue
                    for t in root.iter():
                        if _local_name(t.tag) == "t":
                            if t.text:
                                texts.append(t.text)
                        if _local_name(t.tag) == "ph":
                            ph_type = t.get("type")
                            ph_idx = t.get("idx")
                            if ph_type:
                                placeholders.append(f"{n}:{ph_type}:{ph_idx}")
    except (zipfile.BadZipFile, OSError) as e:
        return {"error": str(e)}
    return {
        "slides_count": slides_count,
        "layout_count": len(layouts),
        "master_count": len(masters),
        "placeholders": placeholders,
        "text_runs": len(texts),
        "text_sample": "\n".join(texts[:200]),
    }


def cmd_pptx_info(args) -> int:
    path = args.pptx
    if not os.path.exists(path):
        print(f"pptx-info: file not found: {path}")
        return 1
    info = _ppt_texts(path)
    out = {"checked_at": utcnow(), "path": path, **info}
    _dump_json(os.path.join(args.work_dir, "pptx_info.json"), out)
    print(json.dumps({k: out[k] for k in ("slides_count", "layout_count", "master_count", "text_runs", "error") if k in out}, ensure_ascii=False))
    return 1 if "error" in out else 0


def _contract_coverage(result: dict, fields: list[str]) -> list[str]:
    return [f for f in fields if f not in result]


def cmd_check(args) -> int:
    _ensure_dir(args.work_dir)
    result_path = os.path.join(args.output_dir, "result.json")
    result, err = _load_json(result_path)
    gaps: list[str] = []
    degradations: list[str] = []
    if err is not None:
        gaps.append(f"result.json: {err}")
        result = {}
        degradations.append(f"result.json: {err}")
    if not isinstance(result, dict):
        gaps.append("result.json 顶层不是对象")
        result = {}

    case_path = os.path.join(args.input_dir, "case.json")
    fields = _contract_fields(case_path)
    gaps.extend(f"缺少契约字段: {f}" for f in _contract_coverage(result, fields))
    if not result.get("case_id"):
        gaps.append("缺少 case_id")
    if result.get("status") not in {"complete", "partial", "failed"}:
        gaps.append("status 不在 partial/complete/failed 中")

    # 主产物可打开性/存在性检查。
    main = result.get("presentation_file")
    if isinstance(main, str) and main:
        main_path = main if os.path.isabs(main) else os.path.join(args.output_dir, main)
        if not os.path.exists(main_path):
            gaps.append(f"presentation_file 不存在: {main_path}")
        elif main_path.lower().endswith(".pptx"):
            info = _ppt_texts(main_path)
            if "error" in info:
                gaps.append(f"presentation_file 无法作为 zip/pptx 打开: {info['error']}")
            elif info.get("slides_count", 0) == 0:
                gaps.append("presentation_file 无幻灯片页")
    else:
        gaps.append("presentation_file 为空或未产出")

    previews = result.get("preview_files")
    if isinstance(previews, list):
        missing = []
        for p in previews:
            pp = p if os.path.isabs(p) else os.path.join(args.output_dir, p)
            if not os.path.exists(pp):
                missing.append(p)
        if missing:
            gaps.append(f"preview_files 缺失 {len(missing)} 项: {missing[:5]}")
    else:
        gaps.append("preview_files 为空")

    # 证据映射文件存在性。
    ev_path = os.path.join(args.work_dir, "evidence_map.json")
    if not os.path.exists(ev_path):
        gaps.append("evidence_map.json 未生成")
    else:
        ev, _e = _load_json(ev_path)
        pages = ev.get("pages", []) if isinstance(ev, dict) else []
        nq = sum(1 for p in pages if isinstance(p, dict) and p.get("quote") and p.get("locator"))
        gaps.append(f"evidence_map 中 {len(pages) - nq} 页缺少 locator/quote")

    spec_path = os.path.join(args.work_dir, "deck_spec.json")
    if not os.path.exists(spec_path):
        gaps.append("deck_spec.json 未生成")
    else:
        spec, _e2 = _load_json(spec_path)
        _spec_fields(spec.get("pages") if isinstance(spec, dict) else [], gaps, "deck_spec.json")

    report = {
        "checked_at": utcnow(),
        "ok": not gaps,
        "gaps": gaps,
        "degradations": degradations,
        "result_path": result_path,
    }
    _dump_json(os.path.join(args.work_dir, "self_check.json"), report)
    print(f"check: ok={report['ok']} gaps={len(gaps)}")
    for g in gaps:
        print(f"gap: {g}")
    return 0 if report["ok"] else 1


def cmd_finalize(args) -> int:
    result_path = os.path.join(args.output_dir, "result.json")
    result, err = _load_json(result_path)
    if err is not None:
        print(f"finalize error: {err}")
        return 1
    if not isinstance(result, dict):
        print("finalize error: result.json 不是对象")
        return 1
    result["status"] = args.status
    result["finalized_at"] = utcnow()
    append_change(args.work_dir, {
        "step": "finalize", "action": f"finalize status={args.status}",
        "status": args.status, "reason": args.reason or "终检后标记交付状态",
    })
    _dump_json(result_path, result)
    print(f"finalize: {result_path} status={args.status}")
    return 0


def cmd_version(args) -> int:
    _ensure_dir(args.work_dir)
    versions_dir = os.path.join(args.work_dir, "versions")
    _ensure_dir(versions_dir)
    rec_path = os.path.join(args.work_dir, "version_records.json")
    rec, _e = _load_mutable(rec_path, {"entries": []})
    entries = rec.setdefault("entries", [])
    seq = len(entries) + 1
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    entry = {
        "id": f"v{seq}", "time": utcnow(), "note": args.note or "",
        "stamp": stamp, "files": [],
    }
    for src in args.files or []:
        if not os.path.exists(src):
            print(f"version: skip missing {src}")
            continue
        base = os.path.basename(src)
        dst = os.path.join(versions_dir, f"{entry['id']}_{base}")
        shutil.copy2(src, dst)
        entry["files"].append(os.path.relpath(dst, args.work_dir).replace("\\", "/"))
    entries.append(entry)
    _dump_json(rec_path, rec)
    print(f"version: {entry['id']} files={len(entry['files'])}")
    return 0


def cmd_record(args) -> int:
    path = append_change(args.work_dir, {
        "step": args.step, "action": args.action, "status": args.status,
        "reason": args.reason or "",
    })
    print(f"record: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="main.py", description="deck-spec-builder 运行时辅助脚本")
    p.add_argument("--input-dir", dest="input_dir", default=DEFAULT_INPUT)
    p.add_argument("--work-dir", dest="work_dir", default=DEFAULT_WORK)
    p.add_argument("--output-dir", dest="output_dir", default=DEFAULT_OUTPUT)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scaffold", help="落 partial 结果骨架")
    s.set_defaults(func=cmd_scaffold)

    i = sub.add_parser("inventory", help="盘点输入目录")
    i.set_defaults(func=cmd_inventory)

    sp = sub.add_parser("spec", help="校验 deck_spec.json")
    sp.add_argument("--spec", default=None, help="deck_spec.json 路径覆盖")
    sp.set_defaults(func=cmd_spec)

    b = sub.add_parser("bind", help="把页级证据合并进 evidence_map.json")
    b.set_defaults(func=cmd_bind)

    pi = sub.add_parser("pptx-info", help="读回 pptx 文本/占位符/母版统计")
    pi.add_argument("pptx")
    pi.set_defaults(func=cmd_pptx_info)

    c = sub.add_parser("check", help="规则校验并写 self_check.json")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("finalize", help="标记终版状态")
    f.add_argument("--status", required=True, choices=["partial", "complete", "failed"])
    f.add_argument("--reason", default="")
    f.set_defaults(func=cmd_finalize)

    v = sub.add_parser("version", help="版本快照")
    v.add_argument("--note", default="")
    v.add_argument("files", nargs="*")
    v.set_defaults(func=cmd_version)

    r = sub.add_parser("record", help="追加 change_log")
    r.add_argument("--step", default="")
    r.add_argument("--action", required=True)
    r.add_argument("--status", default="ok")
    r.add_argument("--reason", default="")
    r.set_defaults(func=cmd_record)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
