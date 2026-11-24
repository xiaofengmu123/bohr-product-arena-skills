#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discovery + manifest + contract extraction for the intro-writing skill.

Robustness contract (all exit 0, never raise):
  * empty input dir        -> manifest with empty materials
  * bad JSON (case.json)   -> error recorded, other materials still read
  * bad encoding           -> degradation recorded, content skipped
  * oversized file         -> content skipped, degradation recorded
  * missing --input        -> default /input; missing dir handled, not a crash

Deterministic: no wall-clock in the manifest, stable ordering, no randomness.
Pure standard library.
"""
import argparse
import json
import os
import sys

MAX_MB_DEFAULT = 2
# Fail-loud chain: no latin-1 fallback, because silent mojibake can seed
# hallucinated facts in a fact-bound writing task. Undecodable -> skip + record.
ENCODINGS = ("utf-8-sig", "utf-8", "gbk")
CONTRACT_KEYS = ("output_contract", "output", "expected_output", "fields", "required_fields")
TEXT_EXTS = {".md", ".txt", ".json", ".jsonl", ".bib", ".tex", ".csv", ".tsv", ".yaml", ".yml", ".rst"}


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--max-mb", type=float, default=MAX_MB_DEFAULT)
    return ap


def read_text_robust(path):
    """Return (text, encoding) or (None, None) if undecodable."""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None, None


def parse_json_robust(text):
    """Return (obj, error_string). Never raises."""
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, str(exc)


def _shape_of(spec):
    if isinstance(spec, list):
        return "list"
    if isinstance(spec, dict):
        return "object"
    return "scalar"


def _default_of(spec):
    """Recursive default value matching a contract field's sub-structure."""
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        for k in ("type", "kind", "shape"):
            if k in spec and isinstance(spec[k], (str, list, dict)):
                return _default_of(spec[k])
        return {k: _default_of(v) for k, v in spec.items()}
    if isinstance(spec, str):
        if spec in ("list", "array"):
            return []
        if spec in ("object", "dict", "map"):
            return {}
        return None
    return None


def _describe_contract(raw):
    """Best-effort flattening of output_contract into field descriptors."""
    fields = []
    if isinstance(raw, dict):
        for name, spec in raw.items():
            fields.append({"name": name, "shape": _shape_of(spec), "default": _default_of(spec), "note": None})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("name") or item.get("key") or item.get("field")
                if name:
                    spec = item.get("type") or item.get("shape") or item
                    fields.append({"name": name, "shape": _shape_of(spec), "default": _default_of(spec), "note": None})
            elif isinstance(item, str):
                fields.append({"name": item, "shape": "scalar", "default": None, "note": None})
    elif isinstance(raw, str):
        fields.append({"name": raw, "shape": "scalar", "default": None, "note": None})
    return fields


def contract_fields_from(obj):
    """Return (fields, raw_contract, found_key). Never raises."""
    if not isinstance(obj, dict):
        return [], None, None
    for key in CONTRACT_KEYS:
        if key in obj and obj[key] is not None:
            raw = obj[key]
            return _describe_contract(raw), raw, key
    return [], None, None


def _is_text_ext(name):
    return os.path.splitext(name)[1].lower() in TEXT_EXTS


def _kind_of(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in {".docx", ".doc", ".pptx", ".xlsx", ".odt"}:
        return "office"
    if ext in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".bmp", ".webp"}:
        return "image"
    return "other"


def _walk_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def _atomic_dump(obj, work_dir, name):
    try:
        os.makedirs(work_dir, exist_ok=True)
        path = os.path.join(work_dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"scan.py: warning: could not write {name}: {exc}", file=sys.stderr)


def _ingest_case(manifest, entry):
    text = entry.get("text")
    if text is None:
        manifest["errors"].append({"path": entry["path"], "reason": "case_not_text", "detail": "case.json 不可解码"})
        return
    obj, err = parse_json_robust(text)
    if err is not None:
        manifest["input_error"] = True
        manifest["errors"].append({"path": entry["path"], "reason": "bad_json", "detail": err})
        return
    fields, raw, found = contract_fields_from(obj)
    manifest["contract_fields"] = fields
    manifest["contract_raw"] = raw
    manifest["contract_found_key"] = found
    if isinstance(obj, dict):
        manifest["case_id"] = obj.get("case_id") or obj.get("id") or obj.get("caseId")
        manifest["case_id_source"] = "case.json"


def _normalize_case(manifest):
    return {
        "case_id": manifest["case_id"],
        "contract_fields": manifest["contract_fields"],
        "contract_found_key": manifest["contract_found_key"],
        "input_error": manifest["input_error"],
        "materials": [m["path"] for m in manifest["materials"]],
    }


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)

    manifest = {
        "input_dir": args.input,
        "input_dir_exists": os.path.isdir(args.input),
        "files": [],
        "materials": [],
        "contract_fields": [],
        "contract_raw": None,
        "contract_found_key": None,
        "case_id": None,
        "case_id_source": None,
        "degradations": [],
        "errors": [],
        "input_error": False,
    }
    max_bytes = int(args.max_mb * 1024 * 1024)

    if not manifest["input_dir_exists"]:
        manifest["input_error"] = True
        manifest["errors"].append({"path": args.input, "reason": "input_dir_missing", "detail": "输入目录不存在，按空输入降级处理"})
        _atomic_dump(manifest, args.work, "input_manifest.json")
        _atomic_dump(_normalize_case(manifest), args.work, "case_normalized.json")
        return 0

    for rel in _walk_files(args.input):
        full = os.path.join(args.input, rel)
        try:
            size = os.path.getsize(full)
        except OSError as exc:
            manifest["degradations"].append({"path": rel, "reason": "stat_failed", "detail": str(exc)})
            continue

        entry = {"path": rel, "size": size, "kind": _kind_of(rel)}

        if size > max_bytes:
            entry["content_skipped"] = True
            manifest["materials"].append(entry)
            manifest["degradations"].append({"path": rel, "reason": "oversized", "detail": f"{size} bytes > {max_bytes} bytes"})
            continue

        if _is_text_ext(rel) or os.path.splitext(rel)[1] == "":
            text, enc = read_text_robust(full)
            if text is None:
                entry["content_skipped"] = True
                manifest["materials"].append(entry)
                manifest["degradations"].append({"path": rel, "reason": "undecodable", "detail": "所有编码回退均失败"})
                continue
            entry["encoding"] = enc
            entry["text"] = text
        else:
            entry["content_skipped"] = True
            entry["note"] = "binary; use platform pdf-text-tool / ocr-tool / office-renderer as needed"

        manifest["materials"].append(entry)

    for entry in manifest["materials"]:
        if os.path.basename(entry["path"]) == "case.json":
            _ingest_case(manifest, entry)

    _atomic_dump(manifest, args.work, "input_manifest.json")
    _atomic_dump(_normalize_case(manifest), args.work, "case_normalized.json")
    return 0


if __name__ == "__main__":
    sys.exit(run())
