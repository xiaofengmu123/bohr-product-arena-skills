#!/usr/bin/env python3
"""main.py — weekly-skill 的确定性编排器（仅标准库）。

子命令（Agent 在 SKILL.md 指导下分步调用，全部退出码 0=干净、1=阻断、2=仅警告）：
  discover        扫描输入目录，写 input_manifest.json（大小/编码/坏 JSON 容错）
  skeleton        尽快落盘合法 partial result.json（含 case_id + 全字段 + change_log 首条）
  validate-spec   校验 spec.json 契约（实体/关系/证据/locator/quote）
  build-svg       调 build_svg 由 spec.json 生成分层 SVG
  check-result    校验 result.json 完整性 + 产物存在性与 PNG/PDF 魔数
  selftest        运行包级自检（scripts/selftest.py）

默认路径：--input /input  --output /app/output  --work /app/work
所有读写使用 UTF-8（读文件容错 utf-8-sig + errors=replace），写文件 utf-8。
"""
import argparse
import datetime
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATUS_ALLOWED = {"success", "partial", "failed", "ok", "insufficient_evidence", "input_error"}
ENTITY_TYPES = {"gene", "protein", "complex", "family", "cell_type", "small_molecule", "process"}
REL_TYPES = {"activation", "inhibition", "binding", "modification", "transport",
             "conversion", "recruitment"}
STRENGTH_ALLOWED = {"stated", "inferred", "uncertain", "conflict"}

# result.json 的完整字段契约（skeleton 必须全部落盘）
RESULT_CONTRACT = [
    "case_id", "status", "title",
    "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings",
    "sources", "evidence_map", "uncertainty", "pending_human",
    "limitations", "degradations", "errors",
    "change_log", "runtime",
]


# ---------------------------------------------------------------------------
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except OSError as e:
        return None


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def _extract_case_id(data):
    """从 case.json 的多个候选键中稳健取 case_id。"""
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId",
                "armiId", "task_id", "taskId"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


# ---------------------------------------------------------------------------
def cmd_discover(args):
    manifest = {"input_dir": args.input, "files": [], "case_candidates": {},
                "errors": [], "generated_at": _now()}
    root = os.path.abspath(args.input)
    if not os.path.isdir(root):
        manifest["errors"].append(f"input dir 不存在: {root}")
    else:
        MAX = 2 * 1024 * 1024
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                fp = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                rel = os.path.relpath(fp, root)
                entry = {"path": fp, "rel": rel, "bytes": size,
                         "ext": os.path.splitext(fn)[1].lower()}
                if size <= MAX:
                    txt = read_text(fp)
                    entry["preview_chars"] = (len(txt) if txt is not None else 0)
                else:
                    entry["preview_chars"] = 0
                    entry["skipped"] = "oversize"
                manifest["files"].append(entry)
                # 尝试识别 case.json（多种命名）
                if fn.lower() in ("case.json", "input.json", "task.json"):
                    d = read_json(fp)
                    if isinstance(d, dict):
                        cid = _extract_case_id(d)
                        if cid:
                            manifest["case_candidates"][rel] = cid
                    elif d is not None or fn.lower() == "case.json":
                        manifest["errors"].append(f"坏 JSON 或无法解析: {rel}")
    out = os.path.join(args.work, "input_manifest.json")
    write_json(out, manifest)
    print(json.dumps({"manifest": out, "files": len(manifest["files"]),
                      "case_candidates": manifest["case_candidates"],
                      "errors": manifest["errors"]}, ensure_ascii=False))
    return 0


def cmd_skeleton(args):
    """先落盘合法 partial 骨架，任何后续崩溃都不丢 case_id 与字段。"""
    case_path = args.case or os.path.join(args.input, "case.json")
    data = read_json(case_path)
    cid = _extract_case_id(data) if isinstance(data, dict) else None
    if not cid:
        # 回退：扫描 input_manifest 候选
        mf = read_json(os.path.join(args.work, "input_manifest.json"))
        if isinstance(mf, dict) and isinstance(mf.get("case_candidates"), dict):
            cid = next(iter(mf["case_candidates"].values()), None)
    cid = cid or "unknown"

    out = os.path.join(args.output, "result.json")
    existing = read_json(out)
    base = existing if isinstance(existing, dict) else {}
    # 只补缺，不覆盖 Agent 已写入的进展
    for k in RESULT_CONTRACT:
        base.setdefault(k, _empty_for(k))
    base["case_id"] = cid
    if not base.get("status"):
        base["status"] = "partial"
    base.setdefault("runtime", {"started_at": _now(), "finished_at": None,
                                "model_requests_used": None, "tools_used": []})
    log = base.setdefault("change_log", [])
    if not any(x.get("step") == "skeleton" for x in log if isinstance(x, dict)):
        log.append({"time": _now(), "step": "skeleton",
                    "note": "前3分钟落盘 partial 骨架：case_id 已填、全字段齐备，之后增量改进"})
    write_json(out, base)
    print(json.dumps({"result": out, "case_id": cid, "status": base["status"]},
                     ensure_ascii=False))
    return 0


def _empty_for(key):
    if key in ("entities", "relations", "data_mappings", "sources", "evidence_map",
               "uncertainty", "pending_human", "limitations", "degradations", "errors",
               "change_log", "editable_source_files", "export_artifacts"):
        return []
    if key == "legend":
        return {}
    if key == "runtime":
        return {}
    return None


# ---------------------------------------------------------------------------
def _validate_spec(spec):
    errors, warnings = [], []
    if not isinstance(spec, dict):
        return ["spec 不是 JSON 对象"], []
    if not isinstance(spec.get("case_id"), str) or not spec["case_id"].strip():
        errors.append("case_id 缺失或非字符串")
    entities = spec.get("entities")
    if not isinstance(entities, list):
        errors.append("entities 必须是列表")
        entities = []
    relations = spec.get("relations")
    if not isinstance(relations, list):
        errors.append("relations 必须是列表")
        relations = []

    ids = set()
    for e in entities:
        if not isinstance(e, dict):
            errors.append("存在非对象的 entity")
            continue
        eid = e.get("id")
        if not eid:
            errors.append("entity 缺 id")
            continue
        if eid in ids:
            errors.append(f"entity id 重复: {eid}")
        ids.add(eid)
        if not e.get("label"):
            errors.append(f"entity {eid} 缺 label")
        if e.get("type") not in ENTITY_TYPES:
            errors.append(f"entity {eid} 的 type 非法: {e.get('type')!r}")
        ev = e.get("evidence")
        if ev is None:
            warnings.append(f"entity {eid} 尚无 evidence（建议最终补齐）")
        elif not _check_evidence(ev):
            errors.append(f"entity {eid} 的 evidence 缺 locator/quote")

    for r in relations:
        if not isinstance(r, dict):
            errors.append("存在非对象的 relation")
            continue
        rid = r.get("id") or f"{r.get('source')}->{r.get('target')}"
        if r.get("type") not in REL_TYPES:
            errors.append(f"relation {rid} 的 type 非法: {r.get('type')!r}")
        for end in ("source", "target"):
            if r.get(end) not in ids:
                errors.append(f"relation {rid} 的 {end}={r.get(end)!r} 无对应 entity")
        if r.get("evidence_strength") not in STRENGTH_ALLOWED:
            warnings.append(f"relation {rid} 的 evidence_strength 非法，将按 stated 渲染")
        ev = r.get("evidence")
        if ev is None:
            warnings.append(f"relation {rid} 尚无 evidence（建议最终补齐）")
        elif not _check_evidence(ev):
            errors.append(f"relation {rid} 的 evidence 缺 locator/quote")

    for i, m in enumerate(spec.get("data_mappings") or []):
        if not isinstance(m, dict):
            errors.append(f"data_mappings[{i}] 非对象")
        elif not m.get("visual") or not m.get("encodes"):
            errors.append(f"data_mappings[{i}] 缺 visual/encodes")
    return errors, warnings


def _check_evidence(ev):
    if not isinstance(ev, list) or not ev:
        return True  # 空列表视为未填写，仅警告
    return all(isinstance(x, dict) and x.get("locator") and x.get("quote") for x in ev)


def cmd_validate_spec(args):
    spec = read_json(args.spec)
    if spec is None:
        print("ERROR: 无法读取 spec.json（不存在或坏 JSON）", file=sys.stderr)
        return 1
    errors, warnings = _validate_spec(spec)
    report = {"spec": os.path.abspath(args.spec), "errors": errors, "warnings": warnings,
              "checked_at": _now()}
    out = args.report or os.path.join(os.path.dirname(os.path.abspath(args.spec)) or ".",
                                      "spec.validation_report.json")
    write_json(out, report)
    print(json.dumps({"errors": len(errors), "warnings": len(warnings), "report": out},
                     ensure_ascii=False))
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    return 1 if errors else (2 if warnings else 0)


def cmd_build_svg(args):
    import build_svg  # noqa: E402
    spec = read_json(args.spec)
    if spec is None:
        print("ERROR: 无法读取 spec.json", file=sys.stderr)
        return 1
    out = args.out or os.path.join(args.output, "mechanism.svg")
    svg, warnings = build_svg.build(spec)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    if args.report:
        write_json(args.report, {"svg": os.path.abspath(out), "warnings": warnings})
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    print(f"built {out} ({len(svg)} bytes, {len(warnings)} warnings)")
    return 0


# ---------------------------------------------------------------------------
def _png_dims(path):
    try:
        with open(path, "rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return None, "not_png"
            l, = struct.unpack(">I", f.read(4))
            typ = f.read(4)
            if typ != b"IHDR" or l != 13:
                return None, "no_ihdr"
            w, h = struct.unpack(">II", f.read(8))
            return (w, h), "ok"
    except OSError:
        return None, "unreadable"


def _check_artifact(entry, errors, warnings):
    if not isinstance(entry, dict):
        errors.append("export_artifacts 中存在非对象项")
        return
    p = entry.get("path")
    if not p:
        errors.append("artifact 缺 path")
        return
    if not os.path.exists(p):
        errors.append(f"artifact 不存在: {p}")
        return
    ext = os.path.splitext(p)[1].lower()
    try:
        with open(p, "rb") as f:
            head = f.read(8)
    except OSError:
        errors.append(f"artifact 不可读: {p}")
        return
    if ext == ".png":
        dims, st = _png_dims(p)
        if st != "ok":
            errors.append(f"PNG 魔数/尺寸校验失败: {p} ({st})")
        else:
            entry.setdefault("width_px", dims[0])
            entry.setdefault("height_px", dims[1])
    elif ext == ".pdf":
        if not head.startswith(b"%PDF-"):
            errors.append(f"PDF 魔数校验失败: {p}")
    elif ext == ".svg":
        if b"<svg" not in head and b"<?xml" not in head:
            warnings.append(f"SVG 首部未含预期标记: {p}")
    else:
        warnings.append(f"未知导出格式: {ext}（可接受，但需人工核对）")


def cmd_check_result(args):
    out_dir = args.output_dir or getattr(args, "output", None) or "/app/output"
    rp = args.result or os.path.join(out_dir, "result.json")
    res = read_json(rp)
    if res is None:
        print("ERROR: result.json 不存在或坏 JSON", file=sys.stderr)
        return 1
    errors, warnings = [], []
    for k in RESULT_CONTRACT:
        if k not in res:
            errors.append(f"result.json 缺字段: {k}")
    cid = res.get("case_id")
    if not isinstance(cid, str) or not cid.strip():
        errors.append("case_id 缺失")
    if res.get("status") not in STATUS_ALLOWED:
        errors.append(f"status 非法: {res.get('status')!r}")
    if not isinstance(res.get("change_log"), list) or not res["change_log"]:
        errors.append("change_log 缺失或为空（首条应为骨架落盘）")
    else:
        if not any(x.get("step") == "skeleton" for x in res["change_log"] if isinstance(x, dict)):
            warnings.append("change_log 未发现 step=skeleton 的首条记录")

    src_files = res.get("editable_source_files") or []
    artifacts = res.get("export_artifacts") or []
    status = res.get("status")
    if status == "success":
        if not src_files:
            errors.append("status=success 但 editable_source_files 为空")
        if not artifacts:
            errors.append("status=success 但 export_artifacts 为空")
    for e in src_files:
        p = e.get("path") if isinstance(e, dict) else None
        if not p or not os.path.exists(p):
            errors.append(f"源文件不存在: {p}")
    for a in artifacts:
        _check_artifact(a, errors, warnings)

    # 证据覆盖：success 时关键元素缺证据视为阻断
    ents, rels = res.get("entities") or [], res.get("relations") or []
    missing = []
    for e in ents:
        if isinstance(e, dict) and not (e.get("evidence") or e.get("sources")):
            missing.append(e.get("id") or e.get("label") or "?")
    for r in rels:
        if isinstance(r, dict) and not (r.get("evidence") or r.get("sources")):
            missing.append(r.get("id") or "?")
    if missing:
        if status == "success":
            errors.append(f"status=success 但 {len(missing)} 个关键元素缺证据: {missing[:6]}")
        else:
            warnings.append(f"{len(missing)} 个关键元素缺证据: {missing[:6]}")

    if not isinstance(res.get("evidence_map"), list):
        errors.append("evidence_map 缺失或非列表")

    report = {"result": os.path.abspath(rp), "errors": errors, "warnings": warnings,
              "checked_at": _now()}
    if args.report:
        write_json(args.report, report)
    print(json.dumps({"errors": len(errors), "warnings": len(warnings)}, ensure_ascii=False))
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    return 1 if errors else (2 if warnings else 0)


def cmd_selftest(args):
    import runpy
    st = os.path.join(HERE, "selftest.py")
    if not os.path.exists(st):
        print("ERROR: selftest.py 缺失", file=sys.stderr)
        return 1
    runpy.run_path(st, run_name="__main__")
    return 0


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="weekly-skill 编排器")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("discover", help="扫描输入目录")
    sp.set_defaults(fn=cmd_discover)

    sp = sub.add_parser("skeleton", help="落盘 partial 骨架")
    sp.add_argument("--case", help="case.json 路径（默认 /input/case.json）")
    sp.set_defaults(fn=cmd_skeleton)

    sp = sub.add_parser("validate-spec", help="校验 spec.json")
    sp.add_argument("spec")
    sp.add_argument("--report")
    sp.set_defaults(fn=cmd_validate_spec)

    sp = sub.add_parser("build-svg", help="spec.json → SVG")
    sp.add_argument("spec")
    sp.add_argument("--out", help="输出 SVG 路径（默认 <output>/mechanism.svg）")
    sp.add_argument("--report")
    sp.set_defaults(fn=cmd_build_svg)

    sp = sub.add_parser("check-result", help="校验 result.json 与产物")
    sp.add_argument("--result")
    sp.add_argument("--output-dir")
    sp.add_argument("--report")
    sp.set_defaults(fn=cmd_check_result)

    sp = sub.add_parser("selftest", help="包级自检")
    sp.set_defaults(fn=cmd_selftest)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
