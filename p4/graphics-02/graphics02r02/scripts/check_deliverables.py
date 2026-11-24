#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate result.json and the artifacts it references.

Checks contract completeness, honest status, artifact existence, magic numbers,
PNG DPI (pHYs chunk), SVG text editability, and PDF font references. Writes a
check report to disk. Exit code 0 means "report produced", not "all passed";
callers inspect the report (or use --fail-on-error to exit non-zero on hard
failures).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def check_png(path):
    try:
        with open(path, "rb") as fh:
            sig = fh.read(8)
    except OSError as exc:
        return {"exists": False, "magic_ok": False, "dpi": None, "detail": str(exc)}
    if sig != b"\x89PNG\r\n\x1a\n":
        return {"exists": True, "magic_ok": False, "dpi": None, "detail": "not a PNG"}
    dpi = None
    try:
        with open(path, "rb") as fh:
            fh.read(8)
            while True:
                hdr = fh.read(8)
                if len(hdr) < 8:
                    break
                length = int.from_bytes(hdr[:4], "big")
                ctype = hdr[4:8]
                data = fh.read(length)
                fh.read(4)  # CRC
                if ctype == b"pHYs":
                    xppm = int.from_bytes(data[:4], "big")
                    unit = data[8]
                    if unit == 1:
                        dpi = round(xppm * 0.0254, 1)
                    break
    except OSError:
        pass
    return {"exists": True, "magic_ok": True, "dpi": dpi, "detail": "ok"}


def check_pdf(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        return {"exists": False, "magic_ok": False, "font_refs": False, "detail": str(exc)}
    ok = head.startswith(b"%PDF-")
    fonts = False
    if ok:
        try:
            with open(path, "rb") as fh:
                blob = fh.read(4_000_000)
            fonts = (b"/Font" in blob) and any(
                token in blob for token in (b"/BaseFont", b"/FontFile", b"/TrueType", b"/Type1")
            )
        except OSError:
            pass
    return {"exists": True, "magic_ok": ok, "font_refs": fonts, "detail": "ok" if ok else "not a PDF"}


def check_svg(path):
    exists = os.path.exists(path)
    if not exists:
        return {"exists": False, "parse_ok": False, "has_text": False, "text_count": 0, "detail": "missing"}
    try:
        tree = ET.parse(path)
    except Exception as exc:
        return {"exists": True, "parse_ok": False, "has_text": False, "text_count": 0, "detail": str(exc)}
    root = tree.getroot()
    texts = [el for el in root.iter() if str(el.tag).endswith("text")]
    has_text = any((el.text or "").strip() for el in texts)
    return {"exists": True, "parse_ok": True, "has_text": has_text, "text_count": len(texts), "detail": "ok"}


def contract_value(result, field):
    if field in result:
        return result[field]
    oc = result.get("output_contract")
    if isinstance(oc, dict) and field in oc:
        return oc[field]
    return None


def check_artifact(path, add, cid):
    if not os.path.exists(path):
        add(cid, "soft", False, "missing: %s" % path)
        return
    ext = os.path.splitext(path)[1].lower()
    if ext == ".png":
        r = check_png(path)
        add(cid + "_png_magic", "hard", r["magic_ok"], r["detail"])
        if r.get("dpi") is not None:
            add(cid + "_png_dpi", "soft", r["dpi"] >= 300, "%.1f dpi" % r["dpi"])
    elif ext == ".pdf":
        r = check_pdf(path)
        add(cid + "_pdf_magic", "hard", r["magic_ok"], r["detail"])
        add(cid + "_pdf_fonts", "soft", r["font_refs"], "font refs found" if r["font_refs"] else "no font refs detectable (may be compressed)")
    elif ext == ".svg":
        r = check_svg(path)
        add(cid + "_svg_parse", "hard", r["parse_ok"], r["detail"])
        add(cid + "_svg_text", "soft", r["has_text"], "%d text nodes" % r["text_count"])
    else:
        add(cid, "soft", True, "no format check for %s" % ext)


def run_checks(result_path, output_dir):
    report = {
        "checked_utc": now_utc(),
        "result_path": result_path,
        "checks": [],
        "summary": {"total": 0, "hard_fail": 0, "soft_fail": 0, "pass": 0},
    }

    def add(cid, severity, ok, detail):
        report["checks"].append({"id": cid, "severity": severity, "pass": ok, "detail": detail})
        report["summary"]["total"] += 1
        if ok:
            report["summary"]["pass"] += 1
        elif severity == "hard":
            report["summary"]["hard_fail"] += 1
        else:
            report["summary"]["soft_fail"] += 1

    if not os.path.exists(result_path):
        add("result_exists", "hard", False, "result.json missing")
        return report
    try:
        result = read_json(result_path)
        add("result_parses", "hard", True, "ok")
    except Exception as exc:
        add("result_parses", "hard", False, str(exc))
        return report

    add("case_id", "hard", bool(result.get("case_id")), result.get("case_id") or "empty")
    add("status", "hard", result.get("status") in ("complete", "partial", "failed"), result.get("status"))

    fields = result.get("contract_fields") or []
    missing = [f for f in fields if contract_value(result, f) is None]
    add("contract_fields", "soft", len(missing) == 0,
        "missing/null: %s" % missing if missing else "%d fields present" % len(fields))

    for key in ("sources", "uncertainty", "change_log"):
        add("meta_" + key, "hard", isinstance(result.get(key), list), "%s: %s" % (key, type(result.get(key)).__name__))
    add("change_log_nonempty", "soft", bool(result.get("change_log")), "change_log has entries")

    for f in fields:
        val = contract_value(result, f)
        if isinstance(val, str) and val and not val.startswith(("http", "/input")):
            p = val if os.path.isabs(val) else os.path.join(output_dir, val)
            check_artifact(p, add, "artifact_" + f)

    ev = result.get("evidence")
    if isinstance(ev, list) and ev:
        add("evidence_present", "soft", True, "%d evidence entries" % len(ev))
    else:
        add("evidence_present", "soft", False, "evidence absent (recommended for traceability)")

    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default=os.environ.get("RESULT_JSON", "/app/output/result.json"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--report", default=os.environ.get("CHECK_REPORT", "/app/work/check_report.json"))
    ap.add_argument("--fail-on-error", action="store_true", help="exit non-zero when a hard check fails")
    a = ap.parse_args(argv)

    report = run_checks(a.result, a.output)
    write_json(a.report, report)
    print(json.dumps({"ok": True, "report": a.report, "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 1 if (a.fail_on_error and report["summary"]["hard_fail"]) else 0


if __name__ == "__main__":
    sys.exit(main())
