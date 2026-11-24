#!/usr/bin/env python3
"""Self-test: validate result.json and verify repeat-run determinism.

Subcommands (use --help for options):

  check         structural + consistency + forbidden-word + source-traceback
                validation of /app/output/result.json against input_summary.json.
  determinism   run build_result.build_skeleton twice on the same summary and
                assert byte-identical output.

Exit codes: 0 = passed; 1 = structural error found; 2 = warnings only
(deterministic, but the author must review before marking complete).

Standard library only.
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile

FORBIDDEN = (
    "首次", "国际领先", "填补空白", "颠覆性", "革命性",
    "世界首创", "全球领先", "国内首创", "全球第一", "重大突破",
    "groundbreaking", "revolutionary", "world-first", "unprecedented",
    "first-in-class", "first ever",
)

VALID_STATUS = {
    "ok", "partial", "complete", "failed",
    "insufficient_evidence", "input_error", "in_progress",
}

REQUIRED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def iter_strings(obj, path="$"):
    """Yield (json_path, string) for every string in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, "%s[%d]" % (path, i))
    elif isinstance(obj, str):
        yield path, obj


def find_text_files(output_dir):
    out = []
    if not output_dir or not os.path.isdir(output_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(output_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.lower().endswith((".md", ".txt")):
                out.append(os.path.join(dirpath, fn))
    return out


def check(args):
    errors, warnings = [], []

    try:
        result = load_json(args.result)
    except OSError as exc:
        return ["cannot read result: %s" % exc], []
    except ValueError as exc:
        return ["bad JSON in result: %s" % exc], []

    try:
        summary = load_json(args.summary)
    except (OSError, ValueError) as exc:
        summary = None
        warnings.append("summary unavailable (%s); contract check skipped" % exc)

    # 1. case_id present and consistent.
    if not result.get("case_id"):
        errors.append("result.case_id missing or empty")
    elif summary and result.get("case_id") != summary.get("case_id"):
        warnings.append("result.case_id != summary.case_id")

    # 2. every output_contract field present under its original name.
    contract = {}
    if summary:
        oc = summary.get("output_contract") or {}
        contract = oc if isinstance(oc, dict) else {
            name: None for name in (oc if isinstance(oc, list) else [])}
    for key in contract:
        if key not in result:
            errors.append("output_contract field missing from result: %s" % key)

    # 3. standard trace fields present.
    for key in REQUIRED_KEYS:
        if key not in result:
            errors.append("required trace field missing: %s" % key)

    # 4. honest status.
    status = result.get("status")
    if status not in VALID_STATUS:
        errors.append("invalid status %r" % (status,))

    # 5. forbidden unsubstantiated claims, in result.json and deliverable text.
    hits = []
    for path, s in iter_strings(result):
        for w in FORBIDDEN:
            if w in s:
                hits.append((path, w, s[:80]))
    for txt in find_text_files(args.output_dir):
        try:
            with open(txt, "r", encoding="utf-8-sig", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        for w in FORBIDDEN:
            if w in content:
                hits.append((os.path.basename(txt), w, ""))
    if hits:
        warnings.append(
            "%d forbidden/unsubstantiated-claim hit(s); remove or confirm each is a "
            "direct quoted source passage: %s"
            % (len(hits), [(h[0], h[1]) for h in hits[:10]]))

    # 6. source traceback: every evidence entry needs locator (+quote preferred).
    for bucket in ("sources", "facts_sources"):
        entries = result.get(bucket)
        if not isinstance(entries, list):
            continue
        for i, e in enumerate(entries):
            if isinstance(e, dict):
                if not e.get("locator"):
                    errors.append("%s[%d] missing locator" % (bucket, i))
                if "quote" not in e:
                    warnings.append(
                        "%s[%d] has no verbatim quote; add locator + quote" % (bucket, i))
            else:
                warnings.append("%s[%d] is not a dict" % (bucket, i))

    # 7. deliverable files referenced must exist.
    for pointer in ("deliverable",):
        d = result.get(pointer)
        if isinstance(d, dict):
            for fkey in ("file", "path"):
                f = d.get(fkey)
                if f and not os.path.isabs(f):
                    cand = os.path.join(args.output_dir or "", f)
                    if not os.path.exists(cand):
                        warnings.append("deliverable file not found: %s" % cand)

    return errors, warnings


def determinism(args):
    spec = importlib.util.spec_from_file_location(
        "build_result", os.path.join(args.script_dir, "build_result.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as td:
        r1 = os.path.join(td, "r1.json")
        r2 = os.path.join(td, "r2.json")
        mod.build_skeleton(args.summary, r1)
        mod.build_skeleton(args.summary, r2)
        with open(r1, "rb") as fh:
            b1 = fh.read()
        with open(r2, "rb") as fh:
            b2 = fh.read()
        if b1 == b2:
            print(json.dumps({"ok": True, "deterministic": True, "bytes": len(b1)},
                             ensure_ascii=False, sort_keys=True))
            return []
        return ["repeat build produced different bytes (%d vs %d)" % (len(b1), len(b2))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pchk = sub.add_parser("check", help="validate result.json")
    pchk.add_argument("--result", default="/app/output/result.json")
    pchk.add_argument("--summary", default="/app/work/input_summary.json")
    pchk.add_argument("--output-dir", default="/app/output")

    pdet = sub.add_parser("determinism", help="verify repeat-run determinism")
    pdet.add_argument("--summary", default="/app/work/input_summary.json")
    pdet.add_argument("--script-dir", default=os.path.dirname(os.path.abspath(__file__)))

    a = ap.parse_args(argv)
    if a.cmd == "check":
        errors, warnings = check(a)
        print(json.dumps({"errors": errors, "warnings": warnings},
                         ensure_ascii=False, indent=2, sort_keys=True))
        if errors:
            return 1
        if warnings:
            return 2
        return 0
    if a.cmd == "determinism":
        errors = determinism(a)
        print(json.dumps({"errors": errors}, ensure_ascii=False, indent=2,
                         sort_keys=True))
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
