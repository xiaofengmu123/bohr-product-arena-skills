#!/usr/bin/env python3
"""Self-test and determinism checker for sci-figure-editor.

Read-only except for writing the determinism manifest under /app/work.
Exit code 0 = all hard checks pass, 1 = at least one failure.

Checks the contract (contract-checklist.md), artifact validity, provenance
evidence completeness, and the determinism guarantee: same inputs -> same
scientific content and byte-identical SVG.
"""
import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import spec_to_svg  # noqa: E402

# Deterministic content core: everything whose identity must not change when
# the same inputs are re-run. Excluded: wall-clock runtime and change_log
# timestamps (inherently varying) and transient notes in errors/degradations.
CORE_KEYS = (
    "case_id", "status", "editable_source_files", "export_files",
    "provenance", "sources", "uncertainty", "pending_human",
)
MANIFEST = ".determinism.json"
MAX_HASH_BYTES = 64 * 1024 * 1024


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    with open(path, "rb") as fh:
        data = fh.read(MAX_HASH_BYTES + 1)
    if len(data) > MAX_HASH_BYTES:
        return None
    return sha256_bytes(data)


def input_hash(input_dir):
    entries = []
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            p = os.path.join(root, name)
            try:
                h = file_hash(p)
            except OSError:
                h = None
            if h is not None:
                rel = os.path.relpath(p, input_dir)
                entries.append((rel, h))
    buf = "\n".join("%s %s" % e for e in entries).encode("utf-8")
    return sha256_bytes(buf)


def core_hash(result):
    core = {k: result.get(k) for k in CORE_KEYS}
    return sha256_bytes(json.dumps(core, ensure_ascii=False, sort_keys=True)
                        .encode("utf-8"))


def check_svg_wellformed(svg_path):
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except Exception as e:
        return False, "SVG 不可解析: %s" % e
    tags = [el.tag.split("}")[-1] for el in root.iter()]
    if root.tag.split("}")[-1] != "svg":
        return False, "根元素不是 <svg>"
    if "g" not in tags:
        return False, "缺少 <g> 图层元素"
    if "text" not in tags:
        return False, "缺少可编辑 <text> 元素"
    return True, "ok"


def render_to_bytes(spec):
    colors = spec_to_svg.assign_colors(spec)
    pos = spec_to_svg.layout(spec)
    return spec_to_svg.render(spec, colors, pos).encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--input-dir", default="/input")
    a = ap.parse_args(argv)

    checks = []  # (label, ok, detail)

    def add(label, ok, detail=""):
        checks.append((label, ok, detail))

    result_path = os.path.join(a.output_dir, "result.json")
    if not os.path.isfile(result_path):
        add("result.json 存在", False, result_path)
        _report(checks, 1)
        return 1
    try:
        result = read_json(result_path)
        add("result.json 可解析", True, result_path)
    except Exception as e:
        add("result.json 可解析", False, str(e))
        _report(checks, 1)
        return 1

    add("含 case_id 且非空", bool(result.get("case_id")),
        str(result.get("case_id")))
    add("含 status 且合法", result.get("status") in (
        "complete", "partial", "failed", "input_error",
        "insufficient_evidence"), str(result.get("status")))

    # Artifact existence
    for key in ("editable_source_files", "export_files"):
        for item in result.get(key, []):
            p = os.path.join(a.output_dir, item.get("path", ""))
            add("产物存在: %s" % item.get("path"), os.path.isfile(p), p)

    # SVG well-formed
    for item in result.get("editable_source_files", []):
        p = os.path.join(a.output_dir, item.get("path", ""))
        if p.endswith(".svg") and os.path.isfile(p):
            ok, detail = check_svg_wellformed(p)
            add("SVG 良构+分层+可编辑: %s" % item.get("path"), ok, detail)

    # Provenance evidence completeness
    prov = result.get("provenance", [])
    bad = [p.get("element") for p in prov
           if not (p.get("locator") and p.get("quote"))]
    add("provenance 逐条含 locator+quote", not bad,
        "缺失: %s" % (",".join(map(str, bad)) if bad else "无"))

    # Legend <-> data consistency from the spec
    spec_path = os.path.join(a.work_dir, "figure_spec.json")
    if os.path.isfile(spec_path):
        try:
            spec = read_json(spec_path)
            spec_to_svg.validate_spec(spec)
            add("figure_spec.json 校验通过", True, spec_path)
            legend = spec.get("legend") or {}
            cats = {e.get("category") for e in spec.get("entities", [])
                    if e.get("category")}
            items = legend.get("items", [])
            item_cats = {i.get("category") or i.get("id") for i in items}
            add("图例分类与实体分类一致", cats == item_cats,
                "实体分类=%s 图例分类=%s" % (sorted(cats), sorted(item_cats)))
        except Exception as e:
            add("figure_spec.json 校验通过", False, str(e))

    # Honest status
    if result.get("status") in ("partial", "failed"):
        has_note = bool(result.get("uncertainty") or result.get("degradations")
                        or result.get("errors"))
        add("partial/failed 时标明 limits", has_note,
            "uncertainty=%d degradations=%d errors=%d" % (
                len(result.get("uncertainty", [])),
                len(result.get("degradations", [])),
                len(result.get("errors", []))))

    # Determinism: (a) render twice -> byte-identical; (b) core hash stability.
    if os.path.isfile(spec_path):
        try:
            spec = read_json(spec_path)
            spec_to_svg.validate_spec(spec)
            b1 = render_to_bytes(spec)
            b2 = render_to_bytes(spec)
            add("同 spec 两次渲染 SVG 字节一致", b1 == b2,
                "sha256=%s" % sha256_bytes(b1))
        except Exception as e:
            add("同 spec 两次渲染 SVG 字节一致", False, str(e))

    ih = input_hash(a.input_dir)
    ch = core_hash(result)
    manifest_path = os.path.join(a.work_dir, MANIFEST)
    prev = None
    if os.path.isfile(manifest_path):
        try:
            prev = read_json(manifest_path)
        except Exception:
            prev = None
    if prev:
        same_input = prev.get("input_hash") == ih
        same_core = prev.get("core_hash") == ch
        add("确定性：同输入两次科学内容核心一致",
            not (same_input and not same_core),
            "输入一致=%s 核心一致=%s" % (same_input, same_core))
    else:
        add("确定性：首次运行建立基线", True, "input_hash=%s" % ih[:12])
    try:
        write_json_manifest(manifest_path, {
            "input_hash": ih, "core_hash": ch,
        })
    except OSError as e:
        add("确定性清单可写", False, str(e))

    failures = [c for c in checks if not c[1]]
    _report(checks, 1 if failures else 0)
    return 1 if failures else 0


def write_json_manifest(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _report(checks, code):
    for label, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        line = "[%s] %s" % (mark, label)
        if detail:
            line += "  -- " + str(detail)
        print(line)
    print("SUMMARY: %d pass, %d fail, exit=%d" % (
        sum(1 for c in checks if c[1]), sum(1 for c in checks if not c[1]),
        code))
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
