#!/usr/bin/env python3
"""Shared helpers for the paper-structure-review skill (stdlib only)."""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def env(name, default):
    return os.environ.get(name, default)


def resolve_paths(case, task, input_dir, work, out, result):
    """Return canonical paths honoring environment overrides."""
    return {
        "case": env("CASE_JSON", case),
        "task": env("TASK_MD", task),
        "input": env("INPUT_DIR", input_dir),
        "work": env("WORK_DIR", work),
        "out": env("OUTPUT_DIR", out),
        "result": env("RESULT_JSON", result),
    }


def ensure_dirs(paths):
    for key in ("work", "out"):
        p = paths[key]
        if p:
            os.makedirs(p, exist_ok=True)


def read_text(path, limit=2_000_000):
    """Read UTF-8 text tolerating BOM; returns '' on any failure."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = os.path.getsize(path)
        if size > limit:
            return "[skipped: file too large]"
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


def read_json(path):
    """Parse JSON tolerating BOM and bad encoding; returns dict on failure."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json(path, data):
    """Write JSON atomically-ish (temp file then replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_existing_result(path):
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def case_id_from(case_data):
    for key in ("case_id", "id", "challenge_id", "armiId"):
        if case_data.get(key):
            return str(case_data[key])
    return "unknown"


def output_contract_from(case_data):
    """Return output_contract if present, else the default contract."""
    oc = case_data.get("output_contract")
    if isinstance(oc, dict) and oc:
        return dict(oc)
    return {
        "diagnosis": None,
        "edit_plan": None,
        "evidence_map": None,
        "version_boundary": None,
        "artifacts": None,
    }


def default_skeleton(case_data):
    oc = output_contract_from(case_data)
    return {
        "case_id": case_id_from(case_data),
        "status": "in_progress",
        "output_contract": {k: (None if v is None else v) for k, v in oc.items()},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {
                "time": utcnow(),
                "action": "bootstrap skeleton",
                "status": "in_progress",
            }
        ],
        "runtime": {
            "started_at": utcnow(),
            "finished_at": None,
            "model_requests": 0,
            "seconds": None,
        },
    }


def fill_output_contract_fields(result, case_data):
    """Ensure every output_contract key exists at top level."""
    oc = output_contract_from(case_data)
    for key, default in oc.items():
        if key not in result:
            result[key] = None if default is None else default


def merge_result(path, patch):
    """Merge patch into existing result.json and write back."""
    data = load_existing_result(path)
    for key, value in patch.items():
        data[key] = value
    write_json(path, data)
    return data


TEXT_EXTS = {
    ".md", ".txt", ".tex", ".rst", ".json", ".jsonl", ".csv", ".tsv",
    ".yaml", ".yml", ".bib", ".xml", ".html", ".rtf",
}
BINARY_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff",
    ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls", ".ipynb", ".zip", ".gz",
}

SECTION_RE = re.compile(
    r"^\s*(#{1,6}\s+.*|"
    r"(?:第[一二三四五六七八九十百0-9]+[章节部分])[^\n]{0,80}|"
    r"(?:\d{1,3}(?:\.\d{1,3}){0,3})\s+\S.{0,80}|"
    r"(?:Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|"
    r"Related\s+Work|References|Acknowledg\w+)[\s:：].{0,60})",
    re.IGNORECASE,
)

NUM_RE = re.compile(
    r"(?<![\w.])(?:\d+(?:\.\d+)?|\.\d+)(?:\s*(?:%|‰|ppm|ppb|mmol|μmol|"
    r"mol|mg|μg|kg|g|s|ms|min|h|d|m|cm|mm|μm|nm|ml|L|°C|K|Hz|kHz|MHz|"
    r"GHz|Da|kDa|bp|kb|Mb|eV|keV|MeV|GeV|J|W|AU|a\.u\.))?(?![\w.])",
)


def iter_files(root, max_bytes=2_000_000, max_depth=6):
    """Yield (rel_path, abs_path) for regular files, skipping oversized ones."""
    if not root or not os.path.isdir(root):
        return
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirnames[:] = []
            continue
        for name in filenames:
            if name.startswith("."):
                continue
            apath = os.path.join(dirpath, name)
            try:
                if os.path.getsize(apath) > max_bytes:
                    continue
            except OSError:
                continue
            yield (os.path.relpath(apath, root), apath)


def normalize_locator(rel_path, line_no=None, section=None):
    loc = rel_path.replace("\\", "/")
    if section:
        loc += f"#{section}"
    if line_no is not None:
        loc += f":L{line_no}"
    return loc


if __name__ == "__main__":
    print(SKILL_ROOT)
