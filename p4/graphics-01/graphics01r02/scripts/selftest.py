#!/usr/bin/env python3
"""Contract + artifact self-check for sci-figure-editor.

`--result` mode validates a result.json against the output contract and checks
that referenced artifacts exist and are parseable (SVG via XML, PNG/PDF via
magic bytes). `--selftest` mode runs the renderer on a minimal demo spec and a
deliberately broken result to prove the checker actually detects problems.

Standard library only. Exit code 0 = all critical checks pass, 1 = issues.
"""
import argparse
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

REQUIRED_FIELDS = [
    "case_id", "status", "editable_source_files", "export_files",
    "provenance", "sources", "uncertainty", "change_log",
]
VALID_STATUS = {"complete", "partial", "failed"}
# chart is optional (only present when the spec carries a chart object)
SVG_LAYERS = {"background", "groups", "edges", "nodes", "legend", "annotations"}


def _load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except FileNotFoundError as e:
        return None, f"missing file: {path}"
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"unparseable {path}: {e}"


def _svg_ok(path):
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        return False, [f"SVG not parseable: {e}"]
    issues = []
    ns = "{http://www.w3.org/2000/svg}"
    texts = root.findall(f".//{ns}text")
    groups = root.findall(f".//{ns}g")
    if not texts:
        issues.append("SVG has no editable <text> elements")
    if len(groups) < 2:
        issues.append("SVG has fewer than 2 <g> layers")
    layer_ids = {g.get("id") for g in groups}
    missing = SVG_LAYERS - layer_ids
    if missing:
        issues.append(f"SVG missing expected layers: {sorted(missing)}")
    return (not issues), issues


def _magic_ok(path, kind):
    if not os.path.exists(path):
        return False, [f"missing artifact: {path}"]
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError as e:
        return False, [f"cannot read {path}: {e}"]
    if kind == "png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
        return False, [f"{path} is not a valid PNG (bad magic)"]
    if kind == "pdf" and not head.startswith(b"%PDF-"):
        return False, [f"{path} is not a valid PDF (bad magic)"]
    return True, []


def check(result_path, output_dir):
    """Return (ok: bool, report: dict). Never raises."""
    report = {"result_path": result_path, "output_dir": output_dir,
              "checks": [], "errors": [], "warnings": []}
    data, err = _load(result_path)
    if err:
        report["errors"].append(err)
        return False, report

    # required top-level fields
    for field in REQUIRED_FIELDS:
        ok = field in data
        report["checks"].append({"check": f"field:{field}", "ok": ok,
                                 "detail": "present" if ok else "missing"})
        if not ok:
            report["errors"].append(f"missing required field: {field}")

    case_id = data.get("case_id")
    if not case_id or case_id == "unknown":
        report["warnings"].append("case_id missing or 'unknown'")
    status = data.get("status")
    if status not in VALID_STATUS:
        report["errors"].append(f"invalid status: {status!r}")
    else:
        report["checks"].append({"check": "status", "ok": True, "detail": status})

    # provenance: each entry must carry element + source_ref + quote
    prov = data.get("provenance") or []
    if not prov:
        report["warnings"].append("provenance is empty (limits not explained)")
    for i, p in enumerate(prov):
        for k in ("element", "source_ref", "quote"):
            if not p.get(k):
                report["errors"].append(f"provenance[{i}] missing '{k}'")
    report["checks"].append({"check": "provenance", "ok": len(prov) > 0,
                             "detail": f"{len(prov)} entries"})

    # sources: each entry must have a ref and a locator path
    sources = data.get("sources") or []
    for i, s in enumerate(sources):
        if not s.get("ref"):
            report["errors"].append(f"sources[{i}] missing 'ref'")
    report["checks"].append({"check": "sources", "ok": len(sources) > 0,
                             "detail": f"{len(sources)} entries"})

    # change_log present and non-empty in non-partial? just check presence
    clog = data.get("change_log") or []
    report["checks"].append({"check": "change_log", "ok": len(clog) > 0,
                             "detail": f"{len(clog)} entries"})
    if not clog:
        report["warnings"].append("change_log empty")

    # runtime must not fabricate model request counts
    rt = data.get("runtime") or {}
    if rt.get("model_requests_used"):
        report["warnings"].append(
            "runtime.model_requests_used should be null (platform records it)")

    # artifacts
    srcs = data.get("editable_source_files") or []
    if not srcs:
        report["warnings"].append("no editable_source_files")
    for i, sf in enumerate(srcs):
        p = sf.get("path")
        if not p:
            report["errors"].append(f"editable_source_files[{i}] missing 'path'")
            continue
        full = os.path.join(output_dir, p)
        if sf.get("kind") == "svg":
            ok, issues = _svg_ok(full)
            report["checks"].append({"check": f"svg:{p}", "ok": ok,
                                     "detail": "; ".join(issues) if issues else "ok"})
            if not ok:
                report["errors"].extend(issues)
        elif os.path.exists(full):
            report["checks"].append({"check": f"source:{p}", "ok": True, "detail": "exists"})
        else:
            report["errors"].append(f"source artifact missing: {full}")

    exps = data.get("export_files") or []
    if not exps:
        report["warnings"].append("no export_files (status must explain)")
    for i, ef in enumerate(exps):
        p = ef.get("path")
        if not p:
            report["errors"].append(f"export_files[{i}] missing 'path'")
            continue
        full = os.path.join(output_dir, p)
        fmt = str(ef.get("format", "")).lower()
        ok, issues = _magic_ok(full, fmt if fmt in ("png", "pdf") else "")
        report["checks"].append({"check": f"export:{p}", "ok": ok,
                                 "detail": "; ".join(issues) if issues else "ok"})
        if not ok:
            report["errors"].extend(issues)

    ok = not report["errors"]
    return ok, report


def _selftest():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import spec_to_svg  # noqa: F401

    results = []
    tmp = tempfile.mkdtemp(prefix="sfe_selftest_")
    spec = {
        "canvas": {"width": 900, "height": 620, "title": "selftest"},
        "nodes": [
            {"id": "n1", "label": "Ligand", "kind": "compound"},
            {"id": "n2", "label": "Receptor", "kind": "protein"},
            {"id": "n3", "label": "Response", "kind": "process"},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "kind": "binding", "label": "binds"},
            {"id": "e2", "from": "n2", "to": "n3", "kind": "activation", "label": "activates"},
        ],
        "groups": [{"id": "g1", "label": "Cell", "nodes": ["n2", "n3"]}],
        "legend": [{"label": "Activation", "marker": "arrow", "color": "#166534"}],
    }
    svg_path = os.path.join(tmp, "figure.svg")
    # render directly (same code path the CLI uses)
    svg = spec_to_svg.render(spec)
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)
    ok, issues = _svg_ok(svg_path)
    results.append(("render+schema", ok, "; ".join(issues) if issues else "ok"))
    if not ok:
        return False, results

    # a broken result must be flagged
    bad = {"case_id": "unknown", "status": "complete", "editable_source_files": [],
           "export_files": [{"path": "nope.png", "format": "png"}],
           "provenance": [], "sources": [], "uncertainty": [], "change_log": []}
    bad_path = os.path.join(tmp, "bad.json")
    with open(bad_path, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    ok, report = check(bad_path, tmp)
    flagged = not ok
    results.append(("checker-flags-broken", flagged,
                    f"{len(report['errors'])} errors, {len(report['warnings'])} warnings"))
    if not flagged:
        return False, results

    # a good result should pass
    good = {
        "case_id": "selftest", "status": "complete",
        "editable_source_files": [{"path": "figure.svg", "kind": "svg",
                                   "layers": True, "editable_text": True, "note": ""}],
        "export_files": [], "provenance": [{"element": "n1", "source_ref": "m#p1",
                                            "quote": "Ligand binds", "mapping": "",
                                            "version": "v1", "confirmed": True}],
        "sources": [{"ref": "m", "path": "m.txt", "locator": "p1", "used_for": "n1"}],
        "uncertainty": [], "change_log": [{"time": "2026-01-01T00:00:00Z",
                                           "action": "scaffold", "detail": ""}],
        "runtime": {"model_requests_used": None},
    }
    good_path = os.path.join(tmp, "good.json")
    with open(good_path, "w", encoding="utf-8") as f:
        json.dump(good, f)
    ok, report = check(good_path, tmp)
    results.append(("checker-passes-good", ok,
                    f"{len(report['errors'])} errors, {len(report['warnings'])} warnings"))
    return ok, results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Self-check for sci-figure-editor")
    ap.add_argument("--result", help="result.json path")
    ap.add_argument("--output-dir", default="/app/output", help="artifact directory")
    ap.add_argument("--selftest", action="store_true", help="run built-in self-test")
    a = ap.parse_args(argv)

    if a.selftest:
        ok, results = _selftest()
        for name, passed, detail in results:
            print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")
        print(json.dumps({"selftest_ok": ok, "results": results}, ensure_ascii=False))
        return 0 if ok else 1

    if not a.result:
        print("error: --result is required (or use --selftest)", file=sys.stderr)
        return 2

    ok, report = check(a.result, a.output_dir)
    print(json.dumps({"ok": ok, "errors": report["errors"],
                      "warnings": report["warnings"], "checks": report["checks"]},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
