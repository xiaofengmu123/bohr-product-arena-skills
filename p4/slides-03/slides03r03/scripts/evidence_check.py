#!/usr/bin/env python3
"""Evidence traceability checks for /app/output/result.json.

Verifies, without modifying anything:
  - every page has a unique, non-empty single_claim and a valid evidence_strength;
  - every page references at least one source and every figure has a 'ref';
  - every evidence_map entry carries a locator AND a verbatim quote;
  - every source_ref used by a page resolves to a known source path.

Exit code 0 means no errors (warnings are non-fatal quality hints).
Only the standard library is used.
"""
import argparse
import json
import sys
from pathlib import Path

ALLOWED_STRENGTH = {"high", "medium", "low"}


def read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_result(path):
    path = Path(path)
    if not path.is_file():
        return None, "result.json not found at %s" % path
    try:
        obj = json.loads(read_text(path))
        return obj, None
    except Exception as exc:  # defensive
        return None, "%s: %s" % (type(exc).__name__, exc)


def check(result):
    errors, warnings = [], []
    if not isinstance(result, dict):
        return ["result is not a JSON object"], []

    known_paths = {s.get("path") for s in result.get("sources", []) if isinstance(s, dict)}

    pages = result.get("pages")
    if not isinstance(pages, list):
        errors.append("pages must be a list")
        pages = []
    claims = {}
    for i, page in enumerate(pages):
        idx = page.get("page_index", i) if isinstance(page, dict) else i
        if not isinstance(page, dict):
            errors.append("page %s: not an object" % idx)
            continue
        claim = (page.get("single_claim") or "").strip()
        if not claim:
            errors.append("page %s: single_claim empty" % idx)
        elif claim in claims:
            errors.append("page %s: single_claim duplicates page %s" % (idx, claims[claim]))
        else:
            claims[claim] = idx
        strength = page.get("evidence_strength")
        if strength not in ALLOWED_STRENGTH:
            warnings.append("page %s: evidence_strength %r not in %s" % (idx, strength, sorted(ALLOWED_STRENGTH)))
        refs = page.get("source_refs")
        if not isinstance(refs, list) or not refs:
            warnings.append("page %s: no source_refs" % idx)
        else:
            for ref in refs:
                if isinstance(ref, str) and known_paths and ref not in known_paths:
                    warnings.append("page %s: source_ref %r not in sources" % (idx, ref))
        for fig in page.get("figures", []) or []:
            if not isinstance(fig, dict) or not fig.get("ref"):
                warnings.append("page %s: figure entry missing 'ref'" % idx)

    em = result.get("evidence_map")
    if not isinstance(em, list):
        errors.append("evidence_map must be a list")
    else:
        for j, item in enumerate(em):
            if not isinstance(item, dict):
                errors.append("evidence_map[%d]: not an object" % j)
                continue
            if not (item.get("locator") or "").strip():
                errors.append("evidence_map[%d]: missing locator" % j)
            if not (item.get("quote") or "").strip():
                errors.append("evidence_map[%d]: missing verbatim quote" % j)
    return errors, warnings


def build_parser():
    ap = argparse.ArgumentParser(prog="evidence_check.py",
                                 description="Evidence traceability checks for result.json.")
    ap.add_argument("--result", default="/app/output/result.json")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    result, err = load_result(args.result)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    errors, warnings = check(result)
    print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
